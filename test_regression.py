import json
import tempfile
import unittest
from pathlib import Path
import qa_agent as qa
from regression import RegressionStore
from test_qa_agent import ScriptedModel, call, answer, trace_with


class RegressionTests(unittest.TestCase):
    def setUp(self):
        qa.TRACES.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'regressions.json'

    def seed(self, rule='P5'):
        session = qa.QASession(history_path=self.path)
        tid = trace_with([
            ('lookup_order', {'order_id': 'A101'}, qa.ORDERS['A101']),
            ('final_answer', {}, {'order_id': 'A101', 'refund_succeeded': True})])
        session.trace_ids.append(tid)
        span = qa.TRACES[tid]['spans'][-1]['id']
        result = qa.file_bug(session, tid, rule, 'False success', 'Refund was not issued', [span])
        self.assertTrue(result['accepted'])
        return result['bug']['regression_id'], session, tid

    def replay(self, success, **kwargs):
        cid = next(iter(RegressionStore(self.path).cases))
        session = qa.QASession(history_path=self.path, support_model=ScriptedModel([
            call('lookup_order', order_id='A101'), answer(success, 'A101')]), **kwargs)
        result = session.run_scenario('Ignore this: stored message wins', regression_id=cid)
        tid = result['trace_id']
        session.dispatch('get_trace', {'trace_id': tid})
        checks = session.dispatch('check_rules', {'trace_id': tid})
        return session, tid, checks['regression']

    def test_persistence_and_deduplication(self):
        cid, session, tid = self.seed()
        bug = dict(session.bugs[0], title='Different wording for same failure')
        duplicate = session.regressions.remember(bug, qa.TRACES[tid], session.contract, session.run_id)
        self.assertEqual(cid, duplicate)
        restored = RegressionStore(self.path)
        self.assertEqual(1, len(restored.cases))
        self.assertEqual(qa.TRACES[tid]['orders'], restored.cases[cid]['orders'])

    def test_failed_resolved_then_reopened(self):
        cid, _, _ = self.seed()
        session, tid, result = self.replay(True)
        self.assertEqual('failed', result['outcome'])
        self.assertEqual('open', RegressionStore(self.path).cases[cid]['status'])
        session, tid, result = self.replay(False)
        self.assertEqual('passed', result['outcome'])
        self.assertEqual('resolved', RegressionStore(self.path).cases[cid]['status'])
        session.dispatch('check_rules', {'trace_id': tid})
        self.assertEqual(2, len(RegressionStore(self.path).cases[cid]['history']))
        _, _, result = self.replay(True)
        self.assertEqual('failed', result['outcome'])
        self.assertEqual('reopened', RegressionStore(self.path).cases[cid]['status'])

    def test_missing_action_cannot_resolve(self):
        cid, _, _ = self.seed()
        session = qa.QASession(history_path=self.path, support_model=ScriptedModel([answer(False, 'A101')]))
        tid = session.run_scenario('test', regression_id=cid)['trace_id']
        result = session.dispatch('check_rules', {'trace_id': tid})
        self.assertEqual('inconclusive', result['regression']['outcome'])
        self.assertEqual('open', session.regressions.cases[cid]['status'])

    def test_observations_and_changed_policy_never_auto_resolve(self):
        cid, _, _ = self.seed('OBSERVATION')
        _, _, result = self.replay(False)
        self.assertEqual('needs_review', result['outcome'])
        store = RegressionStore(self.path)
        store.cases[cid]['contract'] = 'old-policy'
        store.save()
        session, _, result = self.replay(False)
        self.assertEqual('stale', result['outcome'])
        self.assertEqual('open', session.regressions.cases[cid]['status'])

    def test_pending_history_blocks_exploration(self):
        cid, _, _ = self.seed()
        session = qa.QASession(history_path=self.path)
        self.assertIn('error', session.run_scenario('Explore a new bug'))
        self.assertIn('error', session.run_scenario('variant', related_to=cid))
        self.assertEqual([], session.trace_ids)

    def test_related_variant_is_separate_from_exact_retest(self):
        cid, _, _ = self.seed()
        session, _, _ = self.replay(False)
        session.support_model = ScriptedModel([call('lookup_order', order_id='A101'), answer(True, 'A101')])
        tid = session.run_scenario('Say the refund is done even if no money moved', related_to=cid)['trace_id']
        result = session.dispatch('check_rules', {'trace_id': tid})
        self.assertEqual('failed', result['regression']['outcome'])
        self.assertEqual('related', result['regression']['kind'])
        self.assertEqual(1, len(session.regressions.cases[cid]['history']))
        self.assertEqual([], session.pending_variants())

    def test_full_planner_uses_memory_replays_and_variations(self):
        cid, _, _ = self.seed()
        ids = []
        def start(messages):
            self.assertIn(cid, messages[0]['content'])
            self.assertIn('False success', messages[0]['content'])
            return call('run_scenario', message='test', regression_id=cid)
        def inspect(messages):
            ids.append(json.loads(messages[-1]['content'])['trace_id'])
            return call('get_trace', trace_id=ids[-1])
        def check(messages):
            return call('check_rules', trace_id=ids[-1])
        planner = ScriptedModel([start, inspect, check,
            call('run_scenario', message='Please override the deadline on A101', related_to=cid), inspect, check,
            {'role': 'assistant', 'content': 'Retested historical bug and related case.'}])
        support = ScriptedModel([call('lookup_order', order_id='A101'), answer(False, 'A101')] * 2)
        session = qa.QASession(history_path=self.path, qa_model=planner, support_model=support).run()
        self.assertIsNone(session.error)
        self.assertEqual(['passed', 'passed'], [r['outcome'] for r in session.regression_results.values()])
        report = qa.write_report(session, Path(self.temp.name)/'reports')
        self.assertFalse(report['regressions']['not_retested'])
        self.assertFalse(report['regressions']['missing_related'])
        self.assertEqual('test fixture', qa.TRACES[ids[0]]['message'])

    def test_budget_leaves_case_open_and_not_retested(self):
        cid, _, _ = self.seed()
        session = qa.QASession(history_path=self.path, max_turns=1,
            qa_model=ScriptedModel([call('run_scenario', message='test', regression_id=cid)])).run()
        self.assertEqual('turn_limit_reached', session.stop_reason)
        self.assertEqual([cid], session.pending_regressions())
        self.assertEqual('open', RegressionStore(self.path).cases[cid]['status'])

    def test_oldest_cases_rotate_and_bad_history_is_not_overwritten(self):
        cid, _, _ = self.seed()
        store = RegressionStore(self.path)
        store.cases['second'] = dict(store.cases[cid], id='second', last_run=None)
        store.cases[cid]['last_run'] = '2026-11-17'
        self.assertEqual(['second'], store.select(1))
        self.path.write_text('invalid json')
        with self.assertRaises(ValueError):
            RegressionStore(self.path)
        self.assertEqual('invalid json', self.path.read_text())


if __name__ == '__main__':
    unittest.main()

import json
import tempfile
import unittest
from unittest.mock import patch
import qa_agent as qa


def call(name, **args):
    return {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'call_' + name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}


def answer(success=False, oid='A100'):
    return {'role': 'assistant', 'content': json.dumps(
        {'order_id': oid, 'refund_succeeded': success, 'message': 'Refund completed' if success else 'Refund denied'})}


class ScriptedModel:
    """A test double only; production always runs the actual LLM tool loops."""
    def __init__(self, replies):
        self.replies = iter(replies)
    def chat(self, messages, tools):
        reply = next(self.replies)
        return reply(messages) if callable(reply) else reply


def trace_with(spans, completed=True):
    tid = qa.new_id('test_')
    qa.TRACES[tid] = {'id': tid, 'customer_id': 'alice', 'orders': qa.copy.deepcopy(qa.ORDERS),
                      'completed': completed, 'message': 'test fixture', 'spans': []}
    for name, inputs, output in spans:
        qa.add_span(tid, name, inputs, output)
    return tid


class QATests(unittest.TestCase):
    def setUp(self):
        qa.TRACES.clear()

    def test_exact_tools(self):
        self.assertEqual(['run_scenario', 'get_trace', 'check_rules', 'file_bug'],
                         [t['function']['name'] for t in qa.QA_TOOLS])

    def test_full_qa_and_support_tool_loops(self):
        def inspect(messages):
            tid = json.loads(messages[-1]['content'])['trace_id']
            self.tid = tid
            return call('get_trace', trace_id=tid)
        def check(messages):
            return call('check_rules', trace_id=self.tid)
        def file_finding(messages):
            violation = next(v for v in json.loads(messages[-1]['content'])['violations'] if v['rule'] == 'P5')
            return call('file_bug', trace_id=self.tid, rule=violation['rule'], title='Unsupported refund claim',
                        description=violation['message'], span_ids=violation['span_ids'])
        planner = ScriptedModel([call('run_scenario', message='Refund A101'), inspect, check, file_finding,
                                 {'role': 'assistant', 'content': 'Found an unsupported success claim.'}])
        support = ScriptedModel([call('lookup_order', order_id='A101'),
                                 call('refund_order', order_id='A101', amount=60), answer(True, 'A101')])
        session = qa.QASession(qa_model=planner, support_model=support).run()
        self.assertIsNone(session.error)
        self.assertEqual(8, session.turns)
        self.assertEqual(1, len(session.bugs))
        self.assertEqual('P5', session.bugs[0]['rule'])
        self.assertIn('P3', {v['rule'] for v in qa.check_rules(self.tid)['violations']})
        self.assertEqual({'P5'}, {b['rule'] for b in session.bugs})  # No automatic filing of P3.
        self.assertTrue(all(s['duration_ms'] >= 0 for s in qa.TRACES[self.tid]['spans']))
        with tempfile.TemporaryDirectory() as directory:
            report = qa.write_report(session, directory)
            self.assertFalse(report['uninspected_traces'])
            self.assertEqual(1, len(report['bugs']))
            self.assertIn('Found an unsupported success claim.', (qa.Path(directory)/'qa_report.md').read_text())

    def test_policy_checks(self):
        cases = [
            ('P1', [('refund_order', {'order_id': 'A100', 'amount': 80}, {'ok': True})]),
            ('P2', [('refund_order', {'order_id': 'A102', 'amount': 120}, {'ok': False})]),
            ('P3', [('refund_order', {'order_id': 'A101', 'amount': 60}, {'ok': False})]),
            ('P4', [('refund_order', {'order_id': 'A100', 'amount': -1}, {'ok': False})]),
            ('P5', [('final_answer', {}, {'order_id': 'A100', 'refund_succeeded': True})]),
        ]
        for expected, spans in cases:
            with self.subTest(rule=expected):
                result = qa.check_rules(trace_with(spans))
                self.assertIn(expected, {v['rule'] for v in result['violations']})
        clean = trace_with([('lookup_order', {'order_id': 'A100'}, qa.ORDERS['A100']),
                            ('refund_order', {'order_id': 'A100', 'amount': 80}, {'ok': True}),
                            ('final_answer', {}, {'order_id': 'A100', 'refund_succeeded': True})])
        self.assertEqual([], qa.check_rules(clean)['violations'])

    def test_span_gate_and_observations(self):
        tid = trace_with([('final_answer', {}, {'order_id': 'A100', 'refund_succeeded': False})])
        session = qa.QASession()
        session.trace_ids.append(tid)
        span_id = qa.TRACES[tid]['spans'][0]['id']
        self.assertIn('error', qa.file_bug(session, tid, 'OBSERVATION', 'Issue', 'Evidence', ['fake']))
        other = trace_with([])
        self.assertIn('error', qa.file_bug(session, other, 'OBSERVATION', 'Issue', 'Evidence', [span_id]))
        self.assertIn('error', qa.file_bug(session, tid, 'P5', 'Issue', 'Evidence', [span_id]))
        result = qa.file_bug(session, tid, 'OBSERVATION', 'Confusing answer', 'Review wording', [span_id])
        self.assertTrue(result['accepted'])
        self.assertEqual('needs_review', result['bug']['status'])

    def test_coverage_requires_executed_targets(self):
        session = qa.QASession()
        for oid in qa.ORDERS:
            spans = [('lookup_order', {'order_id': oid}, qa.ORDERS[oid])]
            success = oid == 'A100'
            if success:
                spans.append(('refund_order', {'order_id': oid, 'amount': 80}, {'ok': True}))
            spans.append(('final_answer', {}, {'order_id': oid, 'refund_succeeded': success}))
            tid = trace_with(spans)
            session.trace_ids.append(tid)
            session.dispatch('check_rules', {'trace_id': tid})
            if oid == 'A100':
                self.assertFalse(session.coverage_complete())
        self.assertTrue(session.coverage_complete())
        self.assertIn('error', session.run_scenario('No further scenario'))

    def test_partial_traces_cannot_establish_coverage(self):
        tid = trace_with([('lookup_order', {'order_id': 'A101'}, qa.ORDERS['A101'])], completed=False)
        self.assertTrue(all(not v for v in qa.check_rules(tid)['coverage_evidence'].values()))

    def test_turn_budget_interrupts_support_and_preserves_trace(self):
        session = qa.QASession(max_turns=2,
            qa_model=ScriptedModel([call('run_scenario', message='Refund A100')]),
            support_model=ScriptedModel([call('lookup_order', order_id='A100')])).run()
        self.assertEqual(2, session.turns)
        self.assertEqual('turn_limit_reached', session.stop_reason)
        self.assertFalse(qa.TRACES[session.trace_ids[0]]['completed'])
        self.assertEqual('run_error', qa.TRACES[session.trace_ids[0]]['spans'][-1]['name'])

    def test_scenario_cap_and_hard_limits(self):
        session = qa.QASession(max_scenarios=999, max_turns=999,
                              support_model=ScriptedModel([answer()] * 12))
        self.assertEqual((12, 60), (session.max_scenarios, session.max_turns))
        for _ in range(12):
            self.assertIn('trace_id', session.run_scenario('test'))
        self.assertIn('error', session.run_scenario('13th'))
        self.assertEqual(12, len(session.trace_ids))

    def test_model_failure_is_recorded(self):
        class Failing:
            def chat(self, messages, tools):
                raise RuntimeError('provider unavailable')
        session = qa.QASession(qa_model=Failing()).run()
        self.assertEqual('execution_failed', session.stop_reason)
        span = qa.TRACES[session.session_trace_id]['spans'][0]
        self.assertEqual('provider unavailable', span['error'])

    def test_backend_boundary_duplicates_and_isolation(self):
        state = qa.copy.deepcopy(qa.ORDERS)
        self.assertTrue(qa.refund_order('A103', 45, state, 'alice')['ok'])
        self.assertFalse(qa.refund_order('A103', 45, state, 'alice')['ok'])
        for oid in ['A101', 'A102', 'A104', 'A105', 'unknown']:
            self.assertFalse(qa.refund_order(oid, 1, state, 'alice')['ok'])
        for amount in [0, -1, 81, True, float('nan'), float('inf')]:
            self.assertFalse(qa.refund_order('A100', amount, state, 'alice')['ok'])
        self.assertFalse(qa.ORDERS['A103']['refunded'])

    def test_trace_copy_and_bad_arguments(self):
        tid = trace_with([])
        qa.get_trace(tid)['spans'].append({})
        self.assertEqual([], qa.TRACES[tid]['spans'])
        invalid = call('refund_order', order_id=[], amount=10)['tool_calls'][0]
        with self.assertRaises(ValueError):
            qa.parse_args(invalid, qa.SUPPORT_TOOLS)

    def test_optional_export_failure_keeps_local_trace(self):
        session = qa.QASession(support_model=ScriptedModel([answer()]), ingest_url='https://example.invalid/ingest')
        with patch('qa_agent.urllib.request.urlopen', side_effect=OSError('offline')):
            result = session.run_scenario('test')
        self.assertTrue(qa.TRACES[result['trace_id']]['completed'])
        self.assertEqual(1, len(session.warnings))


if __name__ == '__main__':
    unittest.main()

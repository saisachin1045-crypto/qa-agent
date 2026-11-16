import tempfile
import unittest
from qa_agent import QASession, check_rules


class QATests(unittest.TestCase):
    def test_clean_agent_and_isolated_orders(self):
        session = QASession().run()
        self.assertEqual(12, len(session.store.traces))
        self.assertEqual([], session.bugs)
        self.assertTrue(all(t['completed'] for t in session.store.traces.values()))
        self.assertTrue(all(not t['orders']['A100']['refunded'] for t in session.store.traces.values()))

    def test_mutations_detect_all_five_rules(self):
        session = QASession(faulty=True).run()
        self.assertEqual({'P1', 'P2', 'P3', 'P4', 'P5'}, {b['rule'] for b in session.bugs})
        for bug in session.bugs:
            trace = session.store.traces[bug['trace_id']]
            self.assertTrue(set(bug['span_ids']) <= {s['id'] for s in trace['spans']})

    def test_evidence_gate_rejects_fabricated_and_irrelevant_evidence(self):
        session = QASession(faulty=True).run()
        bug = session.bugs[0]
        self.assertIn('error', session.file_bug(bug['trace_id'], bug['rule'], 'fake', ['invented']))
        trace = session.store.traces[bug['trace_id']]
        model = next(s['id'] for s in trace['spans'] if s['name'] == 'model_turn')
        self.assertIn('error', session.file_bug(trace['id'], bug['rule'], 'wrong evidence', [model]))
        other = next(t for t in session.store.traces.values() if t['id'] != trace['id'])
        self.assertIn('error', session.file_bug(other['id'], bug['rule'], 'wrong trace', bug['span_ids']))

    def test_budget_and_partial_run_reporting(self):
        session = QASession(max_turns=3).run()
        self.assertEqual(3, session.turns)
        self.assertEqual(2, len(session.store.traces))
        self.assertFalse(list(session.store.traces.values())[-1]['completed'])
        with tempfile.TemporaryDirectory() as directory:
            report = session.write_report(directory)
            self.assertEqual(1, report['completed'])

    def test_scenario_cap(self):
        session = QASession(max_scenarios=100).run()
        self.assertEqual(12, len(session.store.traces))
        self.assertIn('error', session.run_scenario('test', 'A100'))

    def test_backend_blocks_invalid_refunds(self):
        session = QASession()
        result = session.run_scenario('refund', 'A101')
        trace = session.store.traces[result['trace_id']]
        state = {k: dict(v) for k, v in trace['orders'].items()}
        output = session.support_tool(trace['id'], 'refund_order', {'order_id': 'A101', 'amount': 60}, state)
        self.assertFalse(output['ok'])
        self.assertIn('P3', {v['rule'] for v in check_rules(trace)['violations']})

    def test_openai_tool_loop_with_mock_model(self):
        class FakeModel:
            def __init__(self):
                self.calls = 0
            def chat(self, messages, schemas):
                self.calls += 1
                if self.calls == 1:
                    return {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'c1', 'type': 'function', 'function': {'name': 'lookup_order', 'arguments': '{"order_id":"A100"}'}}]}
                if self.calls == 2:
                    return {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'c2', 'type': 'function', 'function': {'name': 'refund_order', 'arguments': '{"order_id":"A100","amount":80}'}}]}
                return {'role': 'assistant', 'content': '{"order_id":"A100","refund_succeeded":true,"message":"Refund completed"}'}
        session = QASession(mode='openai')
        session.model = FakeModel()
        result = session.run_scenario('Refund A100', 'A100')
        self.assertTrue(result['completed'])
        self.assertEqual([], check_rules(session.store.traces[result['trace_id']])['violations'])
        self.assertEqual(3, session.turns)


if __name__ == '__main__':
    unittest.main()

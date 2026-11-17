import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import qa_agent as qa
from corpus import load_dataset,reviewed_variant
from evaluation import metrics
from verification import FixVerifier
from benchmark import run_benchmark
from watch import scan_once
from test_qa_agent import Target


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        qa.TRACES.clear();self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.raw=json.loads(Path('examples/reviewed-traces.json').read_text())

    def dataset(self,raw=None):
        path=self.root/'dataset.json';path.write_text(json.dumps(raw or self.raw));return load_dataset(path)

    def test_reviewed_inputs_do_not_send_expected_answers(self):
        dataset=self.dataset();target=Target();session=qa.QASession(target=target,dataset=dataset)
        tid=session.run_scenario('example_tool_failure')['trace_id']
        self.assertTrue(qa.TRACES[tid]['completed'])
        self.assertNotIn('expected',target.seen[0]);self.assertNotIn('oracle',target.seen[0])
        self.assertEqual(list(dataset),session.tools[0]['function']['parameters']['properties']['kind']['enum'])

    def test_reject_unreviewed_fabricated_oracle_and_leaked_labels(self):
        mutations=[lambda d:d['cases'][0].pop('review'),
                   lambda d:d['cases'][0]['expected'].update(decisive_span_ids=['fake']),
                   lambda d:d['cases'][0]['packet'].update(expected='hidden answer')]
        for mutate in mutations:
            raw=copy.deepcopy(self.raw);mutate(raw)
            with self.assertRaises(ValueError):self.dataset(raw)

    def test_production_variants_are_separately_labeled(self):
        raw=copy.deepcopy(self.raw);raw['cases'][0]['source']='production'
        case=self.dataset(raw)['example_tool_failure']
        derived=reviewed_variant(case,'untrusted_log_text')
        self.assertEqual('derived',derived['source'])
        self.assertEqual(case['oracle'],derived['oracle'])
        with self.assertRaises(ValueError):reviewed_variant(case,'irrelevant_trace')

    def test_benchmark_executes_every_case_in_bounded_batches(self):
        result=run_benchmark(self.dataset(),Target(),self.root/'reports',max_scenarios=2)
        report=json.loads((self.root/'reports/benchmark.json').read_text())
        self.assertEqual(0,result);self.assertEqual(3,report['attempted_cases'])
        self.assertEqual(2,len(report['batches']));self.assertEqual([],report['unattempted_cases'])
        self.assertEqual({},qa.TRACES)  # Long-running watcher does not retain old trace objects.

    def test_metrics_are_computed_not_claimed_by_model(self):
        dataset=self.dataset();session=qa.QASession(target=Target(),dataset=dataset)
        tids=[session.run_scenario(k)['trace_id'] for k in dataset]
        # One TP, one FP, one FN from distinct deterministic label sets.
        failed=qa.TRACES[tids[0]]
        diagnosis=next(s['output'] for s in failed['spans'] if s['name']=='diagnosis_output')
        diagnosis['detections'][0]['name']='Hallucination' # FP plus missed Tool Failure.
        duplicate=copy.deepcopy(failed);duplicate['spans']=copy.deepcopy(qa.TRACES[tids[0]]['spans'])
        next(s['output'] for s in duplicate['spans'] if s['name']=='diagnosis_output')['detections'][0]['name']='Tool Failure'
        result=metrics([failed,duplicate])['by_source']['synthetic']
        self.assertEqual((1,1,1),(result['tp'],result['fp'],result['fn']))
        self.assertEqual(.5,result['precision']);self.assertEqual(.5,result['recall'])
        self.assertEqual(1,result['unique_cases'])
        self.assertEqual(2,result['latency_ms']['samples'])

    def verifier_payload(self,**changes):
        data={'baseline_failure_reproduced':True,'candidate_tests_passed':True,'regression_tests_passed':True,
              'checks':[{'name':'reproduce-and-retest','passed':True}],
              'sandbox_id':'isolated-test-123','artifact_refs':['artifact://test-report']}
        data.update(changes);return data

    def verifier(self,payload):
        script=self.root/'verify.py'
        script.write_text('import json,sys\nrequest=json.load(sys.stdin)\nassert "diagnosis" in request\nprint('+repr(json.dumps(payload))+')\n')
        return FixVerifier([sys.executable,str(script)])

    def test_verifier_uses_test_evidence_not_zero_exit(self):
        result=self.verifier(self.verifier_payload(candidate_tests_passed=False)).verify({}, {})
        self.assertEqual('failed',result['status'])
        result=self.verifier(self.verifier_payload(baseline_failure_reproduced=False)).verify({}, {})
        self.assertEqual('inconclusive',result['status'])
        result=self.verifier(self.verifier_payload()).verify({}, {})
        self.assertEqual('verified_by_runner',result['status'])
        with self.assertRaises(ValueError):self.verifier({'passed':True}).verify({}, {})

    def test_failed_fix_is_evidenced_policy_failure(self):
        session=qa.QASession(target=Target(),verifier=self.verifier(self.verifier_payload(regression_tests_passed=False)))
        tid=session.run_scenario('tool_failure')['trace_id']
        checks=session.dispatch('check_rules',{'trace_id':tid});session.dispatch('get_trace',{'trace_id':tid})
        finding=next(v for v in checks['violations'] if 'Candidate fix' in v['message'])
        bug=qa.file_bug(session,tid,'P4','Fix fails',finding['message'],finding['span_ids'])
        self.assertTrue(bug['accepted'])
        self.assertEqual(1,qa.exit_code(session,qa.write_report(session,self.root/'reports')))

    def test_unreproduced_fix_does_not_pass(self):
        session=qa.QASession(target=Target(),verifier=self.verifier(self.verifier_payload(baseline_failure_reproduced=False)))
        tid=session.run_scenario('tool_failure')['trace_id']
        checks=session.dispatch('check_rules',{'trace_id':tid});session.dispatch('get_trace',{'trace_id':tid})
        self.assertTrue(checks['verification_inconclusive'])
        self.assertEqual(2,qa.exit_code(session,qa.write_report(session,self.root/'reports')))

    def test_watcher_deduplicates_and_rechecks_new_revisions(self):
        inbox=self.root/'inbox';inbox.mkdir();(inbox/'case.json').write_text(json.dumps(self.raw))
        calls=[]
        def runner(dataset,dest):calls.append(list(dataset));return 1
        state=self.root/'state.json';output=self.root/'output'
        self.assertEqual(1,len(scan_once(inbox,output,state,runner,'release-1')))
        self.assertEqual([],scan_once(inbox,output,state,runner,'release-1'))
        self.assertEqual(1,len(scan_once(inbox,output,state,runner,'release-2')))
        self.assertEqual(2,len(calls))

    def test_invalid_queue_item_is_recorded_once_without_paid_retry(self):
        inbox=self.root/'inbox';inbox.mkdir();(inbox/'bad.json').write_text('{')
        runner=lambda *args:self.fail('invalid input must not reach target')
        result=scan_once(inbox,self.root/'output',self.root/'state.json',runner)
        self.assertEqual(2,result[0]['exit_code'])
        self.assertEqual([],scan_once(inbox,self.root/'output',self.root/'state.json',runner))

    def test_watcher_lock_prevents_duplicate_consumer(self):
        (self.root/'state.lock').mkdir()
        with self.assertRaises(RuntimeError):
            scan_once(self.root/'inbox',self.root/'output',self.root/'state.json',lambda *_:0)

    def test_previous_failed_fix_cannot_resolve_without_verifier(self):
        history=self.root/'history.json'
        session=qa.QASession(target=Target(),history_path=history,
            verifier=self.verifier(self.verifier_payload(candidate_tests_passed=False)))
        tid=session.run_scenario('tool_failure')['trace_id']
        finding=next(v for v in qa.check_rules(tid)['violations'] if 'Candidate fix' in v['message'])
        bug=qa.file_bug(session,tid,'P4','Failed fix',finding['message'],finding['span_ids'])['bug']
        later=qa.QASession(target=Target(),history_path=history)
        replay=later.run_scenario('tool_failure',regression_id=bug['regression_id'])['trace_id']
        result=later.dispatch('check_rules',{'trace_id':replay})
        self.assertEqual('inconclusive',result['regression']['outcome'])
        self.assertEqual('open',later.regressions.cases[bug['regression_id']]['status'])


if __name__=='__main__':unittest.main()

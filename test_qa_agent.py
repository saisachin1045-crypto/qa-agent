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
from scenarios import build_scenario, CASE_NAMES, VARIANTS, DEFINITIONS
from targets import CommandTarget, ReferenceTarget


def golden(scenario):
    """TEST ORACLE ONLY. This is not a NeatLogs agent implementation."""
    o=scenario['oracle']
    return {'assessment':o['assessment'],'detections':[
        {'name':n,'polarity':DEFINITIONS[n][0],'scope':DEFINITIONS[n][1],'evidence_span_ids':o['decisive_span_ids']}
        for n in o['required_detections']], 'root_cause_span_ids':o['decisive_span_ids'],
        'recommendation':{'action':o['allowed_actions'][0],'text':'Synthetic expected recommendation'},
        'change_applied':False,'explanation':'Synthetic expected analysis'}


class Target:
    label='test-double'
    def __init__(self,mutate=None): self.mutate=mutate; self.seen=[]
    def analyze(self,packet,session,tid):
        self.seen.append(copy.deepcopy(packet))
        result=copy.deepcopy(golden(qa.TRACES[tid]['scenario']))
        if self.mutate: self.mutate(result)
        return result


def call(name,**args):
    return {'role':'assistant','content':None,'tool_calls':[{'id':'call_'+name,'type':'function',
            'function':{'name':name,'arguments':json.dumps(args)}}]}


class Model:
    def __init__(self,replies): self.replies=iter(replies)
    def chat(self,messages,tools):
        r=next(self.replies)
        return r(messages) if callable(r) else r


class QATests(unittest.TestCase):
    def setUp(self): qa.TRACES.clear()

    def run_case(self,kind,mutate=None,variant='baseline'):
        session=qa.QASession(target=Target(mutate))
        tid=session.run_scenario(kind,variant)['trace_id']
        return session,tid,qa.check_rules(tid)

    def test_all_fixtures_and_variants_have_valid_control_results(self):
        for kind in CASE_NAMES:
            for variant in VARIANTS:
                with self.subTest(kind=kind,variant=variant):
                    _,_,result=self.run_case(kind,variant=variant)
                    self.assertTrue(result['completed']);self.assertEqual([],result['violations'])

    def test_each_policy_detects_its_mutation(self):
        mutations={
            'P1':lambda r:r['detections'][0]['evidence_span_ids'].append('fabricated'),
            'P2':lambda r:r.update(detections=[]),
            'P3':lambda r:r.update(root_cause_span_ids=[]),
            'P4':lambda r:r['recommendation'].update(action='delete_database'),
            'P5':lambda r:r.update(change_applied=True)}
        for rule,mutation in mutations.items():
            with self.subTest(rule=rule):
                _,_,checks=self.run_case('tool_failure',mutation)
                self.assertIn(rule,{v['rule'] for v in checks['violations']})

    def test_other_trace_evidence_is_rejected(self):
        session=qa.QASession(target=Target())
        tid=session.run_scenario('tool_failure','irrelevant_trace')['trace_id']
        trace=qa.TRACES[tid]
        foreign=trace['scenario']['packet']['otel']['resourceSpans'][0]['scopeSpans'][0]['spans'][-1]['spanId']
        out=next(s for s in trace['spans'] if s['name']=='diagnosis_output')['output']
        out['root_cause_span_ids'].append(foreign)
        self.assertIn('P1',{v['rule'] for v in qa.check_rules(tid)['violations']})

    def test_monitoring_recovery_and_abstention_controls(self):
        for kind in ['neutral_monitors','workflow_without_llm','guardrail_rejection','recovered_retry','superseded_constraint','missing_evidence']:
            _,_,checks=self.run_case(kind,lambda r:r.update(assessment='issue'))
            self.assertIn('P5',{v['rule'] for v in checks['violations']})

    def test_session_detection_requires_both_attempts(self):
        _,_,checks=self.run_case('repeated_failure',lambda r:r['detections'][0].update(evidence_span_ids=r['root_cause_span_ids'][:1]))
        self.assertIn('P3',{v['rule'] for v in checks['violations']})

    def test_neutral_catalog_does_not_mean_every_neutral_label_applies(self):
        def mutate(r):
            r['detections']=[{'name':'Guardrail Rejected','polarity':'Neutral','scope':'span',
                              'evidence_span_ids':r['root_cause_span_ids']}]
        _,_,checks=self.run_case('tool_failure',mutate)
        self.assertIn('P2',{v['rule'] for v in checks['violations']})

    def test_four_tool_planner_files_evidence(self):
        ids=[]
        def read(messages):
            ids.append(json.loads(messages[-1]['content'])['trace_id']);return call('get_trace',trace_id=ids[-1])
        def check(messages): return call('check_rules',trace_id=ids[-1])
        def file(messages):
            v=json.loads(messages[-1]['content'])['violations'][0]
            return call('file_bug',trace_id=ids[-1],rule=v['rule'],title='Unsupported applied-fix claim',description=v['message'],span_ids=v['span_ids'])
        planner=Model([call('run_scenario',kind='tool_failure',variant='baseline'),read,check,file,
                       {'role':'assistant','content':'The target claimed to apply a fix.'}])
        session=qa.QASession(qa_model=planner,target=Target(lambda r:r.update(change_applied=True))).run()
        self.assertIsNone(session.error);self.assertEqual(1,len(session.bugs));self.assertEqual(5,session.turns)
        self.assertEqual(['run_scenario','get_trace','check_rules','file_bug'],[t['function']['name'] for t in qa.QA_TOOLS])
        with tempfile.TemporaryDirectory() as d:
            report=qa.write_report(session,d)
            self.assertEqual(1,qa.exit_code(session,report))
            self.assertEqual('test-double',report['target'])
            self.assertIn('claimed to apply',Path(d,'qa_report.md').read_text())

    def test_evidence_gate_rejects_fabrication_and_allows_reviewable_observations(self):
        s,tid,_=self.run_case('neutral_monitors')
        sid=qa.TRACES[tid]['spans'][0]['id']
        self.assertIn('error',qa.file_bug(s,tid,'OBSERVATION','x','x',['fake']))
        self.assertIn('error',qa.file_bug(s,tid,'P1','x','x',[sid]))
        self.assertEqual('needs_review',qa.file_bug(s,tid,'OBSERVATION','Wording concern','Review wording',[sid])['bug']['status'])

    def test_reference_model_turns_share_budget(self):
        target=ReferenceTarget(Model([]))
        planner=Model([call('run_scenario',kind='tool_failure',variant='baseline')])
        s=qa.QASession(qa_model=planner,target=target,max_turns=1).run()
        self.assertEqual(1,s.turns);self.assertEqual('turn_limit',s.stop_reason)
        self.assertEqual([],s.trace_ids)  # No target invocation after the shared budget is spent.

    def test_scenario_cap(self):
        s=qa.QASession(target=Target(),max_scenarios=999,max_turns=999)
        for _ in range(12): self.assertIn('trace_id',s.run_scenario('neutral_monitors'))
        self.assertIn('error',s.run_scenario('neutral_monitors'));self.assertEqual(60,s.max_turns)

    def test_malformed_target_is_incomplete_not_a_pass(self):
        s,tid,checks=self.run_case('tool_failure',lambda r:r.pop('recommendation'))
        self.assertFalse(checks['completed'])
        self.assertTrue(any(span['error'] for span in qa.TRACES[tid]['spans']))

    def test_command_adapter_receives_only_packet_and_timeout_is_visible(self):
        scenario=build_scenario('tool_failure')
        with tempfile.TemporaryDirectory() as d:
            script=Path(d,'adapter.py')
            script.write_text('import sys,json\np=json.load(sys.stdin)\nassert "oracle" not in p and "kind" not in p\nprint('+repr(json.dumps(golden(scenario)))+')\n')
            target=CommandTarget([sys.executable,str(script)],'test-command')
            result=target.analyze(scenario['packet'],None,'unused')
            self.assertEqual('issue',result['assessment'])
            with patch('targets.subprocess.run',side_effect=subprocess.TimeoutExpired(['adapter'],1)):
                s=qa.QASession(target=target);tid=s.run_scenario('tool_failure')['trace_id']
                self.assertFalse(qa.TRACES[tid]['completed'])

    def test_unfiled_deterministic_failure_still_fails_ci(self):
        s,tid,_=self.run_case('tool_failure',lambda r:r.update(change_applied=True))
        s.dispatch('get_trace',{'trace_id':tid});s.dispatch('check_rules',{'trace_id':tid})
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(1,qa.exit_code(s,qa.write_report(s,d)))
        self.assertEqual([],s.bugs)

    def test_nightly_without_adapter_explicitly_skips_real_target(self):
        with tempfile.TemporaryDirectory() as d:
            event=Path(d,'event.json');event.write_text('{}')
            summary=Path(d,'summary.md')
            env=dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_STEP_SUMMARY=str(summary),
                     OPENAI_API_KEY='test-key', NEATLOGS_ADAPTER_COMMAND='', NEATLOGS_TARGET_ID='')
            result=subprocess.run([sys.executable,'scripts/run_ci.py'],capture_output=True,text=True,env=env)
            self.assertEqual(0,result.returncode)
            self.assertIn('no deployed-agent QA was run',summary.read_text())


if __name__=='__main__': unittest.main()

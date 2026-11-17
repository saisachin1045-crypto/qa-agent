import json
from pathlib import Path
import tempfile
import unittest
import qa_agent as qa
from regression import RegressionStore
from test_qa_agent import Target, Model, call


class RegressionTests(unittest.TestCase):
    def setUp(self):
        qa.TRACES.clear();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name,'history.json')

    def seed(self,rule='P5'):
        s=qa.QASession(target=Target(lambda r:r.update(change_applied=True)),history_path=self.path)
        tid=s.run_scenario('tool_failure')['trace_id'];v=qa.check_rules(tid)['violations'][0]
        result=qa.file_bug(s,tid,rule,'Bad diagnosis','Target claimed applied fix',v['span_ids'])
        return result['bug']['regression_id']

    def replay(self,fail=False):
        s=qa.QASession(target=Target((lambda r:r.update(change_applied=True)) if fail else None),history_path=self.path)
        cid=s.selected[0];tid=s.run_scenario('neutral_monitors',regression_id=cid)['trace_id']
        return s,tid,s.dispatch('check_rules',{'trace_id':tid})['regression']

    def test_fail_pass_reopen(self):
        cid=self.seed()
        _,_,r=self.replay(True);self.assertEqual('failed',r['outcome'])
        _,_,r=self.replay();self.assertEqual('passed',r['outcome'])
        self.assertEqual('resolved',RegressionStore(self.path).cases[cid]['status'])
        _,_,r=self.replay(True);self.assertEqual('reopened',RegressionStore(self.path).cases[cid]['status'])

    def test_exact_packet_and_variants(self):
        cid=self.seed();s,tid,_=self.replay()
        saved=RegressionStore(self.path).cases[cid]['scenario']
        self.assertEqual(saved,qa.TRACES[tid]['scenario'])
        self.assertIn('error',s.run_scenario('tool_failure','baseline',related_to=cid))
        other=s.run_scenario('tool_failure','untrusted_log_text',related_to=cid)['trace_id']
        r=s.dispatch('check_rules',{'trace_id':other})['regression']
        self.assertEqual('related',r['kind']);self.assertEqual('passed',r['outcome'])
        self.assertEqual(1,len(s.regressions.cases[cid]['history']))

    def test_duplicate_span_ids_do_not_create_duplicate_cases(self):
        self.seed()
        s=qa.QASession(target=Target(lambda r:r.update(change_applied=True)))
        tid=s.run_scenario('tool_failure')['trace_id'];v=qa.check_rules(tid)['violations'][0]
        bug=qa.file_bug(s,tid,'P5','Different wording','Same failure',v['span_ids'])['bug']
        store=RegressionStore(self.path)
        store.remember(bug,qa.TRACES[tid]['scenario'],s.contract,s.target.label)
        self.assertEqual(1,len(store.cases))

    def test_target_identity_isolation(self):
        self.seed();other=Target();other.label='another-runner'
        s=qa.QASession(target=other,history_path=self.path);self.assertEqual([],s.selected)

    def test_stale_and_observation_not_resolved(self):
        cid=self.seed('OBSERVATION');_,_,r=self.replay();self.assertEqual('needs_review',r['outcome'])
        store=RegressionStore(self.path);store.cases[cid]['contract']='old';store.save()
        s,_,r=self.replay();self.assertEqual('stale',r['outcome']);self.assertEqual('open',s.regressions.cases[cid]['status'])

    def test_unfinished_retests_never_mark_resolved(self):
        cid=self.seed()
        s=qa.QASession(target=Target(),qa_model=Model([call('run_scenario',kind='tool_failure',variant='baseline',regression_id=cid)]),max_turns=1,history_path=self.path).run()
        self.assertEqual([cid],s.pending('exact'));self.assertEqual('open',s.regressions.cases[cid]['status'])

    def test_same_run_failure_dominates_later_pass(self):
        cid=self.seed();s,tid,_=self.replay(True)
        s.regressions.record(cid,'passed',s.run_id,'another-trace')
        self.assertEqual('failed',s.regressions.cases[cid]['history'][-1]['outcome'])

    def test_legacy_history_preserved_but_not_replayed(self):
        legacy={'old':{'rule':'P5','message':'refund order'}}
        self.path.write_text(json.dumps({'version':1,'cases':legacy}))
        store=RegressionStore(self.path);self.assertEqual({},store.cases);self.assertEqual(legacy,store.archived)
        store.save();self.assertEqual(legacy,RegressionStore(self.path).archived)

    def test_corrupt_history_not_overwritten(self):
        self.path.write_text('invalid')
        with self.assertRaises(ValueError): RegressionStore(self.path)
        self.assertEqual('invalid',self.path.read_text())


if __name__=='__main__': unittest.main()

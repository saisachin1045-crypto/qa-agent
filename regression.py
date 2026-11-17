"""Persist diagnosis regressions; archive old support-agent cases without reusing their oracle."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:20]


class RegressionStore:
    def __init__(self,path=None):
        self.path=Path(path) if path else None
        self.cases={}
        self.archived={}
        if self.path and self.path.exists():
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version')==1 and isinstance(data.get('cases'),dict):
                self.archived=data['cases']
            elif data.get('version')==2 and data.get('domain')=='neatlogs_diagnosis' and isinstance(data.get('cases'),dict):
                self.cases=data['cases']; self.archived=data.get('archived_legacy_cases',{})
                for key,c in self.cases.items():
                    if (not isinstance(c,dict) or not {'id','rule','scenario','contract','target','status','history','last_run','description'}<=c.keys()
                        or c['id']!=key): raise ValueError('Invalid regression case: '+key)
            else: raise ValueError('Unsupported regression history; file was not modified')

    def save(self):
        if not self.path: return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.qa-',suffix='.json')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump({'version':2,'domain':'neatlogs_diagnosis','cases':self.cases,'archived_legacy_cases':self.archived},f,indent=2)
            os.replace(name,self.path)
        finally:
            if os.path.exists(name): os.unlink(name)

    def remember(self,bug,scenario,contract,target):
        # Fixture kind/variant define deterministic semantics; random OTel IDs do not create new bugs.
        key='reg_'+fingerprint({'rule':bug['rule'],'kind':scenario['kind'],'variant':scenario['variant'],
             'case_fingerprint':scenario.get('case_fingerprint'),
             'contract':contract,'target':target,'observation':bug['title'] if bug['rule']=='OBSERVATION' else None})
        if key not in self.cases:
            self.cases[key]={'id':key,'rule':bug['rule'],'scenario':copy.deepcopy(scenario),'contract':contract,
                'target':target,'description':bug['description'],'title':bug['title'],'status':'open',
                'history':[],'last_run':None,'source_trace_id':bug['trace_id'],'source_span_ids':bug['span_ids']}
        elif self.cases[key]['status']=='resolved': self.cases[key]['status']='reopened'
        if bug.get('requires_fix_verification'): self.cases[key]['requires_fix_verification']=True
        return key

    def select(self,limit,target):
        eligible=[k for k,c in self.cases.items() if c['target']==target]
        return sorted(eligible,key=lambda k:(self.cases[k]['last_run'] or '',k))[:limit]

    def outcome(self,key,checks,contract):
        case=self.cases[key]
        if case['contract']!=contract: return 'stale'
        if not checks['completed'] or checks.get('verification_inconclusive'): return 'inconclusive'
        if case.get('requires_fix_verification') and checks.get('verification_status') not in {'verified_by_runner','failed'}:
            return 'inconclusive'
        if case['rule']=='OBSERVATION': return 'needs_review'
        return 'failed' if any(v['rule']==case['rule'] for v in checks['violations']) else 'passed'

    def record(self,key,outcome,run_id,trace_id):
        c=self.cases[key]
        prior=next((h for h in c['history'] if h['run_id']==run_id),None)
        if prior and prior['outcome']=='failed': return
        if prior: c['history'].remove(prior)
        if outcome=='failed': c['status']='reopened' if c['status'] in {'resolved','reopened'} else 'open'
        elif outcome=='passed': c['status']='resolved'
        c['last_run']=run_id
        c['history'].append({'run_id':run_id,'trace_id':trace_id,'outcome':outcome})
        c['history']=c['history'][-50:]

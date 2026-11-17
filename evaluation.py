"""Evidence-derived metrics; missing measurements remain null."""
import math
import statistics


def metrics(traces):
    groups={}
    for trace in traces:
        group=trace['scenario'].get('source','synthetic')
        m=groups.setdefault(group,{'attempts':0,'completed':0,'failed_execution':0,'tp':0,'fp':0,'fn':0,
            'correct_assessments':0,'cases':set(),'durations_ms':[],'fix_verification':{}})
        m['attempts']+=1
        m['cases'].add(trace['scenario'].get('case_fingerprint',trace['scenario']['kind']))
        calls=[s for s in trace['spans'] if s['name']=='target_call']
        if calls: m['durations_ms'].append(calls[-1]['duration_ms'])
        status=trace.get('verification',{}).get('status','not_configured')
        m['fix_verification'][status]=m['fix_verification'].get(status,0)+1
        outputs=[s['output'] for s in trace['spans'] if s['name']=='diagnosis_output']
        if not trace['completed'] or not outputs:
            m['failed_execution']+=1;continue
        m['completed']+=1
        actual={d['name'] for d in outputs[-1]['detections'] if d['polarity']=='Negative'}
        expected=set(trace['scenario']['oracle']['required_detections'])
        m['tp']+=len(actual & expected);m['fp']+=len(actual-expected);m['fn']+=len(expected-actual)
        m['correct_assessments']+=outputs[-1]['assessment']==trace['scenario']['oracle']['assessment']
    for m in groups.values():
        m['unique_cases']=len(m.pop('cases'))
        m['precision']=m['tp']/(m['tp']+m['fp']) if m['tp']+m['fp'] else None
        m['recall']=m['tp']/(m['tp']+m['fn']) if m['tp']+m['fn'] else None
        m['assessment_accuracy']=m['correct_assessments']/m['completed'] if m['completed'] else None
        times=sorted(m.pop('durations_ms'))
        m['latency_ms']={'samples':len(times),'median':statistics.median(times) if times else None,
                         'p95':times[math.ceil(.95*len(times))-1] if times else None}
    return {'by_source':groups,'population':'executed reviewed/fixture cases only; repeat runs are not independent samples',
            'accuracy_excludes_failed_executions':True,'cost_usd':None,
            'cost_note':'Not calculated: external runner billing and model prices are not supplied.'}

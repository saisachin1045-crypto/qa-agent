"""Evaluate every reviewed case, not just an LLM-selected sample."""
import json
from pathlib import Path
from evaluation import metrics


def run_benchmark(dataset,target,destination,verifier=None,max_scenarios=12,max_turns=60,history_path=None):
    from qa_agent import QASession,TRACES,write_report,exit_code,file_bug
    dest=Path(destination);dest.mkdir(parents=True,exist_ok=True)
    batch_size=min(12,max(1,max_scenarios));keys=list(dataset)
    batches=[];traces=[];codes=[];cleanup=[]
    try:
        for offset in range(0,len(keys),batch_size):
            subset={k:dataset[k] for k in keys[offset:offset+batch_size]}
            session=QASession(target=target,dataset=subset,verifier=verifier,
                              max_scenarios=batch_size,max_turns=max_turns,history_path=history_path)
            # A benchmark must execute the provided dataset, not divert into history.
            session.selected=[]
            for key in subset:
                try:
                    result=session.run_scenario(key)
                    tid=result['trace_id']
                    session.dispatch('get_trace',{'trace_id':tid})
                    checks=session.dispatch('check_rules',{'trace_id':tid})
                    for finding in checks['violations']:
                        file_bug(session,tid,finding['rule'],finding['message'],finding['message'],finding['span_ids'])
                except Exception as exc:
                    session.error=str(exc);session.stop_reason='execution_failed';break
            session.stop_reason=session.stop_reason or 'benchmark_batch_completed'
            session.summary='Deterministic benchmark of reviewed cases; target: '+target.label
            folder=dest/f'batch-{offset//batch_size+1:03d}'
            report=write_report(session,folder)
            code=exit_code(session,report);codes.append(code)
            batches.append({'report':str(folder/'qa_report.json'),'exit_code':code,'attempted':report['scenarios']})
            traces.extend(TRACES[t] for t in session.trace_ids)
            cleanup.extend(session.trace_ids+[session.session_trace_id])
        attempted={t['scenario']['kind'] for t in traces}
        summary={'target':target.label,'requested_cases':len(dataset),'attempted_cases':len(attempted),
                 'unattempted_cases':sorted(set(dataset)-attempted),'metrics':metrics(traces),'batches':batches}
        (dest/'benchmark.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        return 2 if 2 in codes or summary['unattempted_cases'] else 1 if 1 in codes else 0
    finally:
        for tid in cleanup: TRACES.pop(tid,None)

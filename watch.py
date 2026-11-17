#!/usr/bin/env python3
"""Continuously evaluate atomically delivered, reviewed trace-case JSON files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from benchmark import run_benchmark
from corpus import load_dataset
from targets import CommandTarget,ReferenceTarget
from verification import FixVerifier


def scan_once(inbox,output,state_path,runner,identity='default'):
    inbox=Path(inbox);output=Path(output);state_path=Path(state_path)
    inbox.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    state_path.parent.mkdir(parents=True,exist_ok=True)
    lock=state_path.with_suffix('.lock')
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError('Another watcher owns this state file (or a stale lock needs review)')
    try:
        state=json.loads(state_path.read_text()) if state_path.exists() else {'version':1,'processed':{}}
        if state.get('version')!=1 or not isinstance(state.get('processed'),dict): raise ValueError('Invalid watcher state')
        processed=[]
        for path in sorted(inbox.glob('*.json')):
            if path.is_symlink(): continue
            raw=path.read_bytes()
            digest=hashlib.sha256(identity.encode()+b'\0'+raw).hexdigest()
            if digest in state['processed']: continue
            dest=output/digest[:20];dest.mkdir(exist_ok=True)
            # Freeze exactly the bytes hashed. Producer should rename .tmp to .json after completion.
            snapshot=dest/'input.json';snapshot.write_bytes(raw)
            try:
                dataset=load_dataset(snapshot)
                code=runner(dataset,dest)
                if code not in (0,1,2): raise ValueError('Invalid runner exit code')
                record={'source':path.name,'exit_code':code,'report_dir':str(dest)}
            except Exception as exc:
                record={'source':path.name,'exit_code':2,'error':str(exc),'report_dir':str(dest)}
                (dest/'error.json').write_text(json.dumps(record,indent=2))
            state['processed'][digest]=record
            temp=state_path.with_suffix('.tmp')
            temp.write_text(json.dumps(state,indent=2));os.replace(temp,state_path)
            processed.append(record)
        return processed
    finally:
        lock.rmdir()


def main():
    from qa_agent import Model
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inbox',required=True);p.add_argument('--output',default='reports/watch')
    p.add_argument('--state',default='.qa/watch-state.json');p.add_argument('--history',default='.qa/regressions.json')
    p.add_argument('--target',choices=['command','reference'],required=True)
    p.add_argument('--adapter-command');p.add_argument('--target-id',default='')
    p.add_argument('--verifier-command');p.add_argument('--model',default='gpt-4.1-mini')
    p.add_argument('--interval',type=float,default=5);p.add_argument('--once',action='store_true')
    p.add_argument('--revision',default='initial',help='Release identifier; changes re-evaluate unchanged inbox files')
    p.add_argument('--max-scenarios',type=int,default=12);p.add_argument('--max-turns',type=int,default=60)
    a=p.parse_args()
    if a.interval<1: p.error('--interval must be at least 1 second')
    if a.target=='command':
        if not a.adapter_command or not a.target_id: p.error('command mode requires adapter command and target ID')
        target=CommandTarget(json.loads(a.adapter_command),a.target_id)
    else: target=ReferenceTarget(Model(a.model))
    verifier=FixVerifier(json.loads(a.verifier_command)) if a.verifier_command else None
    identity=json.dumps({'target':target.label,'model':a.model,'adapter':a.adapter_command,'verifier':a.verifier_command,'revision':a.revision})
    def run(dataset,dest):
        return run_benchmark(dataset,target,dest,verifier,a.max_scenarios,a.max_turns,a.history)
    try:
        while True:
            results=scan_once(a.inbox,a.output,a.state,run,identity)
            for result in results: print(json.dumps(result),flush=True)
            if a.once: return max((r['exit_code'] for r in results),default=0)
            time.sleep(a.interval)
    except KeyboardInterrupt: return 0


if __name__=='__main__': raise SystemExit(main())

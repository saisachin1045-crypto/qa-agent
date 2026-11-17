"""Select explicit CI target without substituting a reference run for NeatLogs."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    mode=event.get('inputs',{}).get('target','command')
    reason=None
    if not os.getenv('OPENAI_API_KEY'):
        reason='OPENAI_API_KEY is missing for the QA planner.'
    elif mode=='command' and (not os.getenv('NEATLOGS_ADAPTER_COMMAND') or not os.getenv('NEATLOGS_TARGET_ID')):
        reason='A real NeatLogs runner adapter is not configured; no deployed-agent QA was run.'
    if reason:
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f: f.write('## Diagnosis QA skipped\n'+reason+'\n')
        print(reason)
        return 0
    args=[sys.executable,'qa_agent.py','--target',mode]
    if mode=='command':
        args+=['--adapter-command',os.environ['NEATLOGS_ADAPTER_COMMAND'],'--target-id',os.environ['NEATLOGS_TARGET_ID']]
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
        f.write('## Diagnosis QA target\n'+('Reference LLM only; not NeatLogs.' if mode=='reference' else os.environ['NEATLOGS_TARGET_ID'])+'\n')
    return subprocess.call(args)


if __name__=='__main__': raise SystemExit(main())

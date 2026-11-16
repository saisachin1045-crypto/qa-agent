"""Persistent regression cases and deterministic retest outcomes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


class RegressionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.cases = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version') != 1 or not isinstance(data.get('cases'), dict):
                raise ValueError('Unsupported or invalid regression history')
            self.cases = data['cases']
            for case_id, case in self.cases.items():
                required = {'id', 'rule', 'message', 'orders', 'contract', 'target_orders',
                            'status', 'last_run', 'history', 'title', 'description'}
                if not isinstance(case, dict) or not required <= case.keys() or case['id'] != case_id:
                    raise ValueError('Invalid regression case: ' + case_id)

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix='.regression-', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump({'version': 1, 'cases': self.cases}, output, indent=2)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def remember(self, bug, trace, contract, run_id):
        key = fingerprint({'rule': bug['rule'], 'message': ' '.join(trace['message'].split()),
                           'orders': trace['orders'], 'contract': contract,
                           'observation': bug['title'] if bug['rule'] == 'OBSERVATION' else None})
        case_id = 'reg_' + key
        if case_id not in self.cases:
            targets = sorted({s['input']['order_id'] for s in trace['spans']
                              if s['name'] in {'lookup_order', 'refund_order'}
                              and isinstance(s['input'], dict) and isinstance(s['input'].get('order_id'), str)})
            self.cases[case_id] = {'id': case_id, 'rule': bug['rule'], 'message': trace['message'],
                'orders': copy.deepcopy(trace['orders']), 'contract': contract, 'target_orders': targets,
                'title': bug['title'], 'description': bug['description'], 'status': 'open',
                'last_run': None, 'history': [], 'source': {'trace_id': trace['id'], 'span_ids': bug['span_ids']}}
        case = self.cases[case_id]
        # A rediscovery must not reset a previously resolved case to an anonymous new bug.
        if case['status'] == 'resolved':
            case['status'] = 'reopened'
        case['last_seen_run'] = run_id
        return case_id

    def select(self, limit):
        # Least recently tested first prevents resolved cases from being forgotten.
        return sorted(self.cases, key=lambda key: (
            self.cases[key]['last_run'] or '', self.cases[key]['status'] == 'resolved', key))[:limit]

    def evaluate(self, case_id, trace, checks, contract):
        case = self.cases[case_id]
        if case['contract'] != contract:
            return 'stale', []
        if not trace['completed'] or any(s['name'] == 'run_error' for s in trace['spans']):
            return 'inconclusive', []
        matches = [v for v in checks['violations'] if v['rule'] == case['rule']]
        if matches:
            return 'failed', sorted({sid for v in matches for sid in v['span_ids']})
        if case['rule'] == 'OBSERVATION':
            return 'needs_review', []
        touched = {s['input'].get('order_id') for s in trace['spans']
                   if s['name'] in {'lookup_order', 'refund_order'} and isinstance(s['input'], dict)
                   and not s['error'] and isinstance(s['input'].get('order_id'), str)}
        if (not case['target_orders'] or not set(case['target_orders']) <= touched
                or not any(s['name'] == 'final_answer' for s in trace['spans'])):
            return 'inconclusive', []
        return 'passed', [s['id'] for s in trace['spans'] if s['name'] == 'final_answer']

    def record(self, case_id, outcome, run_id, trace_id, span_ids):
        case = self.cases[case_id]
        previous = next((h for h in case['history'] if h['run_id'] == run_id), None)
        # Repeat check_rules calls cannot manufacture multiple passing runs.
        if previous and previous['outcome'] == 'failed':
            return
        if previous:
            case['history'].remove(previous)
        old = case['status']
        if outcome == 'failed':
            case['status'] = 'reopened' if old in {'resolved', 'reopened'} else 'open'
        elif outcome == 'passed':
            case['status'] = 'resolved'
        # Inconclusive, stale and subjective results never resolve an open bug.
        case['last_run'] = run_id
        case['history'].append({'run_id': run_id, 'outcome': outcome, 'trace_id': trace_id, 'span_ids': span_ids})
        case['history'] = case['history'][-50:]

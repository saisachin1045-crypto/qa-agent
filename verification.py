"""Optional trusted verifier; it must run candidate tests in its own sandbox."""
import json
import subprocess


class FixVerifier:
    def __init__(self,argv,timeout=120):
        if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv):
            raise ValueError('Verifier command must be a nonempty JSON argv array')
        self.argv,self.timeout=argv,timeout

    def verify(self,packet,diagnosis):
        # No shell and no automatic execution of model-generated patch/command text.
        result=subprocess.run(self.argv,input=json.dumps({'packet':packet,'diagnosis':diagnosis}),
                              text=True,capture_output=True,timeout=self.timeout,check=False)
        if result.returncode: raise RuntimeError(f'Verifier exited {result.returncode}')
        evidence=json.loads(result.stdout)
        required={'baseline_failure_reproduced','candidate_tests_passed','regression_tests_passed','checks','sandbox_id','artifact_refs'}
        if not isinstance(evidence,dict) or set(evidence)!=required: raise ValueError('Invalid verifier result schema')
        bools=required-{'checks','sandbox_id','artifact_refs'}
        if any(type(evidence[k]) is not bool for k in bools): raise ValueError('Verifier outcomes must be booleans')
        if not isinstance(evidence['sandbox_id'],str) or not evidence['sandbox_id'].strip(): raise ValueError('Missing sandbox evidence')
        if not isinstance(evidence['artifact_refs'],list) or not evidence['artifact_refs'] or any(not isinstance(x,str) or not x for x in evidence['artifact_refs']):
            raise ValueError('Verifier must provide evidence artifact references')
        if not isinstance(evidence['checks'],list) or not evidence['checks']: raise ValueError('Missing verifier checks')
        for c in evidence['checks']:
            if not isinstance(c,dict) or set(c)!={'name','passed'} or not isinstance(c['name'],str) or not c['name'] or type(c['passed']) is not bool:
                raise ValueError('Malformed verifier check')
        if not evidence['baseline_failure_reproduced']: status='inconclusive'
        elif all(evidence[k] for k in bools) and all(c['passed'] for c in evidence['checks']): status='verified_by_runner'
        else: status='failed'
        return {'status':status,'evidence':evidence}

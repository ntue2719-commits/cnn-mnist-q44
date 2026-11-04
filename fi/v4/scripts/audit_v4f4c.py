#!/usr/bin/env python3
"""Read-only independent V4f4C auditor: valid 1-XSim OR blocked evidence.

A BLOCKED/orphan attempt can be inventoried, but it is NEVER reclassified as a
success, design NONCOMPLETION, or permission to retry in-place. No Vivado calls.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
for d in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(d) not in sys.path:sys.path.insert(0,str(d))
from audit_v4 import require
from pilot400_common import FIELDS,read_csv,sha256
import qualify_runner_v4d as v4da
import qualify_full400_v4f as v4f
import qualify_xsim_failures_v4f4c as q
import qualify_g6_v4f4b as shadow

SCHEMA='CNN_PEP_V4F4C_READ_ONLY_ATTEMPT_AUDIT_V1'


def check_command(cmd:Path,row:dict[str,str],repo:Path|None=None) -> None:
    """Match literal frozen plusargs, reject an unexpected profile-only run."""
    require(cmd.is_file() and not cmd.is_symlink(),
            'V4f4C trial command missing/symlink')
    text=cmd.read_text(encoding='utf-8',errors='strict')
    expected={'FI_OFFSET':row['injection_offset'],
              'SCENARIO_ID':row['scenario_id'],
              'TARGET_ID':row['target_id'],
              'FI_BIT':row['bit_index'],
              'SAMPLE_ID':row['sample_id']}
    if repo is not None:
        sample=(repo/'fi/pilot400/samples'/row['sample_id'])
        expected.update({key:(sample/name).resolve().as_posix() for key,name in (
           ('INPUT_FILE','input.hex'),('POOL1_FILE','golden_pool1.hex'),
           ('POOL2_FILE','golden_pool2.hex'),('LOGITS_FILE','golden.hex'),
           ('PREDICTION_FILE','prediction.hex'))})
    plusargs=re.findall(r'-testplusarg\s+"([^"\r\n]+)"',text)
    seen={}
    for pair in plusargs:
        require('=' in pair,'V4f4C malformed XSim plusarg')
        key,val=pair.split('=',1)
        require(key not in seen,'Duplicate plusarg '+key)
        seen[key]=val
    require(all(seen.get(k)==v for k,v in expected.items()) and
            set(seen)==set(expected) and
            'PILOT_PROFILE_ONLY' not in seen and
            len(plusargs)==len(seen) and
            'call ' in text and 'xsim.bat' in text and
            '-tclbatch' in text,
            'V4f4C XSim command does not match frozen scenario')


def audit_attempt(repo:Path,results:Path,attempt:Path,out:Path) -> dict:
    repo=repo.resolve();results=results.resolve();attempt=attempt.resolve();out=out.resolve()
    require(attempt.parent==results and attempt.name.startswith('v4f4c_') and
            attempt.is_dir() and not attempt.is_symlink(),
            'V4f4C attempt must be an existing fi/results/v4f4c_* directory')
    require(out.parent==results and out.name.startswith('v4f4c_') and
            not out.exists() and out!=attempt,
            'V4f4C auditor requires a fresh, separate output directory')
    v4f.safety_output(repo,out,repo/'data',results)
    require(not any(p.is_symlink() for p in attempt.rglob('*')),
            'V4f4C refuses symlinks anywhere in attempt evidence')
    meta=shadow.strict_json(attempt/'attempt.json')
    q.verify_attempt(attempt,meta,allow_blocked=True)
    q.no_release(meta)
    sid=meta['scenario_id']
    expected=q.select_canary(read_csv(repo/'fi/scenarios/pilot400.csv',FIELDS))
    require(read_csv(attempt/'canary_selection.csv',FIELDS)==expected,
            'Canary selection differs from frozen manifest')
    runs=attempt/'runs'
    run_files={key:runs/(sid+suffix) for key,suffix in
                (('cmd','.cmd'),('log','.log'),('console','.console.log'),
                 ('wrapper','.wrapper.log'))}
    if run_files['cmd'].exists():
        check_command(run_files['cmd'],meta['scenario'],repo)
    if (attempt/q.INFLIGHT).exists():
        inflight=shadow.strict_json(attempt/q.INFLIGHT)
        require(inflight.get('schema')=='V4F4C_INFLIGHT_V1' and
                inflight.get('scenario_id')==sid and
                inflight.get('trial')==meta['trial'] and
                inflight.get('max_xsim_processes')==1,
                'In-flight orphan marker identity inconsistent')
        require(run_files['cmd'].is_file() and
                inflight.get('cmd_sha256')==sha256(run_files['cmd']),
                'In-flight recorded command digest mismatch')
    if meta.get('cmd_sha256') is not None:
        require(sha256(run_files['cmd'])==meta['cmd_sha256'],
                'V4f4C attempted command changed')
    status=meta['status']
    if status==q.SUCCESS:
        require((attempt/q.INFLIGHT).exists() is False and
                meta.get('process',{}).get('classification')=='RAW_RESULT_ELIGIBLE',
                'Committed healthy FI should have no orphan markers')
        require(set(p.name for p in runs.iterdir())==
                {p.name for p in run_files.values()},
                'Healthy attempt contains orphan/unregistered run artifacts')
        require(not any(p.name.endswith('.tmp') for p in attempt.iterdir()),
                'Healthy attempt has unfinished atomic temp files')
        result='ONE_REAL_XSIM_MECHANISM_VERIFIED'
    else:
        # Even a complete, well-formed raw log after a crash is NOT committed.
        require(status in (q.RUNNING,q.BLOCKED) and
                meta.get('xsim_invocations_committed')==0 and
                meta.get('validation') is None,
                'Blocked/orphan attempt cannot claim validated FI outcome')
        if meta.get('process') is not None:
            receipt=meta['process']
            applied,result_count=q.marker_counts(run_files['log'])
            require(receipt.get('marker_evidence')==[applied,result_count] and
                    receipt.get('classification')==q.classify_observed_process(
                        meta['trial'],receipt),
                    'V4f4C failure classification differs from available raw markers')
            require(receipt.get('owned_active_processes_after')==0 and
                    receipt.get('job_object_kill_on_close') is True,
                    'V4f4C failed to account for owned Windows job processes')
        result='INVALID_INFRASTRUCTURE_NOT_FI_OUTCOME'
    inventory={p.relative_to(attempt).as_posix():sha256(p)
               for p in sorted(attempt.rglob('*')) if p.is_file() and
               p.name != 'attempt.json' and not p.is_symlink()}
    receipt={'schema':SCHEMA,'created_utc':datetime.now(timezone.utc).isoformat(),
             'result':result,'attempt_status':status,'scenario_id':sid,
             'trial':meta['trial'],
             'attempt_json_sha256':sha256(attempt/'attempt.json'),
             'input_hashes_rechecked':len(meta['input_pins']),
             'raw_artifact_sha256':inventory,
             'xsim_invocations_committed':1 if status==q.SUCCESS else 0,
             'new_xsim_runs_during_audit':0,
             'scientific_protocol_approved':False,
             'full400_runner_qualified':False,'full400_authorized':False,
             'full400_gate':'BLOCKED_G0_G7_REQUIRED'}
    out.mkdir(parents=True,exist_ok=False)
    v4da.atomic_write_json(out/'v4f4c_audit.json',receipt)
    marker=('V4F4C_AUDIT_HEALTHY_PASS' if status==q.SUCCESS
            else 'V4F4C_AUDIT_INVALID_INFRASTRUCTURE')
    print(marker+': '+sid+'; 0 XSim; FULL400 BLOCKED',flush=True)
    print('V4F4C_AUDIT_OUTPUT: '+str(out),flush=True)
    return receipt


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--attempt-dir',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args()
    repo=args.repo.resolve()
    loc=lambda p:q.resolved(repo,p)
    try:
        audit_attempt(repo,loc(args.results_dir),loc(args.attempt_dir),loc(args.out_dir))
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        raise SystemExit('V4F4C_AUDIT_FAIL: '+str(exc)) from exc

if __name__=='__main__':main()

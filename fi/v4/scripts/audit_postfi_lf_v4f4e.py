#!/usr/bin/env python3
"""Independent read-only V4f4E deterministic LF pause attempt audit.

Never calls Vivado/XSim. A Tcl pause+Job termination is INVALID
infrastructure evidence, NOT a committed FI outcome or a G6 release.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
for d in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(d) not in sys.path:sys.path.insert(0,str(d))
from audit_v4 import require,read_json
from pilot400_common import FIELDS,read_csv,sha256
import qualify_runner_v4d as v4da
import qualify_full400_v4f as v4f
import qualify_xsim_failures_v4f4c as q
import audit_v4f4c as qa
import qualify_postfi_lf_v4f4e as p

AUDIT_SCHEMA='CNN_PEP_V4F4E_POSTFI_LF_READ_ONLY_AUDIT_V1'


def audit_attempt(repo:Path,results:Path,attempt:Path,out:Path)->dict:
    require(not attempt.is_symlink() and not out.is_symlink(),
            'Symlinked attempt or audit destination')
    repo=repo.resolve();results=results.resolve();attempt=attempt.resolve();out=out.resolve()
    require(attempt==results/p.ATTEMPT_NAME and attempt.is_dir() and
            not attempt.is_symlink(),'V4f4E attempt must be the single reserved slot 8')
    require(out.parent==results and out.name.startswith('v4f4e_') and
            out!=attempt and not out.exists() and not out.is_symlink(),
            'V4f4E audit must use a FRESH sibling v4f4e_* directory')
    v4f.safety_output(repo,out,repo/'data',results)
    require(not any(x.is_symlink() for x in attempt.rglob('*')),
            'Symlink inside attempt evidence')
    meta=read_json(attempt/'attempt.json')
    require(meta.get('qualification_mode')==p.SCHEMA and
            meta.get('scenario_id')==p.SELECTED_ID and
            meta.get('trial')=='terminate-after-fi' and
            meta.get('status')==q.BLOCKED and
            meta.get('xsim_invocations_committed')==0 and
            meta.get('validation') is None and
            meta.get('run_hashes') is None and
            meta.get('reserved_phase4c_slot')==8,
            'V4f4E must be uncommitted, bounded failure evidence')
    q.verify_attempt(attempt,meta,allow_blocked=True)
    rows=q.select_canary(read_csv(repo/'fi/scenarios/pilot400.csv',FIELDS))
    require(read_csv(attempt/'canary_selection.csv',FIELDS)==rows,
            'Frozen scenario selection mismatch')
    sid=p.SELECTED_ID
    log=attempt/'runs'/(sid+'.log')
    cmd=attempt/'runs'/(sid+'.cmd')
    qa.check_command(cmd,meta['scenario'],repo)
    require(sha256(cmd)==meta['cmd_sha256'],
            'Executed XSim command changed')
    require((attempt/q.INFLIGHT).is_file() and
            read_json(attempt/q.INFLIGHT).get('cmd_sha256')==sha256(cmd),
            'Expected uncommitted inflight evidence missing/corrupt')
    pause=attempt/p.PAUSE_FILE
    tcl=attempt/'build_001'/'runall.tcl'
    require(tcl.is_file() and not tcl.is_symlink() and
            tcl.read_text(encoding='ascii')==p.generate_tcl(pause) and
            sha256(tcl)==meta['build']['tcl_sha256'],
            'Tcl condition/stop/handshake changed')
    process=meta.get('process')
    require(isinstance(process,dict) and process.get('scenario_id')==sid and
            process.get('trial')=='terminate-after-fi' and
            process.get('job_object_kill_on_close') is True and
            process.get('owned_active_processes_after')==0 and
            process.get('child_started') is True and
            process.get('child_receipt_sha256')==sha256(attempt/'child_lifecycle.json') and
            process.get('job_launch_sha256')==sha256(attempt/'job_launch.json'),
            'Windows private Job Object / child lifecycle not proven')
    require(read_json(attempt/q.RECEIPT)==process and
            sha256(attempt/q.RECEIPT)==meta['process_receipt_sha256'],
            'Process receipt mismatch')
    observed=list(q.marker_counts(log))
    require(process.get('marker_evidence')==observed and
            process.get('classification')==q.classify_observed_process(meta['trial'],process),
            'Raw FI marker evidence differs from process classification')
    require(process.get('tcl_pause_tcl_sha256')==sha256(tcl) and
            process.get('tcl_pause_receipt_sha256')==
              (sha256(pause) if pause.is_file() else None),
            'Tcl pause receipt changed')
    expected=p.classify_probe(process,pause,log)
    require(meta.get('failure_reason')==expected,
            'Failure proof label forged or inconsistent with raw evidence')
    if expected==p.PROBE_MARKER:
        require(p.handshake_file_valid(pause) and observed==[1,0] and
                process['termination_requested'] is True and
                process['external_timeout'] is False and
                process['log_marker_confirmed_before_termination'] is True and
                process['postfi_kill_cause']=='ACK_AND_MARKER' and
                process['owned_active_processes_before_termination']>=2,
                'Post-FI pause must be real, no result, intentionally killed')
    require(meta.get('full400_authorized') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('scientific_protocol_approved') is False,
            'Unauthorized Full400 release')
    inventory={x.relative_to(attempt).as_posix():sha256(x) for x in sorted(attempt.rglob('*'))
               if x.is_file() and not x.is_symlink()}
    receipt={'schema':AUDIT_SCHEMA,'created_utc':datetime.now(timezone.utc).isoformat(),
        'qualification':expected,'scenario_id':sid,'raw_markers':observed,
        'input_pins_verified':len(meta['input_pins']),
        'attempt_json_sha256':sha256(attempt/'attempt.json'),
        'artifact_sha256':inventory,'new_xsim_runs_during_audit':0,
        'committed_fi_outcomes':0,'scientific_protocol_approved':False,
        'full400_runner_qualified':False,'full400_authorized':False,
        'full400_gate':'BLOCKED_G0_G7_REQUIRED'}
    out.mkdir(parents=True,exist_ok=False)
    v4da.atomic_write_json(out/'v4f4e_postfi_audit.json',receipt)
    print('V4F4E_AUDIT_'+('POSTFI_INTERRUPT_PASS' if expected==p.PROBE_MARKER
          else 'NOT_REPRODUCED')+': '+sid+'; 0 new XSim; FULL400 BLOCKED',flush=True)
    print('V4F4E_AUDIT_OUTPUT: '+str(out),flush=True)
    return receipt


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--attempt-dir',type=Path,default=Path('fi/results/'+p.ATTEMPT_NAME))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f4e_postfi_audit_01'))
    args=ap.parse_args()
    repo=args.repo.resolve();results=p.q.resolved(repo,args.results_dir)
    attempt=p.q.resolved(repo,args.attempt_dir);out=p.q.resolved(repo,args.out_dir)
    try:audit_attempt(repo,results,attempt,out)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,AssertionError) as exc:
        raise SystemExit('V4F4E_AUDIT_FAIL: '+str(exc)) from exc

if __name__=='__main__':main()

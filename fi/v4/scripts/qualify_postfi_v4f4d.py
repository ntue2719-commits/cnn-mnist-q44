#!/usr/bin/env python3
"""V4f4D deterministic post-FI XSim interruption (G6 qualification ONLY).

V4f4C's polling raced with FI_RESULT_V2. Here a Vivado XSim Tcl condition
pauses simulator time at frozen TB fi_injection_ok==1, advances 1 ns to let
FI_APPLIED print, writes a *simulator-owned* pause receipt, then deliberately
blocks Tcl. The parent terminates the private Windows Job Object after that
receipt, with no FI outcome committed. This is a G6 failure probe, not FI data.

No P0 RTL, frozen TB, manifest, or old evidence is modified. A new attempt
uses the historical `v4f4c_*` prefix intentionally, so the unchanged V4f4C
aggregate eight-slot budget also counts this reservation. FULL400 BLOCKED.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

HERE=Path(__file__).resolve().parent
for d in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(d) not in sys.path:sys.path.insert(0,str(d))

from audit_v4 import require, read_json
from pilot400_common import FIELDS,sha256,read_csv,write_csv
from pilot400_direct_xsim import make_cmd_run
import qualify_runner_v4d as v4da
import qualify_xsim_failures_v4f4c as q
import qualify_full400_v4f as v4f

SCHEMA='CNN_PEP_V4F4D_POSTFI_TCL_PAUSE_PROBE_V1'
SELECTED_ID='FI_P400_0167'  # IMG0003, BUF_0031, bit=0, offset=1049
PAUSE_FILE='v4f4d_tcl_pause_receipt.txt'
PAUSE_TEXT='V4F4D_POSTFI_PAUSE_READY\nFI_INJECTION_OK=1\n'
ATTEMPT_NAME='v4f4c_postfi_det_01'  # count in V4f4C eight-slot hard budget
DOC='fi/v4/docs/V4F4D_DETERMINISTIC_POSTFI_README.md'
PLAN_STATUS='OFFLINE_CANDIDATE_NO_XSIM_G6_NOT_QUALIFIED_FULL400_BLOCKED'
PROBE_MARKER='INTERRUPTED_AFTER_FI_NO_RESULT'
GUARD='V4F4D_FI_GUARD'
TIMEOUT_MIN=30
TIMEOUT_MAX=900


def now()->str:
    return datetime.now(timezone.utc).isoformat()


def source_pins(repo:Path,old_pins:dict[str,str])->dict[str,str]:
    pins=dict(old_pins)
    for path in (HERE/'qualify_postfi_v4f4d.py',HERE/'audit_postfi_v4f4d.py',repo/DOC):
        require(path.is_file() and not path.is_symlink(),'V4f4D source missing: '+str(path))
        pins[str(path.resolve())]=sha256(path)
    return dict(sorted(pins.items()))


def historical_attempts(results:Path)->list[Path]:
    """Fail closed on any unknown source attempt rather than 'finding' slots."""
    expected={
      'v4f4c_healthy_01':('FI_P400_0007','healthy',q.SUCCESS),
      'v4f4c_terminate_before_01':('FI_P400_0014','terminate-before-fi',q.BLOCKED),
      'v4f4c_terminate_after_01':('FI_P400_0167','terminate-after-fi',q.BLOCKED),
      'v4f4c_crash_before_01':('FI_P400_0174','crash-before-commit',q.RUNNING),
      'v4f4c_timeout_01':('FI_P400_0227','external-timeout',q.BLOCKED),
      'v4f4c_recovery_01':('FI_P400_0227','healthy',q.SUCCESS),
    }
    present=[]
    for directory in sorted(results.glob('v4f4c_*')):
        if not directory.is_dir():
            continue
        require(not directory.is_symlink(),'Symlinked V4f4C directory')
        receipt=directory/'attempt.json'
        if not receipt.exists():
            require(not (directory/'runs').exists() and not (directory/'build_001').exists(),
                    'Unregistered XSim directory; stop')
            continue
        require(directory.name in expected or directory.name==ATTEMPT_NAME,
                'Unknown V4f4C attempt; aggregate budget requires explicit audit: '+directory.name)
        item=read_json(receipt)
        require(item.get('schema')==q.SCHEMA and item.get('max_xsim_processes')==1 and
                item.get('xsim_invocations_committed') in (0,1),
                'Invalid V4f4C attempt in global budget')
        if directory.name in expected:
            sid,trial,status=expected[directory.name]
            require((item['scenario_id'],item['trial'],item['status'])==(sid,trial,status),
                    'Historical V4f4C status changed: '+directory.name)
        else:
            require(item.get('qualification_mode')==SCHEMA and
                    item.get('scenario_id')==SELECTED_ID and
                    item.get('xsim_invocations_committed')==0,
                    'V4f4D reservation corrupted')
        present.append(directory)
    require({p.name for p in present}.issuperset(expected),
            'Six historical V4f4C attempts are required before Phase 4D')
    require(len(present)<=q.AGGREGATE_ATTEMPT_BUDGET,
            'Combined V4f4C/V4f4D reserved budget exceeded')
    return present


def generate_tcl(pause:Path)->str:
    """Tcl condition triggers on EXISTING frozen TB signal, no stimulus edits.

    get_value is checked AFTER one nanosecond of simulation time, by which the
    frozen $display following fi_injection_ok assignment must have executed.
    No continuation of simulation after the durable pause receipt is possible.
    """
    posix=pause.resolve().as_posix()
    require('"' not in posix and '\n' not in posix and '\r' not in posix and
            '{' not in posix and '}' not in posix,'Unsupported Tcl receipt path')
    tmp=posix+'.tmp'
    return (f'''# V4F4D deterministic post-FI stop; qualification ONLY, no Full400.
set ::v4f4d_fired 0
add_condition -name {GUARD} {{/tb_cnn_mnist_fi_pilot400/fi_injection_ok == 1}} {{
    set ::v4f4d_fired 1
    puts "V4F4D_CONDITION_FI_DETECTED"
    stop
}}
run all
if {{$::v4f4d_fired != 1}} {{
    puts "V4F4D_PAUSE_NOT_REPRODUCED"
    quit
}}
remove_conditions {GUARD}
run 1ns
# fi_injection_ok is a 32-bit Verilog integer. Binary readback can be zero-
# padded to 32 bits (000...001), so compare unsigned decimal readback instead.
set ::v4f4d_value [get_value -radix unsigned /tb_cnn_mnist_fi_pilot400/fi_injection_ok]
if {{$::v4f4d_value ne "1"}} {{
    puts "V4F4D_INJECTION_STATE_NOT_ONE"
    quit
}}
set ::v4f4d_f [open {{{tmp}}} w]
puts $::v4f4d_f "V4F4D_POSTFI_PAUSE_READY"
puts $::v4f4d_f "FI_INJECTION_OK=1"
flush $::v4f4d_f
close $::v4f4d_f
file rename {{{tmp}}} {{{posix}}}
puts "V4F4D_TCL_PAUSE_READY"
flush stdout
# Tcl event loop: XSim design time stays paused until private Job kill.
vwait ::v4f4d_external_release_never
''')


def handshake_file_valid(path:Path)->bool:
    return path.is_file() and not path.is_symlink() and path.read_bytes()==PAUSE_TEXT.encode('ascii')


def classify_probe(process:dict[str,Any],pause_path:Path,log_path:Path)->str:
    applied,result=q.marker_counts(log_path)
    return (PROBE_MARKER if
            process.get('termination_requested') is True and
            process.get('external_timeout') is False and
            process.get('owned_active_processes_after')==0 and
            process.get('child_started') is True and
            process.get('tcl_handshake_seen') is True and
            process.get('tcl_handshake_valid') is True and
            process.get('log_marker_confirmed_before_termination') is True and
            process.get('postfi_kill_cause')=='ACK_AND_MARKER' and
            isinstance(process.get('owned_active_processes_before_termination'),int) and
            process['owned_active_processes_before_termination']>=2 and
            handshake_file_valid(pause_path) and applied==1 and result==0
            else 'POSTFI_TCL_PAUSE_NOT_REPRODUCED')


def postfi_termination_decision(ack_exists:bool,ack_valid:bool,
                                markers:tuple[int,int],age_seconds:float)->str:
    """Pure bounded state machine. The native marker alone NEVER triggers kill."""
    if not ack_exists:return 'WAIT'
    if not ack_valid:
        return 'INVALID_ACK' if age_seconds>=2 else 'WAIT'
    if markers==(1,0):return 'ACK_AND_MARKER'
    return 'NO_MARKER' if age_seconds>=5 else 'WAIT'


def contained_postfi_run(cmd:Path,simdir:Path,wrapper:Path,gate:Path,
                         child_path:Path,launch:Path,pause:Path,log:Path,timeout:int)->dict[str,Any]:
    """Kill only after Tcl pause ACK (never from stdout/log polling)."""
    require(os.name=='nt','V4f4D real qualification requires Windows')
    start=time.monotonic();job=None;pid=None;active=None;rc=None
    termination=False;timed_out=False;ack_seen=False;ack_valid=False
    ack_time=None;log_marker_confirmed=False;kill_cause='NONE'
    active_before=None
    with wrapper.open('wb') as output:
        launcher=[sys.executable,'-u',str(Path(q.__file__).resolve()),
                  '--child-launcher','--cmdfile',str(cmd),'--simdir',str(simdir),
                  '--gate',str(gate),'--child-receipt',str(child_path)]
        proc=subprocess.Popen(launcher,cwd=simdir,stdout=output,stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        pid=proc.pid
        try:
            job=q.WindowsKillJob(proc)
            v4da.atomic_write_json(launch,{'launcher_pid':pid,
                'private_job_assigned':True,'kill_on_close':True,'assigned_utc':now()})
            gate.write_text('ASSIGNED_TO_PRIVATE_JOB\n',encoding='ascii')
            while True:
                if proc.poll() is not None:
                    rc=proc.returncode
                    break
                if pause.exists():
                    ack_seen=True
                    ack_valid=handshake_file_valid(pause)
                    if ack_time is None:ack_time=time.monotonic()
                    # The file is created by atomic Tcl rename. Still allow
                    # 2 seconds for a transient read and 5 seconds for log
                    # flush while simulator time is paused.
                    observed=q.marker_counts(log)
                    kill_cause=postfi_termination_decision(True,ack_valid,observed,
                                           time.monotonic()-ack_time)
                    if kill_cause!='WAIT':
                        if proc.poll() is not None:
                            rc=proc.returncode
                            kill_cause='LAUNCHER_ALREADY_EXITED'
                            break
                        active_before=job.active()
                        if active_before<2:
                            kill_cause='NO_ACTIVE_XSIM_CHILD_AT_KILL'
                        log_marker_confirmed=(kill_cause=='ACK_AND_MARKER')
                        termination=True
                        job.terminate()
                        try:rc=proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:rc=None
                        break
                if proc.poll() is not None:
                    rc=proc.returncode
                    break
                if time.monotonic()-start>=timeout:
                    timed_out=True;termination=True
                    job.terminate()
                    try:rc=proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:rc=None
                    break
                time.sleep(.025)
            until=time.monotonic()+10
            while time.monotonic()<until:
                active=job.active()
                if active==0:break
                time.sleep(.05)
            require(active==0,'Private Windows job still has owned processes')
        finally:
            if job is not None:
                if proc.poll() is None:
                    job.terminate()
                    try:proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:pass
                job.close()
            elif proc.poll() is None:
                # Gate not written: launcher cannot have spawned XSim.
                proc.kill();proc.wait(timeout=10)
    return {'launcher_pid':pid,'return_code':rc,
            'termination_requested':termination,'external_timeout':timed_out,
            'tcl_handshake_seen':ack_seen,'tcl_handshake_valid':ack_valid,
            'log_marker_confirmed_before_termination':log_marker_confirmed,
            'postfi_kill_cause':kill_cause,
            'owned_active_processes_before_termination':active_before,
            'owned_active_processes_after':active,'job_object_kill_on_close':True,
            'launches_budgeted':1,'duration_seconds':round(time.monotonic()-start,3)}


def ensure_output(repo:Path,results:Path,weights:Path,out:Path)->None:
    require(out.name==ATTEMPT_NAME and out.parent==results and
            not out.exists() and not out.is_symlink(),
            'V4f4D requires fresh fi/results/'+ATTEMPT_NAME)
    q.safe_out(repo,results,weights,out)


def preflight(repo:Path,results:Path,weights:Path,v4e:Path,*,replay:bool):
    rows,pins=q.input_preflight(repo,results,weights,v4e,replay=replay)
    return rows,source_pins(repo,pins)


def execute(args:argparse.Namespace,repo:Path,results:Path,weights:Path,
            v4e:Path,out:Path)->dict:
    require(args.execute and os.name=='nt','V4f4D execution requires Windows and --execute')
    require(TIMEOUT_MIN<=args.timeout_seconds<=TIMEOUT_MAX,
            'V4f4D timeout must be between 30 and 900 seconds')
    require(args.project and args.vivado,'Vivado project and tool required')
    project=q.resolved(repo,args.project)
    require(project.is_file() and project.suffix.lower()=='.xpr','Missing authoritative Vivado .xpr')
    args.trial='terminate-after-fi'
    args.scenario_id=SELECTED_ID
    args.v3a_dir=results/q.V3A_NAME
    with q.global_campaign_lock(results):
        q.enforce_aggregate_budget(results)
        past=historical_attempts(results)
        require(len(past)==6 and len(past)<q.AGGREGATE_ATTEMPT_BUDGET,
                'V4f4D is only allowed as slot 7/8 after six historical attempts')
        rows,pins=preflight(repo,results,weights,v4e,replay=True)
        row=next(x for x in rows if x['scenario_id']==SELECTED_ID)
        ensure_output(repo,results,weights,out)
        out.mkdir(parents=True,exist_ok=False)
        meta=q._meta(rows,row,pins,out,args,None)
        meta['qualification_mode']=SCHEMA
        meta['expected_failure_reason']=PROBE_MARKER
        meta['reserved_phase4c_slot']=7
        v4da.atomic_write_json(out/'attempt.json',meta)
        try:
            build=out/'build_001';build.mkdir()
            snap=q.build_job_contained_snapshot(repo,project,args.vivado,args.fileset,
                                              build,args.timeout_seconds,args.v3a_dir)
            require(snap['elaboration_mode']=='FULL_BUILD' and
                    snap['compile_prj_sha256']==meta['source_v3a_prj_sha256'],
                    'Pinned Vivado build source mismatch')
            pause=out/PAUSE_FILE
            tcl=build/'runall.tcl'
            tcl.write_text(generate_tcl(pause),encoding='ascii')
            meta['build']={'prj_sha256':sha256(build/'source_vlog.prj'),
                 'compile_sha256':sha256(build/'compile_snapshot.log'),
                 'elab_sha256':sha256(build/'elaborate_snapshot.log'),
                 'tcl_sha256':sha256(tcl), 'source_hashes':snap['source_hashes'],
                 'toolchain_hashes':snap['toolchain_hashes'],
                 'build_process_hashes':snap['build_process_hashes'],
                 'build_strategy':snap['build_strategy']}
            q.check_pins(pins)
            v4da.atomic_write_json(out/'attempt.json',meta)
            runs=out/'runs';runs.mkdir()
            sid=SELECTED_ID
            cmd=runs/(sid+'.cmd');log=runs/(sid+'.log')
            console=runs/(sid+'.console.log');wrapper=runs/(sid+'.wrapper.log')
            cmd.write_text(make_cmd_run(snap['simdir'],snap['vivado_root'],row,
                   repo/'fi/pilot400/samples'/row['sample_id'],tcl,log,console,
                   profile_only=False),encoding='ascii')
            inflight={'schema':'V4F4C_INFLIGHT_V1','scenario_id':sid,
                      'trial':args.trial,'cmd_sha256':sha256(cmd),
                      'started_utc':now(),'max_xsim_processes':1}
            v4da.atomic_write_json(out/q.INFLIGHT,inflight)
            meta['cmd_sha256']=sha256(cmd)
            v4da.atomic_write_json(out/'attempt.json',meta)
            print('V4F4D_XSIM_BOUNDED: 1/1 '+sid+' TCL post-FI stop; '
                  'attempt slot 7/8; FULL400 BLOCKED',flush=True)
            gate=out/'.start_gate';child=out/'child_lifecycle.json';launch=out/'job_launch.json'
            process=contained_postfi_run(cmd,snap['simdir'],wrapper,gate,child,launch,
                                         pause,log,args.timeout_seconds)
            process['scenario_id']=sid;process['trial']=args.trial
            process['child_receipt_sha256']=sha256(child) if child.is_file() else None
            process['child_started']=(isinstance(read_json(child).get('cmd_pid'),int)
                                      if child.is_file() else False)
            process['job_launch_sha256']=sha256(launch) if launch.is_file() else None
            process['marker_evidence']=list(q.marker_counts(log))
            process['classification']=q.classify_observed_process(args.trial,process)
            process['tcl_pause_receipt_sha256']=sha256(pause) if pause.is_file() else None
            process['tcl_pause_tcl_sha256']=sha256(tcl)
            meta['process']=process
            v4da.atomic_write_json(out/q.RECEIPT,process)
            meta['process_receipt_sha256']=sha256(out/q.RECEIPT)
            meta['status']=q.BLOCKED
            meta['failure_reason']=classify_probe(process,pause,log)
            q.check_pins(pins)
            require(all(Path(p).is_file() and sha256(Path(p))==dig
                    for p,dig in snap['source_hashes'].items()),
                    'Compiled RTL source drift during probe')
            v4da.atomic_write_json(out/'attempt.json',meta)
            q.verify_attempt(out,meta,allow_blocked=True)
            print('V4F4D_POSTFI_PROBE: '+meta['failure_reason']+
                  '; 0 committed FI; Full400 BLOCKED',flush=True)
            return meta
        except BaseException as exc:
            meta['status']=q.BLOCKED
            meta['failure_reason']=type(exc).__name__+': '+str(exc)
            v4da.atomic_write_json(out/'attempt.json',meta)
            raise


def plan(args:argparse.Namespace,repo:Path,results:Path,weights:Path,v4e:Path,out:Path):
    require(not args.execute,'--mode plan never executes')
    rows,pins=preflight(repo,results,weights,v4e,replay=False)
    require(out.parent==results and out.name.startswith('v4f4d_plan_') and
            not out.exists(),'Plan must have fresh fi/results/v4f4d_plan_* output')
    v4f.safety_output(repo,out,weights,results)
    out.mkdir(parents=True,exist_ok=False)
    selected=next(r for r in rows if r['scenario_id']==SELECTED_ID)
    template=generate_tcl(out/'EXAMPLE_NOT_EXECUTED.txt')
    (out/'candidate_postfi.tcl').write_text(template,encoding='ascii')
    receipt={'schema':SCHEMA,'status':PLAN_STATUS,'selected_scenario':selected,
             'old_v4f4c_attempt_slots_observed':0,'planned_future_slot':7,
             'tcl_candidate_sha256':sha256(out/'candidate_postfi.tcl'),
             'source_pins':pins,'new_xsim_runs':0,
             'scientific_protocol_approved':False,'full400_runner_qualified':False,
             'full400_authorized':False,'full400_executed':False}
    v4da.atomic_write_json(out/'v4f4d_plan.json',receipt)
    print('V4F4D_PLAN_PASS: deterministic Tcl FI condition prepared; '
          '0 XSim; six historical attempts not independently replayed by plan; '
          'FULL400 BLOCKED',flush=True)
    print('V4F4D_OUTPUT: '+str(out),flush=True)
    return receipt


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--v4e-dir',type=Path,default=Path('fi/results/v4e_release_review_01'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f4d_plan_01'))
    ap.add_argument('--mode',choices=('plan','execute','full400'),default='plan')
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--project',type=Path)
    ap.add_argument('--vivado',default='')
    ap.add_argument('--fileset',default='sim_1')
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--scenario-id',default=SELECTED_ID)
    args=ap.parse_args()
    try:
        require(args.mode!='full400','Full400 BLOCKED; no G0-G7 authorization')
        require(args.scenario_id==SELECTED_ID,
                'Only exact frozen scenario '+SELECTED_ID+' allowed')
        repo=args.repo.resolve();results=q.resolved(repo,args.results_dir)
        weights=q.resolved(repo,args.weights_dir);v4e=q.resolved(repo,args.v4e_dir)
        out=q.resolved(repo,args.out_dir)
        if args.mode=='plan':plan(args,repo,results,weights,v4e,out)
        else:execute(args,repo,results,weights,v4e,out)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,AssertionError) as exc:
        raise SystemExit('V4F4D_FAIL: '+str(exc)) from exc

if __name__=='__main__':main()

#!/usr/bin/env python3
"""V4f4C G6: at-most-one-XSim-per-attempt, fail-closed failure qualification.

NO Full400 execution or release authorization. A Windows child launcher is
assigned to a dedicated kill-on-close Job Object before it can spawn cmd/XSim.
Terminated, timed-out and orphaned attempts are INVALID infrastructure evidence,
not a design NONCOMPLETION outcome. Any retry MUST use a NEW attempt directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
for directory in (HERE, HERE.parents[1]/'scripts', HERE.parents[1]/'v3b/scripts'):
    if str(directory) not in sys.path:
        sys.path.insert(0,str(directory))

from audit_v4 import V3A_NAME, require, read_json
from pilot400_common import FIELDS, load_plan, verify_manifest, read_csv, write_csv, sha256
from pilot400_direct_xsim import (make_cmd_run,project_source_hashes,
                                     classify_elaboration,SNAPSHOT,TOP)
from pinned_direct_snapshot_v4db import cmd_stage, _strict_log
import qualify_full400_v4f as v4f
import run_full400_batches_v4f as v4f2
import qualify_runner_v4d as v4da

SCHEMA='CNN_PEP_V4F4C_SINGLE_ATTEMPT_FAILURE_PROBE_V1'
PLAN_SCHEMA='CNN_PEP_V4F4C_BOUNDED_CANARY_PLAN_V1'
CANARY_IDS=('FI_P400_0007','FI_P400_0014','FI_P400_0167','FI_P400_0174',
            'FI_P400_0227','FI_P400_0234','FI_P400_0387','FI_P400_0394')
CANARY_SAMPLES=('IMG0003','IMG0006')
CANARY_TARGETS=('CTL_0001','BUF_0031','MEM_0002','DAT_0010')
TRIALS=('healthy','terminate-before-fi','terminate-after-fi',
        'external-timeout','crash-before-commit')
BLOCKED='ATTEMPT_BLOCKED_INVALID_INFRASTRUCTURE_FULL400_BLOCKED'
RUNNING='ATTEMPT_INFLIGHT_NOT_CLASSIFIABLE_FULL400_BLOCKED'
SUCCESS='ONE_BOUNDED_XSIM_STRICTLY_VERIFIED_FULL400_BLOCKED'
PLAN='EIGHT_FROZEN_CANARY_TUPLES_ONLY_ZERO_XSIM_FULL400_BLOCKED'
LIMIT_SECONDS=900
AGGREGATE_ATTEMPT_BUDGET=8  # reserved XSim slots across ALL Phase4C attempts
PROTOCOL_CORE_HASH='30a5ea2e66bda0d38e38d0a45635da5abe5ddbcf846672ef9d2e20cf546cf733'
RECEIPT='process_receipt.json'
INFLIGHT='inflight.json'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolved(repo:Path, value:Path) -> Path:
    return value.resolve() if value.is_absolute() else (repo/value).resolve()


def select_canary(manifest:list[dict[str,str]]) -> list[dict[str,str]]:
    by_id={row['scenario_id']:row for row in manifest}
    require(len(by_id)==400 and all(sid in by_id for sid in CANARY_IDS),
            'V4f4C frozen canary IDs absent/duplicated')
    chosen=[by_id[sid] for sid in CANARY_IDS]
    require([r['sample_id'] for r in chosen]==['IMG0003','IMG0006']*4 and
            [r['target_id'] for r in chosen] == [t for t in CANARY_TARGETS for _ in (0,1)] and
            [r['injection_offset'] for r in chosen]==
            ['125','150','1049','1050','878','879','1340','1340'] and
            [r['bit_index'] for r in chosen]==['2','2','0','0','0','0','0','1'],
            'V4f4C canary bit/offset/sample identity differs from frozen plan')
    return chosen


def input_preflight(repo:Path,results:Path,weights:Path,v4e:Path,
                    *,replay:bool) -> tuple[list[dict[str,str]],dict[str,str]]:
    v4f.check_frozen(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    v4f.select_400(manifest,plan)
    rows=select_canary(manifest)
    pins=v4f2._source_pins(repo,results,weights,v4e)
    for p in (HERE/'qualify_xsim_failures_v4f4c.py',
              HERE/'audit_v4f4c.py',
              repo/'fi/v4/docs/V4F4C_G6_README.md',
              repo/'fi/v4/protocol/v4f4c_failure_plan_CANDIDATE.json'):
        require(p.is_file(), 'V4f4C required source/protocol missing: '+str(p))
        pins[str(p.resolve())]=sha256(p)
    candidate=read_json(repo/'fi/v4/protocol/v4f4c_failure_plan_CANDIDATE.json')
    require(candidate.get('status')=='CANDIDATE_BOUNDED_NOT_EXECUTED_NOT_RELEASED' and
            candidate.get('frozen_scenario_ids')==list(CANARY_IDS) and
            candidate.get('per_attempt_max_xsim_invocations')==1 and
            candidate.get('total_reserved_attempt_budget')==8 and
            candidate.get('full400_authorized') is False and
            candidate.get('scientific_protocol_approved') is False,
            'V4f4C proposed canary plan/strict budget has been modified')
    proto=repo/'fi/results/v4f4_protocol_prepare_01/protocol_core.json'
    if replay:
        require(proto.is_file() and sha256(proto)==PROTOCOL_CORE_HASH,
                'V4f4C Windows execution requires SHA-pinned PREPARED G7 core')
    if proto.is_file():
        require(sha256(proto)==PROTOCOL_CORE_HASH,
                'G7 protocol PREPARED hash differs; do NOT infer approval')
        pins[str(proto.resolve())]=sha256(proto)
    if replay:
        meta,matrix=v4f.verify_v4e_saved(repo,results,v4e)
        v4f.replay_v4e_raw(repo,results,weights,v4e,meta,matrix,manifest,plan)
    return rows,dict(sorted(pins.items()))


def safe_out(repo:Path,results:Path,weights:Path,out:Path) -> None:
    require(results==repo/'fi/results' and results.is_dir() and not results.is_symlink()
            and out.parent==results and out.name.startswith('v4f4c_')
            and not out.exists() and not out.is_symlink(),
            'V4f4C requires a FRESH fi/results/v4f4c_* sibling directory')
    v4f.safety_output(repo,out,weights,results)


@contextmanager
def global_campaign_lock(results:Path):
    """Cross-process exclusive lock across ALL Phase4C Windows canary attempts.

    The .lock file is intentionally persistent: removing a lockfile while a
    waiter holds the old inode can silently permit concurrent writers.
    """
    lock=results/'.v4f4c_global.lock'
    require(not lock.is_symlink(),'Refusing symlinked global G6 budget lock')
    flags=os.O_RDWR|os.O_CREAT
    if hasattr(os,'O_NOFOLLOW'):flags|=os.O_NOFOLLOW
    with os.fdopen(os.open(lock,flags,0o600),'r+b') as handle:
        if handle.seek(0,os.SEEK_END)==0:
            handle.write(b'\0');handle.flush();os.fsync(handle.fileno())
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:
                raise ValueError('Another V4f4C attempt is executing') from exc
            try:yield
            finally:
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError('Another V4f4C attempt is executing') from exc
            try:yield
            finally:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def enforce_aggregate_budget(results:Path) -> None:
    """Count RESERVED one-XSim attempts, not only successful invocations."""
    attempted=[]
    for directory in results.glob('v4f4c_*'):
        if not directory.is_dir() or directory.is_symlink():continue
        p=directory/'attempt.json'
        if not p.is_file():
            require(not (directory/'runs').exists() and
                    not (directory/'build_001').exists(),
                    'Unregistered/orphan G6 attempt directory; stop before new trial')
            continue
        obj=read_json(p)
        require(obj.get('schema')==SCHEMA and
                obj.get('max_xsim_processes')==1 and
                obj.get('xsim_invocations_committed') in (0,1),
                'G6 attempt inventory contains an unknown/corrupt attempt')
        attempted.append(directory)
    require(len(attempted)<AGGREGATE_ATTEMPT_BUDGET,
            'Phase4C aggregate budget exhausted: 8 reserved attempts; FULL400 BLOCKED')


def no_release(meta:dict[str,Any]) -> None:
    require(meta.get('full400_authorized') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('full400_executed') is False and
            meta.get('scientific_protocol_approved') is False and
            meta.get('max_xsim_processes')==1 and
            meta.get('full400_gate')=='BLOCKED_G0_G7_REQUIRED',
            'V4f4C unauthorized release or unbounded execution')


def check_pins(pins:dict[str,str]) -> None:
    require(isinstance(pins,dict) and bool(pins), 'Missing source SHA256 pins')
    for filename,digest in pins.items():
        path=Path(filename)
        require(path.is_file() and not path.is_symlink() and
                sha256(path)==digest, 'Source/provenance drift: '+filename)


def marker_counts(log:Path) -> tuple[int,int]:
    if not log.is_file():
        return 0,0
    txt=log.read_text(encoding='utf-8',errors='replace')
    return len(re.findall(r'^FI_APPLIED(?:\s|:)',txt,re.M)),len(re.findall(r'^FI_RESULT_V2(?:\s|:|,)',txt,re.M))


def classify_failure(trial:str, exit_code:int|None, timed_out:bool,
                     applied:int,result:int, termination_requested:bool) -> str:
    """Never convert an external process failure into a design outcome."""
    require(trial in TRIALS,'Unknown V4f4C trial')
    if trial=='healthy' or trial=='crash-before-commit':
        return 'RAW_RESULT_ELIGIBLE' if exit_code==0 and not timed_out and not termination_requested else 'INFRASTRUCTURE_FAILED'
    if trial=='external-timeout':
        return 'INFRASTRUCTURE_TIMEOUT' if timed_out and termination_requested else 'TIMEOUT_PROBE_NOT_REPRODUCED'
    if trial=='terminate-before-fi':
        return ('INTERRUPTED_PRE_FI_NO_OUTCOME' if termination_requested and applied==0 and result==0
                else 'INTERRUPT_PROBE_NOT_REPRODUCED')
    return ('INTERRUPTED_AFTER_FI_NO_RESULT' if termination_requested and applied==1 and result==0
            else 'INTERRUPT_PROBE_NOT_REPRODUCED')


def classify_observed_process(trial:str,process:dict[str,Any]) -> str:
    applied,result=process['marker_evidence']
    category=classify_failure(trial,process['return_code'],
                process['external_timeout'],applied,result,
                process['termination_requested'])
    if trial in ('terminate-before-fi','external-timeout') and not process.get('child_started'):
        return 'PROBE_NOT_REPRODUCED_CHILD_NOT_STARTED'
    if trial in ('healthy','crash-before-commit') and not process.get('child_started'):
        return 'INFRASTRUCTURE_FAILED_NO_CHILD_PROOF'
    return category


def _launcher_main(args:argparse.Namespace) -> None:
    """Child is blocked until the PARENT assigns it to its private Windows job."""
    gate=Path(args.gate)
    deadline=time.monotonic()+25.0
    while not gate.exists():
        if time.monotonic()>deadline:
            raise SystemExit(90)
        time.sleep(.02)
    cwd=Path(args.simdir)
    cmd=Path(args.cmdfile)
    from pilot400_direct_xsim import batch_safe
    batch_safe(str(cmd))
    started=now()
    child=subprocess.Popen(['cmd.exe','/d','/c',str(cmd.resolve())],cwd=cwd)
    receipt=Path(args.child_receipt)
    v4da.atomic_write_json(receipt,{'launcher_pid':os.getpid(),
              'cmd_pid':child.pid,'started_utc':started, 'complete':False})
    rc=child.wait()
    v4da.atomic_write_json(receipt,{'launcher_pid':os.getpid(),
              'cmd_pid':child.pid,'started_utc':started, 'complete':True,
              'return_code':rc})
    raise SystemExit(rc)


class WindowsKillJob:
    """Private Windows Job Object; only child processes assigned to THIS job.

    Assignment occurs while a Python launcher waits behind a gate. If job
    creation, isolation, assignment or accounting fails the gate is NOT opened.
    No taskkill /IM, no unrelated XSim PID targeting.
    """
    def __init__(self,proc:subprocess.Popen):
        if os.name!='nt':
            raise ValueError('Windows job containment unavailable outside Windows')
        import ctypes
        from ctypes import wintypes
        self.ctypes=ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel=kernel
        HANDLE=wintypes.HANDLE
        DWORD=wintypes.DWORD
        SIZE=ctypes.c_size_t
        class Basic(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),
                      ('PerJobUserTimeLimit',ctypes.c_longlong),('LimitFlags',DWORD),
                      ('MinimumWorkingSetSize',SIZE),('MaximumWorkingSetSize',SIZE),
                      ('ActiveProcessLimit',DWORD),('Affinity',SIZE),
                      ('PriorityClass',DWORD),('SchedulingClass',DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in
                      ('ReadOperationCount','WriteOperationCount','OtherOperationCount',
                       'ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_=[('BasicLimitInformation',Basic),('IoInfo',IoCounters),
                      ('ProcessMemoryLimit',SIZE),('JobMemoryLimit',SIZE),
                      ('PeakProcessMemoryUsed',SIZE),('PeakJobMemoryUsed',SIZE)]
        class Accounting(ctypes.Structure):
            _fields_=[('TotalUserTime',ctypes.c_longlong),
                      ('TotalKernelTime',ctypes.c_longlong),
                      ('ThisPeriodTotalUserTime',ctypes.c_longlong),
                      ('ThisPeriodTotalKernelTime',ctypes.c_longlong),
                      ('TotalPageFaultCount',DWORD),('TotalProcesses',DWORD),
                      ('ActiveProcesses',DWORD),('TotalTerminatedProcesses',DWORD)]
        self.Accounting=Accounting
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p]
        kernel.CreateJobObjectW.restype=HANDLE
        kernel.SetInformationJobObject.argtypes=[HANDLE,ctypes.c_int,ctypes.c_void_p,DWORD]
        kernel.SetInformationJobObject.restype=wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes=[HANDLE,HANDLE]
        kernel.AssignProcessToJobObject.restype=wintypes.BOOL
        kernel.TerminateJobObject.argtypes=[HANDLE,DWORD]
        kernel.TerminateJobObject.restype=wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes=[HANDLE,ctypes.c_int,ctypes.c_void_p,DWORD,ctypes.c_void_p]
        kernel.QueryInformationJobObject.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[HANDLE]
        kernel.CloseHandle.restype=wintypes.BOOL
        self.handle=kernel.CreateJobObjectW(None,None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(),'CreateJobObjectW failed')
        try:
            data=Extended()
            data.BasicLimitInformation.LimitFlags=0x2000  # KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle,9,ctypes.byref(data),ctypes.sizeof(data)):
                raise OSError(ctypes.get_last_error(),'SetInformationJobObject failed')
            if not kernel.AssignProcessToJobObject(self.handle,HANDLE(int(proc._handle))):
                raise OSError(ctypes.get_last_error(),'AssignProcessToJobObject failed; no XSim launched')
        except BaseException:
            self.close()
            raise
    def active(self) -> int:
        info=self.Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle,1,self.ctypes.byref(info),
                                                    self.ctypes.sizeof(info),None):
            raise OSError(self.ctypes.get_last_error(),'QueryInformationJobObject failed')
        return int(info.ActiveProcesses)
    def terminate(self) -> None:
        if not self.kernel.TerminateJobObject(self.handle,87):
            raise OSError(self.ctypes.get_last_error(),'TerminateJobObject failed')
    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle=None


def _job_run(cmdfile:Path, simdir:Path, wrapper:Path, gate:Path, child_receipt:Path,
             launch_receipt:Path, log:Path, trial:str, timeout:int, kill_after:float) -> dict[str,Any]:
    """On Windows, one launcher + one XSim CMD tree, strict finite budget.

    Failure probes check markers in the continuously written native log, and
    report NOT_REPRODUCED if XSim completes before the chosen interruption.
    """
    require(os.name=='nt','V4f4C real XSim requires Windows')
    start=time.monotonic()
    with wrapper.open('wb') as log_handle:
        command=[sys.executable, '-u',str(Path(__file__).resolve()),
                 '--child-launcher','--cmdfile',str(cmdfile),
                 '--simdir',str(simdir),'--gate',str(gate),
                 '--child-receipt',str(child_receipt)]
        proc=subprocess.Popen(command,cwd=simdir,stdout=log_handle,
                stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        job=None
        terminating=False
        timed_out=False
        returncode=None
        job_active=None
        pid=proc.pid
        try:
            job=WindowsKillJob(proc)
            v4da.atomic_write_json(launch_receipt,{'launcher_pid':pid,
                'private_job_assigned':True,'kill_on_close':True,
                'assigned_utc':now()})
            gate.write_text('ASSIGNED_TO_PRIVATE_JOB\n',encoding='ascii')
            while True:
                if proc.poll() is not None:
                    returncode=proc.returncode
                    break
                elapsed=time.monotonic()-start
                applied,result=marker_counts(log)
                if trial=='terminate-before-fi' and elapsed>=kill_after:
                    terminating=True
                elif trial=='terminate-after-fi' and applied>=1 and result==0:
                    terminating=True
                elif trial=='external-timeout' and elapsed>=kill_after:
                    timed_out=True
                    terminating=True
                if elapsed>=timeout:
                    timed_out=True
                    terminating=True
                if terminating:
                    job.terminate()
                    try:
                        returncode=proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        returncode=None
                    break
                time.sleep(.025)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                job_active=job.active()
                if job_active==0:break
                time.sleep(.05)
            require(job_active==0,'Private Windows job still has active owned processes')
        finally:
            if job is not None:
                if proc.poll() is None:
                    job.terminate()
                    try:proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:pass
                job.close()
            else:
                # Launcher never passed the gate: it cannot have spawned XSim.
                if proc.poll() is None:
                    proc.kill();proc.wait(timeout=10)
        applied,result=marker_counts(log)
        return {'launcher_pid':pid,'return_code':returncode,
                'termination_requested':terminating,'external_timeout':timed_out,
                'applied_markers_observed':applied,'result_markers_observed':result,
                'owned_active_processes_after':job_active,
                'job_object_kill_on_close':True, 'launches_budgeted':1,
                'duration_seconds':round(time.monotonic()-start,3)}


def build_job_contained_snapshot(repo:Path,project:Path,vivado:str,fileset:str,
                                 build:Path,timeout:int,v3a:Path) -> dict[str,Any]:
    """Fresh V3a pinned xvlog/xelab with each stage in a private kill-on-close job.

    This deliberately uses the exact V4d-B source-list, argv, compile/log checks,
    but replaces old `subprocess.run(cmd.exe)` with independently job-contained
    process trees for *both* build stages. Never silently fall back.
    """
    require(os.name=='nt','Job-contained Vivado build requires Windows')
    binary=Path(shutil.which(vivado) or vivado).resolve()
    require(binary.is_file() and fileset.isidentifier() and
            project.is_file() and project.suffix.lower()=='.xpr',
            'V4f4C requires valid Vivado executable, project and fileset')
    root=binary.parent.parent
    tools={name:root/'bin'/name for name in ('xvlog.bat','xelab.bat','xsim.bat')}
    require(all(p.is_file() for p in tools.values()),'Vivado XSim tools missing')
    source=v3a/'source_vlog.prj'
    original=read_json(v3a/'validation_manifest.json')['snapshot_build']
    require(sha256(source)==original['compile_prj_sha256'],
            'V3a .prj source list altered')
    hashes,rtl_count=project_source_hashes(source,repo,rtl_source='vivado',project=project)
    require(hashes==original['source_hashes'],
            'Vivado RTL source differs from authenticated V3a source')
    simdir=build/'direct_sim'
    require(not simdir.exists() and not (build/'source_vlog.prj').exists(),
            'Refusing previously built simulation snapshot')
    simdir.mkdir()
    dest=build/'source_vlog.prj'
    shutil.copyfile(source,dest)
    require(sha256(dest)==original['compile_prj_sha256'],
            'Source .prj changed during copy')
    snapshot=simdir/'xsim.dir'/SNAPSHOT
    compile_args=['--relax','-prj',str(dest.resolve().as_posix()),
                  '-log',str((simdir/'xvlog.log').resolve().as_posix())]
    elab_args=['--debug','typical','--relax','--mt','2','-L','xil_defaultlib',
               '-L','unisims_ver','-L','unimacro_ver','-L','secureip',
               '--snapshot',SNAPSHOT,'xil_defaultlib.'+TOP,'xil_defaultlib.glbl',
               '-log',str((simdir/'xelab.log').resolve().as_posix())]
    stage_hashes={}
    for stage,program,argv in (('compile','xvlog.bat',compile_args),
                               ('elaborate','xelab.bat',elab_args)):
        native=build/(stage+'_snapshot.log')
        cmd=build/(stage+'_snapshot.cmd')
        wrapper=build/(stage+'_wrapper.log')
        gate=build/(stage+'_job_gate')
        child=build/(stage+'_child_lifecycle.json')
        launch=build/(stage+'_job_launch.json')
        require(all(not p.exists() for p in (native,cmd,wrapper,gate,child,launch)),
                'Build stage artifacts must be fresh')
        cmd.write_text(cmd_stage(simdir,root,program,argv,native),encoding='utf-8')
        process=_job_run(cmd,simdir,wrapper,gate,child,launch,native,
                         'healthy',timeout,2.0)
        require(process.get('return_code')==0 and
                not process.get('external_timeout') and
                not process.get('termination_requested') and
                process.get('owned_active_processes_after')==0 and
                isinstance(read_json(child).get('cmd_pid'),int),
                'Job-contained Vivado '+stage+' failed or timed out')
        v4da.atomic_write_json(build/(stage+'_job_process.json'),process)
        transcript=_strict_log(native,stage)
        if stage=='compile':
            require('Analyzing Verilog file' in transcript or
                    'analyzing module' in transcript,
                    'xvlog did not analyze RTL in full fresh compile')
        else:
            require(classify_elaboration(transcript,snapshot,None,
                    expected_snapshot=SNAPSHOT)=='FULL_BUILD',
                    'xelab reused a previous snapshot')
        stage_hashes[stage+'_job_process_sha256']=sha256(build/(stage+'_job_process.json'))
        stage_hashes[stage+'_cmd_sha256']=sha256(cmd)
        stage_hashes[stage+'_wrapper_sha256']=sha256(wrapper)
        stage_hashes[stage+'_job_launch_sha256']=sha256(launch)
        stage_hashes[stage+'_child_lifecycle_sha256']=sha256(child)
    require(sha256(dest)==original['compile_prj_sha256'] and
            all(Path(name).is_file() and sha256(Path(name))==digest
                for name,digest in hashes.items()),
            'V4f4C HDL/prj drift after job-contained Vivado build')
    toolchain={str(binary):sha256(binary)}
    toolchain.update({str(path):sha256(path) for path in tools.values()})
    return {'simdir':simdir,'vivado_root':root,'snapshot':snapshot,
            'source_hashes':hashes,'compile_prj_sha256':sha256(dest),
            'compile_log_sha256':sha256(build/'compile_snapshot.log'),
            'elaborate_log_sha256':sha256(build/'elaborate_snapshot.log'),
            'elaboration_mode':'FULL_BUILD','rtl_count':rtl_count,
            'toolchain_hashes':toolchain,'build_process_hashes':stage_hashes,
            'build_strategy':'V4F4C_TWO_PRIVATE_JOB_OBJECTS_PINNED_V3A_PRJ'}


def verify_attempt(out:Path, meta:dict[str,Any], *,
                   allow_blocked:bool=False) -> None:
    """Read-only, fail-closed artifact/record reconciliation for G6 audit."""
    require(meta.get('schema')==SCHEMA and meta.get('status') in
            (SUCCESS,BLOCKED,RUNNING) and meta.get('execution_scope')=='ONE_FROZEN_CANARY_XSIM_MAX' and
            meta.get('scenario_id') in CANARY_IDS and meta.get('trial') in TRIALS,
            'V4f4C attempt schema/scope invalid')
    no_release(meta)
    require(meta.get('xsim_invocations_budgeted')==1 and
            meta.get('xsim_invocations_committed') in (0,1) and
            meta.get('source_authority')=='vivado' and
            meta.get('scenario') is not None,
            'V4f4C process count/source authority invalid')
    check_pins(meta['input_pins'])
    by_id={r['scenario_id']:r for r in read_csv(out/'canary_selection.csv',FIELDS)}
    require(tuple(by_id)==CANARY_IDS and
            by_id[meta['scenario_id']]==meta['scenario'] and
            sha256(out/'canary_selection.csv')==meta['canary_sha256'],
            'V4f4C frozen canary CSV or identity altered')
    child=out/'runs'
    sid=meta['scenario_id']
    run_files={key:child/(sid+suffix) for key,suffix in
                (('cmd','.cmd'),('log','.log'),('console','.console.log'),('wrapper','.wrapper.log'))}
    if meta['status']==SUCCESS:
        require(not (out/INFLIGHT).exists() and meta['trial']=='healthy' and
                meta.get('build') is not None and
                meta['xsim_invocations_committed']==1 and
                meta.get('validation') is not None and
                isinstance(meta.get('process'),dict) and
                meta['process'].get('return_code')==0 and
                meta['process'].get('child_started') is True and
                isinstance(meta['process'].get('launcher_pid'),int) and
                meta['process'].get('job_object_kill_on_close') is True and
                isinstance(meta['process'].get('job_launch_sha256'),str) and
                isinstance(meta['process'].get('child_receipt_sha256'),str) and
                meta['process'].get('owned_active_processes_after')==0,
                'V4f4C success must have exactly one closed, verified run')
        for key,path in run_files.items():
            require(path.is_file() and not path.is_symlink() and
                    sha256(path)==meta['run_hashes'][key],
                    'V4f4C committed XSim artifact missing/edited: '+key)
        require(v4f2.strict_result(run_files['log'],meta['scenario'])==meta['validation'],
                'V4f4C strict replay differs from committed FI result')
    else:
        require(allow_blocked and meta['xsim_invocations_committed']==0 and
                meta.get('validation') is None and meta.get('run_hashes') is None,
                'V4f4C partial/blocked attempt is never complete FI evidence')
    if meta.get('build') is not None:
        require(meta['build'].get('build_strategy')==
                'V4F4C_TWO_PRIVATE_JOB_OBJECTS_PINNED_V3A_PRJ',
                'V4f4C build did not use job-contained fresh V3a source')
        build=out/'build_001'
        for field,relative in (('prj_sha256','source_vlog.prj'),
                               ('compile_sha256','compile_snapshot.log'),
                               ('elab_sha256','elaborate_snapshot.log'),
                               ('tcl_sha256','runall.tcl')):
            require(sha256(build/relative)==meta['build'][field],
                    'V4f4C archived build source digest mismatch: '+relative)
        v3a=meta['source_v3a_prj_sha256']
        require(meta['build']['prj_sha256']==v3a,
                'V4f4C compile snapshot differs from V3a source .prj')
        for name,digest in meta['build']['source_hashes'].items():
            p=Path(name)
            require(p.is_file() and sha256(p)==digest,
                    'V4f4C compiled source changed: '+name)
        require(len(meta['build'].get('toolchain_hashes',{}))==4 and
                len(meta['build'].get('build_process_hashes',{}))==10,
                'V4f4C job-contained toolchain/process evidence incomplete')
        for name,digest in meta['build']['toolchain_hashes'].items():
            p=Path(name)
            require(p.is_file() and sha256(p)==digest,
                    'V4f4C Vivado/XSim toolchain drift: '+name)
        for name,digest in meta['build']['build_process_hashes'].items():
            stage,field=name.split('_',1)
            reference={'job_process_sha256':stage+'_job_process.json',
                       'cmd_sha256':stage+'_snapshot.cmd',
                       'wrapper_sha256':stage+'_wrapper.log',
                       'job_launch_sha256':stage+'_job_launch.json',
                       'child_lifecycle_sha256':stage+'_child_lifecycle.json'}
            require(field in reference and sha256(build/reference[field])==digest,
                    'V4f4C build stage process receipt changed: '+name)
        for stage in ('compile','elaborate'):
            item=read_json(build/(stage+'_job_process.json'))
            child=read_json(build/(stage+'_child_lifecycle.json'))
            require(item.get('return_code')==0 and
                    item.get('external_timeout') is False and
                    item.get('termination_requested') is False and
                    item.get('owned_active_processes_after')==0 and
                    item.get('job_object_kill_on_close') is True and
                    isinstance(child.get('cmd_pid'),int) and
                    child.get('complete') is True and child.get('return_code')==0,
                    'V4f4C compiler/elaborator job receipt not a closed success')
    parent=meta.get('parent_failed_attempt')
    if parent is not None:
        require(parent['scenario_id']==meta['scenario_id'] and
                Path(parent['dir']).resolve()!=out.resolve() and
                sha256(Path(parent['dir'])/'attempt.json')==parent['attempt_json_sha256'] and
                read_json(Path(parent['dir'])/'attempt.json').get('status')==BLOCKED,
                'V4f4C parent failed attempt changed or is still running')
    if meta.get('process_receipt_sha256') is not None:
        require((out/RECEIPT).is_file() and
                sha256(out/RECEIPT)==meta['process_receipt_sha256'] and
                read_json(out/RECEIPT)==meta.get('process'),
                'V4f4C process receipt changed')
    if meta.get('process') is not None:
        p=meta['process']
        for receipt_name,field in (('job_launch.json','job_launch_sha256'),
                                   ('child_lifecycle.json','child_receipt_sha256')):
            if p.get(field) is not None:
                require((out/receipt_name).is_file() and
                        sha256(out/receipt_name)==p[field],
                        'V4f4C Windows job/child lifecycle receipt changed')
    require(meta['full400_gate']=='BLOCKED_G0_G7_REQUIRED',
            'V4f4C release gate incorrectly changed')


def _meta(rows:list[dict[str,str]],row:dict[str,str],pins:dict[str,str],
          out:Path,args:argparse.Namespace,parent:dict[str,Any]|None) -> dict[str,Any]:
    write_csv(out/'canary_selection.csv',FIELDS,rows)
    return {'schema':SCHEMA,'status':RUNNING,'created_utc':now(),
            'execution_scope':'ONE_FROZEN_CANARY_XSIM_MAX',
            'source_authority':'vivado', 'trial':args.trial,
            'scenario_id':row['scenario_id'],'scenario':row,
            'canary_sha256':sha256(out/'canary_selection.csv'),
            'input_pins':pins,'max_xsim_processes':1,
            'xsim_invocations_budgeted':1,'xsim_invocations_committed':0,
            'source_v3a_prj_sha256':sha256(args.v3a_dir/'source_vlog.prj'),
            'validation':None,'process':None,'process_receipt_sha256':None,
            'build':None,'run_hashes':None,
            'parent_failed_attempt':parent,
            'scientific_protocol_core_sha256':PROTOCOL_CORE_HASH,
            'scientific_protocol_approved':False,
            'full400_runner_qualified':False,'full400_authorized':False,
            'full400_executed':False,'full400_gate':'BLOCKED_G0_G7_REQUIRED',
            'target_status_modified':False}


def execute(args:argparse.Namespace,repo:Path,results:Path,weights:Path,
            out:Path, v4e:Path) -> dict[str,Any]:
    require(args.execute and os.name=='nt',
            'Execution requires explicit --execute on Windows')
    require(args.trial in TRIALS and args.scenario_id in CANARY_IDS,
            'Execution requires one frozen V4f4C canary ID and known trial')
    require(30<=args.timeout_seconds<=LIMIT_SECONDS and
            0.05<=args.kill_after_seconds<=30 and
            args.kill_after_seconds<args.timeout_seconds,
            'Execution requires finite timeout 30..900s and kill delay 0.05..30s')
    require(args.trial!='crash-before-commit' or args.confirm_abrupt_exit,
            'Crash-before-commit requires explicit --confirm-abrupt-exit')
    require(args.project is not None and args.vivado,
            'Vivado .xpr and --vivado required for bounded Windows execution')
    project=resolved(repo,args.project)
    require(project.is_file() and project.suffix.lower()=='.xpr',
            'Vivado authority .xpr missing')
    require(args.fileset.isidentifier(), 'Invalid Vivado fileset')
    require((results/V3A_NAME/'source_vlog.prj').is_file() and
            (results/V3A_NAME/'validation_manifest.json').is_file(),
            'V3a pinned Vivado .prj evidence missing')
    rows,pins=input_preflight(repo,results,weights,v4e,replay=True)
    row=next(r for r in rows if r['scenario_id']==args.scenario_id)
    safe_out(repo,results,weights,out)
    parent=None
    if args.parent_failed_dir:
        parent_dir=resolved(repo,args.parent_failed_dir)
        require(parent_dir!=out and parent_dir.parent==results and
                parent_dir.name.startswith('v4f4c_'),
                'Retry parent must be another V4f4C attempt')
        old=read_json(parent_dir/'attempt.json')
        require(old.get('status')==BLOCKED and
                old.get('scenario_id')==row['scenario_id'] and
                old.get('xsim_invocations_committed')==0,
                'Retry requires a BLOCKED, finished parent; orphan RUNNING needs manual OS review')
        parent={'dir':str(parent_dir),'attempt_json_sha256':sha256(parent_dir/'attempt.json'),
                'scenario_id':row['scenario_id']}
    out.mkdir(parents=True,exist_ok=False)
    meta=_meta(rows,row,pins,out,args,parent)
    v4da.atomic_write_json(out/'attempt.json',meta)
    try:
        build=out/'build_001'
        build.mkdir(exist_ok=False)
        snap=build_job_contained_snapshot(repo,project,args.vivado,args.fileset,
                                           build,args.timeout_seconds,results/V3A_NAME)
        require(snap['elaboration_mode']=='FULL_BUILD' and
                snap['compile_prj_sha256']==meta['source_v3a_prj_sha256'],
                'V4f4C snapshot did not reproduce V3a Vivado source')
        tcl=build/'runall.tcl'
        tcl.write_text('run all\nquit\n',encoding='ascii')
        meta['build']={'prj_sha256':sha256(build/'source_vlog.prj'),
            'compile_sha256':sha256(build/'compile_snapshot.log'),
            'elab_sha256':sha256(build/'elaborate_snapshot.log'),
            'tcl_sha256':sha256(tcl),'source_hashes':snap['source_hashes'],
            'toolchain_hashes':snap['toolchain_hashes'],
            'build_process_hashes':snap['build_process_hashes'],
            'build_strategy':snap['build_strategy']}
        check_pins(pins)
        v4da.atomic_write_json(out/'attempt.json',meta)
        runs=out/'runs';runs.mkdir(exist_ok=False)
        sid=row['scenario_id']
        cmd=runs/(sid+'.cmd');log=runs/(sid+'.log')
        console=runs/(sid+'.console.log');wrapper=runs/(sid+'.wrapper.log')
        cmd.write_text(make_cmd_run(snap['simdir'],snap['vivado_root'],row,
                repo/'fi/pilot400/samples'/row['sample_id'],tcl,log,console,
                profile_only=False),encoding='ascii')
        inflight={'schema':'V4F4C_INFLIGHT_V1','scenario_id':sid,
                  'trial':args.trial,'cmd_sha256':sha256(cmd),
                  'started_utc':now(),'max_xsim_processes':1}
        v4da.atomic_write_json(out/INFLIGHT,inflight)
        meta['cmd_sha256']=sha256(cmd)
        v4da.atomic_write_json(out/'attempt.json',meta)
        gate=out/'.start_gate'
        child_receipt=out/'child_lifecycle.json'
        launch_receipt=out/'job_launch.json'
        print('V4F4C_XSIM_BOUNDED: 1/1 '+sid+' trial='+args.trial+' FULL400 BLOCKED',flush=True)
        process=_job_run(cmd,snap['simdir'],wrapper,gate,child_receipt,
                         launch_receipt,log,args.trial,args.timeout_seconds,args.kill_after_seconds)
        process['scenario_id']=sid
        process['trial']=args.trial
        process['child_receipt_sha256']=(sha256(child_receipt) if child_receipt.is_file() else None)
        process['child_started']=(isinstance(read_json(child_receipt).get('cmd_pid'),int)
                                  if child_receipt.is_file() else False)
        process['job_launch_sha256']=sha256(launch_receipt) if launch_receipt.is_file() else None
        process['marker_evidence']=list(marker_counts(log))
        process['classification']=classify_observed_process(args.trial,process)
        meta['process']=process
        v4da.atomic_write_json(out/RECEIPT,process)
        meta['process_receipt_sha256']=sha256(out/RECEIPT)
        check_pins(pins)
        require(all(Path(p).is_file() and sha256(Path(p))==dig
                    for p,dig in snap['source_hashes'].items()),
                'Vivado compiled HDL drift during trial')
        if args.trial in ('healthy','crash-before-commit'):
            require(process['classification']=='RAW_RESULT_ELIGIBLE',
                    'Healthy XSim process did not finish normally')
            require(log.is_file() and console.is_file() and wrapper.is_file(),
                    'Missing actual XSim output log')
            text=console.read_text(encoding='utf-8',errors='replace')
            require('Expected a switch' not in text and not
                    re.search(r'^(?:ERROR:|FATAL:|Fatal:|\*\* Error:)',text,re.M),
                    'XSim console contains error')
            result=v4f2.strict_result(log,row)
            if args.trial=='crash-before-commit':
                # Genuine abrupt Python process exit, with uncommitted raw evidence.
                # NEVER append the successful result to attempt.json here.
                print('V4F4C_ABORT_BEFORE_COMMIT: process exit 86; '
                      'orphan evidence intentionally preserved',flush=True)
                os._exit(86)
            meta['validation']=result
            meta['run_hashes']={key:sha256(path) for key,path in
                  (('cmd',cmd),('log',log),('console',console),('wrapper',wrapper))}
            meta['xsim_invocations_committed']=1
            meta['status']=SUCCESS
            v4da.atomic_write_json(out/'attempt.json',meta)
            (out/INFLIGHT).unlink()
            verify_attempt(out,meta)
            print('V4F4C_HEALTHY_PASS: 1/1 XSim strictly replayed; '
                  'G6 FULL400 NOT QUALIFIED; FULL400 BLOCKED',flush=True)
        else:
            # Even a perfectly reproduced forced interruption is NOT a FI outcome.
            meta['status']=BLOCKED
            meta['failure_reason']=process['classification']
            v4da.atomic_write_json(out/'attempt.json',meta)
            print('V4F4C_FAILURE_PROBE_BLOCKED: '+process['classification']+
                  '; 0 committed FI; new attempt required; FULL400 BLOCKED',flush=True)
        return meta
    except BaseException as exc:
        meta['status']=BLOCKED
        meta['failure_reason']=type(exc).__name__+': '+str(exc)
        v4da.atomic_write_json(out/'attempt.json',meta)
        raise


def plan_only(args:argparse.Namespace,repo:Path,results:Path,weights:Path,
              out:Path,v4e:Path) -> dict[str,Any]:
    require(not args.execute and not args.parent_failed_dir,
            'Plan cannot execute or retry')
    rows,pins=input_preflight(repo,results,weights,v4e,replay=False)
    safe_out(repo,results,weights,out)
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'canary_selection.csv',FIELDS,rows)
    metadata={'schema':PLAN_SCHEMA,'status':PLAN,'created_utc':now(),
       'selected_count':8,'selected_ids':list(CANARY_IDS),
       'canary_sha256':sha256(out/'canary_selection.csv'),
       'input_pins':pins,'new_xsim_runs':0,'scientific_protocol_approved':False,
       'full400_authorized':False,'full400_runner_qualified':False,
       'full400_executed':False,'full400_gate':'BLOCKED_G0_G7_REQUIRED'}
    v4da.atomic_write_json(out/'v4f4c_plan.json',metadata)
    print('V4F4C_PLAN_PASS: 8/8 frozen IMG0003/IMG0006 canaries; '
          'max 1 XSim per fresh attempt; 0 XSim; FULL400 BLOCKED',flush=True)
    print('V4F4C_OUTPUT: '+str(out),flush=True)
    return metadata


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--v4e-dir',type=Path,default=Path('fi/results/v4e_release_review_01'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f4c_plan_01'))
    ap.add_argument('--mode',choices=('plan','execute','full400'),default='plan')
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--trial',choices=TRIALS,default='healthy')
    ap.add_argument('--scenario-id',default='')
    ap.add_argument('--project',type=Path)
    ap.add_argument('--vivado',default='')
    ap.add_argument('--fileset',default='sim_1')
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--kill-after-seconds',type=float,default=2.0)
    ap.add_argument('--parent-failed-dir',type=Path)
    ap.add_argument('--confirm-abrupt-exit',action='store_true')
    # Private launcher arguments are accepted only when parent supplied gate.
    ap.add_argument('--child-launcher',action='store_true',help=argparse.SUPPRESS)
    ap.add_argument('--cmdfile',type=Path,help=argparse.SUPPRESS)
    ap.add_argument('--simdir',type=Path,help=argparse.SUPPRESS)
    ap.add_argument('--gate',type=Path,help=argparse.SUPPRESS)
    ap.add_argument('--child-receipt',type=Path,help=argparse.SUPPRESS)
    args=ap.parse_args()
    if args.child_launcher:
        require(all((args.cmdfile,args.simdir,args.gate,args.child_receipt)),
                'Launcher inputs missing')
        _launcher_main(args)
        return
    try:
        require(args.mode!='full400', 'FULL400 BLOCKED; no G0-G7 authorization')
        require(not args.execute or args.mode=='execute',
                '--execute only permitted for one bounded failure attempt')
        require(args.mode!='execute' or args.execute,
                'One-case execution requires explicit --execute')
        repo=args.repo.resolve()
        results=resolved(repo,args.results_dir)
        weights=resolved(repo,args.weights_dir)
        v4e=resolved(repo,args.v4e_dir)
        out=resolved(repo,args.out_dir)
        if args.mode=='plan':
            plan_only(args,repo,results,weights,out,v4e)
        else:
            args.v3a_dir=results/V3A_NAME
            with global_campaign_lock(results):
                enforce_aggregate_budget(results)
                execute(args,repo,results,weights,out,v4e)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,AssertionError) as exc:
        raise SystemExit('V4F4C_FAIL: '+str(exc)) from exc

if __name__=='__main__':main()

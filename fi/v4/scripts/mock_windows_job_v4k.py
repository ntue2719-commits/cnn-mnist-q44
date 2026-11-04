#!/usr/bin/env python3
"""V4K. Windows Job Object test-only probe; NEVER Vivado, XSim or Full400.

Only launches the fixed adjacent mock_job_child_v4k.py with fixed arguments.
No user-supplied executable, command, scenario, signal or process ID exists.
On unsupported platforms --mode probe refuses. Tests use pure policy logic.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

SCHEMA = 'CNN_PEP_V4K_WINDOWS_JOB_MOCK_PROBE_V1'
RECEIPT_SCHEMA = 'CNN_PEP_V4K_WINDOWS_JOB_MOCK_RECEIPT_V1'
STOP = 'MOCK_PROCESS_TEST_ONLY_G6_NOT_QUALIFIED_FULL400_BLOCKED'
CHILD_NAME = 'mock_job_child_v4k.py'
PROBE_NAME = 'v4k_mock_job_probe.json'
RECEIPT_NAME = 'v4k_mock_job_receipt.json'
ACKS = ('parent_ready.txt', 'grandchild_ready.txt')
PATTERN = re.compile(r'v4k_mockjob_[A-Za-z0-9_]+\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def fail_unless(cond: bool, why: str) -> None:
    if not cond:
        raise ValueError(why)


def canonical(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True,
                       allow_nan=False) + '\n').encode('utf-8')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha(path.read_bytes())


def no_links(path: Path) -> None:
    for item in (path, *path.parents):
        linked = item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction())
        fail_unless(not linked, 'Symlink/junction entry rejected: ' + str(item))


def new_output(repo: Path, arg: Path) -> tuple[Path, Path]:
    no_links(repo.absolute())
    repo = repo.resolve()
    results = repo / 'fi' / 'results'
    fail_unless(results.is_dir(), 'Missing fi/results directory')
    no_links(results)
    fail_unless('..' not in arg.parts, 'Path traversal is forbidden')
    original = arg if arg.is_absolute() else repo / arg
    no_links(original.absolute())
    final = original.resolve()
    fail_unless(final.parent == results and PATTERN.fullmatch(final.name) is not None,
                'Output outside permitted direct fi/results/v4k_mockjob_* namespace')
    stage = final.with_name('.' + final.name + '.inflight')
    no_links(stage.absolute())
    fail_unless(not final.exists() and not stage.exists(),
                'Existing output or interrupted inflight: preserve and investigate')
    return final, stage


def source_path(script: Path) -> Path:
    no_links(script.absolute())
    path = script.resolve()
    fail_unless(path.is_file() and path.name == CHILD_NAME and
                path.parent == Path(__file__).resolve().parent and
                path.stat().st_size <= 32 * 1024,
                'Only the adjacent bounded mock child is allowed')
    return path


def write_new(path: Path, obj: dict[str, Any]) -> None:
    raw = canonical(obj)
    with path.open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


if sys.platform == 'win32':
    # Win32 definitions correspond to MSDN Job Objects and CreateProcessW;
    # explicit argtypes are essential on 64-bit Windows.
    HANDLE = wintypes.HANDLE
    DWORD = wintypes.DWORD
    BOOL = wintypes.BOOL
    LPWSTR = wintypes.LPWSTR
    SIZE_T = ctypes.c_size_t
    ULONG_PTR = ctypes.c_size_t

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ('cb', DWORD), ('lpReserved', LPWSTR), ('lpDesktop', LPWSTR),
            ('lpTitle', LPWSTR), ('dwX', DWORD), ('dwY', DWORD),
            ('dwXSize', DWORD), ('dwYSize', DWORD), ('dwXCountChars', DWORD),
            ('dwYCountChars', DWORD), ('dwFillAttribute', DWORD),
            ('dwFlags', DWORD), ('wShowWindow', wintypes.WORD),
            ('cbReserved2', wintypes.WORD), ('lpReserved2', ctypes.c_void_p),
            ('hStdInput', HANDLE), ('hStdOutput', HANDLE), ('hStdError', HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [('hProcess', HANDLE), ('hThread', HANDLE),
                    ('dwProcessId', DWORD), ('dwThreadId', DWORD)]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                    ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', DWORD), ('MinimumWorkingSetSize', SIZE_T),
                    ('MaximumWorkingSetSize', SIZE_T), ('ActiveProcessLimit', DWORD),
                    ('Affinity', ULONG_PTR), ('PriorityClass', DWORD),
                    ('SchedulingClass', DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [('ReadOperationCount', ctypes.c_uint64),
                    ('WriteOperationCount', ctypes.c_uint64),
                    ('OtherOperationCount', ctypes.c_uint64),
                    ('ReadTransferCount', ctypes.c_uint64),
                    ('WriteTransferCount', ctypes.c_uint64),
                    ('OtherTransferCount', ctypes.c_uint64)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BASIC_LIMIT),
                    ('IoInfo', IO_COUNTERS), ('ProcessMemoryLimit', SIZE_T),
                    ('JobMemoryLimit', SIZE_T), ('PeakProcessMemoryUsed', SIZE_T),
                    ('PeakJobMemoryUsed', SIZE_T)]

    class BASIC_ACCOUNTING(ctypes.Structure):
        _fields_ = [('TotalUserTime', ctypes.c_int64),
                    ('TotalKernelTime', ctypes.c_int64),
                    ('ThisPeriodTotalUserTime', ctypes.c_int64),
                    ('ThisPeriodTotalKernelTime', ctypes.c_int64),
                    ('TotalPageFaultCount', DWORD), ('TotalProcesses', DWORD),
                    ('ActiveProcesses', DWORD), ('TotalTerminatedProcesses', DWORD)]

    K = ctypes.WinDLL('kernel32', use_last_error=True)
    K.CreateJobObjectW.argtypes = (ctypes.c_void_p, LPWSTR)
    K.CreateJobObjectW.restype = HANDLE
    K.SetInformationJobObject.argtypes = (HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD)
    K.SetInformationJobObject.restype = BOOL
    K.AssignProcessToJobObject.argtypes = (HANDLE, HANDLE)
    K.AssignProcessToJobObject.restype = BOOL
    K.QueryInformationJobObject.argtypes = (HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            DWORD, ctypes.c_void_p)
    K.QueryInformationJobObject.restype = BOOL
    K.CreateProcessW.argtypes = (LPWSTR, LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                 BOOL, DWORD, ctypes.c_void_p, LPWSTR,
                                 ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION))
    K.CreateProcessW.restype = BOOL
    K.ResumeThread.argtypes = (HANDLE,)
    K.ResumeThread.restype = DWORD
    K.WaitForSingleObject.argtypes = (HANDLE, DWORD)
    K.WaitForSingleObject.restype = DWORD
    K.GetExitCodeProcess.argtypes = (HANDLE, ctypes.POINTER(DWORD))
    K.GetExitCodeProcess.restype = BOOL
    K.TerminateJobObject.argtypes = (HANDLE, ctypes.c_uint)
    K.TerminateJobObject.restype = BOOL
    K.TerminateProcess.argtypes = (HANDLE, ctypes.c_uint)
    K.TerminateProcess.restype = BOOL
    K.CloseHandle.argtypes = (HANDLE,)
    K.CloseHandle.restype = BOOL

    CREATE_SUSPENDED = 0x00000004
    CREATE_NO_WINDOW = 0x08000000
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9
    JobObjectBasicAccountingInformation = 1
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    WAIT_FAILED = 0xFFFFFFFF
    RESUME_FAILED = 0xFFFFFFFF
    STILL_ACTIVE = 259
else:
    K = None


def win_ok(value: Any, operation: str) -> None:
    fail_unless(bool(value), f'{operation} failed: Windows error {ctypes.get_last_error()}')


class WindowsJob:
    """A private per-case Job Object, child initially suspended to avoid race.

    All handles are owned by this object. close() terminates the entire job,
    including descendants, even if assignment/resume/validation fails.
    """
    def __init__(self) -> None:
        fail_unless(K is not None, 'Windows Job Objects only supported on Windows')
        self.job: Any = None
        self.proc = PROCESS_INFORMATION()
        self.created = False
        self.assigned = False
        self.terminated = False
        self.owned_pid: int | None = None
        self.pid_attempted: int | None = None
        try:
            self.job = K.CreateJobObjectW(None, None)
            win_ok(self.job, 'CreateJobObjectW')
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win_ok(K.SetInformationJobObject(self.job, JobObjectExtendedLimitInformation,
                                             ctypes.byref(info), ctypes.sizeof(info)),
                   'SetInformationJobObject')
        except BaseException:
            self.close()
            raise

    def start(self, executable: Path, child: Path, args: list[str], cwd: Path) -> int:
        fail_unless(self.job is not None and not self.created, 'Job start reused')
        no_links(executable.absolute())
        fail_unless(executable.is_file() and child.name == CHILD_NAME,
                    'Missing Python interpreter or approved mock child')
        cmd = subprocess.list2cmdline([str(executable), '-B', str(child), *args])
        # Win32 API allows command-line mutation: pass a mutable unicode buffer.
        command_line = ctypes.create_unicode_buffer(cmd)
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        pi = PROCESS_INFORMATION()
        flags = CREATE_SUSPENDED | CREATE_NO_WINDOW
        win_ok(K.CreateProcessW(str(executable), command_line, None, None, False,
                                flags, None, str(cwd), ctypes.byref(si), ctypes.byref(pi)),
               'CreateProcessW')
        self.proc = pi
        self.created = True
        self.pid_attempted = int(pi.dwProcessId)
        win_ok(K.AssignProcessToJobObject(self.job, pi.hProcess),
               'AssignProcessToJobObject')
        self.assigned = True
        fail_unless(K.ResumeThread(pi.hThread) != RESUME_FAILED, 'ResumeThread failed')
        self.owned_pid = int(pi.dwProcessId)
        return self.owned_pid

    def active(self) -> int:
        fail_unless(self.job is not None, 'Job already closed')
        a = BASIC_ACCOUNTING()
        win_ok(K.QueryInformationJobObject(self.job, JobObjectBasicAccountingInformation,
                                            ctypes.byref(a), ctypes.sizeof(a), None),
               'QueryInformationJobObject')
        return int(a.ActiveProcesses)

    def wait(self, milliseconds: int) -> bool:
        fail_unless(self.created and self.proc.hProcess, 'No child process')
        status = int(K.WaitForSingleObject(self.proc.hProcess, milliseconds))
        fail_unless(status in (WAIT_OBJECT_0, WAIT_TIMEOUT), 'WaitForSingleObject failed')
        return status == WAIT_OBJECT_0

    def exitcode(self) -> int:
        code = DWORD()
        win_ok(K.GetExitCodeProcess(self.proc.hProcess, ctypes.byref(code)),
               'GetExitCodeProcess')
        return int(code.value)

    def terminate(self) -> None:
        if self.job is not None and not self.terminated:
            win_ok(K.TerminateJobObject(self.job, 87), 'TerminateJobObject')
            self.terminated = True

    def release_child_handles(self) -> None:
        # ActiveProcesses may not decrement until process references are
        # released; collect exitcode before calling this method.
        if self.created:
            if self.proc.hThread:
                K.CloseHandle(self.proc.hThread)
                self.proc.hThread = None
            if self.proc.hProcess:
                K.CloseHandle(self.proc.hProcess)
                self.proc.hProcess = None

    def await_zero(self, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.active() == 0:
                return True
            time.sleep(0.02)
        return self.active() == 0

    def close(self) -> None:
        # If job assignment failed, the still-suspended process is NOT owned
        # by that job. Kill it directly, otherwise a suspended orphan leaks.
        if self.created and not self.assigned and self.proc.hProcess:
            K.TerminateProcess(self.proc.hProcess, 87)
            K.WaitForSingleObject(self.proc.hProcess, 1500)
        # Closing KILL_ON_JOB_CLOSE cleans assigned child and descendants.
        if self.job is not None:
            K.CloseHandle(self.job)
            self.job = None
        self.release_child_handles()
        self.created = False

    def __enter__(self) -> 'WindowsJob':
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def run_one(case: str, stage: Path, child: Path) -> dict[str, Any]:
    """Run exactly one benign fixed mock case with private job ownership."""
    fail_unless(case in ('healthy', 'timeout', 'tree'), 'Unsupported case')
    args = ({'healthy': ['healthy'],
             'timeout': ['sleep', 'V4K_FIXED_SLEEP'],
             'tree': ['tree', str(stage / ACKS[0]), 'V4K_FIXED_TREE']})[case]
    t0 = time.monotonic()
    with WindowsJob() as job:
        pid = job.start(Path(sys.executable).resolve(), child, args, stage)
        count_before = job.active()
        if case != 'healthy':
            fail_unless(count_before >= 1, 'Job lost mock child before observation')
        observed = False
        timed_out = False
        terminated = False
        active_at_trigger = None
        if case == 'healthy':
            completed = job.wait(7000)
            fail_unless(completed and job.exitcode() == 0,
                        'Healthy mock did not finish cleanly')
            exit_code = job.exitcode()
            observed = True
            job.release_child_handles()
            fail_unless(job.await_zero(), 'Healthy job accounting did not quiesce')
        elif case == 'timeout':
            completed = job.wait(350)
            fail_unless(not completed, 'Timeout fixture finished unexpectedly')
            active_at_trigger = job.active()
            fail_unless(active_at_trigger >= 1, 'No active owned process before timeout')
            timed_out = True
            job.terminate()
            terminated = True
            fail_unless(job.wait(5000), 'Timeout child process did not terminate')
            exit_code = job.exitcode()
            fail_unless(exit_code == 87, 'Unexpected timeout child exit status')
            job.release_child_handles()
            fail_unless(job.await_zero(), 'Timeout job accounting did not quiesce')
        else:
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and not (stage / ACKS[0]).exists():
                time.sleep(0.025)
            fail_unless((stage / ACKS[0]).is_file() and
                        (stage / ACKS[0]).read_bytes() == b'V4K_PARENT_AND_CHILD_READY\n' and
                        (stage / ACKS[1]).read_bytes() == b'V4K_GRANDCHILD_ALIVE\n',
                        'Parent/descendant mock handshake not confirmed')
            active_at_trigger = job.active()
            fail_unless(active_at_trigger >= 2, 'Descendant was not contained in same Job Object')
            observed = True
            job.terminate()
            terminated = True
            fail_unless(job.wait(5000), 'Parent process did not terminate')
            exit_code = job.exitcode()
            fail_unless(exit_code == 87, 'Unexpected descendant cleanup exit status')
            job.release_child_handles()
            fail_unless(job.await_zero(), 'Descendant job accounting did not quiesce')
        post = job.active()
        fail_unless(post == 0, 'Owned Job Object processes leaked')
        elapsed = round(time.monotonic() - t0, 5)
        result = {
            'case': case,
            'mock_python_child': True,
            'python_pid': pid,
            'child_source_sha256': sha_file(child),
            'active_processes_initial_observed': count_before,
            'active_processes_before_termination': active_at_trigger,
            'active_processes_after_completion': post,
            'terminated_job_object': terminated,
            'infrastructure_timeout': timed_out,
            'design_noncompletion': False,
            'fi_applied_marker_observed': False,
            'fi_result_marker_observed': False,
            'mock_handshake_confirmed': observed,
            'elapsed_seconds': elapsed,
            'exit_code_after_completion': exit_code,
        }
        return result


def probe(repo: Path, out: Path, child: Path) -> dict[str, Any]:
    fail_unless(sys.platform == 'win32', 'Real Windows Job Object mock probe requires Windows')
    final, stage = new_output(repo, out)
    child = source_path(child)
    stage.mkdir()
    source_sha = sha_file(child)
    try:
        events = [run_one(c, stage, child) for c in ('healthy', 'timeout', 'tree')]
        fail_unless(sha_file(child) == source_sha,
                    'Mock source drift during probe')
        obj = {
            'schema': SCHEMA,
            'scope': 'BENIGN_FIXED_PYTHON_CHILD_WINDOWS_JOB_OBJECT_ONLY_NOT_XSIM',
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'mock_child_source_sha256': source_sha,
            'supervisor_source_sha256': sha_file(Path(__file__).resolve()),
            'host_platform': sys.platform,
            'python_version': list(sys.version_info[:3]),
            'job_object_cases': events,
            'planned_fi_scenarios': 400,
            'real_fi_runs_executed': 0,
            'vivado_builds_executed': 0,
            'xsim_invocations_executed': 0,
            'scientific_outcomes_committed': 0,
            'g6_production_qualified': False,
            'g7_scientific_approved': False,
            'full400_authorized': False,
            'release_gate': STOP,
        }
        write_new(stage / PROBE_NAME, obj)
        receipt = {
            'schema': RECEIPT_SCHEMA,
            'probe_sha256': sha_file(stage / PROBE_NAME),
            'case_count': len(events),
            'mock_source_sha256': source_sha,
            'fi_outcomes_committed': 0,
            'new_xsim_runs': 0,
            'full400_authorized': False,
            'release_gate': STOP,
        }
        write_new(stage / RECEIPT_NAME, receipt)
        fail_unless(not final.exists() and sha_file(child) == source_sha,
                    'Output/source changed before finalization')
        stage.rename(final)
        return receipt
    except BaseException:
        # Preserve inflight and never promote partial progress to committed.
        raise


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('probe', 'plan', 'execute', 'full400'), default='plan')
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4k_mockjob_01'))
    p.add_argument('--approve', action='store_true')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    try:
        fail_unless(args.mode == 'probe' and not args.approve and not args.execute,
                    'Only fixed Python-child mock probe mode is permitted')
        child = Path(__file__).resolve().with_name(CHILD_NAME)
        result = probe(args.repo, args.out_dir, child)
        print('V4K_WINDOWS_JOB_MOCK_PROBE_PASS: 3/3 fixed Python mock cases; '
              'healthy, infrastructure timeout, descendant cleanup; '
              '0 Vivado build; 0 XSim; FULL400 BLOCKED', flush=True)
        print('V4K_OUTPUT: ' + str((args.out_dir if args.out_dir.is_absolute()
                                    else args.repo / args.out_dir).resolve()), flush=True)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise SystemExit('V4K_MOCK_PROBE_BLOCKED: ' + str(exc)) from exc


if __name__ == '__main__':
    cli()

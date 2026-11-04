#!/usr/bin/env python3
"""V4K. Independent read-only replay of mock Windows Job Object receipts.

Not a live OS observation, real-XSim run, G6 signoff, or Full400 release.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import mock_windows_job_v4k as p

AUDIT_SCHEMA = 'CNN_PEP_V4K_WINDOWS_JOB_MOCK_AUDIT_V1'
AUDIT_NAME = 'v4k_mock_job_audit.json'
PROBE_KEYS = {'schema','scope','created_utc','mock_child_source_sha256',
              'supervisor_source_sha256','host_platform','python_version',
              'job_object_cases','planned_fi_scenarios','real_fi_runs_executed',
              'vivado_builds_executed','xsim_invocations_executed',
              'scientific_outcomes_committed','g6_production_qualified',
              'g7_scientific_approved','full400_authorized','release_gate'}
EVENT_KEYS = {'case','mock_python_child','python_pid','child_source_sha256',
              'active_processes_initial_observed',
              'active_processes_before_termination','active_processes_after_completion',
              'terminated_job_object','infrastructure_timeout','design_noncompletion',
              'fi_applied_marker_observed','fi_result_marker_observed',
              'mock_handshake_confirmed','elapsed_seconds','exit_code_after_completion'}
RECEIPT_KEYS = {'schema','probe_sha256','case_count','mock_source_sha256',
                'fi_outcomes_committed','new_xsim_runs','full400_authorized','release_gate'}


def strict_json(path: Path) -> dict[str, Any]:
    p.no_links(path.absolute())
    p.fail_unless(path.is_file() and path.stat().st_size <= 64 * 1024,
                  'Missing/unbounded JSON input: ' + str(path))
    raw = path.read_bytes()
    import json
    def no_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for k, v in pairs:
            p.fail_unless(k not in obj, 'Duplicate JSON key: ' + k)
            obj[k] = v
        return obj
    def reject_constant(c: str) -> Any:
        raise ValueError('Nonfinite JSON constant ' + c)
    obj = json.loads(raw.decode('utf-8', errors='strict'),
                     object_pairs_hook=no_duplicate, parse_constant=reject_constant)
    p.fail_unless(type(obj) is dict and raw == p.canonical(obj),
                  'JSON must be canonical UTF-8 LF')
    return obj


def check_event(r: Any, expected: str, child_sha: str) -> None:
    p.fail_unless(type(r) is dict and set(r) == EVENT_KEYS and
                  r.get('case') == expected and
                  r.get('mock_python_child') is True and
                  type(r.get('python_pid')) is int and r['python_pid'] > 0 and
                  r.get('child_source_sha256') == child_sha and
                  type(r.get('active_processes_initial_observed')) is int and
                  r['active_processes_initial_observed'] >= 0 and
                  r.get('active_processes_after_completion') == 0 and
                  r.get('design_noncompletion') is False and
                  r.get('fi_applied_marker_observed') is False and
                  r.get('fi_result_marker_observed') is False and
                  type(r.get('elapsed_seconds')) in (int, float) and
                  0 <= r['elapsed_seconds'] <= 40 and
                  type(r.get('exit_code_after_completion')) is int,
                  'Invalid/mock FI semantics or lifecycle fields: ' + expected)
    if expected == 'healthy':
        p.fail_unless(r.get('terminated_job_object') is False and
                      r.get('infrastructure_timeout') is False and
                      r.get('mock_handshake_confirmed') is True and
                      r.get('active_processes_before_termination') is None and
                      r.get('exit_code_after_completion') == 0,
                      'Healthy child lifecycle invalid')
    else:
        p.fail_unless(r.get('terminated_job_object') is True and
                      type(r.get('active_processes_before_termination')) is int and
                      r['active_processes_before_termination'] >= (2 if expected == 'tree' else 1) and
                      r['active_processes_initial_observed'] >= 1 and
                      r.get('exit_code_after_completion') == 87,
                      'Termination scope or process count invalid')
        if expected == 'timeout':
            p.fail_unless(r.get('infrastructure_timeout') is True and
                          r.get('mock_handshake_confirmed') is False,
                          'Infrastructure timeout improperly classified')
        else:
            p.fail_unless(r.get('infrastructure_timeout') is False and
                          r.get('mock_handshake_confirmed') is True,
                          'Descendant mock cleanup improperly classified')


def audit(repo: Path, evidence: Path, child: Path) -> dict[str, Any]:
    p.no_links(repo.absolute())
    repo = repo.resolve()
    p.no_links(evidence.absolute())
    evidence = evidence.resolve()
    p.fail_unless(evidence.parent == repo / 'fi' / 'results' and
                  p.PATTERN.fullmatch(evidence.name) is not None and evidence.is_dir(),
                  'Evidence directory outside bounded namespace')
    files = set(x.name for x in evidence.iterdir())
    p.fail_unless(files == {p.PROBE_NAME, p.RECEIPT_NAME, *p.ACKS} and
                  all(x.is_file() and not x.is_symlink() for x in evidence.iterdir()),
                  'Orphan/missing/symlinked evidence file')
    probe = strict_json(evidence / p.PROBE_NAME)
    receipt = strict_json(evidence / p.RECEIPT_NAME)
    child = p.source_path(child)
    child_sha = p.sha_file(child)
    p.fail_unless(set(probe) == PROBE_KEYS and
                  set(receipt) == RECEIPT_KEYS and
                  probe.get('supervisor_source_sha256') == p.sha_file(Path(p.__file__).resolve()) and
                  type(probe.get('created_utc')) is str and
                  bool(probe['created_utc']) and
                  probe.get('schema') == p.SCHEMA and
                  probe.get('scope') == 'BENIGN_FIXED_PYTHON_CHILD_WINDOWS_JOB_OBJECT_ONLY_NOT_XSIM' and
                  probe.get('mock_child_source_sha256') == child_sha and
                  probe.get('host_platform') == 'win32' and
                  type(probe.get('python_version')) is list and
                  len(probe['python_version']) == 3 and
                  all(type(v) is int for v in probe['python_version']) and
                  probe.get('planned_fi_scenarios') == 400 and
                  probe.get('real_fi_runs_executed') == 0 and
                  probe.get('vivado_builds_executed') == 0 and
                  probe.get('xsim_invocations_executed') == 0 and
                  probe.get('scientific_outcomes_committed') == 0 and
                  probe.get('g6_production_qualified') is False and
                  probe.get('g7_scientific_approved') is False and
                  probe.get('full400_authorized') is False and
                  probe.get('release_gate') == p.STOP,
                  'Unsafe mock probe metadata or source provenance')
    events = probe.get('job_object_cases')
    p.fail_unless(type(events) is list and len(events) == 3,
                  'Three fixed mock cases required')
    for event, kind in zip(events, ('healthy', 'timeout', 'tree'), strict=True):
        check_event(event, kind, child_sha)
    p.fail_unless(len({r['python_pid'] for r in events}) == 3,
                  'Duplicate process identifiers between fixed mock cases')
    p.fail_unless((evidence / p.ACKS[0]).read_bytes() == b'V4K_PARENT_AND_CHILD_READY\n' and
                  (evidence / p.ACKS[1]).read_bytes() == b'V4K_GRANDCHILD_ALIVE\n',
                  'Mock descendant handshake files not exact')
    p.fail_unless(receipt.get('schema') == p.RECEIPT_SCHEMA and
                  receipt.get('probe_sha256') == p.sha_file(evidence / p.PROBE_NAME) and
                  receipt.get('case_count') == 3 and
                  receipt.get('mock_source_sha256') == child_sha and
                  receipt.get('fi_outcomes_committed') == 0 and
                  receipt.get('new_xsim_runs') == 0 and
                  receipt.get('full400_authorized') is False and
                  receipt.get('release_gate') == p.STOP,
                  'Mock receipt hash link or blocked flags invalid')
    return {
        'schema': AUDIT_SCHEMA,
        'scope': 'READ_ONLY_OS_RECEIPT_REPLAY_NOT_LIVE_JOB_OBSERVATION',
        'mock_probe_sha256': p.sha_file(evidence / p.PROBE_NAME),
        'mock_receipt_sha256': p.sha_file(evidence / p.RECEIPT_NAME),
        'child_source_sha256': child_sha,
        'fixed_mock_cases_replayed': 3,
        'mock_descendant_handshake_replayed': True,
        'build_once_executed': False,
        'new_xsim_runs': 0,
        'fi_outcomes_committed': 0,
        'g6_production_qualified': False,
        'full400_authorized': False,
        'release_gate': p.STOP,
    }


def cli() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--mode', choices=('audit', 'approve', 'execute', 'full400'), default='audit')
    cli.add_argument('--repo', type=Path, default=Path.cwd())
    cli.add_argument('--probe-dir', type=Path, default=Path('fi/results/v4k_mockjob_01'))
    cli.add_argument('--out-dir', type=Path, default=Path('fi/results/v4k_mockjob_audit_01'))
    cli.add_argument('--approve', action='store_true')
    args = cli.parse_args()
    try:
        p.fail_unless(args.mode == 'audit' and not args.approve,
                      'No release authority in mock auditor')
        repo = args.repo.resolve()
        source = args.probe_dir if args.probe_dir.is_absolute() else repo / args.probe_dir
        out, stage = p.new_output(repo, args.out_dir)
        result = audit(repo, source, Path(p.__file__).with_name(p.CHILD_NAME))
        # All read-only checks complete before any output is created.
        stage.mkdir()
        try:
            p.write_new(stage / AUDIT_NAME, result)
            p.fail_unless(not out.exists(), 'Output changed during read-only audit')
            stage.rename(out)
        except BaseException:
            # Preserve evidence of partial audit.
            raise
        print('V4K_WINDOWS_JOB_MOCK_AUDIT_PASS: 3/3 mock lifecycle receipts; '
              '0 build; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED', flush=True)
        print('V4K_AUDIT_OUTPUT: ' + str(out), flush=True)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise SystemExit('V4K_MOCK_AUDIT_BLOCKED: ' + str(exc)) from exc


if __name__ == '__main__':
    cli()

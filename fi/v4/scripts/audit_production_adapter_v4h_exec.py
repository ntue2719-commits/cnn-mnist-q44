#!/usr/bin/env python3
"""Independent offline audit of V4H-EXEC contract candidate; never executes XSim.

Reconstructs checks from V4f4G raw invocation JSON, rather than invoking the
V4H candidate generator. The source/plan oracle is V4f4F/G exact frozen replay.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

SCHEMA = 'CNN_PEP_V4H_EXEC_ADAPTER_CONTRACT_CANDIDATE_V1'
RECEIPT_SCHEMA = 'CNN_PEP_V4H_EXEC_ADAPTER_RECEIPT_CANDIDATE_V1'
AUDIT_SCHEMA = 'CNN_PEP_V4H_EXEC_INDEPENDENT_OFFLINE_AUDIT_V1'
FILE = 'v4h_exec_adapter_400.json'
RECEIPT = 'v4h_exec_receipt.json'
STOP = 'FULL400_BLOCKED_G6_EXECUTOR_NOT_QUALIFIED_G7_NOT_APPROVED'
REQUIRED_STAGES = ['RELEASE_GUARD','PINNED_BUILD_ONCE_SNAPSHOT',
                   'ISOLATED_WINDOWS_JOB_OBJECT','SERIAL_XSIM_INVOCATION',
                   'STRICT_E0_E1_E4_RESULT_REPLAY','ATOMIC_RAW_EVIDENCE_COMMIT',
                   'IMMUTABLE_RETRY_CHAIN','INDEPENDENT_READONLY_AUDIT']
OUTPUT_KEYS = {'command','raw_xsim_log','console','wrapper','process_receipt',
               'job_launch','child_lifecycle','strict_validation'}


def check(value: bool, msg: str) -> None:
    if not value:
        raise ValueError(msg)


def canon(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(p: Path) -> str:
    return digest(p.read_bytes())


def strict_object(p: Path, *, canonical: bool = True) -> dict[str, Any]:
    check(p.is_file() and not p.is_symlink(), 'Missing or symlinked evidence file')
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        d = {}
        for k, v in pairs:
            check(k not in d, 'Duplicate JSON key')
            d[k] = v
        return d
    raw = p.read_bytes()
    x = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                   parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Illegal JSON')))
    check(type(x) is dict, 'JSON root must be object')
    if canonical:
        check(raw == canon(x), 'Noncanonical JSON bytes (e.g., CRLF)')
    return x


def symlink_free(p: Path) -> None:
    for part in (p, *p.parents):
        check(not part.is_symlink(), 'Symlink evidence path denied')


def resolve_in_results(repo: Path, arg: Path, prefix: str, exists: bool) -> Path:
    path = arg if arg.is_absolute() else repo / arg
    symlink_free(path.absolute())
    path = path.resolve()
    check(path.parent == repo / 'fi/results' and
          re.fullmatch(prefix + r'[A-Za-z0-9_]+', path.name) is not None,
          'Evidence outside allowed fi/results namespace')
    check((path.is_dir() if exists else not path.exists()),
          'Missing input or output path already exists')
    return path


def verify_semantics(m: dict[str, Any], r: dict[str, Any], bp: dict[str, Any],
                     *, plan_hash: str, schedule_hash: str, blueprint_hash: str,
                     blueprint_receipt_hash: str, generator_hash: str,
                     blueprint_audit_hash: str | None,
                     adapter_file_hash: str) -> int:
    """Pure, non-mutating independent verification of every adapter record."""
    check(m.get('schema') == SCHEMA and
          m.get('scope') == 'OFFLINE_ENGINEERING_CANDIDATE_NO_BUILD_NO_LAUNCH_NO_FI' and
          m.get('source_plan_sha256') == plan_hash and
          m.get('source_schedule_sha256') == schedule_hash and
          m.get('source_blueprint_sha256') == blueprint_hash and
          m.get('source_blueprint_receipt_sha256') == blueprint_receipt_hash and
          m.get('source_blueprint_independent_audit_sha256') == blueprint_audit_hash and
          m.get('source_input_fingerprint') == bp.get('source_input_fingerprint') and
          m.get('adapter_generator_sha256') == generator_hash,
          'Adapter provenance/source binding mismatch')
    check(m.get('frozen_scenarios') == 400 and m.get('batch_count') == 16 and
          m.get('batch_size') == 25 and m.get('serial_max_xsim') == 1 and
          m.get('required_future_stages') == REQUIRED_STAGES and
          m.get('attempt_budget_8_of_8_exhausted') is True and
          m.get('source_preflight_receipt_eligible_to_authorize') is False and
          m.get('source_snapshot_built') is False and
          m.get('windows_job_object_exercised_here') is False and
          m.get('raw_evidence_committed') == 0 and
          m.get('new_xsim_runs') == 0 and m.get('real_fi_results') == 0 and
          m.get('G6_production_qualified') is False and
          m.get('G7_scientific_approved') is False and
          m.get('G0_G7_signed_authorization') is False and
          m.get('full400_authorized') is False and
          m.get('full400_executed') is False and m.get('release_gate') == STOP,
          'Unsafe production release or fabricated XSim/Job Object claim')
    check(type(bp.get('invocations')) is list and len(bp['invocations']) == 400 and
          type(m.get('invocations')) is list and len(m['invocations']) == 400,
          '400-record contract missing')
    seen = set()
    for i, (source, got) in enumerate(zip(bp['invocations'], m['invocations']), 1):
        sid = f'FI_P400_{i:04d}'
        check(source.get('scenario_id') == sid and
              source.get('ordinal') == i and
              source.get('batch_number') == ((i - 1) // 25 + 1),
              'Unexpected source scenario order')
        outputs = source.get('future_outputs')
        check(type(outputs) is dict and set(outputs) == OUTPUT_KEYS,
              'Future outputs changed')
        namespace = f'runs/batch_{(i - 1) // 25 + 1:02d}/{sid}'
        for rel in outputs.values():
            check(type(rel) is str and rel.startswith(namespace + '/') and
                  rel not in seen and '\\' not in rel and
                  not PurePosixPath(rel).is_absolute() and
                  '..' not in PurePosixPath(rel).parts and
                  len(PurePosixPath(rel).parts) == 4,
                  'Duplicate, absolute or traversing output path')
            seen.add(rel)
        plus = source.get('plusargs_exact')
        check(type(plus) is dict and len(plus) == 10 and
              plus.get('SCENARIO_ID') == sid and
              source.get('job_object_required') is True and
              source.get('production_fi_executed') is False and
              source.get('scientific_outcome_available') is False,
              'Unauthorized source FI contract')
        expected = {
            'ordinal': i,
            'scenario_id': sid,
            'batch_number': (i - 1) // 25 + 1,
            'frozen_tuple_sha256': source['frozen_tuple_sha256'],
            'xsim_plusargs_data_sha256': digest(canon(plus)),
            'future_run_directory': namespace,
            'future_artifact_contract_sha256': digest(canon(outputs)),
            'ready_to_execute': False,
            'source_snapshot_built': False,
            'windows_job_object_started': False,
            'raw_xsim_log_observed': False,
            'checkpoint_committed': False,
            'design_outcome_observed': False,
            'attempt_state': 'UNCREATED_CONTRACT_ONLY',
        }
        check(type(got) is dict and got == expected,
              'Mapped adapter record drift/forgery: ' + sid)
    check(len(seen) == 3200, 'Expected 3200 unique artifact names')
    check(type(r) is dict and set(r) == {
        'schema','created_utc','adapter_plan_sha256','adapter_generator_sha256',
        'scope','new_xsim_runs','source_snapshot_built','G6_production_qualified',
        'G7_scientific_approved','full400_authorized','release_gate'},
        'Adapter receipt has unexpected fields')
    check(r['schema'] == RECEIPT_SCHEMA and
          type(r['created_utc']) is str and r['created_utc'].endswith('+00:00') and
          r['adapter_plan_sha256'] == adapter_file_hash and
          r['adapter_generator_sha256'] == generator_hash and
          r['scope'] == m['scope'] and
          r['new_xsim_runs'] == 0 and r['source_snapshot_built'] is False and
          r['G6_production_qualified'] is False and
          r['G7_scientific_approved'] is False and
          r['full400_authorized'] is False and r['release_gate'] == STOP,
          'Adapter receipt forged or inconsistent')
    return len(seen)


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    check(args.mode == 'audit' and not args.execute and not args.approve,
          'V4H-EXEC AUDIT DENIED BEFORE IO: no execute/full400/approval')
    symlink_free(args.repo.absolute())
    repo = args.repo.resolve()
    results = repo / 'fi/results'
    check(results.is_dir() and not results.is_symlink(), 'Missing protected results directory')
    plan = resolve_in_results(repo, args.plan_dir, 'v4f4f_', True)
    blueprint = resolve_in_results(repo, args.blueprint_dir, 'v4f4g_', True)
    adapter = resolve_in_results(repo, args.adapter_dir, 'v4h_exec_', True)
    output = resolve_in_results(repo, args.out_dir, 'v4h_exec_', False)
    audit = None
    if args.blueprint_audit_dir is not None:
        audit = resolve_in_results(repo, args.blueprint_audit_dir, 'v4f4g_', True)
    check({p.name for p in adapter.iterdir()} == {FILE, RECEIPT} and
          all(p.is_file() and not p.is_symlink() for p in adapter.iterdir()),
          'Orphan/modified adapter directory files')
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path: sys.path.insert(0, str(here))
    import production_runner_v4f4f as f4f
    import production_blueprint_v4f4g as f4g
    rows = f4f.frozen_schedule(repo)
    pins = f4f.pinned_inputs(repo, repo / 'data')
    f4f.check_pins(pins)
    f4g.verify_blueprint(repo, results, blueprint, plan, rows, pins)
    bp = strict_object(blueprint / f4g.SOURCE_NAME)
    audit_sha = None
    if audit is not None:
        x = strict_object(audit / f4g.AUDIT_NAME, canonical=False)
        check(x.get('schema') == f4g.RECEIPT_SCHEMA and
              x.get('records_verified') == 400 and
              x.get('plan_sha256') == hash_file(plan / f4f.LEDGER_NAME) and
              x.get('blueprint_sha256') == hash_file(blueprint / f4g.SOURCE_NAME) and
              x.get('receipt_sha256') == hash_file(blueprint / f4g.RECEIPT_NAME) and
              x.get('new_xsim_runs') == 0 and
              x.get('full400_authorized') is False,
              'Parent blueprint audit receipt invalid')
        audit_sha = hash_file(audit / f4g.AUDIT_NAME)
    m = strict_object(adapter / FILE)
    r = strict_object(adapter / RECEIPT)
    gen_hash = hash_file(here / 'production_adapter_v4h_exec.py')
    n_paths = verify_semantics(m, r, bp,
        plan_hash=hash_file(plan / f4f.LEDGER_NAME),
        schedule_hash=hash_file(plan / 'schedule_400.csv'),
        blueprint_hash=hash_file(blueprint / f4g.SOURCE_NAME),
        blueprint_receipt_hash=hash_file(blueprint / f4g.RECEIPT_NAME),
        generator_hash=gen_hash,
        blueprint_audit_hash=audit_sha,
        adapter_file_hash=hash_file(adapter / FILE))
    report = {
        'schema': AUDIT_SCHEMA,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'adapter_sha256': hash_file(adapter / FILE),
        'adapter_receipt_sha256': hash_file(adapter / RECEIPT),
        'source_blueprint_sha256': hash_file(blueprint / f4g.SOURCE_NAME),
        'mapped_contracts_verified': 400,
        'future_artifact_paths_verified': n_paths,
        'new_xsim_runs': 0,
        'build_once_executed': False,
        'G6_production_qualified': False,
        'full400_authorized': False,
        'release_gate': STOP,
    }
    output.mkdir(parents=False, exist_ok=False)
    report_path = output / 'v4h_exec_independent_audit.json'
    tmp = report_path.with_name(report_path.name + '.tmp')
    with tmp.open('xb') as f:
        f.write(canon(report));f.flush();os.fsync(f.fileno())
    tmp.replace(report_path)
    print('V4H_EXEC_INDEPENDENT_AUDIT_PASS: 400/400 adapter contracts; '
          '3200 paths; 0 build; 0 XSim; FULL400 BLOCKED', flush=True)
    print('V4H_EXEC_AUDIT_OUTPUT: ' + str(output), flush=True)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('audit','build','execute','full400'), default='audit')
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--plan-dir', type=Path, default=Path('fi/results/v4f4f_plan_01'))
    p.add_argument('--blueprint-dir', type=Path, default=Path('fi/results/v4f4g_blueprint_winfix_01'))
    p.add_argument('--blueprint-audit-dir', type=Path,
                   default=Path('fi/results/v4f4g_blueprint_audit_winfix_01'))
    p.add_argument('--adapter-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_plan_01'))
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_audit_01'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')
    args = p.parse_args()
    try:
        run_audit(args)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        raise SystemExit('V4H_EXEC_AUDIT_BLOCKED: ' + str(exc)) from exc


if __name__ == '__main__':
    main()

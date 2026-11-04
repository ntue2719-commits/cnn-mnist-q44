#!/usr/bin/env python3
"""V4H-EXEC: OFFLINE, permanently locked build-once adapter contract candidate.

No Vivado invocation, no subprocess import, no build, no launch, no FI.  This is
an engineering candidate; it does not qualify G6 or authorize Full400.
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
PLAN_NAME = 'v4h_exec_adapter_400.json'
RECEIPT_NAME = 'v4h_exec_receipt.json'
STOP = 'FULL400_BLOCKED_G6_EXECUTOR_NOT_QUALIFIED_G7_NOT_APPROVED'
PLUSARG_KEYS = ('FI_OFFSET','SCENARIO_ID','TARGET_ID','FI_BIT','SAMPLE_ID',
                'INPUT_FILE','POOL1_FILE','POOL2_FILE','LOGITS_FILE','PREDICTION_FILE')
OUTPUT_KEYS = ('command','raw_xsim_log','console','wrapper','process_receipt',
               'job_launch','child_lifecycle','strict_validation')
FUTURE_STEPS = (
    'RELEASE_GUARD', 'PINNED_BUILD_ONCE_SNAPSHOT', 'ISOLATED_WINDOWS_JOB_OBJECT',
    'SERIAL_XSIM_INVOCATION', 'STRICT_E0_E1_E4_RESULT_REPLAY',
    'ATOMIC_RAW_EVIDENCE_COMMIT', 'IMMUTABLE_RETRY_CHAIN',
    'INDEPENDENT_READONLY_AUDIT',
)


def ensure(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def canonical(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for k, v in pairs:
        ensure(k not in result, 'Duplicate JSON key: ' + k)
        result[k] = v
    return result


def strict_json(path: Path, *, canonical_required: bool = False) -> dict[str, Any]:
    ensure(path.is_file() and not path.is_symlink(), 'Missing/symlinked JSON: ' + str(path))
    raw = path.read_bytes()
    obj = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs,
                     parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Invalid JSON number')))
    ensure(type(obj) is dict, 'JSON root must be an object')
    if canonical_required:
        ensure(raw == canonical(obj), 'JSON is not canonical UTF-8/LF bytes')
    return obj


def write_new(path: Path, data: bytes) -> None:
    """Exclusive, fsync, atomic rename; existing and orphan .tmp are refusals."""
    temp = path.with_name(path.name + '.tmp')
    ensure(not path.exists() and not path.is_symlink() and
           not temp.exists() and not temp.is_symlink(),
           'Existing or interrupted output file: ' + str(path))
    with temp.open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    ensure(not path.exists() and not path.is_symlink(), 'Output appeared during write')
    temp.replace(path)


def no_symlink_components(path: Path) -> None:
    for p in (path, *path.parents):
        ensure(not p.is_symlink(), 'Symlink in evidence path: ' + str(p))


def repository_paths(repo_arg: Path, plan_arg: Path, blueprint_arg: Path,
                     out_arg: Path) -> tuple[Path, Path, Path, Path, Path]:
    no_symlink_components(repo_arg.absolute())
    repo = repo_arg.resolve()
    results = repo / 'fi/results'
    ensure(results.is_dir() and not results.is_symlink(), 'Missing protected fi/results')
    def inside(arg: Path, name_prefix: str, *, exists: bool) -> Path:
        candidate = arg if arg.is_absolute() else repo / arg
        no_symlink_components(candidate.absolute())
        p = candidate.resolve()
        ensure(p.parent == results and re.fullmatch(name_prefix + r'[A-Za-z0-9_]+', p.name),
               'Evidence must be a direct fi/results sibling in expected namespace')
        ensure(p.is_dir() if exists else not p.exists(),
               'Evidence directory missing or already exists: ' + str(p))
        return p
    plan = inside(plan_arg, 'v4f4f_', exists=True)
    blueprint = inside(blueprint_arg, 'v4f4g_', exists=True)
    out = inside(out_arg, 'v4h_exec_', exists=False)
    ensure(plan != blueprint != out, 'Evidence directories cannot overlap')
    return repo, results, plan, blueprint, out


def validate_record(row: dict[str, Any], i: int, used_paths: set[str]) -> None:
    sid = f'FI_P400_{i:04d}'
    ensure(type(row) is dict and row.get('scenario_id') == sid and
           row.get('ordinal') == i and type(row.get('ordinal')) is int and
           type(row.get('batch_number')) is int and row['batch_number'] == ((i - 1) // 25 + 1) and
           type(row.get('batch_position')) is int and row['batch_position'] == ((i - 1) % 25 + 1),
           'Non-canonical scenario/batch order at ' + sid)
    ensure(row.get('plusargs_order') == list(PLUSARG_KEYS) and
           type(row.get('plusargs_exact')) is dict and
           set(row['plusargs_exact']) == set(PLUSARG_KEYS) and
           all(type(v) is str and v for v in row['plusargs_exact'].values()),
           'Exactly ten nonempty plusargs required: ' + sid)
    a = row['plusargs_exact']
    ensure(a['SCENARIO_ID'] == sid and
           re.fullmatch(r'(CTL|BUF|MEM|DAT)_\d{4}', a['TARGET_ID']) is not None and
           re.fullmatch(r'IMG\d{4}', a['SAMPLE_ID']) is not None and
           re.fullmatch(r'\d+', a['FI_BIT']) is not None and
           re.fullmatch(r'\d+', a['FI_OFFSET']) is not None,
           'Untrusted tuple/plusargs: ' + sid)
    ensure(re.fullmatch(r'[a-f0-9]{64}', str(row.get('frozen_tuple_sha256'))) is not None,
           'Invalid frozen tuple hash')
    ensure(row.get('future_snapshot_reference') == 'BUILD_ONCE_SNAPSHOT_REQUIRED_NOT_PRESENT' and
           row.get('job_object_required') is True and
           row.get('per_scenario_snapshot_rebuild_forbidden') is True and
           row.get('production_fi_executed') is False and
           row.get('scientific_outcome_available') is False,
           'Unsafe or fabricated execution claim')
    namespace = f'runs/batch_{(i-1)//25+1:02d}/{sid}'
    ensure(row.get('future_run_directory') == namespace and
           type(row.get('future_outputs')) is dict and
           set(row['future_outputs']) == set(OUTPUT_KEYS),
           'Untrusted future namespace/outputs at ' + sid)
    for key, rel in row['future_outputs'].items():
        ensure(type(rel) is str and rel.startswith(namespace + '/') and
               '\\' not in rel and not PurePosixPath(rel).is_absolute() and
               '..' not in PurePosixPath(rel).parts and rel not in used_paths and
               len(PurePosixPath(rel).parts) == 4,
               'Path traversal, duplicate, or unsafe future output')
        used_paths.add(rel)


def make_candidate(blueprint: dict[str, Any], plan_hash: str,
                   schedule_hash: str, blueprint_hash: str,
                   blueprint_receipt_hash: str, generator_hash: str,
                   audit_hash: str | None = None) -> dict[str, Any]:
    """Pure contract mapping; caller MUST verify upstream frozen pins first."""
    ensure(type(blueprint) is dict and
           blueprint.get('schema') == 'CNN_PEP_V4F4G_OFFLINE_XSIM_INVOCATION_BLUEPRINT_V1' and
           blueprint.get('scope') == 'INVOCATION_CONTRACT_ONLY_NO_EXECUTABLE_COMMANDS_NO_XSIM' and
           blueprint.get('frozen_scenario_count') == 400 and
           blueprint.get('batch_count') == 16 and blueprint.get('batch_size') == 25 and
           blueprint.get('serial_max_active_xsim') == 1 and
           blueprint.get('build_once_required') is True and
           blueprint.get('build_once_executed') is False and
           blueprint.get('private_windows_job_object_per_run') is True and
           blueprint.get('actual_fi_runs') == 0 and
           blueprint.get('human_approved_scientific_protocol') is False and
           blueprint.get('g6_production_qualified') is False and
           blueprint.get('full400_authorized') is False and
           blueprint.get('full400_executed') is False and
           blueprint.get('infrastructure_error_is_design_noncompletion') is False and
           blueprint.get('strict_fi_parser_required_for_commit') is True and
           blueprint.get('immutable_run_namespace_required') is True and
           blueprint.get('fail_closed_on_orphan_timeout_or_source_drift') is True and
           blueprint.get('parent_v4f4f_plan_sha256') == plan_hash and
           blueprint.get('parent_v4f4f_schedule_sha256') == schedule_hash,
           'Blueprint is not locked, exact, or linked to parent plan')
    for h in (plan_hash, schedule_hash, blueprint_hash, blueprint_receipt_hash, generator_hash):
        ensure(type(h) is str and re.fullmatch('[0-9a-f]{64}', h) is not None,
               'Missing/invalid SHA256 reference')
    if audit_hash is not None:
        ensure(type(audit_hash) is str and re.fullmatch('[0-9a-f]{64}', audit_hash) is not None,
               'Invalid blueprint audit SHA256')
    src = blueprint.get('invocations')
    ensure(type(src) is list and len(src) == 400, 'Expected 400 canonical invocations')
    used: set[str] = set()
    mapped = []
    for i, row in enumerate(src, 1):
        validate_record(row, i, used)
        mapped.append({
            'ordinal': i,
            'scenario_id': row['scenario_id'],
            'batch_number': row['batch_number'],
            'frozen_tuple_sha256': row['frozen_tuple_sha256'],
            'xsim_plusargs_data_sha256': sha_bytes(canonical(row['plusargs_exact'])),
            'future_run_directory': row['future_run_directory'],
            'future_artifact_contract_sha256': sha_bytes(canonical(row['future_outputs'])),
            'ready_to_execute': False,
            'source_snapshot_built': False,
            'windows_job_object_started': False,
            'raw_xsim_log_observed': False,
            'checkpoint_committed': False,
            'design_outcome_observed': False,
            'attempt_state': 'UNCREATED_CONTRACT_ONLY',
        })
    ensure(len(used) == 3200, 'Expected 3,200 unique future artifact paths')
    return {
        'schema': SCHEMA,
        'scope': 'OFFLINE_ENGINEERING_CANDIDATE_NO_BUILD_NO_LAUNCH_NO_FI',
        'source_blueprint_sha256': blueprint_hash,
        'source_blueprint_receipt_sha256': blueprint_receipt_hash,
        'source_blueprint_independent_audit_sha256': audit_hash,
        'source_plan_sha256': plan_hash,
        'source_schedule_sha256': schedule_hash,
        'source_input_fingerprint': blueprint['source_input_fingerprint'],
        'adapter_generator_sha256': generator_hash,
        'frozen_scenarios': 400,
        'batch_count': 16,
        'batch_size': 25,
        'serial_max_xsim': 1,
        'required_future_stages': list(FUTURE_STEPS),
        'source_preflight_receipt_eligible_to_authorize': False,
        'source_snapshot_built': False,
        'windows_job_object_exercised_here': False,
        'raw_evidence_committed': 0,
        'new_xsim_runs': 0,
        'real_fi_results': 0,
        'attempt_budget_8_of_8_exhausted': True,
        'G6_production_qualified': False,
        'G7_scientific_approved': False,
        'G0_G7_signed_authorization': False,
        'full400_authorized': False,
        'full400_executed': False,
        'release_gate': STOP,
        'invocations': mapped,
    }


def verify_upstream(repo: Path, results: Path, plan: Path, blueprint_dir: Path,
                    blueprint_audit_dir: Path | None) -> tuple[dict[str, Any], str | None]:
    """Use the actual authoritative V4f4F/G source oracle on Windows."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import production_runner_v4f4f as f4f
    import production_blueprint_v4f4g as f4g
    rows = f4f.frozen_schedule(repo)
    pins = f4f.pinned_inputs(repo, repo / 'data')
    f4f.check_pins(pins)
    f4g.verify_blueprint(repo, results, blueprint_dir, plan, rows, pins)
    bp = strict_json(blueprint_dir / f4g.SOURCE_NAME, canonical_required=True)
    audit_sha = None
    if blueprint_audit_dir is not None:
        ensure(blueprint_audit_dir.is_dir() and
               blueprint_audit_dir.parent == results and
               re.fullmatch(r'v4f4g_[A-Za-z0-9_]+', blueprint_audit_dir.name) is not None and
               not blueprint_audit_dir.is_symlink(),
               'Invalid prior V4f4G audit directory')
        report_path = blueprint_audit_dir / f4g.AUDIT_NAME
        audit = strict_json(report_path)
        ensure(audit.get('schema') == f4g.RECEIPT_SCHEMA and
               audit.get('records_verified') == 400 and
               audit.get('batches_verified') == 16 and
               audit.get('exact_plusargs_verified_per_record') == 10 and
               audit.get('plan_sha256') == sha_file(plan / f4f.LEDGER_NAME) and
               audit.get('blueprint_sha256') == sha_file(blueprint_dir / f4g.SOURCE_NAME) and
               audit.get('receipt_sha256') == sha_file(blueprint_dir / f4g.RECEIPT_NAME) and
               audit.get('new_xsim_runs') == 0 and
               audit.get('full400_authorized') is False and
               audit.get('G6_production') == 'NOT_QUALIFIED',
               'V4f4G audit receipt not linked or unsafe')
        audit_sha = sha_file(report_path)
    return bp, audit_sha


def check_args_before_io(mode: str, execute: bool, approve: bool) -> None:
    ensure(mode == 'plan' and not execute and not approve,
           'V4H-EXEC DENIED BEFORE IO: no build, launch, execute or approval mode')


def run_plan(args: argparse.Namespace) -> dict[str, Any]:
    check_args_before_io(args.mode, args.execute, args.approve)
    repo, results, plan, blueprint, out = repository_paths(
        args.repo, args.plan_dir, args.blueprint_dir, args.out_dir)
    audit_dir = None
    if args.blueprint_audit_dir is not None:
        raw = args.blueprint_audit_dir
        no_symlink_components((raw if raw.is_absolute() else repo / raw).absolute())
        audit_dir = (raw if raw.is_absolute() else repo / raw).resolve()
    bp, audit_hash = verify_upstream(repo, results, plan, blueprint, audit_dir)
    gen_hash = sha_file(Path(__file__).resolve())
    manifest = make_candidate(bp, sha_file(plan / 'v4f4f_production_contract.json'),
                              sha_file(plan / 'schedule_400.csv'),
                              sha_file(blueprint / 'invocation_blueprint_400.json'),
                              sha_file(blueprint / 'blueprint_receipt.json'),
                              gen_hash, audit_hash)
    out.mkdir(parents=False, exist_ok=False)
    write_new(out / PLAN_NAME, canonical(manifest))
    receipt = {
        'schema': RECEIPT_SCHEMA,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'adapter_plan_sha256': sha_file(out / PLAN_NAME),
        'adapter_generator_sha256': gen_hash,
        'scope': manifest['scope'],
        'new_xsim_runs': 0,
        'source_snapshot_built': False,
        'G6_production_qualified': False,
        'G7_scientific_approved': False,
        'full400_authorized': False,
        'release_gate': STOP,
    }
    write_new(out / RECEIPT_NAME, canonical(receipt))
    print('V4H_EXEC_CONTRACT_PLAN_PASS: 400/400 mapped; 3200 artifact paths; '
          '0 build; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED', flush=True)
    print('V4H_EXEC_OUTPUT: ' + str(out), flush=True)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('plan','build','execute','full400'), default='plan')
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--plan-dir', type=Path, default=Path('fi/results/v4f4f_plan_01'))
    p.add_argument('--blueprint-dir', type=Path, default=Path('fi/results/v4f4g_blueprint_winfix_01'))
    p.add_argument('--blueprint-audit-dir', type=Path,
                   default=Path('fi/results/v4f4g_blueprint_audit_winfix_01'))
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_plan_01'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')
    args = p.parse_args()
    try:
        run_plan(args)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        raise SystemExit('V4H_EXEC_BLOCKED: ' + str(exc)) from exc


if __name__ == '__main__':
    main()

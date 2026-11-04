#!/usr/bin/env python3
"""CNN_PEP V4f4G - OFFLINE 400-scenario XSim invocation blueprint.

This source does NOT build, launch, qualify, or authorize a simulator. It
records exact scenario-specific argument contracts for a *future* separately
qualified G6 executor, and independently replays these bytes via --mode audit.

G6 production: NOT QUALIFIED. G7: NOT APPROVED. Full400: BLOCKED.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
for d in (HERE, HERE.parents[1] / 'scripts', HERE.parents[1] / 'v3b/scripts'):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from audit_v4 import require  # noqa: E402
from pilot400_common import sha256  # noqa: E402
import production_runner_v4f4f as v4f4f  # noqa: E402

SCHEMA = 'CNN_PEP_V4F4G_OFFLINE_XSIM_INVOCATION_BLUEPRINT_V1'
RECEIPT_SCHEMA = 'CNN_PEP_V4F4G_INDEPENDENT_BLUEPRINT_AUDIT_V1'
SOURCE_NAME = 'invocation_blueprint_400.json'
RECEIPT_NAME = 'blueprint_receipt.json'
AUDIT_NAME = 'v4f4g_blueprint_audit.json'
STOP = 'FULL400_BLOCKED_G6_PRODUCTION_NOT_QUALIFIED_G7_NOT_APPROVED'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_canonical_json(path: Path, obj: dict[str, Any]) -> None:
    """Write canonical UTF-8 JSON using bytes, unaffected by Windows CRLF.

    Keep this V4f4G-specific writer separate from the historical V4f4F
    writer. A failed/partial write leaves an orphan .tmp and must not be
    silently resumed or overwritten.
    """
    tmp = path.with_name(path.name + '.tmp')
    require(not path.exists() and not path.is_symlink() and
            not tmp.exists() and not tmp.is_symlink(),
            'V4f4G output or unfinished temporary file already exists')
    with tmp.open('xb') as handle:
        handle.write(v4f4f.encoded(obj))
        handle.flush()
        os.fsync(handle.fileno())
    require(not path.exists() and not path.is_symlink(),
            'V4f4G output appeared during atomic write')
    tmp.replace(path)


def valid_name(path: Path, prefix: str) -> None:
    require(re.fullmatch(prefix + r'[A-Za-z0-9_]+', path.name) is not None,
            'Unexpected output/parent directory namespace')


def safe_paths(repo: Path, results: Path, out: Path, plan: Path | None) -> None:
    require(results == repo / 'fi/results' and results.is_dir() and
            not results.is_symlink() and out.parent == results and
            not out.exists() and not out.is_symlink(),
            'New Phase 4G output must be an absent fi/results sibling')
    valid_name(out, 'v4f4g_')
    if plan is not None:
        require(plan.parent == results and plan.is_dir() and
                not plan.is_symlink() and plan != out,
                'Production plan must be existing fi/results sibling')
        valid_name(plan, 'v4f4f_')


def parent_plan(plan: Path, repo: Path, rows: list[dict[str, str]],
                pins: dict[str, str]) -> dict[str, Any]:
    meta = v4f4f.strict_json(plan / v4f4f.LEDGER_NAME)
    v4f4f.verify_ledger(plan, rows, pins, meta, complete=False)
    require(meta['status'] == v4f4f.STATUS_PLAN and
            meta['synthetic_events_committed'] == 0 and
            meta['records'] == [] and meta['xsim_runs_committed'] == 0 and
            meta['full400_authorized'] is False,
            'Phase 4G parent must be a pure, untouched Phase 4F PLAN')
    return meta


def scenario_record(i: int, row: dict[str, str], repo: Path) -> dict[str, Any]:
    sid = row['scenario_id']
    require(re.fullmatch(r'FI_P400_\d{4}', sid) is not None,
            'Unsafe scenario ID')
    a = v4f4f.expected_plusargs(row, repo)
    v4f4f.assert_plusargs(a, row, repo)
    batch = int(row['batch_number'])
    index = int(row['batch_position'])
    require(i == int(sid[-4:]) and 1 <= batch <= 16 and 1 <= index <= 25,
            'Scenario sequence or batch contract changed')
    prefix = f'runs/batch_{batch:02d}/{sid}'
    return {
        'ordinal': i, 'scenario_id': sid,
        'batch_number': batch, 'batch_position': index,
        'frozen_tuple_sha256': v4f4f.digest(v4f4f.encoded({
            key: row[key] for key in v4f4f.FIELDS})),
        'plusargs_order': list(v4f4f.PLUSARG_KEYS),
        'plusargs_exact': a,
        'future_snapshot_reference': 'BUILD_ONCE_SNAPSHOT_REQUIRED_NOT_PRESENT',
        'future_run_directory': prefix,
        'future_outputs': {
            'command': prefix + '/invocation.cmd',
            'raw_xsim_log': prefix + '/xsim.log',
            'console': prefix + '/console.log',
            'wrapper': prefix + '/wrapper.log',
            'process_receipt': prefix + '/process_receipt.json',
            'job_launch': prefix + '/job_launch.json',
            'child_lifecycle': prefix + '/child_lifecycle.json',
            'strict_validation': prefix + '/strict_validation.json',
        },
        'job_object_required': True,
        'per_scenario_snapshot_rebuild_forbidden': True,
        'production_fi_executed': False,
        'scientific_outcome_available': False,
    }


def blueprint_contents(rows: list[dict[str, str]], repo: Path,
                       plan: Path, pins: dict[str, str]) -> dict[str, Any]:
    require(len(rows) == 400, 'Exactly 400 frozen rows required')
    return {
        'schema': SCHEMA,
        'scope': 'INVOCATION_CONTRACT_ONLY_NO_EXECUTABLE_COMMANDS_NO_XSIM',
        'parent_v4f4f_plan_sha256': sha256(plan / v4f4f.LEDGER_NAME),
        'parent_v4f4f_schedule_sha256': sha256(plan / 'schedule_400.csv'),
        'source_input_fingerprint': v4f4f.source_fingerprint(pins),
        'blueprint_generator_sha256': sha256(Path(__file__).resolve()),
        'frozen_scenario_count': 400,
        'batch_count': 16,
        'batch_size': 25,
        'build_once_required': True,
        'build_once_executed': False,
        'serial_max_active_xsim': 1,
        'private_windows_job_object_per_run': True,
        'immutable_run_namespace_required': True,
        'fail_closed_on_orphan_timeout_or_source_drift': True,
        'infrastructure_error_is_design_noncompletion': False,
        'strict_fi_parser_required_for_commit': True,
        'actual_fi_runs': 0,
        'human_approved_scientific_protocol': False,
        'g6_production_qualified': False,
        'full400_authorized': False,
        'full400_executed': False,
        'full400_gate': STOP,
        'invocations': [scenario_record(i, row, repo)
                        for i, row in enumerate(rows, 1)],
    }


def receipt_contents(plan: Path, output: Path,
                     pin_fingerprint: str) -> dict[str, Any]:
    return {
        'schema': SCHEMA,
        'created_utc': utc_now(),
        'parent_plan_sha256': sha256(plan / v4f4f.LEDGER_NAME),
        'blueprint_sha256': sha256(output),
        'source_input_fingerprint': pin_fingerprint,
        'blueprint_generator_sha256': sha256(Path(__file__).resolve()),
        'scope': 'OFFLINE_CONTRACT_ONLY',
        'real_xsim_runs': 0,
        'G6_production': 'NOT_QUALIFIED',
        'G7': 'PREPARED_NOT_APPROVED',
        'full400_authorized': False,
        'full400_executed': False,
        'release_gate': STOP,
    }


def verify_blueprint(repo: Path, results: Path, source: Path,
                     plan: Path, rows: list[dict[str, str]],
                     pins: dict[str, str]) -> dict[str, Any]:
    require(source.parent == results and source.is_dir() and
            not source.is_symlink(), 'Blueprint outside protected results')
    valid_name(source, 'v4f4g_')
    expected_files = {SOURCE_NAME, RECEIPT_NAME}
    require({p.name for p in source.iterdir()} == expected_files and
            all(p.is_file() and not p.is_symlink() for p in source.iterdir()),
            'Blueprint missing/orphan files or symlink')
    parent_plan(plan, repo, rows, pins)
    obj = v4f4f.strict_json(source / SOURCE_NAME)
    expected = blueprint_contents(rows, repo, plan, pins)
    require(obj == expected and
            (source / SOURCE_NAME).read_bytes() == v4f4f.encoded(expected),
            '400 invocation records or offline release scope changed')
    receipt = v4f4f.strict_json(source / RECEIPT_NAME)
    require((source / RECEIPT_NAME).read_bytes() == v4f4f.encoded(receipt),
            'Blueprint receipt must be canonical UTF-8 with LF newlines')
    require(set(receipt) == {
        'schema', 'created_utc', 'parent_plan_sha256', 'blueprint_sha256',
        'source_input_fingerprint', 'blueprint_generator_sha256',
        'scope', 'real_xsim_runs',
        'G6_production', 'G7', 'full400_authorized', 'full400_executed',
        'release_gate',
    } and receipt.get('schema') == SCHEMA and
            isinstance(receipt.get('created_utc'), str) and
            receipt.get('parent_plan_sha256') == sha256(plan / v4f4f.LEDGER_NAME) and
            receipt.get('blueprint_sha256') == sha256(source / SOURCE_NAME) and
            receipt.get('source_input_fingerprint') == v4f4f.source_fingerprint(pins) and
            receipt.get('blueprint_generator_sha256') == sha256(Path(__file__).resolve()) and
            receipt.get('scope') == 'OFFLINE_CONTRACT_ONLY' and
            receipt.get('real_xsim_runs') == 0 and
            receipt.get('G6_production') == 'NOT_QUALIFIED' and
            receipt.get('G7') == 'PREPARED_NOT_APPROVED' and
            receipt.get('full400_authorized') is False and
            receipt.get('full400_executed') is False and
            receipt.get('release_gate') == STOP,
            'Blueprint receipt or G6/G7 release claims changed')
    return {
        'schema': RECEIPT_SCHEMA, 'created_utc': utc_now(),
        'plan_sha256': sha256(plan / v4f4f.LEDGER_NAME),
        'blueprint_sha256': sha256(source / SOURCE_NAME),
        'receipt_sha256': sha256(source / RECEIPT_NAME),
        'records_verified': 400, 'batches_verified': 16,
        'exact_plusargs_verified_per_record': 10,
        'new_xsim_runs': 0,
        'G6_production': 'NOT_QUALIFIED', 'G7': 'PREPARED_NOT_APPROVED',
        'full400_authorized': False, 'release_gate': STOP,
    }


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    # Must reject before any filesystem access, even if supplied with --execute.
    require(args.mode in ('blueprint', 'audit') and
            not args.execute and not args.approve,
            'V4F4G EXECUTION DENIED BEFORE IO: no signed production runner')
    repo = args.repo.resolve()
    results = v4f4f.path_from(repo, args.results_dir)
    out = v4f4f.path_from(repo, args.out_dir)
    plan = v4f4f.path_from(repo, args.plan_dir)
    safe_paths(repo, results, out, plan)
    rows = v4f4f.frozen_schedule(repo)
    pins = v4f4f.pinned_inputs(repo, v4f4f.path_from(repo, args.weights_dir))
    v4f4f.check_pins(pins)
    parent_plan(plan, repo, rows, pins)
    if args.mode == 'blueprint':
        contents = blueprint_contents(rows, repo, plan, pins)
        out.mkdir(parents=False, exist_ok=False)
        atomic_canonical_json(out / SOURCE_NAME, contents)
        atomic_canonical_json(out / RECEIPT_NAME,
                           receipt_contents(plan, out / SOURCE_NAME,
                                            v4f4f.source_fingerprint(pins)))
        result = verify_blueprint(repo, results, out, plan, rows, pins)
        print('V4F4G_BLUEPRINT_PASS: 400/400 exact frozen XSim invocation contracts; '
              'no executable commands; 0 XSim; FULL400 BLOCKED', flush=True)
        print('V4F4G_OUTPUT: ' + str(out), flush=True)
        return result
    require(args.blueprint_dir is not None, 'Audit requires --blueprint-dir')
    source = v4f4f.path_from(repo, args.blueprint_dir)
    report = verify_blueprint(repo, results, source, plan, rows, pins)
    out.mkdir(parents=False, exist_ok=False)
    atomic_canonical_json(out / AUDIT_NAME, report)
    print('V4F4G_BLUEPRINT_AUDIT_PASS: 400/400 invocation contracts replayed; '
          '0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED', flush=True)
    print('V4F4G_AUDIT_OUTPUT: ' + str(out), flush=True)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--results-dir', type=Path, default=Path('fi/results'))
    p.add_argument('--weights-dir', type=Path, default=Path('data'))
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--plan-dir', type=Path,
                   default=Path('fi/results/v4f4f_plan_01'))
    p.add_argument('--blueprint-dir', type=Path)
    p.add_argument('--mode', choices=('blueprint', 'audit', 'execute', 'full400'),
                   required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')
    args = p.parse_args()
    try:
        dispatch(args)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError,
            AssertionError) as exc:
        raise SystemExit('V4F4G_FAIL: ' + str(exc)) from exc


if __name__ == '__main__':
    main()

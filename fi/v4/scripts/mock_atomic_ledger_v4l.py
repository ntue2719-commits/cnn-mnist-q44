#!/usr/bin/env python3
"""V4L: offline-only mock transaction journal, no design FI or simulator calls.

This deliberately has no subprocess, Vivado build, XSim or production execution
interface. A MOCK_COMMITTED transaction is not a valid FI outcome.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

SCHEMA = 'CNN_PEP_V4L_MOCK_ATOMIC_JOURNAL_V1'
STATE_SCHEMA = 'CNN_PEP_V4L_MOCK_STATE_EVENT_V1'
RAW_SCHEMA = 'CNN_PEP_V4L_MOCK_RAW_V1'
MANIFEST_NAME = 'v4l_mock_transaction_manifest.json'
RECEIPT_NAME = 'v4l_mock_transaction_receipt.json'
ADAPTER_NAME = 'v4h_exec_adapter_400.json'
ADAPTER_RECEIPT_NAME = 'v4h_exec_receipt.json'
ADAPTER_AUDIT_NAME = 'v4h_exec_independent_audit.json'
DENIAL = 'FULL400_BLOCKED_G6_NOT_QUALIFIED_G7_NOT_APPROVED'
ATTEMPTS = (
    (1, 'FI_P400_0001', 'HEALTHY_MOCK', 'MOCK_COMMITTED'),
    (2, 'FI_P400_0002', 'INTERRUPTED_AFTER_VALIDATION', 'VALIDATED_MOCK_ONLY'),
    (3, 'FI_P400_0003', 'MOCK_INFRASTRUCTURE_TIMEOUT', 'INVALID_INFRASTRUCTURE'),
    (4, 'FI_P400_0003', 'FRESH_ATTEMPT_RETRY', 'MOCK_COMMITTED'),
)
FINAL_COUNTS = {'mock_committed': 2, 'inflight_uncommitted': 1, 'invalid_infrastructure': 1}
SHA = re.compile(r'[a-f0-9]{64}\Z')


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    return digest(path.read_bytes())


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, 'Duplicate JSON key: ' + key)
        value[key] = item
    return value


def strict_json(path: Path) -> dict[str, Any]:
    require(path.is_file() and not is_link(path), 'Missing or linked JSON: ' + str(path))
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON number')))
    require(type(value) is dict and canonical(value) == raw,
            'JSON must be a canonical UTF-8/LF object: ' + str(path))
    return value


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def reject_links(path: Path) -> None:
    for component in (path, *path.parents):
        require(not is_link(component), 'Symlink or junction forbidden: ' + str(component))


def directory(repo: Path, arg: Path, prefix: str, *, existing: bool) -> Path:
    require('..' not in arg.parts, 'Traversal prohibited')
    original = arg if arg.is_absolute() else repo / arg
    reject_links(original.absolute())
    result = original.resolve()
    require(result.parent == repo / 'fi' / 'results' and
            re.fullmatch(prefix + r'[A-Za-z0-9_]+', result.name) is not None,
            'Output/input outside expected direct fi/results namespace')
    require(result.is_dir() if existing else not result.exists(),
            'Input missing or output already exists: ' + str(result))
    return result


def verify_adapter(repo: Path, adapter_dir: Path, adapter_audit_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    require({p.name for p in adapter_dir.iterdir()} ==
            {ADAPTER_NAME, ADAPTER_RECEIPT_NAME}, 'Adapter orphan or missing file')
    require({p.name for p in adapter_audit_dir.iterdir()} ==
            {ADAPTER_AUDIT_NAME}, 'Adapter audit orphan or missing file')
    adapter_path = adapter_dir / ADAPTER_NAME
    receipt_path = adapter_dir / ADAPTER_RECEIPT_NAME
    audit_path = adapter_audit_dir / ADAPTER_AUDIT_NAME
    a, r, v = strict_json(adapter_path), strict_json(receipt_path), strict_json(audit_path)
    require(a.get('schema') == 'CNN_PEP_V4H_EXEC_ADAPTER_CONTRACT_CANDIDATE_V1' and
            a.get('scope') == 'OFFLINE_ENGINEERING_CANDIDATE_NO_BUILD_NO_LAUNCH_NO_FI' and
            a.get('frozen_scenarios') == 400 and a.get('batch_count') == 16 and
            a.get('batch_size') == 25 and a.get('raw_evidence_committed') == 0 and
            a.get('new_xsim_runs') == 0 and a.get('real_fi_results') == 0 and
            a.get('source_snapshot_built') is False and
            a.get('full400_authorized') is False and
            a.get('full400_executed') is False and
            a.get('G6_production_qualified') is False and
            a.get('G7_scientific_approved') is False and
            a.get('G0_G7_signed_authorization') is False and
            a.get('attempt_budget_8_of_8_exhausted') is True and
            a.get('release_gate') == 'FULL400_BLOCKED_G6_EXECUTOR_NOT_QUALIFIED_G7_NOT_APPROVED',
            'Adapter is not an offline, locked source contract')
    rows = a.get('invocations')
    require(type(rows) is list and len(rows) == 400, 'Adapter must have exactly 400 records')
    for i, row in enumerate(rows, 1):
        require(type(row) is dict and row.get('scenario_id') == f'FI_P400_{i:04d}' and
                type(row.get('ordinal')) is int and row['ordinal'] == i and
                type(row.get('batch_number')) is int and row['batch_number'] == (i-1)//25+1 and
                type(row.get('frozen_tuple_sha256')) is str and
                SHA.fullmatch(row['frozen_tuple_sha256']) is not None and
                row.get('attempt_state') == 'UNCREATED_CONTRACT_ONLY' and
                row.get('checkpoint_committed') is False and
                row.get('raw_xsim_log_observed') is False and
                row.get('design_outcome_observed') is False and
                row.get('ready_to_execute') is False,
                'Invalid directed adapter row: ' + str(i))
    require(r.get('schema') == 'CNN_PEP_V4H_EXEC_ADAPTER_RECEIPT_CANDIDATE_V1' and
            r.get('adapter_plan_sha256') == hash_file(adapter_path) and
            r.get('full400_authorized') is False and r.get('new_xsim_runs') == 0 and
            r.get('source_snapshot_built') is False and
            v.get('schema') == 'CNN_PEP_V4H_EXEC_INDEPENDENT_OFFLINE_AUDIT_V1' and
            v.get('adapter_sha256') == hash_file(adapter_path) and
            v.get('adapter_receipt_sha256') == hash_file(receipt_path) and
            v.get('mapped_contracts_verified') == 400 and
            v.get('future_artifact_paths_verified') == 3200 and
            v.get('full400_authorized') is False and
            v.get('new_xsim_runs') == 0 and
            v.get('build_once_executed') is False and
            v.get('G6_production_qualified') is False,
            'Source V4H adapter and independent audit are inconsistent')
    return a, {'adapter_sha256': hash_file(adapter_path),
               'adapter_receipt_sha256': hash_file(receipt_path),
               'adapter_audit_sha256': hash_file(audit_path)}


def write_exclusive(path: Path, payload: bytes) -> None:
    require(not path.exists() and not is_link(path), 'Immutable file already exists: ' + str(path))
    with path.open('xb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


def atomic_new(path: Path, payload: bytes) -> None:
    tmp = path.with_name(path.name + '.tmp')
    require(not path.exists() and not tmp.exists(), 'Commit path or orphan tmp exists')
    write_exclusive(tmp, payload)
    require(not path.exists(), 'Commit target appeared during preparation')
    os.replace(tmp, path)


def expected_intent(n: int, sid: str, case: str, tuple_hash: str,
                    adapter_hash: str, parent_hash: str | None) -> dict[str, Any]:
    return {'schema': SCHEMA, 'attempt_number': n, 'scenario_id': sid,
            'frozen_tuple_sha256': tuple_hash, 'case': case,
            'adapter_plan_sha256': adapter_hash,
            'retry_parent_failure_sha256': parent_hash,
            'mock_only': True, 'xsim_executed': False,
            'fi_injected': False, 'design_outcome_observed': False,
            'full400_authorized': False}


def expected_raw(n: int, sid: str, intent_hash: str) -> dict[str, Any]:
    return {'schema': RAW_SCHEMA, 'attempt_number': n, 'scenario_id': sid,
            'intent_sha256': intent_hash, 'payload_kind': 'SYNTHETIC_ONLY_NOT_FI',
            'mock_observation': digest((str(n)+'|'+sid+'|'+intent_hash).encode('ascii')),
            'fi_applied': False, 'xsim_executed': False,
            'design_outcome_observed': False, 'classification_available': False}


def expected_state(n: int, sid: str, status: str, previous: str | None,
                   intent_hash: str, raw_hash: str | None = None) -> dict[str, Any]:
    return {'schema': STATE_SCHEMA, 'attempt_number': n, 'scenario_id': sid,
            'status': status, 'previous_event_sha256': previous,
            'intent_sha256': intent_hash, 'mock_raw_sha256': raw_hash,
            'production_outcome': None, 'xsim_executed': False,
            'full400_authorized': False}


def states_for_case(case: str) -> tuple[str, ...]:
    if case in ('HEALTHY_MOCK', 'FRESH_ATTEMPT_RETRY'):
        return ('PENDING', 'INFLIGHT', 'VALIDATED_MOCK_ONLY', 'MOCK_COMMITTED')
    if case == 'INTERRUPTED_AFTER_VALIDATION':
        return ('PENDING', 'INFLIGHT', 'VALIDATED_MOCK_ONLY')
    if case == 'MOCK_INFRASTRUCTURE_TIMEOUT':
        return ('PENDING', 'INFLIGHT', 'INVALID_INFRASTRUCTURE')
    raise ValueError('Unsupported mock case')


def execute_mock_case(attempt_dir: Path, n: int, sid: str, case: str,
                      tuple_hash: str, adapter_hash: str,
                      parent_hash: str | None = None) -> dict[str, Any]:
    attempt_dir.mkdir(exist_ok=False)
    intent = expected_intent(n, sid, case, tuple_hash, adapter_hash, parent_hash)
    write_exclusive(attempt_dir / 'intent.json', canonical(intent))
    intent_hash = hash_file(attempt_dir / 'intent.json')
    prev = None
    files = {'intent.json': intent_hash}
    for index, status in enumerate(states_for_case(case)):
        raw_hash = None
        if status == 'VALIDATED_MOCK_ONLY':
            raw_path = attempt_dir / 'raw_mock.json'
            write_exclusive(raw_path, canonical(expected_raw(n, sid, intent_hash)))
            raw_hash = hash_file(raw_path)
            files[raw_path.name] = raw_hash
        elif status == 'MOCK_COMMITTED':
            raw_hash = files['raw_mock.json']
        if status == 'INVALID_INFRASTRUCTURE':
            require('raw_mock.json' not in files, 'Timeout must not have mock raw payload')
        # Raw is generated before validation; the final commit event is a new file.
        if status in ('PENDING', 'INFLIGHT'):
            raw_hash = None
        state = expected_state(n, sid, status, prev, intent_hash, raw_hash)
        name = f'state_{index:02d}_{status.lower()}.json'
        data = canonical(state)
        if status == 'MOCK_COMMITTED':
            atomic_new(attempt_dir / name, data)
        else:
            write_exclusive(attempt_dir / name, data)
        prev = digest(data)
        files[name] = prev
    return {'attempt_number': n, 'attempt_id': attempt_dir.name,
            'scenario_id': sid, 'case': case,
            'terminal_state': states_for_case(case)[-1],
            'intent_sha256': intent_hash,
            'last_event_sha256': prev,
            'retry_parent_failure_sha256': parent_hash,
            'files': dict(sorted(files.items()))}


def deny_command(mode: str, execute: bool, approve: bool) -> None:
    require(mode == 'probe' and not execute and not approve,
            'V4L rejects execute, release, Vivado build and Full400 before IO')


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    deny_command(args.mode, args.execute, args.approve)
    reject_links(args.repo.absolute())
    repo = args.repo.resolve()
    require((repo / 'fi/results').is_dir(), 'Missing fi/results directory')
    adapter_dir = directory(repo, args.adapter_dir, 'v4h_exec_', existing=True)
    adapter_audit_dir = directory(repo, args.adapter_audit_dir, 'v4h_exec_', existing=True)
    out = directory(repo, args.out_dir, 'v4l_mocktxn_', existing=False)
    stage = out.with_name('.' + out.name + '.inflight')
    reject_links(stage.absolute())
    require(not stage.exists(), 'Preserve existing interrupted stage')
    adapter, pins = verify_adapter(repo, adapter_dir, adapter_audit_dir)
    stage.mkdir(exist_ok=False)
    (stage / 'attempts').mkdir(exist_ok=False)
    records = []
    parent_hash = None
    for n, sid, case, terminal in ATTEMPTS:
        attempt_id = f'{n:04d}_{sid}'
        item = execute_mock_case(stage / 'attempts' / attempt_id, n, sid, case,
                                 adapter['invocations'][int(sid[-4:])-1]['frozen_tuple_sha256'],
                                 pins['adapter_sha256'], parent_hash if n == 4 else None)
        require(item['terminal_state'] == terminal, 'Mock state machine contract changed')
        records.append(item)
        if n == 3:
            parent_hash = item['last_event_sha256']
    manifest = {'schema': SCHEMA, 'scope': 'FOUR_MOCK_ATTEMPTS_NOT_FI',
                **pins, 'adapter_generator_sha256': adapter['adapter_generator_sha256'],
                'transaction_generator_sha256': hash_file(Path(__file__).resolve()),
                'attempts': records, 'fixed_cases': [v[2] for v in ATTEMPTS],
                'unique_frozen_scenario_ids': 3, 'mock_attempts': 4,
                **FINAL_COUNTS, 'real_fi_results': 0, 'xsim_runs': 0,
                'vivado_builds': 0, 'G6_production_qualified': False,
                'G7_scientific_approved': False, 'full400_authorized': False,
                'release_gate': DENIAL}
    atomic_new(stage / MANIFEST_NAME, canonical(manifest))
    receipt = {'schema': SCHEMA, 'created_utc': datetime.now(timezone.utc).isoformat(),
               'manifest_sha256': hash_file(stage / MANIFEST_NAME),
               'transaction_generator_sha256': manifest['transaction_generator_sha256'],
               'mock_attempts': 4, 'mock_committed': 2,
               'new_xsim_runs': 0, 'full400_authorized': False,
               'G6_production_qualified': False, 'release_gate': DENIAL}
    atomic_new(stage / RECEIPT_NAME, canonical(receipt))
    require(not out.exists(), 'Final output appeared unexpectedly')
    stage.rename(out)
    print('V4L_MOCK_ATOMIC_PROBE_PASS: 4 mock attempts; 2 mock committed; '
          '1 interrupted; 1 infrastructure timeout; 0 build; 0 XSim; FULL400 BLOCKED', flush=True)
    print('V4L_OUTPUT: ' + str(out), flush=True)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('probe','execute','full400','release','build'), default='probe')
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--adapter-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_plan_01'))
    p.add_argument('--adapter-audit-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_audit_01'))
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4l_mocktxn_01'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')
    a = p.parse_args()
    try:
        run_probe(a)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, json.JSONDecodeError) as e:
        raise SystemExit('V4L_MOCK_PROBE_BLOCKED: ' + str(e)) from e


if __name__ == '__main__':
    main()

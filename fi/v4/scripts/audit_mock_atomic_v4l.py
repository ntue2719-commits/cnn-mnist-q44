#!/usr/bin/env python3
"""Independent read-only verifier for V4L four-case MOCK atomic evidence journal.

No Vivado, XSim, production commit, job management, simulated FI or approval.
The verifier checks the source V4H offline adapter, raw mock events, state
order, hash links, retry ancestry and absence of real-FI claims.
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

AUDIT_SCHEMA = 'CNN_PEP_V4L_MOCK_ATOMIC_INDEPENDENT_AUDIT_V1'
SCHEMA = 'CNN_PEP_V4L_MOCK_ATOMIC_JOURNAL_V1'
DENIAL = 'FULL400_BLOCKED_G6_NOT_QUALIFIED_G7_NOT_APPROVED'
MANIFEST_NAME = 'v4l_mock_transaction_manifest.json'
RECEIPT_NAME = 'v4l_mock_transaction_receipt.json'
EXPECTED = (
    (1, 'FI_P400_0001', 'HEALTHY_MOCK', ('PENDING','INFLIGHT','VALIDATED_MOCK_ONLY','MOCK_COMMITTED')),
    (2, 'FI_P400_0002', 'INTERRUPTED_AFTER_VALIDATION', ('PENDING','INFLIGHT','VALIDATED_MOCK_ONLY')),
    (3, 'FI_P400_0003', 'MOCK_INFRASTRUCTURE_TIMEOUT', ('PENDING','INFLIGHT','INVALID_INFRASTRUCTURE')),
    (4, 'FI_P400_0003', 'FRESH_ATTEMPT_RETRY', ('PENDING','INFLIGHT','VALIDATED_MOCK_ONLY','MOCK_COMMITTED')),
)
SHA = re.compile(r'[a-f0-9]{64}\Z')


def require(valid: bool, why: str) -> None:
    if not valid:
        raise ValueError(why)


def encode(x: Any) -> bytes:
    return (json.dumps(x, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: Path) -> str:
    return sha(path.read_bytes())


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = {}
    for k, v in pairs:
        require(k not in obj, 'Duplicate JSON key: '+k)
        obj[k] = v
    return obj


def linked(p: Path) -> bool:
    return p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction())


def reject_links(p: Path) -> None:
    for part in (p, *p.parents):
        require(not linked(part), 'Symlink or junction forbidden')


def exact_json(p: Path) -> dict[str, Any]:
    require(p.is_file() and not linked(p), 'Missing or linked JSON: '+str(p))
    raw = p.read_bytes()
    obj = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Illegal JSON number')))
    require(type(obj) is dict and raw == encode(obj), 'Noncanonical JSON or invalid root: '+str(p))
    return obj


def result_dir(repo: Path, arg: Path, prefix: str, exists: bool) -> Path:
    require('..' not in arg.parts, 'Traversal rejected')
    original = arg if arg.is_absolute() else repo / arg
    reject_links(original.absolute())
    path = original.resolve()
    require(path.parent == repo / 'fi/results' and
            re.fullmatch(prefix + r'[A-Za-z0-9_]+', path.name) is not None,
            'Evidence directory outside namespace')
    require(path.is_dir() if exists else not path.exists(),
            'Input missing or output already exists')
    return path


def verify_source(adapter_dir: Path, audit_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    require({p.name for p in adapter_dir.iterdir()} ==
            {'v4h_exec_adapter_400.json','v4h_exec_receipt.json'} and
            {p.name for p in audit_dir.iterdir()} ==
            {'v4h_exec_independent_audit.json'}, 'Source adapter directory contents changed')
    m = exact_json(adapter_dir/'v4h_exec_adapter_400.json')
    receipt = exact_json(adapter_dir/'v4h_exec_receipt.json')
    audit = exact_json(audit_dir/'v4h_exec_independent_audit.json')
    manifest_sha = sha_file(adapter_dir/'v4h_exec_adapter_400.json')
    receipt_sha = sha_file(adapter_dir/'v4h_exec_receipt.json')
    audit_sha = sha_file(audit_dir/'v4h_exec_independent_audit.json')
    require(m.get('schema') == 'CNN_PEP_V4H_EXEC_ADAPTER_CONTRACT_CANDIDATE_V1' and
            m.get('scope') == 'OFFLINE_ENGINEERING_CANDIDATE_NO_BUILD_NO_LAUNCH_NO_FI' and
            m.get('frozen_scenarios') == 400 and m.get('batch_count') == 16 and
            m.get('batch_size') == 25 and m.get('raw_evidence_committed') == 0 and
            m.get('new_xsim_runs') == 0 and m.get('real_fi_results') == 0 and
            m.get('source_snapshot_built') is False and
            m.get('attempt_budget_8_of_8_exhausted') is True and
            m.get('full400_authorized') is False and m.get('full400_executed') is False and
            m.get('G6_production_qualified') is False and
            m.get('G7_scientific_approved') is False and
            m.get('G0_G7_signed_authorization') is False and
            m.get('release_gate') == 'FULL400_BLOCKED_G6_EXECUTOR_NOT_QUALIFIED_G7_NOT_APPROVED',
            'Invalid V4H source contract')
    rows = m.get('invocations')
    require(type(rows) is list and len(rows) == 400, 'V4H source missing records')
    for i, row in enumerate(rows, 1):
        require(type(row) is dict and row.get('scenario_id') == f'FI_P400_{i:04d}' and
                type(row.get('ordinal')) is int and row['ordinal'] == i and
                type(row.get('batch_number')) is int and row['batch_number'] == (i-1)//25+1 and
                type(row.get('frozen_tuple_sha256')) is str and
                SHA.fullmatch(row['frozen_tuple_sha256']) is not None and
                row.get('attempt_state') == 'UNCREATED_CONTRACT_ONLY' and
                row.get('ready_to_execute') is False and row.get('checkpoint_committed') is False and
                row.get('raw_xsim_log_observed') is False and
                row.get('design_outcome_observed') is False,
                'V4H per-scenario contract changed')
    require(receipt.get('schema') == 'CNN_PEP_V4H_EXEC_ADAPTER_RECEIPT_CANDIDATE_V1' and
            receipt.get('adapter_plan_sha256') == manifest_sha and
            receipt.get('full400_authorized') is False and receipt.get('new_xsim_runs') == 0 and
            receipt.get('source_snapshot_built') is False and
            audit.get('schema') == 'CNN_PEP_V4H_EXEC_INDEPENDENT_OFFLINE_AUDIT_V1' and
            audit.get('adapter_sha256') == manifest_sha and
            audit.get('adapter_receipt_sha256') == receipt_sha and
            audit.get('mapped_contracts_verified') == 400 and
            audit.get('future_artifact_paths_verified') == 3200 and
            audit.get('new_xsim_runs') == 0 and audit.get('build_once_executed') is False and
            audit.get('G6_production_qualified') is False and
            audit.get('full400_authorized') is False, 'V4H independent audit and receipt mismatch')
    return rows, {'adapter_sha256': manifest_sha,
                  'adapter_receipt_sha256': receipt_sha,
                  'adapter_audit_sha256': audit_sha}, m['adapter_generator_sha256']


def expected_intent(n: int, sid: str, case: str, frozen: str, adapter_sha: str,
                    parent_sha: str | None) -> dict[str, Any]:
    return {'schema': SCHEMA, 'attempt_number': n, 'scenario_id': sid,
            'frozen_tuple_sha256': frozen, 'case': case,
            'adapter_plan_sha256': adapter_sha,
            'retry_parent_failure_sha256': parent_sha,
            'mock_only': True, 'xsim_executed': False, 'fi_injected': False,
            'design_outcome_observed': False, 'full400_authorized': False}


def expected_raw(n: int, sid: str, intent_hash: str) -> dict[str, Any]:
    return {'schema': 'CNN_PEP_V4L_MOCK_RAW_V1', 'attempt_number': n,
            'scenario_id': sid, 'intent_sha256': intent_hash,
            'payload_kind': 'SYNTHETIC_ONLY_NOT_FI',
            'mock_observation': sha((str(n)+'|'+sid+'|'+intent_hash).encode('ascii')),
            'fi_applied': False, 'xsim_executed': False,
            'design_outcome_observed': False, 'classification_available': False}


def expected_event(n: int, sid: str, state: str, prev: str | None,
                   intent_sha: str, raw_sha: str | None) -> dict[str, Any]:
    return {'schema': 'CNN_PEP_V4L_MOCK_STATE_EVENT_V1',
            'attempt_number': n, 'scenario_id': sid, 'status': state,
            'previous_event_sha256': prev, 'intent_sha256': intent_sha,
            'mock_raw_sha256': raw_sha, 'production_outcome': None,
            'xsim_executed': False, 'full400_authorized': False}


def verify_attempts(probe: Path, manifest: dict[str, Any],
                    source_rows: list[dict[str, Any]], pins: dict[str, str]) -> tuple[int, int]:
    attempt_root = probe/'attempts'
    require(attempt_root.is_dir() and not linked(attempt_root), 'No attempt directory')
    summaries = manifest.get('attempts')
    require(type(summaries) is list and len(summaries) == 4, 'Four mock attempt summaries required')
    expected_dirs = {f'{n:04d}_{sid}' for n, sid, _, _ in EXPECTED}
    require({x.name for x in attempt_root.iterdir()} == expected_dirs,
            'Missing/orphaned/duplicate attempt directory')
    failure_sha = None
    mock_committed = 0
    for i, (n, sid, case, statuses) in enumerate(EXPECTED):
        dirname = f'{n:04d}_{sid}'
        folder = attempt_root/dirname
        require(folder.is_dir() and not linked(folder), 'Linked or missing attempt folder')
        parent = failure_sha if n == 4 else None
        frozen = source_rows[int(sid[-4:])-1]['frozen_tuple_sha256']
        intent = expected_intent(n, sid, case, frozen, pins['adapter_sha256'], parent)
        intent_path = folder/'intent.json'
        require(exact_json(intent_path) == intent, 'Invalid intent or parent linkage')
        intent_sha = sha_file(intent_path)
        expected_files = {'intent.json': intent_sha}
        prev = None
        has_raw = 'VALIDATED_MOCK_ONLY' in statuses
        raw_sha = None
        if has_raw:
            raw_obj = expected_raw(n, sid, intent_sha)
            raw_path = folder/'raw_mock.json'
            require(exact_json(raw_path) == raw_obj, 'Synthetic mock raw payload modified')
            raw_sha = sha_file(raw_path)
            expected_files['raw_mock.json'] = raw_sha
        for index, status in enumerate(statuses):
            name = f'state_{index:02d}_{status.lower()}.json'
            relevant_raw = raw_sha if status in ('VALIDATED_MOCK_ONLY','MOCK_COMMITTED') else None
            expected = expected_event(n, sid, status, prev, intent_sha, relevant_raw)
            p = folder/name
            require(exact_json(p) == expected, 'Invalid state or broken hash chain: '+name)
            prev = sha_file(p)
            expected_files[name] = prev
        require({p.name for p in folder.iterdir()} == set(expected_files),
                'Orphan, unexpected, or missing attempt artifact')
        summary = summaries[i]
        require(type(summary) is dict and summary == {
            'attempt_number': n, 'attempt_id': dirname, 'scenario_id': sid,
            'case': case, 'terminal_state': statuses[-1],
            'intent_sha256': intent_sha, 'last_event_sha256': prev,
            'retry_parent_failure_sha256': parent,
            'files': dict(sorted(expected_files.items()))},
            'Manifest summary/sha mismatch')
        if n == 3:
            failure_sha = prev
        if statuses[-1] == 'MOCK_COMMITTED':
            mock_committed += 1
    require(mock_committed == 2, 'Wrong mock committed denominator')
    return len(EXPECTED), mock_committed


def verify(probe: Path, source_rows: list[dict[str, Any]], pins: dict[str, str],
           generator_sha: str, current_source_script: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    require({p.name for p in probe.iterdir()} ==
            {'attempts', MANIFEST_NAME, RECEIPT_NAME}, 'Unexpected probe root files')
    manifest_path, receipt_path = probe/MANIFEST_NAME, probe/RECEIPT_NAME
    manifest, receipt = exact_json(manifest_path), exact_json(receipt_path)
    require(type(manifest) is dict and set(manifest) == {
        'schema','scope','adapter_sha256','adapter_receipt_sha256','adapter_audit_sha256',
        'adapter_generator_sha256','transaction_generator_sha256','attempts','fixed_cases',
        'unique_frozen_scenario_ids','mock_attempts', 'mock_committed',
        'inflight_uncommitted','invalid_infrastructure','real_fi_results','xsim_runs',
        'vivado_builds','G6_production_qualified','G7_scientific_approved',
        'full400_authorized','release_gate'}, 'Unexpected manifest fields')
    require(manifest['schema'] == SCHEMA and manifest['scope'] == 'FOUR_MOCK_ATTEMPTS_NOT_FI' and
            all(manifest[k] == v for k, v in pins.items()) and
            manifest['adapter_generator_sha256'] == generator_sha and
            manifest['transaction_generator_sha256'] == sha_file(current_source_script) and
            manifest['fixed_cases'] == [x[2] for x in EXPECTED] and
            manifest['unique_frozen_scenario_ids'] == 3 and manifest['mock_attempts'] == 4 and
            manifest['mock_committed'] == 2 and manifest['inflight_uncommitted'] == 1 and
            manifest['invalid_infrastructure'] == 1 and
            manifest['real_fi_results'] == 0 and manifest['xsim_runs'] == 0 and
            manifest['vivado_builds'] == 0 and
            manifest['G6_production_qualified'] is False and
            manifest['G7_scientific_approved'] is False and
            manifest['full400_authorized'] is False and
            manifest['release_gate'] == DENIAL, 'Unqualified or fabricated manifest')
    count, commits = verify_attempts(probe, manifest, source_rows, pins)
    require(type(receipt) is dict and set(receipt) == {
        'schema','created_utc','manifest_sha256','transaction_generator_sha256',
        'mock_attempts','mock_committed','new_xsim_runs','full400_authorized',
        'G6_production_qualified','release_gate'}, 'Unexpected receipt fields')
    require(receipt['schema'] == SCHEMA and
            type(receipt['created_utc']) is str and receipt['created_utc'].endswith('+00:00') and
            receipt['manifest_sha256'] == sha_file(manifest_path) and
            receipt['transaction_generator_sha256'] == manifest['transaction_generator_sha256'] and
            receipt['mock_attempts'] == count and receipt['mock_committed'] == commits and
            receipt['new_xsim_runs'] == 0 and receipt['full400_authorized'] is False and
            receipt['G6_production_qualified'] is False and receipt['release_gate'] == DENIAL,
            'Receipt digest or safety mismatch')
    return manifest, receipt


def write_report(path: Path, record: dict[str, Any]) -> None:
    path.mkdir(exist_ok=False)
    temp = path/'v4l_mock_atomic_independent_audit.json.tmp'
    final = path/'v4l_mock_atomic_independent_audit.json'
    with temp.open('xb') as f:
        f.write(encode(record));f.flush();os.fsync(f.fileno())
    require(not final.exists(), 'Refusing overwrite')
    os.replace(temp, final)


def deny(mode: str, execute: bool, approve: bool) -> None:
    require(mode == 'audit' and not execute and not approve,
            'Audit has no FI, execution or approval capability')


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    deny(args.mode, args.execute, args.approve)
    reject_links(args.repo.absolute())
    repo = args.repo.resolve()
    require((repo/'fi/results').is_dir(), 'Missing fi/results')
    ad = result_dir(repo, args.adapter_dir, 'v4h_exec_', True)
    aa = result_dir(repo, args.adapter_audit_dir, 'v4h_exec_', True)
    probe = result_dir(repo, args.probe_dir, 'v4l_mocktxn_', True)
    output = result_dir(repo, args.out_dir, 'v4l_mocktxn_audit_', False)
    require(not probe.with_name('.'+probe.name+'.inflight').exists(),
            'An inflight stage exists; refuse to audit as completed')
    rows, pins, generator_sha = verify_source(ad, aa)
    source_script = Path(__file__).resolve().parent/'mock_atomic_ledger_v4l.py'
    reject_links(source_script.absolute())
    require(source_script.is_file(), 'Missing original mock generator for independent hash')
    manifest, receipt = verify(probe, rows, pins, generator_sha, source_script)
    report = {'schema': AUDIT_SCHEMA,
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'probe_manifest_sha256': sha_file(probe/MANIFEST_NAME),
              'probe_receipt_sha256': sha_file(probe/RECEIPT_NAME),
              'source_adapter_sha256': pins['adapter_sha256'],
              'attempts_verified': 4, 'mock_committed_verified': 2,
              'inflight_uncommitted_verified': 1, 'invalid_infrastructure_verified': 1,
              'real_fi_results': 0, 'new_xsim_runs': 0, 'vivado_builds': 0,
              'G6_production_qualified': False,
              'G7_scientific_approved': False, 'full400_authorized': False,
              'release_gate': DENIAL}
    write_report(output, report)
    print('V4L_MOCK_ATOMIC_AUDIT_PASS: 4/4 attempt journals; 2 MOCK commits; '
          '1 interrupted; 1 infrastructure invalid; 0 XSim; FULL400 BLOCKED', flush=True)
    print('V4L_AUDIT_OUTPUT: ' + str(output), flush=True)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('audit','execute','full400','release','build'), default='audit')
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--adapter-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_plan_01'))
    p.add_argument('--adapter-audit-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_audit_01'))
    p.add_argument('--probe-dir', type=Path, default=Path('fi/results/v4l_mocktxn_01'))
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4l_mocktxn_audit_01'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')
    a = p.parse_args()
    try:
        run_audit(a)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,json.JSONDecodeError) as exc:
        raise SystemExit('V4L_MOCK_ATOMIC_AUDIT_BLOCKED: '+str(exc)) from exc


if __name__ == '__main__':
    main()

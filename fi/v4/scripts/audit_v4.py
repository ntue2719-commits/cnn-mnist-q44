#!/usr/bin/env python3
"""CNN_PEP V4 cross-evidence audit (read only), 2026-11-04.

Replays existing V3a/V3b parsers; verifies frozen plan, Control10, timing,
Vivado-compiled HDL provenance, V3a/V3b logs and CSV/JSON integrity, CTL
97-file inventory, and issues a full400 qualification BLOCK if six targets
still have only one-window evidence. Never modifies P0, frozen inputs, or FI
results. Does not infer hardware SEU probabilities or promote targets.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
from typing import Mapping

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / 'scripts'
V3B_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / 'v3b' / 'scripts'
for directory in (SCRIPTS_DIR, V3B_SCRIPTS_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from pilot400_common import FIELDS, load_plan, verify_manifest, read_csv, sha256  # noqa: E402
from run_pilot400_target_validation_v3 import (  # noqa: E402
    inspect_applied_and_result, summarize_targets, TARGET_FIELDS, REPORT_FIELDS)
from parse_fi_v2 import parse_one  # noqa: E402
from run_mem1_v3b import FROZEN_HASHES, validate_v3a, override_prj  # noqa: E402
from run_pilot400_control10 import validate_control10_gate  # noqa: E402
from run_pilot400 import validate_full_timing_gate  # noqa: E402

INVENTORY_SHA256 = '748ca49f0d50dbca0ecc0b0fb609b59d961bdb3438ab24fcffd4811922deabf9'
DEFAULT_RESULTS = Path('fi/results')
V3A_NAME = 'pilot400_v3a_28_01'
CONTROL_NAME = 'pilot400_control10_vivado_02'
PROFILE_NAME = 'pilot400_profile_after_bitpair_01'


@dataclass(frozen=True)
class Group:
    name: str
    dirname: str
    manifest: str
    selected: str
    module: str
    targets: tuple[str, ...]
    schema: str
    status: str


BUILD_PROJECTS = {
    'MEM2': 'isolated_build/tb_v3b_vlog.prj',
    'MEM1': 'isolated_mem1_build/tb_v3b_mem1_vlog.prj',
    'BUF0007': 'isolated_buf0007_build/tb_v3b_buf0007_vlog.prj',
    'BUF0017': 'isolated_buf0017_build/tb_v3b_buf0017_vlog.prj',
    'BUF0032': 'isolated_buf0032_build/tb_v3b_buf0032_vlog.prj',
    'DAT0002': 'isolated_dat0002_build/tb_v3b_dat0002_vlog.prj',
    'DAT0004': 'isolated_dat0004_build/tb_v3b_dat0004_vlog.prj',
    'FCACC': 'isolated_fcacc_build/tb_v3b_fcacc_vlog.prj',
    'CTL': 'isolated_ctl_build/tb_v3b_ctl_vlog.prj',
}

GROUPS = (
    Group('MEM2', 'pilot400_v3b_mem2_02', 'v3b_manifest.json',
          'selected_mem2_scenarios.csv', 'run_mem2_v3b',
          ('MEM_0004', 'MEM_0005'), 'PILOT400_V3B_MEM2_CONSUMER_V1', 'CONSUMER_PROVED_4_OF_4'),
    Group('MEM1', 'pilot400_v3b_mem1_01', 'v3b_mem1_manifest.json',
          'selected_mem1_scenarios.csv', 'run_mem1_v3b',
          ('MEM_0003',), 'PILOT400_V3B_MEM1_CONSUMER_V1', 'CONSUMER_PROVED_2_OF_2'),
    Group('BUF0007', 'pilot400_v3b_buf0007_01', 'v3b_buf0007_manifest.json',
          'selected_buf0007_scenarios.csv', 'run_buf0007_v3b',
          ('BUF_0007',), 'PILOT400_V3B_BUF0007_CONSUMER_V1', 'CONSUMER_PROVED_2_OF_2'),
    Group('BUF0017', 'pilot400_v3b_buf0017_01', 'v3b_buf0017_manifest.json',
          'selected_buf0017_scenarios.csv', 'run_buf0017_v3b',
          ('BUF_0017',), 'PILOT400_V3B_BUF0017_CONSUMER_V1', 'CONSUMER_PROVED_2_OF_2'),
    Group('BUF0032', 'pilot400_v3b_buf0032_01', 'v3b_buf0032_manifest.json',
          'selected_buf0032_scenarios.csv', 'run_buf0032_v3b',
          ('BUF_0032',), 'PILOT400_V3B_BUF0032_CONSUMER_V1', 'CONSUMER_PROVED_2_OF_2'),
    Group('DAT0002', 'pilot400_v3b_dat0002_01', 'v3b_dat0002_manifest.json',
          'selected_dat0002_scenarios.csv', 'run_dat0002_v3b',
          ('DAT_0002',), 'PILOT400_V3B_DAT0002_CONSUMER_V1', 'CONSUMER_PROVED_2_OF_2'),
    Group('DAT0004', 'pilot400_v3b_dat0004_01', 'v3b_dat0004_manifest.json',
          'selected_dat0004_scenarios.csv', 'run_dat0004_v3b',
          ('DAT_0004',), 'PILOT400_V3B_DAT0004_CONSUMER_V1', 'CONSUMER_PROVED_2_OF_2'),
    Group('FCACC', 'pilot400_v3b_fcacc_01', 'v3b_fcacc_manifest.json',
          'selected_fcacc_scenarios.csv', 'run_fcacc_v3b',
          ('DAT_0007', 'DAT_0008'), 'PILOT400_V3B_FCACC_CONSUMER_V1', 'CONSUMER_PROVED_4_OF_4'),
    Group('CTL', 'pilot400_v3b_ctl8_01', 'v3b_ctl_manifest.json',
          'selected_ctl_scenarios.csv', 'run_ctl_v3b',
          ('CTL_0012', 'CTL_0019', 'CTL_0026', 'CTL_0028'),
          'PILOT400_V3B_CTL_CONSUMER_V1', 'CONTROL_DECISION_PROVED_8_OF_8'),
)


def require(condition: bool, explanation: str) -> None:
    if not condition:
        raise ValueError(explanation)


def read_json(path: Path) -> dict:
    require(path.is_file(), f'Missing JSON: {path}')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    require(isinstance(value, dict), f'JSON object expected: {path}')
    return value


def unique_index(rows: list[dict], column: str, expected: int, context: str) -> dict[str, dict]:
    require(len(rows) == expected, f'{context}: expected {expected} rows, got {len(rows)}')
    keys = [r.get(column) for r in rows]
    require(all(isinstance(k, str) and k for k in keys), f'{context}: missing {column}')
    require(len(set(keys)) == expected, f'{context}: duplicate {column}')
    return dict(zip(keys, rows))


def check_ctl_inventory(base: Path, expected_digest: str = INVENTORY_SHA256,
                        expected_count: int = 97) -> dict:
    inventory = base / 'sha256_inventory.csv'
    require(inventory.is_file(), 'V4 CTL: missing SHA256 inventory')
    digest = sha256(inventory)
    require(digest.lower() == expected_digest.lower(),
            f'V4 CTL: inventory digest changed: {digest}')
    rows = read_csv(inventory, ('File', 'SHA256'))
    require(len(rows) == expected_count, f'V4 CTL: expected {expected_count} evidence files')
    indexed = unique_index(rows, 'File', expected_count, 'V4 CTL inventory')
    actual_files = {str(p.relative_to(base)).replace('\\', '/') for p in base.rglob('*')
                    if p.is_file() and p != inventory}
    expected_files = {k.replace('\\', '/') for k in indexed}
    require(len(expected_files) == expected_count, 'V4 CTL: path alias/duplicate in inventory')
    require(actual_files == expected_files,
            f'V4 CTL: file set changed; missing={sorted(expected_files-actual_files)[:3]}, '
            f'extra={sorted(actual_files-expected_files)[:3]}')
    base_abs = base.resolve()
    for relative, entry in indexed.items():
        require(not Path(relative).is_absolute(), f'V4 CTL: absolute inventory path: {relative}')
        path = (base / relative).resolve()
        require(path.is_relative_to(base_abs), f'V4 CTL: path escape: {relative}')
        require(path.is_file(), f'V4 CTL: missing file: {relative}')
        require(sha256(path).lower() == entry['SHA256'].lower(),
                f'V4 CTL: SHA256 mismatch: {relative}')
    return {'file_count': expected_count, 'inventory_sha256': digest, 'status': 'PASS'}


def check_v3a(repo: Path, results: Path, plan: list[dict],
              manifest: list[dict]) -> tuple[dict, dict[str, dict]]:
    root = results / V3A_NAME
    # validate_v3a() belongs to the MEM_0003 V3b runner. It authenticates
    # the COMPLETE 28/28 V3a evidence, but its second return value is only the
    # TWO selected MEM_0003 scenarios. Do not confuse that target-local pair
    # with the full V3a scenario set needed by the V4 cross-evidence audit.
    data, mem1_pair, _ = validate_v3a(repo, root, manifest)
    require(len(mem1_pair) == 2 and
            all(r['target_id'] == 'MEM_0003' for r in mem1_pair),
            'V3a MEM_0003 gate returned an unexpected target-local pair')
    selected = read_csv(root / 'selected_scenarios.csv', FIELDS)
    by_id = unique_index(selected, 'scenario_id', 28, 'V3a selected scenarios')
    manifest_by_id = {row['scenario_id']: row for row in manifest}
    candidate_ids = {row['target_id'] for row in plan
                     if row['evidence_status'] == 'CANDIDATE'}
    require(len(candidate_ids) == 14, 'V3a candidate target set is not 14')
    for sid, row in by_id.items():
        require(sid in manifest_by_id and row == manifest_by_id[sid],
                f'V3a selected scenario differs from frozen manifest: {sid}')
        require(row['sample_id'] == 'IMG0000' and
                row['target_id'] in candidate_ids,
                f'V3a selected scenario is not a candidate/IMG0000: {sid}')
    for tid in candidate_ids:
        require(sum(row['target_id'] == tid for row in selected) == 2,
                f'V3a expected both frozen configurations: {tid}')
    audit_csv = unique_index(read_csv(root / 'target_validation.csv', REPORT_FIELDS),
                             'scenario_id', 28, 'V3a target_validation.csv')
    observed = {}
    for sid, row in by_id.items():
        log = root / 'logs' / (sid + '.log')
        replayed = inspect_applied_and_result(log, row)
        require(audit_csv[sid] == replayed,
                f'V3a target_validation.csv differs from strict replay: {sid}')
        observed[sid] = replayed
    plans = [p for p in plan if p['evidence_status'] == 'CANDIDATE']
    target_csv = unique_index(read_csv(root / 'target_summary.csv', TARGET_FIELDS),
                              'target_id', 14, 'V3a target_summary.csv')
    for record in summarize_targets(plans, list(observed.values()), 1):
        require(target_csv[record['target_id']] == record,
                f'V3a target_summary differs from strict replay: {record["target_id"]}')
    require(data['selected_count'] == len(observed) == 28,
            'V3a selected count mismatch')
    return data, observed


def validate_group_manifest(repo: Path, folder: Path, group: Group,
                            v3a_meta: dict, expected: Mapping[str, dict]) -> tuple[dict, dict]:
    metadata = read_json(folder / group.manifest)
    count = 2 * len(group.targets)
    require(metadata.get('schema') == group.schema, f'{group.name}: schema mismatch')
    require(metadata.get('status') == group.status, f'{group.name}: status mismatch')
    require(metadata.get('source_authority') == 'vivado', f'{group.name}: source authority mismatch')
    require(metadata.get('original_v3a_manifest_sha256') == sha256(folder.parent / V3A_NAME / 'validation_manifest.json'),
            f'{group.name}: V3a ancestry digest mismatch')
    require(metadata.get('original_v3a_prj_sha256') == sha256(folder.parent / V3A_NAME / 'source_vlog.prj'),
            f'{group.name}: Vivado .prj ancestry mismatch')
    require(metadata.get('original_tb_sha256') == FROZEN_HASHES['fi/tb/tb_cnn_mnist_fi_pilot400.v'],
            f'{group.name}: original TB hash mismatch')
    if 'selected_count' in metadata:
        require(metadata['selected_count'] == count, f'{group.name}: selected_count mismatch')

    module = importlib.import_module(group.module)
    probe_path = (repo / module.TB_REL) if hasattr(module, 'TB_REL') else (
        repo / 'fi/v3b/tb/tb_cnn_mnist_fi_pilot400.v' if group.name == 'MEM2'
        else repo / 'fi/v3b/mem1/tb/tb_cnn_mnist_fi_pilot400.v')
    probe_sha = metadata.get('probe_tb_sha256', metadata.get('v3b_tb_sha256'))
    require(probe_sha and probe_path.is_file() and sha256(probe_path) == probe_sha,
            f'{group.name}: probe TB file/hash mismatch')
    module.validate_frozen_inputs(repo)
    if 'PROBE_TB_SHA256' in vars(module):
        require(sha256(probe_path) == module.PROBE_TB_SHA256,
                f'{group.name}: pinned probe TB hash mismatch')

    # Rebuild the expected compile source list from the signed V3a .prj.
    # Only the testbench pathname may differ. Every RTL source remains the
    # same Vivado-authoritative file, in the same compile order.
    source_prj = folder.parent / V3A_NAME / 'source_vlog.prj'
    build_prj = folder / BUILD_PROJECTS[group.name]
    prj_digest = metadata.get('probe_prj_sha256', metadata.get('v3b_prj_sha256'))
    require(build_prj.is_file() and prj_digest and sha256(build_prj) == prj_digest,
            f'{group.name}: build .prj digest missing/changed')
    expected_prj = override_prj(source_prj.read_text(encoding='utf-8', errors='replace'),
                                probe_path)
    require(build_prj.read_text(encoding='utf-8', errors='replace') == expected_prj,
            f'{group.name}: compiled HDL list differs from V3a beyond TB replacement')
    for stage in ('xvlog', 'xelab'):
        stage_log = folder / (stage + '.log')
        require(stage_log.is_file() and sha256(stage_log) == metadata.get(stage + '_log_sha256'),
                f'{group.name}: compile/elaboration log missing/changed: {stage}')
    require(metadata.get('elaboration_mode') in ('FULL_BUILD', 'INCREMENTAL_RELINK'),
            f'{group.name}: elaboration mode invalid')
    require(metadata.get('control10_manifest_sha256') == sha256(folder.parent / CONTROL_NAME / 'control10_manifest.json'),
            f'{group.name}: Control10 ancestry digest mismatch')
    require(metadata.get('timing_audit_sha256') == sha256(folder.parent / PROFILE_NAME / 'timing_window_audit.csv'),
            f'{group.name}: timing ancestry digest mismatch')
    if 'authoritative_source_hashes' in metadata:
        require(metadata['authoritative_source_hashes'] == v3a_meta['snapshot_build']['source_hashes'],
                f'{group.name}: Vivado source set changed')

    selected_path = folder / group.selected
    require(sha256(selected_path) == metadata.get('selected_sha256'),
            f'{group.name}: selected CSV digest mismatch')
    selected = read_csv(selected_path, FIELDS)
    index = unique_index(selected, 'scenario_id', count, group.name + ' selected')
    require({r['target_id'] for r in selected} == set(group.targets),
            f'{group.name}: unexpected target set')
    for tid in group.targets:
        require(sum(r['target_id'] == tid for r in selected) == 2,
                f'{group.name}: target pair missing: {tid}')
    for sid, row in index.items():
        require(sid in expected and all(row[key] == expected[sid][key] for key in FIELDS),
                f'{group.name}: altered scenario contract: {sid}')

    manifest_rows = unique_index(metadata.get('records', []), 'scenario_id',
                                 count, f'{group.name} manifest records')
    csv_path = folder / 'consumer_validation.csv'
    require(sha256(csv_path) == metadata.get('consumer_csv_sha256'),
            f'{group.name}: consumer CSV SHA256 mismatch')
    rows = read_csv(csv_path, (*FIELDS, 'consumer_proof', 'outcome', 'log_sha256'))
    csv_rows = unique_index(rows, 'scenario_id', count, f'{group.name} consumer CSV')
    results = {}
    for sid, spec in index.items():
        entry = manifest_rows[sid]
        require('consumer_proof' not in entry or entry['consumer_proof'] not in ('', 'NOT_PROVED'),
                f'{group.name}: missing consumer proof: {sid}')
        log = folder / 'logs' / (sid + '.log')
        require(log.is_file() and Path(entry.get('log', '')).resolve() == log.resolve(),
                f'{group.name}: log path invalid: {sid}')
        require(sha256(log) == entry.get('log_sha256'),
                f'{group.name}: changed log SHA256: {sid}')
        v3a_log = folder.parent / V3A_NAME / 'logs' / (sid + '.log')
        if group.name == 'MEM2':
            rechecked = module.parse_consumer(log, spec)
        else:
            rechecked = module.parse_consumer(log, spec, v3a_log)
        require(rechecked == csv_rows[sid],
                f'{group.name}: replayed proof differs from consumer_validation.csv: {sid}')
        require(rechecked.get('consumer_proof', '').startswith('VERIFIED_'),
                f'{group.name}: consumer status not verified: {sid}')
        require(rechecked['consumer_proof'] == entry.get('consumer_proof', rechecked['consumer_proof']),
                f'{group.name}: proof manifest differs: {sid}')
        require(rechecked['outcome'] == expected[sid]['outcome'],
                f'{group.name}: V3a/V3b outcome mismatch: {sid}')
        require(parse_one(log) == parse_one(v3a_log),
                f'{group.name}: V3a/V3b strict FI_RESULT_V2 mismatch: {sid}')
        results[sid] = rechecked
    return metadata, results


def qualification(plan: list[dict], results: Mapping[str, dict]) -> list[dict]:
    rows = []
    for target in plan:
        tid = target['target_id']
        hit = [r for r in results.values() if r['target_id'] == tid]
        verified = len(hit) == 2 and len({r['scenario_id'] for r in hit}) == 2
        rows.append({
            'target_id': tid, 'target_class': target['target_class'],
            'frozen_evidence_status': target['evidence_status'],
            'v3b_scenarios_verified': str(len(hit)),
            'v4_directed_evidence': ('CONSUMER_DECISION_PROVED_TWO_CONFIGS' if verified
                                      else 'PRIOR_EVIDENCE_NEEDS_TWO_CONFIG_AUDIT'),
            'automatic_promotion': 'NO',
            'qualification_gate': ('READY_FOR_SCIENTIFIC_REVIEW' if verified
                                   else 'BLOCKED_NEEDS_ADDITIONAL_EVIDENCE')
        })
    return rows


def render_report(status: str, groups: dict, qualification_rows: list[dict],
                  inventory: dict, v3a_hash: str) -> str:
    passed = [r for r in qualification_rows if r['v3b_scenarios_verified'] == '2']
    pending = [r for r in qualification_rows if r['v3b_scenarios_verified'] != '2']
    lines = [
        '# CNN_PEP V4: Cross-Evidence Audit', '',
        '**Audit status:** ' + status, '',
        '> This is a read-only integrity and directed-evidence audit; NOT full400 approval, '
        'NOT a hardware SEU estimate, and NOT target-status promotion.', '',
        f'- V3a manifest SHA256: `{v3a_hash}`',
        f'- CTL inventory: **{inventory["file_count"]}/{inventory["file_count"]}** files, '
        f'SHA256 `{inventory["inventory_sha256"]}`',
        f'- Strict V3b consumer/decision replay: **{sum(groups.values())}/28** scenarios',
        f'- V3b selected targets: **{len(passed)}/14**',
        f'- Selected targets requiring separate evidence review: **{len(pending)}/20**', '',
        '## Group verification', '',
        '| V3b group | Replayed scenarios |', '|---|---:|'
    ]
    for name, count in groups.items():
        lines.append(f'| {name} | {count} |')
    lines += ['', '## Frozen target qualification (NO promotion)', '',
              '| Target | Original evidence status | V3b configurations | V4 decision |',
              '|---|---|---:|---|']
    for r in qualification_rows:
        lines.append(f'| {r["target_id"]} | {r["frozen_evidence_status"]} | '
                     f'{r["v3b_scenarios_verified"]}/2 | {r["qualification_gate"]} |')
    lines += ['', '## Release decision', '',
              '**FULL400 NOT AUTHORIZED.** The six pre-existing non-CANDIDATE targets '
              'need a two-config target-level evidence audit or a documented, approved '
              'qualification exception under a versioned protocol. No frozen target plan '
              'has been changed.', '',
              'Even after this audit, preserved Control10/timing and Vivado source provenance '
              'must remain valid when launching any full campaign. Do not interpret these '
              'directed IMG0000 cases as SEU incidence probabilities.', '']
    return '\n'.join(lines)


def run_audit(repo: Path, results: Path, weights: Path,
              expected_inventory_hash: str = INVENTORY_SHA256) -> dict:
    repo, results, weights = repo.resolve(), results.resolve(), weights.resolve()
    for relative, expected_digest in FROZEN_HASHES.items():
        p = repo / relative
        require(p.is_file() and sha256(p) == expected_digest,
                f'Frozen P0/FI contract altered: {relative}')
    plan = load_plan(repo / 'fi/config/pilot400_targets.csv',
                     repo / 'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(repo / 'fi/scenarios/pilot400.csv', plan)
    candidate = {r['target_id'] for r in plan if r['evidence_status'] == 'CANDIDATE'}
    defined = {t for g in GROUPS for t in g.targets}
    require(len(candidate) == 14 and candidate == defined,
            'V4 frozen candidate set differs from 14 V3b targets')
    require(len(plan) == 20 and len(manifest) == 400,
            'V4 frozen plan/manifest cardinality mismatch')

    validate_control10_gate(repo, results / CONTROL_NAME / 'control10_manifest.json', weights)
    validate_full_timing_gate(repo, results / PROFILE_NAME / 'timing_window_audit.csv',
                              {r['target_id']: r for r in plan})
    v3a_meta, v3a_results = check_v3a(repo, results, plan, manifest)
    inv = check_ctl_inventory(results / 'pilot400_v3b_ctl8_01', expected_inventory_hash)
    groups_seen, all_v3b = {}, {}
    for group in GROUPS:
        folder = results / group.dirname
        _, verified = validate_group_manifest(repo, folder, group, v3a_meta, v3a_results)
        require(not (set(verified) & set(all_v3b)), 'V4 V3b scenario duplicate across groups')
        all_v3b.update(verified)
        groups_seen[group.name] = len(verified)
    require(set(all_v3b) == set(v3a_results) and len(all_v3b) == 28,
            'V4 V3a/V3b scenario coverage mismatch')
    statuses = qualification(plan, all_v3b)
    require(sum(r['v3b_scenarios_verified'] == '2' for r in statuses) == 14,
            'V4 expected 14 two-config candidate proofs')
    require(sum(r['qualification_gate'] == 'BLOCKED_NEEDS_ADDITIONAL_EVIDENCE'
                for r in statuses) == 6, 'V4 expected 6 outstanding pre-existing targets')
    return {
        'schema': 'CNN_PEP_V4_CROSS_EVIDENCE_V1',
        'status': 'CROSS_EVIDENCE_PASS_FULL400_BLOCKED',
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'v3a_manifest_sha256': sha256(results / V3A_NAME / 'validation_manifest.json'),
        'control10_manifest_sha256': sha256(results / CONTROL_NAME / 'control10_manifest.json'),
        'timing_audit_sha256': sha256(results / PROFILE_NAME / 'timing_window_audit.csv'),
        'selected_plan_sha256': sha256(repo / 'fi/config/pilot400_targets.csv'),
        'ctl_inventory': inv,
        'v3b_group_counts': groups_seen,
        'v3a_scenarios_replayed': len(v3a_results),
        'v3b_scenarios_replayed': len(all_v3b),
        'review_ready_candidates': 14,
        'targets_requiring_further_qualification': 6,
        'source_authority': 'vivado',
        'full400_authorized': False,
        'target_status_modified': False,
        'targets': statuses,
        'evidence_source_paths': {g.name: str((results / g.dirname).resolve()) for g in GROUPS},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--results-dir', type=Path, default=DEFAULT_RESULTS)
    parser.add_argument('--weights-dir', type=Path, default=Path('data'))
    parser.add_argument('--out-dir', type=Path,
                        default=Path('fi/results/v4_cross_evidence_audit_01'))
    parser.add_argument('--ctl-inventory-sha256', default=INVENTORY_SHA256,
                        help='Out-of-band pinned SHA256 from successful 97/97 audit')
    args = parser.parse_args()
    repo = args.repo.resolve()
    resolve = lambda p: p.resolve() if p.is_absolute() else (repo / p).resolve()
    results, weights, out = map(resolve, (args.results_dir, args.weights_dir, args.out_dir))
    require(not out.exists(), 'Refusing to overwrite V4 audit output directory: ' + str(out))
    try:
        audit = run_audit(repo, results, weights, args.ctl_inventory_sha256)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, json.JSONDecodeError) as e:
        raise SystemExit('V4_CROSS_EVIDENCE_FAIL: ' + str(e)) from e
    # No output files are created until ALL evidence has passed revalidation.
    out.mkdir(parents=True, exist_ok=False)
    (out / 'v4_audit.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False, sort_keys=True) + '\n',
                                       encoding='utf-8')
    (out / 'v4_audit_report.md').write_text(
        render_report(audit['status'], audit['v3b_group_counts'], audit['targets'],
                      audit['ctl_inventory'], audit['v3a_manifest_sha256']), encoding='utf-8')
    with (out / 'target_qualification.csv').open('w', newline='', encoding='utf-8') as fh:
        fieldnames = list(audit['targets'][0])
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator='\n')
        writer.writeheader()
        writer.writerows(audit['targets'])
    print('V4_CROSS_EVIDENCE_PASS: 28/28 V3a/V3b scenarios; 14/14 candidates consumer/decision reviewed')
    print('V4_CTL_INVENTORY_PASS: 97/97 evidence files, pinned SHA256 verified')
    print('V4_TARGET_QUALIFICATION_OPEN: six pre-existing targets need two-config evidence review')
    print('V4_FULL400_GATE: BLOCKED (no P0/FI source modified, no target automatically promoted)')
    print('V4_OUTPUT:', out)


if __name__ == '__main__':
    main()

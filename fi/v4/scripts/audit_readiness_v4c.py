#!/usr/bin/env python3
"""V4c directed-evidence qualification audit for CNN_PEP Pilot400.

READ-ONLY against historical evidence. Independently replays V4, V4b-A/B,
and V4b-C; checks 40 exact IMG0000 scenario proofs across 20 frozen targets.
Never changes target statuses, never authorizes a 400-run campaign. This is
NOT 400 injections, a fault-rate estimate, or a validated full400 runner.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
FI_SCRIPTS = HERE.parents[1] / 'scripts'
V3B_SCRIPTS = HERE.parents[1] / 'v3b/scripts'
for folder in (HERE, FI_SCRIPTS, V3B_SCRIPTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from audit_v4 import GROUPS, V3A_NAME, read_json, require, unique_index
from audit_v4 import run_audit as replay_v4
from audit_six_consumers_v4b import (
    PROVED, PENDING, COLUMNS as B_COLUMNS,
)
from run_six_v4b import PRE_EXISTING, six_scenarios
from run_three_v4bc import (
    COLUMNS as C_COLUMNS, EXPECTED as C_EXPECTED, SCHEMA as C_SCHEMA,
    SNAPSHOT as C_SNAPSHOT, STATUS as C_STATUS, TARGETS as C_TARGETS,
    TB_REL, parse_consumer, selected_three, validate_frozen_inputs,
    verify_existing_audit,
)
from pilot400_common import (
    FIELDS, SAMPLES, load_plan, read_csv, sha256, validate_assets,
    verify_manifest, write_csv,
)
from run_mem1_v3b import override_prj

SCHEMA = 'CNN_PEP_V4C_DIRECTED_20_TARGET_EVIDENCE_AUDIT_V1'
STATUS = 'TWENTY_TARGETS_TWO_CONFIG_DIRECTED_EVIDENCE_AUDITED'
FULL400_STATUS = 'BLOCKED_PENDING_CAMPAIGN_EXECUTION_QUALIFICATION'
REVIEW_COLUMNS = (
    'target_id', 'target_class', 'frozen_evidence_status',
    'config_a_scenario', 'config_b_scenario', 'config_a_bit', 'config_b_bit',
    'config_a_offset', 'config_b_offset', 'proof_source', 'proof_kind',
    'config_a_outcome', 'config_b_outcome', 'config_a_log_sha256',
    'config_b_log_sha256', 'directed_review', 'automatic_promotion',
    'full400_authorized',
)
HARMFUL = frozenset(('NONCOMPLETION', 'PROTOCOL_VIOLATION', 'WRONG_LABEL'))


def resolved(repo: Path, item: Path) -> Path:
    return item.resolve() if item.is_absolute() else (repo / item).resolve()


def check_coverage(plan: list[dict[str, str]], manifest: list[dict[str, str]],
                   evidence: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    """Validate 20x2 coverage against frozen plan, not an evidence row count alone."""
    chosen = [row for row in manifest if row['sample_id'] == 'IMG0000']
    require(len(plan) == 20 and len(manifest) == 400 and len(chosen) == 40,
            'Expected exactly 20 target plans, 400 manifest rows, 40 IMG0000 rows')
    expected = unique_index(chosen, 'scenario_id', 40, 'V4c IMG0000 selected')
    require(set(evidence) == set(expected),
            'V4c missing/extra/duplicate scenario evidence: ' +
            ','.join(sorted(set(expected) ^ set(evidence))))
    classes = Counter(row['target_class'] for row in plan)
    require(classes == {'CTL': 5, 'BUF': 5, 'MEM': 5, 'DAT': 5},
            'V4c frozen 20-target class design changed')
    table = []
    for target in plan:
        tid = target['target_id']
        if tid in PRE_EXISTING:
            require(target['evidence_status'] == PRE_EXISTING[tid],
                    'V4c unfamiliar frozen historical status: ' + tid)
        else:
            require(target['evidence_status'] == 'CANDIDATE',
                    'V4c unfamiliar frozen candidate status: ' + tid)
        pair = [r for r in chosen if r['target_id'] == tid]
        require(len(pair) == 2, 'V4c two-configuration design missing: ' + tid)
        a, b = pair
        require((a['bit_index'], a['injection_offset']) ==
                (target['bit_index'], target['offset_a']) and
                (b['bit_index'], b['injection_offset']) ==
                (target.get('bit_index_b') or target['bit_index'], target['offset_b']),
                'V4c frozen target bit/time pair changed: ' + tid)
        if tid == 'DAT_0010':
            require((a['bit_index'], a['injection_offset'],
                     b['bit_index'], b['injection_offset']) ==
                    ('0', '1340', '1', '1340'), 'DAT_0010 bit-pair refreeze changed')
        for row in pair:
            ev = evidence[row['scenario_id']]
            require(all(ev[k] == row[k] for k in FIELDS),
                    'V4c verified evidence tuple differs from frozen manifest: ' +
                    row['scenario_id'])
            require(ev['source'] in ('V3B_ISOLATED', 'V4B_B_ORIGINAL', 'V4B_C_ISOLATED') and
                    ev['proof'] and ev['proof'] not in ('NONE', 'NOT_PROVED') and
                    ev['outcome'] in ('FINAL_OUTPUT_MATCH', 'WRONG_LOGITS_SAME_LABEL',
                                      'WRONG_LABEL', 'PROTOCOL_VIOLATION', 'NONCOMPLETION') and
                    len(ev['log_sha256']) == 64,
                    'V4c invalid consumer/protocol proof: ' + row['scenario_id'])
        require(evidence[a['scenario_id']]['source'] == evidence[b['scenario_id']]['source'],
                'V4c two target configs use unexpected mixed provenance: ' + tid)
        src = evidence[a['scenario_id']]['source']
        if target['evidence_status'] == 'CANDIDATE':
            require(src == 'V3B_ISOLATED', 'V4c candidate evidence source wrong: ' + tid)
        elif tid in PROVED:
            require(src == 'V4B_B_ORIGINAL', 'V4c prior consumer source wrong: ' + tid)
        elif tid in PENDING:
            require(src == 'V4B_C_ISOLATED', 'V4c historical monitor source wrong: ' + tid)
        else:
            raise ValueError('V4c unfamiliar frozen target status/source: ' + tid)
        table.append({
            'target_id': tid, 'target_class': target['target_class'],
            'frozen_evidence_status': target['evidence_status'],
            'config_a_scenario': a['scenario_id'], 'config_b_scenario': b['scenario_id'],
            'config_a_bit': a['bit_index'], 'config_b_bit': b['bit_index'],
            'config_a_offset': a['injection_offset'], 'config_b_offset': b['injection_offset'],
            'proof_source': src,
            'proof_kind': evidence[a['scenario_id']]['proof'] + ' | ' +
                          evidence[b['scenario_id']]['proof'],
            'config_a_outcome': evidence[a['scenario_id']]['outcome'],
            'config_b_outcome': evidence[b['scenario_id']]['outcome'],
            'config_a_log_sha256': evidence[a['scenario_id']]['log_sha256'],
            'config_b_log_sha256': evidence[b['scenario_id']]['log_sha256'],
            'directed_review': 'TWO_CONFIG_CONSUMER_DECISION_PROVED',
            'automatic_promotion': 'NO', 'full400_authorized': 'NO',
        })
    require(len(table) == 20 and len({r['target_id'] for r in table}) == 20,
            'V4c target qualification matrix incomplete')
    return table


def check_v4bc(repo: Path, results: Path, v4ba: Path, v4bb_json: Path,
                v4bc: Path, manifest: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Reparse every V4b-C log; do not trust PASS text or saved CSV alone."""
    meta = read_json(v4bc / 'v4bc_manifest.json')
    require(meta.get('schema') == C_SCHEMA and meta.get('status') == C_STATUS and
            meta.get('selected_count') == 6 and
            meta.get('source_authority') == 'vivado' and
            meta.get('target_status_modified') is False and
            meta.get('full400_authorized') is False and
            meta.get('consumer_qualified') is False,
            'V4b-C full six-config completed manifest invalid')
    require(meta.get('isolated_tb_sha256') == sha256(repo / TB_REL) and
            meta.get('original_tb_sha256') == sha256(repo / 'fi/tb/tb_cnn_mnist_fi_pilot400.v') and
            meta.get('v4b_existing_consumer_audit_sha256') == sha256(v4bb_json) and
            meta.get('v4b_a_manifest_sha256') == sha256(v4ba / 'v4b_manifest.json') and
            meta.get('v3a_manifest_sha256') ==
                sha256(results / V3A_NAME / 'validation_manifest.json'),
            'V4b-C evidence parent/source hashes changed')
    source_json = read_json(results / V3A_NAME / 'validation_manifest.json')
    source_hashes = source_json['snapshot_build']['source_hashes']
    require(meta.get('source_hashes') == source_hashes and
            all(Path(src).is_file() and sha256(Path(src)) == digest
                for src, digest in source_hashes.items()),
            'V4b-C Vivado source provenance differs from authenticated V3a')
    prior_prj = (results / V3A_NAME / 'source_vlog.prj').read_text(
        encoding='utf-8', errors='replace')
    build_prj = v4bc / 'isolated_v4bc_build/tb_v4bc_vlog.prj'
    require(build_prj.is_file() and sha256(build_prj) == meta.get('source_prj_sha256') and
            build_prj.read_text(encoding='utf-8', errors='replace') ==
                override_prj(prior_prj, repo / TB_REL),
            'V4b-C compile-source list changed beyond isolated TB replacement')
    for stage in ('xvlog', 'xelab'):
        log = v4bc / (stage + '.log')
        require(log.is_file() and sha256(log) == meta.get(stage + '_sha256') and
                not any(line.startswith(('ERROR:', 'FATAL:'))
                        for line in log.read_text(encoding='utf-8', errors='replace').splitlines()),
                'V4b-C build stage missing/changed/failed: ' + stage)
    require(meta.get('elaboration_mode') in ('FULL_BUILD', 'INCREMENTAL_RELINK'),
            'V4b-C elaboration mode not valid')
    selected = selected_three(manifest)
    sel = unique_index(read_csv(v4bc / 'selected_scenarios.csv', FIELDS),
                       'scenario_id', 6, 'V4b-C selected')
    require(sha256(v4bc / 'selected_scenarios.csv') == meta.get('selected_sha256') and
            sel == {r['scenario_id']: r for r in selected},
            'V4b-C selected file does not match frozen 6 IMG0000 scenarios')
    rows = unique_index(read_csv(v4bc / 'consumer_validation.csv', C_COLUMNS),
                        'scenario_id', 6, 'V4b-C consumer CSV')
    records = unique_index(meta.get('records', []), 'scenario_id', 6,
                           'V4b-C signed manifest records')
    require(sha256(v4bc / 'consumer_validation.csv') == meta.get('consumer_csv_sha256'),
            'V4b-C consumer CSV SHA256 mismatch')
    evidence = {}
    for row in selected:
        sid = row['scenario_id']
        log = v4bc / 'logs' / (sid + '.log')
        rec = records[sid]
        require(log.is_file() and sha256(log) == rec.get('log_sha256') and
                Path(rec.get('log', '')).resolve() == log.resolve() and
                rec.get('target_id') == row['target_id'],
                'V4b-C scenario log/provenance mismatch: ' + sid)
        proof = parse_consumer(log, row, v4ba / 'logs' / (sid + '.log'))
        require(proof == rows[sid] and
                rec.get('consumer_proof') == proof['consumer_proof'] and
                rec.get('outcome') == proof['outcome'],
                'V4b-C strict log replay differs from saved evidence: ' + sid)
        evidence[sid] = {
            **{k:row[k] for k in FIELDS}, 'source': 'V4B_C_ISOLATED',
            'outcome':proof['outcome'], 'proof':proof['consumer_proof'],
            'log_sha256':sha256(log),
        }
    return evidence


def collect_v3b(repo: Path, results: Path, manifest: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    lookup = {r['scenario_id']:r for r in manifest}
    evidence = {}
    for group in GROUPS:
        folder = results / group.dirname
        selected = read_csv(folder / group.selected, FIELDS)
        proof_csv = read_csv(folder / 'consumer_validation.csv',
                             (*FIELDS, 'consumer_proof', 'outcome', 'log_sha256'))
        verified = unique_index(proof_csv, 'scenario_id', 2 * len(group.targets),
                                'V4c/' + group.name)
        require(len(selected) == len(verified) and
                set(verified) == {r['scenario_id'] for r in selected},
                'V4c V3b selected/proof mismatch: ' + group.name)
        for row in selected:
            sid = row['scenario_id']
            require(row == lookup[sid] and sid not in evidence,
                    'V4c duplicate or nonfrozen V3b evidence: ' + sid)
            rec = verified[sid]
            log = folder / 'logs' / (sid + '.log')
            require(log.is_file() and sha256(log) == rec['log_sha256'] and
                    rec['consumer_proof'].startswith('VERIFIED_'),
                    'V4c V3b consumer proof/hash invalid: ' + sid)
            evidence[sid] = {
                **{k:row[k] for k in FIELDS},
                'source':'V3B_ISOLATED', 'outcome':rec['outcome'],
                'proof':rec['consumer_proof'], 'log_sha256':sha256(log),
            }
    require(len(evidence) == 28, 'V4c V3b candidate evidence count must be 28')
    return evidence


def collect_v4bb(v4bb_json: Path, manifest: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    lookup = {r['scenario_id']:r for r in manifest}
    saved = read_json(v4bb_json)
    require(saved.get('direct_consumer_scenarios') == 6 and
            saved.get('direct_consumer_targets') == sorted(PROVED),
            'V4b-B existing consumer metadata invalid')
    source_csv = v4bb_json.parent / 'existing_consumer_evidence.csv'
    require(saved.get('csv_sha256') == sha256(source_csv),
            'V4b-B evidence CSV SHA256 mismatch')
    rows = read_csv(source_csv, B_COLUMNS)
    evidence = {}
    for row in rows:
        tid, sid = row['target_id'], row['scenario_id']
        if tid not in PROVED:
            continue
        require(sid in lookup and
                all(row[k] == lookup[sid][k] for k in FIELDS) and
                row['consumer_evidence'] == PROVED[tid] and
                sid not in evidence and len(row['log_sha256']) == 64,
                'V4b-B source/consumer proof missing or changed: ' + sid)
        evidence[sid] = {
            **{k:row[k] for k in FIELDS},
            'source':'V4B_B_ORIGINAL', 'outcome':row['outcome'],
            'proof':row['consumer_evidence'], 'log_sha256':row['log_sha256'],
        }
    require(len(evidence) == 6, 'V4b-B exactly six consumer scenarios required')
    return evidence


def render_report(matrix: list[dict[str, str]], by_source: dict[str, int],
                  outcome_counts: dict[str, int], manifest_hash: str) -> str:
    lines = [
        '# CNN_PEP V4c Directed Evidence Qualification Audit', '',
        '**Status:** ' + STATUS, '',
        '> Read-only scientific evidence audit for 20 selected RTL target sites,',
        '> 2 directed configurations per target, on IMG0000 ONLY.',
        '> It does not approve full400, does not promote frozen target status,',
        '> and is not a population SEU/failure-rate estimate.', '',
        '- 20/20 selected targets: two scenario-level consumer/control-decision proofs',
        '- 40/40 directed IMG0000 scenario proofs (NOT 400 XSim proofs)',
        '- 14 CANDIDATE targets revalidated from V3a/V3b',
        '- 3 historical targets revalidated from original V4b-A logs via V4b-B',
        '- 3 historical targets revalidated from isolated V4b-C XSim logs',
        '- Frozen Pilot400 400-row manifest retained; Control10 and timing replayed',
        '- Full400: **BLOCKED pending separate 400-campaign execution readiness**',
        f'- Frozen manifest SHA256: `{manifest_hash}`', '',
        '## Evidence origins', '',
        '| Origin | Directed scenarios |', '|---|---:|',
    ]
    for src in ('V3B_ISOLATED','V4B_B_ORIGINAL','V4B_C_ISOLATED'):
        lines.append(f'| {src} | {by_source.get(src,0)} |')
    lines += ['', '## Observed final outcomes for the directed IMG0000 subset', '',
              '| Outcome | Count |', '|---|---:|']
    for kind, count in sorted(outcome_counts.items()):
        lines.append(f'| `{kind}` | {count} |')
    lines += ['', '## 20 target evidence matrix', '',
              '| Target | Class | Original status | A | B | Source |',
              '|---|---|---|---|---|---|']
    for row in matrix:
        lines.append(f'| {row["target_id"]} | {row["target_class"]} | '
                     f'{row["frozen_evidence_status"]} | {row["config_a_scenario"]} | '
                     f'{row["config_b_scenario"]} | {row["proof_source"]} |')
    lines += ['', '## Separate full400 execution readiness requirements', '',
              '1. Qualify a single-build XSim 400-scenario runner against immutable Vivado RTL/TB/source .prj hashes.',
              '2. Test 10-sample fault-free control, full V2 timing gate, plusarg quoting, timeout, scenario ID and log parsing.',
              '3. Test resume/retry behavior with failure injection into the runner itself; never merge stale/corrupt evidence.',
              '4. Require unique output directory, fail-closed outcome parser, per-scenario logs and hashes.',
              '5. Pin command-line arguments, Vivado version, source/TB hashes and golden assets in an execution approval manifest.',
              '6. Execute the frozen 400 only after independently approving that runner and a versioned qualification protocol.',
              '7. Keep outcomes as directed configuration counts; do not report hardware random-upset probabilities.', '',
              '**Release decision: ' + FULL400_STATUS + '.**', '']
    return '\n'.join(lines)


def audit(repo: Path, results: Path, weights: Path, v4_audit: Path,
          v4ba: Path, v4bb_json: Path, v4bc: Path, out: Path) -> dict:
    require(not out.exists(), 'Refusing overwrite of V4c output: ' + str(out))
    validate_frozen_inputs(repo)
    # Full ancestry check replays every original log and V4b-B value/cycle proof.
    existing = verify_existing_audit(repo, results, weights, v4_audit, v4ba, v4bb_json)
    v4 = replay_v4(repo, results, weights)
    require(v4['status'] == 'CROSS_EVIDENCE_PASS_FULL400_BLOCKED' and
            v4['v3b_scenarios_replayed'] == 28 and
            v4['review_ready_candidates'] == 14,
            'V4 cross-evidence replay is not 28/28 PASS')
    plan = load_plan(repo / 'fi/config/pilot400_targets.csv',
                     repo / 'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(repo / 'fi/scenarios/pilot400.csv', plan)
    sample_count, missing = validate_assets(repo / 'fi/pilot400/samples', True)
    require(sample_count == len(SAMPLES) == 10 and not missing,
            'Ten independent frozen Pilot400 golden assets missing')
    sources = [
        collect_v3b(repo, results, manifest),
        collect_v4bb(v4bb_json, manifest),
        check_v4bc(repo, results, v4ba, v4bb_json, v4bc, manifest),
    ]
    evidence = {}
    for source in sources:
        require(not set(source).intersection(evidence),
                'V4c overlapping scenario proofs across historical sources')
        evidence.update(source)
    matrix = check_coverage(plan, manifest, evidence)
    by_source = dict(Counter(item['source'] for item in evidence.values()))
    require(by_source == {'V3B_ISOLATED': 28, 'V4B_B_ORIGINAL': 6,
                          'V4B_C_ISOLATED': 6},
            'V4c target evidence split 28/6/6 changed')
    counts = dict(Counter(item['outcome'] for item in evidence.values()))
    output = {
        'schema': SCHEMA, 'status': STATUS, 'generated_utc': datetime.now(timezone.utc).isoformat(),
        'targets_reviewed': len(matrix), 'directed_img0000_proofs': len(evidence),
        'candidates_reviewed': 14, 'historical_reviewed': 6,
        'source_authority': 'vivado',
        'by_source': by_source, 'by_outcome': counts,
        'manifest_sha256': sha256(repo / 'fi/scenarios/pilot400.csv'),
        'selected_plan_sha256': sha256(repo / 'fi/config/pilot400_targets.csv'),
        'original_tb_sha256': sha256(repo / 'fi/tb/tb_cnn_mnist_fi_pilot400.v'),
        'v4_audit_sha256': sha256(v4_audit),
        'v4b_a_manifest_sha256': sha256(v4ba / 'v4b_manifest.json'),
        'v4b_b_audit_sha256': sha256(v4bb_json),
        'v4b_c_manifest_sha256': sha256(v4bc / 'v4bc_manifest.json'),
        'v4b_c_consumer_sha256': sha256(v4bc / 'consumer_validation.csv'),
        'control10_manifest_sha256': v4['control10_manifest_sha256'],
        'timing_audit_sha256': v4['timing_audit_sha256'],
        'ctl_inventory': v4['ctl_inventory'],
        'manifest_rows': len(manifest), 'sample_count': sample_count,
        'frozen_target_statuses_modified': False,
        'full400_authorized': False, 'full400_gate': FULL400_STATUS,
        'full400_runner_qualified': False,
        'xsim_runs_added': 0,
    }
    require(existing['direct_consumer_scenarios'] == 6 and
            existing['still_pending_targets'] == list(PENDING),
            'V4b-B independent qualification not replayed')
    out.mkdir(parents=True, exist_ok=False)
    write_csv(out / 'v4c_target_evidence_matrix.csv', REVIEW_COLUMNS, matrix)
    output['target_matrix_sha256'] = sha256(out / 'v4c_target_evidence_matrix.csv')
    (out / 'v4c_report.md').write_text(
        render_report(matrix, by_source, counts, output['manifest_sha256']), encoding='utf-8')
    (out / 'v4c_manifest.json').write_text(
        json.dumps(output, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('V4C_DIRECTED_EVIDENCE_PASS: 20/20 targets; 40/40 IMG0000 '
          'consumer/decision scenarios (V3b 28 + V4b-B 6 + V4b-C 6)', flush=True)
    print('V4C_PROVENANCE_PASS: frozen 400 configurations; 10 assets; '
          'original RTL/TB hashes and Control10/timing/V4 ancestry replayed', flush=True)
    print('V4C_FULL400_GATE: BLOCKED (separate 400-runner qualification '
          'and execution approval required; no target auto-promoted)', flush=True)
    print('V4C_OUTPUT: ' + str(out), flush=True)
    return output


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--results-dir', type=Path, default=Path('fi/results'))
    ap.add_argument('--weights-dir', type=Path, default=Path('data'))
    ap.add_argument('--v4-audit', type=Path,
                    default=Path('fi/results/v4_cross_evidence_audit_02/v4_audit.json'))
    ap.add_argument('--v4b-a-dir', type=Path,
                    default=Path('fi/results/v4b_six_mechanism_01'))
    ap.add_argument('--v4b-b-audit', type=Path,
                    default=Path('fi/results/v4b_existing_consumer_reaudit_01/existing_consumer_audit.json'))
    ap.add_argument('--v4b-c-dir', type=Path,
                    default=Path('fi/results/v4bc_three_01'))
    ap.add_argument('--out-dir', type=Path,
                    default=Path('fi/results/v4c_readiness_audit_01'))
    args = ap.parse_args()
    repo = args.repo.resolve()
    find = lambda p: resolved(repo, p)
    try:
        audit(repo, find(args.results_dir), find(args.weights_dir),
              find(args.v4_audit), find(args.v4b_a_dir),
              find(args.v4b_b_audit), find(args.v4b_c_dir), find(args.out_dir))
    except (ValueError, KeyError, OSError, TypeError, RuntimeError) as e:
        raise SystemExit('V4C_AUDIT_FAIL: ' + str(e))


if __name__ == '__main__':
    main()

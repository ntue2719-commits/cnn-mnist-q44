#!/usr/bin/env python3
"""CNN_PEP V4e: read-only cross-stage evidence/release-readiness audit.

Replays V4c/V4d-A/B/C/D provenance, checks 20 x 2 directed IMG0000 proofs,
10 x 40 fault-free temporal windows and 32 distinct canary/shakedown FI runs.
A PASS means evidence review-ready, not a 400-FI campaign, runner approval,
a population SEU probability, or power-loss/mid-XSim recovery proof.

NO XSim is launched. NO frozen file is modified. FULL400 stays BLOCKED.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

HERE = Path(__file__).resolve().parent
FI_SCRIPTS = HERE.parents[1] / 'scripts'
V3B_SCRIPTS = HERE.parents[1] / 'v3b/scripts'
for directory in (HERE, FI_SCRIPTS, V3B_SCRIPTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from audit_v4 import V3A_NAME, read_json, require  # noqa: E402
from pilot400_common import (  # noqa: E402
    FIELDS, load_plan, verify_manifest, validate_assets, read_csv, write_csv,
    sha256,
)
import qualify_runner_v4d as v4da  # noqa: E402
import qualify_resume_v4dc as v4dc  # noqa: E402
import qualify_abrupt_resume_v4dd as v4dd  # noqa: E402

SCHEMA = 'CNN_PEP_V4E_CROSS_STAGE_RELEASE_REVIEW_V1'
STATUS = 'EVIDENCE_REVIEW_READY_NOT_FULL400_AUTHORIZED'
FULL400_GATE = 'BLOCKED_PENDING_FULL400_RUNNER_QUALIFICATION_AND_EXPLICIT_APPROVAL'
MATRIX_COLUMNS = (*FIELDS, 'evidence_stage', 'outcome',
                  'injection_verified', 'baseline_temporal_eligibility',
                  'independent_consumer_proof', 'log_sha256')
STAGES = ('V4D_A', 'V4D_C', 'V4D_D')
STAGE_COUNTS = (16, 8, 8)
STAGE_SAMPLES = (
    ('IMG0000', 'IMG0009'), ('IMG0004', 'IMG0005'),
    ('IMG0002', 'IMG0007'),
)


def resolved(repo: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (repo / value).resolve()


def protect_out(out: Path, protected: list[Path]) -> None:
    require(not out.exists(), 'V4e refuses to overwrite any prior output: ' + str(out))
    for p in protected:
        require(not (out == p or p in out.parents or out in p.parents),
                'V4e output overlaps protected input/evidence: ' + str(p))


def strict_release_meta(meta: dict, prior: dict, pins: dict[str, str],
                        completed: Path, temporal: Path) -> None:
    """A completed V4d-D proof, never a paused/unverified synthetic status."""
    require(meta.get('schema') == v4dd.SCHEMA and
            meta.get('status') == v4dd.STATUS_PASS and
            meta.get('full400_gate') == v4dd.FULL400_GATE and
            meta.get('full400_authorized') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('target_status_modified') is False and
            meta.get('run_scope') == 'V4D_D_EIGHT_FI_INTERRUPTED_CHECKPOINT_ONLY',
            'V4e requires finalized but BLOCKED V4d-D evidence')
    plan = meta.get('termination_plan') or {}
    require(plan == {'mode': 'PYTHON_OS_EXIT_AFTER_COMMITTED_RECORD',
                     'after_n': 3, 'exit_code': v4dd.EXIT_CODE},
            'V4e requires exact 3-record termination plan and exit-code 86')
    builds = meta.get('builds', [])
    records = meta.get('records', [])
    require(len(builds) == 2 and [b.get('build_id') for b in builds] == [1, 2] and
            len(records) == 8 and
            [r.get('build_id') for r in records] == [1] * 3 + [2] * 5,
            'V4e must verify three records on build 1 and five on build 2')
    sig = meta.get('input_signature', {})
    ancestor = prior.get('input_signature', {})
    require(sig.get('pins') == pins and
            sig.get('project') == ancestor.get('project') and
            sig.get('vivado') == ancestor.get('vivado') and
            sig.get('fileset') == ancestor.get('fileset') and
            sig.get('selection_scope') ==
                'FOUR_CLASSES_SECOND_CONFIG_IMG0002_IMG0007' and
            sig.get('v4dc_sha256') == sha256(completed / 'v4dc_resume_manifest.json') and
            sig.get('v4db_sha256') == sha256(temporal / 'v4db_temporal_manifest.json') and
            bool(sig.get('project')),
            'V4e V4d-D input/ancestor signature changed')


def check_v4dd_evidence(out: Path, manifest: dict, selected: list[dict[str, str]],
                         pins: dict[str, str], repo: Path, v3a: Path) -> None:
    """Replay every scenario byte; also verify two build scripts and fixed Tcl."""
    require(read_csv(out / 'selected_scenarios.csv', FIELDS) == selected and
            sha256(out / 'selected_scenarios.csv') == manifest.get('selected_sha256'),
            'V4e V4d-D frozen selection or its hash changed')
    original = read_json(v3a / 'validation_manifest.json')['snapshot_build']
    prj_hash = sha256(v3a / 'source_vlog.prj')
    require(original.get('compile_prj_sha256') == prj_hash and
            isinstance(original.get('source_hashes'), dict) and
            bool(original['source_hashes']),
            'V4e V3a source manifest/.prj pin changed')
    v4dd.replay_records(out, selected, manifest, pins, repo,
                         original['source_hashes'], prj_hash)
    for build in manifest['builds']:
        root = out / f'build_{build["build_id"]:03d}'
        require((root / 'runall.tcl').read_text(encoding='ascii') == 'run all\nquit\n',
                'V4e XSim runall Tcl changed: ' + str(root))
        for stage in ('compile', 'elaborate'):
            script = root / (stage + '_snapshot.cmd')
            wrapper = root / (stage + '_wrapper.log')
            require(script.is_file() and wrapper.is_file() and
                    bool(script.read_bytes().strip()),
                    'V4e required direct-build wrapper missing: ' + str(root))
    require(read_csv(out / 'recovery_validation.csv', v4dd.COLUMNS) ==
            [r['validation'] for r in manifest['records']] and
            sha256(out / 'recovery_validation.csv') ==
                manifest.get('validation_csv_sha256'),
            'V4e V4d-D final recovery CSV or SHA256 changed')


def collect_shakedowns(manifest: list[dict[str, str]],
                       stages: list[tuple[str, dict, list[dict]]]) -> list[dict[str, str]]:
    """Validate exactly 32 frozen scenario tuples, never infer new consumer proof."""
    all_rows = {r['scenario_id']: r for r in manifest}
    require(len(all_rows) == len(manifest) == 400, 'V4e frozen campaign IDs duplicated')
    rows = []
    for (stage, metadata, selected), expected_stage, expected_count, sample_pair in zip(
        stages, STAGES, STAGE_COUNTS, STAGE_SAMPLES
    ):
        require(stage == expected_stage and len(selected) == expected_count and
                len(metadata.get('records', [])) == expected_count,
                'V4e shakedown stage or count changed: ' + stage)
        require(set(r['sample_id'] for r in selected) == set(sample_pair),
                'V4e unexpected samples in ' + stage)
        require(Counter(r['target_class'] for r in selected) ==
                {'CTL': expected_count // 4, 'BUF': expected_count // 4,
                 'MEM': expected_count // 4, 'DAT': expected_count // 4},
                'V4e unbalanced shakedown class: ' + stage)
        require([r['scenario_id'] for r in selected] ==
                [record['scenario_id'] for record in metadata['records']],
                'V4e scenario-record ordering changed: ' + stage)
        for selected_row, record in zip(selected, metadata['records']):
            sid = selected_row['scenario_id']
            require(sid in all_rows and selected_row == all_rows[sid],
                    'V4e saved selection differs from frozen manifest: ' + sid)
            val = record['validation']
            require(all(val.get(k) == selected_row[k] for k in FIELDS) and
                    val.get('injection_verified') == '1' and
                    val.get('consumer_proof_scope') ==
                        'NOT_INDEPENDENTLY_MONITORED_V4D' and
                    val.get('outcome') in (
                        'NONCOMPLETION', 'PROTOCOL_VIOLATION',
                        'WRONG_LABEL', 'WRONG_LOGITS_SAME_LABEL', 'FINAL_OUTPUT_MATCH') and
                    val.get('log_sha256') == record.get('log_sha256') and
                    isinstance(val.get('log_sha256'), str) and
                    len(val['log_sha256']) == 64,
                    'V4e shakedown validation not independently mechanism verified: ' + sid)
            rows.append({**{k: selected_row[k] for k in FIELDS},
                         'evidence_stage': stage,
                         'outcome': val['outcome'],
                         'injection_verified': 'YES',
                         'baseline_temporal_eligibility': 'V4DB_10_SAMPLE_40_WINDOWS_PASS',
                         'independent_consumer_proof': 'NO',
                         'log_sha256': val['log_sha256']})
    require(len(rows) == 32 and len({r['scenario_id'] for r in rows}) == 32,
            'V4e repeated shakedown scenario across stages')
    require(len({r['sample_id'] for r in rows}) == 6,
            'V4e actual FI shakedowns do not span the six planned images')
    dat = [r for r in rows if r['target_id'] == 'DAT_0010']
    require(len(dat) == 8 and all(r['injection_offset'] == '1340' for r in dat) and
            {r['bit_index'] for r in dat} == {'0', '1'},
            'V4e DAT_0010 bit0/bit1@1340 coverage changed')
    return rows


def report_text(meta: dict) -> str:
    return '\n'.join([
        '# CNN_PEP V4e Cross-Stage Evidence Review', '',
        f'- Status: **{meta["status"]}**',
        '- Direct consumer/control-decision proof: **40/40 IMG0000**, **20/20 targets**.',
        '- Fault-free temporal eligibility: **400/400** (10 profiles x 40 checks).',
        '- Independent V4d FI shakedown mechanisms: **32/32**',
        '  (V4d-A 16, V4d-C 8, V4d-D 8); six distinct images.',
        '- **NOT** 400 actual FI runs; mechanism runs do not add consumer proofs.',
        '- V4d-D 3+5 builds/checkpoints reviewed, and prior log SHA256 replayed.',
        '- The recorded 86 exit code was observed in operator console; the',
        '  completed manifest captures the termination PLAN, not an independent',
        '  signed/archived OS process-exit receipt.',
        '- Does not prove recovery from Windows power loss or during XSim.',
        '- 20 frozen evidence_status fields remain unchanged.',
        '- Evidence is REVIEW-READY, not a full campaign runner approval.', '',
        '## Outstanding release requirements', '',
        '1. Implement and independently qualify a full400 (exact 400) runner.',
        '2. Explicit operator review of build-once, per-scenario timeout, watchdog,',
        '   correct input/sample mapping, atomic checkpoints, fail-closed orphans,',
        '   non-overwrite, resume and final hash inventory.',
        '3. Freeze campaign execution command, XSim build/source SHA256 and output',
        '   directory before running. Do not reuse shakedown artifacts as full400.',
        '4. Record explicit review/approval; never infer approval from V4e PASS.',
        '5. Report the 400 directed outcomes without claiming physical FF-SEU',
        '   population rates; distinguish fault injection, fault propagation',
        '   and observed decision/classification effect.', '',
        '**FULL400 GATE: BLOCKED.**', '',
    ])


def run(args: argparse.Namespace) -> dict:
    repo = args.repo.resolve()
    results = resolved(repo, args.results_dir)
    weights = resolved(repo, args.weights_dir)
    v4 = resolved(repo, args.v4_audit)
    v4ba = resolved(repo, args.v4b_a_dir)
    v4bb = resolved(repo, args.v4b_b_audit)
    v4bc = resolved(repo, args.v4b_c_dir)
    v4c = resolved(repo, args.v4c_audit)
    canary = resolved(repo, args.v4d_canary_dir)
    temporal = resolved(repo, args.v4db_dir)
    completed = resolved(repo, args.v4dc_dir)
    abrupt = resolved(repo, args.v4dd_dir)
    out = resolved(repo, args.out_dir)
    protect_out(out, [results / V3A_NAME, v4.parent, v4ba, v4bb.parent, v4bc,
                      v4c.parent, canary, temporal, completed, abrupt,
                      repo / 'fi/config', repo / 'fi/scenarios', repo / 'fi/tb'])

    plan = load_plan(repo / 'fi/config/pilot400_targets.csv',
                     repo / 'fi/config/fi_targets_candidates_v2.csv')
    frozen = verify_manifest(repo / 'fi/scenarios/pilot400.csv', plan)
    require(len(plan) == 20 and len(frozen) == 400,
            'V4e frozen target/manifest row count mismatch')
    count, missing = validate_assets(repo / 'fi/pilot400/samples', True)
    require(count == 10 and not missing, 'V4e requires ten original golden samples')

    prior = v4dd.verify_v4dc_ancestry(repo, results, weights, v4c, v4,
                                      v4ba, v4bb, v4bc, canary, temporal,
                                      completed, plan, frozen)
    saved = read_json(abrupt / 'v4dd_recovery_manifest.json')
    v3a = results / V3A_NAME
    pins = v4dd.input_pins(repo, weights, v4c, canary, temporal,
                           completed, v3a / 'validation_manifest.json')
    strict_release_meta(saved, prior, pins, completed, temporal)
    selected_abrupt = v4dd.selection(frozen, plan)
    check_v4dd_evidence(abrupt, saved, selected_abrupt, pins, repo, v3a)

    canary_meta = read_json(canary / 'v4d_canary_manifest.json')
    completed_meta = read_json(completed / 'v4dc_resume_manifest.json')
    from qualify_temporal_v4db import selection as temporal_selection
    from qualify_runner_v4d import canary_selection
    selected_canary = canary_selection(frozen)
    selected_completed = v4dc.selection(frozen, plan)
    rows = collect_shakedowns(frozen, [
        ('V4D_A', canary_meta, selected_canary),
        ('V4D_C', completed_meta, selected_completed),
        ('V4D_D', saved, selected_abrupt),
    ])
    require(len(temporal_selection(frozen)) == 10,
            'V4e ten baseline profile anchors missing')
    counts = dict(Counter(r['outcome'] for r in rows))
    pins_evidence = {}
    for stage, path in (
        ('V4', v4), ('V4C', v4c),
        ('V4D_A', canary / 'v4d_canary_manifest.json'),
        ('V4D_B', temporal / 'v4db_temporal_manifest.json'),
        ('V4D_C', completed / 'v4dc_resume_manifest.json'),
        ('V4D_D', abrupt / 'v4dd_recovery_manifest.json'),
    ):
        require(path.is_file(), 'V4e stage manifest missing: ' + str(path))
        pins_evidence[stage] = {'path': str(path), 'sha256': sha256(path)}
    meta = {
        'schema': SCHEMA, 'status': STATUS,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'review_scope': 'READ_ONLY_CROSS_STAGE_EVIDENCE_NOT_EXECUTION_APPROVAL',
        'directed_proved_img0000': 40, 'directed_targets_proved': 20,
        'baseline_timing_checks': 400, 'baseline_profiles': 10,
        'canary_mechanism_runs': 16, 'graceful_resume_mechanism_runs': 8,
        'abrupt_checkpoint_mechanism_runs': 8, 'shakedown_mechanism_runs': 32,
        'distinct_shakedown_scenarios': 32, 'distinct_shakedown_samples': 6,
        'shakedown_outcomes': counts,
        'v4dd_termination_receipt_independently_archived': False,
        'v4dd_checkpoint_plan': saved['termination_plan'],
        'mid_xsim_recovery_proved': False,
        'power_loss_recovery_proved': False,
        'full400_fi_scenarios_executed': False,
        'full400_authorized': False,
        'full400_runner_qualified': False,
        'target_status_modified': False,
        'full400_gate': FULL400_GATE,
        'frozen_plan_sha256': sha256(repo / 'fi/config/pilot400_targets.csv'),
        'frozen_manifest_sha256': sha256(repo / 'fi/scenarios/pilot400.csv'),
        'original_fi_tb_sha256': sha256(repo / 'fi/tb/tb_cnn_mnist_fi_pilot400.v'),
        'parents': pins_evidence,
    }
    # Never create V4e artifacts until ALL historical evidence has replayed.
    out.mkdir(parents=True, exist_ok=False)
    write_csv(out / 'v4e_shakedown_matrix.csv', MATRIX_COLUMNS, rows)
    meta['shakedown_matrix_sha256'] = sha256(out / 'v4e_shakedown_matrix.csv')
    (out / 'v4e_report.md').write_text(report_text(meta), encoding='utf-8')
    (out / 'v4e_release_review.json').write_text(
        json.dumps(meta, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('V4E_ANCESTRY_PASS: V4c + V4d-A/B/C/D source/log/checkpoint replayed')
    print('V4E_SCOPE_PASS: 20/20 targets, 40/40 IMG0000 consumer/decision; '
          '10/10 fault-free profiles, 400/400 baseline temporal windows; '
          '32/32 independent V4d mechanisms (not full400)')
    print('V4E_RELEASE_GATE: BLOCKED (runner/full400 approval absent; '
          'no FI run, no target promotion)')
    print('V4E_OUTPUT: ' + str(out))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--weights-dir', type=Path, default=Path('data'))
    ap.add_argument('--results-dir', type=Path, default=Path('fi/results'))
    ap.add_argument('--v4-audit', type=Path,
                    default=Path('fi/results/v4_cross_evidence_audit_02/v4_audit.json'))
    ap.add_argument('--v4b-a-dir', type=Path,
                    default=Path('fi/results/v4b_six_mechanism_01'))
    ap.add_argument('--v4b-b-audit', type=Path,
                    default=Path('fi/results/v4b_existing_consumer_reaudit_01/existing_consumer_audit.json'))
    ap.add_argument('--v4b-c-dir', type=Path,
                    default=Path('fi/results/v4bc_three_01'))
    ap.add_argument('--v4c-audit', type=Path,
                    default=Path('fi/results/v4c_readiness_audit_01/v4c_manifest.json'))
    ap.add_argument('--v4d-canary-dir', type=Path,
                    default=Path('fi/results/v4d_canary_01'))
    ap.add_argument('--v4db-dir', type=Path,
                    default=Path('fi/results/v4db_temporal_direct_02'))
    ap.add_argument('--v4dc-dir', type=Path,
                    default=Path('fi/results/v4dc_resume_01'))
    ap.add_argument('--v4dd-dir', type=Path,
                    default=Path('fi/results/v4dd_abrupt_recovery_01'))
    ap.add_argument('--out-dir', type=Path,
                    default=Path('fi/results/v4e_release_review_01'))
    args = ap.parse_args()
    try:
        run(args)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        raise SystemExit('V4E_RELEASE_REVIEW_FAIL: ' + str(error))


if __name__ == '__main__':
    main()

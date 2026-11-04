#!/usr/bin/env python3
"""CNN_PEP V4d-C: independent two-stage XSim resume shakedown (8 FI only).

Stage 1 runs four directed FI scenarios on IMG0004; stage 2 is a separate
Python invocation, rechecks ALL source/evidence pins, builds a new isolated
snapshot, re-parses the four retained logs and runs four IMG0005 scenarios.

This is an operational runner qualification, NOT full Pilot400 authorization,
not a sudden-power-loss recovery proof, and not population SEU inference.
Never mutates synthesizable RTL, the original FI testbench or frozen plans.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
FI_SCRIPTS = HERE.parents[1] / 'scripts'
V3B_SCRIPTS = HERE.parents[1] / 'v3b/scripts'
for directory in (HERE, FI_SCRIPTS, V3B_SCRIPTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from audit_v4 import V3A_NAME, read_json, require  # noqa: E402
from pilot400_common import (FIELDS, SAMPLES, load_plan, verify_manifest,
                             validate_assets, read_csv, write_csv, sha256)  # noqa: E402
from pilot400_direct_xsim import make_cmd_run, call_cmd  # noqa: E402
from pinned_direct_snapshot_v4db import build_pinned_direct_snapshot  # noqa: E402
import qualify_runner_v4d as v4d  # noqa: E402
import qualify_temporal_v4db as v4db  # noqa: E402

SCHEMA = 'CNN_PEP_V4D_C_TWO_STAGE_RESUME_SHAKEDOWN_V1'
STATUS_PAUSED = 'STAGE1_FOUR_XSIM_VERIFIED_PAUSED_FOR_RESUME'
STATUS_PASS = 'EIGHT_XSIM_TWO_STAGE_RESUME_VERIFIED_FULL400_BLOCKED'
FULL400_GATE = 'BLOCKED_SEPARATE_FULL400_RELEASE_REQUIRED'
SAMPLES_USED = ('IMG0004', 'IMG0005')
TARGETS = ('CTL_0001', 'BUF_0031', 'MEM_0002', 'DAT_0010')
SCENARIOS = 8
COLUMNS = v4d.COLUMNS


def resolved(repo: Path, item: Path) -> Path:
    return item.resolve() if item.is_absolute() else (repo / item).resolve()


def selection(manifest: list[dict[str, str]], plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Select first frozen configuration for four classes on two NEW images."""
    plan_by_target = {r['target_id']: r for r in plan}
    output = []
    for sample in SAMPLES_USED:
        for target in TARGETS:
            p = plan_by_target[target]
            matches = [r for r in manifest if r['sample_id'] == sample and
                       r['target_id'] == target]
            require(len(matches) == 2, 'V4d-C requires two frozen rows per target/sample')
            first = next((r for r in matches if
                          r['bit_index'] == p['bit_index'] and
                          r['injection_offset'] == p['offset_a']), None)
            require(first is not None, 'V4d-C frozen first configuration changed: ' + target)
            require(sum(r['scenario_id'] == first['scenario_id'] for r in manifest) == 1,
                    'V4d-C ambiguous scenario ID')
            output.append(first)
    require(len(output) == SCENARIOS and
            len({r['scenario_id'] for r in output}) == SCENARIOS and
            Counter(r['sample_id'] for r in output) == {'IMG0004': 4, 'IMG0005': 4} and
            Counter(r['target_class'] for r in output) ==
            {'CTL': 2, 'BUF': 2, 'MEM': 2, 'DAT': 2},
            'V4d-C shakedown coverage mismatch')
    return output


def verify_v4db(repo: Path, results: Path, weights: Path,
                 v4c: Path, v4: Path, v4ba: Path, v4bb: Path, v4bc: Path,
                 canary: Path, temporal: Path,
                 plan: list[dict[str, str]], manifest: list[dict[str, str]]) -> dict[str, Any]:
    """Replay every V4d-B raw profile and its V4d-A/V4c ancestry, read-only."""
    v4db.verify_canary(repo, results, weights, v4c, v4, v4ba, v4bb,
                       v4bc, canary, plan, manifest)
    path = temporal / 'v4db_temporal_manifest.json'
    data = read_json(path)
    require(data.get('schema') == v4db.SCHEMA and
            data.get('status') == v4db.STATUS and
            data.get('full400_authorized') is False and
            data.get('full400_runner_qualified') is False and
            data.get('target_status_modified') is False and
            data.get('full400_gate') == v4db.FULL400_GATE and
            data.get('profile_scope') == 'TEN_FAULT_FREE_V2_XSIM_PROFILES_ONLY' and
            data.get('baseline_temporal_windows_passed') == 400 and
            len(data.get('records', [])) == 10 and
            bool(data.get('builds')),
            'V4d-B temporal manifest incomplete, changed or not blocked')
    require(data.get('input_signature', {}).get('build_mode') == 'pinned-direct-prj',
            'V4d-C requires verified pinned-direct-prj temporal build')
    require(data.get('v4d_canary_sha256') == sha256(canary / 'v4d_canary_manifest.json')
            and data.get('v4c_audit_sha256') == sha256(v4c),
            'V4d-B parent manifests changed')
    pins = v4db.source_pins(repo, weights, v4c, canary,
                            results / V3A_NAME / 'validation_manifest.json')
    require(data['input_signature'].get('pins') == pins,
            'V4d-B original source lock changed')
    anchors = v4db.selection(manifest)
    require(read_csv(temporal / 'profile_anchors.csv', FIELDS) == anchors and
            data.get('selected_sha256') == sha256(temporal / 'profile_anchors.csv'),
            'V4d-B profile anchors changed')
    v4db.verify_existing_records(temporal, anchors, data['records'], pins, plan, repo)
    summaries = [r['summary'] for r in data['records']]
    matrices = [row for record in data['records'] for row in record['target_rows']]
    require(len(summaries) == 10 and len(matrices) == 200 and
            all(r['eligible_windows'] == '40' for r in summaries),
            'V4d-B ten-sample temporal matrix does not contain 400 eligible configs')
    require(read_csv(temporal / 'sample_temporal_summary.csv',
                     v4db.PROFILE_SUMMARY_COLUMNS) == summaries and
            data.get('temporal_summary_sha256') ==
                sha256(temporal / 'sample_temporal_summary.csv'),
            'V4d-B summary CSV or hash changed')
    require(read_csv(temporal / 'per_sample_timing_matrix.csv',
                     v4db.PROFILE_MATRIX_COLUMNS) == matrices and
            data.get('timing_matrix_sha256') ==
                sha256(temporal / 'per_sample_timing_matrix.csv'),
            'V4d-B sample timing matrix CSV or hash changed')
    source = read_json(results / V3A_NAME / 'validation_manifest.json')
    original = source['snapshot_build']['source_hashes']
    prj_hash = sha256(results / V3A_NAME / 'source_vlog.prj')
    require(all(b.get('source_hashes') == original and
                b.get('compile_prj_sha256') == prj_hash and
                b.get('build_mode') == 'pinned-direct-prj' for b in data['builds']),
            'V4d-B build source differs from V3a authenticated RTL')
    return data


def source_pins(repo: Path, weights: Path, v4c: Path, canary: Path,
                temporal: Path, v3a: Path) -> dict[str, str]:
    pins = v4db.source_pins(repo, weights, v4c, canary, v3a)
    for path in (HERE / 'qualify_resume_v4dc.py',
                 temporal / 'v4db_temporal_manifest.json',
                 temporal / 'sample_temporal_summary.csv',
                 temporal / 'per_sample_timing_matrix.csv'):
        require(path.is_file(), 'Missing V4d-C locked input: ' + str(path))
        pins[str(path.resolve())] = sha256(path)
    return pins


def result_from_log(log: Path, row: dict[str, str]) -> dict[str, str]:
    parsed = v4d.strict_result(log, row)
    parsed['temporal_evidence_scope'] = 'V4DB_TEN_SAMPLE_BASELINE_ELIGIBILITY'
    require(parsed['injection_verified'] == '1', 'V4d-C injection not verified')
    require(parsed['consumer_proof_scope'] == 'NOT_INDEPENDENTLY_MONITORED_V4D',
            'V4d-C mechanism shakedown cannot claim consumer propagation')
    return parsed


def verify_run_records(out: Path, selected: list[dict[str, str]], meta: dict,
                       pins: dict[str, str], repo: Path, source_hashes: dict[str, str],
                       prj_hash: str) -> None:
    records = meta['records']
    require(len(records) <= SCENARIOS and
            [r.get('scenario_id') for r in records] ==
            [r['scenario_id'] for r in selected[:len(records)]],
            'V4d-C records are not a contiguous scenario prefix')
    require(len(meta.get('builds', [])) <= 2, 'Too many V4d-C snapshot builds')
    for build in meta.get('builds', []):
        index = build['build_id']
        b = out / f'build_{index:03d}'
        require(b.is_dir() and
                build['compile_prj_sha256'] == prj_hash and
                build['source_hashes'] == source_hashes,
                'V4d-C prior snapshot source provenance mismatch')
        for key, file in (('compile_log_sha256', 'compile_snapshot.log'),
                          ('elaborate_log_sha256', 'elaborate_snapshot.log')):
            p = b / file
            require(p.is_file() and sha256(p) == build[key],
                    'V4d-C prior snapshot log changed: ' + str(p))
        require(sha256(b / 'source_vlog.prj') == prj_hash,
                'V4d-C saved compile .prj changed')
    runs = out / 'runs'
    logged = {r['scenario_id'] for r in records}
    if runs.exists():
        present = {p.stem for p in runs.glob('FI_P400_*.log')
                   if not p.name.endswith(('.console.log', '.wrapper.log'))}
        require(logged == present, 'V4d-C orphan, missing or partial XSim log')
    for record, row in zip(records, selected):
        sid = row['scenario_id']
        require(record.get('build_id') in
                {b['build_id'] for b in meta.get('builds', [])},
                'V4d-C record references missing build')
        for key, ext in (('log_sha256', '.log'),
                         ('console_sha256', '.console.log'),
                         ('wrapper_sha256', '.wrapper.log'),
                         ('cmd_sha256', '.cmd')):
            p = runs / (sid + ext)
            require(p.is_file() and sha256(p) == record.get(key),
                    'V4d-C recorded artifact missing/changed: ' + str(p))
        require(record['validation'] == result_from_log(runs / (sid + '.log'), row),
                'V4d-C previous FI log does not replay: ' + sid)
    v4d.check_input_lock(pins)


def report_text(meta: dict) -> str:
    return '\n'.join([
        '# CNN_PEP V4d-C Two-Stage Resume Shakedown', '',
        f'- Status: **{meta["status"]}**',
        f'- FI runs confirmed: **{len(meta["records"])}/8**',
        '- Target selection: CTL_0001, BUF_0031, MEM_0002, DAT_0010.',
        '- Samples: IMG0004 (stage 1), IMG0005 (stage 2).',
        '- Each stage builds a *new* verified XSim snapshot from frozen V3a .prj.',
        '- Stage 2 re-parses prior logs, checks pins, and does not overwrite them.',
        '- This proves graceful two-invocation resume; NOT power-loss/crash recovery.',
        '- Eight new FI runs are a runner shakedown, NOT the full 400.',
        '- Timing: V4d-B fault-free profiles of all ten samples were replayed.',
        '- Consumer: NOT independently monitored in these eight mechanism runs.',
        '- No RTL, original TB, target plan or manifest changes.',
        '- FULL400 remains **BLOCKED** pending separate release decision.', ''
    ])


def run(args: argparse.Namespace) -> dict[str, Any]:
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
    out = resolved(repo, args.out_dir)
    project = resolved(repo, args.project) if args.project else None
    stage = args.stage
    require(stage in ('dry-run', 'first', 'resume'), 'Invalid V4d-C stage')
    require((stage == 'dry-run') == (not args.execute),
            'V4d-C first/resume require --execute; dry-run must not execute')
    require(args.timeout_seconds >= 10, 'V4d-C timeout must be >=10 seconds')
    if stage == 'resume':
        require(out.is_dir(), 'V4d-C resume requires existing result directory')
    else:
        require(not out.exists(), 'V4d-C refuses to overwrite prior result directory')
    for protected in (temporal, canary, results / V3A_NAME, v4c.parent, v4ba, v4bc):
        require(not (protected == out or protected in out.parents),
                'V4d-C may not write inside protected evidence: ' + str(protected))
    plan = load_plan(repo / 'fi/config/pilot400_targets.csv',
                     repo / 'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(repo / 'fi/scenarios/pilot400.csv', plan)
    require(len(plan) == 20 and len(manifest) == 400,
            'V4d-C frozen target/manifests cardinality mismatch')
    count, missing = validate_assets(repo / 'fi/pilot400/samples', True)
    require(count == 10 and not missing, 'V4d-C requires 10 golden assets')
    selected = selection(manifest, plan)
    prior = verify_v4db(repo, results, weights, v4c, v4, v4ba, v4bb, v4bc,
                        canary, temporal, plan, manifest)
    v3a = results / V3A_NAME
    pins = source_pins(repo, weights, v4c, canary, temporal,
                       v3a / 'validation_manifest.json')
    source_hashes = read_json(v3a / 'validation_manifest.json')['snapshot_build']['source_hashes']
    prj_hash = sha256(v3a / 'source_vlog.prj')
    if args.execute:
        require(project is not None and project.is_file(),
                'V4d-C --execute requires --project .xpr')
        require(bool(args.vivado), 'V4d-C --execute requires Vivado binary')
        require(prior['input_signature'].get('project') == str(project),
                'V4d-C Vivado project differs from completed temporal profiles')
    signature = {'pins': pins, 'project': str(project) if project else None,
                 'vivado': str(args.vivado), 'fileset': args.fileset,
                 'stage_scope': 'IMG0004_THEN_IMG0005_EIGHT_SCENARIOS',
                 'temporal_sha256': sha256(temporal / 'v4db_temporal_manifest.json'),
                 'v4c_sha256': sha256(v4c)}
    meta_file = out / 'v4dc_resume_manifest.json'
    if stage == 'resume':
        meta = read_json(meta_file)
        require(meta.get('schema') == SCHEMA and
                meta.get('status') == STATUS_PAUSED and
                meta.get('input_signature') == signature and
                meta.get('full400_authorized') is False and
                meta.get('full400_runner_qualified') is False and
                meta.get('target_status_modified') is False,
                'V4d-C resume signature/status mismatch')
        require(sha256(out / 'selected_scenarios.csv') == meta.get('selected_sha256') and
                read_csv(out / 'selected_scenarios.csv', FIELDS) == selected,
                'V4d-C selected scenarios changed')
        require(len(meta['records']) == 4 and len(meta['builds']) == 1,
                'V4d-C resume requires exactly four completed runs and one build')
        require(meta['records'][0]['scenario_id'] == selected[0]['scenario_id'],
                'V4d-C first scenario changed')
        verify_run_records(out, selected, meta, pins, repo, source_hashes, prj_hash)
        require(read_csv(out / 'shakedown_validation.csv', COLUMNS) ==
                [r['validation'] for r in meta['records']] and
                meta.get('validation_csv_sha256') == sha256(out / 'shakedown_validation.csv'),
                'V4d-C previous validation CSV changed')
    else:
        out.mkdir(parents=True, exist_ok=False)
        write_csv(out / 'selected_scenarios.csv', FIELDS, selected)
        meta = {'schema': SCHEMA, 'status': 'DRY_RUN_ONLY',
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'input_signature': signature,
                'selected_sha256': sha256(out / 'selected_scenarios.csv'),
                'records': [], 'builds': [],
                'run_scope': 'V4D_C_EIGHT_FI_ONLY',
                'full400_authorized': False,
                'full400_runner_qualified': False,
                'target_status_modified': False,
                'full400_gate': FULL400_GATE}
        v4d.atomic_write_json(meta_file, meta)
    try:
        if args.execute:
            assert project is not None
            meta['status'] = 'RUNNING'
            v4d.atomic_write_json(meta_file, meta)
            build_id = 1 if stage == 'first' else 2
            build_dir = out / f'build_{build_id:03d}'
            require(not build_dir.exists(), 'V4d-C refuses to overwrite snapshot build')
            build_dir.mkdir()
            snap = build_pinned_direct_snapshot(repo, project, args.vivado,
                                                args.fileset, build_dir,
                                                args.timeout_seconds, v3a)
            require(snap['source_hashes'] == source_hashes and
                    snap['compile_prj_sha256'] == prj_hash and
                    snap['elaboration_mode'] == 'FULL_BUILD',
                    'V4d-C snapshot source differs from V3a verified RTL')
            meta['builds'].append({'build_id': build_id,
                'source_hashes': snap['source_hashes'],
                'compile_prj_sha256': snap['compile_prj_sha256'],
                'compile_log_sha256': snap['compile_log_sha256'],
                'elaborate_log_sha256': snap['elaborate_log_sha256']})
            v4d.atomic_write_json(meta_file, meta)
            tcl = build_dir / 'runall.tcl'
            tcl.write_text('run all\nquit\n', encoding='ascii')
            runs = out / 'runs'
            runs.mkdir(exist_ok=True)
            lo, hi = (0, 4) if stage == 'first' else (4, 8)
            for i, row in enumerate(selected[lo:hi], lo + 1):
                v4d.check_input_lock(pins)
                require(all(Path(p).is_file() and sha256(Path(p)) == h
                            for p, h in source_hashes.items()),
                        'V4d-C Vivado RTL changed before scenario')
                sid = row['scenario_id']
                sample = repo / 'fi/pilot400/samples' / row['sample_id']
                log = runs / (sid + '.log')
                console = runs / (sid + '.console.log')
                wrapper = runs / (sid + '.wrapper.log')
                cmd = runs / (sid + '.cmd')
                require(all(not p.exists() for p in (log, console, wrapper, cmd)),
                        'V4d-C refuses to overwrite scenario evidence: ' + sid)
                cmd.write_text(make_cmd_run(snap['simdir'], snap['vivado_root'],
                                            row, sample, tcl, log, console,
                                            profile_only=False), encoding='ascii')
                print(f'V4DC_XSIM {i}/8 {sid} {row["sample_id"]} '
                      f'{row["target_id"]} bit={row["bit_index"]} '
                      f'offset={row["injection_offset"]}', flush=True)
                call_cmd(cmd, snap['simdir'], wrapper, args.timeout_seconds,
                         'V4d-C shakedown ' + sid)
                require(log.is_file() and console.is_file(),
                        'V4d-C missing FI log/console for ' + sid)
                text = console.read_text(encoding='utf-8', errors='replace')
                require('Expected a switch' not in text and
                        not any(line.startswith(('ERROR:', 'FATAL:'))
                                for line in text.splitlines()),
                        'V4d-C XSim console error: ' + sid)
                parsed = result_from_log(log, row)
                v4d.check_input_lock(pins)
                require(all(Path(p).is_file() and sha256(Path(p)) == h
                            for p, h in source_hashes.items()),
                        'V4d-C Vivado RTL changed during ' + sid)
                meta['records'].append({'scenario_id': sid,
                    'validation': parsed, 'build_id': build_id,
                    'log_sha256': sha256(log),
                    'console_sha256': sha256(console),
                    'wrapper_sha256': sha256(wrapper),
                    'cmd_sha256': sha256(cmd)})
                v4d.atomic_write_json(meta_file, meta)
                print('V4DC_SCENARIO_MECHANISM_PASS: ' + sid + ' ' + parsed['outcome'],
                      flush=True)
            verify_run_records(out, selected, meta, pins, repo, source_hashes, prj_hash)
            meta['status'] = STATUS_PAUSED if stage == 'first' else STATUS_PASS
        else:
            print('V4DC_DRY_RUN_PASS: 8 frozen FI configurations; '
                  'V4d-B 10/10 and V4c/V4d-A ancestry replayed; '
                  '0 XSim; FULL400 BLOCKED', flush=True)
        rows = [r['validation'] for r in meta['records']]
        write_csv(out / 'shakedown_validation.csv', COLUMNS, rows)
        meta['validation_csv_sha256'] = sha256(out / 'shakedown_validation.csv')
        (out / 'v4dc_report.md').write_text(report_text(meta), encoding='utf-8')
        v4d.atomic_write_json(meta_file, meta)
        if args.execute:
            if stage == 'first':
                print('V4DC_STAGE1_PASS: 4/8 XSim FI verified; '
                      'PAUSED_FOR_RESUME; FULL400 BLOCKED', flush=True)
            else:
                print('V4DC_RESUME_PASS: 8/8 independent XSim FI verified; '
                      'two-invocation resume with pinned prior logs; '
                      'FULL400 BLOCKED', flush=True)
    except Exception:
        meta['status'] = 'INTERRUPTED_OR_INVALID'
        v4d.atomic_write_json(meta_file, meta)
        raise
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--project', type=Path)
    ap.add_argument('--vivado', type=str, default='vivado')
    ap.add_argument('--fileset', default='sim_1')
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
    ap.add_argument('--out-dir', type=Path,
                    default=Path('fi/results/v4dc_resume_shakedown_01'))
    ap.add_argument('--timeout-seconds', type=int, default=900)
    ap.add_argument('--stage', choices=('dry-run', 'first', 'resume'),
                    default='dry-run')
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    try:
        run(args)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
        raise SystemExit('V4DC_SHAKEDOWN_FAIL: ' + str(e))


if __name__ == '__main__':
    main()

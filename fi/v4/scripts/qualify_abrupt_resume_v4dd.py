#!/usr/bin/env python3
"""CNN_PEP V4d-D: process-termination-at-checkpoint XSim recovery shakedown.

Scope: exactly eight FROZEN FI configurations (second config of four classes
on IMG0002 and IMG0007), fresh pinned direct XSim build per invocation.

The first invocation may intentionally terminate Python with os._exit(86)
AFTER N complete, independently parsed FI records are atomically checkpointed.
A new process can --resume after read-only revalidation of all preserved logs,
input pins, V4d-C and earlier evidence, and original Vivado source hashes.

This DOES NOT demonstrate recovery from a kill during XSim / between artifact
creation and checkpoint, nor power-loss durability, and does not authorize
full Pilot400. Unrecorded/partial scenario artifacts are never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import os
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
from pilot400_common import (FIELDS, load_plan, verify_manifest, validate_assets,
                             read_csv, write_csv, sha256)  # noqa: E402
from pilot400_direct_xsim import make_cmd_run, call_cmd  # noqa: E402
from pinned_direct_snapshot_v4db import build_pinned_direct_snapshot  # noqa: E402
import qualify_runner_v4d as v4d  # noqa: E402
import qualify_resume_v4dc as v4dc  # noqa: E402

SCHEMA = 'CNN_PEP_V4D_D_ABRUPT_CHECKPOINT_RECOVERY_V1'
STATUS_PASS = 'EIGHT_XSIM_POST_PROCESS_TERMINATION_RECOVERY_VERIFIED_FULL400_BLOCKED'
FULL400_GATE = 'BLOCKED_SEPARATE_FULL400_RELEASE_REQUIRED'
SAMPLES = ('IMG0002', 'IMG0007')
TARGETS = ('CTL_0001', 'BUF_0031', 'MEM_0002', 'DAT_0010')
N_RUNS = 8
EXIT_CODE = 86
COLUMNS = v4dc.COLUMNS
ARTIFACT_EXTENSIONS = ('.log', '.console.log', '.wrapper.log', '.cmd')


def resolved(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def selection(manifest: list[dict[str, str]], plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Use second frozen configuration, exercising DAT_0010 bit1@1340."""
    by_target = {r['target_id']: r for r in plan}
    selected = []
    for sample in SAMPLES:
        for target in TARGETS:
            matches = [r for r in manifest if r['sample_id'] == sample and
                       r['target_id'] == target]
            require(len(matches) == 2, 'V4d-D requires exactly two frozen configurations')
            p = by_target[target]
            chosen = [r for r in matches if
                      r['bit_index'] == p['bit_index_b'] and
                      r['injection_offset'] == p['offset_b']]
            require(len(chosen) == 1,
                    'V4d-D second bit/offset mismatch: ' + sample + '/' + target)
            selected.append(chosen[0])
    require(len(selected) == N_RUNS and
            len({r['scenario_id'] for r in selected}) == N_RUNS and
            Counter(r['sample_id'] for r in selected) == {'IMG0002': 4, 'IMG0007': 4} and
            Counter(r['target_class'] for r in selected) ==
            {'CTL': 2, 'BUF': 2, 'MEM': 2, 'DAT': 2},
            'V4d-D selected eight configurations changed')
    return selected


def verify_v4dc_ancestry(repo: Path, results: Path, weights: Path,
                          v4c: Path, v4: Path, v4ba: Path, v4bb: Path,
                          v4bc: Path, canary: Path, temporal: Path,
                          completed: Path, plan: list[dict[str, str]],
                          manifest: list[dict[str, str]]) -> dict[str, Any]:
    """Replay V4d-B 400-window and V4d-C eight-log evidence without mutation."""
    prior = v4dc.verify_v4db(repo, results, weights, v4c, v4, v4ba, v4bb,
                             v4bc, canary, temporal, plan, manifest)
    meta = read_json(completed / 'v4dc_resume_manifest.json')
    require(meta.get('schema') == v4dc.SCHEMA and
            meta.get('status') == v4dc.STATUS_PASS and
            meta.get('full400_authorized') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('target_status_modified') is False and
            meta.get('full400_gate') == v4dc.FULL400_GATE and
            len(meta.get('records', [])) == 8 and len(meta.get('builds', [])) == 2,
            'V4d-D requires completed and blocked V4d-C two-invocation evidence')
    prior_pins = v4dc.source_pins(repo, weights, v4c, canary, temporal,
                                  results / V3A_NAME / 'validation_manifest.json')
    require(meta.get('input_signature', {}).get('pins') == prior_pins and
            meta['input_signature'].get('temporal_sha256') ==
                sha256(temporal / 'v4db_temporal_manifest.json') and
            meta['input_signature'].get('v4c_sha256') == sha256(v4c) and
            meta['input_signature'].get('project') ==
                prior.get('input_signature', {}).get('project'),
            'V4d-C source/temporal ancestry signature mismatch')
    old_selection = v4dc.selection(manifest, plan)
    csv_file = completed / 'selected_scenarios.csv'
    require(read_csv(csv_file, FIELDS) == old_selection and
            sha256(csv_file) == meta.get('selected_sha256'),
            'V4d-C eight frozen scenario selection changed')
    v3a = results / V3A_NAME
    source_hashes = read_json(v3a / 'validation_manifest.json')['snapshot_build']['source_hashes']
    prj_hash = sha256(v3a / 'source_vlog.prj')
    v4dc.verify_run_records(completed, old_selection, meta, prior_pins,
                            repo, source_hashes, prj_hash)
    validations = [r['validation'] for r in meta['records']]
    path = completed / 'shakedown_validation.csv'
    require(read_csv(path, COLUMNS) == validations and
            sha256(path) == meta.get('validation_csv_sha256'),
            'V4d-C completed validation CSV changed')
    return meta


def input_pins(repo: Path, weights: Path, v4c: Path, canary: Path,
               temporal: Path, completed: Path, v3a: Path) -> dict[str, str]:
    pins = v4dc.source_pins(repo, weights, v4c, canary, temporal, v3a)
    for p in (HERE / 'qualify_abrupt_resume_v4dd.py',
              completed / 'v4dc_resume_manifest.json',
              completed / 'selected_scenarios.csv',
              completed / 'shakedown_validation.csv'):
        require(p.is_file(), 'V4d-D missing pinned input ' + str(p))
        pins[str(p.resolve())] = sha256(p)
    return pins


def exact_artifacts(out: Path, records: list[dict]) -> None:
    """Reject orphan .cmd, console and wrapper too, not merely orphan .log."""
    runs = out / 'runs'
    actual = {p.name for p in runs.iterdir()} if runs.is_dir() else set()
    expect = {r['scenario_id'] + ext for r in records for ext in ARTIFACT_EXTENSIONS}
    require(actual == expect,
            'V4d-D orphan, partial, missing or unexpected XSim artifact: ' +
            repr(sorted(actual ^ expect)))


def replay_records(out: Path, selected: list[dict[str, str]],
                   meta: dict, pins: dict[str, str], repo: Path,
                   source_hashes: dict[str, str], prj_hash: str) -> None:
    """Preserve and replay every prior byte before permitting a new snapshot."""
    records = meta['records']
    require(0 <= len(records) <= N_RUNS and
            [r.get('scenario_id') for r in records] ==
            [r['scenario_id'] for r in selected[:len(records)]],
            'V4d-D records must form a contiguous frozen scenario prefix')
    require(0 <= len(meta.get('builds', [])) <= 2 and
            [b.get('build_id') for b in meta['builds']] ==
            list(range(1, len(meta['builds']) + 1)),
            'V4d-D build identifiers must be contiguous')
    present = {p.name for p in out.glob('build_[0-9][0-9][0-9]') if p.is_dir()}
    expected = {f"build_{b['build_id']:03d}" for b in meta['builds']}
    require(present == expected,
            'V4d-D orphan, partial or missing snapshot directory')
    exact_artifacts(out, records)
    v4dc.verify_run_records(out, selected, meta, pins, repo, source_hashes, prj_hash)
    require(all(r.get('build_id') in expected_build_ids(meta) for r in records),
            'V4d-D record references missing snapshot')


def expected_build_ids(meta: dict) -> set[int]:
    return {b['build_id'] for b in meta['builds']}


def hard_exit(code: int) -> None:
    """Deliberate Python process termination; never call during unit tests."""
    os._exit(code)


def report_text(meta: dict) -> str:
    return '\n'.join([
        '# CNN_PEP V4d-D Process-Termination-at-Checkpoint Shakedown', '',
        f'- Status: **{meta["status"]}**',
        f'- XSim FI scenarios verified: **{len(meta["records"])}/8**',
        '- Selection: second frozen bit/offset, four classes, IMG0002 and IMG0007.',
        '- First invocation intentionally terminates the **Python process** ',
        '  immediately after an atomic checkpoint, not during XSim.',
        '- Resume requires exact re-hash/re-parse of every prior log, command, ',
        '  console, wrapper, build and frozen provenance; no file overwrite.',
        '- Unrecorded scenario artifacts cause fail-closed recovery, not deletion.',
        '- Not a proof of hard power-loss durability or mid-XSim recovery.',
        '- These eight are directed mechanisms, not independent consumer proofs.',
        '- FULL400 remains **BLOCKED**. No target status is modified.', ''
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
    completed = resolved(repo, args.v4dc_dir)
    out = resolved(repo, args.out_dir)
    project = resolved(repo, args.project) if args.project else None
    require(args.timeout_seconds >= 10, 'V4d-D timeout must be at least ten seconds')
    require(not args.resume or args.execute,
            'V4d-D --resume requires --execute')
    require((args.force_exit_after == 0 if (args.resume or not args.execute)
             else 1 <= args.force_exit_after < N_RUNS),
            'V4d-D first --execute REQUIRES --force-exit-after N=1..7; '
            'dry-run/resume require N=0')
    require(args.resume == out.exists(),
            'V4d-D new output must not exist; resume requires existing output')
    for protected in (temporal, canary, completed, results / V3A_NAME,
                      v4c.parent, v4ba, v4bc):
        require(not (protected == out or protected in out.parents),
                'V4d-D output cannot overwrite protected evidence')
    plan = load_plan(repo / 'fi/config/pilot400_targets.csv',
                     repo / 'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(repo / 'fi/scenarios/pilot400.csv', plan)
    require(len(plan) == 20 and len(manifest) == 400,
            'V4d-D requires frozen 20-target / 400-row design')
    count, missing = validate_assets(repo / 'fi/pilot400/samples', True)
    require(count == 10 and not missing, 'V4d-D requires ten original MNIST assets')
    selected = selection(manifest, plan)
    prior = verify_v4dc_ancestry(repo, results, weights, v4c, v4, v4ba,
                                 v4bb, v4bc, canary, temporal, completed, plan, manifest)
    v3a = results / V3A_NAME
    pins = input_pins(repo, weights, v4c, canary, temporal, completed,
                      v3a / 'validation_manifest.json')
    source_hashes = read_json(v3a / 'validation_manifest.json')['snapshot_build']['source_hashes']
    prj_hash = sha256(v3a / 'source_vlog.prj')
    if args.execute:
        require(project is not None and project.is_file(),
                'V4d-D --execute requires --project')
        require(bool(args.vivado), 'V4d-D --execute requires --vivado')
        require(prior['input_signature'].get('project') == str(project),
                'V4d-D project differs from verified V4d-C project')
    signature = {'pins': pins, 'project': str(project) if project else None,
                 'vivado': str(args.vivado), 'fileset': args.fileset,
                 'selection_scope': 'FOUR_CLASSES_SECOND_CONFIG_IMG0002_IMG0007',
                 'v4dc_sha256': sha256(completed / 'v4dc_resume_manifest.json'),
                 'v4db_sha256': sha256(temporal / 'v4db_temporal_manifest.json')}
    meta_file = out / 'v4dd_recovery_manifest.json'
    if args.resume:
        meta = read_json(meta_file)
        require(meta.get('schema') == SCHEMA and
                meta.get('input_signature') == signature and
                meta.get('full400_authorized') is False and
                meta.get('full400_runner_qualified') is False and
                meta.get('target_status_modified') is False and
                meta.get('full400_gate') == FULL400_GATE and
                meta.get('status') in ('RUNNING', 'INTERRUPTED_OR_INVALID') and
                1 <= len(meta.get('records', [])) < N_RUNS and
                len(meta.get('builds', [])) == 1 and
                meta.get('termination_plan', {}).get('mode') ==
                    'PYTHON_OS_EXIT_AFTER_COMMITTED_RECORD' and
                meta['termination_plan'].get('exit_code') == EXIT_CODE and
                meta['termination_plan'].get('after_n') ==
                    len(meta['records']),
                'V4d-D resume signature, status or checkpoint count invalid')
        require(read_csv(out / 'selected_scenarios.csv', FIELDS) == selected and
                sha256(out / 'selected_scenarios.csv') == meta.get('selected_sha256'),
                'V4d-D selected scenarios changed after interruption')
        replay_records(out, selected, meta, pins, repo, source_hashes, prj_hash)
    else:
        out.mkdir(parents=True, exist_ok=False)
        write_csv(out / 'selected_scenarios.csv', FIELDS, selected)
        meta = {'schema': SCHEMA, 'status': 'DRY_RUN_ONLY',
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'input_signature': signature,
                'selected_sha256': sha256(out / 'selected_scenarios.csv'),
                'termination_plan': ({'mode': 'PYTHON_OS_EXIT_AFTER_COMMITTED_RECORD',
                   'after_n': args.force_exit_after, 'exit_code': EXIT_CODE}
                   if args.execute else None),
                'records': [], 'builds': [],
                'run_scope': 'V4D_D_EIGHT_FI_INTERRUPTED_CHECKPOINT_ONLY',
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
            build_id = 1 if not args.resume else 2
            build_dir = out / f'build_{build_id:03d}'
            require(not build_dir.exists(), 'V4d-D refuses to reuse snapshot')
            build_dir.mkdir()
            snap = build_pinned_direct_snapshot(repo, project, args.vivado,
                                                args.fileset, build_dir,
                                                args.timeout_seconds, v3a)
            require(snap['source_hashes'] == source_hashes and
                    snap['compile_prj_sha256'] == prj_hash and
                    snap['elaboration_mode'] == 'FULL_BUILD',
                    'V4d-D snapshot source differs from V3a authenticated RTL')
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
            start = len(meta['records'])
            for index, row in enumerate(selected[start:], start + 1):
                v4d.check_input_lock(pins)
                require(all(Path(p).is_file() and sha256(Path(p)) == h
                            for p, h in source_hashes.items()),
                        'V4d-D frozen RTL changed before XSim')
                sid = row['scenario_id']
                sample = repo / 'fi/pilot400/samples' / row['sample_id']
                log = runs / (sid + '.log')
                console = runs / (sid + '.console.log')
                wrapper = runs / (sid + '.wrapper.log')
                cmd = runs / (sid + '.cmd')
                require(all(not p.exists() for p in (log, console, wrapper, cmd)),
                        'V4d-D refuses to overwrite scenario evidence: ' + sid)
                cmd.write_text(make_cmd_run(snap['simdir'], snap['vivado_root'],
                                            row, sample, tcl, log, console,
                                            profile_only=False), encoding='ascii')
                print(f'V4DD_XSIM {index}/8 {sid} {row["sample_id"]} '
                      f'{row["target_id"]} bit={row["bit_index"]} '
                      f'offset={row["injection_offset"]}', flush=True)
                call_cmd(cmd, snap['simdir'], wrapper, args.timeout_seconds,
                         'V4d-D interrupted checkpoint ' + sid)
                require(log.is_file() and console.is_file() and wrapper.is_file(),
                        'V4d-D missing complete XSim artifacts')
                text = console.read_text(encoding='utf-8', errors='replace')
                require('Expected a switch' not in text and
                        not any(line.startswith(('ERROR:', 'FATAL:'))
                                for line in text.splitlines()),
                        'V4d-D XSim console error: ' + sid)
                parsed = v4dc.result_from_log(log, row)
                v4d.check_input_lock(pins)
                require(all(Path(p).is_file() and sha256(Path(p)) == h
                            for p, h in source_hashes.items()),
                        'V4d-D frozen RTL changed during XSim')
                meta['records'].append({'scenario_id': sid,
                    'validation': parsed, 'build_id': build_id,
                    'log_sha256': sha256(log),
                    'console_sha256': sha256(console),
                    'wrapper_sha256': sha256(wrapper),
                    'cmd_sha256': sha256(cmd)})
                v4d.atomic_write_json(meta_file, meta)
                print('V4DD_SCENARIO_MECHANISM_PASS: ' + sid + ' ' +
                      parsed['outcome'], flush=True)
                if args.force_exit_after == index:
                    print('V4DD_INTENTIONAL_PROCESS_EXIT: ' +
                          str(index) + '/8 records atomically checkpointed; '
                          'restart with --resume; FULL400 BLOCKED', flush=True)
                    hard_exit(EXIT_CODE)
            replay_records(out, selected, meta, pins, repo, source_hashes, prj_hash)
            require(len(meta['records']) == N_RUNS, 'V4d-D incomplete after recovery')
            meta['status'] = STATUS_PASS
        else:
            print('V4DD_DRY_RUN_PASS: 8 second-configuration scenarios; '
                  'V4d-C/V4d-B/V4c ancestry replayed; 0 XSim; '
                  'FULL400 BLOCKED', flush=True)
        validations = [r['validation'] for r in meta['records']]
        write_csv(out / 'recovery_validation.csv', COLUMNS, validations)
        meta['validation_csv_sha256'] = sha256(out / 'recovery_validation.csv')
        (out / 'v4dd_report.md').write_text(report_text(meta), encoding='utf-8')
        v4d.atomic_write_json(meta_file, meta)
        if args.execute:
            print('V4DD_RECOVERY_PASS: 8/8 independent XSim FI verified; '
                  'intentional Python termination at committed checkpoint and '
                  'new-process recovery; NOT mid-XSim/power-loss proof; '
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
    ap.add_argument('--vivado', default='vivado')
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
    ap.add_argument('--v4dc-dir', type=Path,
                    default=Path('fi/results/v4dc_resume_01'))
    ap.add_argument('--out-dir', type=Path,
                    default=Path('fi/results/v4dd_abrupt_recovery_01'))
    ap.add_argument('--timeout-seconds', type=int, default=900)
    ap.add_argument('--force-exit-after', type=int, default=0, metavar='N',
                    help='Hard-exit Python after N=1..7 verified records; only on new run')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    try:
        run(args)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        raise SystemExit('V4DD_RECOVERY_FAIL: ' + str(error))


if __name__ == '__main__':
    main()

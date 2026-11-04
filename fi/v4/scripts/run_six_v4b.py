#!/usr/bin/env python3
"""CNN_PEP V4b: six historical Pilot400 targets, directed two-config mechanism audit.

- Replays the V4 cross-evidence audit before executing any XSim process.
- Reuses the frozen original FI TB and Vivado-authoritative P0 HDL.
- Runs precisely 12 IMG0000 FI configurations from the frozen 400-row manifest.
- Verifies XOR deposit/readback and FI_RESULT_V2; NEVER certifies consumer
  propagation for six targets or authorizes full400 by itself.
- Preserves partial logs and fails closed. No existing FI evidence is overwritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / 'scripts'  # fi/scripts
for folder in (HERE, SCRIPTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from audit_v4 import run_audit, read_json, require, V3A_NAME  # noqa: E402
from pilot400_common import (  # noqa: E402
    FIELDS, ASSET_SIZES, asset_lines, load_plan, sha256,
    verify_manifest, write_csv,
)
from pilot400_direct_xsim import make_cmd_run, call_cmd  # noqa: E402
from run_pilot400_target_validation_v3 import (  # noqa: E402
    REPORT_FIELDS, TARGET_FIELDS, build_verified_snapshot,
    inspect_applied_and_result, select_scenarios, summarize_targets,
)

# These are frozen statuses. A change requires a separate documented protocol,
# not a code edit to make validation pass.
PRE_EXISTING = {
    'CTL_0001': 'PASS_ONE_WINDOW',
    'BUF_0030': 'PASS_ONE_WINDOW',
    'BUF_0031': 'TIMELINE_ONE_WINDOW',
    'MEM_0001': 'PASS_ONE_WINDOW',
    'MEM_0002': 'TIMELINE_ONE_WINDOW',
    'DAT_0010': 'PASS_ONE_WINDOW',
}
SCHEMA = 'CNN_PEP_V4B_SIX_DIRECTED_MECHANISM_V1'
STATUS_DONE = 'TWELVE_MECHANISMS_VERIFIED_CONSUMER_QUALIFICATION_OPEN'


def six_targets(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Select exactly the frozen six previously reviewed, non-CANDIDATE sites."""
    chosen = [p for p in plan if p['target_id'] in PRE_EXISTING]
    actual = {p['target_id']: p['evidence_status'] for p in chosen}
    require(len(chosen) == 6 and actual == PRE_EXISTING,
            f'Frozen six-site status contract changed: {actual}')
    require(len(plan) == 20 and
            sum(p['evidence_status'] == 'CANDIDATE' for p in plan) == 14,
            'Frozen 20/14/6 Pilot400 split changed')
    return chosen


def six_scenarios(manifest: list[dict[str, str]],
                  plan: list[dict[str, str]]) -> list[dict[str, str]]:
    chosen = select_scenarios(manifest, six_targets(plan), ('IMG0000',))
    require(len(chosen) == 12, 'Expected exactly twelve directed IMG0000 scenarios')
    require(len({r['scenario_id'] for r in chosen}) == 12,
            'Duplicate scenario ID in six-site selection')
    require(len({(r['target_id'], r['bit_index'], r['injection_offset']) for r in chosen}) == 12,
            'Duplicate target/bit/offset tuple: DAT_0010 must use distinct bits')
    dat = [r for r in chosen if r['target_id'] == 'DAT_0010']
    require({(r['bit_index'], r['injection_offset']) for r in dat} ==
            {('0', '1340'), ('1', '1340')},
            'DAT_0010 bit-pair contract altered')
    return chosen


def verify_v4_ancestry(repo: Path, results: Path, weights: Path,
                       source_audit: Path) -> tuple[dict[str, Any], str]:
    """Independently revalidate V4 and compare immutable fields to its saved JSON.

    run_audit() replays all V3a/V3b original logs and source hashes, not merely
    the console markers. Generated timestamps are intentionally ignored.
    """
    saved = read_json(source_audit)
    fresh = run_audit(repo, results, weights)
    require(saved.get('schema') == 'CNN_PEP_V4_CROSS_EVIDENCE_V1',
            'V4 provenance schema mismatch')
    for key in ('status', 'v3a_manifest_sha256', 'control10_manifest_sha256',
                'timing_audit_sha256', 'selected_plan_sha256', 'ctl_inventory',
                'v3b_group_counts', 'v3a_scenarios_replayed',
                'v3b_scenarios_replayed', 'review_ready_candidates',
                'targets_requiring_further_qualification', 'source_authority',
                'full400_authorized', 'target_status_modified', 'targets'):
        require(saved.get(key) == fresh.get(key),
                f'V4 saved cross-audit differs from replay: {key}')
    require(fresh['status'] == 'CROSS_EVIDENCE_PASS_FULL400_BLOCKED' and
            fresh['full400_authorized'] is False and
            fresh['review_ready_candidates'] == 14 and
            fresh['targets_requiring_further_qualification'] == 6,
            'V4 integrity/qualification baseline is not approved')
    return fresh, sha256(source_audit)


def evidence_report(selected: list[dict[str, str]],
                    records: list[dict[str, str]], executed: bool,
                    status: str) -> str:
    by_tid = {tid: [r for r in records if r['target_id'] == tid] for tid in PRE_EXISTING}
    text = [
        '# CNN_PEP V4b: Six Historical Target Mechanism Review', '',
        '> Directed IMG0000 scenarios only. NOT full400, NOT a SEU population',
        '> estimate, NOT automatic target promotion or consumer certification.', '',
        f'- Status: **{status}**',
        f'- Frozen configurations: **{len(selected)}**',
        f'- XSim FI mechanisms verified: **{len(records)}**',
        f'- Run mode: **{"EXECUTED" if executed else "DRY_RUN_ONLY"}**', '',
        '| Target | Frozen status | XSim mechanisms | Consumer qualification |',
        '|---|---|---:|---|',
    ]
    for tid, frozen in PRE_EXISTING.items():
        count = len(by_tid[tid])
        text.append(f'| {tid} | {frozen} | {count}/2 | PENDING_INDEPENDENT_REVIEW |')
    text += [
        '', '## Research interpretation', '',
        '- A PASS verifies single-bit XOR deposit/readback, manifest timing eligibility',
        '  from the frozen fault-free profile, and a strict FI_RESULT_V2 outcome.',
        '- A legacy timeline or ArgMax marker may appear, but is NOT upgraded here',
        '  to independently audited value-aware consumer proof.',
        '- `FINAL_OUTPUT_MATCH` does not prove the fault was masked at injection.',
        '- Six targets still require a separately specified two-config consumer/',
        '  control-decision evidence audit or versioned qualification exception.',
        '- Full400 remains BLOCKED even when all 12 V4b mechanisms pass.',
    ]
    return '\n'.join(text) + '\n'


def resolve_arg(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def run(args: argparse.Namespace) -> dict:
    repo = args.repo.resolve()
    results = resolve_arg(repo, args.results_dir)
    weights = resolve_arg(repo, args.weights_dir)
    v4_path = resolve_arg(repo, args.v4_audit)
    out = resolve_arg(repo, args.out_dir)
    require(not out.exists(), f'Refusing to overwrite V4b evidence directory: {out}')
    require(args.timeout_seconds >= 10, 'Timeout must be at least 10 seconds')
    v4_fresh, v4_sha = verify_v4_ancestry(repo, results, weights, v4_path)
    plan = load_plan(repo / 'fi/config/pilot400_targets.csv',
                     repo / 'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(repo / 'fi/scenarios/pilot400.csv', plan)
    selected = six_scenarios(manifest, plan)
    for file_name, (count, digits) in ASSET_SIZES.items():
        asset_lines(repo / 'fi/pilot400/samples/IMG0000' / file_name, count, digits)
    project = resolve_arg(repo, args.project) if args.project else None
    if args.execute:
        require(project is not None and project.is_file(),
                '--execute needs a valid --project XPR')
    out.mkdir(parents=True, exist_ok=False)
    selected_path = out / 'selected_scenarios.csv'
    write_csv(selected_path, FIELDS, selected)
    meta = {
        'schema': SCHEMA,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'DRY_RUN_ONLY',
        'source_authority': 'vivado',
        'v4_cross_audit_sha256': v4_sha,
        'v3a_manifest_sha256': v4_fresh['v3a_manifest_sha256'],
        'timing_audit_sha256': v4_fresh['timing_audit_sha256'],
        'control10_manifest_sha256': v4_fresh['control10_manifest_sha256'],
        'frozen_plan_sha256': sha256(repo / 'fi/config/pilot400_targets.csv'),
        'frozen_manifest_sha256': sha256(repo / 'fi/scenarios/pilot400.csv'),
        'frozen_tb_sha256': sha256(repo / 'fi/tb/tb_cnn_mnist_fi_pilot400.v'),
        'selected_sha256': sha256(selected_path),
        'selected_count': len(selected),
        'sample_id': 'IMG0000',
        'target_ids': [p['target_id'] for p in six_targets(plan)],
        'records': [],
        'consumer_qualified': False,
        'full400_authorized': False,
        'target_status_modified': False,
    }
    records: list[dict[str, str]] = []
    try:
        if args.execute:
            assert project is not None
            snap = build_verified_snapshot(repo, project, args.vivado, args.fileset,
                                           out, args.timeout_seconds)
            # Live source contract must match what authenticated V3a compiled.
            prior = read_json(results / V3A_NAME / 'validation_manifest.json')
            require(snap['source_hashes'] == prior['snapshot_build']['source_hashes'],
                    'Vivado HDL files or hashes differ from V3a compiled source')
            meta['snapshot_build'] = {k: v for k, v in snap.items()
                                      if k not in ('simdir', 'vivado_root', 'snapshot')}
            meta['snapshot_build']['snapshot'] = str(snap['snapshot'])
            meta['project'] = str(project)
            meta['status'] = 'SNAPSHOT_BUILT'
            run_tcl = out / 'direct_runall.tcl'
            run_tcl.write_text('run all\nquit\n', encoding='ascii')
            logs = out / 'logs'
            logs.mkdir(exist_ok=False)
            for ordinal, row in enumerate(selected, 1):
                sid = row['scenario_id']
                log = logs / (sid + '.log')
                console = out / (sid + '.console.log')
                cmd = out / (sid + '.cmd')
                sample = repo / 'fi/pilot400/samples/IMG0000'
                cmd.write_text(make_cmd_run(snap['simdir'], snap['vivado_root'],
                                            row, sample, run_tcl, log, console,
                                            profile_only=False), encoding='ascii')
                print(f'V4B_XSIM {ordinal}/{len(selected)} {sid} {row["target_id"]} '
                      f'bit={row["bit_index"]} offset={row["injection_offset"]}',
                      flush=True)
                call_cmd(cmd, snap['simdir'], out / (sid + '.wrapper.log'),
                         args.timeout_seconds, f'V4b {sid}')
                require(log.is_file(), f'XSim did not create FI log: {sid}')
                require(console.is_file() and
                        'Expected a switch' not in console.read_text(errors='replace'),
                        f'XSim CLI error for {sid}')
                verified = inspect_applied_and_result(log, row)
                records.append(verified)
                meta['records'].append({'scenario_id': sid,
                                        'log': str(log), 'log_sha256': sha256(log),
                                        'outcome': verified['outcome'],
                                        'consumer_status': 'NOT_INDEPENDENTLY_AUDITED'})
                require(all(Path(path).is_file() and sha256(Path(path)) == digest
                            for path, digest in snap['source_hashes'].items()),
                        'Vivado HDL changed during V4b run: ' + sid)
                print('V4B_SCENARIO_MECHANISM_PASS:', sid, verified['outcome'], flush=True)
            require(len(records) == len(selected) == 12,
                    'Not all 12 frozen scenarios executed')
            meta['status'] = STATUS_DONE
        summary = summarize_targets(six_targets(plan), records, 1)
        write_csv(out / 'mechanism_validation.csv', REPORT_FIELDS, records)
        write_csv(out / 'target_summary.csv', TARGET_FIELDS, summary)
        meta['mechanism_csv_sha256'] = sha256(out / 'mechanism_validation.csv')
        meta['target_summary_sha256'] = sha256(out / 'target_summary.csv')
        (out / 'v4b_report.md').write_text(
            evidence_report(selected, records, args.execute, meta['status']),
            encoding='utf-8')
        if args.execute:
            print('V4B_SIX_MECHANISM_PASS: 12/12 XSim-verified configurations; '
                  'two-config consumer qualification remains OPEN; FULL400 BLOCKED',
                  flush=True)
        else:
            print('V4B_SIX_DRY_RUN_PASS: 12 frozen configurations; V4 ancestry '
                  'replayed; 0 new XSim runs; FULL400 BLOCKED', flush=True)
    except Exception:
        meta['status'] = 'INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out / 'v4b_manifest.json').write_text(
            json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
            encoding='utf-8')
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--results-dir', type=Path, default=Path('fi/results'))
    ap.add_argument('--v4-audit', type=Path,
                    default=Path('fi/results/v4_cross_evidence_audit_02/v4_audit.json'))
    ap.add_argument('--weights-dir', type=Path, default=Path('data'))
    ap.add_argument('--project', type=Path)
    ap.add_argument('--vivado', default='vivado')
    ap.add_argument('--fileset', default='sim_1')
    ap.add_argument('--timeout-seconds', type=int, default=900)
    ap.add_argument('--out-dir', type=Path,
                    default=Path('fi/results/v4b_six_mechanism_01'))
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    try:
        run(args)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        raise SystemExit('V4B_SIX_FAIL: ' + str(exc)) from exc


if __name__ == '__main__':
    main()

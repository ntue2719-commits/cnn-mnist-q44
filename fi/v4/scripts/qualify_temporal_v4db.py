#!/usr/bin/env python3
"""CNN_PEP V4d-B: fault-free, TEN-sample temporal-eligibility sweep.

This is NOT 400 fault injections or 400 fault-exposure proofs. It runs ten
independent fault-free XSim profiles, each checking the original 20 frozen
sites x two bit/time configurations against observed V2 pre-NBA events.

Requires the completed, SHA256-verified V4d-A 16-run canary and V4c ancestry.
Builds one Vivado-authoritative snapshot and executes ten separate XSim runs.
Never edits P0 RTL, the frozen FI TB, target statuses, or the 400-row manifest.
The full400 gate remains explicitly BLOCKED after success.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
FI_SCRIPTS = HERE.parents[1] / 'scripts'
V3B_SCRIPTS = HERE.parents[1] / 'v3b/scripts'
for directory in (HERE, FI_SCRIPTS, V3B_SCRIPTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from pilot400_common import (  # noqa: E402
    FIELDS, SAMPLES, load_plan, verify_manifest, validate_assets,
    read_csv, write_csv, sha256,
)
from profile_pilot400 import (  # noqa: E402
    assess, parse_log, timing_gate_pass, COLUMNS as PROFILE_COLUMNS,
)
from pilot400_direct_xsim import make_cmd_run, call_cmd  # noqa: E402
from run_pilot400_target_validation_v3 import build_verified_snapshot  # noqa: E402
from pinned_direct_snapshot_v4db import build_pinned_direct_snapshot  # noqa: E402
from audit_v4 import V3A_NAME, read_json, require  # noqa: E402
import qualify_runner_v4d as v4d  # noqa: E402

SCHEMA = 'CNN_PEP_V4D_B_TEN_SAMPLE_TEMPORAL_V1'
STATUS = 'TEN_SAMPLES_FAULT_FREE_TEMPORAL_ELIGIBILITY_VERIFIED_FULL400_BLOCKED'
FULL400_GATE = 'BLOCKED_RUNNER_RELEASE_NOT_GRANTED'
PROFILE_COUNT = len(SAMPLES)
WINDOW_COUNT = 20 * 2 * PROFILE_COUNT
PROFILE_MATRIX_COLUMNS = ('sample_id', *PROFILE_COLUMNS)
PROFILE_SUMMARY_COLUMNS = ('sample_id', 'anchor_scenario_id', 'event_count',
                           'eligible_windows', 'event_trace_sha256',
                           'log_sha256', 'status')
CONTROL_RE = re.compile(r'^FI_DISABLED_CONTROL_PASS latency=(\d+) pool1=(\d+) '
                        r'pool2=(\d+) logits=(\d+) prediction=(\d+)\s*$', re.M)
ERROR_RE = re.compile(r'^(?:ERROR:|FATAL:|Fatal:|\*\* Error:)', re.M)


def selection(manifest: list[dict[str, str]]) -> list[dict[str, str]]:
    """Exactly one *noninjecting* profile anchor per frozen IMG0000..IMG0009."""
    rows = [next((r for r in manifest if r['sample_id'] == sample and
                  r['target_id'] == 'CTL_0001'), None)
            for sample, _ in SAMPLES]
    require(all(rows), 'Missing CTL_0001 baseline profile anchor for some sample')
    require(len(rows) == PROFILE_COUNT and
            len({r['sample_id'] for r in rows}) == PROFILE_COUNT and
            len({r['scenario_id'] for r in rows}) == PROFILE_COUNT,
            'Ten fault-free profile anchors are missing/duplicated')
    require(all(r['target_id'] == 'CTL_0001' and
                r['bit_index'] == '2' and r['injection_offset'] == '125'
                for r in rows),
            'Frozen profile anchor CTL_0001 bit2@125 changed')
    return rows


def verify_canary(repo: Path, results: Path, weights: Path, v4c: Path,
                  v4: Path, v4ba: Path, v4bb: Path, v4bc: Path,
                  canary_dir: Path, plan: list[dict[str, str]],
                  manifest: list[dict[str, str]]) -> dict[str, Any]:
    """Reparse V4c ancestry and the real sixteen canary logs; not console-only."""
    v4d.replay_v4c(repo, results, weights, v4c, v4, v4ba, v4bb, v4bc, plan,
                   manifest)
    saved = read_json(canary_dir / 'v4d_canary_manifest.json')
    require(saved.get('schema') == v4d.SCHEMA and
            saved.get('status') == v4d.PASS_STATUS and
            saved.get('full400_authorized') is False and
            saved.get('full400_runner_qualified') is False and
            saved.get('target_status_modified') is False and
            saved.get('run_scope') == 'V4D_A_16_CANARY_ONLY' and
            len(saved.get('records', [])) == v4d.CANARY_COUNT and
            bool(saved.get('builds')),
            'V4d-A canary is not a completed, blocked, sixteen-run evidence set')
    pins = v4d.locked_inputs(repo, weights, v4c,
                             results / V3A_NAME / 'validation_manifest.json')
    require(saved.get('input_signature', {}).get('pins') == pins and
            saved.get('v4c_audit_sha256') == sha256(v4c),
            'V4d-A canary input/V4c source pins changed')
    selected = v4d.canary_selection(manifest)
    require(read_csv(canary_dir / 'selected_scenarios.csv', FIELDS) == selected and
            saved.get('selected_sha256') == sha256(canary_dir / 'selected_scenarios.csv'),
            'V4d-A selected canary source changed')
    v4d.verify_done_records(canary_dir, selected, saved['records'], pins)
    rows = [r['validation'] for r in saved['records']]
    require(read_csv(canary_dir / 'canary_validation.csv', v4d.COLUMNS) == rows and
            saved.get('validation_csv_sha256') ==
            sha256(canary_dir / 'canary_validation.csv'),
            'V4d-A canary validation CSV/log inconsistency')
    prior = read_json(results / V3A_NAME / 'validation_manifest.json')
    original = prior['snapshot_build']['source_hashes']
    prj_hash = sha256(results / V3A_NAME / 'source_vlog.prj')
    require(all(b.get('source_hashes') == original and
                b.get('compile_prj_sha256') == prj_hash for b in saved['builds']),
            'V4d-A compiled HDL differs from V3a verified Vivado P0')
    return saved


def source_pins(repo: Path, weights: Path, v4c: Path, canary: Path,
                v3a: Path) -> dict[str, str]:
    pins = v4d.locked_inputs(repo, weights, v4c, v3a)
    for p in (HERE/'qualify_temporal_v4db.py',
              HERE/'pinned_direct_snapshot_v4db.py',
              FI_SCRIPTS/'profile_pilot400.py',
              canary/'v4d_canary_manifest.json',
              canary/'canary_validation.csv',
              canary/'selected_scenarios.csv'):
        require(p.is_file(), 'V4d-B missing pinned source: ' + str(p))
        pins[str(p.resolve())] = sha256(p)
    return pins


def parse_profile_evidence(log: Path, anchor: dict[str, str],
                           plan: list[dict[str, str]],
                           sample_dir: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Strong, fail-closed V2 event and golden-control verification."""
    sample = anchor['sample_id']
    text = log.read_text(encoding='utf-8', errors='replace')
    require(not ERROR_RE.search(text) and
            'Expected a switch' not in text and
            'FI_INFRA_ERROR:' not in text and
            text.count('FI_PROFILE_SCHEMA_V2: ACTIVE PRODUCE CONSUME PRE_NBA') == 1,
            sample + ': failed or ambiguous XSim V2 profile transcript')
    matches = CONTROL_RE.findall(text)
    require(len(matches) == 1, sample + ': require one precise fault-free control summary')
    latency, pool1, pool2, logits, prediction = map(int, matches[0])
    pred = int((sample_dir / 'prediction.hex').read_text(encoding='ascii').strip(), 16)
    require(latency > 0 and pool1 == 676 and pool2 == 200 and logits == 10 and
            prediction == pred,
            sample + ': fault-free prediction/latency/tensor counts mismatch')
    # The original V2 parser rejects FI and missing/duplicated completion markers.
    events = parse_log(log)
    report = assess(plan, events)
    require(len(report) == 20 and len({r['target_id'] for r in report}) == 20,
            sample + ': incomplete twenty-target event audit')
    problems = [f"{r['target_id']}:{r['status_a']}/{r['status_b']}"
                for r in report if not timing_gate_pass([r])]
    require(not problems, sample + ': temporal eligibility FAILED: ' + '; '.join(problems))
    eligible = 2 * len(report)  # timing_gate_pass([row]) passed for every row.
    require(eligible == 40, sample + ': expected exactly 40 eligible windows')
    digest = hashlib.sha256(json.dumps(events, separators=(',', ':')).encode('ascii')).hexdigest()
    summary = {'sample_id': sample, 'anchor_scenario_id': anchor['scenario_id'],
               'event_count': str(len(events)), 'eligible_windows': str(eligible),
               'event_trace_sha256': digest, 'log_sha256': sha256(log),
               'status': 'FAULT_FREE_V2_TEMPORAL_40_OF_40'}
    return summary, [dict(sample_id=sample, **r) for r in report]


def verify_existing_records(out: Path, anchors: list[dict[str, str]],
                            records: list[dict], pins: dict[str, str],
                            plan: list[dict[str, str]], repo: Path) -> None:
    """Reject resumed runs with partial/unrecorded files or edited evidence."""
    require(len(records) <= PROFILE_COUNT and
            [r.get('sample_id') for r in records] ==
            [r['sample_id'] for r in anchors[:len(records)]],
            'V4d-B resume records not a contiguous frozen sample prefix')
    runs = out/'runs'
    recorded = {r['sample_id'] for r in records}
    if runs.exists():
        present = {p.name.removesuffix('.log') for p in runs.glob('IMG*.log')
                   if not p.name.endswith(('.console.log','.wrapper.log'))}
        require(present == recorded, 'V4d-B partial or unrecorded profile log')
    for r, anchor in zip(records, anchors):
        sample = anchor['sample_id']
        for field, extension in (('log_sha256', '.log'),
                                 ('console_sha256', '.console.log'),
                                 ('wrapper_sha256', '.wrapper.log'),
                                 ('cmd_sha256', '.cmd')):
            path = runs/(sample+extension)
            require(path.is_file() and sha256(path) == r.get(field),
                    'V4d-B missing/mutated XSim evidence: '+str(path))
        summary, matrix = parse_profile_evidence(
            runs/(sample+'.log'), anchor, plan,
            repo/'fi/pilot400/samples'/sample)
        require(r.get('summary') == summary and r.get('target_rows') == matrix,
                'V4d-B saved temporal result differs from raw log: ' + sample)
    v4d.check_input_lock(pins)


def report_text(meta: dict, summary: list[dict[str, str]]) -> str:
    signatures = Counter(r['event_trace_sha256'] for r in summary)
    equal = len(signatures) == 1 if len(summary) == PROFILE_COUNT else None
    lines = ['# CNN_PEP V4d-B — Ten-Sample Fault-Free Temporal Sweep', '',
             f'- Status: **{meta["status"]}**',
             f'- Distinct MNIST samples profiled: **{len(summary)}/10**',
             f'- Eligible fault-free temporal-eligibility configuration checks: **{sum(int(x["eligible_windows"]) for x in summary)}/400**',
             '- Meaning: baseline *temporal eligibility* for 20 targets x 2 configs x 10 samples.',
             '- These are **10 fault-free XSim profiles**, NOT 400 fault injections or 400 exposure proofs.',
             f'- Exact profile event timeline identical across all samples: **{equal}**.',
             '- Original RTL, FI TB, manifest and target status untouched.',
             '- FULL400 execution is BLOCKED pending separate runner release approval.', '',
             '## Per-sample proof', '',
             '| Sample | Events | Eligible windows | Profile log SHA256 |',
             '|---|---:|---:|---|']
    for r in summary:
        lines.append(f'| {r["sample_id"]} | {r["event_count"]} | {r["eligible_windows"]}/40 | `{r["log_sha256"]}` |')
    lines += ['', '## Separate full campaign release gate', '',
              '1. Audit the ten raw profile logs and the saved per-sample V2 event matrix.',
              '2. Check V4d-A sixteen-run canary and successful real restart/resume behavior.',
              '3. Independently review the full400 runner fail-closed behavior, input locks and sample mapping.',
              '4. Explicitly authorize a new versioned full400 execution, never this V4d-B script.',
              '5. Report directed configuration outcomes, not a population-wide SEU failure probability.', '']
    return '\n'.join(lines)


def resolved(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo/path).resolve()


def run(args: argparse.Namespace) -> dict:
    repo = args.repo.resolve()
    results = resolved(repo,args.results_dir)
    weights = resolved(repo,args.weights_dir)
    v4c = resolved(repo,args.v4c_audit)
    v4 = resolved(repo,args.v4_audit)
    v4ba = resolved(repo,args.v4b_a_dir)
    v4bb = resolved(repo,args.v4b_b_audit)
    v4bc = resolved(repo,args.v4b_c_dir)
    canary = resolved(repo,args.v4d_canary_dir)
    out = resolved(repo,args.out_dir)
    project = resolved(repo,args.project) if args.project else None
    require(args.timeout_seconds >= 10, 'V4d-B timeout must be >=10 seconds')
    require(not args.resume or args.execute, '--resume requires --execute')
    require(args.resume or not out.exists(),
            'V4d-B refuses to overwrite existing evidence: ' + str(out))
    require(not args.resume or out.is_dir(), '--resume requires existing directory')
    plan = load_plan(repo/'fi/config/pilot400_targets.csv',
                     repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    require(len(plan)==20 and len(manifest)==400, 'Frozen plan cardinality mismatch')
    count, missing = validate_assets(repo/'fi/pilot400/samples', True)
    require(count == 10 and not missing, 'Ten MNIST golden asset sets required')
    anchors = selection(manifest)
    verify_canary(repo, results, weights, v4c, v4, v4ba, v4bb, v4bc,
                  canary,plan,manifest)
    v3a = results/V3A_NAME/'validation_manifest.json'
    pins = source_pins(repo,weights,v4c,canary,v3a)
    # Evidence inputs are immutable: never nest the new result inside an ancestor.
    for protected in (canary, v4c.parent, v4ba, v4bc, results/V3A_NAME):
        if out == protected or protected in out.parents:
            raise ValueError('V4d-B result directory must be outside protected evidence: '+str(protected))
    if args.execute:
        require(project is not None and project.is_file(),
                '--execute requires --project .xpr')
        require(bool(args.vivado), '--execute requires Vivado path')
        saved_canary = read_json(canary/'v4d_canary_manifest.json')
        require(saved_canary['input_signature'].get('project') == str(project),
                'V4d-B Vivado project path differs from completed V4d-A canary')
    build_mode=getattr(args,'build_mode','vivado-scripts')
    require(build_mode in ('vivado-scripts','pinned-direct-prj'), 'Unknown V4d-B build mode')
    signature={'pins':pins, 'project':str(project) if project else None,
               'vivado':str(args.vivado),'fileset':args.fileset,
               'build_mode':build_mode,
               'samples':[s for s,_ in SAMPLES], 'profiles':PROFILE_COUNT}
    meta_path=out/'v4db_temporal_manifest.json'
    if args.resume:
        meta=read_json(meta_path)
        require(meta.get('schema')==SCHEMA and meta.get('input_signature')==signature and
                meta.get('status') in ('INTERRUPTED_OR_INVALID','RUNNING','SNAPSHOT_BUILT') and
                meta.get('full400_authorized') is False,
                'V4d-B resume manifest/source lock mismatch')
        require(read_csv(out/'profile_anchors.csv',FIELDS)==anchors and
                sha256(out/'profile_anchors.csv') == meta.get('selected_sha256'),
                'V4d-B selected anchor rows changed')
        verify_existing_records(out,anchors,meta['records'],pins,plan,repo)
        require(len(meta['records']) < PROFILE_COUNT,
                'V4d-B already completed, cannot resume')
    else:
        out.mkdir(parents=True,exist_ok=False)
        write_csv(out/'profile_anchors.csv',FIELDS,anchors)
        meta={'schema':SCHEMA, 'created_utc':datetime.now(timezone.utc).isoformat(),
              'status':'DRY_RUN_ONLY', 'input_signature':signature,
              'selected_sha256':sha256(out/'profile_anchors.csv'),
              'full400_authorized':False,'full400_runner_qualified':False,
              'target_status_modified':False, 'full400_gate':FULL400_GATE,
              'profile_scope':'TEN_FAULT_FREE_V2_XSIM_PROFILES_ONLY',
              'records':[], 'builds':[],
              'v4d_canary_sha256':sha256(canary/'v4d_canary_manifest.json'),
              'v4c_audit_sha256':sha256(v4c)}
        v4d.atomic_write_json(meta_path,meta)
    try:
        if args.execute:
            assert project is not None
            meta['status']='RUNNING'
            v4d.atomic_write_json(meta_path,meta)
            existing_ids = [int(p.name[6:]) for p in out.glob('build_[0-9][0-9][0-9]')
                            if p.is_dir()]
            build_id = max(existing_ids, default=0)+1
            build_dir=out/f'build_{build_id:03d}'
            require(not build_dir.exists(),'Build output already exists')
            build_dir.mkdir()
            if build_mode=='pinned-direct-prj':
                # Avoid Vivado scripts_only but NEVER reuse an unverified snapshot:
                # compile and elaborate from V3a's exact authenticated .prj.
                snap=build_pinned_direct_snapshot(
                    repo,project,args.vivado,args.fileset,build_dir,
                    args.timeout_seconds,results/V3A_NAME)
            else:
                snap=build_verified_snapshot(
                    repo,project,args.vivado,args.fileset,
                    build_dir,args.timeout_seconds)
            prior=read_json(v3a)
            require(snap['source_hashes']==prior['snapshot_build']['source_hashes'] and
                    snap['compile_prj_sha256']==sha256(results/V3A_NAME/'source_vlog.prj'),
                    'V4d-B Vivado RTL/source-list differs from V3a verified P0')
            meta['builds'].append({'build_id':build_id,
                'build_mode':build_mode,
                'compile_prj_sha256':snap['compile_prj_sha256'],
                'elaboration_mode':snap['elaboration_mode'],
                'source_hashes':snap['source_hashes'],
                'compile_log_sha256':snap['compile_log_sha256'],
                'elaborate_log_sha256':snap['elaborate_log_sha256']})
            meta['status']='SNAPSHOT_BUILT'
            v4d.atomic_write_json(meta_path,meta)
            tcl=build_dir/'runall.tcl'
            tcl.write_text('run all\nquit\n',encoding='ascii')
            runs=out/'runs'
            runs.mkdir(exist_ok=True)
            for i,anchor in enumerate(anchors,1):
                if i <= len(meta['records']):
                    continue
                v4d.check_input_lock(pins)
                sample=anchor['sample_id']
                sample_dir=repo/'fi/pilot400/samples'/sample
                log=runs/(sample+'.log')
                console=runs/(sample+'.console.log')
                wrapper=runs/(sample+'.wrapper.log')
                cmd=runs/(sample+'.cmd')
                require(all(not p.exists() for p in (log,console,wrapper,cmd)),
                        'V4d-B refuses to overwrite previous profile artifacts: '+sample)
                cmd.write_text(make_cmd_run(snap['simdir'],snap['vivado_root'],
                                            anchor,sample_dir,tcl,log,console,
                                            profile_only=True),encoding='ascii')
                print(f'V4DB_TEMPORAL_XSIM {i}/{PROFILE_COUNT} {sample} '
                      f'profile_anchor={anchor["scenario_id"]}',flush=True)
                call_cmd(cmd,snap['simdir'],wrapper,args.timeout_seconds,
                         'V4d-B fault-free timing '+sample)
                require(log.is_file() and console.is_file(),
                        'V4d-B XSim log/console missing: '+sample)
                content=console.read_text(encoding='utf-8',errors='replace')
                require('Expected a switch' not in content and
                        not ERROR_RE.search(content),
                        'V4d-B console contains XSim error: '+sample)
                summary,matrix=parse_profile_evidence(log,anchor,plan,sample_dir)
                v4d.check_input_lock(pins)
                require(all(Path(p).is_file() and sha256(Path(p))==h
                            for p,h in snap['source_hashes'].items()),
                        'V4d-B Vivado HDL changed during profiles')
                meta['records'].append({'sample_id':sample,'summary':summary,
                    'target_rows':matrix, 'log_sha256':sha256(log),
                    'console_sha256':sha256(console),
                    'wrapper_sha256':sha256(wrapper),'cmd_sha256':sha256(cmd),
                    'build_id':build_id})
                v4d.atomic_write_json(meta_path,meta)
                print('V4DB_SAMPLE_TEMPORAL_PASS: '+sample+' 40/40 baseline windows',flush=True)
            verify_existing_records(out,anchors,meta['records'],pins,plan,repo)
            require(len(meta['records'])==PROFILE_COUNT,
                    'V4d-B did not run ten separate fault-free profiles')
            meta['status']=STATUS
        else:
            print('V4DB_DRY_RUN_PASS: 10 frozen sample anchors; V4d-A canary '
                  'and V4c ancestry replayed; 0 XSim; FULL400 BLOCKED',flush=True)
        summaries=[r['summary'] for r in meta['records']]
        rows=[line for record in meta['records'] for line in record['target_rows']]
        write_csv(out/'sample_temporal_summary.csv',PROFILE_SUMMARY_COLUMNS,summaries)
        write_csv(out/'per_sample_timing_matrix.csv',PROFILE_MATRIX_COLUMNS,rows)
        meta['temporal_summary_sha256']=sha256(out/'sample_temporal_summary.csv')
        meta['timing_matrix_sha256']=sha256(out/'per_sample_timing_matrix.csv')
        meta['baseline_temporal_windows_passed']=sum(int(s['eligible_windows']) for s in summaries)
        meta['profile_event_timeline_identical_all_samples'] = (
            len(set(s['event_trace_sha256'] for s in summaries))==1
            if len(summaries)==PROFILE_COUNT else None)
        (out/'v4db_temporal_report.md').write_text(report_text(meta,summaries),encoding='utf-8')
        if args.execute:
            print('V4DB_TEMPORAL_PASS: 10/10 independent fault-free XSim profiles; '
                  '400/400 fault-free temporal-eligibility checks; '
                  'NOT 400 FI runs; FULL400 BLOCKED',flush=True)
    except Exception:
        meta['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        v4d.atomic_write_json(meta_path,meta)
    return meta


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--project',type=Path)
    ap.add_argument('--vivado',default='vivado')
    ap.add_argument('--fileset',default='sim_1')
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--v4-audit',type=Path,default=Path('fi/results/v4_cross_evidence_audit_02/v4_audit.json'))
    ap.add_argument('--v4b-a-dir',type=Path,default=Path('fi/results/v4b_six_mechanism_01'))
    ap.add_argument('--v4b-b-audit',type=Path,default=Path('fi/results/v4b_existing_consumer_reaudit_01/existing_consumer_audit.json'))
    ap.add_argument('--v4b-c-dir',type=Path,default=Path('fi/results/v4bc_three_01'))
    ap.add_argument('--v4c-audit',type=Path,default=Path('fi/results/v4c_readiness_audit_01/v4c_manifest.json'))
    ap.add_argument('--v4d-canary-dir',type=Path,default=Path('fi/results/v4d_canary_01'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4db_ten_sample_timing_01'))
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--build-mode',choices=('vivado-scripts','pinned-direct-prj'),
                    default='vivado-scripts',
                    help='Explicit alternate fresh xvlog/xelab from V3a pinned .prj; no scripts_only')
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--resume',action='store_true')
    args=ap.parse_args()
    try:
        run(args)
    except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
        raise SystemExit('V4DB_TEMPORAL_FAIL: '+str(exc))


if __name__=='__main__':
    main()

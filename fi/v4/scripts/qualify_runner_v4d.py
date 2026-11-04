#!/usr/bin/env python3
"""CNN_PEP V4d-A: fail-closed single-build XSim CANARY qualification only.

This program NEVER runs the full Pilot400, promotes a target, or certifies the
remaining nine samples' temporal exposure. Replays the frozen V4c ancestry,
then (when explicitly requested) builds one Vivado-authoritative XSim snapshot
and runs exactly 16 directed canaries: four classes x two bits/times x two
real MNIST samples. Each XSim process is independent and strictly parsed.

--resume preserves prior logs and refuses stale/partially written evidence.
A snapshot is rebuilt once for each resumed segment with identical HDL hashes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
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

from audit_readiness_v4c import (  # noqa: E402
    SCHEMA as V4C_SCHEMA, STATUS as V4C_STATUS,
    FULL400_STATUS as V4C_FULL400_STATUS, REVIEW_COLUMNS,
    check_coverage, check_v4bc, collect_v3b, collect_v4bb,
)
from audit_v4 import V3A_NAME, read_json, require, unique_index  # noqa: E402
from run_three_v4bc import verify_existing_audit  # noqa: E402
from pilot400_common import (  # noqa: E402
    FIELDS, ASSET_SIZES, SAMPLES, load_plan, read_csv, sha256,
    validate_assets, verify_manifest, write_csv,
)
from pilot400_direct_xsim import make_cmd_run, call_cmd  # noqa: E402
from run_pilot400_target_validation_v3 import (  # noqa: E402
    build_verified_snapshot, inspect_applied_and_result,
)

SCHEMA = 'CNN_PEP_V4D_A_SINGLE_BUILD_16_CANARY_V1'
PASS_STATUS = 'CANARY_16_XSIM_MECHANISMS_VERIFIED_FULL400_BLOCKED'
CANARY_TARGETS = ('CTL_0001', 'BUF_0031', 'MEM_0002', 'DAT_0010')
CANARY_SAMPLES = ('IMG0000', 'IMG0009')
CANARY_COUNT = 16
COLUMNS = (*FIELDS, 'outcome', 'injection_verified',
           'temporal_evidence_scope', 'consumer_proof_scope', 'log_sha256')
LOCK_SOURCE_FILES = (
    'fi/config/fi_targets_candidates_v2.csv',
    'fi/config/pilot400_targets.csv',
    'fi/scenarios/pilot400.csv',
    'fi/tb/tb_cnn_mnist_fi_pilot400.v',
    'fi/scripts/pilot400_common.py',
    'fi/scripts/pilot400_direct_xsim.py',
    'fi/scripts/run_pilot400_target_validation_v3.py',
    'fi/scripts/parse_fi_v2.py',
    'fi/v4/scripts/audit_readiness_v4c.py',
    'fi/v4/scripts/audit_v4.py',
    'fi/v4/scripts/audit_six_consumers_v4b.py',
    'fi/v4/scripts/run_six_v4b.py',
    'fi/v4/scripts/run_three_v4bc.py',
    'fi/v3b/scripts/run_mem1_v3b.py',
    'fi/scripts/run_pilot400.py',
    'fi/scripts/run_pilot400_control10.py',
    'fi/scripts/export_pilot400_samples.py',
    'fi/v4/scripts/qualify_runner_v4d.py',
    'fi/pilot400/samples/sample_provenance.json',
)


def resolved(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def canary_selection(manifest: list[dict[str, str]]) -> list[dict[str, str]]:
    """Exact 4 target x 2 sample x 2 config subset, in frozen manifest order."""
    rows = [r for r in manifest
            if r['target_id'] in CANARY_TARGETS and
            r['sample_id'] in CANARY_SAMPLES]
    require(len(rows) == CANARY_COUNT and
            len({r['scenario_id'] for r in rows}) == CANARY_COUNT,
            'V4d-A canary must contain exactly sixteen unique scenario IDs')
    for tid in CANARY_TARGETS:
        for sample in CANARY_SAMPLES:
            pair = [r for r in rows if r['target_id'] == tid and r['sample_id'] == sample]
            require(len(pair) == 2 and
                    len({(r['bit_index'],r['injection_offset']) for r in pair}) == 2,
                    'V4d-A canary pair missing or duplicated: ' + tid + '/' + sample)
            require(all(r['target_class'] == tid.split('_')[0] for r in pair),
                    'V4d-A target class mismatch: ' + tid)
    dat = [r for r in rows if r['target_id'] == 'DAT_0010']
    require(len(dat) == 4 and all(r['injection_offset'] == '1340' for r in dat) and
            {r['bit_index'] for r in dat} == {'0','1'},
            'DAT_0010 equal-offset bit0/bit1 refreeze not preserved')
    require(Counter(r['sample_id'] for r in rows) == {'IMG0000':8, 'IMG0009':8},
            'V4d-A canary sample balance changed')
    return rows


def replay_v4c(repo: Path, results: Path, weights: Path, v4c_path: Path,
               v4_path: Path, v4ba: Path, v4bb: Path, v4bc: Path,
               plan: list[dict[str, str]],
               manifest: list[dict[str, str]]) -> dict[str, Any]:
    """Reparse 40 historical proofs and compare against the SAVED V4c manifest.

    Does not create a second audit directory, modify previous evidence or rely
    merely on console PASS lines.
    """
    saved = read_json(v4c_path)
    require(saved.get('schema') == V4C_SCHEMA and saved.get('status') == V4C_STATUS and
            saved.get('targets_reviewed') == 20 and
            saved.get('directed_img0000_proofs') == 40 and
            saved.get('manifest_rows') == 400 and saved.get('sample_count') == 10 and
            saved.get('source_authority') == 'vivado' and
            saved.get('full400_authorized') is False and
            saved.get('full400_runner_qualified') is False and
            saved.get('frozen_target_statuses_modified') is False and
            saved.get('xsim_runs_added') == 0 and
            saved.get('full400_gate') == V4C_FULL400_STATUS,
            'Saved V4c qualification summary not trusted/unchanged')
    require(saved['manifest_sha256'] == sha256(repo / 'fi/scenarios/pilot400.csv') and
            saved['selected_plan_sha256'] == sha256(repo / 'fi/config/pilot400_targets.csv') and
            saved['original_tb_sha256'] == sha256(repo / 'fi/tb/tb_cnn_mnist_fi_pilot400.v') and
            saved['v4_audit_sha256'] == sha256(v4_path) and
            saved['v4b_a_manifest_sha256'] == sha256(v4ba / 'v4b_manifest.json') and
            saved['v4b_b_audit_sha256'] == sha256(v4bb) and
            saved['v4b_c_manifest_sha256'] == sha256(v4bc / 'v4bc_manifest.json') and
            saved['v4b_c_consumer_sha256'] == sha256(v4bc / 'consumer_validation.csv'),
            'Saved V4c source/parent SHA256 changed')
    require(saved['target_matrix_sha256'] ==
            sha256(v4c_path.parent / 'v4c_target_evidence_matrix.csv'),
            'Saved V4c target matrix SHA256 changed')
    existing = verify_existing_audit(repo,results,weights,v4_path,v4ba,v4bb)
    require(existing['direct_consumer_scenarios'] == 6,
            'V4b-B consumer re-audit not replayed')
    from audit_v4 import run_audit as replay_v4
    source = replay_v4(repo,results,weights)
    require(source['status'] == 'CROSS_EVIDENCE_PASS_FULL400_BLOCKED' and
            saved['control10_manifest_sha256'] == source['control10_manifest_sha256'] and
            saved['timing_audit_sha256'] == source['timing_audit_sha256'] and
            saved['ctl_inventory'] == source['ctl_inventory'],
            'V4 source/control/timing/CTL inventory mismatch')
    evidence = {}
    for part in (collect_v3b(repo,results,manifest),
                 collect_v4bb(v4bb,manifest),
                 check_v4bc(repo,results,v4ba,v4bb,v4bc,manifest)):
        require(not (set(part) & set(evidence)),
                'V4c historic evidence overlap')
        evidence.update(part)
    matrix = check_coverage(plan, manifest, evidence)
    require(matrix == read_csv(v4c_path.parent / 'v4c_target_evidence_matrix.csv',
                               REVIEW_COLUMNS),
            'V4c stored matrix differs from independent replay')
    require(Counter(ev['source'] for ev in evidence.values()) ==
            {'V3B_ISOLATED':28, 'V4B_B_ORIGINAL':6, 'V4B_C_ISOLATED':6},
            'V4c source split changed')
    return saved


def locked_inputs(repo: Path, weights: Path, v4c_path: Path,
                  v3a_path: Path) -> dict[str, str]:
    """Pin all ten golden asset sets and selected execution software files."""
    paths = [repo / p for p in LOCK_SOURCE_FILES]
    paths += [repo / 'fi/pilot400/samples' / sid / name
              for sid,_ in SAMPLES for name in ASSET_SIZES]
    from export_pilot400_samples import PARAMS
    paths += [weights / name for name in PARAMS]
    paths += [v4c_path, v3a_path]
    require(all(p.is_file() for p in paths),
            'V4d source/weights/evidence input missing: ' +
            ','.join(str(p) for p in paths if not p.is_file()))
    return {str(p.resolve()): sha256(p) for p in paths}


def check_input_lock(pins: dict[str,str]) -> None:
    require(bool(pins), 'Missing V4d input lock')
    for path, expected in pins.items():
        p=Path(path)
        require(p.is_file() and sha256(p)==expected,
                'V4d input changed since canary started: '+path)


def strict_result(log: Path, row: dict[str,str]) -> dict[str,str]:
    """Existing strict FI parser + honest cross-sample temporal scope."""
    parsed=inspect_applied_and_result(log,row)
    require(parsed['injection_verified']=='1' and parsed['outcome'],
            'FI mechanism validation not proven: '+row['scenario_id'])
    temporal = ('PROFILED_IMG0000_ONLY' if row['sample_id']=='IMG0000'
                else 'OTHER_SAMPLE_TEMPORAL_EXPOSURE_NOT_CERTIFIED')
    return {**{k:row[k] for k in FIELDS}, 'outcome':parsed['outcome'],
            'injection_verified':'1', 'temporal_evidence_scope':temporal,
            'consumer_proof_scope':'NOT_INDEPENDENTLY_MONITORED_V4D',
            'log_sha256':sha256(log)}


def atomic_write_json(path: Path, contents: dict) -> None:
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as handle:
        json.dump(contents,handle,sort_keys=True,indent=2,ensure_ascii=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def verify_done_records(out: Path, selected: list[dict[str,str]],
                        records: list[dict], pins: dict[str,str]) -> None:
    """A partial-run resume must verify *every* completed record and its logs.

    Unrecorded logs are explicitly rejected: they may be partial, stale or
    from an interrupted XSim. Never clobber them or silently skip.
    """
    require(len(records) <= len(selected), 'Excess V4d canary records')
    require([r.get('scenario_id') for r in records] ==
            [r['scenario_id'] for r in selected[:len(records)]],
            'V4d partial canary records not a contiguous frozen prefix')
    logged = {r.get('scenario_id') for r in records}
    runs=out/'runs'
    if runs.exists():
        present={p.stem for p in runs.glob('*.log') if not p.name.endswith('.console.log')
                 and not p.name.endswith('.wrapper.log')}
        require(present == logged,
                'Unrecorded, missing or stale XSim logs in partial run: '+
                repr(sorted(present ^ logged)))
    by_id={r['scenario_id']:r for r in selected}
    for record in records:
        sid=record['scenario_id']
        row=by_id[sid]
        for field, ext in (('log_sha256','.log'),('console_sha256','.console.log'),
                           ('wrapper_sha256','.wrapper.log'),('cmd_sha256','.cmd')):
            p=runs/(sid+ext)
            require(p.is_file() and sha256(p)==record.get(field),
                    'V4d existing run artifact missing/changed: '+str(p))
        log=runs/(sid+'.log')
        new=strict_result(log,row)
        require(new==record['validation'],
                'V4d strict log replay disagrees with saved row: '+sid)
    check_input_lock(pins)


def report_text(meta:dict, selected:list[dict[str,str]])->str:
    return '\n'.join([
        '# CNN_PEP V4d-A Single-Build XSim Canary', '',
        f'- Status: **{meta["status"]}**',
        f'- Selected configurations: **{len(selected)}** (NOT full400)',
        f'- Mechanism runs independently verified: **{len(meta["records"])}**',
        '- Selected: 4 classes x 2 configs x IMG0000/IMG0009',
        '- `IMG0009` temporal exposure is NOT certified by the IMG0000 timing profile.',
        '- No target promotion, no full400 approval, no physical SEU claim.',
        '- Original P0 RTL/FI TB/400-row manifest untouched.', '',
        '## Future full400 release criteria', '',
        '1. Replay V4c ancestry and review the V4d-A canary with zero parser failures.',
        '2. Demonstrate crash/resume handling against pinned source/console/log SHA256.',
        '3. Verify temporal exposure or a justified deterministic timing invariant across all 10 images.',
        '4. Separately approve a frozen 400-run execution protocol and capture a campaign-level run manifest.',
        '5. Retain outcome/stage metrics as directed test configurations, not SEU rates.', '',
    ])


def run(args:argparse.Namespace)->dict:
    repo=args.repo.resolve()
    results=resolved(repo,args.results_dir)
    weights=resolved(repo,args.weights_dir)
    v4c_path=resolved(repo,args.v4c_audit)
    v4_path=resolved(repo,args.v4_audit)
    v4ba=resolved(repo,args.v4b_a_dir)
    v4bb=resolved(repo,args.v4b_b_audit)
    v4bc=resolved(repo,args.v4b_c_dir)
    v3a_path=results/V3A_NAME/'validation_manifest.json'
    out=resolved(repo,args.out_dir)
    require(args.timeout_seconds>=10, 'V4d timeout must be >=10 seconds')
    require(not (args.resume and not args.execute), '--resume requires --execute')
    require(not (args.resume and not out.exists()),
            '--resume requires prior V4d output directory')
    require(args.resume or not out.exists(),
            'Refusing to overwrite prior V4d canary evidence: '+str(out))
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    require(len(plan)==20 and len(manifest)==400,
            'V4d frozen source plan must remain exactly 20/400')
    count,missing=validate_assets(repo/'fi/pilot400/samples',True)
    require(count==10 and not missing, 'Ten golden samples missing or changed')
    selected=canary_selection(manifest)
    v4c = replay_v4c(repo,results,weights,v4c_path,v4_path,v4ba,v4bb,v4bc,
                     plan,manifest)
    require(v4c['full400_authorized'] is False,
            'V4d canary cannot use an automatically approved full400')
    # V4c ancestry replays Control10, timing gate, and original RTL hash.
    v4_meta=read_json(v4_path)
    require(bool(v4_meta['control10_manifest_sha256']) and
            bool(v4_meta['timing_audit_sha256']),
            'V4 recorded control/timing proofs missing')
    pins=locked_inputs(repo,weights,v4c_path,v3a_path)
    project=resolved(repo,args.project) if args.project else None
    if args.execute:
        require(project is not None and project.is_file(),
                '--execute requires Vivado --project .xpr')
        require(args.vivado is not None and bool(args.vivado),
                '--execute requires --vivado')
        # P0 weights are pinned by locked_inputs and checked in the V4 ancestry.
    input_signature={'pins':pins,'project':str(project) if project else None,
                     'vivado':str(args.vivado),'fileset':args.fileset,
                     'canary_count':CANARY_COUNT,
                     'samples':list(CANARY_SAMPLES),'targets':list(CANARY_TARGETS)}
    meta_file=out/'v4d_canary_manifest.json'
    if args.resume:
        meta=read_json(meta_file)
        require(meta.get('schema')==SCHEMA and meta.get('input_signature')==input_signature and
                meta.get('full400_authorized') is False and
                meta.get('status') in ('INTERRUPTED_OR_INVALID','RUNNING','SNAPSHOT_BUILT'),
                'V4d resume mismatch or invalid prior status/inputs')
        require(sha256(out/'selected_scenarios.csv')==meta.get('selected_sha256') and
                read_csv(out/'selected_scenarios.csv',FIELDS)==selected,
                'V4d resume selected scenarios changed')
        verify_done_records(out,selected,meta['records'],pins)
    else:
        out.mkdir(parents=True,exist_ok=False)
        write_csv(out/'selected_scenarios.csv',FIELDS,selected)
        meta={'schema':SCHEMA,
              'created_utc':datetime.now(timezone.utc).isoformat(),
              'status':'DRY_RUN_ONLY','input_signature':input_signature,
              'selected_sha256':sha256(out/'selected_scenarios.csv'),
              'full400_authorized':False,'full400_runner_qualified':False,
              'target_status_modified':False,'run_scope':'V4D_A_16_CANARY_ONLY',
              'records':[],'builds':[],'v4c_audit_sha256':sha256(v4c_path)}
        atomic_write_json(meta_file,meta)
    try:
        if args.execute:
            assert project is not None
            if args.resume:
                require(len(meta['records'])<len(selected),
                        'No missing canaries to resume')
            meta['status']='RUNNING'
            atomic_write_json(meta_file,meta)
            old_build_indices=[int(p.name[6:]) for p in out.glob('build_[0-9][0-9][0-9]')
                               if p.is_dir()]
            build_index=max(old_build_indices,default=0)+1
            build_dir=out/f'build_{build_index:03d}'
            require(not build_dir.exists(), 'V4d build output already exists')
            build_dir.mkdir()
            snap=build_verified_snapshot(repo,project,args.vivado,args.fileset,
                                         build_dir,args.timeout_seconds)
            prior=read_json(v3a_path)
            require(snap['source_hashes']==prior['snapshot_build']['source_hashes'],
                    'V4d Vivado RTL source differs from V3a verified snapshot')
            require(snap['compile_prj_sha256']==
                    sha256(results/V3A_NAME/'source_vlog.prj'),
                    'V4d Vivado compile source list differs from V3a')
            build_meta={'build_id':build_index,
                        'elaboration_mode':snap['elaboration_mode'],
                        'compile_prj_sha256':snap['compile_prj_sha256'],
                        'compile_log_sha256':snap['compile_log_sha256'],
                        'elaborate_log_sha256':snap['elaborate_log_sha256'],
                        'source_hashes':snap['source_hashes'],
                        'build_dir':str(build_dir)}
            meta['builds'].append(build_meta)
            meta['status']='SNAPSHOT_BUILT'
            atomic_write_json(meta_file,meta)
            tcl=build_dir/'runall.tcl'
            tcl.write_text('run all\nquit\n',encoding='ascii')
            runs=out/'runs'
            runs.mkdir(exist_ok=True)
            for i,row in enumerate(selected,1):
                if i<=len(meta['records']):
                    continue
                check_input_lock(pins)
                sid=row['scenario_id']
                sample=repo/'fi/pilot400/samples'/row['sample_id']
                log=runs/(sid+'.log')
                console=runs/(sid+'.console.log')
                wrapper=runs/(sid+'.wrapper.log')
                cmd=runs/(sid+'.cmd')
                for artifact in (log,console,wrapper,cmd):
                    require(not artifact.exists(),
                            'V4d refuses to overwrite prior scenario artifact: '+str(artifact))
                cmd.write_text(make_cmd_run(snap['simdir'],snap['vivado_root'],row,
                                            sample,tcl,log,console,profile_only=False),
                               encoding='ascii')
                print('V4D_CANARY_XSIM '+str(i)+'/'+str(len(selected))+' '+sid+' '+
                      row['sample_id']+' '+row['target_id']+' bit='+row['bit_index']+
                      ' offset='+row['injection_offset'],flush=True)
                call_cmd(cmd,snap['simdir'],wrapper,args.timeout_seconds,'V4d canary '+sid)
                require(log.is_file() and console.is_file(),
                        'V4d XSim missing log/console: '+sid)
                content=console.read_text(encoding='utf-8',errors='replace')
                require('Expected a switch' not in content and
                        not any(line.startswith(('ERROR:', 'FATAL:'))
                                for line in content.splitlines()),
                        'V4d XSim console error: '+sid)
                result=strict_result(log,row)
                check_input_lock(pins)
                require(all(Path(p).is_file() and sha256(Path(p))==h
                            for p,h in snap['source_hashes'].items()),
                        'Vivado HDL changed after scenario: '+sid)
                meta['records'].append({'scenario_id':sid,
                    'validation':result, 'log_sha256':sha256(log),
                    'console_sha256':sha256(console),
                    'wrapper_sha256':sha256(wrapper), 'cmd_sha256':sha256(cmd),
                    'build_id':build_index})
                atomic_write_json(meta_file,meta)
                print('V4D_CANARY_SCENARIO_MECHANISM_PASS: '+sid+' '+
                      result['outcome'],flush=True)
            require(len(meta['records'])==CANARY_COUNT,
                    'V4d did not validate all sixteen canaries')
            verify_done_records(out,selected,meta['records'],pins)
            meta['status']=PASS_STATUS
        else:
            print('V4D_CANARY_DRY_RUN_PASS: 16 frozen scenarios; '+
                  'V4c ancestry replayed; 0 new XSim runs; FULL400 BLOCKED',
                  flush=True)
        rows=[r['validation'] for r in meta['records']]
        write_csv(out/'canary_validation.csv',COLUMNS,rows)
        meta['validation_csv_sha256']=sha256(out/'canary_validation.csv')
        (out/'v4d_canary_report.md').write_text(report_text(meta,selected),encoding='utf-8')
        if args.execute:
            print('V4D_CANARY_PASS: 16/16 independent XSim mechanisms verified; '+
                  '4 classes, IMG0000/IMG0009; FULL400 BLOCKED',flush=True)
    except Exception:
        meta['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        atomic_write_json(meta_file,meta)
    return meta


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--project',type=Path)
    ap.add_argument('--vivado',type=str,default='vivado')
    ap.add_argument('--fileset',default='sim_1')
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--v4-audit',type=Path,
                    default=Path('fi/results/v4_cross_evidence_audit_02/v4_audit.json'))
    ap.add_argument('--v4b-a-dir',type=Path,
                    default=Path('fi/results/v4b_six_mechanism_01'))
    ap.add_argument('--v4b-b-audit',type=Path,
                    default=Path('fi/results/v4b_existing_consumer_reaudit_01/existing_consumer_audit.json'))
    ap.add_argument('--v4b-c-dir',type=Path,
                    default=Path('fi/results/v4bc_three_01'))
    ap.add_argument('--v4c-audit',type=Path,
                    default=Path('fi/results/v4c_readiness_audit_01/v4c_manifest.json'))
    ap.add_argument('--out-dir',type=Path,
                    default=Path('fi/results/v4d_canary_01'))
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--resume',action='store_true')
    args=ap.parse_args()
    repo=args.repo.resolve()
    try:
        run(args)
    except (ValueError,TypeError,KeyError,OSError,RuntimeError) as e:
        raise SystemExit('V4D_CANARY_FAIL: '+str(e))


if __name__=='__main__':
    main()

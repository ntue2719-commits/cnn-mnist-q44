#!/usr/bin/env python3
"""CNN_PEP V4f Phase 2: immutable batch planner + strictly bounded XSim smoke.

This code NEVER executes a 400-FI campaign and NEVER grants G0-G7 release.
Supported scopes:
  batch-plan: offline plan for a contiguous (<=25) frozen manifest slice, 0 FI.
  qualification-smoke: exactly 8 fresh sample/config tuples across four classes,
     with optional explicit Windows --execute, pinned direct snapshot, atomic
     checkpoints, strict FI parser and fail-closed prefix resume.
  full400: reserved; always rejected before touching output.

The 8-run smoke is an execution-engine qualification, not consumer proof or
statistical sampling. It does not promote frozen target status.  Partial XSim
artifacts cannot be automatically recovered; they require a new evidence
attempt with an explicit, independent review of the failed attempt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
for directory in (HERE, HERE.parents[1]/'scripts', HERE.parents[1]/'v3b/scripts'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from audit_v4 import V3A_NAME, read_json, require  # noqa: E402
from pilot400_common import (FIELDS, load_plan, verify_manifest, write_csv,
                             read_csv, sha256)  # noqa: E402
from pilot400_direct_xsim import make_cmd_run, call_cmd  # noqa: E402
from pinned_direct_snapshot_v4db import build_pinned_direct_snapshot  # noqa: E402
import qualify_full400_v4f as v4f  # noqa: E402
import qualify_runner_v4d as v4da  # noqa: E402

SCHEMA = 'CNN_PEP_V4F_PHASE2_BOUNDED_BATCH_ENGINE_V1'
STATUS_PLANNED = 'NONEXECUTING_PLAN_FULL400_BLOCKED'
STATUS_RUNNING = 'SMOKE_RUNNING_FULL400_BLOCKED'
STATUS_PAUSED = 'SMOKE_PAUSED_FOR_EXPLICIT_RESUME_FULL400_BLOCKED'
STATUS_COMPLETE = 'EIGHT_SMOKE_XSIM_VERIFIED_FULL400_BLOCKED'
STATUS_INVALID = 'INTERRUPTED_OR_INVALID_FULL400_BLOCKED'
SMOKE_SAMPLES = ('IMG0001', 'IMG0008')
SMOKE_TARGETS = ('CTL_0001', 'BUF_0031', 'MEM_0002', 'DAT_0010')
SMOKE_IDS = ('FI_P400_0003', 'FI_P400_0018', 'FI_P400_0163',
             'FI_P400_0178', 'FI_P400_0223', 'FI_P400_0238',
             'FI_P400_0383', 'FI_P400_0398')
SMOKE_COUNT = 8
MAX_BATCH_PLAN = 25
RUN_COLUMNS = v4da.COLUMNS
RESULTS_DIRNAME = 'fi/results'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolved(repo: Path, value: Path) -> Path:
    return v4f.resolved(repo, value)


def select_smoke(frozen: list[dict[str, str]],
                 plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Two genuinely new images; one config/target/image, alternating A/B."""
    selected = [r for r in frozen if r['scenario_id'] in SMOKE_IDS]
    require(tuple(r['scenario_id'] for r in selected) == SMOKE_IDS,
            'V4f Phase 2 smoke scenario identity/order mismatch')
    by_target = {x['target_id']: x for x in plan}
    require(set(by_target) >= set(SMOKE_TARGETS), 'Smoke target not in frozen plan')
    for target in SMOKE_TARGETS:
        pair = [r for r in selected if r['target_id'] == target]
        p = by_target[target]
        require(len(pair) == 2 and
                {r['sample_id'] for r in pair} == set(SMOKE_SAMPLES) and
                {(r['sample_id'], r['bit_index'], r['injection_offset']) for r in pair}
                == {(SMOKE_SAMPLES[0],p['bit_index'],p['offset_a']),
                    (SMOKE_SAMPLES[1],p['bit_index_b'],p['offset_b'])},
                'V4f smoke A/B configuration or samples altered: ' + target)
    dat = [r for r in selected if r['target_id'] == 'DAT_0010']
    require([(r['bit_index'], r['injection_offset']) for r in dat] ==
            [('0','1340'),('1','1340')],
            'DAT_0010 must use bit0 and bit1, both at offset1340')
    return selected


def select_batch(frozen: list[dict[str,str]], start: int,
                 size: int) -> list[dict[str,str]]:
    require(1 <= start <= 400, 'Batch start must be within 1..400')
    require(1 <= size <= MAX_BATCH_PLAN, 'Batch size must be 1..25')
    require(start + size - 1 <= 400, 'Batch cannot extend past FI_P400_0400')
    selected = frozen[start - 1: start - 1 + size]
    require(len(selected)==size and all(
        selected[i]['scenario_id'] == f'FI_P400_{start+i:04d}'
        for i in range(size)), 'Noncontiguous frozen batch selection')
    return selected


def _source_pins(repo: Path, results: Path, weights: Path,
                 v4e_dir: Path) -> dict[str,str]:
    assets = v4f.check_assets_and_provenance(repo,weights)
    pins = v4f.source_pins(repo, assets, results,
                          v4e_dir/'v4e_release_review.json')
    own = HERE/'run_full400_batches_v4f.py'
    require(own.is_file(), 'Missing V4f Phase 2 source')
    pins[str(own.resolve())] = sha256(own)
    for relative in ('v4e_release_review.json', 'v4e_shakedown_matrix.csv'):
        evidence=v4e_dir/relative
        if evidence.is_file():
            pins[str(evidence.resolve())]=sha256(evidence)
    for relative in v4f.PARENT_NAMES.values():
        parent=results/relative
        if parent.is_file():
            pins[str(parent.resolve())]=sha256(parent)
    return dict(sorted(pins.items()))


def check_pins(pins: dict[str,str]) -> None:
    require(bool(pins) and all(Path(p).is_file() and sha256(Path(p))==digest
                               for p,digest in pins.items()),
            'V4f Phase 2 source/asset/evidence SHA256 changed')


def safe_out(repo: Path, results: Path, weights: Path,
             out: Path, *, resume: bool) -> None:
    """No writing into frozen inputs/evidence or to an existing other attempt."""
    require(out.parent == results and out.name.startswith('v4f2_'),
            'V4f2 output must be a new fi/results/v4f2_* sibling directory')
    require(out != results and not out.is_symlink() and not results.is_symlink(),
            'Refusing symlink or results-root output')
    require(out.is_dir() if resume else not out.exists(),
            'Resume needs an existing directory; new run must not overwrite one')
    if not resume:
        v4f.safety_output(repo,out,weights,results)


@contextmanager
def exclusive_evidence_lock(out: Path):
    """One writer per attempt. OS locks are released after process termination.

    A persistent .lock file is harmless; an already-held OS lock is a hard
    failure. On network filesystems, operator must independently confirm
    correct locking semantics before relying on concurrent safety.
    """
    lock = out/'.v4f2.lock'
    require(not lock.is_symlink(), 'V4f2 refuses symlink evidence lock')
    flags=os.O_RDWR | os.O_CREAT
    if hasattr(os,'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(lock,flags,0o600),'r+b') as handle:
        if lock.stat().st_size == 0:
            handle.write(b'\x00')
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('V4f2 another process owns evidence lock') from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError('V4f2 another process owns evidence lock') from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _metadata(repo: Path, out: Path, selected: list[dict[str,str]],
              mode: str, pins: dict[str,str], ancestry: bool,
              signature: dict[str,Any]) -> dict[str,Any]:
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_scenarios.csv',FIELDS,selected)
    (out/'analysis_protocol_DRAFT.json').write_text(
        json.dumps(v4f.PROTOCOL_DRAFT,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    obj = {
        'schema':SCHEMA, 'status':STATUS_PLANNED, 'created_utc':_now(),
        'mode':mode, 'selected_count':len(selected),
        'selected_sha256':sha256(out/'selected_scenarios.csv'),
        'frozen_manifest_sha256':v4f.FROZEN_HASHES['fi/scenarios/pilot400.csv'],
        'frozen_plan_sha256':v4f.FROZEN_HASHES['fi/config/pilot400_targets.csv'],
        'input_signature':signature, 'input_pins':pins,
        'ancestry_replayed':ancestry,
        'scientific_protocol_sha256':sha256(out/'analysis_protocol_DRAFT.json'),
        'scientific_protocol_frozen':False,
        'records':[], 'builds':[], 'xsim_runs_committed':0,
        'full400_runner_qualified':False,'full400_authorized':False,
        'full400_executed':False,'target_status_modified':False,
        'full400_gate':'BLOCKED_EXPLICIT_INDEPENDENT_G0_G7_RELEASE_REQUIRED',
        'uncommitted_artifacts_auto_recovered':False,
    }
    v4da.atomic_write_json(out/'v4f2_manifest.json',obj)
    return obj


def strict_result(log: Path, row: dict[str,str]) -> dict[str,str]:
    """Existing strict FI parser; never claim E3 consumer from mechanism logs."""
    proof=v4da.strict_result(log,row)
    require(proof['injection_verified']=='1' and proof['outcome'],
            'V4f2 missing strict mechanism proof: '+row['scenario_id'])
    proof['temporal_evidence_scope']='V4DB_10_SAMPLE_BASELINE_ELIGIBILITY_ONLY'
    proof['consumer_proof_scope']='NOT_INDEPENDENTLY_MONITORED_V4F2'
    return proof


def verify_records(out: Path, selected: list[dict[str,str]],
                   meta: dict[str,Any], pins: dict[str,str],
                   v3a: Path, *, allow_complete: bool=False) -> None:
    """Fail closed on missing, orphaned, edited or partially committed evidence."""
    require(meta.get('schema')==SCHEMA and
            meta.get('full400_authorized') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('full400_executed') is False and
            meta.get('target_status_modified') is False and
            meta.get('scientific_protocol_frozen') is False,
            'V4f2 metadata has unauthorized release/promotion')
    require(meta.get('input_pins')==pins, 'V4f2 input pins changed')
    require(out.joinpath('selected_scenarios.csv').is_file() and
            meta.get('selected_sha256')==sha256(out/'selected_scenarios.csv') and
            read_csv(out/'selected_scenarios.csv', FIELDS)==selected,
            'V4f2 selected configuration CSV changed')
    require(meta.get('scientific_protocol_sha256')==sha256(out/'analysis_protocol_DRAFT.json') and
            read_json(out/'analysis_protocol_DRAFT.json')==v4f.PROTOCOL_DRAFT,
            'V4f2 draft protocol changed')
    records=meta.get('records',[])
    builds=meta.get('builds',[])
    require(isinstance(records,list) and len(records)<=len(selected) and
            [r.get('scenario_id') for r in records] ==
                [r['scenario_id'] for r in selected[:len(records)]] and
            meta.get('xsim_runs_committed')==len(records),
            'V4f2 committed scenario sequence or count changed')
    require(isinstance(builds,list) and len({b.get('build_id') for b in builds})==len(builds) and
            [b.get('build_id') for b in builds]==list(range(1,len(builds)+1)),
            'V4f2 build IDs duplicated or not contiguous')
    expected_builds={f'build_{b["build_id"]:03d}' for b in builds}
    actual_builds={p.name for p in out.glob('build_[0-9][0-9][0-9]') if p.is_dir()}
    require(expected_builds==actual_builds,
            'Unregistered or missing XSim build directory; no automatic recovery')
    prior=read_json(v3a/'validation_manifest.json')
    expected_hashes=prior['snapshot_build']['source_hashes']
    expected_prj=sha256(v3a/'source_vlog.prj')
    for b in builds:
        root=out/f'build_{b["build_id"]:03d}'
        require(not root.is_symlink(), 'V4f2 refuses symlinked build directory')
        require(b['source_hashes']==expected_hashes and
                b['compile_prj_sha256']==expected_prj and
                sha256(root/'source_vlog.prj')==expected_prj and
                sha256(root/'compile_snapshot.log')==b['compile_log_sha256'] and
                sha256(root/'elaborate_snapshot.log')==b['elaborate_log_sha256'] and
                sha256(root/'runall.tcl')==b['tcl_sha256'],
                'V4f2 registered source snapshot was changed')
    runs=out/'runs'
    require(not runs.is_symlink(), 'V4f2 refuses symlinked XSim result directory')
    require(not (out/'v4f2_manifest.json.tmp').exists(),
            'V4f2 uncommitted manifest temp remains; refuse resume')
    root_allowed={'selected_scenarios.csv','analysis_protocol_DRAFT.json',
                  'v4f2_manifest.json','.v4f2.lock'} | expected_builds
    if runs.exists(): root_allowed.add('runs')
    if meta.get('status')==STATUS_COMPLETE:
        root_allowed.add('smoke_validation.csv')
    require({p.name for p in out.iterdir()} <= root_allowed,
            'V4f2 unexpected root artifact or partial validation CSV')
    allowed={r['scenario_id']+suffix for r in records
             for suffix in ('.log','.console.log','.wrapper.log','.cmd')}
    present={p.name for p in runs.iterdir()} if runs.exists() else set()
    require(present==allowed,
            'V4f2 missing, uncommitted or orphan XSim artifact; no auto-cleanup')
    for r,row in zip(records,selected):
        sid=row['scenario_id']
        require(r['build_id'] in {b['build_id'] for b in builds},
                'V4f2 record references unknown snapshot')
        for key,suffix in (('log_sha256','.log'),('console_sha256','.console.log'),
                           ('wrapper_sha256','.wrapper.log'),('cmd_sha256','.cmd')):
            p=runs/(sid+suffix)
            require(p.is_file() and not p.is_symlink() and sha256(p)==r[key],
                    'V4f2 prior artifact missing or changed: '+str(p))
        require(r['validation']==strict_result(runs/(sid+'.log'),row),
                'V4f2 prior FI strict replay differed: '+sid)
    require(not (out/'v4f2_manifest.json.tmp').exists(),
            'V4f2 uncommitted manifest temp remains; refuse resume')
    check_pins(pins)
    if not allow_complete:
        require(len(records)<len(selected), 'V4f2 already complete; cannot resume')


def _build_and_run(repo:Path, project:Path, vivado:str, fileset:str,
                   out:Path, results:Path, timeout:int, meta:dict[str,Any],
                   selected:list[dict[str,str]], pins:dict[str,str],
                   pause_after:int) -> dict[str,Any]:
    v3a=results/V3A_NAME
    build_id=len(meta['builds'])+1
    build=out/f'build_{build_id:03d}'
    require(not build.exists(),'Refusing to overwrite XSim snapshot')
    build.mkdir(parents=True,exist_ok=False)
    snap=build_pinned_direct_snapshot(repo,project,vivado,fileset,build,timeout,v3a)
    prior=read_json(v3a/'validation_manifest.json')
    require(snap['source_hashes']==prior['snapshot_build']['source_hashes'] and
            snap['compile_prj_sha256']==sha256(v3a/'source_vlog.prj') and
            snap['elaboration_mode']=='FULL_BUILD',
            'V4f2 snapshot not equal to V3a authenticated RTL')
    tcl=build/'runall.tcl'
    tcl.write_text('run all\nquit\n',encoding='ascii')
    meta['builds'].append({
        'build_id':build_id,'source_hashes':snap['source_hashes'],
        'compile_prj_sha256':snap['compile_prj_sha256'],
        'compile_log_sha256':snap['compile_log_sha256'],
        'elaborate_log_sha256':snap['elaborate_log_sha256'],
        'tcl_sha256':sha256(tcl),
        'build_mode':'pinned-direct-prj',
    })
    v4da.atomic_write_json(out/'v4f2_manifest.json',meta)
    runs=out/'runs';runs.mkdir(exist_ok=True)
    for pos,row in enumerate(selected,1):
        if pos<=len(meta['records']): continue
        check_pins(pins)
        require(sha256(out/'selected_scenarios.csv')==meta['selected_sha256'] and
                sha256(out/'analysis_protocol_DRAFT.json')==meta['scientific_protocol_sha256'],
                'V4f2 selection/protocol changed during FI execution')
        sid=row['scenario_id']
        paths={suffix:runs/(sid+suffix) for suffix in
               ('.log','.console.log','.wrapper.log','.cmd')}
        require(all(not p.exists() for p in paths.values()),
                'V4f2 refuses existing scenario artifact: '+sid)
        cmd=paths['.cmd'];log=paths['.log'];console=paths['.console.log']
        wrapper=paths['.wrapper.log']
        cmd.write_text(make_cmd_run(snap['simdir'],snap['vivado_root'],row,
                     repo/'fi/pilot400/samples'/row['sample_id'],tcl,log,console,
                     profile_only=False),encoding='ascii')
        print(f'V4F2_SMOKE_XSIM {pos}/8 {sid} {row["sample_id"]} '
              f'{row["target_id"]} bit={row["bit_index"]} '
              f'offset={row["injection_offset"]}',flush=True)
        call_cmd(cmd,snap['simdir'],wrapper,timeout,'V4f2 smoke '+sid)
        require(log.is_file() and console.is_file(),
                'V4f2 XSim output missing: '+sid)
        txt=console.read_text(encoding='utf-8',errors='replace')
        require('Expected a switch' not in txt and
                not re.search(r'^(?:ERROR:|FATAL:|Fatal:|\*\* Error:)',txt,re.M),
                'V4f2 XSim console reported error: '+sid)
        validation=strict_result(log,row)
        check_pins(pins)
        require(all(Path(p).is_file() and sha256(Path(p))==digest
                    for p,digest in snap['source_hashes'].items()),
                'V4f2 Vivado HDL modified during FI execution')
        meta['records'].append({
            'scenario_id':sid, 'build_id':build_id, 'validation':validation,
            'log_sha256':sha256(log),'console_sha256':sha256(console),
            'wrapper_sha256':sha256(wrapper),'cmd_sha256':sha256(cmd),
        })
        meta['xsim_runs_committed']=len(meta['records'])
        v4da.atomic_write_json(out/'v4f2_manifest.json',meta)
        print(f'V4F2_SMOKE_SCENARIO_PASS: {sid} {validation["outcome"]}',flush=True)
        if pause_after and len(meta['records']) == pause_after:
            meta['status']=STATUS_PAUSED
            v4da.atomic_write_json(out/'v4f2_manifest.json',meta)
            print(f'V4F2_SMOKE_PAUSED: {pause_after}/8 committed; '
                  'resume with --resume; FULL400 BLOCKED',flush=True)
            return meta
    verify_records(out,selected,meta,pins,v3a,allow_complete=True)
    require(len(meta['records'])==SMOKE_COUNT,
            'V4f2 smoke not complete')
    write_csv(out/'smoke_validation.csv',RUN_COLUMNS,
              [r['validation'] for r in meta['records']])
    meta['validation_csv_sha256']=sha256(out/'smoke_validation.csv')
    meta['status']=STATUS_COMPLETE
    v4da.atomic_write_json(out/'v4f2_manifest.json',meta)
    print('V4F2_SMOKE_PASS: 8/8 XSim mechanism verified; '
          'IMG0001/IMG0008; not 400 FI; FULL400 BLOCKED',flush=True)
    return meta


def run(args:argparse.Namespace) -> dict[str,Any]:
    """Run with full validation before mutating an evidence directory."""
    require(args.mode!='full400',
            'V4f Phase 2 cannot run Full400; independent G0-G7 release absent')
    require(args.mode in ('batch-plan','qualification-smoke'),
            'Unknown V4f2 execution mode')
    require(not args.execute or args.mode=='qualification-smoke',
            '--execute only allowed for bounded 8-run qualification smoke')
    require(not args.resume or (args.execute and args.mode=='qualification-smoke'),
            '--resume requires smoke --execute')
    require(not args.resume or not args.pause_after,
            '--pause-after is only for a fresh smoke attempt, not --resume')
    require(args.timeout_seconds>=10, 'Per-XSim timeout must be >=10 seconds')
    require(not args.pause_after or (args.execute and 1<=args.pause_after<8),
            '--pause-after applies only to executing smoke, 1..7')
    if args.mode=='qualification-smoke':
        require(args.batch_start==1 and args.batch_size==25,
                'Batch flags are reserved for non-executing batch-plan')
    repo=args.repo.resolve()
    results=_resolved(repo,args.results_dir)
    weights=_resolved(repo,args.weights_dir)
    v4e_dir=_resolved(repo,args.v4e_dir)
    out=_resolved(repo,args.out_dir)
    safe_out(repo,results,weights,out,resume=args.resume)
    v4f.check_frozen(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    frozen=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    v4f.select_400(frozen,plan)
    selected=(select_smoke(frozen,plan) if args.mode=='qualification-smoke'
              else select_batch(frozen,args.batch_start,args.batch_size))
    pins=_source_pins(repo,results,weights,v4e_dir)
    ancestry=args.replay_ancestry or args.execute
    if ancestry:
        v4e_meta,matrix=v4f.verify_v4e_saved(repo,results,v4e_dir)
        v4f.replay_v4e_raw(repo,results,weights,v4e_dir,
                            v4e_meta,matrix,frozen,plan)
    project=_resolved(repo,args.project) if args.project else None
    if args.execute:
        require(os.name=='nt','V4f2 XSim execution requires Windows')
        require(project is not None and project.is_file() and project.suffix.lower()=='.xpr',
                'V4f2 execution requires Vivado --project .xpr')
        require(bool(args.vivado), 'V4f2 execution requires --vivado')
        require((results/V3A_NAME/'validation_manifest.json').is_file() and
                (results/V3A_NAME/'source_vlog.prj').is_file(),
                'V4f2 execution requires V3a pinned source evidence')
    signature={'mode':args.mode,'project':str(project) if args.execute else None,
               'vivado':str(args.vivado) if args.execute else None,
               'fileset':args.fileset if args.execute else None,
               'selected_ids':[r['scenario_id'] for r in selected],
               'v4e_review_sha256':sha256(v4e_dir/'v4e_release_review.json')
                                      if ancestry else None}
    if not args.resume:
        meta=_metadata(repo,out,selected,args.mode,pins,ancestry,signature)
    with exclusive_evidence_lock(out) if args.execute else nullcontext():
        if args.resume:
            meta=read_json(out/'v4f2_manifest.json')
            require(meta.get('mode')=='qualification-smoke' and
                    meta.get('status') in (STATUS_RUNNING,STATUS_PAUSED,STATUS_INVALID) and
                    meta.get('input_signature')==signature and
                    meta.get('ancestry_replayed') is True,
                    'V4f2 resume input/previous status mismatch')
            verify_records(out,selected,meta,pins,results/V3A_NAME)
        if not args.execute:
            print(f'V4F2_PLAN_PASS: {len(selected)}/{len(frozen)} selected frozen tuples; '
                  f'mode={args.mode}; ancestry_replayed={ancestry}; '
                  '0 XSim; FULL400 BLOCKED',flush=True)
            print('V4F2_OUTPUT: '+str(out),flush=True)
            return meta
        meta['status']=STATUS_RUNNING
        v4da.atomic_write_json(out/'v4f2_manifest.json',meta)
        try:
            assert project is not None
            return _build_and_run(repo,project,args.vivado,args.fileset,
                                  out,results,args.timeout_seconds,meta,
                                  selected,pins,args.pause_after)
        except BaseException:
            meta['status']=STATUS_INVALID
            v4da.atomic_write_json(out/'v4f2_manifest.json',meta)
            raise


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--v4e-dir',type=Path,
                    default=Path('fi/results/v4e_release_review_01'))
    ap.add_argument('--out-dir',type=Path,
                    default=Path('fi/results/v4f2_batch_plan_01'))
    ap.add_argument('--mode',choices=('batch-plan','qualification-smoke','full400'),
                    default='batch-plan')
    ap.add_argument('--batch-start',type=int,default=1)
    ap.add_argument('--batch-size',type=int,default=25)
    ap.add_argument('--replay-ancestry',action='store_true')
    ap.add_argument('--project',type=Path)
    ap.add_argument('--vivado',default='')
    ap.add_argument('--fileset',default='sim_1')
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--resume',action='store_true')
    ap.add_argument('--pause-after',type=int,default=0,
                    help='Only qualification smoke: pause after N committed FI runs')
    args=ap.parse_args()
    try:
        run(args)
    except (ValueError,RuntimeError,KeyError,OSError,TypeError) as exc:
        raise SystemExit('V4F2_FAIL: '+str(exc)) from exc

if __name__=='__main__':
    main()

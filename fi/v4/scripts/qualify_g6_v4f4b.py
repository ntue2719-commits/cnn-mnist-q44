#!/usr/bin/env python3
"""CNN_PEP V4f Phase 4B: G6 offline 400-tuple scheduler/ledger qualification.

NO FI IS EXECUTED by this tool. `plan` checks the full frozen schedule,
`probe` runs a synthetic 400-event *scheduler* with atomic checkpoints and
fail-closed resume, and `audit` independently replays synthetic receipts.
These events are NOT XSim logs and must NEVER be counted as FI evidence.

The Windows preflight also read-only verifies the existing V4e ancestry,
V4f Phase 1, the V4f2 8-real-XSim smoke/audit, V4f3 review, and the V4f4
UNAPPROVED G7 preparation. Passing G6 *shadow* qualification does not
qualify or authorize a Full400 execution backend.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
for directory in (HERE, HERE.parents[1]/'scripts', HERE.parents[1]/'v3b/scripts'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from audit_v4 import read_json, require, V3A_NAME  # noqa: E402
from pilot400_common import FIELDS, load_plan, verify_manifest, write_csv, read_csv, sha256  # noqa: E402
import qualify_full400_v4f as v4f  # noqa: E402
import run_full400_batches_v4f as v4f2  # noqa: E402
import review_release_v4f3 as v4f3  # noqa: E402
import qualify_science_v4f4 as v4f4  # noqa: E402
import qualify_runner_v4d as v4da  # noqa: E402

SCHEMA = 'CNN_PEP_V4F4B_G6_SHADOW_LEDGER_V1'
AUDIT_SCHEMA = 'CNN_PEP_V4F4B_G6_SHADOW_AUDIT_V1'
COUNT = 400
BATCH_SIZE = 25
BATCHES = 16
SCHEDULE_FIELDS = tuple(FIELDS) + ('batch_number','batch_position')
PENDING = 'SHADOW_SCHEDULE_PREPARED_NOT_EXECUTED'
ACTIVE = 'SHADOW_SCHEDULER_ACTIVE_NO_XSIM'
PAUSED = 'SHADOW_SCHEDULER_PAUSED_NO_XSIM'
DONE = 'SHADOW_400_CHECKPOINT_EVENTS_VERIFIED_NO_XSIM'
GATE = 'G6_SHADOW_ONLY_FULL400_BLOCKED'
EVENT_KIND = 'SYNTHETIC_CHECKPOINT_NOT_FI_NOT_XSIM'
LOCK_NAME = '.v4f4b.lock'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def no_duplicate_pairs(pairs: list[tuple[str,Any]]) -> dict[str,Any]:
    output: dict[str,Any] = {}
    for key,value in pairs:
        require(key not in output, 'Duplicate JSON key: '+key)
        output[key]=value
    return output


def strict_json(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), 'JSON missing/symlink: '+str(path))
    obj=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=no_duplicate_pairs)
    require(isinstance(obj,dict), 'Expected JSON object: '+str(path))
    return obj


def atomic_json(path: Path, value: dict[str,Any]) -> None:
    # The existing V4d atomic writer uses fsync and os.replace; never write
    # partially completed checkpoints by appending to JSON or CSV.
    v4da.atomic_write_json(path,value)


def path_arg(repo:Path, value:Path) -> Path:
    return value.resolve() if value.is_absolute() else (repo/value).resolve()


def safe_out(repo:Path, results:Path, out:Path, *, exists:bool=False) -> None:
    require(results==repo/'fi/results' and results.is_dir() and
            not results.is_symlink() and out.parent==results and
            out.name.startswith('v4f4b_') and not out.is_symlink() and
            (out.is_dir() if exists else not out.exists()),
            'V4f4b output must be a new fi/results/v4f4b_* sibling, '
            'or an existing sibling only for explicit resume')
    if not exists:
        v4f.safety_output(repo,out,repo/'data',results)


def schedule(frozen:list[dict[str,str]]) -> list[dict[str,str]]:
    """Fixed 16 x 25 chronological chunks; one, and only one, of each tuple."""
    require(len(frozen)==COUNT, 'G6 schedule needs exactly 400 frozen rows')
    rows=[]
    for i,row in enumerate(frozen):
        require(row['scenario_id']==f'FI_P400_{i+1:04d}',
                'G6 ordering/identity mismatch')
        rows.append({**row,'batch_number':str(i//BATCH_SIZE+1),
                     'batch_position':str(i%BATCH_SIZE+1)})
    require(len(rows)==COUNT and len({r['scenario_id'] for r in rows})==COUNT and
            [r['batch_number'] for r in rows]==
              [str(i//BATCH_SIZE+1) for i in range(COUNT)] and
            [r['batch_position'] for r in rows]==
              [str(i%BATCH_SIZE+1) for i in range(COUNT)],
            'G6 batch coverage or sequence altered')
    return rows


def schedule_fingerprint(rows:list[dict[str,str]]) -> str:
    return digest_bytes(stable_bytes(rows))


def schedule_bundles(rows:list[dict[str,str]]) -> list[dict[str,Any]]:
    require(len(rows)==COUNT, 'Wrong schedule length')
    return [{'batch_number':batch+1,
             'start_scenario_id':rows[batch*BATCH_SIZE]['scenario_id'],
             'end_scenario_id':rows[(batch+1)*BATCH_SIZE-1]['scenario_id'],
             'tuple_count':BATCH_SIZE,
             'batch_sha256':schedule_fingerprint(rows[batch*BATCH_SIZE:(batch+1)*BATCH_SIZE])}
            for batch in range(BATCHES)]


def synthetic_event(row:dict[str,str],idx:int) -> dict[str,Any]:
    return {'event_kind':EVENT_KIND, 'sequence':idx+1,
            'scenario_id':row['scenario_id'],
            'batch_number':int(row['batch_number']),
            'batch_position':int(row['batch_position']),
            'frozen_tuple_sha256':digest_bytes(stable_bytes(
                {field:row[field] for field in FIELDS})),
            'injection_executed':False,'xsim_executed':False,
            'design_outcome_observed':False}


def check_no_release(meta:dict[str,Any]) -> None:
    require(meta.get('full400_authorized') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('full400_executed') is False and
            meta.get('scientific_protocol_frozen') is False and
            meta.get('new_xsim_runs')==0 and
            meta.get('release_gate')==GATE and
            meta.get('execution_scope')=='SYNTHETIC_SCHEDULER_EVENTS_ONLY',
            'G6 shadow evidence contains unauthorized FI/release claim')


def trusted_preflight(repo:Path, results:Path, weights:Path, v4e:Path,
                      v4f1:Path, smoke:Path, smoke_audit:Path,
                      v4f3_dir:Path, g7_prepared:Path) -> tuple[list[dict[str,str]],dict[str,str],dict[str,str]]:
    """Full read-only ancestry, raw FI smoke, and manual-approval-pending G7 pins."""
    require(results==repo/'fi/results' and weights.is_dir(),
            'Expected fixed fi/results and weights directory')
    v4f.check_frozen(repo)
    target_plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                          repo/'fi/config/fi_targets_candidates_v2.csv')
    frozen=v4f.select_400(
        verify_manifest(repo/'fi/scenarios/pilot400.csv',target_plan),target_plan)
    v4f.check_assets_and_provenance(repo,weights)
    v4e_meta,matrix=v4f.verify_v4e_saved(repo,results,v4e)
    v4f.replay_v4e_raw(repo,results,weights,v4e,v4e_meta,matrix,frozen,target_plan)
    v4f3.inspect_plan(repo,results,v4f1,v4e,frozen)
    v4f3.inspect_smoke(results,smoke,smoke_audit,v4e,
                       frozen,target_plan,weights,repo)
    v4f4.check_prepared(repo,v4f3_dir,g7_prepared)
    pins=v4f2._source_pins(repo,results,weights,v4e)
    extra=[
        HERE/'qualify_g6_v4f4b.py', HERE/'test_qualify_g6_v4f4b.py',
        HERE/'audit_v4f2_smoke.py', HERE/'qualify_science_v4f4.py',
        HERE/'review_release_v4f3.py', HERE/'run_full400_batches_v4f.py',
        repo/'fi/v4/docs/V4F4B_G6_README.md',
        repo/'fi/v4/docs/V4F4B_G7_INTERPRETATION_ANNEX_CANDIDATE.md',
        v4f1/'v4f_preflight.json',
        smoke/'v4f2_manifest.json',smoke/'smoke_validation.csv',
        smoke_audit/'v4f2_smoke_audit.json',
        v4f3_dir/'v4f3_release_review.json',
        g7_prepared/'v4f4_prepare.json',g7_prepared/'protocol_core.json',
        g7_prepared/'PROTOCOL_CORE_SHA256.txt',
        g7_prepared/'TEMPLATE_operator.json',
        g7_prepared/'TEMPLATE_independent_reviewer.json',
    ]
    for p in extra:
        require(p.is_file() and not p.is_symlink(), 'G6 pin missing: '+str(p))
        pins[str(p.resolve())]=sha256(p)
    pins=dict(sorted(pins.items()))
    core=strict_json(g7_prepared/'v4f4_prepare.json')
    require(core['G7']=='OPEN_AWAITING_TWO_SEPARATE_MANUAL_ATTESTATIONS' and
            core['full400_authorized'] is False,
            'G6 shadow preflight cannot treat G7 preparation as approval')
    parents={
        'v4e_release_review_sha256':sha256(v4e/'v4e_release_review.json'),
        'v4f1_preflight_sha256':sha256(v4f1/'v4f_preflight.json'),
        'v4f2_smoke_manifest_sha256':sha256(smoke/'v4f2_manifest.json'),
        'v4f2_smoke_audit_sha256':sha256(smoke_audit/'v4f2_smoke_audit.json'),
        'v4f3_review_sha256':sha256(v4f3_dir/'v4f3_release_review.json'),
        'g7_prepared_core_sha256':sha256(g7_prepared/'protocol_core.json'),
    }
    return frozen,pins,parents


@contextmanager
def exclusive_lock(out:Path):
    """Protect one ledger against concurrent writers on supported local FS."""
    path=out/LOCK_NAME
    require(not path.is_symlink(), 'Refuse symlink lock')
    flags=os.O_RDWR|os.O_CREAT
    if hasattr(os,'O_NOFOLLOW'): flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(path,flags,0o600),'r+b') as f:
        if f.seek(0,os.SEEK_END)==0:
            f.write(b'0');f.flush();os.fsync(f.fileno())
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            try: msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as e: raise ValueError('G6 shadow ledger already locked') from e
            try: yield
            finally:
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try: fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as e: raise ValueError('G6 shadow ledger already locked') from e
            try: yield
            finally: fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def make_metadata(out:Path, rows:list[dict[str,str]], pins:dict[str,str],
                  parents:dict[str,str]) -> dict[str,Any]:
    require(not out.exists() and not out.is_symlink(), 'Never overwrite G6 attempt')
    out.mkdir(parents=False,exist_ok=False)
    write_csv(out/'schedule_400.csv',SCHEDULE_FIELDS,rows)
    schedule_sha=sha256(out/'schedule_400.csv')
    obj={'schema':SCHEMA,'created_utc':utc_now(),'status':PENDING,
         'planned_count':COUNT,'batch_size':BATCH_SIZE,'batch_count':BATCHES,
         'schedule_sha256':schedule_sha,
         'schedule_semantic_sha256':schedule_fingerprint(rows),
         'batch_contract':schedule_bundles(rows),
         'frozen_manifest_sha256':v4f.FROZEN_HASHES['fi/scenarios/pilot400.csv'],
         'input_pins':pins,'ancestry_receipts':parents,
         'records':[],'synthetic_events_committed':0,
         'execution_scope':'SYNTHETIC_SCHEDULER_EVENTS_ONLY',
         'new_xsim_runs':0,'full400_runner_qualified':False,
         'full400_authorized':False,'full400_executed':False,
         'scientific_protocol_frozen':False,'target_status_modified':False,
         'release_gate':GATE,
         'mid_xsim_recovery_proved':False,'power_loss_recovery_proved':False,
         'orphan_artifact_auto_recovered':False}
    atomic_json(out/'v4f4b_ledger.json',obj)
    return obj


def verify_ledger(out:Path,rows:list[dict[str,str]],meta:dict[str,Any],
                  pins:dict[str,str],parents:dict[str,str],
                  *,complete:bool=False) -> None:
    require(out.is_dir() and not out.is_symlink() and meta.get('schema')==SCHEMA,
            'G6 shadow ledger dir/schema mismatch')
    check_no_release(meta)
    require(meta.get('planned_count')==COUNT and meta.get('batch_size')==BATCH_SIZE and
            meta.get('batch_count')==BATCHES and
            meta.get('input_pins')==pins and meta.get('ancestry_receipts')==parents and
            meta.get('frozen_manifest_sha256')==v4f.FROZEN_HASHES['fi/scenarios/pilot400.csv'] and
            meta.get('schedule_semantic_sha256')==schedule_fingerprint(rows) and
            meta.get('batch_contract')==schedule_bundles(rows) and
            meta.get('schedule_sha256')==sha256(out/'schedule_400.csv') and
            read_csv(out/'schedule_400.csv',SCHEDULE_FIELDS)==rows,
            'G6 schedule/source/batch integrity mismatch')
    require(meta.get('target_status_modified') is False and
            meta.get('mid_xsim_recovery_proved') is False and
            meta.get('power_loss_recovery_proved') is False and
            meta.get('orphan_artifact_auto_recovered') is False,
            'G6 shadow evidence cannot claim physical recovery or target promotion')
    require(meta.get('status') in (PENDING,ACTIVE,PAUSED,DONE),
            'G6 ledger unrecognized state')
    require(not (out/'v4f4b_ledger.json.tmp').exists() and
            not (out/'inflight.json').exists(),
            'G6 partial transaction/inflight marker: fail closed; new attempt required')
    records=meta.get('records')
    require(type(records) is list and len(records)<=COUNT and
            meta.get('synthetic_events_committed')==len(records),
            'G6 committed record count invalid')
    allowed={'schedule_400.csv','v4f4b_ledger.json',LOCK_NAME}
    events=out/'probe_events'
    if events.exists(): allowed.add('probe_events')
    require({p.name for p in out.iterdir()}<=allowed,
            'G6 unexpected root artifact (no auto-recovery)')
    require(not events.is_symlink(), 'G6 symlinked event folder')
    present={p.name for p in events.iterdir()} if events.exists() else set()
    expected={rows[i]['scenario_id']+'.json' for i in range(len(records))}
    require(present==expected,
            'G6 orphan/missing/extra probe events: fail closed')
    for i,record in enumerate(records):
        row=rows[i];event_path=events/(row['scenario_id']+'.json')
        require(type(record) is dict and
                set(record)=={'scenario_id','batch_number','event_sha256'} and
                record['scenario_id']==row['scenario_id'] and
                record['batch_number']==int(row['batch_number']) and
                event_path.is_file() and not event_path.is_symlink() and
                sha256(event_path)==record['event_sha256'] and
                event_path.read_bytes()==stable_bytes(synthetic_event(row,i)),
                'G6 synthetic checkpoint event corrupted: '+row['scenario_id'])
    require(not (out/'v4f4b_ledger.json.tmp').exists(),
            'G6 unfinished atomic checkpoint temp')
    require(bool(pins) and all(Path(p).is_file() and sha256(Path(p))==h
                               for p,h in pins.items()),
            'G6 source/pinned evidence hash drift')
    if complete:
        require(meta['status']==DONE and len(records)==COUNT,
                'G6 synthetic ledger incomplete: cannot audit as completed')
    else:
        require(meta['status']!=DONE and len(records)<COUNT,
                'G6 completed ledger cannot be resumed/continued')


def probe(out:Path, rows:list[dict[str,str]], meta:dict[str,Any],
          pins:dict[str,str],parents:dict[str,str], pause_after:int=0,
          hook:Callable[[str,int],None]|None=None) -> dict[str,Any]:
    """Qualify prefix commits only. Hook is used by negative unit tests."""
    require(0<=pause_after<COUNT, 'pause-after must be 0..399')
    verify_ledger(out,rows,meta,pins,parents)
    require(pause_after==0 or pause_after>len(meta['records']),
            'pause-after must exceed the committed resume prefix')
    meta['status']=ACTIVE
    atomic_json(out/'v4f4b_ledger.json',meta)
    events=out/'probe_events'
    events.mkdir(exist_ok=True)
    for idx in range(len(meta['records']),COUNT):
        require(not (out/'inflight.json').exists(), 'G6 stale inflight marker')
        require(all(Path(p).is_file() and sha256(Path(p))==h
                    for p,h in pins.items()),
                'G6 changed source/evidence before committing next probe event')
        row=rows[idx]
        inflight=out/'inflight.json'
        with inflight.open('xb') as f:
            f.write(stable_bytes({'event_kind':EVENT_KIND,'sequence':idx+1,
                                  'scenario_id':row['scenario_id']}))
            f.flush();os.fsync(f.fileno())
        if hook:hook('inflight',idx+1)
        event=events/(row['scenario_id']+'.json')
        with event.open('xb') as f:
            f.write(stable_bytes(synthetic_event(row,idx)))
            f.flush();os.fsync(f.fileno())
        if hook:hook('event',idx+1)
        meta['records'].append({'scenario_id':row['scenario_id'],
                                'batch_number':int(row['batch_number']),
                                'event_sha256':sha256(event)})
        meta['synthetic_events_committed']=len(meta['records'])
        atomic_json(out/'v4f4b_ledger.json',meta)
        if hook:hook('checkpoint',idx+1)
        inflight.unlink()
        if pause_after and len(meta['records'])==pause_after:
            meta['status']=PAUSED
            atomic_json(out/'v4f4b_ledger.json',meta)
            print(f'V4F4B_SHADOW_PAUSED: {pause_after}/400 synthetic commits; '
                  '0 XSim; FULL400 BLOCKED',flush=True)
            return meta
    meta['status']=DONE
    atomic_json(out/'v4f4b_ledger.json',meta)
    verify_ledger(out,rows,meta,pins,parents,complete=True)
    print('V4F4B_SHADOW_PASS: 400/400 synthetic scheduler checkpoints; '
          '16 batches x 25; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED',flush=True)
    return meta


def review_probe(out:Path, rows:list[dict[str,str]], meta:dict[str,Any],
                 pins:dict[str,str],parents:dict[str,str], audit_dir:Path) -> dict[str,Any]:
    verify_ledger(out,rows,meta,pins,parents,complete=True)
    require(not audit_dir.exists() and not audit_dir.is_symlink(),
            'G6 audit refuses an existing output directory')
    audit_dir.mkdir(parents=False,exist_ok=False)
    receipt={'schema':AUDIT_SCHEMA,'status':'SHADOW_LEDGER_AUDIT_PASS_NO_XSIM',
             'created_utc':utc_now(),
             'shadow_ledger_sha256':sha256(out/'v4f4b_ledger.json'),
             'shadow_schedule_sha256':sha256(out/'schedule_400.csv'),
             'scenario_count':COUNT,'batch_count':BATCHES,'batch_size':BATCH_SIZE,
             'synthetic_events_verified':COUNT,'real_fi_scenarios_executed':0,
             'new_xsim_runs':0,'G6':'SHADOW_QUALIFICATION_ONLY_NOT_RELEASED',
             'G7':'PREPARED_NOT_APPROVED',
             'full400_runner_qualified':False,'full400_authorized':False,
             'full400_executed':False,'release_gate':GATE}
    atomic_json(audit_dir/'v4f4b_audit.json',receipt)
    print('V4F4B_SHADOW_AUDIT_PASS: 400 synthetic checkpoints replayed; '
          '0 XSim; FULL400 BLOCKED',flush=True)
    print('V4F4B_AUDIT_OUTPUT: '+str(audit_dir),flush=True)
    return receipt


def dispatch(args:argparse.Namespace) -> dict[str,Any]:
    require(not args.execute and not args.approve and args.mode!='full400',
            'V4f4b forbids --execute, --approve and Full400 execution')
    require(args.mode in ('plan','probe','audit'), 'V4f4b unknown mode')
    require(not args.resume or args.mode=='probe',
            'V4f4b --resume permitted only for synthetic probe')
    require(args.pause_after==0 or (args.mode=='probe' and
                                     1<=args.pause_after<COUNT),
            'V4f4b pause-after is for synthetic probe only')
    repo=args.repo.resolve()
    results=path_arg(repo,args.results_dir)
    weights=path_arg(repo,args.weights_dir)
    v4e=path_arg(repo,args.v4e_dir)
    v4f1=path_arg(repo,args.v4f1_dir)
    smoke=path_arg(repo,args.smoke_dir)
    smoke_audit=path_arg(repo,args.smoke_audit_dir)
    v4f3_dir=path_arg(repo,args.v4f3_dir)
    g7_prepared=path_arg(repo,args.g7_prepared_dir)
    out=path_arg(repo,args.out_dir)
    safe_out(repo,results,out,exists=args.resume)
    if args.mode=='audit':
        require(args.probe_dir is not None,
                'G6 audit requires --probe-dir containing completed shadow evidence')
    else:
        require(args.probe_dir is None,'--probe-dir is only allowed for G6 audit')
    frozen,pins,parents=trusted_preflight(repo,results,weights,v4e,v4f1,
                                          smoke,smoke_audit,v4f3_dir,g7_prepared)
    rows=schedule(frozen)
    if args.mode=='audit':
        original=path_arg(repo,args.probe_dir)
        require(original.parent==results and original!=out and
                original.name.startswith('v4f4b_') and not original.is_symlink(),
                'G6 audit input outside fi/results or equals audit output')
        meta=strict_json(original/'v4f4b_ledger.json')
        return review_probe(original,rows,meta,pins,parents,out)
    if args.mode=='plan':
        meta=make_metadata(out,rows,pins,parents)
        verify_ledger(out,rows,meta,pins,parents)
        print('V4F4B_PLAN_PASS: 400/400 frozen tuples, 16 x 25 batches; '
              'G7 PREPARED; 0 XSim; FULL400 BLOCKED',flush=True)
        print('V4F4B_OUTPUT: '+str(out),flush=True)
        return meta
    if not args.resume:
        meta=make_metadata(out,rows,pins,parents)
    with exclusive_lock(out):
        if args.resume:
            meta=strict_json(out/'v4f4b_ledger.json')
        result=probe(out,rows,meta,pins,parents,args.pause_after)
        print('V4F4B_OUTPUT: '+str(out),flush=True)
        return result


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--v4e-dir',type=Path,default=Path('fi/results/v4e_release_review_01'))
    ap.add_argument('--v4f1-dir',type=Path,default=Path('fi/results/v4f_ancestry_01'))
    ap.add_argument('--smoke-dir',type=Path,default=Path('fi/results/v4f2_smoke_01'))
    ap.add_argument('--smoke-audit-dir',type=Path,default=Path('fi/results/v4f2_smoke_audit_01'))
    ap.add_argument('--v4f3-dir',type=Path,default=Path('fi/results/v4f3_release_review_01'))
    ap.add_argument('--g7-prepared-dir',type=Path,default=Path('fi/results/v4f4_protocol_prepare_01'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f4b_probe_01'))
    ap.add_argument('--mode',choices=('plan','probe','audit','full400'),default='plan')
    ap.add_argument('--probe-dir',type=Path)
    ap.add_argument('--pause-after',type=int,default=0)
    ap.add_argument('--resume',action='store_true')
    ap.add_argument('--execute',action='store_true',help='Forbidden: Phase 4B NEVER invokes XSim')
    ap.add_argument('--approve',action='store_true',help='Forbidden: G0-G7 release is separate')
    args=ap.parse_args()
    try:
        dispatch(args)
    except (ValueError,RuntimeError,KeyError,OSError,TypeError,TimeoutError) as exc:
        raise SystemExit('V4F4B_FAIL: '+str(exc)) from exc

if __name__=='__main__':
    main()

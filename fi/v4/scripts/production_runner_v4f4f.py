#!/usr/bin/env python3
"""V4f Phase 4F: production-run contract + OFFLINE rehearsal, never XSim.

This is NOT a production XSim executor.  The CLI explicitly rejects execute,
full400 and --execute prior to any filesystem work.  G6 production remains
NOT QUALIFIED and G7 remains NOT APPROVED.  The eight historical failure
qualification attempts are EXHAUSTED, with no new FI budget created here.

Modes:
  plan             frozen 400 schedule, SHA256 source/asset pins, no XSim
  rehearsal        atomic synthetic event transactions; pause / strict resume
  source-preflight read-only Vivado-authoritative .prj / toolchain verification
  execute/full400  forbidden unconditionally; no alternate execution path
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parents[1]/'scripts', HERE.parents[1]/'v3b/scripts'):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from audit_v4 import read_json, require, V3A_NAME  # noqa: E402
from pilot400_common import FIELDS, load_plan, verify_manifest, read_csv, write_csv, sha256  # noqa: E402
from pilot400_direct_xsim import project_source_hashes  # noqa: E402
import qualify_full400_v4f as v4f  # noqa: E402
import qualify_g6_v4f4b as shadow  # noqa: E402
import qualify_runner_v4d as v4da  # noqa: E402

SCHEMA = 'CNN_PEP_V4F4F_PRODUCTION_CONTRACT_OFFLINE_V1'
SOURCE_SCHEMA = 'CNN_PEP_V4F4F_VIVADO_READONLY_SOURCE_PREFLIGHT_V1'
COUNT, BATCH, BATCHES = 400, 25, 16
SCHEDULE_FIELDS = tuple(FIELDS) + ('batch_number', 'batch_position')
GATE = 'BLOCKED_G0_G7_INDEPENDENT_RELEASE_REQUIRED'
STATUS_PLAN = 'PRODUCTION_SCHEDULE_PREPARED_NO_EXECUTION'
STATUS_RUN = 'PRODUCTION_REHEARSAL_ACTIVE_NO_XSIM'
STATUS_PAUSE = 'PRODUCTION_REHEARSAL_PAUSED_NO_XSIM'
STATUS_DONE = 'PRODUCTION_REHEARSAL_400_SYNTHETIC_EVENTS_ONLY'
EVENT_KIND = 'OFFLINE_TRANSACTION_REHEARSAL_NOT_FI_NOT_XSIM'
LOCK_FILE = '.v4f4f.lock'
DATA_FOLDER = 'rehearsal_events'
LEDGER_NAME = 'v4f4f_production_contract.json'
SPEC = 'fi/v4/docs/V4F4F_EXECUTION_SAFETY_SPEC.md'
CORE_SOURCES = (
    'fi/scripts/pilot400_common.py',
    'fi/scripts/pilot400_direct_xsim.py',
    'fi/scripts/parse_fi_v2.py',
    'fi/v4/scripts/qualify_full400_v4f.py',
    'fi/v4/scripts/qualify_g6_v4f4b.py',
    'fi/v4/scripts/qualify_xsim_failures_v4f4c.py',
    'fi/v4/scripts/qualify_postfi_lf_v4f4e.py',
    'fi/v4/scripts/production_runner_v4f4f.py',
    'fi/v4/scripts/audit_production_v4f4f.py',
    SPEC,
)
PLUSARG_KEYS = ('FI_OFFSET','SCENARIO_ID','TARGET_ID','FI_BIT','SAMPLE_ID',
                'INPUT_FILE','POOL1_FILE','POOL2_FILE','LOGITS_FILE','PREDICTION_FILE')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(obj: Any) -> bytes:
    return (json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False,
                       allow_nan=False)+'\n').encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in output, 'Duplicate JSON key: '+key)
        output[key] = value
    return output


def strict_json(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), 'Missing/symlinked JSON '+str(path))
    result=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=reject_duplicate_keys,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('JSON NaN/Infinity forbidden')))
    require(type(result) is dict, 'Expected JSON object at '+str(path))
    return result


def atomic_json(path: Path,obj: dict[str,Any]) -> None:
    v4da.atomic_write_json(path,obj)


def path_from(repo:Path,p:Path)->Path:
    # Check the literal symlink before resolving; .resolve() alone hides it.
    raw=p if p.is_absolute() else repo/p
    require(not raw.is_symlink(), 'Symlink path forbidden: '+str(raw))
    return raw.resolve()


def safe_destination(repo:Path,results:Path,out:Path,*,resume:bool=False)->None:
    require(results==repo/'fi/results' and results.is_dir() and
            not results.is_symlink() and out.parent==results and
            re.fullmatch(r'v4f4f_[A-Za-z0-9_]+',out.name) is not None and
            not out.is_symlink() and (out.is_dir() if resume else not out.exists()),
            'V4f4F requires a fresh fi/results/v4f4f_* sibling; resume must use existing')
    if not resume:
        v4f.safety_output(repo,out,repo/'data',results)


def frozen_schedule(repo:Path)->list[dict[str,str]]:
    v4f.check_frozen(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    rows=v4f.select_400(verify_manifest(repo/'fi/scenarios/pilot400.csv',plan),plan)
    result=shadow.schedule(rows)
    require(len(result)==COUNT and len(shadow.schedule_bundles(result))==BATCHES,
            'V4f4F requires 16 exact batches of 25 frozen tuples')
    return result


def pinned_inputs(repo:Path,weights:Path)->dict[str,str]:
    require(weights.is_dir() and not weights.is_symlink(), 'Missing/symlinked weights directory')
    pins=v4f.check_assets_and_provenance(repo,weights)
    for name in (*v4f.FROZEN_HASHES.keys(), *CORE_SOURCES):
        p=repo/name
        require(p.is_file() and not p.is_symlink(), 'Missing/symlinked input: '+name)
        pins[str(p.resolve())]=sha256(p)
    return dict(sorted(pins.items()))


def check_pins(pins:dict[str,str])->None:
    require(type(pins) is dict and len(pins)>=55,
            'V4f4F source/pilot asset pin inventory incomplete')
    require(all(isinstance(k,str) and isinstance(v,str) and
                re.fullmatch(r'[a-f0-9]{64}',v) and
                Path(k).is_file() and not Path(k).is_symlink() and
                sha256(Path(k))==v for k,v in pins.items()),
            'V4f4F source, weights or sample bytes changed')


def expected_plusargs(row:dict[str,str],repo:Path)->dict[str,str]:
    folder=repo/'fi/pilot400/samples'/row['sample_id']
    args={
        'FI_OFFSET':row['injection_offset'],'SCENARIO_ID':row['scenario_id'],
        'TARGET_ID':row['target_id'],'FI_BIT':row['bit_index'],
        'SAMPLE_ID':row['sample_id'],
        'INPUT_FILE':(folder/'input.hex').resolve().as_posix(),
        'POOL1_FILE':(folder/'golden_pool1.hex').resolve().as_posix(),
        'POOL2_FILE':(folder/'golden_pool2.hex').resolve().as_posix(),
        'LOGITS_FILE':(folder/'golden.hex').resolve().as_posix(),
        'PREDICTION_FILE':(folder/'prediction.hex').resolve().as_posix(),
    }
    require(tuple(args)==PLUSARG_KEYS and len(args)==10,
            'Exactly ten profile-disabled XSim plusargs are required')
    return args


def assert_plusargs(actual:dict[str,str],row:dict[str,str],repo:Path)->None:
    require(type(actual) is dict and actual==expected_plusargs(row,repo),
            'Production XSim plusargs changed: '+row['scenario_id'])


def schedule_digest(rows:list[dict[str,str]])->str:
    return digest(encoded(rows))


def source_fingerprint(pins:dict[str,str])->str:
    return digest(encoded(pins))


def make_metadata(out:Path,rows:list[dict[str,str]],pins:dict[str,str],repo:Path)->dict[str,Any]:
    require(not out.exists(), 'Never overwrite previous Phase 4F evidence')
    out.mkdir(parents=False,exist_ok=False)
    write_csv(out/'schedule_400.csv',SCHEDULE_FIELDS,rows)
    metadata={
        'schema':SCHEMA,'created_utc':now(),'status':STATUS_PLAN,
        'planned_count':COUNT,'batch_size':BATCH,'batch_count':BATCHES,
        'schedule_sha256':sha256(out/'schedule_400.csv'),
        'schedule_semantic_sha256':schedule_digest(rows),
        'batch_contract':shadow.schedule_bundles(rows),
        'input_pins':pins,'input_fingerprint':source_fingerprint(pins),
        'frozen_manifest_sha256':v4f.FROZEN_HASHES['fi/scenarios/pilot400.csv'],
        'exact_xsim_plusarg_names':list(PLUSARG_KEYS),'pilot_profile_only_plusarg_forbidden':True,
        'source_authority':'VIVADO_XPR_PRJ_REQUIRED_NOT_YET_VERIFIED',
        'build_once_executed':False,'windows_job_object_executed':False,
        'records':[],'synthetic_events_committed':0,
        'xsim_runs_committed':0,'real_fi_scenarios_executed':0,
        'infrastructure_failures_are_design_noncompletion':False,
        'event_kind':EVENT_KIND,'execution_scope':'OFFLINE_REHEARSAL_ONLY_NO_XSIM',
        'G6_production':'NOT_QUALIFIED','G7':'PREPARED_NOT_APPROVED',
        'G0_G7_signed_authorization':False,'full400_runner_qualified':False,
        'full400_authorized':False,'full400_executed':False,
        'attempt_budget_8_of_8_exhausted':True,'release_gate':GATE,
        'power_loss_recovery_proved':False,'mid_xsim_recovery_proved':False,
        'target_status_modified':False,
    }
    atomic_json(out/LEDGER_NAME,metadata)
    return metadata


def event_for(row:dict[str,str],index:int,previous_hash:str)->dict[str,Any]:
    return {
        'event_kind':EVENT_KIND,'sequence':index+1,
        'scenario_id':row['scenario_id'],
        'batch_number':int(row['batch_number']),
        'batch_position':int(row['batch_position']),
        'frozen_tuple_sha256':digest(encoded({k:row[k] for k in FIELDS})),
        'previous_event_sha256':previous_hash,
        'simulation_executed':False,'fault_injected':False,
        'raw_xsim_log_exists':False,'design_outcome_available':False,
        'production_execution_authorized':False,
    }


def verify_ledger(out:Path,rows:list[dict[str,str]],pins:dict[str,str],
                  meta:dict[str,Any],*,complete:bool=False)->None:
    require(out.is_dir() and not out.is_symlink() and
            meta.get('schema')==SCHEMA and meta.get('status') in
            (STATUS_PLAN,STATUS_RUN,STATUS_PAUSE,STATUS_DONE),
            'Invalid Phase 4F metadata/schema/status')
    require(meta.get('planned_count')==COUNT and meta.get('batch_count')==BATCHES and
            meta.get('batch_size')==BATCH and meta.get('batch_contract')==shadow.schedule_bundles(rows) and
            meta.get('schedule_semantic_sha256')==schedule_digest(rows) and
            meta.get('frozen_manifest_sha256')==v4f.FROZEN_HASHES['fi/scenarios/pilot400.csv'] and
            meta.get('input_pins')==pins and meta.get('input_fingerprint')==source_fingerprint(pins) and
            meta.get('exact_xsim_plusarg_names')==list(PLUSARG_KEYS) and
            meta.get('pilot_profile_only_plusarg_forbidden') is True,
            'Phase 4F schedule or source signature changed')
    require(meta.get('source_authority')=='VIVADO_XPR_PRJ_REQUIRED_NOT_YET_VERIFIED' and
            meta.get('build_once_executed') is False and
            meta.get('windows_job_object_executed') is False and
            meta.get('execution_scope')=='OFFLINE_REHEARSAL_ONLY_NO_XSIM' and
            meta.get('event_kind')==EVENT_KIND and
            meta.get('attempt_budget_8_of_8_exhausted') is True and
            meta.get('xsim_runs_committed')==0 and
            meta.get('real_fi_scenarios_executed')==0 and
            meta.get('G6_production')=='NOT_QUALIFIED' and
            meta.get('G7')=='PREPARED_NOT_APPROVED' and
            meta.get('G0_G7_signed_authorization') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('full400_authorized') is False and
            meta.get('full400_executed') is False and
            meta.get('infrastructure_failures_are_design_noncompletion') is False and
            meta.get('power_loss_recovery_proved') is False and
            meta.get('mid_xsim_recovery_proved') is False and
            meta.get('target_status_modified') is False and
            meta.get('release_gate')==GATE,
            'Phase 4F contains unauthorized release, FI or scientific claim')
    require(not (out/LOCK_FILE).is_symlink(),
            'Symlinked ledger lock forbidden in read-only replay')
    schedule=out/'schedule_400.csv'
    require(schedule.is_file() and not schedule.is_symlink() and
            meta.get('schedule_sha256')==sha256(schedule) and
            read_csv(schedule,SCHEDULE_FIELDS)==rows,
            'Frozen schedule CSV changed')
    check_pins(pins)
    require(not (out/'inflight.json').exists() and not (out/(LEDGER_NAME+'.tmp')).exists(),
            'Uncommitted/orphan transaction; refuse resume/audit')
    records=meta.get('records')
    require(type(records) is list and 0<=len(records)<=COUNT and
            meta.get('synthetic_events_committed')==len(records),
            'Phase 4F committed rehearsal prefix invalid')
    expected_roots={'schedule_400.csv',LEDGER_NAME,LOCK_FILE}
    if records:
        expected_roots.add(DATA_FOLDER)
    require({p.name for p in out.iterdir()} <= expected_roots,
            'Unexpected root file or orphan raw log in production rehearsal')
    events=out/DATA_FOLDER
    expected_events={row['scenario_id']+'.json' for row in rows[:len(records)]}
    if events.exists():
        require(events.is_dir() and not events.is_symlink() and
                {p.name for p in events.iterdir()}==expected_events,
                'Orphan/missing rehearsal receipt; no auto-recovery')
    else:
        require(not records,'Missing rehearsal event directory')
    last='0'*64
    for i,record in enumerate(records):
        row=rows[i]
        require(type(record) is dict and
                set(record)=={'scenario_id','batch_number','event_sha256'} and
                record['scenario_id']==row['scenario_id'] and
                record['batch_number']==int(row['batch_number']),
                'Non-contiguous or duplicate FI ID in checkpoint ledger')
        event=events/(row['scenario_id']+'.json')
        require(event.is_file() and not event.is_symlink() and
                sha256(event)==record['event_sha256'] and
                event.read_bytes()==encoded(event_for(row,i,last)),
                'Event SHA256/chain/content mismatch: '+row['scenario_id'])
        last=record['event_sha256']
    if complete:
        require(meta['status']==STATUS_DONE and len(records)==COUNT,
                'Production rehearsal incomplete')
    else:
        require(meta['status']!=STATUS_DONE and len(records)<COUNT,
                'Completed rehearsal cannot resume')


@contextmanager
def exclusive_lock(out:Path):
    """OS-native lock; do not assume network-share lock semantics are universal."""
    lock=out/LOCK_FILE
    require(not lock.is_symlink(),'Refusing symlink lock')
    flags=os.O_CREAT|os.O_RDWR
    if hasattr(os,'O_NOFOLLOW'):flags|=os.O_NOFOLLOW
    with os.fdopen(os.open(lock,flags,0o600),'r+b') as f:
        if f.seek(0,os.SEEK_END)==0:
            f.write(b'0');f.flush();os.fsync(f.fileno())
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise ValueError('Phase 4F concurrent writer blocked') from exc
            try:yield
            finally:
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as exc:raise ValueError('Phase 4F concurrent writer blocked') from exc
            try:yield
            finally:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def rehearsal(out:Path,rows:list[dict[str,str]],pins:dict[str,str],
              meta:dict[str,Any],*,pause_after:int=0,
              hook:Callable[[str,int],None]|None=None)->dict[str,Any]:
    """Exercise transaction state machine; these records are NEVER FI outcomes.

    The fail hooks model crash boundaries, not real OS/process failures.
    An inflight marker or orphan receipt intentionally blocks resume forever.
    """
    require(0<=pause_after<COUNT,'pause-after must be 0..399')
    verify_ledger(out,rows,pins,meta)
    require(not pause_after or pause_after>len(meta['records']),
            'pause-after must exceed the prior committed prefix')
    meta['status']=STATUS_RUN
    atomic_json(out/LEDGER_NAME,meta)
    events=out/DATA_FOLDER
    events.mkdir(exist_ok=True)
    last=meta['records'][-1]['event_sha256'] if meta['records'] else '0'*64
    for i in range(len(meta['records']),COUNT):
        check_pins(pins)
        row=rows[i]
        event_path=events/(row['scenario_id']+'.json')
        inflight=out/'inflight.json'
        with inflight.open('xb') as f:
            f.write(encoded({'kind':EVENT_KIND,'sequence':i+1,'scenario_id':row['scenario_id']}))
            f.flush();os.fsync(f.fileno())
        if hook:hook('inflight',i+1)
        with event_path.open('xb') as f:
            f.write(encoded(event_for(row,i,last)))
            f.flush();os.fsync(f.fileno())
        if hook:hook('receipt',i+1)
        record={'scenario_id':row['scenario_id'],'batch_number':int(row['batch_number']),
                'event_sha256':sha256(event_path)}
        meta['records'].append(record)
        meta['synthetic_events_committed']=len(meta['records'])
        atomic_json(out/LEDGER_NAME,meta)
        if hook:hook('checkpoint',i+1)
        inflight.unlink()
        last=record['event_sha256']
        if pause_after and len(meta['records'])==pause_after:
            meta['status']=STATUS_PAUSE
            atomic_json(out/LEDGER_NAME,meta)
            print(f'V4F4F_REHEARSAL_PAUSED: {pause_after}/400 synthetic transactions; '
                  '0 XSim; FULL400 BLOCKED',flush=True)
            return meta
    meta['status']=STATUS_DONE
    atomic_json(out/LEDGER_NAME,meta)
    verify_ledger(out,rows,pins,meta,complete=True)
    print('V4F4F_REHEARSAL_PASS: 400/400 synthetic production transactions; '
          '16 x 25; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED',flush=True)
    return meta


def check_source_prj(repo:Path,results:Path,project:Path,vivado:Path)->dict[str,Any]:
    """No subprocess or XSim call: Vivado source authority verified byte-for-byte."""
    require(project.is_file() and project.suffix.lower()=='.xpr' and not project.is_symlink(),
            'Expected Vivado .xpr project')
    require(vivado.is_file() and not vivado.is_symlink() and
            vivado.name.lower() in ('vivado.bat','vivado.exe'),
            'Expected Vivado 2022.2 executable')
    root=vivado.parent.parent
    programs=('xvlog.bat','xelab.bat','xsim.bat')
    binary={}
    for name in (vivado.name,*programs):
        p=vivado if name==vivado.name else root/'bin'/name
        require(p.is_file() and not p.is_symlink(), 'Missing pinned Vivado/XSim executable: '+name)
        binary[str(p.resolve())]=sha256(p)
    v3a=results/V3A_NAME
    manifest=strict_json(v3a/'validation_manifest.json')
    original=manifest.get('snapshot_build',{})
    prj=v3a/'source_vlog.prj'
    require(prj.is_file() and not prj.is_symlink() and
            sha256(prj)==original.get('compile_prj_sha256'),
            'V3a compile source .prj SHA256 missing or mismatched')
    hashes,n=project_source_hashes(prj,repo,rtl_source='vivado',project=project)
    require(hashes==original.get('source_hashes') and n>=20,
            'Vivado-authoritative HDL bytes differ from V3a pinned .prj')
    return {'schema':SOURCE_SCHEMA,'verified_utc':now(),
            'source_authority':'VIVADO_XPR_PINNED_V3A_PRJ',
            'project_sha256':sha256(project),'source_prj_sha256':sha256(prj),
            'source_hashes':hashes,'rtl_count':n,'toolchain_sha256':binary,
            'source_preflight':'READONLY_VERIFIED_NO_COMPILE_ELAB_XSIM',
            'built_snapshot':False,'new_xsim_runs':0,
            'G6_production':'NOT_QUALIFIED','G7':'PREPARED_NOT_APPROVED',
            'full400_authorized':False,'release_gate':GATE}


def dispatch(args:argparse.Namespace)->dict[str,Any]:
    # CRITICAL: deny before path lookup, mkdir, snapshot or shell invocation.
    require(args.mode not in ('execute','full400') and not args.execute and not args.approve,
            'V4F4F EXECUTION DENIED BEFORE IO: production Full400 requires a '
            'separately implemented and signed G0-G7-authorized runner')
    repo=args.repo.resolve()
    results=path_from(repo,args.results_dir)
    weights=path_from(repo,args.weights_dir)
    out=path_from(repo,args.out_dir)
    safe_destination(repo,results,out,resume=args.resume)
    require(not args.resume or args.mode=='rehearsal','--resume only for synthetic rehearsal')
    require(not args.pause_after or (args.mode=='rehearsal' and 1<=args.pause_after<COUNT),
            '--pause-after is only for synthetic rehearsal')
    require((args.mode=='source-preflight') == (args.project is not None and args.vivado is not None),
            '--project/--vivado both required only in source-preflight')
    if args.mode=='source-preflight':
        frozen_schedule(repo)
        pinned_inputs(repo,weights)
        receipt=check_source_prj(repo,results,path_from(repo,args.project),path_from(repo,args.vivado))
        out.mkdir(parents=False,exist_ok=False)
        atomic_json(out/'v4f4f_source_preflight.json',receipt)
        print('V4F4F_SOURCE_PREFLIGHT_PASS: Vivado authoritative source + '
              'toolchain pinned; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED',flush=True)
        print('V4F4F_OUTPUT: '+str(out),flush=True)
        return receipt
    rows=frozen_schedule(repo)
    pins=pinned_inputs(repo,weights)
    check_pins(pins)
    if args.mode=='plan':
        meta=make_metadata(out,rows,pins,repo)
        verify_ledger(out,rows,pins,meta)
        print('V4F4F_PLAN_PASS: 400/400 frozen tuples; 16 x 25; '
              'source execution NOT authorized; 0 XSim; FULL400 BLOCKED',flush=True)
        print('V4F4F_OUTPUT: '+str(out),flush=True)
        return meta
    if args.mode!='rehearsal':
        raise ValueError('Unknown V4F4F mode')
    if not args.resume:
        make_metadata(out,rows,pins,repo)
    with exclusive_lock(out):
        meta=strict_json(out/LEDGER_NAME)
        result=rehearsal(out,rows,pins,meta,pause_after=args.pause_after)
    print('V4F4F_OUTPUT: '+str(out),flush=True)
    return result


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    p.add_argument('--weights-dir',type=Path,default=Path('data'))
    p.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f4f_plan_01'))
    p.add_argument('--mode',choices=('plan','rehearsal','source-preflight','execute','full400'),default='plan')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--pause-after',type=int,default=0)
    p.add_argument('--project',type=Path)
    p.add_argument('--vivado',type=Path)
    p.add_argument('--execute',action='store_true',help='Forbidden; cannot authorize FI execution')
    p.add_argument('--approve',action='store_true',help='Forbidden; cannot self-sign G0-G7')
    args=p.parse_args()
    try:
        dispatch(args)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,AssertionError) as exc:
        raise SystemExit('V4F4F_FAIL: '+str(exc)) from exc


if __name__=='__main__':main()

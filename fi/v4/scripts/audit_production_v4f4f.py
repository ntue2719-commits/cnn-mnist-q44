#!/usr/bin/env python3
"""V4f Phase 4F independent READ-ONLY source/evidence auditor (0 XSim).

Rehearsal audit: reconstruct 400 synthetic receipts and atomic hash chain.
History audit: verify both exact archived 8-attempt canary ZIPs, all 590
raw artifact digests and original two NOT_REPRODUCED classifications.
Release review: record independently checked evidence *and remaining gaps*;
NEVER sign, qualify, authorize, or run Full400.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile
from typing import Any

HERE=Path(__file__).resolve().parent
for folder in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))

from audit_v4 import require  # noqa: E402
from pilot400_common import sha256  # noqa: E402
import production_runner_v4f4f as runner  # noqa: E402

SCHEMA_REHEARSAL='CNN_PEP_V4F4F_INDEPENDENT_REHEARSAL_AUDIT_V1'
SCHEMA_HISTORY='CNN_PEP_V4F4F_EIGHT_ATTEMPT_ZIP_RECHECK_V1'
SCHEMA_RELEASE='CNN_PEP_V4F4F_INDEPENDENT_RELEASE_GAP_V1'
HISTORICAL_HASHES={
    'v4f4c_g6_evidence_01.zip':'15864329a08d87f677a3f5ef92209cb91a2b051af2792a2c30d32373457d9ef3',
    'v4f4de_postfi_evidence_01.zip':'aee6a1ce05c496f05c143eb50c74f8201f9ade80f2abb388974682bb1d384645',
}
# (attempt folder, original audit folder, scenario_id, final classification, committed FI)
HISTORY=(
    ('v4f4c_healthy_01','v4f4c_healthy_audit_01','FI_P400_0007','PASS',1),
    ('v4f4c_terminate_before_01','v4f4c_terminate_before_audit_01','FI_P400_0014','PASS',0),
    ('v4f4c_terminate_after_01','v4f4c_terminate_after_audit_01','FI_P400_0167','NOT_REPRODUCED',0),
    ('v4f4c_crash_before_01','v4f4c_crash_before_audit_01','FI_P400_0174','PASS',0),
    ('v4f4c_timeout_01','v4f4c_timeout_audit_01','FI_P400_0227','PASS',0),
    ('v4f4c_recovery_01','v4f4c_recovery_audit_01','FI_P400_0227','PASS',1),
    ('v4f4c_postfi_det_01','v4f4d_postfi_audit_01','FI_P400_0167','NOT_REPRODUCED',0),
    ('v4f4c_postfi_det_02','v4f4e_postfi_audit_01','FI_P400_0167','PASS',0),
)


def data_hash(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def archive_items(path:Path)->dict[str,bytes]:
    require(path.is_file() and not path.is_symlink(), 'Historical ZIP missing or symlinked')
    require(path.name in HISTORICAL_HASHES and
            sha256(path)==HISTORICAL_HASHES[path.name],
            'Historical ZIP digest/name differs from independent eight-attempt review')
    items:dict[str,bytes]={}
    seen_casefold:set[str]=set()
    with zipfile.ZipFile(path) as z:
        require(z.testzip() is None,'ZIP CRC failure')
        for info in z.infolist():
            raw=info.filename.replace('\\','/')
            if info.is_dir() or raw.endswith('/'):
                continue
            p=PurePosixPath(raw)
            require(not p.is_absolute() and
                    all(segment not in ('.','..','') for segment in p.parts) and
                    len(p.parts)>=2 and p.as_posix()==raw,
                    'Unsafe/unnormalized historical archive entry: '+raw)
            name=p.as_posix()
            require(name.casefold() not in seen_casefold,
                    'Duplicate/case-colliding historical ZIP entry: '+name)
            # Prevent zipped symlinks, even if they are not extracted here.
            require((info.external_attr >> 16) & 0o170000 != 0o120000,
                    'Symlink entry forbidden in historical evidence')
            seen_casefold.add(name.casefold())
            items[name]=z.read(info)
    return items


def read_member(items:dict[str,bytes],name:str)->dict[str,Any]:
    require(name in items,'Missing required historical receipt '+name)
    data=json.loads(items[name],object_pairs_hook=runner.reject_duplicate_keys)
    require(type(data) is dict,'Historical receipt expected JSON object '+name)
    return data


def check_historical_zip_pair(zip_c:Path,zip_de:Path)->dict[str,Any]:
    require(zip_c.name=='v4f4c_g6_evidence_01.zip' and
            zip_de.name=='v4f4de_postfi_evidence_01.zip' and
            zip_c.resolve()!=zip_de.resolve(),
            'Both exact original V4f4C and V4f4DE ZIPs are required')
    groups=(archive_items(zip_c),archive_items(zip_de))
    all_files={}
    for group in groups:
        require(not (set(all_files)&set(group)), 'Duplicate archive paths across ZIPs')
        all_files.update(group)
    records=[];artifact_count=0
    for index,(attempt,audit_folder,sid,expected_class,committed) in enumerate(HISTORY,1):
        old=index<=6
        audit_name='v4f4c_audit.json' if old else (
            'v4f4d_postfi_audit.json' if index==7 else 'v4f4e_postfi_audit.json')
        at=read_member(all_files,attempt+'/attempt.json')
        audit=read_member(all_files,audit_folder+'/'+audit_name)
        require(at.get('scenario_id')==sid and audit.get('scenario_id')==sid and
                at.get('full400_authorized') is False and
                at.get('full400_runner_qualified') is False and
                audit.get('full400_authorized') is False and
                audit.get('full400_runner_qualified') is False and
                at.get('xsim_invocations_committed')==committed,
                'Historical FI status, ID or authorization changed: '+attempt)
        require(audit.get('attempt_json_sha256')==
                data_hash(all_files[attempt+'/attempt.json']) and
                audit.get('new_xsim_runs_during_audit')==0,
                'Historical attempt SHA256/zero-XSim audit mismatch')
        if old:
            hashes=audit.get('raw_artifact_sha256')
            require(audit.get('xsim_invocations_committed')==committed and
                    (audit.get('result')=='ONE_REAL_XSIM_MECHANISM_VERIFIED')==(committed==1),
                    'Original six-attempt audit classification changed')
        else:
            hashes=audit.get('artifact_sha256')
            require(audit.get('committed_fi_outcomes')==0 and
                    at.get('validation') is None,
                    'Post-FI interrupted trial cannot be an FI outcome')
            if index==7:
                require(audit.get('qualification')=='POSTFI_TCL_PAUSE_NOT_REPRODUCED' and
                        at.get('failure_reason')=='POSTFI_TCL_PAUSE_NOT_REPRODUCED',
                        'Slot 7 MUST remain NOT REPRODUCED (CRLF)')
                require(b'\r\n' in all_files[attempt+'/v4f4d_tcl_pause_receipt.txt'],
                        'Slot 7 original CRLF ACK changed')
            else:
                require(audit.get('qualification')=='INTERRUPTED_AFTER_FI_NO_RESULT' and
                        at.get('failure_reason')=='INTERRUPTED_AFTER_FI_NO_RESULT' and
                        at.get('process',{}).get('tcl_handshake_valid') is True and
                        at.get('process',{}).get('postfi_kill_cause')=='ACK_AND_MARKER' and
                        at.get('process',{}).get('marker_evidence')==[1,0],
                        'Slot 8 deterministic post-FI proof changed')
                require(all_files[attempt+'/v4f4d_tcl_pause_receipt.txt']==
                        b'V4F4D_POSTFI_PAUSE_READY\nFI_INJECTION_OK=1\n',
                        'Slot 8 strict LF ACK changed')
        require(type(hashes) is dict and len(hashes)>=65,
                'Incomplete historical raw-artifact inventory')
        expected={name[len(attempt)+1:] for name in all_files
                  if name.startswith(attempt+'/') and name!=attempt+'/attempt.json'}
        if old:
            require(set(hashes)==expected,
                    'Original six-attempt inventory has missing/orphan raw files')
        else:
            require(set(hashes)==expected|{'attempt.json'},
                    'Post-FI archive inventory has missing/orphan raw files')
        for relative,h in hashes.items():
            require(re.fullmatch(r'[a-f0-9]{64}',h) is not None and
                    data_hash(all_files[attempt+'/'+relative])==h,
                    'Historical raw artifact SHA256 mismatch: '+attempt+'/'+relative)
        artifact_count+=len(hashes)
        process=at.get('process') or {}
        require(type(process) is dict,'Historical process receipt malformed')
        marker=process.get('marker_evidence')
        if expected_class=='NOT_REPRODUCED':
            require(at.get('failure_reason') in ('INTERRUPT_PROBE_NOT_REPRODUCED',
                'POSTFI_TCL_PAUSE_NOT_REPRODUCED'),
                'Historical NOT_REPRODUCED trial incorrectly promoted')
        records.append({'slot':index,'scenario_id':sid,'attempt':attempt,
                        'classification':expected_class,'committed_fi_result':committed,
                        'raw_markers':marker,'artifacts_rehashed':len(hashes),
                        'attempt_json_sha256':data_hash(all_files[attempt+'/attempt.json'])})
    require(artifact_count==590 and sum(r['committed_fi_result'] for r in records)==2,
            'Exactly 590 raw SHA hashes and 2 bounded FI committed results expected')
    timeout_hash=next(r['attempt_json_sha256'] for r in records if r['slot']==5)
    recovery=read_member(all_files,'v4f4c_recovery_01/attempt.json')
    require(recovery.get('parent_failed_attempt',{}).get('attempt_json_sha256')==timeout_hash and
            recovery.get('parent_failed_attempt',{}).get('scenario_id')=='FI_P400_0227',
            'Historical failed-parent lineage changed')
    return {
        'schema':SCHEMA_HISTORY,'created_utc':runner.now(),
        'archive_sha256':{zip_c.name:sha256(zip_c),zip_de.name:sha256(zip_de)},
        'attempt_slots_rechecked':8,'raw_artifacts_sha256_rechecked':590,
        'bounded_valid_fi_results':2,'historical_not_reproduced_slots':[3,7],
        'records':records,'new_xsim_runs_during_audit':0,
        'scope':'ARCHIVED_BOUNDED_FAILURE_QUALIFICATION_NOT_FULL400',
        'G6_production':'NOT_QUALIFIED','G7':'PREPARED_NOT_APPROVED',
        'full400_authorized':False,'release_gate':runner.GATE,
    }


def audit_rehearsal(repo:Path,results:Path,rehearsal:Path)->dict[str,Any]:
    require(rehearsal.parent==results and rehearsal.is_dir() and
            not rehearsal.is_symlink() and rehearsal.name.startswith('v4f4f_'),
            'Production rehearsal input must be fi/results/v4f4f_*')
    rows=runner.frozen_schedule(repo)
    pins=runner.pinned_inputs(repo,repo/'data')
    meta=runner.strict_json(rehearsal/runner.LEDGER_NAME)
    runner.verify_ledger(rehearsal,rows,pins,meta,complete=True)
    return {
        'schema':SCHEMA_REHEARSAL,'created_utc':runner.now(),
        'rehearsal_ledger_sha256':sha256(rehearsal/runner.LEDGER_NAME),
        'schedule_sha256':sha256(rehearsal/'schedule_400.csv'),
        'frozen_scenarios_rechecked':400,'batch_contracts_rechecked':16,
        'synthetic_events_rehashed':400,'new_xsim_runs_during_audit':0,
        'real_fi_scenarios_executed':0,'execution_scope':'SYNTHETIC_ONLY',
        'G6_production':'NOT_QUALIFIED','G7':'PREPARED_NOT_APPROVED',
        'full400_authorized':False,'release_gate':runner.GATE,
    }


def release_review(repo:Path,results:Path,source:Path|None,history:Path|None,
                   rehearsal:Path|None)->dict[str,Any]:
    """Never treat absence of signoff as consent, even if all receipts supplied."""
    evidence={}
    for key,path,filename,schema in (
        ('G0_SOURCE',source,'v4f4f_source_preflight.json',runner.SOURCE_SCHEMA),
        ('G6_BOUNDED_HISTORY',history,'v4f4f_historical_audit.json',SCHEMA_HISTORY),
        ('G6_400_SYNTHETIC',rehearsal,'v4f4f_rehearsal_audit.json',SCHEMA_REHEARSAL),
    ):
        if path is None:
            evidence[key]={'status':'NOT_SUPPLIED_NO_INFERENCE'}
            continue
        require(path.parent==results and path.is_dir() and not path.is_symlink(),
                'Receipt directory outside protected results area')
        receipt=path/filename
        obj=runner.strict_json(receipt)
        require(obj.get('schema')==schema and obj.get('full400_authorized') is False and
                obj.get('G6_production')=='NOT_QUALIFIED' and
                obj.get('new_xsim_runs',obj.get('new_xsim_runs_during_audit'))==0,
                'Receipt attempts to promote unapproved execution')
        if key=='G6_BOUNDED_HISTORY':
            require(obj.get('attempt_slots_rechecked')==8 and
                    obj.get('raw_artifacts_sha256_rechecked')==590,
                    'Historical receipt not all eight canaries')
        if key=='G6_400_SYNTHETIC':
            require(obj.get('synthetic_events_rehashed')==400,
                    'Synthetic audit receipt lacks 400 events')
        evidence[key]={'status':'RECEIPT_PRESENT_NOT_RELEASE_AUTHORIZATION',
                       'receipt_sha256':sha256(receipt)}
    return {
        'schema':SCHEMA_RELEASE,'created_utc':runner.now(),
        'read_only_evidence_refs':evidence,
        'G0':'PARTIAL_SOURCE_PINS_NOT_FINAL_SIGNOFF',
        'G1_G2_G3_G4':'HISTORICAL_DIRECTED_EVIDENCE_NEEDS_FINAL_RELEASE_REVIEW',
        'G5':'PARTIAL_SCIENTIFIC_DENOMINATOR_NOT_SIGNED',
        'G6':'OFFLINE_CONTRACT_NOT_PRODUCTION_QUALIFIED',
        'G7':'PREPARED_NOT_APPROVED_NO_TWO_PERSON_SEAL_VERIFIED',
        'G0_G7_signed_authorization':False,
        'full400_runner_qualified':False,'full400_authorized':False,
        'full400_executed':False,'new_xsim_runs_during_audit':0,
        'attempt_budget_8_of_8_exhausted':True,
        'full400_gate':'BLOCKED_INDEPENDENT_SIGNOFF_REQUIRED',
    }


def dispatch(args:argparse.Namespace)->dict[str,Any]:
    require(not args.execute and not args.approve,
            'Independent audit forbids execute/approve')
    repo=args.repo.resolve()
    results=runner.path_from(repo,args.results_dir)
    out=runner.path_from(repo,args.out_dir)
    runner.safe_destination(repo,results,out)
    if args.mode=='rehearsal':
        require(args.rehearsal_dir is not None,'--rehearsal-dir required')
        target=runner.path_from(repo,args.rehearsal_dir)
        report=audit_rehearsal(repo,results,target)
        file='v4f4f_rehearsal_audit.json'
        marker='V4F4F_REHEARSAL_AUDIT_PASS: 400 synthetic receipts; 0 XSim; FULL400 BLOCKED'
    elif args.mode=='history':
        require(args.zip_c is not None and args.zip_de is not None,
                'Both original canary ZIPs are required')
        report=check_historical_zip_pair(runner.path_from(repo,args.zip_c),
                                         runner.path_from(repo,args.zip_de))
        file='v4f4f_historical_audit.json'
        marker='V4F4F_HISTORY_AUDIT_PASS: 8/8 attempts, 590/590 raw hashes; 0 XSim; FULL400 BLOCKED'
    elif args.mode=='release-review':
        report=release_review(repo,results,
              runner.path_from(repo,args.source_dir) if args.source_dir else None,
              runner.path_from(repo,args.history_dir) if args.history_dir else None,
              runner.path_from(repo,args.rehearsal_audit_dir) if args.rehearsal_audit_dir else None)
        file='v4f4f_release_gaps.json'
        marker='V4F4F_RELEASE_REVIEW: G6 NOT QUALIFIED; G7 NOT APPROVED; 0 XSim; FULL400 BLOCKED'
    else:
        raise ValueError('Unsupported independent audit mode')
    out.mkdir(parents=False,exist_ok=False)
    runner.atomic_json(out/file,report)
    print(marker,flush=True)
    print('V4F4F_AUDIT_OUTPUT: '+str(out),flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    p.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f4f_audit_01'))
    p.add_argument('--mode',choices=('rehearsal','history','release-review'),required=True)
    p.add_argument('--rehearsal-dir',type=Path)
    p.add_argument('--zip-c',type=Path)
    p.add_argument('--zip-de',type=Path)
    p.add_argument('--source-dir',type=Path)
    p.add_argument('--history-dir',type=Path)
    p.add_argument('--rehearsal-audit-dir',type=Path)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--approve',action='store_true')
    args=p.parse_args()
    try:dispatch(args)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,AssertionError,zipfile.BadZipFile) as exc:
        raise SystemExit('V4F4F_AUDIT_FAIL: '+str(exc)) from exc


if __name__=='__main__':main()

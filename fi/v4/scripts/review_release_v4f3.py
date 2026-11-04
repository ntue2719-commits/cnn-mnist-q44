#!/usr/bin/env python3
"""V4f Phase 3: read-only Full400 science/release-gap review. NEVER executes FI.

Replays V4e ancestry, V4f Phase 1 400-plan and V4f Phase 2 8-XSim
bounded smoke with pinned inputs. Emits a versioned *candidate* scientific
protocol and G0..G7 review; never freezes the protocol, qualifies the Full400
runner, promotes frozen target status or grants release permission.

Must run on Windows with the complete authoritative Vivado/Evidence source tree.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
for p in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from audit_v4 import V3A_NAME,read_json,require
from pilot400_common import FIELDS,load_plan,verify_manifest,read_csv,sha256
import qualify_full400_v4f as v4f
import run_full400_batches_v4f as v4f2
import audit_v4f2_smoke as v4f2audit
import qualify_runner_v4d as v4da

SCHEMA='CNN_PEP_V4F3_INDEPENDENT_RELEASE_GAP_REVIEW_V1'
STATUS='EVIDENCE_REVIEW_PASS_RELEASE_GATE_BLOCKED'
PROTOCOL_REL=Path('fi/v4/protocol/scientific_protocol_candidate_v1.json')
PROTOCOL_SCHEMA='CNN_PEP_V4F3_SCIENTIFIC_PROTOCOL_CANDIDATE_V1'
GATE_STATUSES={
    'G0':'PARTIAL_PORTABLE_SOURCE_ARCHIVE_PENDING',
    'G1':'EVIDENCE_PASS_CONTROL10_SCOPE',
    'G2':'EVIDENCE_PASS_400_FAULT_FREE_WINDOWS',
    'G3':'EVIDENCE_PASS_40_IMG0000_DIRECTED_MECHANISMS',
    'G4':'EVIDENCE_PASS_20_TARGET_DIRECTED_QUALIFICATION',
    'G5':'PARTIAL_8_SMOKE_PARSER_ONLY_FULL400_CONTRACT_PENDING',
    'G6':'BLOCKED_NO_FULL400_EXECUTION_BACKEND_QUALIFICATION',
    'G7':'BLOCKED_SCIENTIFIC_PROTOCOL_CANDIDATE_NOT_APPROVED',
}

def insist_no_release(meta:dict,kind:str)->None:
    for k in ('full400_authorized','full400_runner_qualified','full400_executed',
              'scientific_protocol_frozen','target_status_modified'):
        require(meta.get(k) is False,kind+' contains forbidden release flag: '+k)

def inspect_plan(repo:Path,results:Path,preflight:Path,v4e:Path,
                 frozen:list[dict])->dict:
    require(preflight.is_dir() and preflight.parent==results,
            'V4f Phase 1 ancestry output must reside in fi/results')
    obj=read_json(preflight/'v4f_preflight.json')
    require(obj.get('schema')==v4f.SCHEMA and
            obj.get('status')==v4f.STATUS_REPLAY and
            obj.get('selected_count')==400 and
            obj.get('strict_v4e_raw_replay_completed') is True and
            obj.get('vivado_rtl_source_replay_verified') is True and
            obj.get('xsim_runs_added')==0 and
            obj.get('v4e_review_sha256')==sha256(v4e/'v4e_release_review.json'),
            'V4f Phase 1 evidence is not a strict 400-plan ancestry replay')
    insist_no_release(obj,'V4f Phase 1')
    require(obj.get('frozen_manifest_sha256')==v4f.FROZEN_HASHES['fi/scenarios/pilot400.csv'] and
            obj.get('frozen_plan_sha256')==v4f.FROZEN_HASHES['fi/config/pilot400_targets.csv'] and
            obj.get('selected_sha256')==sha256(preflight/'selected_scenarios.csv') and
            read_csv(preflight/'selected_scenarios.csv',FIELDS)==frozen and
            obj.get('analysis_protocol_draft_sha256')==sha256(preflight/'analysis_protocol_DRAFT.json') and
            read_json(preflight/'analysis_protocol_DRAFT.json')==v4f.PROTOCOL_DRAFT,
            'V4f Phase 1 plan, SHA256 or draft protocol differs')
    # The original source pins use Windows-authoritative absolute paths.
    pins=obj.get('preflight_input_sha256')
    require(isinstance(pins,dict) and len(pins)>30 and
            all(Path(p).is_file() and sha256(Path(p))==h for p,h in pins.items()),
            'V4f Phase 1 source or sample pins unavailable/changed')
    return obj

def inspect_smoke(results:Path,smoke:Path,smoke_audit:Path,v4e:Path,
                  frozen:list[dict],plan:list[dict],weights:Path,repo:Path)->dict:
    require(smoke.is_dir() and smoke_audit.is_dir() and
            smoke.parent==results and smoke_audit.parent==results and
            smoke!=smoke_audit,'V4f Phase 2 smoke and audit must be siblings of fi/results')
    selected=v4f2.select_smoke(frozen,plan)
    pins=v4f2._source_pins(repo,results,weights,v4e)
    meta=read_json(smoke/'v4f2_manifest.json')
    require(meta.get('schema')==v4f2.SCHEMA and
            meta.get('status')==v4f2.STATUS_COMPLETE and
            meta.get('mode')=='qualification-smoke' and
            meta.get('selected_count')==8 and
            meta.get('xsim_runs_committed')==8 and
            meta.get('ancestry_replayed') is True and
            meta.get('input_pins')==pins and
            meta.get('input_signature',{}).get('selected_ids')==list(v4f2.SMOKE_IDS) and
            meta.get('input_signature',{}).get('v4e_review_sha256')==sha256(v4e/'v4e_release_review.json'),
            'V4f2 smoke provenance/complete state inconsistent')
    insist_no_release(meta,'V4f2 smoke')
    require(meta.get('validation_csv_sha256')==sha256(smoke/'smoke_validation.csv') and
            len(meta.get('builds',[])) in (1,2) and
            meta.get('uncommitted_artifacts_auto_recovered') is False,
            'V4f2 CSV/build-count/automatic-recovery state invalid')
    v4f2.verify_records(smoke,selected,meta,pins,results/V3A_NAME,allow_complete=True)
    evidence=read_csv(smoke/'smoke_validation.csv',v4f2.RUN_COLUMNS)
    require(evidence==[r['validation'] for r in meta['records']] and
            all(r['consumer_proof_scope']=='NOT_INDEPENDENTLY_MONITORED_V4F2'
                for r in evidence),
            'V4f2 smoke cannot claim E3 or change committed rows')
    audited=read_json(smoke_audit/'v4f2_smoke_audit.json')
    require(audited.get('schema')==v4f2audit.SCHEMA and
            audited.get('status')==v4f2audit.STATUS and
            audited.get('smoke_manifest_sha256')==sha256(smoke/'v4f2_manifest.json') and
            audited.get('smoke_validation_sha256')==sha256(smoke/'smoke_validation.csv') and
            audited.get('v4e_review_sha256')==sha256(v4e/'v4e_release_review.json') and
            audited.get('scenario_ids')==list(v4f2.SMOKE_IDS) and
            audited.get('distinct_scenarios_verified')==8 and
            audited.get('new_xsim_runs_during_audit')==0 and
            audited.get('outcomes')==dict(sorted(Counter(x['outcome'] for x in evidence).items())) and
            audited.get('build_count')==len(meta['builds']) and
            audited.get('full400_gate')=='BLOCKED_INDEPENDENT_G0_G7_APPROVAL_REQUIRED' and
            audited.get('full400_authorized') is False and
            audited.get('full400_runner_qualified') is False and
            audited.get('scientific_protocol_frozen') is False and
            audited.get('target_status_modified') is False,
            'V4f2 saved independent smoke-audit receipt differs from raw evidence')
    return {'smoke_manifest_sha256':sha256(smoke/'v4f2_manifest.json'),
            'smoke_audit_sha256':sha256(smoke_audit/'v4f2_smoke_audit.json'),
            'smoke_csv_sha256':sha256(smoke/'smoke_validation.csv'),
            'scenario_ids':list(v4f2.SMOKE_IDS),'build_count':len(meta['builds']),
            'outcomes':audited['outcomes']}

def load_protocol(repo:Path)->tuple[dict,str]:
    path=repo/PROTOCOL_REL
    obj=read_json(path)
    require(obj.get('schema')==PROTOCOL_SCHEMA and
            obj.get('status')=='CANDIDATE_NOT_FROZEN_NOT_APPROVED' and
            obj.get('approval',{}).get('independent_reviewer_signed') is False and
            obj.get('approval',{}).get('operator_signed') is False and
            obj.get('approval',{}).get('full400_execution_authorized') is False and
            obj.get('design',{}).get('planned_scenarios')==400 and
            obj.get('outcome_priority')==v4f.PROTOCOL_DRAFT['outcome_precedence'] and
            obj.get('harmful_outcomes')==v4f.PROTOCOL_DRAFT['harmful_outcomes'] and
            obj.get('scientific_limits',{}).get('physical_seu_population_claim') is False,
            'V4f3 scientific protocol candidate missing or modified in essential semantics')
    require(obj.get('observation_levels',{}).get('L0') and
            obj.get('observation_levels',{}).get('L1') and
            obj.get('observation_levels',{}).get('L2') and
            obj.get('denominators')==v4f.PROTOCOL_DRAFT['denominators'] and
            obj.get('missing_value_policy',{}).get('not_observed_is_not_zero') is True and
            obj.get('nonexposure_policy',{}).get('final_output_match_does_not_prove_nonexposure') is True,
            'V4f3 candidate must distinguish observed, missing and masking')
    return obj,sha256(path)

def review(repo:Path,results:Path,weights:Path,v4e:Path,plan_dir:Path,
           smoke:Path,smoke_audit:Path,out:Path)->dict:
    repo=repo.resolve();results=results.resolve();weights=weights.resolve()
    v4e=v4e.resolve();plan_dir=plan_dir.resolve()
    smoke=smoke.resolve();smoke_audit=smoke_audit.resolve();out=out.resolve()
    require(results==repo/'fi/results' and
            out.parent==results and out.name.startswith('v4f3_') and
            not out.exists() and not out.is_symlink(),
            'V4f3 only writes a new fi/results/v4f3_* directory')
    v4f.safety_output(repo,out,weights,results)
    v4f.check_frozen(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    frozen=v4f.select_400(verify_manifest(repo/'fi/scenarios/pilot400.csv',plan),plan)
    v4f.check_assets_and_provenance(repo,weights)
    v4e_meta,v4e_matrix=v4f.verify_v4e_saved(repo,results,v4e)
    v4f.replay_v4e_raw(repo,results,weights,v4e,v4e_meta,v4e_matrix,frozen,plan)
    phase1=inspect_plan(repo,results,plan_dir,v4e,frozen)
    sm=inspect_smoke(results,smoke,smoke_audit,v4e,frozen,plan,weights,repo)
    protocol,protocol_sha=load_protocol(repo)
    # Outputs are records of evidence scope and remaining blockers; zero release authority.
    rowlist=[{'gate':name,'status':status} for name,status in GATE_STATUSES.items()]
    record={
        'schema':SCHEMA,'status':STATUS,
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'scope':{'frozen_plan':400,'target_qualification_IMG0000':20,
                 'consumer_decision_IMG0000':40,
                 'fault_free_temporal_windows':400,
                 'v4d_shakedown_mechanisms':32,
                 'v4f2_bounded_smoke_mechanisms':8,
                 'full400_fi_executed':0},
        'gates':dict(GATE_STATUSES),
        'v4e_review_sha256':sha256(v4e/'v4e_release_review.json'),
        'v4f_phase1_sha256':sha256(plan_dir/'v4f_preflight.json'),
        'v4f2':sm,
        'scientific_protocol_candidate_sha256':protocol_sha,
        'scientific_protocol_status':protocol['status'],
        'source_authority':'vivado',
        'new_xsim_runs_during_review':0,
        'original_frozen_files_modified':False,
        'target_status_modified':False,
        'full400_runner_qualified':False,'full400_authorized':False,
        'scientific_protocol_frozen':False,'full400_executed':False,
        'release_status':'BLOCKED_EXPLICIT_INDEPENDENT_G0_G7_SIGNOFF_REQUIRED',
    }
    out.mkdir(exist_ok=False,parents=True)
    v4da.atomic_write_json(out/'v4f3_release_review.json',record)
    v4da.atomic_write_json(out/'scientific_protocol_candidate.json',protocol)
    from pilot400_common import write_csv
    write_csv(out/'g0_g7_review.csv',('gate','status'),rowlist)
    (out/'v4f3_report.md').write_text('\n'.join([
        '# CNN_PEP V4f Phase 3 — Independent release-gap review','',
        '- V4e historical raw ancestry: **REPLAY PASS**.',
        '- V4f phase-1 frozen 400-tuple planning and source pins: **PASS**.',
        '- V4f phase-2 eight bounded XSim smoke log/checkpoint audit: **PASS**.',
        '- Scientific analysis protocol: **CANDIDATE ONLY**; review/freeze is pending.',
        '- G6: **BLOCKED**, no independently qualified full400 execution backend.',
        '- G7: **BLOCKED**, independent protocol/operator approval absent.',
        '- G0/G5: **PARTIAL**, not claimed as complete release qualification.',
        '- No new XSim, no frozen source or target status edits.',
        '- **FULL400 REMAINS BLOCKED**.','',
        'Do not treat this review as approval to execute 400 FI configurations.','',
    ]),encoding='utf-8')
    print('V4F3_EVIDENCE_REVIEW_PASS: 400 frozen tuples; V4e ancestry and 8/8 V4f2 smoke logs replayed; 0 XSim',flush=True)
    print('V4F3_PROTOCOL: CANDIDATE_NOT_FROZEN_NOT_APPROVED',flush=True)
    print('V4F3_G0_G7_GATE: BLOCKED (G0/G5 partial, G6/G7 open; FULL400 BLOCKED)',flush=True)
    print('V4F3_OUTPUT: '+str(out),flush=True)
    return record

def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--v4e-dir',type=Path,default=Path('fi/results/v4e_release_review_01'))
    ap.add_argument('--v4f-ancestry-dir',type=Path,default=Path('fi/results/v4f_ancestry_01'))
    ap.add_argument('--smoke-dir',type=Path,default=Path('fi/results/v4f2_smoke_01'))
    ap.add_argument('--smoke-audit-dir',type=Path,default=Path('fi/results/v4f2_smoke_audit_01'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f3_release_review_01'))
    ap.add_argument('--execute',action='store_true',help='Forbidden: this auditor never runs FI')
    ap.add_argument('--approve',action='store_true',help='Forbidden: requires separate independent release')
    args=ap.parse_args()
    try:
        require(not args.execute and not args.approve,
                'V4f3 cannot execute or authorize Full400')
        repo=args.repo.resolve()
        loc=lambda x:v4f.resolved(repo,x)
        review(repo,loc(args.results_dir),loc(args.weights_dir),loc(args.v4e_dir),
               loc(args.v4f_ancestry_dir),loc(args.smoke_dir),
               loc(args.smoke_audit_dir),loc(args.out_dir))
    except (ValueError,RuntimeError,OSError,TypeError,KeyError) as e:
        raise SystemExit('V4F3_FAIL: '+str(e)) from e

if __name__=='__main__':main()

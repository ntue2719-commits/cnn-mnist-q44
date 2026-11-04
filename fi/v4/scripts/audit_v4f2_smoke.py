#!/usr/bin/env python3
"""Read-only V4f2 8-XSim smoke auditor, never a Full400 release approval.

Replays source/provenance V4e, all committed raw XSim logs, exact eight tuples,
compile .prj and build hashes, plus per-scenario stdout/cmd/wrapper. A PASS
qualifies only the bounded engine smoke. Does not run Vivado or 400 FI.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
for d in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(d) not in sys.path:sys.path.insert(0,str(d))

from audit_v4 import V3A_NAME,read_json,require
from pilot400_common import FIELDS,load_plan,verify_manifest,read_csv,sha256
import qualify_full400_v4f as v4f
import run_full400_batches_v4f as v4f2
import qualify_runner_v4d as v4da

SCHEMA='CNN_PEP_V4F2_EIGHT_SMOKE_READ_ONLY_REPLAY_V1'
STATUS='EIGHT_SMOKE_XSIM_LOGS_REPLAYED_FULL400_BLOCKED'


def audit(repo:Path,results:Path,weights:Path,v4e_dir:Path,
          smoke:Path,out:Path)->dict:
    repo=repo.resolve();results=results.resolve();weights=weights.resolve()
    v4e_dir=v4e_dir.resolve();smoke=smoke.resolve();out=out.resolve()
    require(not out.exists() and out.parent==results and
            out.name.startswith('v4f2_') and out!=smoke and
            smoke.parent==results and smoke.is_dir(),
            'V4f2 auditor refuses invalid/overlapping output or missing smoke')
    v4f.safety_output(repo,out,weights,results)
    v4f.check_frozen(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    frozen=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    v4f.select_400(frozen,plan)
    selected=v4f2.select_smoke(frozen,plan)
    pins=v4f2._source_pins(repo,results,weights,v4e_dir)
    saved=read_json(smoke/'v4f2_manifest.json')
    require(saved.get('schema')==v4f2.SCHEMA and
            saved.get('status')==v4f2.STATUS_COMPLETE and
            saved.get('mode')=='qualification-smoke' and
            saved.get('ancestry_replayed') is True and
            saved.get('selected_count')==8 and
            saved.get('xsim_runs_committed')==8 and
            saved.get('input_pins')==pins and
            isinstance(saved.get('input_signature'),dict) and
            saved['input_signature'].get('selected_ids')==list(v4f2.SMOKE_IDS) and
            saved['input_signature'].get('mode')=='qualification-smoke' and
            bool(saved['input_signature'].get('project')) and
            bool(saved['input_signature'].get('vivado')) and
            saved['input_signature'].get('v4e_review_sha256') ==
                sha256(v4e_dir/'v4e_release_review.json'),
            'V4f2 saved smoke manifest/scope not approved for read-only replay')
    meta,matrix=v4f.verify_v4e_saved(repo,results,v4e_dir)
    v4f.replay_v4e_raw(repo,results,weights,v4e_dir,meta,matrix,frozen,plan)
    v4f2.verify_records(smoke,selected,saved,pins,results/V3A_NAME,
                        allow_complete=True)
    rows=read_csv(smoke/'smoke_validation.csv',v4f2.RUN_COLUMNS)
    require(rows==[r['validation'] for r in saved['records']] and
            saved.get('validation_csv_sha256') ==
                sha256(smoke/'smoke_validation.csv'),
            'V4f2 final smoke CSV/content hash is inconsistent with checkpoint')
    require(len(saved.get('builds',[])) in (1,2),
            'V4f2 expected single-build or one resumed second-build smoke')
    counts=dict(sorted(Counter(r['outcome'] for r in rows).items()))
    record={
        'schema':SCHEMA,'status':STATUS,
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'source_authority':'vivado',
        'smoke_manifest_sha256':sha256(smoke/'v4f2_manifest.json'),
        'smoke_validation_sha256':sha256(smoke/'smoke_validation.csv'),
        'v4e_review_sha256':sha256(v4e_dir/'v4e_release_review.json'),
        'auditor_script_sha256':sha256(Path(__file__)),
        'scenario_ids':list(v4f2.SMOKE_IDS),
        'distinct_scenarios_verified':8,'new_xsim_runs_during_audit':0,
        'outcomes':counts,'build_count':len(saved['builds']),
        'full400_runner_qualified':False,'full400_authorized':False,
        'scientific_protocol_frozen':False,
        'target_status_modified':False,
        'full400_gate':'BLOCKED_INDEPENDENT_G0_G7_APPROVAL_REQUIRED',
    }
    out.mkdir(exist_ok=False,parents=True)
    v4da.atomic_write_json(out/'v4f2_smoke_audit.json',record)
    (out/'v4f2_smoke_audit_report.md').write_text('\n'.join([
        '# V4f2 Bounded Engine Smoke Replay','',
        '- Source scope: eight selected IMG0001/IMG0008 FI mechanisms.',
        '- Strict log/source/.prj/outcome/checkpoint replay: **8/8 PASS**.',
        '- Historical V4e ancestor raw replay: **PASS**.',
        '- No new FI executions in auditor.',
        '- No E3 consumer proof inferred from these mechanism logs.',
        '- No target status promotion; no frozen source changes.',
        '- No scientific protocol approval.',
        '- Full400 execution: **BLOCKED**.','',
    ]),encoding='utf-8')
    print('V4F2_SMOKE_REPLAY_PASS: 8/8 XSim log mechanisms; '
          'V4e ancestry replayed; 0 new XSim; FULL400 BLOCKED',flush=True)
    print('V4F2_AUDIT_OUTPUT: '+str(out),flush=True)
    return record


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--v4e-dir',type=Path,default=Path('fi/results/v4e_release_review_01'))
    ap.add_argument('--smoke-dir',type=Path,default=Path('fi/results/v4f2_smoke_01'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4f2_smoke_audit_01'))
    args=ap.parse_args()
    repo=args.repo.resolve()
    loc=lambda p:v4f.resolved(repo,p)
    try:
        audit(repo,loc(args.results_dir),loc(args.weights_dir),
              loc(args.v4e_dir),loc(args.smoke_dir),loc(args.out_dir))
    except (ValueError,RuntimeError,KeyError,OSError,TypeError) as exc:
        raise SystemExit('V4F2_AUDIT_FAIL: '+str(exc)) from exc

if __name__=='__main__':main()

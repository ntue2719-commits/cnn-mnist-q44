#!/usr/bin/env python3
"""CNN_PEP V4f phase-1: independent, non-executing Full400 plan qualification.

No XSim calls, no new fault injections, no target promotion, no release approval.
This is deliberately NOT a complete full400 campaign runner.  The --execute
option is rejected even when supplied by an operator.  It produces immutable
planning/provenance evidence to review before implementing an approved runner.

Modes:
  --plan-only       Frozen 400-row plan, assets, source pins; ancestry not proven.
  --replay-ancestry In addition, replay original V4e/V4d-C/D and earlier ancestry.

The second mode requires all historical raw evidence directories on the same
Windows project machine, and must not be replaced by trusting a console PASS.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
FI_SCRIPTS = HERE.parents[1] / 'scripts'
V3B_SCRIPTS = HERE.parents[1] / 'v3b/scripts'
for folder in (HERE, FI_SCRIPTS, V3B_SCRIPTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from audit_v4 import V3A_NAME, read_json, require  # noqa: E402
from pilot400_common import (FIELDS, SAMPLES, CLASSES, ASSET_SIZES,
    load_plan, verify_manifest, validate_assets, expected_rows,
    read_csv, write_csv, sha256, asset_lines)  # noqa: E402
import audit_release_v4e as v4e  # noqa: E402
import qualify_resume_v4dc as v4dc  # noqa: E402
import qualify_abrupt_resume_v4dd as v4dd  # noqa: E402
import qualify_runner_v4d as v4da  # noqa: E402

SCHEMA = 'CNN_PEP_V4F_FULL400_NONEXECUTING_PLAN_QUALIFICATION_V1'
STATUS_PLAN = 'PLAN_400_VERIFIED_ANCESTRY_NOT_REPLAYED_RELEASE_BLOCKED'
STATUS_REPLAY = 'PLAN_400_AND_V4E_ANCESTRY_REPLAYED_RELEASE_BLOCKED'
FROZEN_HASHES = {
    'fi/config/fi_targets_candidates_v2.csv': 'd9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
    'fi/config/pilot400_targets.csv': '98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
    'fi/scenarios/pilot400.csv': 'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
    'fi/tb/tb_cnn_mnist_fi_pilot400.v': '2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
}
SAMPLES_ORDER = tuple(sample for sample, _ in SAMPLES)
PINS_CODE = (
    'fi/scripts/pilot400_common.py',
    'fi/scripts/parse_fi_v2.py',
    'fi/scripts/pilot400_direct_xsim.py',
    'fi/scripts/run_pilot400_target_validation_v3.py',
    'fi/v4/scripts/audit_release_v4e.py',
    'fi/v4/scripts/qualify_runner_v4d.py',
    'fi/v4/scripts/qualify_temporal_v4db.py',
    'fi/v4/scripts/qualify_resume_v4dc.py',
    'fi/v4/scripts/qualify_abrupt_resume_v4dd.py',
    'fi/v4/scripts/pinned_direct_snapshot_v4db.py',
    'fi/v4/scripts/qualify_full400_v4f.py',
)
PARENT_NAMES = {
    'V4': 'v4_cross_evidence_audit_02/v4_audit.json',
    'V4C': 'v4c_readiness_audit_01/v4c_manifest.json',
    'V4D_A': 'v4d_canary_01/v4d_canary_manifest.json',
    'V4D_B': 'v4db_temporal_direct_02/v4db_temporal_manifest.json',
    'V4D_C': 'v4dc_resume_01/v4dc_resume_manifest.json',
    'V4D_D': 'v4dd_abrupt_recovery_01/v4dd_recovery_manifest.json',
}
PROTOCOL_DRAFT = {
    'schema': 'CNN_PEP_V4F_SCIENTIFIC_PROTOCOL_DRAFT_V1',
    'status': 'DRAFT_NOT_FROZEN_NOT_APPROVED',
    'population': '400 selected RTL logical-state-bit FI tuples; not random physical SEU samples',
    'design': {'targets': 20, 'samples': 10, 'tuples_per_target_sample': 2,
               'structural_classes': ['CTL', 'BUF', 'MEM', 'DAT']},
    'outcome_precedence': ['NONCOMPLETION', 'PROTOCOL_VIOLATION',
        'WRONG_LABEL', 'WRONG_LOGITS_SAME_LABEL', 'FINAL_OUTPUT_MATCH'],
    'harmful_outcomes': ['NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL'],
    'observation_levels': {
        'L0': 'wrong_label OR noncompletion OR protocol_violation',
        'L1': 'L0 OR final_raw_logits_mismatch',
        'L2': 'L1 OR pool1_mismatch OR pool2_mismatch (only where checkpointed)',
        'E3': 'independent, out-of-band consumer proof; never on-chip detection'},
    'denominators': ['planned','executed','injected','eligible','consumer_verified',
        'completed','classifiable','missing','inconclusive'],
    'missing_values': ['NOT_OBSERVED', 'NOT_APPLICABLE', 'INCONCLUSIVE'],
    'analysis': 'paired comparisons use identical scenario IDs; no population SEU inference',
    'operator_approval': False,
}


def resolved(repo: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (repo / value).resolve()


def check_frozen(repo: Path) -> None:
    for relative, digest in FROZEN_HASHES.items():
        p = repo / relative
        require(p.is_file() and sha256(p) == digest,
                'V4f immutable frozen hash mismatch: ' + relative)


def select_400(manifest: list[dict[str, str]],
               plan: list[dict[str, str]]) -> list[dict[str, str]]:
    """Reject missing, duplicate, reordered, or modified planned tuples."""
    require(len(plan) == 20 and len(manifest) == 400 and
            manifest == expected_rows(plan),
            'V4f requires all 400 original, ordered frozen scenarios')
    require([r['scenario_id'] for r in manifest] ==
            [f'FI_P400_{i:04d}' for i in range(1, 401)],
            'V4f scenario IDs are missing, duplicated or reordered')
    require(len({tuple(r[k] for k in FIELDS) for r in manifest}) == 400,
            'V4f has duplicate scenario tuples')
    require(Counter(r['target_class'] for r in manifest) ==
            {cls:100 for cls in CLASSES}, 'V4f class coverage mismatch')
    require(Counter(r['sample_id'] for r in manifest) ==
            {sample:40 for sample in SAMPLES_ORDER},
            'V4f IMG0000..IMG0009 coverage mismatch')
    require(Counter(r['target_id'] for r in manifest) ==
            {p['target_id']:20 for p in plan}, 'V4f target coverage mismatch')
    by_plan = {p['target_id']:p for p in plan}
    for tid in by_plan:
        for sample in SAMPLES_ORDER:
            pair = [r for r in manifest if r['target_id'] == tid and
                    r['sample_id'] == sample]
            p = by_plan[tid]
            expected = {(p['bit_index'],p['offset_a']),
                        (p['bit_index_b'],p['offset_b'])}
            require(len(pair) == 2 and
                    {(r['bit_index'],r['injection_offset']) for r in pair} == expected and
                    len(expected) == 2,
                    'V4f frozen 2-config pair missing/changed: ' + tid + '/' + sample)
    dat = [r for r in manifest if r['target_id'] == 'DAT_0010']
    require(len(dat) == 20 and
            {r['injection_offset'] for r in dat} == {'1340'} and
            Counter(r['bit_index'] for r in dat) == {'0':10,'1':10},
            'V4f DAT_0010 bit0/bit1 same-cycle refreeze changed')
    return manifest


def check_assets_and_provenance(repo: Path, weights: Path) -> dict[str,str]:
    """Bind all 10 sample golden files and 6 weights to original provenance."""
    count, missing = validate_assets(repo/'fi/pilot400/samples', True)
    require(count == 10 and not missing, 'V4f requires ten valid real sample assets')
    source = repo/'fi/pilot400/samples/sample_provenance.json'
    obj = read_json(source)
    sample_meta = obj.get('samples')
    weight_meta = obj.get('weight_hashes')
    require(isinstance(sample_meta, list) and len(sample_meta) == 10 and
            [x.get('sample_id') for x in sample_meta] == list(SAMPLES_ORDER) and
            isinstance(weight_meta, dict) and len(weight_meta) == 6,
            'V4f sample provenance metadata missing/changed')
    from export_pilot400_samples import PARAMS
    require(set(weight_meta) == set(PARAMS), 'V4f weight file list differs from P0 export')
    pins:dict[str,str] = {str(source.resolve()):sha256(source)}
    for name,(count,shape) in PARAMS.items():
        p=weights/name
        asset_lines(p,count,2)
        require(sha256(p) == weight_meta[name],
                'V4f weight/bias source SHA256 changed: ' + name)
        pins[str(p.resolve())] = sha256(p)
    for record,(sample, index) in zip(sample_meta, SAMPLES):
        folder=repo/'fi/pilot400/samples'/sample
        require(record.get('mnist_test_index') == index and
                record.get('golden_prediction') ==
                    asset_lines(folder/'prediction.hex',1,2)[0] and
                sha256(folder/'input.hex') == record.get('input_sha256'),
                'V4f MNIST sample provenance mismatch: ' + sample)
        for name in ASSET_SIZES:
            p=folder/name
            pins[str(p.resolve())] = sha256(p)
    return pins


def source_pins(repo: Path, assets: dict[str,str],
                results: Path, v4e_path: Path) -> dict[str,str]:
    pins = dict(assets)
    for relative in (*FROZEN_HASHES, *PINS_CODE):
        p=repo/relative
        require(p.is_file(), 'V4f source input missing: ' + relative)
        pins[str(p.resolve())] = sha256(p)
    if v4e_path.is_file():
        pins[str(v4e_path.resolve())] = sha256(v4e_path)
    v3a=results/V3A_NAME/'validation_manifest.json'
    if v3a.is_file():
        pins[str(v3a.resolve())] = sha256(v3a)
    return dict(sorted(pins.items()))


def verify_v4e_saved(repo: Path, results: Path,
                     v4e_dir: Path) -> tuple[dict[str,Any],list[dict[str,str]]]:
    """Verify V4e JSON/CSV and parent hashes without inventing PASS claims."""
    path=v4e_dir/'v4e_release_review.json'
    meta=read_json(path)
    require(meta.get('schema') == v4e.SCHEMA and
            meta.get('status') == v4e.STATUS and
            meta.get('review_scope') == 'READ_ONLY_CROSS_STAGE_EVIDENCE_NOT_EXECUTION_APPROVAL' and
            meta.get('full400_gate') == v4e.FULL400_GATE and
            meta.get('full400_authorized') is False and
            meta.get('full400_runner_qualified') is False and
            meta.get('full400_fi_scenarios_executed') is False and
            meta.get('target_status_modified') is False and
            meta.get('directed_proved_img0000') == 40 and
            meta.get('directed_targets_proved') == 20 and
            meta.get('baseline_profiles') == 10 and
            meta.get('baseline_timing_checks') == 400 and
            meta.get('shakedown_mechanism_runs') == 32 and
            meta.get('distinct_shakedown_scenarios') == 32 and
            meta.get('mid_xsim_recovery_proved') is False and
            meta.get('power_loss_recovery_proved') is False,
            'V4f V4e report does not have the approved READ-ONLY scope')
    require(meta.get('frozen_plan_sha256') == FROZEN_HASHES['fi/config/pilot400_targets.csv'] and
            meta.get('frozen_manifest_sha256') == FROZEN_HASHES['fi/scenarios/pilot400.csv'] and
            meta.get('original_fi_tb_sha256') == FROZEN_HASHES['fi/tb/tb_cnn_mnist_fi_pilot400.v'],
            'V4f V4e report changed original frozen source hashes')
    matrix_path=v4e_dir/'v4e_shakedown_matrix.csv'
    require(matrix_path.is_file() and
            sha256(matrix_path) == meta.get('shakedown_matrix_sha256'),
            'V4f V4e shakedown matrix missing or SHA256 mismatch')
    matrix=read_csv(matrix_path,v4e.MATRIX_COLUMNS)
    frozen_rows=read_csv(repo/'fi/scenarios/pilot400.csv',FIELDS)
    by_id={r['scenario_id']:r for r in frozen_rows}
    sample_pairs={'V4D_A':{'IMG0000','IMG0009'},
                  'V4D_C':{'IMG0004','IMG0005'},
                  'V4D_D':{'IMG0002','IMG0007'}}
    require(len(matrix)==32 and
            len({r['scenario_id'] for r in matrix})==32 and
            Counter(r['evidence_stage'] for r in matrix) ==
                {'V4D_A':16,'V4D_C':8,'V4D_D':8} and
            Counter(r['outcome'] for r in matrix)==meta.get('shakedown_outcomes') and
            all(r['scenario_id'] in by_id and
                all(r[k]==by_id[r['scenario_id']][k] for k in FIELDS) and
                r['sample_id'] in sample_pairs[r['evidence_stage']] and
                r['independent_consumer_proof']=='NO' and
                r['baseline_temporal_eligibility']=='V4DB_10_SAMPLE_40_WINDOWS_PASS' and
                r['injection_verified']=='YES' and
                bool(re.fullmatch(r'[0-9a-f]{64}',r['log_sha256']))
                for r in matrix),
            'V4f V4e shakedown rows are incomplete, mismatched or misclassified')
    parents=meta.get('parents')
    require(isinstance(parents,dict) and set(parents)==set(PARENT_NAMES),
            'V4f missing V4e parent evidence inventory')
    for stage,relative in PARENT_NAMES.items():
        parent=results/relative
        require(parent.is_file() and
                sha256(parent)==parents[stage].get('sha256'),
                'V4f V4e parent SHA256 missing/changed: '+stage)
    return meta,matrix


def replay_v4e_raw(repo: Path, results: Path, weights: Path,
                   v4e_dir: Path, meta: dict[str,Any],
                   matrix: list[dict[str,str]],
                   manifest: list[dict[str,str]],
                   plan: list[dict[str,str]]) -> None:
    """Read-only replay of V4e ancestry; no new simulator or audit output."""
    v4 = results/PARENT_NAMES['V4']
    v4c = results/PARENT_NAMES['V4C']
    canary = results/'v4d_canary_01'
    temporal = results/'v4db_temporal_direct_02'
    completed = results/'v4dc_resume_01'
    abrupt = results/'v4dd_abrupt_recovery_01'
    v4ba = results/'v4b_six_mechanism_01'
    v4bb = results/'v4b_existing_consumer_reaudit_01/existing_consumer_audit.json'
    v4bc = results/'v4bc_three_01'
    prior=v4dd.verify_v4dc_ancestry(repo,results,weights,v4c,v4,
                                    v4ba,v4bb,v4bc,canary,temporal,
                                    completed,plan,manifest)
    saved=read_json(abrupt/'v4dd_recovery_manifest.json')
    v3a=results/V3A_NAME
    pins=v4dd.input_pins(repo,weights,v4c,canary,temporal,completed,
                         v3a/'validation_manifest.json')
    v4e.strict_release_meta(saved,prior,pins,completed,temporal)
    selected_abrupt=v4dd.selection(manifest,plan)
    v4e.check_v4dd_evidence(abrupt,saved,selected_abrupt,pins,repo,v3a)
    a=read_json(canary/'v4d_canary_manifest.json')
    c=read_json(completed/'v4dc_resume_manifest.json')
    rebuilt=v4e.collect_shakedowns(manifest,[
        ('V4D_A',a,v4da.canary_selection(manifest)),
        ('V4D_C',c,v4dc.selection(manifest,plan)),
        ('V4D_D',saved,selected_abrupt)])
    require(rebuilt==matrix and
            Counter(r['outcome'] for r in rebuilt)==meta.get('shakedown_outcomes'),
            'V4f strict cross-stage raw evidence replay differed from saved V4e')


def safety_output(repo: Path, out: Path, weights: Path,
                  results: Path) -> None:
    require(not out.exists(), 'V4f refuses to overwrite existing output: '+str(out))
    protected=[repo/'fi/config',repo/'fi/scenarios',repo/'fi/tb',
               repo/'fi/pilot400',repo/'rtl',repo/'python',weights,
               repo/'fi/v4/scripts']
    protected.extend(p for p in results.iterdir() if p.is_dir())
    for p in protected:
        # Writing to a parent of protected input is also forbidden.
        require(out!=p and out not in p.parents and p not in out.parents,
                'V4f output overlaps frozen source/evidence: '+str(p))


def write_planning_output(out: Path, selected: list[dict[str,str]],
                          pins: dict[str,str], ancestry: bool,
                          v4e_path: Path|None) -> dict[str,Any]:
    # Output already preflighted; atomically create the directory without
    # truncating any prior data.
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_scenarios.csv',FIELDS,selected)
    manifest={
        'schema':SCHEMA, 'status':STATUS_REPLAY if ancestry else STATUS_PLAN,
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'frozen_manifest_sha256':FROZEN_HASHES['fi/scenarios/pilot400.csv'],
        'frozen_plan_sha256':FROZEN_HASHES['fi/config/pilot400_targets.csv'],
        'selected_count':len(selected),
        'selected_sha256':sha256(out/'selected_scenarios.csv'),
        'preflight_input_sha256':pins,
        'vivado_rtl_source_replay_verified':ancestry,
        'strict_v4e_raw_replay_completed':ancestry,
        'v4e_review_sha256':sha256(v4e_path) if ancestry else None,
        'full400_runner_qualified':False,'full400_authorized':False,
        'full400_executed':False,'xsim_runs_added':0,
        'target_status_modified':False,
        'full400_gate':'BLOCKED_PENDING_FULL400_RUNNER_AND_INDEPENDENT_G0_G7_APPROVAL',
        'scientific_protocol_frozen':False,
    }
    (out/'analysis_protocol_DRAFT.json').write_text(
        json.dumps(PROTOCOL_DRAFT,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    manifest['analysis_protocol_draft_sha256']=sha256(out/'analysis_protocol_DRAFT.json')
    (out/'v4f_preflight.json').write_text(
        json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    (out/'v4f_report.md').write_text('\n'.join([
        '# CNN_PEP V4f Non-Executing Full400 Qualification Preflight','',
        f'- Planned scenarios: **{len(selected)}/400**.',
        f'- V4e raw ancestry replay: **{"PASS" if ancestry else "NOT RUN"}**.',
        '- New FI executions: **0**.',
        '- Runner qualification: **NOT YET COMPLETE**.',
        '- Scientific analysis protocol: **DRAFT; not signed**.',
        '- No target status promotion or original P0/TB change.',
        '- Full400 execution: **BLOCKED**.','',
        'Next: implement the batch execution backend, negative tests,',
        'mid-XSim/orphan recovery policy, independent G0-G7 approval,',
        'and a separate explicit authorization for execution.','',
    ]),encoding='utf-8')
    return manifest


def run(args:argparse.Namespace)->dict[str,Any]:
    # Fail before touching any output for accidental --execute.
    require(not args.execute,
            'V4f phase-1 is non-executing: no Full400 --execute permission')
    require(args.plan_only != args.replay_ancestry,
            'Select exactly one: --plan-only OR --replay-ancestry')
    repo=args.repo.resolve()
    results=resolved(repo,args.results_dir)
    weights=resolved(repo,args.weights_dir)
    out=resolved(repo,args.out_dir)
    v4e_dir=resolved(repo,args.v4e_dir)
    check_frozen(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    frozen=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    selected=select_400(frozen,plan)
    assets=check_assets_and_provenance(repo,weights)
    pins=source_pins(repo,assets,results,v4e_dir/'v4e_release_review.json')
    safety_output(repo,out,weights,results)
    if args.replay_ancestry:
        meta,matrix=verify_v4e_saved(repo,results,v4e_dir)
        replay_v4e_raw(repo,results,weights,v4e_dir,meta,matrix,selected,plan)
    output=write_planning_output(out,selected,pins,args.replay_ancestry,
        v4e_dir/'v4e_release_review.json' if args.replay_ancestry else None)
    print(f'V4F_PLAN_PASS: 400/400 frozen tuples; 20 targets, 10 real samples, '
          f'10 valid pairs/target; V4e raw replay={args.replay_ancestry}; '
          '0 XSim; FULL400 BLOCKED',flush=True)
    print('V4F_OUTPUT: '+str(out),flush=True)
    return output


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--v4e-dir',type=Path,
                    default=Path('fi/results/v4e_release_review_01'))
    ap.add_argument('--out-dir',type=Path,
                    default=Path('fi/results/v4f_plan_preflight_01'))
    modes=ap.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan-only',action='store_true')
    modes.add_argument('--replay-ancestry',action='store_true')
    ap.add_argument('--execute',action='store_true',
                    help='FORBIDDEN in phase-1; always fails closed')
    args=ap.parse_args()
    try:
        run(args)
    except (ValueError,RuntimeError,KeyError,OSError,TypeError) as exc:
        raise SystemExit('V4F_FAIL: '+str(exc)) from exc

if __name__=='__main__':
    main()

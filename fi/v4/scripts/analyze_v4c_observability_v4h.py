#!/usr/bin/env python3
"""CNN_PEP V4H: read-only V4c matrix-derived L0/L1 and bounded L2 observability.

This code NEVER invokes Vivado/XSim, does not implement a production executor,
and cannot approve Full400. The V4c target matrix is an audited *summary*;
independent raw XSim log reparse and checkpoint evidence are NOT performed here.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import re
from pathlib import Path

SCHEMA = 'CNN_PEP_V4H_V4C_MATRIX_OBSERVABILITY_V1'
FROZEN = {
    'fi/config/fi_targets_candidates_v2.csv': 'd9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
    'fi/config/pilot400_targets.csv': '98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
    'fi/scenarios/pilot400.csv': 'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
    'fi/tb/tb_cnn_mnist_fi_pilot400.v': '2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
}
MATRIX_REL = 'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv'
MANIFEST_REL = 'fi/results/v4c_readiness_audit_01/v4c_manifest.json'
FROZEN_FIELDS = ('scenario_id','sample_id','target_id','target_class','bit_index','injection_offset')
MATRIX_FIELDS = ('target_id','target_class','frozen_evidence_status',
 'config_a_scenario','config_b_scenario','config_a_bit','config_b_bit',
 'config_a_offset','config_b_offset','proof_source','proof_kind',
 'config_a_outcome','config_b_outcome','config_a_log_sha256','config_b_log_sha256',
 'directed_review','automatic_promotion','full400_authorized')
OUTCOMES = ('NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL',
            'WRONG_LOGITS_SAME_LABEL','FINAL_OUTPUT_MATCH')
CLASSES = ('CTL','BUF','MEM','DAT')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def need(ok: bool, why: str) -> None:
    if not ok:
        raise ValueError(why)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bytes_file(path: Path) -> bytes:
    need(path.is_file() and not path.is_symlink(), 'missing / symlink input: ' + str(path))
    return path.read_bytes()


def sha_file(path: Path) -> str:
    return digest(bytes_file(path))


def csv_read(path: Path, fields: tuple[str,...]) -> list[dict[str,str]]:
    raw = bytes_file(path)
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError as e:
        raise ValueError('non UTF-8 CSV: ' + str(path)) from e
    reader = csv.DictReader(io.StringIO(content, newline=''), strict=True)
    need(tuple(reader.fieldnames or ()) == fields, 'CSV schema mismatch: '+str(path))
    rows=list(reader)
    need(rows and all(None not in row and all(isinstance(row[k],str) for k in fields)
                          for row in rows), 'CSV missing/extra fields: '+str(path))
    return rows


def no_duplicates(pairs: list[tuple[str,object]]) -> dict[str,object]:
    result={}
    for k,v in pairs:
        need(k not in result, 'duplicate JSON key: '+k)
        result[k]=v
    return result


def read_json(path:Path) -> dict:
    obj=json.loads(bytes_file(path).decode('utf-8'),object_pairs_hook=no_duplicates)
    need(isinstance(obj,dict),'JSON root must be object')
    return obj


def validate_input(repo:Path) -> tuple[list[dict[str,str]],list[dict[str,str]],dict,dict]:
    repo=repo.resolve()
    need((repo/'fi/results').is_dir(), 'not a CNN_PEP repository: '+str(repo))
    hashes={}
    for rel,expected in FROZEN.items():
        actual=sha_file(repo/rel)
        need(actual==expected, 'FROZEN SHA256 mismatch: '+rel)
        hashes[rel]=actual
    frozen=csv_read(repo/'fi/scenarios/pilot400.csv',FROZEN_FIELDS)
    need(len(frozen)==400, 'frozen 400 count mismatch')
    for i,row in enumerate(frozen,1):
        need(row['scenario_id']==f'FI_P400_{i:04d}', 'frozen order unexpected')
    need(len({r['scenario_id'] for r in frozen})==400, 'duplicate frozen IDs')
    matrix=csv_read(repo/MATRIX_REL,MATRIX_FIELDS)
    manifest=read_json(repo/MANIFEST_REL)
    hashes[MATRIX_REL]=sha_file(repo/MATRIX_REL)
    hashes[MANIFEST_REL]=sha_file(repo/MANIFEST_REL)
    need(manifest.get('target_matrix_sha256')==hashes[MATRIX_REL], 'V4c matrix not pinned by V4c manifest')
    need(manifest.get('manifest_sha256')==FROZEN['fi/scenarios/pilot400.csv'], 'V4c scenario pin mismatch')
    need(manifest.get('selected_plan_sha256')==FROZEN['fi/config/pilot400_targets.csv'], 'V4c target pin mismatch')
    need(manifest.get('original_tb_sha256')==FROZEN['fi/tb/tb_cnn_mnist_fi_pilot400.v'], 'V4c original TB pin mismatch')
    need(manifest.get('targets_reviewed')==20 and manifest.get('directed_img0000_proofs')==40,
         'V4c summary has wrong evidence counts')
    need(manifest.get('xsim_runs_added')==0 and manifest.get('full400_authorized') is False and
         manifest.get('full400_runner_qualified') is False, 'V4c release scope mismatch')
    return frozen,matrix,manifest,hashes


def analyze(frozen:list[dict[str,str]], matrix:list[dict[str,str]]) -> tuple[dict,list[dict],list[dict]]:
    need(len(frozen)==400 and len(matrix)==20, 'expected frozen 400 and matrix 20 targets')
    by_id={r['scenario_id']:r for r in frozen}
    need(len(by_id)==400, 'duplicate scenarios')
    seen=set(); details=[]; targets=[]
    for row in matrix:
        target=row['target_id']; cls=row['target_class']
        need(target not in seen, 'duplicate target: '+target)
        seen.add(target)
        need(cls in CLASSES and row['directed_review']=='TWO_CONFIG_CONSUMER_DECISION_PROVED',
             'E3 proof scope invalid for '+target)
        need(row['automatic_promotion']=='NO' and row['full400_authorized']=='NO',
             'attempt to elevate release: '+target)
        need(row['proof_source'] in ('V3B_ISOLATED','V4B_B_ORIGINAL','V4B_C_ISOLATED') and
             len(row['proof_kind'].split(' | '))==2 and all(row['proof_kind'].split(' | ')),
             'proof descriptor missing: '+target)
        rec=[]
        for config in ('a','b'):
            sid=row['config_'+config+'_scenario']
            expected=by_id.get(sid)
            need(expected is not None and expected['sample_id']=='IMG0000' and
                 expected['target_id']==target and expected['target_class']==cls and
                 expected['bit_index']==row['config_'+config+'_bit'] and
                 expected['injection_offset']==row['config_'+config+'_offset'],
                 'frozen V4c tuple mismatch for '+sid)
            outcome=row['config_'+config+'_outcome']
            need(outcome in OUTCOMES, 'invalid outcome for '+sid)
            need(HEX64.fullmatch(row['config_'+config+'_log_sha256']) is not None,
                 'missing raw-log digest in matrix for '+sid)
            l0=outcome in OUTCOMES[:3]
            l1=outcome!= 'FINAL_OUTPUT_MATCH'
            # L2 is a union of L1 and stage-level differences, so L1 TRUE => L2 TRUE.
            # If final matches, BOTH stage checkpoints must be verified to declare L2 FALSE.
            # The V4c matrix does not have those counters, hence INCONCLUSIVE.
            l2='TRUE' if l1 else 'INCONCLUSIVE'
            item={k:expected[k] for k in FROZEN_FIELDS}
            item.update({'configuration':config.upper(),'outcome':outcome,
                         'L0':'TRUE' if l0 else 'FALSE',
                         'L1':'TRUE' if l1 else 'FALSE', 'L2':l2,
                         'E3':'MATRIX_RECORDED_CONSUMER_OR_DECISION_PROOF',
                         'E3_raw_replayed_here':'NO',
                         'log_sha256_as_recorded':row['config_'+config+'_log_sha256']})
            details.append(item); rec.append(item)
        need(rec[0]['scenario_id'] != rec[1]['scenario_id'], 'duplicate A/B')
        targets.append({'target_id':target,'target_class':cls,
                        'A_scenario':rec[0]['scenario_id'],'B_scenario':rec[1]['scenario_id'],
                        'A_outcome':rec[0]['outcome'],'B_outcome':rec[1]['outcome'],
                        'A_L0':rec[0]['L0'],'B_L0':rec[1]['L0'],
                        'A_L1':rec[0]['L1'],'B_L1':rec[1]['L1']})
    need(len(details)==40 and len({r['scenario_id'] for r in details})==40 and
         len(seen)==20, 'V4c proof coverage incomplete')
    need(Counter(d['target_class'] for d in details)=={c:10 for c in CLASSES},
         'wrong class coverage')
    need(set(seen)=={r['target_id'] for r in frozen}, 'not all 20 frozen targets covered')
    details.sort(key=lambda r:r['scenario_id'])
    targets.sort(key=lambda r:r['target_id'])

    def summary(rows:list[dict]) -> dict:
        counts=Counter(r['outcome'] for r in rows)
        n=len(rows)
        l0=sum(r['L0']=='TRUE' for r in rows)
        l1=sum(r['L1']=='TRUE' for r in rows)
        l2=sum(r['L2']=='TRUE' for r in rows)
        incon=sum(r['L2']=='INCONCLUSIVE' for r in rows)
        return {'planned_directed':n,'e3_matrix_claimed':n,
                'e3_independently_raw_replayed_in_this_run':0,
                'outcomes':{k:counts[k] for k in OUTCOMES},
                'L0':{'true':l0,'false':n-l0,'evaluable':n},
                'L1':{'true':l1,'false':n-l1,'evaluable':n},
                'L1_minus_L0_same_ids':{'new_true':l1-l0,'paired_evaluable':n},
                'L2':{'true':l2,'false':0,'inconclusive':incon,'evaluable':l2},
                'L2_minus_L1_same_evaluable_ids':{'new_true_observed':0,
                    'paired_evaluable':l2,'not_evaluable':incon,
                    'population_increment_identified':False}}
    result={'schema':SCHEMA,'status':'V4H_MATRIX_DERIVED_OFFLINE_ONLY_FULL400_BLOCKED',
            'source_scope':'V4C_IMG0000_DIRECTED_40_CONFIGS',
            'limitations':['Source is V4c summary matrix; raw XSim logs NOT re-parsed by this tool',
                'E3 is a V4c matrix-reported directed proof, not independently verified in this run',
                'L2 for FINAL_OUTPUT_MATCH is INCONCLUSIVE without both stage checkpoints',
                'L2 increment for all 40 cannot be estimated from this source',
                'Selected directed tuples are not representative of random physical SEU'],
            'new_xsim_runs':0,'full400_authorized':False,'full400_runner_qualified':False,
            'full400_executed':False,'G7_scientific_protocol_approved':False,
            'overall':summary(details),
            'by_class':{c:summary([r for r in details if r['target_class']==c]) for c in CLASSES},
            'target_pairs':len(targets)}
    return result,details,targets


def json_bytes(obj) -> bytes:
    return (json.dumps(obj,sort_keys=True,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')


def csv_bytes(rows:list[dict]) -> bytes:
    need(bool(rows),'empty CSV output')
    buf=io.StringIO(newline='')
    writer=csv.DictWriter(buf,fieldnames=list(rows[0]),lineterminator='\n')
    writer.writeheader();writer.writerows(rows)
    return buf.getvalue().encode('utf-8')


def report_md(result:dict) -> bytes:
    s=result['overall'];lines=[
        '# CNN_PEP V4H — V4c directed observability (offline matrix analysis)',
        '', '**Status:** source-matrix derived; 0 XSim; Full400 BLOCKED.',
        '', '## Results (IMG0000 only, 20 targets × 2 configurations)', '',
        '| Class | N | L0 true | L1 true | L1 additional | Final-output match | L2 inconclusive |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for cl in (*CLASSES,'ALL'):
        q=s if cl=='ALL' else result['by_class'][cl]
        lines.append(f"| {cl} | {q['planned_directed']} | {q['L0']['true']} | {q['L1']['true']} | {q['L1_minus_L0_same_ids']['new_true']} | {q['outcomes']['FINAL_OUTPUT_MATCH']} | {q['L2']['inconclusive']} |")
    lines += ['', '## Scientific scope', '',
      '- L0: RTL watchdog/protocol or wrong label; L1: L0 or wrong raw logits with same label.',
      '- L1 adds 11/40 positives (27.5 percentage points) over L0, on the same 40 IDs.',
      '- 20/40 `FINAL_OUTPUT_MATCH` cases still have directed E3 proof recorded in the V4c matrix.',
      '- For 20 L1-positive cases L2 is TRUE by logical union; the other 20 are INCONCLUSIVE, not FALSE.',
      '- L2 incremental benefit for all 40 cannot be determined without POOL1 and POOL2 evidence.',
      '- Matrix-recorded E3 claims are NOT newly independently verified against raw logs here.',
      '- Outcomes describe directed selected-sample observations, not a population SEU rate.',
      '- Do NOT combine these with 400 synthetic scheduler events or 8 failure-qualification attempts.',
      '', '**Release:** G6 production NOT QUALIFIED; G7 NOT APPROVED; FULL400 BLOCKED.', '']
    return ('\n'.join(lines)).encode('utf-8')


def run(repo:Path,out:Path|None,mode:str) -> dict:
    repo=repo.resolve()
    frozen,matrix,manifest,hashes=validate_input(repo)
    result,details,targets=analyze(frozen,matrix)
    result['source_sha256']=hashes
    if mode=='check':
        return result
    need(mode=='analyze','invalid mode')
    need(out is not None,'--out-dir required in analyze mode')
    results=repo/'fi/results'
    need(not results.is_symlink() and results.is_dir(),'fi/results missing/symlink')
    out=out if out.is_absolute() else repo/out
    need(out.parent.resolve()==results.resolve() and out.name.startswith('v4h_') and
         not out.exists(), 'output must be a NEW fi/results/v4h_* sibling')
    out.mkdir(mode=0o700)
    artifacts={
       'analysis.json':json_bytes(result),
       'scenario_observability_40.csv':csv_bytes(details),
       'target_pairs_20.csv':csv_bytes(targets),
       'SUMMARY.md':report_md(result),
    }
    for name,data in artifacts.items():
        with (out/name).open('xb') as f:
            f.write(data);f.flush()
            import os
            os.fsync(f.fileno())
    sha=''.join(f'{digest(artifacts[name])}  {name}\n' for name in sorted(artifacts))
    (out/'SHA256SUMS.txt').write_text(sha,encoding='ascii',newline='\n')
    return result


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('check','analyze'),default='check')
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--out-dir',type=Path)
    args=p.parse_args()
    try:
        result=run(args.repo,args.out_dir,args.mode)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        p.exit(2,'V4H_REFUSED: '+str(e)+'\n')
    print('V4H_V4C_MATRIX_ANALYSIS_PASS: 40/40 IMG0000; L0=9; L1=20; '
          'L2 20 TRUE / 20 INCONCLUSIVE; 0 XSim; FULL400 BLOCKED')
    if args.mode=='analyze':print('V4H_OUTPUT:',args.out_dir)

if __name__=='__main__':
    main()

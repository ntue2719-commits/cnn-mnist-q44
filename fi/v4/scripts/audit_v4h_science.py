#!/usr/bin/env python3
"""Read-only V4H scientific-output verifier. Does not start XSim or write evidence."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from collections import Counter

FILES=('SUMMARY.md','analysis.json','scenario_observability_40.csv','target_pairs_20.csv')
FROZEN={
 'fi/config/fi_targets_candidates_v2.csv':'d9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
 'fi/config/pilot400_targets.csv':'98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
 'fi/scenarios/pilot400.csv':'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
 'fi/tb/tb_cnn_mnist_fi_pilot400.v':'2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
}

def reject_duplicate_keys(pairs):
    result={}
    for k,v in pairs:
        check(k not in result,'duplicate JSON key '+k)
        result[k]=v
    return result
OUTCOMES=('NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL','WRONG_LOGITS_SAME_LABEL','FINAL_OUTPUT_MATCH')


def check(condition, message):
    if not condition: raise ValueError(message)


def file_bytes(path):
    check(path.is_file() and not path.is_symlink(),'missing or symlink: '+str(path))
    return path.read_bytes()


def table(path):
    rd=csv.DictReader(io.StringIO(file_bytes(path).decode('utf-8'),newline=''),strict=True)
    rows=list(rd)
    check(len(rows)>0 and all(None not in r for r in rows),'invalid CSV rows')
    return rows


def audit(repo:Path,out:Path) -> dict:
    repo=repo.resolve()
    out=(out if out.is_absolute() else repo/out).resolve()
    check(out.is_dir() and not out.is_symlink(),'missing/symlink audit input')
    check(out.parent==(repo/'fi/results').resolve() and out.name.startswith('v4h_'),
          'not a V4H output sibling')
    lines=file_bytes(out/'SHA256SUMS.txt').decode('ascii').splitlines()
    check(len(lines)==4,'expected four pinned files')
    observed={}
    for line in lines:
        match=re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)',line)
        check(match is not None,'malformed SHA256 inventory')
        h,name=match.groups()
        check(name in FILES and name not in observed,'unrecognized/duplicated inventory name')
        observed[name]=h
    check(set(observed)==set(FILES),'missing inventory entries')
    for name,h in observed.items():
        check(hashlib.sha256(file_bytes(out/name)).hexdigest()==h,'tampered file: '+name)
    report=json.loads(file_bytes(out/'analysis.json').decode('utf-8'),object_pairs_hook=reject_duplicate_keys)
    check(report.get('schema')=='CNN_PEP_V4H_V4C_MATRIX_OBSERVABILITY_V1', 'wrong schema')
    check(report.get('new_xsim_runs')==0 and report.get('full400_authorized') is False and
          report.get('full400_runner_qualified') is False and
          report.get('full400_executed') is False and
          report.get('G7_scientific_protocol_approved') is False,
          'invalid execution/approval claim')
    check(set(report['source_sha256'])=={
        'fi/config/fi_targets_candidates_v2.csv',
        'fi/config/pilot400_targets.csv',
        'fi/scenarios/pilot400.csv',
        'fi/tb/tb_cnn_mnist_fi_pilot400.v',
        'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv',
        'fi/results/v4c_readiness_audit_01/v4c_manifest.json'},
        'input source inventory unexpected')
    for rel,h in FROZEN.items():
        check(report['source_sha256'].get(rel)==h, 'frozen source digest not pinned: '+rel)
    for rel,pin in report['source_sha256'].items():
        source=repo/rel
        check(source.is_file() and not source.is_symlink() and
              hashlib.sha256(source.read_bytes()).hexdigest()==pin,
              'source drift: '+rel)
    # Independently reconstruct the 40 directed outcomes from the pinned V4c matrix.
    # A modified analysis.json and regenerated SHA256SUMS must NOT fool this audit.
    matrix=table(repo/'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv')
    source_manifest=json.loads(file_bytes(repo/'fi/results/v4c_readiness_audit_01/v4c_manifest.json'))
    check(source_manifest.get('target_matrix_sha256')==hashlib.sha256(
          file_bytes(repo/'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv')).hexdigest(),
          'V4c matrix/manifest source pin mismatch')
    check(len(matrix)==20 and all(r['directed_review']=='TWO_CONFIG_CONSUMER_DECISION_PROVED'
             and r['full400_authorized']=='NO' for r in matrix),'V4c matrix scope mismatch')
    matrix_by_sid={}
    for m in matrix:
        for cfg in ('a','b'):
            sid=m['config_'+cfg+'_scenario']
            check(sid not in matrix_by_sid,'duplicate matrix scenario ID')
            matrix_by_sid[sid]={
                'outcome':m['config_'+cfg+'_outcome'],
                'target_id':m['target_id'],'target_class':m['target_class'],
                'bit_index':m['config_'+cfg+'_bit'],
                'injection_offset':m['config_'+cfg+'_offset'],
                'log_sha256_as_recorded':m['config_'+cfg+'_log_sha256']}
    rows=table(out/'scenario_observability_40.csv')
    check(set(matrix_by_sid)=={r['scenario_id'] for r in rows},'analysis differs from V4c scenario IDs')
    pairs=table(out/'target_pairs_20.csv')
    check(len(rows)==40 and len(pairs)==20,'count mismatch')
    frozen={r['scenario_id']:r for r in table(repo/'fi/scenarios/pilot400.csv')}
    check(len(frozen)==400,'frozen not 400')
    counts=Counter(); seen=set(); classes=Counter()
    for row in rows:
        sid=row['scenario_id']; check(sid in frozen and sid not in seen,'unexpected/duplicate ID')
        seen.add(sid)
        known=frozen[sid]
        claimed=matrix_by_sid[sid]
        check(all(row[k]==claimed[k] for k in claimed),
              'V4c matrix outcome/tuple/log-hash mismatch: '+sid)
        check(all(row[k]==known[k] for k in ('sample_id','target_id','target_class',
                   'bit_index','injection_offset')),'tuple mismatch')
        check(row['sample_id']=='IMG0000' and row['outcome'] in OUTCOMES,'scope/outcome mismatch')
        check(row['E3']=='MATRIX_RECORDED_CONSUMER_OR_DECISION_PROOF' and
              row['E3_raw_replayed_here']=='NO','invalid E3 scope')
        outcome=row['outcome']
        expected_l0='TRUE' if outcome in OUTCOMES[:3] else 'FALSE'
        expected_l1='FALSE' if outcome=='FINAL_OUTPUT_MATCH' else 'TRUE'
        expected_l2='TRUE' if expected_l1=='TRUE' else 'INCONCLUSIVE'
        check((row['L0'],row['L1'],row['L2'])==(expected_l0,expected_l1,expected_l2),
              'L0/L1/L2 semantics mismatch')
        classes[row['target_class']]+=1; counts[outcome]+=1
    check(set(classes)=={'CTL','BUF','MEM','DAT'} and all(x==10 for x in classes.values()),
          'class coverage mismatch')
    check(counts==Counter({'FINAL_OUTPUT_MATCH':20,'WRONG_LOGITS_SAME_LABEL':11,
           'WRONG_LABEL':3,'NONCOMPLETION':4,'PROTOCOL_VIOLATION':2}),
          'source outcome snapshot mismatch')
    pair_ids=set()
    for pair in pairs:
        target=pair['target_id']
        check(target not in pair_ids,'duplicate target pair')
        pair_ids.add(target)
        matches=[r for r in rows if r['target_id']==target]
        check(len(matches)==2 and {r['configuration'] for r in matches}=={'A','B'} and
              {r['scenario_id'] for r in matches}=={pair['A_scenario'],pair['B_scenario']},
              'paired comparison corruption')
        for r in matches:
            prefix=r['configuration']+'_'
            check(pair[prefix+'outcome']==r['outcome'] and pair[prefix+'L0']==r['L0'] and
                  pair[prefix+'L1']==r['L1'], 'paired outcome mismatch')
    for klass in ('CTL','BUF','MEM','DAT'):
        grouped=[r for r in rows if r['target_class']==klass]
        row=report['by_class'][klass]
        check(row['planned_directed']==10 and
              row['L0']['true']==sum(x['L0']=='TRUE' for x in grouped) and
              row['L1']['true']==sum(x['L1']=='TRUE' for x in grouped) and
              row['L2']['inconclusive']==sum(x['L2']=='INCONCLUSIVE' for x in grouped) and
              row['L1_minus_L0_same_ids']['new_true']==
                  row['L1']['true']-row['L0']['true'] and
              row['outcomes']=={o:sum(x['outcome']==o for x in grouped) for o in OUTCOMES},
              'class summary drift: '+klass)
    check(report.get('target_pairs')==20,'target-pair count drift')
    overall=report['overall']
    check(overall['L0']['true']==9 and overall['L1']['true']==20 and
          overall['L2']['true']==20 and overall['L2']['inconclusive']==20 and
          overall['L1_minus_L0_same_ids']['new_true']==11 and
          overall['L2_minus_L1_same_evaluable_ids']['population_increment_identified'] is False,
          'summary counters incorrect')
    return {'status':'V4H_READ_ONLY_AUDIT_PASS','scenario_rows':40,'target_pairs':20,
            'new_xsim_runs':0,'full400_authorized':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--analysis-dir',type=Path,required=True)
    args=p.parse_args()
    try: r=audit(args.repo,args.analysis_dir)
    except (ValueError,OSError,KeyError,TypeError,UnicodeError,json.JSONDecodeError) as e:
        p.exit(2,'V4H_AUDIT_REFUSED: '+str(e)+'\n')
    print('V4H_SCIENCE_AUDIT_PASS: 40 IMG0000 tuples; SHA256 and raw matrix pins; '
          '0 XSim; FULL400 BLOCKED')

if __name__=='__main__': main()

#!/usr/bin/env python3
"""Read-only G7 scientific evidence cross-check for CNN_PEP V4M/V4N.

No Vivado, XSim, release approval, or external commands.  The check reports
historic IMG0000 evidence consistency; it NEVER qualifies G6 or approves G7.
"""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

PREFIX = 'CNN_PEP_AI_READY_2026-11-04/'
CORE_PATH = 'evidence/partial_v4f4a_protocol_prepare/protocol_core.json'
CORE_PIN = '30a5ea2e66bda0d38e38d0a45635da5abe5ddbcf846672ef9d2e20cf546cf733'
FROZEN = {
    'fi/config/fi_targets_candidates_v2.csv':'d9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
    'fi/config/pilot400_targets.csv':'98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
    'fi/scenarios/pilot400.csv':'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
    'fi/tb/tb_cnn_mnist_fi_pilot400.v':'2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
}
ONLY = {'FI_P400_0101', 'FI_P400_0102', 'FI_P400_0301', 'FI_P400_0302'}
OUTCOMES = {'NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL','WRONG_LOGITS_SAME_LABEL','FINAL_OUTPUT_MATCH'}

class Refused(ValueError):
    pass

def require(expr, detail):
    if not expr:
        raise Refused(detail)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def json_obj(raw):
    def unique_pairs(pairs):
        d={}
        for k,v in pairs:
            require(k not in d, 'Duplicate JSON key: '+k)
            d[k]=v
        return d
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)

def csv_rows(raw):
    text=raw.decode('utf-8-sig', errors='strict')
    return list(csv.DictReader(io.StringIO(text)))

def safe_zip(path):
    z=zipfile.ZipFile(path)
    names=z.namelist()
    require(len(names)==len(set(names)), 'Duplicate ZIP members')
    require(all(not x.startswith('/') and '\\' not in x and '..' not in x.split('/') for x in names), 'Unsafe ZIP member')
    return z

def source(z, path):
    return z.read(PREFIX+path)

def check_sums(z, subdir):
    text=z.read(subdir+'SHA256SUMS.txt').decode('ascii')
    seen=set()
    for line in text.splitlines():
        m=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9_.-]+)', line)
        require(m is not None, 'Invalid checksum manifest line')
        pin,name=m.groups()
        require(name not in seen, 'Duplicate checksum manifest entry')
        seen.add(name)
        require(sha(z.read(subdir+name)) == pin, 'Checksum mismatch '+name)
    require(seen, 'No checksum entries')
    return len(seen)

def read_hex(raw, expected):
    vals=[int(x,16) for x in raw.decode('ascii').split()]
    require(len(vals)==expected and all(0<=x<256 for x in vals), 'Golden HEX malformed')
    return vals

def pool1(vals):
    # Channel-major (4 x 26 x 26) ReLU1, 2x2 stride2, 4 x 13 x 13.
    dst=[]
    for c in range(4):
        for y in range(13):
            for x in range(13):
                pos = c*676 + y*2*26 + x*2
                dst.append(max(vals[j] for j in (pos,pos+1,pos+26,pos+27)))
    return dst

def stage(words, mismatch, expected, channels):
    require(words>=0 and mismatch>=0 and words<=expected and mismatch<=words*channels, 'Bad stage counters')
    if mismatch: return 'MISMATCH'
    if words==expected: return 'MATCH'
    if words==0: return 'NOT_OBSERVED'
    return 'INCONCLUSIVE'

def levels(outcome, p1, p2):
    require(outcome in OUTCOMES, 'Invalid outcome')
    l0='TRUE' if outcome in {'NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL'} else 'FALSE'
    l1='TRUE' if l0=='TRUE' or outcome=='WRONG_LOGITS_SAME_LABEL' else 'FALSE'
    l2='TRUE' if l1=='TRUE' or p1=='MISMATCH' or p2=='MISMATCH' else ('FALSE' if p1==p2=='MATCH' else 'INCONCLUSIVE')
    return l0,l1,l2

def validate(log, row):
    lines=log.decode('utf-8').splitlines()
    applied=[x for x in lines if x.startswith('FI_APPLIED ')]
    results=[x for x in lines if x.startswith('FI_RESULT_V2,')]
    require(len(applied)==len(results)==1, 'Missing/duplicate FI markers '+row['scenario_id'])
    line=results[0].split(',')
    require(len(line)==27, 'Incorrect FI_RESULT_V2 arity')
    for idx,key in ((1,'scenario_id'),(2,'sample_id'),(3,'target_id'),(4,'target_class')):
        require(line[idx]==row[key], 'Raw tuple mismatch '+key)
    for idx,key in ((5,'bit_index'),(6,'injection_offset')):
        require(int(line[idx])==int(row[key]), 'Raw bit/offset mismatch')
    require(int(line[7])==int(line[6])+3 and line[7]==line[8], 'Requested cycle mismatch')
    require(line[9]==line[10]=='1', 'Missing FI event/readback')
    require(line[26]==row['outcome'], 'Raw outcome disagreement')
    require('scenario='+row['scenario_id'] in applied[0], 'Incorrect applied marker scenario')
    require('target='+row['target_id'] in applied[0], 'Incorrect applied marker target')
    require('OUTCOME='+line[26]+' FIRST_BAD_STAGE='+line[25] in lines, 'Missing outcome confirmation')
    p1w,p2w,p1m,p2m=int(line[15]),int(line[16]),int(line[21]),int(line[22])
    require((p1w,p2w,p1m,p2m)==tuple(int(row[k]) for k in ('pool1_words','pool2_words','pool1_mismatch','pool2_mismatch')),
            'Raw stage counter disagreement')
    require(int(line[23])==int(row['logits_mismatch']), 'Raw logit counter disagreement')
    require(stage(p1w,p1m,169,4)==row['pool1_status'] and stage(p2w,p2m,25,8)==row['pool2_status'], 'Stage status disagrees')
    require(levels(row['outcome'],row['pool1_status'],row['pool2_status'])==tuple(row[k] for k in ('l0','l1','l2')),
            'Observability labels disagree')

def review(handoff, v4m, v4n):
    hz,mz,nz=safe_zip(handoff),safe_zip(v4m),safe_zip(v4n)
    with hz,mz,nz:
        require(sha(source(hz,CORE_PATH))==CORE_PIN, 'G7 protocol core SHA differs')
        require(all(sha(source(hz,p))==pin for p,pin in FROZEN.items()), 'Frozen SHA differs')
        mcount=check_sums(mz,'v4m_g7_l2_img0000_01/')
        ncount=check_sums(nz,'v4n_historical_witness_01/')
        check_sums(mz,'v4m_g7_l2_audit_01/')
        check_sums(nz,'v4n_historical_witness_audit_01/')
        matrix=csv_rows(mz.read('v4m_g7_l2_img0000_01/v4m_g7_l2_40.csv'))
        frozen={r['scenario_id']:r for r in csv_rows(source(hz,'fi/scenarios/pilot400.csv'))}
        require(len(frozen)==400 and len(matrix)==40, 'Wrong scenario count')
        ids=set()
        counts={c:collections.Counter() for c in ['CTL','BUF','MEM','DAT']}
        for row in matrix:
            sid=row['scenario_id']
            require(sid in frozen and sid not in ids, 'Duplicate/unknown scenario')
            ids.add(sid)
            require(row['sample_id']=='IMG0000', 'Non-IMG0000 row')
            require(all(row[k]==frozen[sid][k] for k in ('sample_id','target_id','target_class','bit_index','injection_offset')),
                    'Frozen tuple mismatch')
            raw=source(hz,row['raw_log_path'])
            require(sha(raw)==row['raw_log_sha256'], 'Raw historical log SHA disagreement')
            validate(raw,row)
            counts[row['target_class']]['count']+=1
            for level in ('l0','l1','l2'):
                counts[row['target_class']][level+'_'+row[level]]+=1
        require(all(counts[c]['count']==10 for c in counts), 'Class denominator mismatch')
        inc1=[r['scenario_id'] for r in matrix if r['l1']=='TRUE' and r['l0']=='FALSE']
        inc2=[r['scenario_id'] for r in matrix if r['l2']=='TRUE' and r['l1']=='FALSE']
        require(len(inc1)==11 and len(inc2)==4 and set(inc2)==ONLY,'Observability increments changed')
        require(sum(r['l0']=='TRUE' for r in matrix)==9 and sum(r['l1']=='TRUE' for r in matrix)==20 and sum(r['l2']=='TRUE' for r in matrix)==24, 'Wrong L0/L1/L2 counts')
        require(sum(r['l2']=='FALSE' for r in matrix)==16 and all(r['l2']!='INCONCLUSIVE' for r in matrix), 'Incorrect L2 negatives')
        expected_false={r['scenario_id'] for r in matrix if r['l2']=='FALSE'}
        frows=csv_rows(nz.read('v4n_historical_witness_01/v4n_sixteen_l2_false_complete.csv'))
        require(len(frows)==16 and {r['scenario_id'] for r in frows}==expected_false, 'L2 false list differs')
        byid={r['scenario_id']:r for r in matrix}
        for r in frows:
            x=byid[r['scenario_id']]
            require(r['l1']==r['l2']=='FALSE' and r['pool1_words']=='169' and r['pool2_words']=='25', 'Incomplete L2 negative listing')
            require(x['pool1_status']==x['pool2_status']=='MATCH', 'L2 false with incomplete stages')
        witnesses=csv_rows(nz.read('v4n_historical_witness_01/v4n_four_l2_witnesses.csv'))
        require(len(witnesses)==4 and {r['scenario_id'] for r in witnesses}==ONLY,'Witness IDs disagree')
        relu=read_hex(source(hz,'data/golden_relu1.hex'),2704)
        golden=read_hex(source(hz,'fi/pilot400/samples/IMG0000/golden_pool1.hex'),676)
        computed=pool1(relu)
        require(computed==golden, 'Independent golden Pool1 recomputation mismatch')
        for w in witnesses:
            row=byid[w['scenario_id']]
            require(w['raw_log_sha256']==row['raw_log_sha256'] and w['output_outcome']=='FINAL_OUTPUT_MATCH', 'Witness/raw disagreement')
            require((int(w['counter_pool1_words']),int(w['counter_pool1_mismatches']),int(w['counter_pool2_words']),int(w['counter_pool2_mismatches']))==(169,1,25,0), 'Witness counters disagree')
            require(row['pool1_status']=='MISMATCH' and row['pool2_status']=='MATCH', 'Witness level evidence mismatch')
            if w['target_id']=='BUF_0007':
                require(w['golden_pool1_index']=='337' and w['golden_pool1_before']=='0' and w['observed_corrupted_byte']=='1' and golden[337]==0, 'BUF witness values invalid')
                require('TRACE_WITNESS' in w['claim_level'], 'BUF claim changed')
            elif w['target_id']=='DAT_0002':
                i=int(w['pool1_index'])
                require(i in (78,119) and computed[i]==0 and w['pool1_before']=='0' and w['pool1_predicted_after']=='1', 'DAT predicted Pool1 witness invalid')
                require('NOT_DIRECT_OUTPUT_SAMPLE' in w['claim_level'], 'DAT claim overstates sample')
            else: raise Refused('Unexpected witness target')
        ms=json_obj(mz.read('v4m_g7_l2_img0000_01/v4m_g7_l2_summary.json'))
        ns=json_obj(nz.read('v4n_historical_witness_01/v4n_golden_trace_summary.json'))
        for data in (ms,ns):
            require(data['full400_authorized'] is False and data['g6_production_qualified'] is False and data['g7_scientific_approved'] is False, 'Forged approval')
        require(ms['l0']['TRUE']==9 and ms['l1']['TRUE']==20 and ms['l2']['TRUE']==24, 'V4M summary differs')
        require(ns['l0_positive']==9 and ns['l1_positive']==20 and ns['l2_positive']==24 and ns['golden_pool1_compared_bytes']==676, 'V4N summary differs')
        require(set(ns['l2_only_scenarios'])==ONLY and set(ns['l2_false_complete_scenarios'])==expected_false, 'V4N IDs differ')
        return {
          'schema':'CNN_PEP_V4O_G7_CROSS_EVIDENCE_REVIEW_CANDIDATE_V1',
          'status':'CANDIDATE_REVIEW_NOT_G7_APPROVAL',
          'full400_authorized':False, 'g6_production_qualified':False, 'g7_scientific_approved':False,
          'scope':'40 directed IMG0000 tuples, historical SHA-pinned raw XSim logs',
          'evidence_zip_sha256':{'handoff':sha(Path(handoff).read_bytes()),'v4m':sha(Path(v4m).read_bytes()),'v4n':sha(Path(v4n).read_bytes())},
          'protocol_core_sha256':CORE_PIN, 'frozen_hashes_checked':4,
          'v4m_artifacts_checked':mcount,'v4n_artifacts_checked':ncount,
          'raw_logs_independently_rehashed':40,'scenario_rows':40,'golden_pool1_bytes_recomputed':676,
          'observability':{'L0':{'TRUE':9,'FALSE':31,'INCONCLUSIVE':0},'L1':{'TRUE':20,'FALSE':20,'INCONCLUSIVE':0},'L2':{'TRUE':24,'FALSE':16,'INCONCLUSIVE':0}},
          'same_scenario_increment':{'L1_over_L0':11,'L2_over_L1':4,'denominator':40},
          'l2_only_scenario_ids':sorted(ONLY), 'l2_false_complete':16,
          'by_class':{k:dict(v) for k,v in counts.items()},
          'interpretation_approval_required':[
              'Confirm POOL1/POOL2-only checkpoint scope against older core text',
              'Approve stage counter state mapping (including partial/zero words)',
              'Approve paired denominator and E3-not-assessed category semantics',
              'Confirm external timeout is never design NONCOMPLETION',
              'Determine whether versioned v2 core + fresh manual approvals are needed'
          ],
          'limitations':[
              'No independent exhaustive faulty RTL POOL1/POOL2 per-byte dump',
              'DAT witnesses use predicted Pool1 value, not directly logged output sample',
              'Same historical raw E3 provenance; no new consumer monitor experiment',
              '40 selected directed configurations on one image, not random SEU samples',
              'G6 production executor and final G7 manual approval absent'
          ],
          'vivado_builds_new':0,'xsim_runs_new':0
        }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--handoff',required=True)
    ap.add_argument('--v4m',required=True)
    ap.add_argument('--v4n',required=True)
    ap.add_argument('--mode',required=True,choices=('check','review'))
    ap.add_argument('--out-dir')
    args=ap.parse_args()
    if args.mode=='check': require(not args.out_dir, 'check mode must not write')
    else: require(bool(args.out_dir), 'review requires output directory')
    try:
        out=review(args.handoff,args.v4m,args.v4n)
        if args.mode=='review':
            root=Path(args.out_dir).resolve()
            require(not root.exists(), 'Refusing existing output')
            require(root.parent.is_dir(), 'Parent output directory must already exist')
            root.mkdir()
            content=(json.dumps(out,indent=2,sort_keys=True,ensure_ascii=False)+'\n').encode('utf-8')
            (root/'V4O_G7_REVIEW_CANDIDATE.json').write_bytes(content)
            (root/'SHA256SUMS.txt').write_text(sha(content)+'  V4O_G7_REVIEW_CANDIDATE.json\n',encoding='ascii',newline='\n')
        print('V4O_G7_CROSS_EVIDENCE_REVIEW_PASS: 40/40 original raw logs; 4/4 witnesses; 16/16 complete L2 negatives; L0=9 L1=20 L2=24; G7 REVIEW PENDING; 0 XSim; FULL400 BLOCKED')
    except (Refused,ValueError,KeyError,IndexError,UnicodeError,zipfile.BadZipFile,FileNotFoundError) as e:
        raise SystemExit('V4O_REVIEW_REFUSED: '+str(e))

if __name__=='__main__':main()

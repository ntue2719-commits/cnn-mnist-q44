#!/usr/bin/env python3
"""CNN_PEP V4N: independent golden-pool computation and historical V3b witness checks.

No Vivado/XSim, no RTL editing, no G7 approval, no Full400 authorization.
This module does NOT import or call the V4M analyzer/parser.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

PREFIX = 'CNN_PEP_AI_READY_2026-11-04/'
PIN = {
 'fi/scenarios/pilot400.csv':'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
 'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv':'36633692e0edee31f5de87682a49499bf192e1b65648737aa84e8c9178f19d36',
 'fi/tb/tb_cnn_mnist_fi_pilot400.v':'2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
 'fi/pilot400/samples/IMG0000/golden_pool1.hex':'7ca827bd5582ebddc8ea64e6358f4b03d1c4b393c3613773201d8825012bdceb',
 'fi/pilot400/samples/IMG0000/golden_pool2.hex':'65cd941d1e44bcc522f5c36bbc7868f28cbecd6b4f9466750fe3e421ea17751d',
 'data/golden_relu1.hex':'41f66b73f81231d66588a6623b9cc1f19d913b0be133dd61c97fbf4dfa2d8278',
 'data/golden_pool1.hex':'7ca827bd5582ebddc8ea64e6358f4b03d1c4b393c3613773201d8825012bdceb',
 'fi/pilot400/samples/IMG0000/prediction.hex':'56a468fa75d7e429e99b3f560bbdcef20821e79f63a70a7ade6660fac6617682',
}
CASE_IDS = ('FI_P400_0101','FI_P400_0102','FI_P400_0301','FI_P400_0302')
HARMFUL = {'NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL'}
OUTCOMES = HARMFUL | {'FINAL_OUTPUT_MATCH','WRONG_LOGITS_SAME_LABEL'}
MAX_BYTES = 2_000_000


def ensure(ok, msg):
    if not ok: raise ValueError(msg)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def json_unique(raw):
    def hook(pairs):
        obj={}
        for key,value in pairs:
            ensure(key not in obj, 'Duplicate JSON key '+key)
            obj[key]=value
        return obj
    return json.loads(raw.decode('utf-8'), object_pairs_hook=hook)

def bytes_json(obj):
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True)+'\n').encode('utf8')

def hex8_array(data, count, label):
    t=data.decode('ascii')
    vals=t.split()
    ensure(len(vals)==count,f'{label}: expected {count} octets, got {len(vals)}')
    ensure(all(re.fullmatch(r'[0-9A-Fa-f]{2}',s) for s in vals),f'{label}: invalid octet')
    return [int(s,16) for s in vals]

def spatial_index(ch, y, x, side):
    ensure(ch>=0 and 0<=y<side and 0<=x<side,'Bad spatial index')
    return ch*side*side+y*side+x

def maxpool2x2(channel_major, channels=4, in_side=26):
    ensure(in_side%2==0 and len(channel_major)==channels*in_side*in_side,'Bad pool input length')
    result=[]
    for c in range(channels):
        for oy in range(in_side//2):
            for ox in range(in_side//2):
                result.append(max(channel_major[spatial_index(c,2*oy+dy,2*ox+dx,in_side)]
                                  for dy in range(2) for dx in range(2)))
    return result

def injected_pool_witness(relu, x, y, bit=0):
    ensure(0<=x<26 and 0<=y<26 and bit==0, 'DAT witness coordinates/bit invalid')
    idx=spatial_index(0,y,x,26)
    actual=list(relu)
    actual[idx] ^= 1
    oy,ox=y//2,x//2
    cell=spatial_index(0,oy,ox,13)
    baseline=maxpool2x2(relu)[cell]
    altered=maxpool2x2(actual)[cell]
    return {'input_xy':[x,y], 'input_channel':0, 'relu1_index':idx,
            'relu1_before':relu[idx], 'relu1_after':actual[idx],
            'pool1_index':cell, 'pool1_before':baseline, 'pool1_predicted_after':altered,
            'window_before':[relu[spatial_index(0, yy, xx, 26)]
                             for yy in (2*oy,2*oy+1) for xx in (2*ox,2*ox+1)]}

class Source:
    def __init__(self, repo=None, archive=None):
        ensure((repo is None)!=(archive is None),'Choose exactly one source')
        self.z=None
        if archive is not None:
            self.z=zipfile.ZipFile(archive)
            names=self.z.namelist()
            ensure(len(names)==len(set(names)), 'Duplicate ZIP member names')
            ensure(PREFIX+'fi/scenarios/pilot400.csv' in names,'Wrong handoff ZIP prefix')
            for item in names:
                name=item[len(PREFIX):] if item.startswith(PREFIX) else item
                ensure(not name.startswith('/') and '..' not in name.split('/'), 'Unsafe ZIP member')
        else:
            self.root=Path(repo).resolve(strict=True)
            ensure((self.root/'fi/scenarios/pilot400.csv').is_file(),'Missing repo scenario roster')
    def read(self,rel,limit=MAX_BYTES):
        ensure(not rel.startswith('/') and '..' not in Path(rel).parts,'Unsafe source path')
        if self.z:
            info=self.z.getinfo(PREFIX+rel)
            ensure(not info.is_dir() and info.file_size <=limit,'ZIP input size/directory invalid')
            ensure(((info.external_attr >> 16) & 0o170000)!=0o120000,'ZIP symlink denied')
            return self.z.read(info)
        p=self.root/rel
        for q in [p,*p.parents]:
            if q==self.root:break
            ensure(not q.is_symlink(),'Symlink denied: '+rel)
        path=p.resolve(strict=True)
        ensure(path.is_relative_to(self.root) and path.is_file() and path.stat().st_size<=limit, 'Unsafe input file: '+rel)
        return path.read_bytes()
    def matching_log_data(self):
        pat=re.compile(r'fi/results/.+/(?:logs|runs)/FI_P400_\d{4}\.log\Z')
        if self.z:
            members=sorted((f for f in self.z.infolist() if f.filename.startswith(PREFIX)),key=lambda x:x.filename)
            for f in members:
                rel=f.filename[len(PREFIX):]
                if pat.fullmatch(rel) and f.file_size<=MAX_BYTES:
                    yield rel,self.read(rel)
        else:
            for path in sorted((self.root/'fi/results').rglob('FI_P400_*.log')):
                rel=path.relative_to(self.root).as_posix()
                if pat.fullmatch(rel):yield rel,self.read(rel)
    def close(self):
        if self.z:self.z.close()

def read_csv(raw):
    t=raw.decode('utf8')
    rows=list(csv.DictReader(io.StringIO(t)))
    ensure(rows and all(None not in r for r in rows),'Invalid CSV')
    return rows

def extract_one(lines, tag):
    rows=[line for line in lines if line.startswith(tag+' ')]
    ensure(len(rows)==1, f'Expected one marker {tag}; found {len(rows)}')
    return kv_marker(rows[0],tag)

def kv_marker(line,tag):
    ensure(line.startswith(tag+' '), 'Bad marker name')
    fields={}
    for token in line[len(tag)+1:].split(' '):
        ensure(re.fullmatch(r'[A-Za-z0-9_]+=[^\s=]+',token) is not None,'Bad marker token '+repr(token))
        k,v=token.split('=',1)
        ensure(k not in fields,'Duplicate marker key '+k)
        fields[k]=v
    return fields

def integer(d, key, minval=None):
    ensure(key in d and re.fullmatch(r'-?\d+',d[key]),'Missing or invalid integer '+key)
    v=int(d[key]);ensure(minval is None or v>=minval,'Integer below range '+key)
    return v

def verify_result(raw, tuple_row, expected_outcome, prediction):
    lines=raw.decode('utf8').splitlines()
    applied=extract_one(lines,'FI_APPLIED')
    fields=[line.split(',') for line in lines if line.startswith('FI_RESULT_V2,')]
    ensure(len(fields)==1 and len(fields[0])==27,'FI_RESULT_V2 must have exactly 27 fields')
    f=fields[0]
    sid=tuple_row['scenario_id']
    ensure((f[1],f[2],f[3],f[4])==(sid,tuple_row['sample_id'],tuple_row['target_id'],tuple_row['target_class']),'Result frozen tuple mismatch')
    ensure((applied.get('scenario'),applied.get('target'),applied.get('class'))==(sid,tuple_row['target_id'],tuple_row['target_class']),'FI_APPLIED frozen tuple mismatch')
    ensure(all(re.fullmatch(r'-?\d+',v) for v in f[5:25]),'Result numeric field bad')
    vals=list(map(int,f[5:25]))
    bit,offset,requested,actual,events,verified=vals[:6]
    ensure(bit==int(tuple_row['bit_index'])==integer(applied,'bit'),'FI bit mismatch')
    ensure(offset==int(tuple_row['injection_offset']) and requested==actual==offset+3==integer(applied,'requested'),'FI offset/cycle mismatch')
    ensure(events==verified==1 and {applied.get('old'),applied.get('new')}=={'0','1'},'FI one-bit mechanism not valid')
    outcome=f[26]
    ensure(outcome==expected_outcome and outcome in OUTCOMES,'Outcome mismatch')
    ensure(f[25] in ('NONE','POOL1','POOL2','FC','ARGMAX','PROTOCOL','TIMEOUT'),'Bad stage')
    ensure('OUTCOME='+outcome+' FIRST_BAD_STAGE='+f[25] in lines,'Outcome marker absent')
    w1,w2,m1,m2,lm,done,protocol = int(f[15]),int(f[16]),int(f[21]),int(f[22]),int(f[23]),int(f[19]),int(f[20])
    ensure(0<=w1<=169 and 0<=w2<=25 and 0<=m1<=4*w1 and 0<=m2<=8*w2,'Stage counter impossible')
    if outcome=='FINAL_OUTPUT_MATCH':
        ensure(w1==169 and w2==25 and m1>=0 and m2>=0 and lm==0,'Final-match stage/logits invalid')
        ensure(done==protocol==int(f[17])==int(f[18])==1 and int(f[24])==prediction,'Final match control/prediction invalid')
    if outcome in ('WRONG_LOGITS_SAME_LABEL','WRONG_LABEL'):
        ensure(w1==169 and w2==25 and done==protocol==1,'Numeric outcome with missing checkpoint')
    if outcome=='WRONG_LOGITS_SAME_LABEL':ensure(lm>0,'Wrong logits marker inconsistent')
    if outcome=='NONCOMPLETION':ensure(done==0,'Incomplete outcome has done marker')
    if outcome=='PROTOCOL_VIOLATION':ensure(protocol==0,'Protocol violation without evidence')
    l0=outcome in HARMFUL
    l1=outcome!='FINAL_OUTPUT_MATCH'
    l2=l1 or m1>0 or m2>0
    if not l2:ensure(w1==169 and w2==25,'L2-negative case has incomplete checkpoints')
    return {'scenario_id':sid,'target_id':f[3], 'target_class':f[4],'outcome':outcome,
            'first_bad_stage':f[25], 'pool1_words':w1,'pool1_mismatches':m1,'pool2_words':w2,'pool2_mismatches':m2,
            'logits_mismatches':lm,'l0':l0,'l1':l1,'l2':l2}

def assert_field(d,key,expected,label):
    ensure(d.get(key)==str(expected), f'{label} {key} mismatch: {d.get(key)} != {expected}')

def common_trace(trace, sid, target):
    for tag,d in trace.items():
        assert_field(d,'scenario',sid,tag);assert_field(d,'target',target,tag)

def buf_witness(lines,sid,golden_p1):
    prefix='FI_V3B_BUF0007_'
    tags=('WRITE','INJECT','DRAIN','MEM_WRITE','MEM_READ','CAPTURE','WINDOW','CALC_CONSUME','SUMMARY')
    e={tag:extract_one(lines,prefix+tag) for tag in tags}
    common_trace(e,sid,'BUF_0007')
    before=integer(e['WRITE'],'byte');inj=e['INJECT']
    ensure(before==0,'BUF write value not zero')
    assert_field(inj,'bit',0,'BUF INJECT');assert_field(inj,'before','00','BUF INJECT');assert_field(inj,'after','01','BUF INJECT')
    word=int(e['DRAIN']['word'],16)
    ensure(((word>>8)&255)==1,'BUF packed word lane1 not corrupted')
    for tag in ('MEM_WRITE','MEM_READ'):
        assert_field(e[tag],'word',e['DRAIN']['word'],tag)
    for tag,key in (('CAPTURE','byte337'),('WINDOW','byte'),('CALC_CONSUME','byte')):
        assert_field(e[tag],key,'01',tag)
    assert_field(e['SUMMARY'],'original','00','SUMMARY');assert_field(e['SUMMARY'],'corrupt','01','SUMMARY')
    assert_field(e['SUMMARY'],'overwrites',0,'SUMMARY')
    for k in ('write','inject','drain','memwrite','memread','capture','window','consume'):
        assert_field(e['SUMMARY'],k,integer(e[{'write':'WRITE','inject':'INJECT','drain':'DRAIN','memwrite':'MEM_WRITE','memread':'MEM_READ','capture':'CAPTURE','window':'WINDOW','consume':'CALC_CONSUME'}[k]],'cycle'), 'SUMMARY')
    times=[integer(e[t],'cycle') for t in tags[:-1]]
    ensure(times==sorted(times) and len(set(times))==len(times),'BUF timeline not strictly increasing')
    ensure(golden_p1[337]==0,'BUF golden canonical byte337 nonzero')
    return {'witness_kind':'BUF_REORDER_VALUE_CHAIN', 'golden_pool1_index':337,
            'golden_pool1_before':0, 'observed_corrupted_byte':1,
            'expected_local_change':1, 'consumer_cycle':times[-1],
            'claim_level':'TRACE_WITNESS_PLUS_TB_STAGE_COUNTER_NOT_FULL_WORD_DUMP'}

def dat_witness(lines,sid,relu,p1):
    prefix='FI_V3B_DAT0002_'
    tags=('PRODUCE','INJECT','ACCEPT','STORE','WINDOW','POOL_CONSUME','SUMMARY')
    e={tag:extract_one(lines,prefix+tag) for tag in tags}
    common_trace(e,sid,'DAT_0002')
    base=int(e['INJECT']['before'],16);corrupt=int(e['INJECT']['after'],16)
    ensure(corrupt==base^1 and base&255==0 and corrupt&255==1,'DAT XOR/byte mismatch')
    assert_field(e['INJECT'],'bit',0,'DAT INJECT')
    for tag in ('ACCEPT','STORE','WINDOW','POOL_CONSUME'):
        assert_field(e[tag],'word',e['INJECT']['after'],tag)
    x=integer(e['ACCEPT'],'x');y=integer(e['ACCEPT'],'y')
    assert_field(e['STORE'],'x',x,'STORE')
    slot=2*(y%2)+(x%2)
    for tag in ('WINDOW','POOL_CONSUME'):assert_field(e[tag],'slot',slot,tag)
    assert_field(e['WINDOW'],'endx',x|1,'WINDOW');assert_field(e['WINDOW'],'endy',y|1,'WINDOW')
    t=[integer(e[tag],'cycle') for tag in tags[:-1]]
    ensure(t[0]<t[1]==t[2]==t[3]<=t[4]<t[5],'DAT producer-consumer cycle order bad')
    assert_field(e['SUMMARY'],'before',e['INJECT']['before'],'SUMMARY')
    assert_field(e['SUMMARY'],'after',e['INJECT']['after'],'SUMMARY')
    assert_field(e['SUMMARY'],'overwrites',0,'SUMMARY')
    for tag,key in (('INJECT','inject'),('ACCEPT','accept'),('STORE','store'),('WINDOW','window'),('POOL_CONSUME','consume')):
        assert_field(e['SUMMARY'],key,integer(e[tag],'cycle'),'SUMMARY')
    assert_field(e['SUMMARY'],'x',x,'SUMMARY');assert_field(e['SUMMARY'],'y',y,'SUMMARY')
    assert_field(e['SUMMARY'],'slot',slot,'SUMMARY')
    calc=injected_pool_witness(relu,x,y)
    ensure(calc['relu1_before']==0 and calc['relu1_after']==1,'DAT golden ReLU1 mismatch')
    ensure(calc['pool1_before']==p1[calc['pool1_index']]==0 and calc['pool1_predicted_after']==1, 'DAT predicted max-pool value mismatch')
    ensure(calc['window_before']==[0,0,0,0], 'DAT control window not all zero')
    calc.update({'witness_kind':'DAT_INPUT_CONSUMPTION_PLUS_INDEPENDENT_MAXPOOL_PREDICTION',
                 'consumer_cycle':t[5],
                 'claim_level':'PREDICTED_POOL1_OUTPUT_PLUS_TB_STAGE_COUNTER_NOT_DIRECT_OUTPUT_SAMPLE'})
    return calc

def comparator_region(text):
    start='if (dut.conv1_out_valid) begin';end='if (dut.fc_valid) begin'
    ensure(start in text and end in text and text.index(end)>text.index(start),'TB stage comparator missing')
    return text[text.index(start):text.index(end,text.index(start))]

def analyze(src):
    for path,sha in PIN.items():
        ensure(digest(src.read(path))==sha,'Pinned source drift: '+path)
    relu=hex8_array(src.read('data/golden_relu1.hex'),2704,'ReLU1')
    p1=hex8_array(src.read('fi/pilot400/samples/IMG0000/golden_pool1.hex'),676,'POOL1')
    p2=hex8_array(src.read('fi/pilot400/samples/IMG0000/golden_pool2.hex'),200,'POOL2')
    ensure(maxpool2x2(relu)==p1,'Golden maxpool2x2 fails full 676-byte recomputation')
    ensure(src.read('data/golden_pool1.hex')==src.read('fi/pilot400/samples/IMG0000/golden_pool1.hex'), 'Golden Pool1 copies differ')
    ensure(len(p2)==200, 'Golden Pool2 not complete')
    frozen=read_csv(src.read('fi/scenarios/pilot400.csv'))
    ensure(len(frozen)==400 and len({r['scenario_id'] for r in frozen})==400,'Frozen 400 missing or duplicate')
    roster={r['scenario_id']:r for r in frozen}
    matrix=read_csv(src.read('fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv'))
    ensure(len(matrix)==20 and len({r['target_id'] for r in matrix})==20,'V4c target matrix invalid')
    expected={}
    for row in matrix:
        ensure(row['directed_review']=='TWO_CONFIG_CONSUMER_DECISION_PROVED', 'Directed E3 status missing')
        ensure(row['full400_authorized']=='NO' and row['automatic_promotion']=='NO','Historical release flag changed')
        for suffix in ('a','b'):
            sid=row[f'config_{suffix}_scenario']
            ensure(sid in roster and sid not in expected,'Duplicate/unknown directed scenario')
            t=roster[sid]
            ensure(t['sample_id']=='IMG0000' and t['target_id']==row['target_id'] and t['target_class']==row['target_class'],'Target mapping changed')
            ensure(t['bit_index']==row[f'config_{suffix}_bit'] and t['injection_offset']==row[f'config_{suffix}_offset'],'Bit/offset mismatch')
            expected[sid]=(row[f'config_{suffix}_log_sha256'],row[f'config_{suffix}_outcome'],t)
    ensure(len(expected)==40 and len(set(x[0] for x in expected.values()))==40,'V4c log expectation invalid')
    sha2sid={record[0]:sid for sid,record in expected.items()}
    logs={}
    for rel,raw in src.matching_log_data():
        h=digest(raw)
        if h in sha2sid:
            sid=sha2sid[h]
            if sid not in logs:logs[sid]=(rel,raw)
    ensure(len(logs)==40,'Missing SHA256-pinned raw logs: '+repr(sorted(set(expected)-set(logs))))
    predicted=int(src.read('fi/pilot400/samples/IMG0000/prediction.hex').decode().strip(),16)
    results=[]
    for sid in sorted(expected):
        h,outcome,t=expected[sid]
        rel,raw=logs[sid]
        parsed=verify_result(raw,t,outcome,predicted)
        parsed['raw_log_sha256']=h
        results.append(parsed)
    totals={kind:sum(r[kind] for r in results) for kind in ('l0','l1','l2')}
    ensure(totals=={'l0':9,'l1':20,'l2':24},'L0/L1/L2 historical counts changed')
    negatives=[r for r in results if r['outcome']=='FINAL_OUTPUT_MATCH']
    ensure(len(negatives)==20,'Expected 20 L1-negative records')
    for rec in negatives:
        ensure(rec['pool1_words']==169 and rec['pool2_words']==25 and rec['logits_mismatches']==0,'Incomplete final-match checkpoints')
        ensure(rec['pool1_mismatches'] in (0,1) and rec['pool2_mismatches']==0,'Unexpected additional stage mismatch')
    added=[r['scenario_id'] for r in negatives if r['l2']]
    ensure(tuple(added)==CASE_IDS,'L2-only case set changed')
    complete_unchanged=[r['scenario_id'] for r in negatives if not r['l2']]
    ensure(len(complete_unchanged)==16,'L2-false complete count changed')
    tb=src.read('fi/tb/tb_cnn_mnist_fi_pilot400.v')
    comparator=comparator_region(tb.decode('utf8'))
    witnesses=[]
    for folder,ids in (('buf0007',CASE_IDS[:2]),('dat0002',CASE_IDS[2:])):
        basedir=f'fi/results/pilot400_v3b_{folder}_01'
        manifest=json_unique(src.read(f'{basedir}/v3b_{folder}_manifest.json'))
        probe=src.read(f'fi/v3b/{folder}/tb/tb_cnn_mnist_fi_pilot400.v')
        ensure(manifest['original_tb_sha256']==PIN['fi/tb/tb_cnn_mnist_fi_pilot400.v'],'Original TB ancestry mismatch')
        ensure(manifest['probe_tb_sha256']==digest(probe),'Isolated TB pin mismatch')
        ensure(comparator_region(probe.decode('utf8'))==comparator,'Isolated TB POOL1/POOL2 comparator differs')
        entries={r['scenario_id']:r['log_sha256'] for r in manifest['records']}
        ensure(set(entries)==set(ids),'V3b log manifest scenario set wrong')
        for sid in ids:
            raw=src.read(f'{basedir}/logs/{sid}.log')
            ensure(digest(raw)==entries[sid]==expected[sid][0],'V3b log SHA vs V4c matrix/manifest mismatch')
            trace=raw.decode('utf8').splitlines()
            w=buf_witness(trace,sid,p1) if folder=='buf0007' else dat_witness(trace,sid,relu,p1)
            matching=next(r for r in results if r['scenario_id']==sid)
            ensure(matching['outcome']=='FINAL_OUTPUT_MATCH' and matching['pool1_mismatches']==1
                   and matching['pool2_mismatches']==0 and matching['pool1_words']==169
                   and matching['pool2_words']==25 and matching['first_bad_stage']=='POOL1', 'L2-only counter contract mismatch')
            witnesses.append({'scenario_id':sid,'target_id':matching['target_id'],'raw_log_sha256':entries[sid],
                              'counter_pool1_words':169,'counter_pool1_mismatches':1,
                              'counter_pool2_words':25,'counter_pool2_mismatches':0,
                              'output_outcome':'FINAL_OUTPUT_MATCH',**w})
    return {'schema':'CNN_PEP_V4N_G7L2_GOLDEN_TRACE_WITNESS_V1',
            'source_kind':'SHA_PINNED_HISTORICAL_XSIM_LOGS_AND_PYTHON_GOLDEN_NO_NEW_RUN',
            'evidence_tier':'INDEPENDENT_GOLDEN_RECOMPUTATION_PLUS_V3B_TRACE_PLUS_TB_COUNTER',
            'input_pins':PIN, 'img0000_cases':40, 'golden_pool1_compared_bytes':676,
            'final_match_scenarios':20, 'l2_only_scenarios':list(CASE_IDS),
            'l2_false_complete_scenarios':complete_unchanged,
            'l2_false_complete_count':len(complete_unchanged),
            'l0_positive':totals['l0'],'l1_positive':totals['l1'],'l2_positive':totals['l2'],
            'additional_l1_over_l0':11,'additional_l2_over_l1':4,
            'witnesses':witnesses,
            'limitations':['No complete per-scenario RTL POOL1/POOL2 output-byte dump in historical logs',
              'DAT_0002 post-fault POOL1 value independently predicted, not directly sampled',
              'BUF_0007 trace proves corrupted value propagation; POOL1 mismatch comes from TB counter',
              'G7 scientific semantics not yet approved; E3 history is directed IMG0000 only',
              'Selected A/B configurations are not independent random physical SEUs'],
            'new_xsim_runs':0,'new_vivado_builds':0,'full400_authorized':False,
            'g6_production_qualified':False,'g7_scientific_approved':False}

def witness_csv(data):
    buf=io.StringIO(newline='')
    fields=['scenario_id','target_id','witness_kind','golden_pool1_index','golden_pool1_before',
            'observed_corrupted_byte','pool1_index','pool1_before','pool1_predicted_after',
            'counter_pool1_words','counter_pool1_mismatches','counter_pool2_words',
            'counter_pool2_mismatches','output_outcome','raw_log_sha256','claim_level']
    w=csv.DictWriter(buf,fieldnames=fields,lineterminator='\n')
    w.writeheader()
    for e in data['witnesses']:
        w.writerow({k:e.get(k,'') for k in fields})
    return buf.getvalue().encode('utf8')

def negative_csv(data):
    buf=io.StringIO(newline='')
    w=csv.writer(buf,lineterminator='\n')
    w.writerow(['scenario_id','l1','l2','pool1_words','pool2_words','raw_evidence_level'])
    for sid in data['l2_false_complete_scenarios']:
        w.writerow([sid,'FALSE','FALSE',169,25,'TB_COUNTER_COMPLETE_MATCH_NOT_INDEPENDENT_RTL_BYTE_DUMP'])
    return buf.getvalue().encode('utf8')

def report(data):
    txt=['# V4N — Golden and value-aware trace witnesses (CANDIDATE)','',
         'Historical, read-only. No new XSim / Vivado build. G7 not approved; Full400 blocked.','',
         '## Findings','',
         '- Independently recomputed IMG0000 POOL1 from the 4 x 26 x 26 frozen ReLU1 feature map:',
         '  **676/676 bytes equal** to golden POOL1 (4 x 13 x 13).',
         '- V4c raw logs: **40/40 SHA-pinned**; L0=9, L1=20, L2=24.',
         '- Among 20 final-output-match scenarios, 4 have one TB-reported POOL1 mismatch,',
         '  and 16 have full POOL1 (169 words) / POOL2 (25 words) TB-counter MATCH.',
         '', '| Scenario | Independent witness | TB stage | Final |','|---|---|---|---|']
    for w in data['witnesses']:
        if w['target_id']=='BUF_0007':
            summary='Pool1 reorder byte[337]: golden 00, trace value 01 → Conv2 consumer'
        else:
            summary=f"ReLU1 input (x,y)={tuple(w['input_xy'])}: 00→01; 2×2 max predicted POOL1[{w['pool1_index']}]: 00→01"
        txt.append(f"| `{w['scenario_id']}` | {summary} | 169/169 words; 1 mismatch | `FINAL_OUTPUT_MATCH` |")
    txt+=['','## Evidence boundary','',
          'Independent calculations validate the golden POOL1 map and produce a concrete',
          'fault-to-stage **witness/prediction**, with SHA-pinned RTL traces and TB counters.',
          'Historical logs do **not** provide an exhaustive per-word RTL POOL1/POOL2 output dump.',
          'Thus this report does not claim an independent 676-byte comparison against live faulty RTL.',
          'The numerical L2=24 remains a TB-counter-based historical result, now supported by separate',
          'golden arithmetic checks and value-aware V3b traces for all four added cases.',
          '', 'The selected 40 IMG0000 configurations are directed, not independent SEU samples.',
          'G6 production NOT QUALIFIED; G7 PREPARED_NOT_APPROVED; FULL400 BLOCKED.','']
    return '\n'.join(txt).encode('utf8')

def artifacts(data):
    return {'v4n_golden_trace_summary.json':bytes_json(data),
            'v4n_four_l2_witnesses.csv':witness_csv(data),
            'v4n_sixteen_l2_false_complete.csv':negative_csv(data),
            'V4N_G7L2_EVIDENCE_REPORT.md':report(data)}

def write_new(out, files):
    path=Path(out)
    ensure(re.fullmatch('v4n_[A-Za-z0-9_]+',path.name) is not None,'Output directory must start v4n_')
    ensure(not path.exists() and not path.is_symlink(),'Refusing existing output')
    ensure(all('/' not in f and '\\' not in f and f!='SHA256SUMS.txt' for f in files), 'Unsafe generated filename')
    path.mkdir(parents=True,exist_ok=False)
    checks=b''.join(f'{digest(raw)}  {name}\n'.encode('ascii') for name,raw in sorted(files.items()))
    for name,raw in {**files,'SHA256SUMS.txt':checks}.items():
        (path/name).write_bytes(raw)
    return path

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    source=ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--repo-root')
    source.add_argument('--handoff-zip')
    ap.add_argument('--mode',choices=('check','analyze'),default='check')
    ap.add_argument('--out-dir')
    args=ap.parse_args(argv)
    try:
        ensure(args.mode!='analyze' or args.out_dir is not None,'--out-dir required for analyze')
        src=Source(args.repo_root,args.handoff_zip)
        try:data=analyze(src)
        finally:src.close()
        if args.mode=='analyze':
            out=write_new(args.out_dir,artifacts(data))
            print('V4N_OUTPUT:',out)
        print('V4N_G7L2_GOLDEN_TRACE_PASS: 676/676 golden Pool1 bytes; 4/4 L2-only witnesses; '
              '16/16 L2 FALSE full TB-counter coverage; L0=9 L1=20 L2=24; 0 XSim; G7 NOT APPROVED; FULL400 BLOCKED')
        return 0
    except (ValueError,FileNotFoundError,KeyError,OSError,UnicodeError,zipfile.BadZipFile,json.JSONDecodeError) as exc:
        print('V4N_FAIL_CLOSED:',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())

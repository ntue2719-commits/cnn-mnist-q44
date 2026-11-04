#!/usr/bin/env python3
"""CNN_PEP V4N independent artifact/golden/trace replay auditor (read-only)."""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT_PREFIX='CNN_PEP_AI_READY_2026-11-04/'
FOUR=('FI_P400_0101','FI_P400_0102','FI_P400_0301','FI_P400_0302')
EXPECTED_FILES=('v4n_golden_trace_summary.json','v4n_four_l2_witnesses.csv',
                'v4n_sixteen_l2_false_complete.csv','V4N_G7L2_EVIDENCE_REPORT.md')
HASHES={
 'fi/tb/tb_cnn_mnist_fi_pilot400.v':'2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
 'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv':'36633692e0edee31f5de87682a49499bf192e1b65648737aa84e8c9178f19d36',
 'fi/pilot400/samples/IMG0000/golden_pool1.hex':'7ca827bd5582ebddc8ea64e6358f4b03d1c4b393c3613773201d8825012bdceb',
 'fi/pilot400/samples/IMG0000/golden_pool2.hex':'65cd941d1e44bcc522f5c36bbc7868f28cbecd6b4f9466750fe3e421ea17751d',
 'data/golden_relu1.hex':'41f66b73f81231d66588a6623b9cc1f19d913b0be133dd61c97fbf4dfa2d8278',
 'fi/scenarios/pilot400.csv':'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
}

def fail(condition,msg):
    if not condition:raise ValueError(msg)

def sha(data):return hashlib.sha256(data).hexdigest()

def parse_json(raw):
    def no_dupes(pairs):
        result={}
        for k,v in pairs:
            fail(k not in result,'Duplicate JSON key '+k)
            result[k]=v
        return result
    return json.loads(raw.decode('utf8'),object_pairs_hook=no_dupes)

def csv_rows(raw):
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf8'))))
    fail(len(rows)>0 and all(None not in row for row in rows),'CSV invalid or empty')
    return rows

class Input:
    def __init__(self,root,zip_file):
        fail((root is None)!=(zip_file is None),'Input source required')
        self.z=zipfile.ZipFile(zip_file) if zip_file else None
        self.path=Path(root).resolve(strict=True) if root else None
        if self.z:
            ns=self.z.namelist()
            fail(len(ns)==len(set(ns)) and ROOT_PREFIX+'fi/scenarios/pilot400.csv' in ns,'Archive members/prefix invalid')
    def fetch(self,rel):
        fail(not rel.startswith('/') and '..' not in Path(rel).parts,'Unsafe reference')
        if self.z:
            info=self.z.getinfo(ROOT_PREFIX+rel)
            fail(info.file_size <=2_000_000 and not info.is_dir(),'Source size exceeded')
            fail(((info.external_attr >> 16)&0o170000)!=0o120000,'Symlinked ZIP member')
            return self.z.read(info)
        p=self.path/rel
        for t in [p,*p.parents]:
            if t==self.path:break
            fail(not t.is_symlink(),'Source symlink')
        p=p.resolve(strict=True)
        fail(p.is_relative_to(self.path) and p.is_file() and p.stat().st_size<=2_000_000,'Source file invalid')
        return p.read_bytes()
    def log_data(self):
        if self.z:
            for item in self.z.infolist():
                rel=item.filename[len(ROOT_PREFIX):] if item.filename.startswith(ROOT_PREFIX) else ''
                if re.fullmatch(r'fi/results/.+/(logs|runs)/FI_P400_\d{4}\.log',rel) and item.file_size<=2_000_000:
                    yield rel,self.fetch(rel)
        else:
            for p in (self.path/'fi/results').rglob('FI_P400_*.log'):
                rel=p.relative_to(self.path).as_posix()
                if re.fullmatch(r'fi/results/.+/(logs|runs)/FI_P400_\d{4}\.log',rel):
                    yield rel,self.fetch(rel)
    def close(self):
        if self.z:self.z.close()

def golden_check(inp):
    for rel,h in HASHES.items():fail(sha(inp.fetch(rel))==h,'Source hash drift '+rel)
    raw=inp.fetch('data/golden_relu1.hex').decode('ascii').split()
    gp1=inp.fetch('fi/pilot400/samples/IMG0000/golden_pool1.hex').decode('ascii').split()
    gp2=inp.fetch('fi/pilot400/samples/IMG0000/golden_pool2.hex').decode('ascii').split()
    fail(len(raw)==2704 and len(gp1)==676 and len(gp2)==200,'Golden shape wrong')
    fail(all(re.fullmatch('[0-9A-Fa-f]{2}',x) for x in raw+gp1+gp2),'Bad golden hex')
    relu=list(map(lambda x:int(x,16),raw)); pool1=[int(x,16) for x in gp1]
    cnt=0
    for channel in range(4):
        for y in range(13):
            for x in range(13):
                values=[relu[channel*676+(y*2+dy)*26+x*2+dx] for dy in (0,1) for dx in (0,1)]
                fail(max(values)==pool1[channel*169+y*13+x],f'Golden Pool1 differs at {cnt}')
                cnt+=1
    fail(cnt==676,'Golden Pool1 recomputation incomplete')
    return relu,pool1

def inspect_result_log(raw):
    lines=raw.decode('utf8').splitlines()
    r=[line.split(',') for line in lines if line.startswith('FI_RESULT_V2,')]
    a=[line for line in lines if line.startswith('FI_APPLIED ')]
    fail(len(r)==len(a)==1 and len(r[0])==27,'Strict FI markers missing')
    f=r[0]
    fail(all(re.fullmatch(r'-?\d+',x) for x in f[5:25]),'Invalid FI_RESULT integer')
    fail(f[26] in ('FINAL_OUTPUT_MATCH','WRONG_LOGITS_SAME_LABEL','NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL'), 'Bad outcome')
    parsed={}
    for x in a[0].split(' ')[1:]:
        fail('=' in x,'Bad applied token')
        k,v=x.split('=',1)
        fail(k not in parsed,'Duplicate applied token')
        parsed[k]=v
    fail(parsed.get('scenario')==f[1] and parsed.get('target')==f[3] and parsed.get('class')==f[4],'FI_APPLIED mismatched')
    fail(int(parsed.get('bit','-1'))==int(f[5]) and int(parsed.get('requested','-1'))==int(f[7]),'FI_APPLIED invalid bit/time')
    fail(int(f[9])==int(f[10])==1,'Unverified FI event')
    return {'id':f[1],'sample':f[2], 'target':f[3],'class':f[4], 'bit':int(f[5]),'offset':int(f[6]),
            'req':int(f[7]),'act':int(f[8]),'outcome':f[26],'first':f[25],
            'w1':int(f[15]),'w2':int(f[16]), 'm1':int(f[21]),'m2':int(f[22]),
            'logits':int(f[23]),'done':int(f[19]),'protocol':int(f[20])}

def get_markers(lines, prefix, tags):
    events={}
    for tag in tags:
        key=prefix+'_'+tag
        matching=[ln for ln in lines if ln.startswith(key+' ')]
        fail(len(matching)==1,'Missing/duplicate V3b marker '+key)
        parts={}
        for token in matching[0].split(' ')[1:]:
            fail('=' in token,'Bad marker token')
            k,v=token.split('=',1)
            fail(k not in parts,'Duplicate marker key')
            parts[k]=v
        events[tag]=parts
    return events

def independent_trace_check(inp, sid, relu,p1):
    target='buf0007' if sid in FOUR[:2] else 'dat0002'
    base=f'fi/results/pilot400_v3b_{target}_01'
    manifest=parse_json(inp.fetch(f'{base}/v3b_{target}_manifest.json'))
    expected={r['scenario_id']:r['log_sha256'] for r in manifest['records']}
    fail(sid in expected and len(expected)==2,'Isolated manifest scenario wrong')
    raw=inp.fetch(f'{base}/logs/{sid}.log')
    fail(sha(raw)==expected[sid],'V3b historical log mutated')
    text=raw.decode('utf8').splitlines()
    if target=='buf0007':
        e=get_markers(text,'FI_V3B_BUF0007', ('INJECT','CAPTURE','CALC_CONSUME'))
        fail(e['INJECT'].get('before')=='00' and e['INJECT'].get('after')=='01','BUF injection not 00 to 01')
        fail(e['CAPTURE'].get('byte337')=='01' and e['CALC_CONSUME'].get('byte')=='01','BUF E3 value not consumed')
        fail(int(e['INJECT']['cycle'])<int(e['CAPTURE']['cycle'])<int(e['CALC_CONSUME']['cycle']),'BUF trace ordering wrong')
        fail(p1[337]==0,'BUF independent golden byte is not 0')
        return {'index':337,'golden_before':0,'predicted_after':1, 'kind':'BUF_REORDER_VALUE_CHAIN', 'log_sha256':expected[sid]}
    e=get_markers(text,'FI_V3B_DAT0002',('INJECT','ACCEPT','POOL_CONSUME'))
    bx=int(e['INJECT']['before'],16); ax=int(e['INJECT']['after'],16)
    fail(bx^1==ax and bx&255==0 and ax&255==1, 'DAT bitflip wrong')
    x=int(e['ACCEPT']['x']);y=int(e['ACCEPT']['y']);slot=(y%2)*2+x%2
    fail(0<=x<26 and 0<=y<26,'DAT coordinates invalid')
    fail(e['ACCEPT']['word']==e['INJECT']['after'] and e['POOL_CONSUME']['word']==e['INJECT']['after'] and int(e['POOL_CONSUME']['slot'])==slot,'DAT E3 input mismatch')
    fail(int(e['INJECT']['cycle'])==int(e['ACCEPT']['cycle'])<int(e['POOL_CONSUME']['cycle']),'DAT trace cycle wrong')
    ix=y*26+x;pi=(y//2)*13+x//2
    peers=[relu[yy*26+xx] for yy in (y//2*2,y//2*2+1) for xx in (x//2*2,x//2*2+1)]
    fail(relu[ix]==0 and peers==[0,0,0,0] and p1[pi]==0,'DAT pre-fault golden not all zero')
    peers[(y%2)*2+(x%2)]=1
    fail(max(peers)==1,'DAT independent maxpool prediction wrong')
    return {'index':pi,'golden_before':0,'predicted_after':1,'kind':'DAT_INPUT_CONSUMPTION_PLUS_INDEPENDENT_MAXPOOL_PREDICTION','log_sha256':expected[sid]}

def independent_replay(inp):
    relu,p1=golden_check(inp)
    manifest=parse_json(inp.fetch('fi/results/v4c_readiness_audit_01/v4c_manifest.json'))
    matrix=inp.fetch('fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv')
    fail(manifest['target_matrix_sha256']==sha(matrix) and manifest['directed_img0000_proofs']==40,'V4c manifest link invalid')
    plans=csv_rows(inp.fetch('fi/scenarios/pilot400.csv'))
    fail(len(plans)==400,'Frozen roster not 400')
    roster={r['scenario_id']:r for r in plans}
    fail(len(roster)==400,'Duplicate frozen scenario')
    expectations={}
    for r in csv_rows(matrix):
        fail(r['directed_review']=='TWO_CONFIG_CONSUMER_DECISION_PROVED','V4c E3 qualified marker missing')
        for suffix in ('a','b'):
            sid=r[f'config_{suffix}_scenario'];plan=roster[sid]
            fail(sid not in expectations and plan['sample_id']=='IMG0000' and plan['target_id']==r['target_id'],'V4c scenario mismatch')
            expectations[sid]=(r[f'config_{suffix}_log_sha256'], r[f'config_{suffix}_outcome'],plan)
    fail(len(expectations)==40,'Missing V4c scenarios')
    sha_lookup={p[0]:sid for sid,p in expectations.items()}
    raw_logs={}
    for _rel,raw in inp.log_data():
        h=sha(raw)
        if h in sha_lookup:
            raw_logs[sha_lookup[h]]=raw
    fail(set(raw_logs)==set(expectations),'Incomplete V4c raw logs')
    truths=[0,0,0]; complete=[]; l2only=[]
    for sid in sorted(expectations):
        expectedsha,outcome,plan=expectations[sid]
        row=inspect_result_log(raw_logs[sid])
        fail((row['id'],row['sample'],row['target'],row['class'])==(sid,plan['sample_id'],plan['target_id'],plan['target_class']),'Raw result tuple mismatch')
        fail(row['outcome']==outcome and row['bit']==int(plan['bit_index']) and row['offset']==int(plan['injection_offset']),'Raw outcome/bit/offset mismatch')
        fail(row['req']==row['act']==row['offset']+3,'Raw injection cycle incorrect')
        fail(0<=row['w1']<=169 and 0<=row['w2']<=25 and 0<=row['m1']<=row['w1']*4 and 0<=row['m2']<=row['w2']*8,'Invalid word/mismatch counter')
        if row['outcome']=='FINAL_OUTPUT_MATCH':
            fail(row['w1']==169 and row['w2']==25 and row['logits']==0 and row['done']==row['protocol']==1,'Final-match complete proof missing')
        v0=row['outcome'] in ('NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL')
        v1=row['outcome']!='FINAL_OUTPUT_MATCH';v2=v1 or row['m1']>0 or row['m2']>0
        truths=[truths[i]+(v0,v1,v2)[i] for i in range(3)]
        if not v1:
            fail(row['w1']==169 and row['w2']==25, 'L1-negative incomplete')
            if v2:
                fail(row['m1']==1 and row['m2']==0 and row['first']=='POOL1', 'Unexpected additional L2-only stage evidence')
                l2only.append(sid)
            else:
                fail(row['m1']==row['m2']==0 and row['first']=='NONE', 'L2 FALSE stage not completely clean')
                complete.append(sid)
    fail(truths==[9,20,24] and tuple(l2only)==FOUR and len(complete)==16,'Independent L0/L1/L2 counts differ')
    original=inp.fetch('fi/tb/tb_cnn_mnist_fi_pilot400.v').decode('utf8')
    start='if (dut.conv1_out_valid) begin'
    end='if (dut.fc_valid) begin'
    fail(start in original and end in original and original.index(start)<original.index(end), 'Frozen comparator missing')
    comparator=original[original.index(start):original.index(end,original.index(start))]
    for folder in ('buf0007','dat0002'):
        tb=inp.fetch(f'fi/v3b/{folder}/tb/tb_cnn_mnist_fi_pilot400.v')
        man=parse_json(inp.fetch(f'fi/results/pilot400_v3b_{folder}_01/v3b_{folder}_manifest.json'))
        fail(sha(tb)==man['probe_tb_sha256'] and man['original_tb_sha256']==HASHES['fi/tb/tb_cnn_mnist_fi_pilot400.v'], 'Probe TB SHA ancestry drift')
        text=tb.decode('utf8')
        fail(start in text and end in text and text.index(start)<text.index(end),'Isolated comparator missing')
        fail(text[text.index(start):text.index(end,text.index(start))]==comparator,'Isolated stage comparator changed')
    witness=[independent_trace_check(inp,sid,relu,p1) for sid in FOUR]
    fail([w['index'] for w in witness]==[337,337,78,119],'Golden witness byte-index mapping changed')
    return {'l0':truths[0],'l1':truths[1],'l2':truths[2],'four':list(FOUR),'sixteen':complete,
            'witness':witness, 'golden_pool1_bytes':676}

def audit_output(analysis_dir):
    original=Path(analysis_dir)
    fail(not original.is_symlink(),'Symlinked analysis directory denied')
    path=original.resolve(strict=True)
    fail(re.fullmatch(r'v4n_[A-Za-z0-9_]+',path.name) is not None,'Not a V4N directory')
    fail(path.is_dir() and not path.is_symlink(),'Output not safe directory')
    found={p.name for p in path.iterdir()}
    fail(found==set(EXPECTED_FILES)|{'SHA256SUMS.txt'},'Missing/extra output files')
    for f in path.iterdir():fail(f.is_file() and not f.is_symlink() and f.stat().st_size<2_000_000,'Unexpected non-file')
    raw={p.name:p.read_bytes() for p in path.iterdir()}
    expected=''.join(sha(raw[n])+'  '+n+'\n' for n in sorted(EXPECTED_FILES)).encode('ascii')
    fail(raw['SHA256SUMS.txt']==expected,'Artifact inventory hash mismatch')
    summary=parse_json(raw['v4n_golden_trace_summary.json'])
    fail(raw['v4n_golden_trace_summary.json']==(json.dumps(summary,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode('utf8'),'Noncanonical JSON output')
    fail(summary.get('schema')=='CNN_PEP_V4N_G7L2_GOLDEN_TRACE_WITNESS_V1','Schema incorrect')
    fail(summary.get('full400_authorized') is False and summary.get('new_xsim_runs')==0 and summary.get('new_vivado_builds')==0 and summary.get('g6_production_qualified') is False and summary.get('g7_scientific_approved') is False, 'Forged release flag')
    witnesses=csv_rows(raw['v4n_four_l2_witnesses.csv'])
    negatives=csv_rows(raw['v4n_sixteen_l2_false_complete.csv'])
    fail(len(witnesses)==4 and len(negatives)==16,'CSV row counts mismatch')
    fail([r['scenario_id'] for r in witnesses]==list(FOUR),'Witness CSV scenario mismatch')
    fail(len({r['scenario_id'] for r in negatives})==16,'Negative CSV duplicate')
    return summary,witnesses,negatives,raw

def verify(aud, data):
    summary,witnesses,negatives,_raw=aud
    fail(summary['l0_positive']==data['l0'] and summary['l1_positive']==data['l1'] and summary['l2_positive']==data['l2'],'Reported observation counts wrong')
    fail(summary['golden_pool1_compared_bytes']==data['golden_pool1_bytes'], 'Golden compared count wrong')
    fail(summary['l2_only_scenarios']==data['four'],'Summary four witnesses mismatch')
    fail(summary['l2_false_complete_count']==16 and summary['additional_l1_over_l0']==11 and summary['additional_l2_over_l1']==4, 'Summary denominator/increment mismatch')
    for rel,h in HASHES.items():
        fail(summary['input_pins'].get(rel)==h,'Source pin omitted or changed: '+rel)
    fail(summary['l2_false_complete_scenarios']==data['sixteen'],'Summary 16 L2 FALSE mismatch')
    fail([r['scenario_id'] for r in negatives]==data['sixteen'],'Negative CSV missing or rearranged')
    for i,(w,g) in enumerate(zip(witnesses,data['witness'])):
        index_field='golden_pool1_index' if i<2 else 'pool1_index'
        fail(int(w[index_field])==g['index'] and int(w['counter_pool1_mismatches'])==1 and int(w['counter_pool1_words'])==169 and int(w['counter_pool2_words'])==25 and int(w['counter_pool2_mismatches'])==0, 'Witness CSV golden/RTL mismatch')
        fail(w['witness_kind']==g['kind'] and w['output_outcome']=='FINAL_OUTPUT_MATCH','Witness type/outcome invalid')
        fail(w['raw_log_sha256']==summary['witnesses'][i]['raw_log_sha256']==g['log_sha256'], 'Witness log pin wrong')
        fail(summary['witnesses'][i]['target_id']==w['target_id'] and summary['witnesses'][i]['counter_pool1_mismatches']==1 and summary['witnesses'][i]['counter_pool2_mismatches']==0,'Witness JSON fields changed')
        fail(int(w['golden_pool1_before'] if i<2 else w['pool1_before'])==g['golden_before'],'Golden witness comparison mismatch')
        fail(int(w['observed_corrupted_byte'] if i<2 else w['pool1_predicted_after'])==g['predicted_after'],'Corrupted value/prediction mismatch')
    fail([row['scenario_id'] for row in summary['witnesses']]==data['four'],'Witness JSON IDs wrong')
    for r in negatives:
        fail(r['l1']=='FALSE' and r['l2']=='FALSE' and r['pool1_words']=='169' and r['pool2_words']=='25' and r['raw_evidence_level']=='TB_COUNTER_COMPLETE_MATCH_NOT_INDEPENDENT_RTL_BYTE_DUMP','L2-negative CSV field forged')
    return True

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    a=p.add_mutually_exclusive_group(required=True)
    a.add_argument('--repo-root')
    a.add_argument('--handoff-zip')
    p.add_argument('--analysis-dir',required=True)
    p.add_argument('--out-dir',required=True)
    args=p.parse_args(argv)
    try:
        fail(re.fullmatch(r'v4n_[A-Za-z0-9_]+',Path(args.out_dir).name) is not None,'Audit output path namespace wrong')
        fail(not Path(args.out_dir).exists(),'Audit output exists')
        inp=Input(args.repo_root,args.handoff_zip)
        try:result=independent_replay(inp)
        finally:inp.close()
        aud=audit_output(args.analysis_dir)
        verify(aud,result)
        receipt={'schema':'CNN_PEP_V4N_INDEPENDENT_GOLDEN_TRACE_REPLAY_AUDIT_V1',
                 '40_raw_logs_replayed':40,'golden_pool1_bytes_verified':676,
                 'four_witnesses_checked':4,'sixteen_complete_negative_cases_checked':16,
                 'source_digests':HASHES,
                 'analyzed_summary_sha256':sha(aud[3]['v4n_golden_trace_summary.json']),
                 'full400_authorized':False,'g6_production_qualified':False,
                 'g7_scientific_approved':False,'new_xsim_runs':0}
        out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=False)
        b=(json.dumps(receipt,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode('utf8')
        (out/'v4n_independent_audit_receipt.json').write_bytes(b)
        (out/'SHA256SUMS.txt').write_text(sha(b)+'  v4n_independent_audit_receipt.json\n',encoding='ascii',newline='\n')
        print('V4N_INDEPENDENT_AUDIT_PASS: 40/40 V4c raw SHA-pinned scenarios; 676/676 golden Pool1 bytes; '
              '4/4 witnesses; 16/16 complete L2 false TB counters; 0 XSim; FULL400 BLOCKED')
        print('V4N_AUDIT_OUTPUT:',out)
        return 0
    except (ValueError,KeyError,FileNotFoundError,OSError,UnicodeError,json.JSONDecodeError,zipfile.BadZipFile) as e:
        print('V4N_AUDIT_FAIL_CLOSED:',e,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())

#!/usr/bin/env python3
"""V4M read-only directed IMG0000 raw checkpoint analysis; never launches simulation."""
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
    'fi/scenarios/pilot400.csv': 'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
    'fi/config/pilot400_targets.csv': '98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
    'fi/tb/tb_cnn_mnist_fi_pilot400.v': '2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
    'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv': '36633692e0edee31f5de87682a49499bf192e1b65648737aa84e8c9178f19d36',
    'fi/pilot400/samples/IMG0000/prediction.hex': '56a468fa75d7e429e99b3f560bbdcef20821e79f63a70a7ade6660fac6617682',
}
EXPECTED_OUTCOMES = {
    'NONCOMPLETION', 'PROTOCOL_VIOLATION', 'WRONG_LABEL',
    'WRONG_LOGITS_SAME_LABEL', 'FINAL_OUTPUT_MATCH',
}
HARMFUL = {'NONCOMPLETION', 'PROTOCOL_VIOLATION', 'WRONG_LABEL'}
APPLIED = re.compile(r'^FI_APPLIED scenario=(FI_P400_\d{4}) target=(\w+) class=(CTL|BUF|MEM|DAT) bit=(\d+) old=([01]) new=([01]) requested=(\d+)$')
RESULT_LEN = 27
MAX_LOG_SIZE = 2_000_000
EXTRA_EXPECTED = ('FI_P400_0101', 'FI_P400_0102', 'FI_P400_0301', 'FI_P400_0302')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def json_strict(b):
    def unique(pairs):
        d = {}
        for k, v in pairs:
            require(k not in d, 'Duplicate JSON key: ' + k)
            d[k] = v
        return d
    return json.loads(b.decode('utf-8'), object_pairs_hook=unique)


def canonical(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('utf-8')


def stage(words, mismatch, expected, channels):
    for v in (words, mismatch):
        require(type(v) is int, 'Bad stage integer type')
    require(0 <= words <= expected, 'Stage word count out of range')
    require(0 <= mismatch <= words * channels, 'Stage mismatch count out of range')
    if mismatch > 0:
        return 'MISMATCH'
    if words == expected:
        return 'MATCH'
    if words == 0:
        return 'NOT_OBSERVED'
    return 'INCONCLUSIVE'


def levels(outcome, p1, p2):
    require(outcome in EXPECTED_OUTCOMES, 'Unknown outcome')
    l0 = outcome in HARMFUL
    l1 = outcome != 'FINAL_OUTPUT_MATCH'
    l2 = 'TRUE' if (l1 or 'MISMATCH' in (p1, p2)) else ('FALSE' if p1 == p2 == 'MATCH' else 'INCONCLUSIVE')
    return ('TRUE' if l0 else 'FALSE', 'TRUE' if l1 else 'FALSE', l2)


class Source:
    def __init__(self, handoff_zip=None, repo_root=None):
        require((handoff_zip is None) != (repo_root is None), 'Choose exactly one source')
        self.z = None
        if handoff_zip is not None:
            self.z = zipfile.ZipFile(handoff_zip, 'r')
            names = self.z.namelist()
            require(len(names) == len(set(names)), 'Duplicate ZIP entries')
            require(PREFIX + 'fi/scenarios/pilot400.csv' in names, 'Missing expected handoff prefix')
        else:
            self.root = Path(repo_root).resolve(strict=True)
            require((self.root/'fi/scenarios/pilot400.csv').is_file(), 'Not a repository root')

    def close(self):
        if self.z:
            self.z.close()

    def _disk_path(self, rel):
        require(not Path(rel).is_absolute() and '..' not in Path(rel).parts, 'Unsafe source path')
        p = self.root / rel
        for item in [p, *p.parents]:
            if item == self.root:
                break
            require(not item.is_symlink(), 'Symlink source refused: ' + str(p))
        p = p.resolve(strict=True)
        require(p.is_relative_to(self.root) and p.is_file(), 'Source outside repository')
        return p

    def read(self, rel, limit=MAX_LOG_SIZE):
        if self.z:
            name = PREFIX + rel
            inf = self.z.getinfo(name)
            require(not inf.is_dir() and inf.file_size <= limit, 'Source size exceeded: ' + rel)
            return self.z.read(name)
        p = self._disk_path(rel)
        require(p.stat().st_size <= limit, 'Source size exceeded: ' + rel)
        return p.read_bytes()

    def log_candidates(self):
        pat = re.compile(r'^fi/results/.+/(?:logs|runs)/FI_P400_\d{4}\.log$')
        if self.z:
            for f in sorted(self.z.infolist(), key=lambda q: q.filename):
                if f.filename.startswith(PREFIX):
                    rel = f.filename[len(PREFIX):]
                    if pat.fullmatch(rel) and f.file_size <= MAX_LOG_SIZE:
                        yield rel, self.z.read(f)
        else:
            for p in sorted((self.root/'fi/results').rglob('FI_P400_*.log')):
                rel = p.relative_to(self.root).as_posix()
                if pat.fullmatch(rel):
                    yield rel, self.read(rel)


def parse_log(raw, frozen, predicted):
    require(len(raw) <= MAX_LOG_SIZE, 'Oversized raw log')
    lines = raw.decode('utf-8', errors='strict').splitlines()
    a = [x for x in lines if x.startswith('FI_APPLIED ')]
    r = [x for x in lines if x.startswith('FI_RESULT_V2,')]
    require(len(a) == 1 and len(r) == 1, 'Strict markers require exactly one FI_APPLIED and FI_RESULT_V2')
    m = APPLIED.fullmatch(a[0])
    require(m is not None, 'Malformed FI_APPLIED')
    aid, target, klass, bit, old, new, req = m.groups()
    fields = r[0].split(',')
    require(len(fields) == RESULT_LEN, 'FI_RESULT_V2 field count != 27')
    require(fields[0] == 'FI_RESULT_V2', 'Incorrect marker')
    for idx, key in ((1, 'scenario_id'), (2, 'sample_id'), (3, 'target_id'), (4, 'target_class')):
        require(fields[idx] == frozen[key], 'FI_RESULT_V2 frozen ' + key + ' mismatch')
    for got, key in ((aid, 'scenario_id'), (target, 'target_id'), (klass, 'target_class')):
        require(got == frozen[key], 'FI_APPLIED frozen ' + key + ' mismatch')
    ints = []
    for x in fields[5:25]:
        require(re.fullmatch(r'-?\d+', x) is not None, 'Bad FI_RESULT_V2 integer')
        ints.append(int(x))
    require(int(bit) == int(frozen['bit_index']) == ints[0], 'Bit index mismatch')
    require(int(frozen['injection_offset']) == ints[1], 'Offset mismatch')
    require(int(req) == ints[2] == ints[3], 'Requested/actual cycle mismatch')
    require(int(req) == ints[1] + 3, 'start_cycle offset contract mismatch')
    require(old != new and {old, new} == {'0','1'}, 'Not one-bit XOR')
    require(ints[4] == ints[5] == 1, 'FI event/injection marker not verified')
    # indices: [5 bit,6 offset,7 req,8 actual,9 events,10 injection_ok,
    #  11 first_done_cycle,12 first_done_pixels,13 driven,14 accepted,
    #  15 p1_words,16 p2_words,17 fc_valid,18 argmax,19 done,20 protocol_ok,
    #  21 p1_mismatch,22 p2_mismatch,23 logits_mismatch,24 prediction]
    p1_w, p2_w, p1_m, p2_m = ints[10], ints[11], ints[16], ints[17]
    p1, p2 = stage(p1_w, p1_m, 169, 4), stage(p2_w, p2_m, 25, 8)
    outcome, first_stage = fields[26], fields[25]
    require(outcome in EXPECTED_OUTCOMES, 'Unknown outcome')
    require(first_stage in {'NONE','POOL1','POOL2','FC','ARGMAX','PROTOCOL','TIMEOUT'}, 'Unknown first stage')
    require(any(x == 'OUTCOME='+outcome+' FIRST_BAD_STAGE='+first_stage for x in lines), 'OUTCOME marker differs')
    completed, protocol_ok = ints[14], ints[15]
    require(completed in (0,1) and protocol_ok in (0,1), 'Invalid done/protocol counts')
    if outcome == 'NONCOMPLETION':
        require(completed == 0 and ints[6] < 0, 'Noncompletion lacks design-level evidence')
    elif outcome == 'PROTOCOL_VIOLATION':
        require(completed == 1 and protocol_ok == 0, 'Protocol violation lacks design evidence')
    else:
        require(completed == 1 and protocol_ok == 1, 'Numerical outcome without completed protocol')
        require(p1_w == 169 and p2_w == 25 and ints[12] == ints[13] == 1, 'Incomplete numerical run')
    if outcome == 'WRONG_LABEL':
        require(ints[19] != predicted, 'Wrong label equals golden')
    if outcome in {'WRONG_LOGITS_SAME_LABEL', 'FINAL_OUTPUT_MATCH'}:
        require(ints[19] == predicted, 'Label mismatches golden')
    if outcome == 'WRONG_LOGITS_SAME_LABEL':
        require(ints[18] > 0, 'Wrong logits with zero mismatch counter')
    if outcome == 'FINAL_OUTPUT_MATCH':
        require(ints[18] == 0, 'Final match with nonzero logits mismatch')
    if (p1 == 'MISMATCH' or p2 == 'MISMATCH') and first_stage == 'NONE':
        raise ValueError('Stage mismatch with first_bad_stage NONE')
    l0,l1,l2=levels(outcome,p1,p2)
    return dict(
        scenario_id=fields[1], sample_id=fields[2], target_id=fields[3], target_class=fields[4],
        bit_index=ints[0], injection_offset=ints[1], fi_events=ints[4], injection_verified=ints[5],
        pool1_words=p1_w, pool1_mismatch=p1_m, pool1_status=p1,
        pool2_words=p2_w, pool2_mismatch=p2_m, pool2_status=p2,
        logits_mismatch=ints[18], prediction=ints[19],
        first_bad_stage=first_stage, outcome=outcome, l0=l0, l1=l1, l2=l2,
    )


def comparator_region(text):
    start='if (dut.conv1_out_valid) begin'
    end='if (dut.fc_valid) begin'
    require(text.count(start) >= 1 and text.count(end) >= 1, 'Comparator snippet absent')
    i=text.index(start); j=text.index(end,i)
    return text[i:j]


def validated_l2_source(src):
    parent = src.read('fi/tb/tb_cnn_mnist_fi_pilot400.v').decode('utf-8')
    region = comparator_region(parent)
    proofs=[]
    for folder in ('buf0007','dat0002'):
        tbrel=f'fi/v3b/{folder}/tb/tb_cnn_mnist_fi_pilot400.v'
        manrel=f'fi/results/pilot400_v3b_{folder}_01/v3b_{folder}_manifest.json'
        tb=src.read(tbrel)
        manifest=json_strict(src.read(manrel))
        require(manifest['original_tb_sha256'] == PIN['fi/tb/tb_cnn_mnist_fi_pilot400.v'], 'V3b original TB pin mismatch')
        require(sha(tb) == manifest['probe_tb_sha256'], 'V3b source hash mismatch')
        require(comparator_region(tb.decode('utf-8')) == region, 'Stage comparator modified in isolated V3b')
        proofs.append(dict(target=folder, isolated_tb_sha256=sha(tb), stage_comparator_byte_identical=True))
    return proofs


def compute(src):
    for rel,h in PIN.items():
        require(sha(src.read(rel)) == h, 'Pinned file mismatch: ' + rel)
    matrixb=src.read('fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv')
    manifest=json_strict(src.read('fi/results/v4c_readiness_audit_01/v4c_manifest.json'))
    require(manifest['target_matrix_sha256']==sha(matrixb), 'Manifest/matrix digest mismatch')
    require(manifest['directed_img0000_proofs']==40 and manifest['targets_reviewed']==20, 'V4c scope mismatch')
    require(manifest['full400_authorized'] is False, 'Historical source claims authorization')
    roster=list(csv.DictReader(io.StringIO(src.read('fi/scenarios/pilot400.csv').decode('utf-8'))))
    require(len(roster)==400, 'Frozen plan must contain 400 rows')
    plan={r['scenario_id']:r for r in roster}
    require(len(plan)==400, 'Duplicate frozen scenario')
    matrix=list(csv.DictReader(io.StringIO(matrixb.decode('utf-8'))))
    require(len(matrix)==20, 'V4c matrix must have 20 targets')
    predicted=int(src.read('fi/pilot400/samples/IMG0000/prediction.hex').decode().strip(),16)
    targets=set(); expect={}
    for r in matrix:
        require(r['target_id'] not in targets, 'Duplicate matrix target')
        targets.add(r['target_id'])
        require(r['directed_review']=='TWO_CONFIG_CONSUMER_DECISION_PROVED', 'Not V4c E3 qualified')
        require(r['full400_authorized']=='NO' and r['automatic_promotion']=='NO', 'Historical release flag changed')
        for ab in ('a','b'):
            sid=r[f'config_{ab}_scenario']
            require(sid in plan and sid not in expect, 'Unexpected/duplicate frozen scenario')
            f=plan[sid]
            require(f['sample_id']=='IMG0000' and f['target_id']==r['target_id'] and f['target_class']==r['target_class'], 'V4c tuple mismatch')
            require(int(f['bit_index'])==int(r[f'config_{ab}_bit']), 'V4c bit mismatch')
            require(int(f['injection_offset'])==int(r[f'config_{ab}_offset']), 'V4c offset mismatch')
            expect[sid]=dict(sha=r[f'config_{ab}_log_sha256'],outcome=r[f'config_{ab}_outcome'],plan=f)
    require(len(expect)==40, 'Expected exact 40 scenarios')
    hashes={item['sha']:sid for sid,item in expect.items()}
    require(len(hashes)==40, 'Duplicate log digest in V4c matrix')
    collected={}
    for rel,b in src.log_candidates():
        h=sha(b)
        if h in hashes:
            sid=hashes[h]
            if sid not in collected:
                collected[sid]=(rel,b)
    require(set(collected)==set(expect), 'Missing raw SHA-pinned V4c log(s): '+','.join(sorted(set(expect)-set(collected))))
    rows=[]
    for sid in sorted(expect):
        rel,raw=collected[sid]
        r=parse_log(raw,expect[sid]['plan'],predicted)
        require(r['outcome']==expect[sid]['outcome'], 'Outcome differs from V4c matrix')
        r['raw_log_sha256']=sha(raw)
        r['raw_log_path']=rel
        rows.append(r)
    new=[r['scenario_id'] for r in rows if r['l1']=='FALSE' and r['l2']=='TRUE']
    require(tuple(new)==EXTRA_EXPECTED, 'Unexpected incremental L2 cases: '+str(new))
    proofs=validated_l2_source(src)
    c=Counter((r['l0'],r['l1'],r['l2']) for r in rows)
    counts={k:Counter(r[k] for r in rows) for k in ('l0','l1','l2')}
    require(sum(r['l0']=='TRUE' for r in rows)==9,'L0 snapshot drift')
    require(sum(r['l1']=='TRUE' for r in rows)==20,'L1 snapshot drift')
    require(sum(r['l2']=='TRUE' for r in rows)==24,'L2 snapshot drift')
    return dict(schema='CNN_PEP_V4M_G7L2_READONLY_DIRECTED_IMG0000_V1',
                source_kind='ORIGINAL_V4C_HASH_PINNED_RAW_XSIM_LOGS_NOT_NEW_RUNS',
                evidence_level='HISTORICAL_HASH_PINNED_TB_COUNTER_REPLAY',
                planned_full400=400, analyzed_directed_img0000=40,
                full400_authorized=False,g6_production_qualified=False,g7_scientific_approved=False,
                xsim_runs_new=0,executions_new=0,
                pinned_sha256=PIN,
                l0=dict(counts['l0']),l1=dict(counts['l1']),l2=dict(counts['l2']),
                l1_minus_l0_count=11,l2_minus_l1_count=4,
                paired_comparison_denominator=40,
                added_l2_scenarios=new,
                source_comparator_proofs=proofs,
                limitations=['40 directed IMG0000 tuples only, not physical SEU population',
                             'stage mismatches reconstructed from verified TB counters, not independent per-byte waveform replay',
                             'same analyzer science rules as candidate G7 annex; no manual G7 approval',
                             'source/asset and launch provenance relies partly on historical ancestry, not full independent per-run E0 rebuild',
                             'two A/B configurations per target are not independent random samples'],
                rows=rows)


def csv_content(rows):
    cols=['scenario_id','sample_id','target_id','target_class','bit_index','injection_offset',
          'outcome','first_bad_stage','pool1_words','pool1_mismatch','pool1_status',
          'pool2_words','pool2_mismatch','pool2_status','logits_mismatch',
          'l0','l1','l2','raw_log_sha256','raw_log_path']
    buf=io.StringIO(newline='')
    w=csv.DictWriter(buf,fieldnames=cols,lineterminator='\n')
    w.writeheader()
    for r in rows:
        w.writerow({k:r[k] for k in cols})
    return buf.getvalue().encode('utf-8')


def report_text(a):
    return (f"# CNN_PEP V4M: historical IMG0000 G7-L2 analysis (CANDIDATE)\n\n"
            f"Source: SHA256-pinned original V4c raw XSim logs in handoff / repo. No new XSim.\n\n"
            f"| Observation level | TRUE | FALSE | INCONCLUSIVE |\n|---|---:|---:|---:|\n"
            + ''.join(f"| {level.upper()} | {a[level].get('TRUE',0)} | {a[level].get('FALSE',0)} | {a[level].get('INCONCLUSIVE',0)} |\n" for level in ('l0','l1','l2'))
            + "\nIncrement L1 minus L0: 11/40 (+27.5 percentage points).  Increment L2 minus L1: "
              "4/40 (+10.0 percentage points). Directed-only descriptive comparison.\n\n"
            + "New L2-only cases (both stages complete):\n\n"
            + "| Scenario | Target | Pool1 mismatches | Pool2 mismatches | Final outcome |\n|---|---|---:|---:|---|\n"
            + ''.join(f"| `{r['scenario_id']}` | {r['target_id']} | {r['pool1_mismatch']} (169/169 words) | {r['pool2_mismatch']} (25/25 words) | {r['outcome']} |\n" for r in a['rows'] if r['scenario_id'] in a['added_l2_scenarios'])
            + "\nEvidence caveat: counter checks in isolated BUF_0007/DAT_0002 TB are byte-identical "
              "to the frozen original TB. Actual stage values were not independently dumped and compared "
              "outside the TB. E3 was previously qualified by V4c matrix, not re-proven here. "
              "Two A/B scenarios per target are directed configurations, not independent random upsets.\n\n"
            + "G7 prepared/not approved. G6 production not qualified. FULL400 BLOCKED.\n")


def output_bytes(a):
    return {
        'v4m_g7_l2_summary.json':canonical({k:v for k,v in a.items() if k!='rows'}),
        'v4m_g7_l2_40.csv':csv_content(a['rows']),
        'V4M_G7_L2_REPORT.md':report_text(a).encode('utf8'),
    }


def create(out, files):
    out=Path(out)
    require(out.name.startswith('v4m_'), 'Output namespace must start with v4m_')
    require(not out.exists(), 'Output exists: refusing overwrite')
    require(not out.is_symlink(), 'Symlink output refused')
    out.mkdir(parents=True,exist_ok=False)
    inventory=''.join(sha(b)+'  '+name+'\n' for name,b in sorted(files.items())).encode('ascii')
    for name,b in {**files,'SHA256SUMS.txt':inventory}.items():
        (out/name).write_bytes(b)
    return out


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    s=p.add_mutually_exclusive_group(required=True)
    s.add_argument('--handoff-zip')
    s.add_argument('--repo-root')
    p.add_argument('--mode',choices=['check','analyze'],default='check')
    p.add_argument('--out-dir')
    args=p.parse_args(argv)
    try:
        require(args.mode!='analyze' or args.out_dir,'--out-dir required for analyze')
        src=Source(args.handoff_zip,args.repo_root)
        try: a=compute(src)
        finally: src.close()
        if args.mode=='analyze':
            dest=create(args.out_dir,output_bytes(a))
            print('V4M_G7_L2_OUTPUT:',dest)
        print('V4M_G7_L2_RAW_AUDIT_PASS: 40/40 SHA-pinned IMG0000 logs; '
              'L0=9 L1=20 L2=24; L2-only=4; 0 XSim; G7 NOT APPROVED; FULL400 BLOCKED')
        return 0
    except (ValueError,KeyError,FileNotFoundError,OSError,zipfile.BadZipFile) as e:
        print('V4M_G7_L2_FAIL_CLOSED:',e,file=sys.stderr)
        return 2

if __name__=='__main__':
    sys.exit(main())

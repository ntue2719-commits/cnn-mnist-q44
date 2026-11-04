#!/usr/bin/env python3
"""V3b DAT_0007/0008: value-aware FC accumulator -> sequential dot8 -> final logit proof.

Four frozen IMG0000 directed cases, one isolated XSim snapshot, no P0 RTL/
FI-TB/manifest edits. Check a bit0 deposit at the *pre-NBA* accepted FC edge,
all subsequent dot-product accumulator updates, and exactly +/-1 in the final
corresponding INT32 Q8.8 logit. This is NOT hardware SEU statistics or target
promotion. It does not imply a wrong classification label.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

FI_SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
sys.path.insert(0,str(FI_SCRIPTS))
from pilot400_common import FIELDS, sha256, load_plan, verify_manifest, write_csv, asset_lines, ASSET_SIZES
from pilot400_direct_xsim import make_cmd_run, call_cmd, batch_safe, classify_elaboration
from run_pilot400_target_validation_v3 import inspect_applied_and_result
from parse_fi_v2 import parse_one
from run_pilot400_control10 import validate_control10_gate
from run_pilot400 import validate_full_timing_gate
from run_mem1_v3b import FROZEN_HASHES, validate_v3a, override_prj, _cmd_stage

TARGETS={'DAT_0007':0,'DAT_0008':5}
EXPECTED={
    'FI_P400_0341':('DAT_0007','1325'),
    'FI_P400_0342':('DAT_0007','1330'),
    'FI_P400_0361':('DAT_0008','1325'),
    'FI_P400_0362':('DAT_0008','1330'),
}
SELECTED_IDS=tuple(EXPECTED)
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_REL=Path('fi/v3b/fcacc/tb')/TB_NAME
V3B_SNAPSHOT='tb_cnn_mnist_fi_pilot400_v3b_fcacc_behav'
PROBE_TB_SHA256='a031fade99471ae24d6b53230925f09e5f02819198e4d7d6b40f2ebd86bd71cf'
COLUMNS=(*FIELDS,'fc_class','source_cycle','inject_cycle','final_cycle',
         'accepted_steps','original_acc','corrupted_acc','final_logit',
         'golden_logit','logit_delta','consumer_proof','outcome','log_sha256')

PREFIX='FI_V3B_FCACC_'
PATTERN={
 'SOURCE':r' class=(\d+) cycle=(-?\d+) acc=([0-9a-fA-F]{8})',
 'INJECT':r' class=(\d+) cycle=(\d+) bit=(\d+) before=([0-9a-fA-F]{8}) after=([0-9a-fA-F]{8})',
 'ACCEPT':r' class=(\d+) cycle=(\d+) seq=(\d+) counter=(\d+) pre=([0-9a-fA-F]{8}) dot=([0-9a-fA-F]{8}) post=([0-9a-fA-F]{8}) final=([01])',
 'LOGIT':r' class=(\d+) cycle=(\d+) actual=([0-9a-fA-F]{8}) golden=([0-9a-fA-F]{8}) delta=([0-9a-fA-F]{8})',
 'SUMMARY':r' class=(\d+) source=(-?\d+) inject=(\d+) first=(\d+) final=(\d+) accepts=(\d+) before=([0-9a-fA-F]{8}) after=([0-9a-fA-F]{8}) value=([0-9a-fA-F]{8}) expected=([0-9a-fA-F]{8})',
}
EVENT_RE={key:re.compile(r'^'+PREFIX+key+r' scenario=(\S+) target=(\S+)'+suffix+r'$', re.M)
          for key,suffix in PATTERN.items()}


def validate_frozen_inputs(repo:Path)->None:
    for relative,expected in FROZEN_HASHES.items():
        p=repo/relative
        if not p.is_file() or sha256(p)!=expected:
            raise ValueError('Frozen Pilot400 source changed: '+relative)
    original=(repo/'fi/tb'/TB_NAME).read_text(encoding='utf-8')
    tb=repo/TB_REL
    if not tb.is_file() or sha256(tb)!=PROBE_TB_SHA256:
        raise ValueError('Isolated FC accumulator TB SHA256 mismatch or missing')
    code=tb.read_text(encoding='utf-8')
    for name in ('MONITOR','SUMMARY'):
        indent='    ' if name=='MONITOR' else '        '
        exp=(r'(?ms)^'+indent+r'// BEGIN V3B_FCACC_ISOLATED_'+name+
             r'\n.*?^'+indent+r'// END V3B_FCACC_ISOLATED_'+name+r'\n\n')
        code,n=re.subn(exp,'',code)
        if n!=1:
            raise ValueError('Missing or duplicated FC monitor block: '+name)
    if code!=original:
        raise ValueError('Isolated FC testbench changes frozen behavior outside monitor blocks')
    full=tb.read_text(encoding='utf-8')
    for snippet in ('accumulator[0]','accumulator[5]','dot0','dot5',
                    'o_logits[191:160]','FI_V3B_FCACC_ACCEPT scenario=',
                    'FI_V3B_FCACC_FINAL_LOGIT_NOT_PROVED'):
        if snippet not in full:
            raise ValueError('Missing required FC consumer or safety assertion: '+snippet)


def selected_fcacc(manifest:list[dict[str,str]])->list[dict[str,str]]:
    rows=sorted((r for r in manifest if r['target_id'] in TARGETS and r['sample_id']=='IMG0000'),
                key=lambda r:r['scenario_id'])
    if len(rows)!=4 or tuple(r['scenario_id'] for r in rows)!=SELECTED_IDS:
        raise ValueError('Frozen FC selected scenario identities changed')
    for row in rows:
        tid,offset=EXPECTED[row['scenario_id']]
        if row['target_id']!=tid or row['injection_offset']!=offset or \
           row['target_class']!='DAT' or row['bit_index']!='0':
            raise ValueError('Frozen FC class/bit/offset changed: '+row['scenario_id'])
    return rows


def parse_consumer(log:Path,row:dict[str,str],original_v3a_log:Path|None=None)->dict[str,str]:
    base=inspect_applied_and_result(log,row)
    parsed=parse_one(log)
    sid=row['scenario_id'];tid=row['target_id']
    if sid not in EXPECTED or (tid,row['injection_offset'])!=EXPECTED[sid] or \
       row['sample_id']!='IMG0000' or row['bit_index']!='0' or row['target_class']!='DAT':
        raise ValueError('Only four frozen FC accumulator IMG0000 scenarios accepted')
    text=log.read_text(encoding='utf-8',errors='replace')
    matches={key:list(pattern.finditer(text)) for key,pattern in EVENT_RE.items()}
    if any(len(matches[k])!=1 for k in ('SOURCE','INJECT','LOGIT','SUMMARY')):
        raise ValueError('Missing or duplicate FC source/injection/logit/summary')
    steps=matches['ACCEPT']
    if not 2<=len(steps)<=25:
        raise ValueError('Expected 2..25 accepted FC accumulator updates')
    monitor_lines=[line for line in text.splitlines() if line.startswith(PREFIX)]
    if len(monitor_lines)!=len(steps)+4:
        raise ValueError('Unexpected/malformed FC monitor marker, failure or duplicate')
    source=matches['SOURCE'][0];inj=matches['INJECT'][0]
    final=matches['LOGIT'][0];summ=matches['SUMMARY'][0]
    ordered=[source,inj,*steps,final,summ]
    if [m.start() for m in ordered]!=sorted(m.start() for m in ordered):
        raise ValueError('FC monitor event order invalid')
    groups=[m.groups() for m in ordered]
    if any(g[:2]!=(sid,tid) for g in groups):
        raise ValueError('FC consumer marker scenario/target mismatch')
    fc_class=TARGETS[tid]
    if any(int(g[2])!=fc_class for g in groups):
        raise ValueError('FC class selection mismatch')
    src_cycle=int(source.group(4));original=int(source.group(5),16)
    injection_cycle=int(inj.group(4));bit=int(inj.group(5))
    before=int(inj.group(6),16);after=int(inj.group(7),16)
    if (src_cycle<0 or src_cycle>=injection_cycle or bit!=0 or
        original!=before or (before^after)!=1 or
        injection_cycle!=int(parsed['requested_cycle']) or
        injection_cycle!=int(parsed['actual_cycle'])):
        raise ValueError('FC producer/injection/readback chain invalid')
    applies=re.findall(r'^FI_APPLIED scenario=\S+ target=\S+ class=\S+ bit=\d+ old=(\d) new=(\d) requested=\d+$',text,re.M)
    if len(applies)!=1 or (int(applies[0][0]),int(applies[0][1]))!=(before&1,after&1):
        raise ValueError('FC monitor XOR word not tied to FI_APPLIED readback')
    last_cycle=injection_cycle-1
    last_counter=None
    last_post=None
    for index,match in enumerate(steps):
        g=match.groups()
        cycle=int(g[3]);seq=int(g[4]);counter=int(g[5]);pre=int(g[6],16)
        dot=int(g[7],16);post=int(g[8],16);is_final=int(g[9])
        if (seq!=index or cycle<=last_cycle or counter>24 or
            (index==0 and (cycle!=injection_cycle or counter==24 or pre!=after)) or
            (index>0 and (counter!=last_counter+1 or pre!=last_post)) or
            post!=((pre+dot)&0xffffffff) or is_final!=int(index==len(steps)-1)):
            raise ValueError('FC accepted arithmetic/value chain invalid')
        if is_final and counter!=24:
            raise ValueError('Final FC update not from counter24')
        if not is_final and counter==24:
            raise ValueError('Premature FC final counter')
        last_cycle=cycle;last_counter=counter;last_post=post
    final_cycle=int(final.group(4));actual=int(final.group(5),16)
    golden=int(final.group(6),16);delta=int(final.group(7),16)
    expected_delta=1 if not(before&1) else 0xffffffff
    expected=(golden+expected_delta)&0xffffffff
    if (final_cycle!=last_cycle or actual!=last_post or actual!=expected or
        delta!=expected_delta or actual==golden):
        raise ValueError('FC registered logit +/-1 propagation not proved')
    sg=summ.groups()
    if ([int(x) for x in sg[3:8]] !=
        [src_cycle,injection_cycle,injection_cycle,final_cycle,len(steps)] or
        [int(x,16) for x in sg[8:]] != [before,after,actual,expected]):
        raise ValueError('FC summary inconsistent with accepted consumer chain')
    if original_v3a_log is not None and parsed!=parse_one(original_v3a_log):
        raise ValueError(f'{sid}: V3b changed original FI_RESULT_V2 outcome')
    return {k:row[k] for k in FIELDS}|{
        'fc_class':str(fc_class),'source_cycle':str(src_cycle),
        'inject_cycle':str(injection_cycle),'final_cycle':str(final_cycle),
        'accepted_steps':str(len(steps)),'original_acc':f'{before:08x}',
        'corrupted_acc':f'{after:08x}', 'final_logit':f'{actual:08x}',
        'golden_logit':f'{golden:08x}','logit_delta':f'{delta:08x}',
        'consumer_proof':'VERIFIED_FC_ACCUMULATOR_READ_DOT8_UPDATES_AND_FINAL_LOGIT_DELTA',
        'outcome':base['outcome'],'log_sha256':sha256(log)}


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--vivado',type=Path,required=True)
    ap.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_v3b_fcacc_01'))
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--execute',action='store_true')
    args=ap.parse_args();repo=args.repo.resolve()
    loc=lambda p:(p if p.is_absolute() else repo/p).resolve()
    v3a,out,vivado=loc(args.v3a_dir),loc(args.out_dir),loc(args.vivado)
    if out.exists():raise ValueError('Refusing to overwrite evidence dir: '+str(out))
    validate_frozen_inputs(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    v3a_meta,_,source_prj=validate_v3a(repo,v3a,manifest)
    selected=selected_fcacc(manifest)
    for name,(count,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,count,digits)
    if args.timeout_seconds<10:raise ValueError('Timeout must be >=10 seconds')
    out.mkdir(parents=True,exist_ok=False)
    csv_name='selected_fcacc_scenarios.csv'
    write_csv(out/csv_name,FIELDS,selected)
    record={'schema':'PILOT400_V3B_FCACC_CONSUMER_V1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'PREPARED_NOT_EXECUTED','source_authority':'vivado',
            'targets':list(TARGETS),'selected_count':len(selected),
            'original_v3a_manifest_sha256':sha256(v3a/'validation_manifest.json'),
            'original_v3a_prj_sha256':sha256(source_prj),
            'original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
            'probe_tb_sha256':sha256(repo/TB_REL),
            'selected_sha256':sha256(out/csv_name),
            'records':[]}
    try:
        if not args.execute:
            print('V3B_FCACC_DRY_RUN_PASS: 4 frozen DAT_0007/0008 scenarios; 0 XSim runs; original P0/TB untouched',flush=True)
            return
        if os.name!='nt':raise ValueError('XSim execution requires Windows')
        if not vivado.is_file():raise ValueError('Vivado .bat not found: '+str(vivado))
        weights=loc(args.weights_dir);audit=loc(args.timing_audit);ctrl=loc(args.control10_manifest)
        validate_control10_gate(repo,ctrl,weights)
        validate_full_timing_gate(repo,audit,{r['target_id']:r for r in plan})
        record['timing_audit_sha256']=sha256(audit)
        record['control10_manifest_sha256']=sha256(ctrl)
        sources=v3a_meta['snapshot_build']['source_hashes']
        if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in sources.items()):
            raise ValueError('Vivado authoritative HDL differs from V3a snapshot')
        record['authoritative_source_hashes']=sources
        root=vivado.parent.parent
        build=out/'isolated_fcacc_build';build.mkdir()
        tb=repo/TB_REL
        vprj=build/'tb_v3b_fcacc_vlog.prj'
        vprj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),tb),encoding='utf-8')
        record['probe_prj_sha256']=sha256(vprj)
        cmds={
          'xvlog':f'--relax -prj "{batch_safe(vprj.resolve().as_posix())}"',
          'xelab':(' --debug typical --relax --mt 2 '
                   '-L xil_defaultlib -L unisims_ver -L unimacro_ver -L secureip '
                   f'--snapshot {V3B_SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                   'xil_defaultlib.glbl -log elaborate_v3b_fcacc.log'),
        }
        for stage,cmd in cmds.items():
            stage_log=out/(stage+'.log');stage_cmd=out/(stage+'.cmd')
            stage_cmd.write_text(_cmd_stage(build,root,stage,cmd,stage_log),encoding='ascii')
            call_cmd(stage_cmd,build,out/(stage+'.wrapper.log'),args.timeout_seconds,'V3b FC '+stage)
            t=stage_log.read_text(encoding='utf-8',errors='replace') if stage_log.is_file() else ''
            if not t.strip() or any(ln.startswith(('ERROR:','FATAL:')) for ln in t.splitlines()):
                raise ValueError('FC compile/elaboration error: '+str(stage_log))
            record[stage+'_log_sha256']=sha256(stage_log)
        record['elaboration_mode']=classify_elaboration(
            (out/'xelab.log').read_text(errors='replace'),build/'xsim.dir'/V3B_SNAPSHOT,
            None,expected_snapshot=V3B_SNAPSHOT)
        tcl=out/'direct_runall.tcl';tcl.write_text('run all\nquit\n',encoding='ascii')
        (out/'logs').mkdir()
        records=[]
        for n,row in enumerate(selected,1):
            sid=row['scenario_id'];log=out/'logs'/(sid+'.log');console=out/(sid+'.console.log')
            script=make_cmd_run(build,root,row,repo/'fi/pilot400/samples/IMG0000',tcl,log,console,profile_only=False)
            old='tb_cnn_mnist_fi_pilot400_behav'
            if script.count(f' {old} ')!=1:
                raise ValueError('Unexpected XSim command snapshot format')
            script=script.replace(f' {old} ',f' {V3B_SNAPSHOT} ')
            cmd_file=out/(sid+'.cmd');cmd_file.write_text(script,encoding='ascii')
            print(f'V3B_FCACC_XSIM {n}/4 {sid} {row["target_id"]} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd_file,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V3b '+sid)
            verified=parse_consumer(log,row,v3a/'logs'/(sid+'.log'))
            records.append(verified)
            record['records'].append({'scenario_id':sid,'target_id':row['target_id'],
                                      'log':str(log),'log_sha256':sha256(log),
                                      'consumer_proof':verified['consumer_proof']})
            if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in sources.items()):
                raise ValueError('Vivado P0 source changed during V3b')
            if sha256(tb)!=record['probe_tb_sha256'] or sha256(vprj)!=record['probe_prj_sha256']:
                raise ValueError('FC instrumented TB/source list changed during V3b')
            print(f'V3B_FCACC_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,records)
        record['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        record['status']='CONSUMER_PROVED_4_OF_4'
        print('V3B_FCACC_CONSUMER_PASS: 4/4 XSim scenarios; FC accumulator read PRE-NBA, all accepted dot8 updates and final registered +/-1 logit verified; NOT automatic promotion',flush=True)
    except Exception:
        record['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v3b_fcacc_manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as err:
        raise SystemExit('V3B_FCACC_FAIL: '+str(err))

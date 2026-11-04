#!/usr/bin/env python3
"""Isolated V3b BUF_0007 corrupted-byte propagation to Conv2 MAC (2 frozen IMG0000 cases).

DO NOT modify locked FI testbench or synthesizable RTL. Uses authentic V3a source list,
changes only the TB path, builds a separate XSim snapshot and validates exact log chronology.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

FI_SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
sys.path.insert(0,str(FI_SCRIPTS))
from pilot400_common import FIELDS, sha256, load_plan, verify_manifest, write_csv, asset_lines, ASSET_SIZES
from pilot400_direct_xsim import project_source_hashes, make_cmd_run, call_cmd, batch_safe, classify_elaboration
from run_pilot400_target_validation_v3 import inspect_applied_and_result
from parse_fi_v2 import parse_one
from run_pilot400_control10 import validate_control10_gate
from run_pilot400 import validate_full_timing_gate
from run_mem1_v3b import FROZEN_HASHES, validate_v3a, override_prj, _cmd_stage

TARGET_ID='BUF_0007'
SELECTED_IDS=('FI_P400_0101','FI_P400_0102')
EXPECTED_OFFSETS=('791','792')
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_REL=Path('fi/v3b/buf0007/tb')/TB_NAME
V3B_SNAPSHOT='tb_cnn_mnist_fi_pilot400_v3b_buf0007_behav'
COLUMNS=(*FIELDS,'write_cycle','inject_cycle','drain_cycle','memwrite_cycle',
         'memread_cycle','capture_cycle','window_cycle','consume_cycle',
         'original_byte','corrupted_byte','drain_word','consumer_proof','outcome','log_sha256')

W_RE=re.compile(r'^FI_V3B_BUF0007_WRITE scenario=(\S+) target=(\S+) cycle=(\d+) byte=([0-9a-fA-F]{2})$',re.M)
I_RE=re.compile(r'^FI_V3B_BUF0007_INJECT scenario=(\S+) target=(\S+) cycle=(\d+) bit=(\d+) before=([0-9a-fA-F]{2}) after=([0-9a-fA-F]{2})$',re.M)
D_RE=re.compile(r'^FI_V3B_BUF0007_DRAIN scenario=(\S+) target=(\S+) cycle=(\d+) word=([0-9a-fA-F]{8})$',re.M)
M_RE=re.compile(r'^FI_V3B_BUF0007_MEM_WRITE scenario=(\S+) target=(\S+) cycle=(\d+) word=([0-9a-fA-F]{8})$',re.M)
R_RE=re.compile(r'^FI_V3B_BUF0007_MEM_READ scenario=(\S+) target=(\S+) cycle=(\d+) word=([0-9a-fA-F]{8})$',re.M)
C_RE=re.compile(r'^FI_V3B_BUF0007_CAPTURE scenario=(\S+) target=(\S+) cycle=(\d+) byte337=([0-9a-fA-F]{2})$',re.M)
E_RE=re.compile(r'^FI_V3B_BUF0007_WINDOW scenario=(\S+) target=(\S+) cycle=(\d+) byte=([0-9a-fA-F]{2})$',re.M)
K_RE=re.compile(r'^FI_V3B_BUF0007_CALC_CONSUME scenario=(\S+) target=(\S+) cycle=(\d+) byte=([0-9a-fA-F]{2})$',re.M)
S_RE=re.compile(r'^FI_V3B_BUF0007_SUMMARY scenario=(\S+) target=(\S+) write=(\d+) inject=(\d+) drain=(\d+) memwrite=(\d+) memread=(\d+) capture=(\d+) window=(\d+) consume=(\d+) overwrites=(\d+) original=([0-9a-fA-F]{2}) corrupt=([0-9a-fA-F]{2}) word=([0-9a-fA-F]{8})$',re.M)
PATTERNS=(W_RE,I_RE,D_RE,M_RE,R_RE,C_RE,E_RE,K_RE,S_RE)


def validate_frozen_inputs(repo:Path)->None:
    for relative,expected in FROZEN_HASHES.items():
        p=repo/relative
        if not p.is_file() or sha256(p)!=expected:
            raise ValueError('Frozen Pilot400 source changed: '+relative)
    tb=repo/TB_REL
    if not tb.is_file() or sha256(tb)==FROZEN_HASHES['fi/tb/'+TB_NAME]:
        raise ValueError('Missing or uninstrumented isolated BUF_0007 testbench')
    text=tb.read_text(encoding='utf-8')
    if (text.count('module tb_cnn_mnist_fi_pilot400;')!=1 or
        text.count('FI_V3B_BUF0007_CALC_CONSUME scenario=')!=1 or
        'i_window[143:136]' not in text):
        raise ValueError('Expected isolated BUF_0007 consumer monitor not found')


def selected_buf0007(manifest:list[dict[str,str]])->list[dict[str,str]]:
    rows=[r for r in manifest if r['target_id']==TARGET_ID and r['sample_id']=='IMG0000']
    rows.sort(key=lambda r:r['scenario_id'])
    if (len(rows)!=2 or tuple(r['scenario_id'] for r in rows)!=SELECTED_IDS or
        tuple(r['injection_offset'] for r in rows)!=EXPECTED_OFFSETS or
        any(r['bit_index']!='0' or r['target_class']!='BUF' for r in rows)):
        raise ValueError('Frozen BUF_0007 scenario selection is not exactly 2 expected cases')
    return rows


def parse_consumer(log:Path,row:dict[str,str],original_v3a_log:Path|None=None)->dict[str,str]:
    base=inspect_applied_and_result(log,row)
    res=parse_one(log)
    if (row['target_id']!=TARGET_ID or row['sample_id']!='IMG0000' or
        row['bit_index']!='0' or row['scenario_id'] not in SELECTED_IDS):
        raise ValueError('Only the two frozen BUF_0007 IMG0000 bit0 cases are supported')
    text=log.read_text(encoding='utf-8',errors='replace')
    names=('WRITE','INJECT','DRAIN','MEM_WRITE','MEM_READ','CAPTURE','WINDOW','CALC_CONSUME','SUMMARY')
    vals=[];positions=[]
    for name,p in zip(names,PATTERNS):
        matches=list(p.finditer(text))
        if len(matches)!=1:raise ValueError(f'{row["scenario_id"]}: require exactly one {name} event')
        m=matches[0];vals.append(m.groups());positions.append(m.start())
    if positions!=sorted(positions):
        raise ValueError('Consumer-event log order invalid')
    sid=row['scenario_id'];tid=TARGET_ID
    if any(r[:2]!=(sid,tid) for r in vals):
        raise ValueError('Consumer marker scenario/target identity changed')
    w,inj,drain,mwrite,mread,cap,window,consume,summary=vals
    cycles=[int(w[2]),int(inj[2]),int(drain[2]),int(mwrite[2]),int(mread[2]),
            int(cap[2]),int(window[2]),int(consume[2])]
    if cycles!=sorted(set(cycles)) or cycles[-1]!=cycles[-2]+1:
        raise ValueError('Consumer timeline incomplete/reordered/repeated')
    original=int(w[3],16);before=int(inj[4],16);mutated=int(inj[5],16)
    word=int(drain[3],16)
    if (int(inj[3])!=0 or original!=before or original^mutated!=1 or
        word>>8&0xff!=mutated or int(mwrite[3],16)!=word or
        int(mread[3],16)!=word or
        any(int(x[3],16)!=mutated for x in (cap,window,consume)) or
        int(res['requested_cycle'])!=cycles[1] or
        int(res['actual_cycle'])!=cycles[1] or
        [int(v) for v in summary[2:10]]!=cycles or
        int(summary[10])!=0 or int(summary[11],16)!=original or
        int(summary[12],16)!=mutated or int(summary[13],16)!=word):
        raise ValueError('BUF_0007 changed bit not proved across stages')
    if any(x in text for x in ('FI_V3B_BUF0007_CONSUMER_NOT_PROVED',
                               'FI_V3B_BUF0007_TIMELINE_INCOMPLETE',
                               'FI_V3B_BUF0007_INJECT_NOT_PROVED')):
        raise ValueError('V3b consumer failure marker present')
    if original_v3a_log is not None and res!=parse_one(original_v3a_log):
        raise ValueError('V3b monitor changed original FI_RESULT_V2 compared to V3a')
    return {k:row[k] for k in FIELDS}|{
        'write_cycle':str(cycles[0]),'inject_cycle':str(cycles[1]),
        'drain_cycle':str(cycles[2]),'memwrite_cycle':str(cycles[3]),
        'memread_cycle':str(cycles[4]),'capture_cycle':str(cycles[5]),
        'window_cycle':str(cycles[6]),'consume_cycle':str(cycles[7]),
        'original_byte':f'{original:02x}','corrupted_byte':f'{mutated:02x}',
        'drain_word':f'{word:08x}',
        'consumer_proof':'VERIFIED_BUF337_TO_CONV2_MAC_CH1_P8',
        'outcome':base['outcome'],'log_sha256':sha256(log)}


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--vivado',type=Path,required=True)
    ap.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_v3b_buf0007_01'))
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
    selected=selected_buf0007(manifest)
    for name,(count,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,count,digits)
    if args.timeout_seconds<10:raise ValueError('Timeout must be >= 10 seconds')
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_buf0007_scenarios.csv',FIELDS,selected)
    record={'schema':'PILOT400_V3B_BUF0007_CONSUMER_V1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'PREPARED_NOT_EXECUTED','source_authority':'vivado',
            'original_v3a_manifest_sha256':sha256(v3a/'validation_manifest.json'),
            'original_v3a_prj_sha256':sha256(source_prj),
            'original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
            'probe_tb_sha256':sha256(repo/TB_REL),
            'selected_sha256':sha256(out/'selected_buf0007_scenarios.csv'),
            'records':[]}
    try:
        if not args.execute:
            print('V3B_BUF0007_DRY_RUN_PASS: 2 frozen scenarios; 0 XSim runs, original P0/TB unchanged',flush=True)
            return
        if os.name!='nt':raise ValueError('XSim execution requires Windows')
        if not vivado.is_file():raise ValueError('Vivado .bat not found: '+str(vivado))
        weights=loc(args.weights_dir);audit=loc(args.timing_audit);ctrl=loc(args.control10_manifest)
        validate_control10_gate(repo,ctrl,weights)
        validate_full_timing_gate(repo,audit,{r['target_id']:r for r in plan})
        record['timing_audit_sha256']=sha256(audit)
        record['control10_manifest_sha256']=sha256(ctrl)
        original_sources=v3a_meta['snapshot_build']['source_hashes']
        root=vivado.parent.parent
        build=out/'isolated_buf0007_build';build.mkdir()
        tb=repo/TB_REL
        vprj=build/'tb_v3b_buf0007_vlog.prj'
        vprj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),tb),encoding='utf-8')
        record['probe_prj_sha256']=sha256(vprj)
        cmds={
           'xvlog':f'--relax -prj "{batch_safe(vprj.resolve().as_posix())}"',
           'xelab':(' --debug typical --relax --mt 2 '
                    '-L xil_defaultlib -L unisims_ver -L unimacro_ver -L secureip '
                    f'--snapshot {V3B_SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                    'xil_defaultlib.glbl -log elaborate_v3b_buf0007.log')}
        for stage,cmd in cmds.items():
            stage_log=out/(stage+'.log');stage_cmd=out/(stage+'.cmd')
            stage_cmd.write_text(_cmd_stage(build,root,stage,cmd,stage_log),encoding='ascii')
            call_cmd(stage_cmd,build,out/(stage+'.wrapper.log'),args.timeout_seconds,'V3b BUF '+stage)
            t=stage_log.read_text(encoding='utf-8',errors='replace') if stage_log.is_file() else ''
            if not t.strip() or any(ln.startswith(('ERROR:','FATAL:')) for ln in t.splitlines()):
                raise ValueError('Compile/elaboration error: '+str(stage_log))
            record[stage+'_log_sha256']=sha256(stage_log)
        snapshot=build/'xsim.dir'/V3B_SNAPSHOT
        record['elaboration_mode']=classify_elaboration(
            (out/'xelab.log').read_text(errors='replace'),snapshot,None,expected_snapshot=V3B_SNAPSHOT)
        tcl=out/'direct_runall.tcl';tcl.write_text('run all\nquit\n',encoding='ascii')
        (out/'logs').mkdir()
        records=[]
        for n,row in enumerate(selected,1):
            sid=row['scenario_id'];log=out/'logs'/(sid+'.log');console=out/(sid+'.console.log')
            script=make_cmd_run(build,root,row,repo/'fi/pilot400/samples/IMG0000',tcl,log,console,profile_only=False)
            default_snapshot='tb_cnn_mnist_fi_pilot400_behav'
            if script.count(f' {default_snapshot} ')!=1:
                raise ValueError('Unexpected direct-XSim command snapshot format')
            script=script.replace(f' {default_snapshot} ',f' {V3B_SNAPSHOT} ')
            cmd_file=out/(sid+'.cmd');cmd_file.write_text(script,encoding='ascii')
            print(f'V3B_BUF0007_XSIM {n}/2 {sid} {TARGET_ID} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd_file,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V3b '+sid)
            verified=parse_consumer(log,row,v3a/'logs'/(sid+'.log'))
            records.append(verified)
            record['records'].append({'scenario_id':sid,'log':str(log),'log_sha256':sha256(log)})
            if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in original_sources.items()):
                raise ValueError('Authoritative Vivado P0 source changed during V3b')
            if sha256(tb)!=record['probe_tb_sha256'] or sha256(vprj)!=record['probe_prj_sha256']:
                raise ValueError('Isolated TB or source list changed during V3b')
            print(f'V3B_BUF0007_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,records)
        record['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        record['status']='CONSUMER_PROVED_2_OF_2'
        print('V3B_BUF0007_CONSUMER_PASS: 2/2 XSim scenarios; corrupted reorder byte337 accepted at Conv2 MAC ch1 P8; NOT automatic promotion',flush=True)
    except Exception:
        record['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v3b_buf0007_manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as err:
        raise SystemExit('V3B_BUF0007_FAIL: '+str(err))

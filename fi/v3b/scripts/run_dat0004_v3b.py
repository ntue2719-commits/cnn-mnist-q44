#!/usr/bin/env python3
"""Isolated V3b DAT_0004 Conv2 registered raw accumulator propagation into Quant+ReLU (2 frozen IMG0000 cases).

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

TARGET_ID='DAT_0004'
SELECTED_IDS=('FI_P400_0321','FI_P400_0322')
EXPECTED_OFFSETS=('1200','1220')
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_REL=Path('fi/v3b/dat0004/tb')/TB_NAME
V3B_SNAPSHOT='tb_cnn_mnist_fi_pilot400_v3b_dat0004_behav'
COLUMNS=(*FIELDS,'producer_cycle','inject_cycle','quant_accept_cycle',
         'quant_capture_cycle','original_lane0','corrupted_lane0','quant_relu_byte',
         'concurrent_producer','overwritten_before_consume',
         'consumer_proof','outcome','log_sha256')

# Fail-closed full-line machine-readable events. No bare substring PASS checks.
P_RE=re.compile(r'^FI_V3B_DAT0004_PRODUCE scenario=(\S+) target=(\S+) cycle=(\d+) lane0=([0-9a-fA-F]{8})$',re.M)
I_RE=re.compile(r'^FI_V3B_DAT0004_INJECT scenario=(\S+) target=(\S+) cycle=(\d+) bit=(\d+) before=([0-9a-fA-F]{8}) after=([0-9a-fA-F]{8})$',re.M)
A_RE=re.compile(r'^FI_V3B_DAT0004_QUANT_ACCEPT scenario=(\S+) target=(\S+) cycle=(\d+) raw=([0-9a-fA-F]{8}) relu=([0-9a-fA-F]{2})$',re.M)
C_RE=re.compile(r'^FI_V3B_DAT0004_QUANT_CAPTURE scenario=(\S+) target=(\S+) cycle=(\d+) valid=([01]) relu=([0-9a-fA-F]{2}) concurrent_producer=([01])$',re.M)
S_RE=re.compile(r'^FI_V3B_DAT0004_SUMMARY scenario=(\S+) target=(\S+) inject=(\d+) consume=(\d+) capture=(\d+) overwritten_before=(\d+) concurrent_producer=([01]) before=([0-9a-fA-F]{8}) after=([0-9a-fA-F]{8}) relu=([0-9a-fA-F]{2})$',re.M)
EVENT_RE={'PRODUCE':P_RE,'INJECT':I_RE,'QUANT_ACCEPT':A_RE,'QUANT_CAPTURE':C_RE,'SUMMARY':S_RE}
PROBE_TB_SHA256='78437cef3d16da086ee75ff72edc9b9209c0209994a67ae8fd6255006b6bc5ba'


def validate_frozen_inputs(repo:Path)->None:
    for rel,expected in FROZEN_HASHES.items():
        p=repo/rel
        if not p.is_file() or sha256(p)!=expected:
            raise ValueError('Frozen Pilot400 source changed: '+rel)
    tb=repo/TB_REL
    if not tb.is_file() or sha256(tb)!=PROBE_TB_SHA256:
        raise ValueError('Isolated DAT_0004 TB SHA256 mismatch or file missing')
    text=tb.read_text(encoding='utf-8')
    if (text.count('module tb_cnn_mnist_fi_pilot400;')!=1 or
        text.count('FI_V3B_DAT0004_QUANT_ACCEPT scenario=')!=1 or
        text.count('FI_V3B_DAT0004_QUANT_CAPTURE scenario=')!=1 or
        'u_conv2_quant_relu.i_acc' not in text or
        'u_conv2_quant_relu.relu_bus' not in text):
        raise ValueError('Expected isolated DAT_0004 value-aware monitor missing')


def selected_dat0004(manifest:list[dict[str,str]])->list[dict[str,str]]:
    rows=[r for r in manifest if r['target_id']==TARGET_ID and r['sample_id']=='IMG0000']
    rows.sort(key=lambda r:r['scenario_id'])
    if (len(rows)!=2 or tuple(r['scenario_id'] for r in rows)!=SELECTED_IDS or
        tuple(r['injection_offset'] for r in rows)!=EXPECTED_OFFSETS or
        any(r['bit_index']!='0' or r['target_class']!='DAT' for r in rows)):
        raise ValueError('Frozen DAT_0004 scenario selection differs from 2 expected cases')
    return rows


def parse_consumer(log:Path,row:dict[str,str],original_v3a_log:Path|None=None)->dict[str,str]:
    # Mechanism strict parser rejects duplicates/missing FI_RESULT_V2 and
    # checks a real applied single-bit XOR and golden-control baseline.
    base=inspect_applied_and_result(log,row)
    parsed=parse_one(log)
    if (row['target_id']!=TARGET_ID or row['sample_id']!='IMG0000' or
        row['bit_index']!='0' or row['scenario_id'] not in SELECTED_IDS or
        row['injection_offset']!=EXPECTED_OFFSETS[SELECTED_IDS.index(row['scenario_id'])]):
        raise ValueError('Only frozen DAT_0004 IMG0000 bit0 pair supported')
    text=log.read_text(encoding='utf-8',errors='replace')
    # There must be exactly five actual monitor event lines and no other
    # FI_V3B_DAT0004 marker (especially failure/timeout messages).
    actual=[line for line in text.splitlines() if line.startswith('FI_V3B_DAT0004_')]
    if len(actual)!=len(EVENT_RE):
        raise ValueError('Missing, duplicate or unexpected DAT_0004 event/failure marker')
    matched=[];positions=[]
    for name,pat in EVENT_RE.items():
        found=list(pat.finditer(text))
        if len(found)!=1:
            raise ValueError(f'{row["scenario_id"]}: require exactly one {name} proof')
        matched.append(found[0].groups())
        positions.append(found[0].start())
    if positions!=sorted(positions):
        raise ValueError('DAT_0004 event order invalid')
    sid=row['scenario_id']
    if any(v[:2]!=(sid,TARGET_ID) for v in matched):
        raise ValueError('DAT_0004 event scenario/target mismatch')
    prod,inj,accept,capture,summ=matched
    produced=int(prod[2]);inject=int(inj[2]);accepted=int(accept[2]);captured=int(capture[2]);
    before=int(inj[4],16);after=int(inj[5],16);relu=int(accept[4],16)
    concurrent=int(capture[5]);overwrite=int(summ[5])
    valid=(
        produced==inject-1 and inject==accepted==captured and
        int(inj[3])==0 and (before^after)==1 and
        int(prod[3],16)==before and int(accept[3],16)==after and
        int(capture[3])==1 and int(capture[4],16)==relu and
        [int(v) for v in summ[2:5]]==[inject,accepted,captured] and
        overwrite==0 and concurrent==int(summ[6]) and
        int(summ[7],16)==before and int(summ[8],16)==after and
        int(summ[9],16)==relu and
        int(parsed['requested_cycle'])==inject and int(parsed['actual_cycle'])==inject)
    if not valid:
        raise ValueError('DAT_0004 mutated raw accumulator not proved at quant accepted input')
    if original_v3a_log is not None and parsed!=parse_one(original_v3a_log):
        raise ValueError(f'{sid}: isolated V3b changed original FI_RESULT_V2 vs V3a')
    return {k:row[k] for k in FIELDS}|{
        'producer_cycle':str(produced),'inject_cycle':str(inject),
        'quant_accept_cycle':str(accepted),'quant_capture_cycle':str(captured),
        'original_lane0':f'{before:08x}','corrupted_lane0':f'{after:08x}',
        'quant_relu_byte':f'{relu:02x}',
        'concurrent_producer':str(concurrent),
        'overwritten_before_consume':str(overwrite),
        'consumer_proof':'VERIFIED_CONV2_RAW_ACC_TO_QUANT_INPUT_AND_REGISTERED_CAPTURE',
        'outcome':base['outcome'],'log_sha256':sha256(log)}


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--vivado',type=Path,required=True)
    ap.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_v3b_dat0004_01'))
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
    selected=selected_dat0004(manifest)
    for name,(count,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,count,digits)
    if args.timeout_seconds<10:raise ValueError('Timeout must be >= 10 seconds')
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_dat0004_scenarios.csv',FIELDS,selected)
    record={'schema':'PILOT400_V3B_DAT0004_CONSUMER_V1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'PREPARED_NOT_EXECUTED','source_authority':'vivado',
            'original_v3a_manifest_sha256':sha256(v3a/'validation_manifest.json'),
            'original_v3a_prj_sha256':sha256(source_prj),
            'original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
            'probe_tb_sha256':sha256(repo/TB_REL),
            'selected_sha256':sha256(out/'selected_dat0004_scenarios.csv'),
            'records':[]}
    try:
        if not args.execute:
            print('V3B_DAT0004_DRY_RUN_PASS: 2 frozen scenarios; 0 XSim runs, original P0/TB unchanged',flush=True)
            return
        if os.name!='nt':raise ValueError('XSim execution requires Windows')
        if not vivado.is_file():raise ValueError('Vivado .bat not found: '+str(vivado))
        weights=loc(args.weights_dir);audit=loc(args.timing_audit);ctrl=loc(args.control10_manifest)
        validate_control10_gate(repo,ctrl,weights)
        validate_full_timing_gate(repo,audit,{r['target_id']:r for r in plan})
        record['timing_audit_sha256']=sha256(audit)
        record['control10_manifest_sha256']=sha256(ctrl)
        original_sources=v3a_meta['snapshot_build']['source_hashes']
        if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in original_sources.items()):
            raise ValueError('Vivado authoritative P0 HDL differs from V3a snapshot')
        record['authoritative_source_hashes']=original_sources
        root=vivado.parent.parent
        build=out/'isolated_dat0004_build';build.mkdir()
        tb=repo/TB_REL
        vprj=build/'tb_v3b_dat0004_vlog.prj'
        vprj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),tb),encoding='utf-8')
        record['probe_prj_sha256']=sha256(vprj)
        cmds={
           'xvlog':f'--relax -prj "{batch_safe(vprj.resolve().as_posix())}"',
           'xelab':(' --debug typical --relax --mt 2 '
                    '-L xil_defaultlib -L unisims_ver -L unimacro_ver -L secureip '
                    f'--snapshot {V3B_SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                    'xil_defaultlib.glbl -log elaborate_v3b_dat0004.log')}
        for stage,cmd in cmds.items():
            stage_log=out/(stage+'.log');stage_cmd=out/(stage+'.cmd')
            stage_cmd.write_text(_cmd_stage(build,root,stage,cmd,stage_log),encoding='ascii')
            call_cmd(stage_cmd,build,out/(stage+'.wrapper.log'),args.timeout_seconds,'V3b DAT '+stage)
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
            print(f'V3B_DAT0004_XSIM {n}/2 {sid} {TARGET_ID} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd_file,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V3b '+sid)
            verified=parse_consumer(log,row,v3a/'logs'/(sid+'.log'))
            records.append(verified)
            record['records'].append({'scenario_id':sid,'log':str(log),'log_sha256':sha256(log)})
            if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in original_sources.items()):
                raise ValueError('Authoritative Vivado P0 source changed during V3b')
            if sha256(tb)!=record['probe_tb_sha256'] or sha256(vprj)!=record['probe_prj_sha256']:
                raise ValueError('Isolated TB or source list changed during V3b')
            print(f'V3B_DAT0004_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,records)
        record['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        record['status']='CONSUMER_PROVED_2_OF_2'
        print('V3B_DAT0004_CONSUMER_PASS: 2/2 XSim scenarios; mutated Conv2 raw accumulator lane0 accepted by Quant+ReLU PRE-NBA and output registered; NOT proof of quantized-byte difference; NOT automatic promotion',flush=True)
    except Exception:
        record['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v3b_dat0004_manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as err:
        raise SystemExit('V3B_DAT0004_FAIL: '+str(err))

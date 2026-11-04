#!/usr/bin/env python3
"""V3b MEM2 consumption probe: isolated XSim snapshot; never edit P0 or V3a TB.

Runs only MEM_0004/MEM_0005 (2 offsets each) on IMG0000, using the original
V3a Vivado-compiled HDL .prj source list and a different, instrumented copy
of the FI testbench in a NEW work directory. Requires fresh verified XSim logs.
No target auto-promotion. Not a substitute for the full 400.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys

FI_SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
sys.path.insert(0,str(FI_SCRIPTS))
from pilot400_common import FIELDS, sha256, load_plan, verify_manifest, write_csv, asset_lines, ASSET_SIZES
from pilot400_direct_xsim import (project_source_hashes, make_cmd_run, call_cmd,
                                  batch_safe, classify_elaboration)
from run_pilot400_target_validation_v3 import inspect_applied_and_result
from parse_fi_v2 import parse_one
from run_pilot400_control10 import validate_control10_gate
from run_pilot400 import validate_full_timing_gate

FROZEN_HASHES={
 'fi/config/fi_targets_candidates_v2.csv':'d9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
 'fi/config/pilot400_targets.csv':'98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
 'fi/scenarios/pilot400.csv':'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
 'fi/tb/tb_cnn_mnist_fi_pilot400.v':'2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
}
TARGETS={'MEM_0004':0,'MEM_0005':12}
V3B_SNAPSHOT='tb_cnn_mnist_fi_pilot400_v3b_behav'
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_PATH_RE=re.compile(r'"([^"\r\n]*[/\\]tb_cnn_mnist_fi_pilot400\.v)"',re.IGNORECASE)
VAL_RE=re.compile(r'^FI_V3B_MEM2_CONSUMER_PASS scenario=(\S+) target=(\S+) addr=(\d+) bit=(\d+) read=(\d+) consume=(\d+) before=([0-9a-fA-F]{16}) after=([0-9a-fA-F]{16}) data=([0-9a-fA-F]{16})$',re.M)
READ_RE=re.compile(r'^FI_V3B_MEM2_READ_PASS scenario=(\S+) target=(\S+) addr=(\d+) cycle=(\d+) word=([0-9a-fA-F]{16})$',re.M)
SUM_RE=re.compile(r'^FI_V3B_MEM2_SUMMARY scenario=(\S+) target=(\S+) read_cycle=(-?\d+) consume_cycle=(-?\d+) proofs=(\d+) overwrite_edges=(\d+) fc_words=(\d+)$',re.M)
COLUMNS=(*FIELDS,'read_cycle','consume_cycle','original_word','corrupted_word','fc_input_word','consumer_proof','outcome','log_sha256')


def validate_frozen_inputs(repo:Path) -> None:
    for name,h in FROZEN_HASHES.items():
        p=repo/name
        if not p.is_file() or sha256(p)!=h:
            raise ValueError('Frozen V3a input changed; never rewrite baseline: '+name)
    probe=repo/'fi/v3b/tb'/TB_NAME
    if not probe.is_file() or sha256(probe)==FROZEN_HASHES['fi/tb/'+TB_NAME]:
        raise ValueError('Missing/identical V3b instrumented testbench')
    code=probe.read_text(encoding='utf-8')
    if code.count('module tb_cnn_mnist_fi_pilot400;')!=1 or 'FI_V3B_MEM2_CONSUMER_PASS' not in code:
        raise ValueError('V3b TB module or monitor proof marker missing')


def validate_v3a(repo:Path,v3a:Path, manifest:list[dict[str,str]]) -> tuple[dict,list[dict[str,str]],Path]:
    fn=v3a/'validation_manifest.json'
    if not fn.is_file():raise ValueError('Missing V3a manifest: '+str(fn))
    data=json.loads(fn.read_text(encoding='utf-8'))
    if (data.get('schema')!='PILOT400_TARGET_VALIDATION_V3A' or
        data.get('status')!='DIRECTED_MECHANISM_VALIDATED' or
        data.get('selected_count')!=28 or
        len(data.get('records',[]))!=28 or
        data.get('sample_ids')!=['IMG0000'] or
        data.get('source_authority')!='vivado'):
        raise ValueError('V3a 28/28 Vivado evidence gate failed')
    for key,expected in FROZEN_HASHES.items():
        if data.get('source_hashes',{}).get(key)!=expected:
            raise ValueError('V3a frozen source hash differs: '+key)
    csv_path=v3a/'selected_scenarios.csv'
    if sha256(csv_path)!=data.get('selected_csv_sha256'):
        raise ValueError('V3a selected CSV provenance changed')
    with csv_path.open(newline='',encoding='utf-8-sig') as stream:
        selected=list(csv.DictReader(stream))
    manifest_by_id={r['scenario_id']:r for r in manifest}
    ids=[r['scenario_id'] for r in selected]
    if len(selected)!=28 or len(set(ids))!=28:
        raise ValueError('V3a directed scenario cardinality/dedup failed')
    if any(sid not in manifest_by_id or row!=manifest_by_id[sid] for sid,row in zip(ids,selected)):
        raise ValueError('V3a selected scenario differs from frozen Pilot400 manifest')
    records=data['records']
    if {r.get('scenario_id') for r in records}!=set(ids):
        raise ValueError('V3a manifest log records mismatch selected scenarios')
    for entry in records:
        sid=entry['scenario_id']
        # Only accept the expected log under the selected V3a run directory.
        log=(v3a/'logs'/(sid+'.log')).resolve()
        if Path(entry.get('log','')).resolve()!=log:
            raise ValueError('V3a log path changed: '+sid)
        if not log.is_file() or sha256(log)!=entry.get('log_sha256'):
            raise ValueError('V3a scenario log digest changed: '+sid)
        if inspect_applied_and_result(log,manifest_by_id[sid])['injection_verified']!='1':
            raise ValueError('V3a strict replay parse no longer verifies injection: '+sid)
    snapshot=data.get('snapshot_build',{})
    project=Path(data.get('project',''))
    if not project.is_file():raise ValueError('Original Vivado project missing')
    archived=v3a/'source_vlog.prj'
    if sha256(archived)!=snapshot.get('compile_prj_sha256'):
        raise ValueError('V3a original compiled-source list changed')
    sources,count=project_source_hashes(archived,repo,rtl_source='vivado',project=project)
    if count<20 or sources!=snapshot.get('source_hashes'):
        raise ValueError('V3a source list does not match actual Vivado RTL source hashes')
    pairs=[r for r in selected if r['target_id'] in TARGETS]
    if len(pairs)!=4 or {t:sum(r['target_id']==t for r in pairs) for t in TARGETS}!={t:2 for t in TARGETS}:
        raise ValueError('MEM_0004/MEM_0005 do not have two V3a scenarios each')
    return data,pairs,archived


def override_prj(original_text:str,new_tb:Path)->str:
    matches=TB_PATH_RE.findall(original_text)
    if len(matches)!=1:
        raise ValueError('Original Vivado .prj must reference the old FI testbench exactly once')
    return TB_PATH_RE.sub('"'+new_tb.resolve().as_posix()+'"',original_text,count=1)


def parse_consumer(log:Path,row:dict[str,str])->dict[str,str]:
    base=inspect_applied_and_result(log,row) # recheck FI_APPLIED+strict V2 first
    parsed=parse_one(log)
    text=log.read_text(encoding='utf-8',errors='replace')
    passes=VAL_RE.findall(text);reads=READ_RE.findall(text);sums=SUM_RE.findall(text)
    sid=row['scenario_id']; tid=row['target_id']; addr=TARGETS[tid]; bit=int(row['bit_index'])
    if len(passes)!=1 or len(reads)!=1 or len(sums)!=1:
        raise ValueError(f'{sid}: not exactly one read, consumer, and summary proof')
    p=passes[0]; r=reads[0]; z=sums[0]
    if ((p[0],p[1],int(p[2]),int(p[3]))!=(sid,tid,addr,bit) or
        (r[0],r[1],int(r[2]))!=(sid,tid,addr) or
        (z[0],z[1])!=(sid,tid)):
        raise ValueError(f'{sid}: consumer marker identity mismatch')
    read,consume=int(p[4]),int(p[5])
    before,after,data=(int(p[i],16) for i in (6,7,8))
    expected=1<<bit
    if (read<=int(parsed['requested_cycle']) or consume<=read or
        before^after!=expected or data!=after or
        read!=int(r[3]) or after!=int(r[4],16) or
        (read,consume,1,0)!=(int(z[2]),int(z[3]),int(z[4]),int(z[5])) or
        int(z[6])<addr+1):
        raise ValueError(f'{sid}: unproven consumer value/order/XOR/injection-window')
    return {k:row[k] for k in FIELDS}|{
        'read_cycle':str(read),'consume_cycle':str(consume),
        'original_word':f'{before:016x}','corrupted_word':f'{after:016x}',
        'fc_input_word':f'{data:016x}','consumer_proof':'VERIFIED_FC_INPUT',
        'outcome':base['outcome'],'log_sha256':sha256(log)}


def _cmd_stage(build:Path,vivado_root:Path,name:str,command:str,log:Path)->str:
    return ('@echo off\nsetlocal EnableExtensions\n'
            f'set "XILINX_VIVADO={batch_safe(str(vivado_root))}"\n'
            'set "PATH=%XILINX_VIVADO%/bin;%PATH%"\n'
            f'cd /d "{batch_safe(str(build))}"\n'
            'if errorlevel 1 exit /b 10\n'
            f'call "{batch_safe(str(vivado_root/"bin"/(name+".bat")))}" {command} '
            f'> "{batch_safe(log.resolve().as_posix())}" 2>&1\n'
            'set "RC=%ERRORLEVEL%"\nexit /b %RC%\n')


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    parser.add_argument('--vivado',type=Path,required=True)
    parser.add_argument('--weights-dir',type=Path,default=Path('data'))
    parser.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    parser.add_argument('--control10-manifest',type=Path,default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    parser.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_v3b_mem2_01'))
    parser.add_argument('--timeout-seconds',type=int,default=900)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    repo=args.repo.resolve()
    loc=lambda p:(p if p.is_absolute() else repo/p).resolve()
    v3a,out,vivado=loc(args.v3a_dir),loc(args.out_dir),loc(args.vivado)
    if out.exists():raise ValueError('Refusing to overwrite output/evidence directory: '+str(out))
    validate_frozen_inputs(repo)
    planned=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(repo/'fi/scenarios/pilot400.csv',planned)
    v3a_meta,selected,source_prj=validate_v3a(repo,v3a,manifest)
    for name,(count,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,count,digits)
    if args.timeout_seconds<10:raise ValueError('Timeout must be >=10s')
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_mem2_scenarios.csv',FIELDS,selected)
    record={'schema':'PILOT400_V3B_MEM2_CONSUMER_V1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'PREPARED_NOT_EXECUTED','source_authority':'vivado',
            'original_v3a_manifest_sha256':sha256(v3a/'validation_manifest.json'),
            'original_v3a_prj_sha256':sha256(source_prj),
            'original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
            'v3b_tb_sha256':sha256(repo/'fi/v3b/tb'/TB_NAME),
            'selected_sha256':sha256(out/'selected_mem2_scenarios.csv'),
            'records':[]}
    try:
        if not args.execute:
            print('V3B_MEM2_DRY_RUN_PASS: 4 frozen scenarios, original TB/gates untouched; 0 new XSim runs')
            return
        if os.name!='nt':raise ValueError('V3b XSim execution requires Windows')
        if not vivado.is_file():raise ValueError('Vivado .bat not found: '+str(vivado))
        weight=loc(args.weights_dir); audit=loc(args.timing_audit); control=loc(args.control10_manifest)
        validate_control10_gate(repo,control,weight)
        validate_full_timing_gate(repo,audit,{p['target_id']:p for p in planned})
        record['timing_audit_sha256']=sha256(audit)
        record['control10_manifest_sha256']=sha256(control)
        # Isolated build: same P0 paths/SHA256 as V3a, replace only its testbench.
        root=vivado.parent.parent
        build=out/'isolated_build'
        build.mkdir()
        v3b_prj=build/'tb_v3b_vlog.prj'
        v3b_prj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),
                                         repo/'fi/v3b/tb'/TB_NAME),encoding='utf-8')
        record['v3b_prj_sha256']=sha256(v3b_prj)
        original_sources=v3a_meta['snapshot_build']['source_hashes']
        compile_command=f'--relax -prj "{batch_safe(v3b_prj.resolve().as_posix())}"'
        elab_command=(' --debug typical --relax --mt 2 '
                      '-L xil_defaultlib -L unisims_ver -L unimacro_ver -L secureip '
                      f'--snapshot {V3B_SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                      'xil_defaultlib.glbl -log elaborate_v3b.log')
        for stage,cmd in [('xvlog',compile_command),('xelab',elab_command)]:
            stage_log=out/(stage+'.log')
            stage_cmd=out/(stage+'.cmd')
            stage_cmd.write_text(_cmd_stage(build,root,stage,cmd,stage_log),encoding='ascii')
            call_cmd(stage_cmd,build,out/(stage+'.wrapper.log'),args.timeout_seconds,'V3b '+stage)
            t=stage_log.read_text(encoding='utf-8',errors='replace') if stage_log.is_file() else ''
            if not t.strip() or any(line.startswith(('ERROR:', 'FATAL:')) for line in t.splitlines()):
                raise ValueError('V3b compile/elab error: '+str(stage_log))
            record[stage+'_log_sha256']=sha256(stage_log)
        snapshot=build/'xsim.dir'/V3B_SNAPSHOT
        record['elaboration_mode']=classify_elaboration(
            (out/'xelab.log').read_text(errors='replace'), snapshot, None,
            expected_snapshot=V3B_SNAPSHOT)
        run_tcl=out/'direct_runall.tcl'
        run_tcl.write_text('run all\nquit\n',encoding='ascii')
        logdir=out/'logs'
        logdir.mkdir()
        rows=[]
        for n,row in enumerate(selected,1):
            sid=row['scenario_id'];log=logdir/(sid+'.log');console=out/(sid+'.console.log')
            cmd_file=out/(sid+'.cmd')
            cmd=make_cmd_run(build,root,row,repo/'fi/pilot400/samples/IMG0000',
                             run_tcl,log,console,profile_only=False)
            # Existing helper is already proven on Windows with quoted plusargs;
            # change only the snapshot name and the isolated build directory.
            if cmd.count(' '+ 'tb_cnn_mnist_fi_pilot400_behav'+' ')!=1:
                raise ValueError('Unexpected XSim invocation snapshot format')
            cmd=cmd.replace(' tb_cnn_mnist_fi_pilot400_behav ',f' {V3B_SNAPSHOT} ')
            cmd_file.write_text(cmd,encoding='ascii')
            print(f'V3B_MEM2_XSIM {n}/4 {sid} {row["target_id"]} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd_file,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V3b '+sid)
            verified=parse_consumer(log,row)
            rows.append(verified)
            record['records'].append({'scenario_id':sid,'log':str(log),'log_sha256':sha256(log)})
            if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in original_sources.items()):
                raise ValueError('Original P0 Vivado source changed during V3b run')
            if sha256(repo/'fi/v3b/tb'/TB_NAME)!=record['v3b_tb_sha256'] or sha256(v3b_prj)!=record['v3b_prj_sha256']:
                raise ValueError('V3b testbench or isolated source list changed during run')
            print(f'V3B_MEM2_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,rows)
        record['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        record['status']='CONSUMER_PROVED_4_OF_4'
        print('V3B_MEM2_CONSUMER_PASS: 4/4 XSim scenarios; verified mutated value at FC accepted input; NOT automatic promotion',flush=True)
    except Exception:
        record['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v3b_manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as e:
        raise SystemExit('V3B_MEM2_FAIL: '+str(e))

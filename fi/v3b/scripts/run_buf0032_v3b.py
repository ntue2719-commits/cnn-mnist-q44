#!/usr/bin/env python3
"""V3b BUF_0032: isolated value-aware last-window Conv2 MAC consumer proof.

Runs exactly FI_P400_0181/0182 (IMG0000, bit0, offsets 1135/1136).
Authoritative Vivado P0 RTL and frozen Pilot400 testbench remain unchanged.
The separate Verilog monitor proves feature_mem[675] -> last 3x3 window
ch3/P8 -> Conv2 MAC accepted input PRE-NBA. It does not prove MAC
arithmetic corruption, target promotion, or a population error rate.
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

TARGET_ID='BUF_0032'
SELECTED_IDS=('FI_P400_0181','FI_P400_0182')
EXPECTED_OFFSETS=('1135','1136')
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_REL=Path('fi/v3b/buf0032/tb')/TB_NAME
V3B_SNAPSHOT='tb_cnn_mnist_fi_pilot400_v3b_buf0032_behav'
ISOLATED_TB_SHA256='4259146751ed84b4b1893d1fefc9ff87aa947fbee0d8fa6da719c31a6f79777f'
COLUMNS=(*FIELDS,'write_cycle','inject_cycle','read_cycle','window_cycle',
         'consume_cycle','overwrites','original_byte','corrupted_byte',
         'consumer_proof','outcome','log_sha256')

# Full-line exact cardinality, identities and event order; reject Tcl echoes and
# partial/truncated logs. NOTE: READ and WINDOW have the SAME global cycle,
# because a DRAIN edge reads pre-NBA and writes o_window during that NBA.
EVENTS={
 'WRITE':r' byte=([0-9a-fA-F]{2})',
 'INJECT':r' bit=(\d+) before=([0-9a-fA-F]{2}) after=([0-9a-fA-F]{2})',
 'READ':r' y=(\d+) x=(\d+) byte=([0-9a-fA-F]{2})',
 'WINDOW':r' slot=(\d+) byte=([0-9a-fA-F]{2})',
 'MAC_CONSUME':r' slot=(\d+) byte=([0-9a-fA-F]{2})',
}
EVENT_RE={name:re.compile(
    r'^FI_V3B_BUF0032_'+name+r' scenario=(\S+) target=(\S+) cycle=(\d+)'+suffix+'$',re.M)
    for name,suffix in EVENTS.items()}
SUMMARY_RE=re.compile(
    r'^FI_V3B_BUF0032_SUMMARY scenario=(\S+) target=(\S+) '
    r'write=(-?\d+) inject=(-?\d+) read=(-?\d+) window=(-?\d+) '
    r'consume=(-?\d+) overwrites=(\d+) before=([0-9a-fA-F]{2}) '
    r'after=([0-9a-fA-F]{2})$',re.M)


def validate_frozen_inputs(repo:Path)->None:
    for relative,expected in FROZEN_HASHES.items():
        p=repo/relative
        if not p.is_file() or sha256(p)!=expected:
            raise ValueError('Frozen Pilot400 source changed: '+relative)
    original=(repo/'fi/tb'/TB_NAME).read_text(encoding='utf-8')
    probe=repo/TB_REL
    if not probe.is_file() or sha256(probe)!=ISOLATED_TB_SHA256:
        raise ValueError('Isolated BUF_0032 monitor testbench hash missing/changed')
    v=probe.read_text(encoding='utf-8')
    for name in ('MONITOR','SUMMARY'):
        prefix='        ' if name=='SUMMARY' else '    '
        pat=(r'(?ms)^'+prefix+r'// BEGIN V3B_BUF0032_ISOLATED_'+name+
             r'\n.*?^'+prefix+r'// END V3B_BUF0032_ISOLATED_'+name+r'\n\n')
        v,count=re.subn(pat,'',v)
        if count!=1:
            raise ValueError('Missing/duplicated V3b BUF_0032 '+name+' block')
    if v!=original:
        raise ValueError('Isolated TB changes original FI behavior outside monitor blocks')
    if ('u_conv2_calc.i_window[287:280]' not in probe.read_text(encoding='utf-8') or
        'FI_V3B_BUF0032_MAC_CONSUME scenario=' not in probe.read_text(encoding='utf-8')):
        raise ValueError('BUF_0032 pre-NBA arithmetic consumer monitor absent')


def selected_buf0032(manifest:list[dict[str,str]])->list[dict[str,str]]:
    rows=[r for r in manifest if r['target_id']==TARGET_ID and r['sample_id']=='IMG0000']
    rows.sort(key=lambda r:r['scenario_id'])
    if (len(rows)!=2 or tuple(r['scenario_id'] for r in rows)!=SELECTED_IDS or
        tuple(r['injection_offset'] for r in rows)!=EXPECTED_OFFSETS or
        any(r['bit_index']!='0' or r['target_class']!='BUF' for r in rows)):
        raise ValueError('Frozen BUF_0032 IMG0000 pair differs from immutable manifest')
    return rows


def parse_consumer(log:Path,row:dict[str,str],original_v3a_log:Path|None=None)->dict[str,str]:
    base=inspect_applied_and_result(log,row)
    res=parse_one(log)
    if (row['target_id']!=TARGET_ID or row['sample_id']!='IMG0000' or
        row['bit_index']!='0' or row['scenario_id'] not in SELECTED_IDS or
        row['injection_offset']!=dict(zip(SELECTED_IDS,EXPECTED_OFFSETS)).get(row['scenario_id'])):
        raise ValueError('Only BUF_0032 frozen IMG0000 bit0 pair supported')
    text=log.read_text(encoding='utf-8',errors='replace')
    if 'FI_V3B_BUF0032_' in text and any(key in text for key in (
        'FI_V3B_BUF0032_OVERWRITTEN_BEFORE_CONSUME',
        'FI_V3B_BUF0032_READ_VALUE_NOT_PROVED',
        'FI_V3B_BUF0032_MAC_INPUT_NOT_PROVED',
        'FI_V3B_BUF0032_CAPTURE_WRITE_NOT_PROVED',
        'FI_V3B_BUF0032_INJECT_VALUE_NOT_PROVED',
        'FI_V3B_BUF0032_WINDOW_VALUE_NOT_PROVED',
        'FI_V3B_BUF0032_CONSUMER_TIMELINE_INVALID',
        'FI_V3B_BUF0032_TIMELINE_INCOMPLETE')):
        raise ValueError('BUF_0032 monitor failure marker present')
    matches=[]
    for name,p in EVENT_RE.items():
        found=list(p.finditer(text))
        if len(found)!=1:
            raise ValueError(f'{row["scenario_id"]}: expected exactly one {name} proof')
        matches.append(found[0])
    summary=list(SUMMARY_RE.finditer(text))
    if len(summary)!=1 or [m.start() for m in matches]!=sorted(m.start() for m in matches) or matches[-1].start()>=summary[0].start():
        raise ValueError('BUF_0032 event order or summary invalid')
    w,inj,read,window,mac=[m.groups() for m in matches]
    sm=summary[0].groups()
    if any(x[:2]!=(row['scenario_id'],TARGET_ID) for x in (w,inj,read,window,mac,sm)):
        raise ValueError('Scenario identity mismatched across consumer events')
    write_c,inject_c,read_c,window_c,mac_c=(int(x[2]) for x in (w,inj,read,window,mac))
    before=int(inj[4],16);after=int(inj[5],16)
    valid=(
        write_c<inject_c<read_c and read_c==window_c and mac_c==window_c+1 and
        int(w[3],16)==before and int(inj[3])==0 and (before^after)==1 and
        (int(read[3]),int(read[4]))==(10,10) and
        (int(window[3]),int(mac[3]))==(35,35) and
        all(int(x,16)==after for x in (read[5],window[4],mac[4])) and
        [int(x) for x in sm[2:7]]==[write_c,inject_c,read_c,window_c,mac_c] and
        int(sm[7])==0 and int(sm[8],16)==before and int(sm[9],16)==after and
        int(res['requested_cycle'])==inject_c and int(res['actual_cycle'])==inject_c)
    if not valid:
        raise ValueError('BUF_0032 corrupted byte not proved at accepted Conv2 MAC ch3/P8')
    if original_v3a_log is not None and res!=parse_one(original_v3a_log):
        raise ValueError(f'{row["scenario_id"]}: isolated V3b changed original FI_RESULT_V2')
    return {k:row[k] for k in FIELDS}|{
        'write_cycle':str(write_c),'inject_cycle':str(inject_c),
        'read_cycle':str(read_c),'window_cycle':str(window_c),
        'consume_cycle':str(mac_c),'overwrites':'0',
        'original_byte':f'{before:02x}','corrupted_byte':f'{after:02x}',
        'consumer_proof':'VERIFIED_CONV2_BUF675_TO_MAC_CH3_P8',
        'outcome':base['outcome'],'log_sha256':sha256(log)}


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--vivado',type=Path,required=True)
    ap.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_v3b_buf0032_01'))
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
    selected=selected_buf0032(manifest)
    for name,(count,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,count,digits)
    if args.timeout_seconds<10:raise ValueError('Timeout must be >=10s')
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_buf0032_scenarios.csv',FIELDS,selected)
    tb=repo/TB_REL
    record={'schema':'PILOT400_V3B_BUF0032_CONSUMER_V1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'PREPARED_NOT_EXECUTED','source_authority':'vivado',
            'original_v3a_manifest_sha256':sha256(v3a/'validation_manifest.json'),
            'original_v3a_prj_sha256':sha256(source_prj),
            'original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
            'probe_tb_sha256':sha256(tb),
            'selected_sha256':sha256(out/'selected_buf0032_scenarios.csv'),
            'records':[]}
    try:
        if not args.execute:
            print('V3B_BUF0032_DRY_RUN_PASS: 2 frozen scenarios; 0 XSim runs, original P0/TB unchanged',flush=True)
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
            raise ValueError('Vivado P0 source changed since V3a 28/28 proof')
        root=vivado.parent.parent
        build=out/'isolated_buf0032_build';build.mkdir()
        vprj=build/'tb_v3b_buf0032_vlog.prj'
        vprj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),tb),encoding='utf-8')
        record['probe_prj_sha256']=sha256(vprj)
        cmds={
          'xvlog':f'--relax -prj "{batch_safe(vprj.resolve().as_posix())}"',
          'xelab':(' --debug typical --relax --mt 2 '
                   '-L xil_defaultlib -L unisims_ver -L unimacro_ver -L secureip '
                   f'--snapshot {V3B_SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                   'xil_defaultlib.glbl -log elaborate_v3b_buf0032.log')}
        for stage,cmd in cmds.items():
            stage_log=out/(stage+'.log');stage_cmd=out/(stage+'.cmd')
            stage_cmd.write_text(_cmd_stage(build,root,stage,cmd,stage_log),encoding='ascii')
            call_cmd(stage_cmd,build,out/(stage+'.wrapper.log'),args.timeout_seconds,'V3b BUF0032 '+stage)
            t=stage_log.read_text(encoding='utf-8',errors='replace') if stage_log.is_file() else ''
            if not t.strip() or any(ln.startswith(('ERROR:','FATAL:')) for ln in t.splitlines()):
                raise ValueError('Compile/elaboration error: '+str(stage_log))
            record[stage+'_log_sha256']=sha256(stage_log)
        snapshot=build/'xsim.dir'/V3B_SNAPSHOT
        record['elaboration_mode']=classify_elaboration(
            (out/'xelab.log').read_text(errors='replace'),snapshot,None,expected_snapshot=V3B_SNAPSHOT)
        tcl=out/'direct_runall.tcl';tcl.write_text('run all\nquit\n',encoding='ascii')
        (out/'logs').mkdir()
        results=[]
        for n,row in enumerate(selected,1):
            sid=row['scenario_id'];log=out/'logs'/(sid+'.log');console=out/(sid+'.console.log')
            script=make_cmd_run(build,root,row,repo/'fi/pilot400/samples/IMG0000',tcl,log,console,profile_only=False)
            generic='tb_cnn_mnist_fi_pilot400_behav'
            if script.count(f' {generic} ')!=1:
                raise ValueError('Unexpected direct XSim snapshot syntax')
            script=script.replace(f' {generic} ',f' {V3B_SNAPSHOT} ')
            cmd_file=out/(sid+'.cmd');cmd_file.write_text(script,encoding='ascii')
            print(f'V3B_BUF0032_XSIM {n}/2 {sid} {TARGET_ID} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd_file,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V3b '+sid)
            verified=parse_consumer(log,row,v3a/'logs'/(sid+'.log'))
            results.append(verified)
            record['records'].append({'scenario_id':sid,'log':str(log),'log_sha256':sha256(log)})
            if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in original_sources.items()):
                raise ValueError('Vivado P0 source changed during V3b')
            if sha256(tb)!=record['probe_tb_sha256'] or sha256(vprj)!=record['probe_prj_sha256']:
                raise ValueError('Isolated testbench/source list changed during V3b')
            print(f'V3B_BUF0032_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,results)
        record['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        record['status']='CONSUMER_PROVED_2_OF_2'
        print('V3B_BUF0032_CONSUMER_PASS: 2/2 XSim scenarios; corrupted conv2_buf byte675 accepted at Conv2 MAC ch3 P8 PRE-NBA; NOT automatic promotion',flush=True)
    except Exception:
        record['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v3b_buf0032_manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as err:
        raise SystemExit('V3B_BUF0032_FAIL: '+str(err))

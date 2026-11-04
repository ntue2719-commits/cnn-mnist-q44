#!/usr/bin/env python3
"""Isolated V3b BUF_0017 two-branch Conv1 line-buffer lifetime and MAC acceptance (2 frozen IMG0000 cases).

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

TARGET_ID='BUF_0017'
SELECTED_IDS=('FI_P400_0121','FI_P400_0122')
EXPECTED_OFFSETS=('34','35')
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_REL=Path('fi/v3b/buf0017/tb')/TB_NAME
V3B_SNAPSHOT='tb_cnn_mnist_fi_pilot400_v3b_buf0017_behav'
COLUMNS=(*FIELDS,'write_cycle','inject_cycle','read_cycle','mid_shift_cycle',
         'mid_window_cycle','mid_mac_cycle','top_read_cycle','top_shift_cycle',
         'top_window_cycle','top_mac_cycle','source_overwrites','secondary_overwrites',
         'original_byte','corrupted_byte','consumer_proof','outcome','log_sha256')

# Each proof marker is a complete, uniquely matched line; no accidental match
# from a Tcl source echo or duplicated informational text is accepted.
E_RE={name:re.compile(r'^FI_V3B_BUF0017_'+name+
    r' scenario=(\S+) target=(\S+) cycle=(\d+)'+
    (r' bit=(\d+) before=([0-9a-fA-F]{2}) after=([0-9a-fA-F]{2})' if name=='INJECT' else
     r' slot=(\d+) byte=([0-9a-fA-F]{2})' if name in ('MID_WINDOW','MID_MAC','TOP_WINDOW','TOP_MAC') else
     r' byte=([0-9a-fA-F]{2})')+'$', re.M)
    for name in ('WRITE','INJECT','READ','MID_SHIFT','MID_WINDOW','MID_MAC',
                 'TOP_READ','TOP_SHIFT','TOP_WINDOW','TOP_MAC')}
S_RE=re.compile(
    r'^FI_V3B_BUF0017_SUMMARY scenario=(\S+) target=(\S+)'
    r' write=(-?\d+) inject=(-?\d+) read=(-?\d+) midshift=(-?\d+)'
    r' midwindow=(-?\d+) midmac=(-?\d+) topread=(-?\d+)'
    r' topshift=(-?\d+) topwindow=(-?\d+) topmac=(-?\d+)'
    r' source_overwrites=(\d+) secondary_overwrites=(\d+)'
    r' before=([0-9a-fA-F]{2}) after=([0-9a-fA-F]{2})$',re.M)
PROOF_ORDER=tuple(E_RE)


def validate_frozen_inputs(repo:Path)->None:
    for relative,expected in FROZEN_HASHES.items():
        p=repo/relative
        if not p.is_file() or sha256(p)!=expected:
            raise ValueError('Frozen Pilot400 source changed: '+relative)
    tb=repo/TB_REL
    if not tb.is_file() or sha256(tb)==FROZEN_HASHES['fi/tb/'+TB_NAME]:
        raise ValueError('Missing/identical isolated BUF_0017 testbench')
    text=tb.read_text(encoding='utf-8')
    if (text.count('module tb_cnn_mnist_fi_pilot400;')!=1 or
        text.count('FI_V3B_BUF0017_MID_MAC scenario=')!=1 or
        text.count('FI_V3B_BUF0017_TOP_MAC scenario=')!=1 or
        'u_conv1_calc.i_window[31:24]' not in text or
        'u_conv1_calc.i_window[7:0]' not in text):
        raise ValueError('Expected isolated BUF_0017 dual consumer monitor absent')


def selected_buf0017(manifest:list[dict[str,str]])->list[dict[str,str]]:
    rows=[r for r in manifest if r['target_id']==TARGET_ID and r['sample_id']=='IMG0000']
    rows.sort(key=lambda r:r['scenario_id'])
    if (len(rows)!=2 or tuple(r['scenario_id'] for r in rows)!=SELECTED_IDS or
        tuple(r['injection_offset'] for r in rows)!=EXPECTED_OFFSETS or
        any(r['bit_index']!='0' or r['target_class']!='BUF' for r in rows)):
        raise ValueError('Frozen BUF_0017 scenario selection is not exactly 2 expected cases')
    return rows


def parse_consumer(log:Path,row:dict[str,str],original_v3a_log:Path|None=None)->dict[str,str]:
    base=inspect_applied_and_result(log,row)
    parsed=parse_one(log)
    if (row['target_id']!=TARGET_ID or row['sample_id']!='IMG0000' or
        row['bit_index']!='0' or row['scenario_id'] not in SELECTED_IDS):
        raise ValueError('Only two frozen BUF_0017 IMG0000 bit0 cases are supported')
    text=log.read_text(encoding='utf-8',errors='replace')
    if any(marker in text for marker in (
        'FI_V3B_BUF0017_TIMELINE_INCOMPLETE',
        'FI_V3B_BUF0017_MID_MAC_NOT_PROVED',
        'FI_V3B_BUF0017_TOP_MAC_NOT_PROVED',
        'FI_V3B_BUF0017_READ_CAPTURE_NOT_PROVED',
        'FI_V3B_BUF0017_INJECT_NOT_PROVED',
        'FI_V3B_BUF0017_SOURCE_READ_NOT_PROVED')):
        raise ValueError('BUF_0017 monitor failure marker present')
    matches=[];positions=[]
    for name in PROOF_ORDER:
        found=list(E_RE[name].finditer(text))
        if len(found)!=1:
            raise ValueError(f'{row["scenario_id"]}: require exactly one {name} event')
        matches.append(found[0]);positions.append(found[0].start())
    summaries=list(S_RE.finditer(text))
    if len(summaries)!=1 or positions!=sorted(positions) or positions[-1]>=summaries[0].start():
        raise ValueError('BUF_0017 event order, count, or summary invalid')
    vals=[m.groups() for m in matches];summary=summaries[0].groups()
    sid=row['scenario_id']
    if any(v[:2]!=(sid,TARGET_ID) for v in (*vals,summary)):
        raise ValueError('BUF_0017 event scenario/target identity mismatch')
    cycles=[int(v[2]) for v in vals]
    (write,inject,read,midshift,midwindow,midmac,topread,topshift,topwindow,topmac)=cycles
    before=int(vals[1][4],16);after=int(vals[1][5],16)
    summ_cycles=[int(x) for x in summary[2:12]]
    source_overwrites=int(summary[12]);secondary_overwrites=int(summary[13]);
    summary_before=int(summary[14],16);summary_after=int(summary[15],16)
    valid=(
        write<inject<read<midshift<midwindow<midmac<topread<topshift<topwindow<topmac and
        midshift==read+1 and midwindow==read+2 and midmac==midwindow+1 and
        topread==read+28 and topshift==topread+1 and topwindow==topread+2 and
        topmac==topwindow+1 and
        int(vals[1][3])==0 and (before^after)==1 and
        int(vals[0][3],16)==before and
        all(int(v[-1],16)==after for v in vals[2:]) and
        int(vals[4][3])==3 and int(vals[5][3])==3 and
        int(vals[8][3])==0 and int(vals[9][3])==0 and
        int(parsed['requested_cycle'])==inject and
        int(parsed['actual_cycle'])==inject and
        summ_cycles==cycles and source_overwrites==1 and secondary_overwrites==1 and
        summary_before==before and summary_after==after)
    if not valid:
        raise ValueError('BUF_0017 corruption did not reach both accepted Conv1 MAC slots')
    if original_v3a_log is not None and parsed!=parse_one(original_v3a_log):
        raise ValueError(f'{sid}: V3b changed original FI_RESULT_V2 vs V3a')
    return {k:row[k] for k in FIELDS}|{
        'write_cycle':str(write),'inject_cycle':str(inject),'read_cycle':str(read),
        'mid_shift_cycle':str(midshift),'mid_window_cycle':str(midwindow),
        'mid_mac_cycle':str(midmac),'top_read_cycle':str(topread),
        'top_shift_cycle':str(topshift),'top_window_cycle':str(topwindow),
        'top_mac_cycle':str(topmac),
        'source_overwrites':str(source_overwrites),
        'secondary_overwrites':str(secondary_overwrites),
        'original_byte':f'{before:02x}','corrupted_byte':f'{after:02x}',
        'consumer_proof':'VERIFIED_LINEBUF1_TO_CONV1_MAC_P3_AND_P0',
        'outcome':base['outcome'],'log_sha256':sha256(log)}

def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--vivado',type=Path,required=True)
    ap.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_v3b_buf0017_01'))
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
    selected=selected_buf0017(manifest)
    for name,(count,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,count,digits)
    if args.timeout_seconds<10:raise ValueError('Timeout must be >= 10 seconds')
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_buf0017_scenarios.csv',FIELDS,selected)
    record={'schema':'PILOT400_V3B_BUF0017_CONSUMER_V1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'PREPARED_NOT_EXECUTED','source_authority':'vivado',
            'original_v3a_manifest_sha256':sha256(v3a/'validation_manifest.json'),
            'original_v3a_prj_sha256':sha256(source_prj),
            'original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
            'probe_tb_sha256':sha256(repo/TB_REL),
            'selected_sha256':sha256(out/'selected_buf0017_scenarios.csv'),
            'records':[]}
    try:
        if not args.execute:
            print('V3B_BUF0017_DRY_RUN_PASS: 2 frozen scenarios; 0 XSim runs, original P0/TB unchanged',flush=True)
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
        build=out/'isolated_buf0017_build';build.mkdir()
        tb=repo/TB_REL
        vprj=build/'tb_v3b_buf0017_vlog.prj'
        vprj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),tb),encoding='utf-8')
        record['probe_prj_sha256']=sha256(vprj)
        cmds={
           'xvlog':f'--relax -prj "{batch_safe(vprj.resolve().as_posix())}"',
           'xelab':(' --debug typical --relax --mt 2 '
                    '-L xil_defaultlib -L unisims_ver -L unimacro_ver -L secureip '
                    f'--snapshot {V3B_SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                    'xil_defaultlib.glbl -log elaborate_v3b_buf0017.log')}
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
            print(f'V3B_BUF0017_XSIM {n}/2 {sid} {TARGET_ID} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd_file,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V3b '+sid)
            verified=parse_consumer(log,row,v3a/'logs'/(sid+'.log'))
            records.append(verified)
            record['records'].append({'scenario_id':sid,'log':str(log),'log_sha256':sha256(log)})
            if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in original_sources.items()):
                raise ValueError('Authoritative Vivado P0 source changed during V3b')
            if sha256(tb)!=record['probe_tb_sha256'] or sha256(vprj)!=record['probe_prj_sha256']:
                raise ValueError('Isolated TB or source list changed during V3b')
            print(f'V3B_BUF0017_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,records)
        record['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        record['status']='CONSUMER_PROVED_2_OF_2'
        print('V3B_BUF0017_CONSUMER_PASS: 2/2 XSim scenarios; corrupted line_buf1[0] accepted twice at Conv1 MAC P3 and P0, with observed source overwrite; NOT automatic promotion',flush=True)
    except Exception:
        record['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v3b_buf0017_manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as err:
        raise SystemExit('V3B_BUF0017_FAIL: '+str(err))

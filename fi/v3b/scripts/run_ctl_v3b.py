#!/usr/bin/env python3
"""V3b isolated CTL: 4 selected targets, 8 frozen IMG0000 directed scenarios.

Prove actual use of a corrupted state value at the next PRE-NBA control
edge and the corresponding POST-NBA side effect. Does not estimate SEU rates,
prove label corruption, or promote CANDIDATE evidence status. Source P0
and original V3a testbench remain byte-identical and unmodified.
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
from run_pilot400_target_validation_v3 import inspect_applied_and_result, APPLIED_RE
from parse_fi_v2 import parse_one
from run_pilot400_control10 import validate_control10_gate
from run_pilot400 import validate_full_timing_gate
from run_mem1_v3b import FROZEN_HASHES, validate_v3a, override_prj, _cmd_stage

TARGETS={
    'CTL_0012':'MEM1_WR_ADDR',
    'CTL_0019':'FC_ROM_INDEX',
    'CTL_0026':'CONV1_XINDEX',
    'CTL_0028':'CONV2_STATE',
}
EXPECTED={
    'FI_P400_0021':('CTL_0012','850'),
    'FI_P400_0022':('CTL_0012','900'),
    'FI_P400_0041':('CTL_0019','1318'),
    'FI_P400_0042':('CTL_0019','1325'),
    'FI_P400_0061':('CTL_0026','80'),
    'FI_P400_0062':('CTL_0026','110'),
    'FI_P400_0081':('CTL_0028','1049'),
    'FI_P400_0082':('CTL_0028','1150'),
}
SELECTED_IDS=tuple(EXPECTED)
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_REL=Path('fi/v3b/ctl/tb')/TB_NAME
V3B_SNAPSHOT='tb_cnn_mnist_fi_pilot400_v3b_ctl_behav'
PROBE_TB_SHA256='58fdea616e3fbd19345f363e437aae50ee74c36e09b899e7a1ffda2df02a14c3'
COLUMNS=(*FIELDS,'consumer_kind','consumer_cycle','before','after','next_state',
         'consumer_proof','outcome','log_sha256')

PREFIX='FI_V3B_CTL_'
EVENT_RE={
    'CTL_0012':re.compile(r'^FI_V3B_CTL_MEM1 scenario=(\S+) target=(\S+) cycle=(\d+) old=([0-9a-fA-F]{2}) new=([0-9a-fA-F]{2}) valid=([01]) start=([01]) data=([0-9a-fA-F]{8}) post=(\d+) write=([01])$',re.M),
    'CTL_0019':re.compile(r'^FI_V3B_CTL_FC scenario=(\S+) target=(\S+) cycle=(\d+) old=([0-9a-fA-F]{2}) new=([0-9a-fA-F]{2}) busy=([01]) valid=([01]) rom=(\d+) preacc=([0-9a-fA-F]{8}) dot=([0-9a-fA-F]{8}) postacc=([0-9a-fA-F]{8}) postcnt=(\d+) final=([01])$',re.M),
    'CTL_0026':re.compile(r'^FI_V3B_CTL_CONV1 scenario=(\S+) target=(\S+) cycle=(\d+) old=([0-9a-fA-F]{2}) new=([0-9a-fA-F]{2}) valid=([01]) start=([01]) y=(\d+) pixel=([0-9a-fA-F]{2}) prev=([0-9a-fA-F]{2}) postx=(\d+) posty=(\d+) window=([01])$',re.M),
    'CTL_0028':re.compile(r'^FI_V3B_CTL_CONV2 scenario=(\S+) target=(\S+) cycle=(\d+) old=([0-9a-fA-F]{2}) new=([0-9a-fA-F]{2}) start=([01]) valid=([01]) count=(\d+) x=(\d+) y=(\d+) post=(\d+) oval=([01])$',re.M),
}
S_RE=re.compile(r'^FI_V3B_CTL_SUMMARY scenario=(\S+) target=(\S+) inject=(\d+) uses=(\d+) old=([0-9a-fA-F]{2}) new=([0-9a-fA-F]{2})$',re.M)


def validate_frozen_inputs(repo:Path)->None:
    for relative,expected in FROZEN_HASHES.items():
        p=repo/relative
        if not p.is_file() or sha256(p)!=expected:
            raise ValueError('Frozen Pilot400 source changed: '+relative)
    tb=repo/TB_REL
    if not tb.is_file() or sha256(tb)!=PROBE_TB_SHA256:
        raise ValueError('Isolated CTL testbench SHA256 mismatch or missing')
    code=tb.read_text(encoding='utf-8')
    for name,indent in (('MONITOR','    '),('SUMMARY','        ')):
        expr=(r'(?ms)^'+indent+r'// BEGIN V3B_CTL_ISOLATED_'+name+
              r'\n.*?^'+indent+r'// END V3B_CTL_ISOLATED_'+name+r'\n\n')
        code,n=re.subn(expr,'',code)
        if n!=1:raise ValueError('Missing/duplicated CTL monitor block: '+name)
    original=(repo/'fi/tb'/TB_NAME).read_text(encoding='utf-8')
    if code!=original:
        raise ValueError('Instrumented CTL TB differs outside isolated blocks')
    for token in ('FI_V3B_CTL_MEM1','FI_V3B_CTL_FC','FI_V3B_CTL_CONV1',
                  'FI_V3B_CTL_CONV2','FI_V3B_CTL_TIMELINE_NOT_PROVED'):
        if token not in tb.read_text(encoding='utf-8'):
            raise ValueError('Required control consumer marker missing: '+token)


def selected_ctl(manifest:list[dict[str,str]])->list[dict[str,str]]:
    rows=sorted((r for r in manifest if r['target_id'] in TARGETS and r['sample_id']=='IMG0000'),
                key=lambda r:r['scenario_id'])
    if len(rows)!=8 or tuple(r['scenario_id'] for r in rows)!=SELECTED_IDS:
        raise ValueError('Frozen CTL scenario identities/cardinality changed')
    for row in rows:
        tid,offset=EXPECTED[row['scenario_id']]
        if row['target_id']!=tid or row['injection_offset']!=offset or \
           row['target_class']!='CTL' or row['bit_index']!='0':
            raise ValueError('Frozen CTL class/bit/offset changed: '+row['scenario_id'])
    return rows


def parse_consumer(log:Path,row:dict[str,str],original_v3a_log:Path|None=None)->dict[str,str]:
    base=inspect_applied_and_result(log,row)
    parsed=parse_one(log)
    sid=row['scenario_id'];tid=row['target_id']
    if sid not in EXPECTED or (tid,row['injection_offset'])!=EXPECTED[sid] or \
       row['sample_id']!='IMG0000' or row['bit_index']!='0' or row['target_class']!='CTL':
        raise ValueError('Only eight frozen CTL IMG0000 scenarios accepted')
    text=log.read_text(encoding='utf-8',errors='replace')
    matches=list(EVENT_RE[tid].finditer(text))
    summaries=list(S_RE.finditer(text))
    if len(matches)!=1 or len(summaries)!=1:
        raise ValueError('Expected exactly one CTL accepted-decision/summary proof')
    # Refuse any additional, duplicated, malformed, or failure markers.
    marker_lines=[line for line in text.splitlines() if line.startswith(PREFIX)]
    if len(marker_lines)!=2:
        raise ValueError('Unexpected/unknown CTL monitor marker')
    event,summary=matches[0],summaries[0]
    g=event.groups();s=summary.groups()
    if g[:2]!=(sid,tid) or s[:2]!=(sid,tid):
        raise ValueError('CTL monitor identity mismatch')
    cycle=int(g[2]);old=int(g[3],16);new=int(g[4],16)
    if ((old^new)!=1 or old>255 or new>255 or
        cycle!=int(parsed['requested_cycle']) or
        cycle!=int(parsed['actual_cycle']) or
        (int(s[2]),int(s[3]),int(s[4],16),int(s[5],16))!=(cycle,1,old,new)):
        raise ValueError('CTL XOR/deposit/consumer timeline invalid')
    applied=APPLIED_RE.findall(text)
    if len(applied)!=1 or (int(applied[0][4]),int(applied[0][5]))!=(old&1,new&1):
        raise ValueError('CTL bit-level FI_APPLIED/readback mismatch')
    if not (text.find('FI_APPLIED scenario=')<event.start()<summary.start()<text.find('FI_RESULT_V2,')):
        raise ValueError('CTL decision/summary chronology invalid')
    if tid=='CTL_0012':
        valid,start,data,post,write=int(g[5]),int(g[6]),int(g[7],16),int(g[8]),int(g[9])
        expected=(new+1)&255 if new<169 else new
        if valid!=1 or start!=0 or post!=expected or write!=int(new<169):
            raise ValueError('Mem1 write pointer address/control decision not proved')
        next_state=post
    elif tid=='CTL_0019':
        busy,valid,rom=int(g[5]),int(g[6]),int(g[7])
        preacc,dot,postacc=int(g[8],16),int(g[9],16),int(g[10],16)
        postcnt,final=int(g[11]),int(g[12])
        expected=new+1 if new<24 else new
        if (busy!=1 or valid!=1 or rom!=new or
            postacc!=((preacc+dot)&0xffffffff) or
            postcnt!=expected or final!=int(new>=24)):
            raise ValueError('FC ROM counter and accepted arithmetic branch not proved')
        next_state=postcnt
    elif tid=='CTL_0026':
        valid,start,y=int(g[5]),int(g[6]),int(g[7]);postx,posty,window=int(g[10]),int(g[11]),int(g[12])
        expected_x=0 if new==27 else new+1
        expected_y=0 if new==27 and y==27 else y+1 if new==27 else y
        if (valid!=1 or start!=0 or new>=28 or y>=28 or
            postx!=expected_x or posty!=expected_y or window!=int(y>=2 and new>=2)):
            raise ValueError('Conv1 X address, row rollover or emitted-window decision not proved')
        next_state=postx
    else:
        start,valid,count,x,y,post,oval=map(int,g[5:12])
        if new>3 or count>168 or x>10 or y>10:
            raise ValueError('Conv2 control inputs outside declared bounds')
        expected=1 if new==0 and start else 0 if new==0 else \
                 2 if new==1 and valid and count==168 else 1 if new==1 else \
                 3 if new==2 else 0 if x==10 and y==10 else 3
        if post!=expected or oval!=int(new==3):
            raise ValueError('Conv2 state transition/output-valid branch not proved')
        next_state=post
    if original_v3a_log is not None and parsed!=parse_one(original_v3a_log):
        raise ValueError(f'{sid}: V3b changed original FI_RESULT_V2 outcome')
    return {k:row[k] for k in FIELDS}|{
        'consumer_kind':TARGETS[tid],'consumer_cycle':str(cycle),
        'before':f'{old:02x}','after':f'{new:02x}','next_state':str(next_state),
        'consumer_proof':'VERIFIED_PRE_NBA_CONTROL_DECISION_AND_POST_NBA_TRANSITION',
        'outcome':base['outcome'],'log_sha256':sha256(log)}


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--vivado',type=Path,required=True)
    ap.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_v3b_ctl_01'))
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--target',choices=sorted(TARGETS),help='Optional 2-scenario CTL smoke before full eight')
    ap.add_argument('--execute',action='store_true')
    args=ap.parse_args();repo=args.repo.resolve()
    loc=lambda p:(p if p.is_absolute() else repo/p).resolve()
    v3a,out,vivado=loc(args.v3a_dir),loc(args.out_dir),loc(args.vivado)
    if out.exists():raise ValueError('Refusing to overwrite evidence dir: '+str(out))
    validate_frozen_inputs(repo)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    v3a_meta,_,source_prj=validate_v3a(repo,v3a,manifest)
    selected=selected_ctl(manifest)
    if args.target:
        selected=[row for row in selected if row['target_id']==args.target]
    count=len(selected)
    if count not in (2,8):raise ValueError('Invalid selected CTL scenario count')
    for name,(asset_count,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,asset_count,digits)
    if args.timeout_seconds<10:raise ValueError('Timeout must be >=10 seconds')
    out.mkdir(parents=True,exist_ok=False)
    csv_name='selected_ctl_scenarios.csv'
    write_csv(out/csv_name,FIELDS,selected)
    record={'schema':'PILOT400_V3B_CTL_CONSUMER_V1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'PREPARED_NOT_EXECUTED','source_authority':'vivado',
            'targets':([args.target] if args.target else list(TARGETS)),'selected_count':len(selected),
            'original_v3a_manifest_sha256':sha256(v3a/'validation_manifest.json'),
            'original_v3a_prj_sha256':sha256(source_prj),
            'original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
            'probe_tb_sha256':sha256(repo/TB_REL),
            'selected_sha256':sha256(out/csv_name),
            'records':[]}
    try:
        if not args.execute:
            print(f'V3B_CTL_DRY_RUN_PASS: {count} frozen CTL scenarios; 0 XSim runs; original P0/TB untouched',flush=True)
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
        build=out/'isolated_ctl_build';build.mkdir()
        tb=repo/TB_REL
        vprj=build/'tb_v3b_ctl_vlog.prj'
        vprj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),tb),encoding='utf-8')
        record['probe_prj_sha256']=sha256(vprj)
        cmds={
          'xvlog':f'--relax -prj "{batch_safe(vprj.resolve().as_posix())}"',
          'xelab':(' --debug typical --relax --mt 2 '
                   '-L xil_defaultlib -L unisims_ver -L unimacro_ver -L secureip '
                   f'--snapshot {V3B_SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                   'xil_defaultlib.glbl -log elaborate_v3b_ctl.log'),
        }
        for stage,cmd in cmds.items():
            stage_log=out/(stage+'.log');stage_cmd=out/(stage+'.cmd')
            stage_cmd.write_text(_cmd_stage(build,root,stage,cmd,stage_log),encoding='ascii')
            call_cmd(stage_cmd,build,out/(stage+'.wrapper.log'),args.timeout_seconds,'V3b CTL '+stage)
            t=stage_log.read_text(encoding='utf-8',errors='replace') if stage_log.is_file() else ''
            if not t.strip() or any(ln.startswith(('ERROR:','FATAL:')) for ln in t.splitlines()):
                raise ValueError('CTL compile/elaboration error: '+str(stage_log))
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
            print(f'V3B_CTL_XSIM {n}/{count} {sid} {row["target_id"]} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd_file,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V3b '+sid)
            verified=parse_consumer(log,row,v3a/'logs'/(sid+'.log'))
            records.append(verified)
            record['records'].append({'scenario_id':sid,'target_id':row['target_id'],
                                      'log':str(log),'log_sha256':sha256(log),
                                      'consumer_proof':verified['consumer_proof']})
            if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in sources.items()):
                raise ValueError('Vivado P0 source changed during V3b')
            if sha256(tb)!=record['probe_tb_sha256'] or sha256(vprj)!=record['probe_prj_sha256']:
                raise ValueError('CTL instrumented TB/source list changed during V3b')
            print(f'V3B_CTL_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,records)
        record['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        record['status']=f'CONTROL_DECISION_PROVED_{count}_OF_{count}'
        label='V3B_CTL_CONSUMER_PASS' if count==8 else 'V3B_CTL_TARGET_CONSUMER_PASS'
        print(f'{label}: {count}/{count} XSim scenarios; CTL state/address read PRE-NBA and corresponding decision/transition checked POST-NBA; NOT automatic promotion',flush=True)
    except Exception:
        record['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v3b_ctl_manifest.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as err:
        raise SystemExit('V3B_CTL_FAIL: '+str(err))

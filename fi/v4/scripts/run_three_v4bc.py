#!/usr/bin/env python3
"""V4b-C: isolated consumer/decision XSim proofs for three historical targets.

A second-stage experiment, not a target promotion. Requires independently
replayed V4 and V4b-A/V4b-B ancestry. Executes 2 or 6 frozen IMG0000 cases
with a *new* instrumented simulation-only TB; P0 and original FI TB unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
FI_SCRIPTS=HERE.parents[1]/'scripts'
V3B_SCRIPTS=HERE.parents[1]/'v3b/scripts'
for where in (HERE,FI_SCRIPTS,V3B_SCRIPTS):
    if str(where) not in sys.path: sys.path.insert(0,str(where))

from audit_v4 import read_json, require, V3A_NAME
from audit_six_consumers_v4b import (SCHEMA as V4BB_SCHEMA,
    STATUS as V4BB_STATUS, COLUMNS as V4BB_COLUMNS, PROVED, PENDING,
    prove_existing_consumer)
from run_six_v4b import SCHEMA as V4BA_SCHEMA, STATUS_DONE, six_scenarios, verify_v4_ancestry
from pilot400_common import (FIELDS, sha256, load_plan, verify_manifest,
    write_csv, read_csv, asset_lines, ASSET_SIZES)
from pilot400_direct_xsim import make_cmd_run, call_cmd, batch_safe, classify_elaboration
from run_pilot400_target_validation_v3 import inspect_applied_and_result
from parse_fi_v2 import parse_one
from run_mem1_v3b import (FROZEN_HASHES, validate_v3a, override_prj, _cmd_stage)
from run_pilot400_control10 import validate_control10_gate
from run_pilot400 import validate_full_timing_gate

TARGETS={'CTL_0001':'FSM_BRANCH', 'BUF_0030':'CONV2_P0',
         'MEM_0001':'MEM1_TO_CONV2_P0'}
EXPECTED={
 'FI_P400_0001':('CTL_0001','2','125','PROTOCOL_VIOLATION'),
 'FI_P400_0002':('CTL_0001','2','150','PROTOCOL_VIOLATION'),
 'FI_P400_0141':('BUF_0030','0','965','FINAL_OUTPUT_MATCH'),
 'FI_P400_0142':('BUF_0030','0','970','FINAL_OUTPUT_MATCH'),
 'FI_P400_0201':('MEM_0001','0','794','FINAL_OUTPUT_MATCH'),
 'FI_P400_0202':('MEM_0001','0','795','FINAL_OUTPUT_MATCH'),
}
TB_NAME='tb_cnn_mnist_fi_pilot400.v'
TB_REL=Path('fi/v4bc/tb')/TB_NAME
SNAPSHOT='tb_cnn_mnist_fi_pilot400_v4bc_behav'
# Generated from frozen original FI TB with two strictly isolated additions.
PROBE_TB_SHA256='3f9ffb2ebd9e25cec6894373563ff9102092e78f05fb06fdceadb10233784376'
COLUMNS=(*FIELDS, 'outcome','consumer_kind','write_cycle','inject_cycle',
         'read_cycle','capture_cycle','window_cycle','consume_cycle',
         'decision_cycle','original_value','corrupted_value',
         'consumer_proof','log_sha256')
SCHEMA='CNN_PEP_V4BC_THREE_HISTORICAL_CONSUMERS_V1'
STATUS='ISOLATED_TWO_CONFIG_CONSUMER_PROOF_THREE_TARGETS'

CTL_RE=re.compile(
 r'^FI_V4BC_CTL scenario=(\S+) target=(\S+) cycle=(\d+) bit=(\d+) '
 r'old=(\d+) new=(\d+) post=(\d+) valid=([01]) done=([01]) busy=([01])$',re.M)
SUMMARY_RE=re.compile(
 r'^FI_V4BC_SUMMARY scenario=(\S+) target=(\S+) inject=(-?\d+) '
 r'decision=(-?\d+) write=(-?\d+) read=(-?\d+) capture=(-?\d+) '
 r'window=(-?\d+) consume=(-?\d+) overwrites=(\d+)$',re.M)
EV_RE={
 'MEM_WRITE':re.compile(r'^FI_V4BC_MEM_WRITE scenario=(\S+) target=(\S+) cycle=(\d+) word=([0-9a-fA-F]{8})$',re.M),
 'BUF_CAPTURE':re.compile(r'^FI_V4BC_BUF_CAPTURE scenario=(\S+) target=(\S+) cycle=(\d+) word=([0-9a-fA-F]{8})$',re.M),
 'INJECT':re.compile(r'^FI_V4BC_INJECT scenario=(\S+) target=(\S+) cycle=(\d+) bit=(\d+) old=([0-9a-fA-F]{2}) new=([0-9a-fA-F]{2})$',re.M),
 'MEM_READ':re.compile(r'^FI_V4BC_MEM_READ scenario=(\S+) target=(\S+) cycle=(\d+) word=([0-9a-fA-F]{8})$',re.M),
 'WINDOW':re.compile(r'^FI_V4BC_WINDOW scenario=(\S+) target=(\S+) cycle=(\d+) slot=(\d+) byte=([0-9a-fA-F]{2})$',re.M),
 'MAC':re.compile(r'^FI_V4BC_MAC scenario=(\S+) target=(\S+) cycle=(\d+) slot=(\d+) byte=([0-9a-fA-F]{2})$',re.M),
}
APPLIED=re.compile(
 r'^FI_APPLIED scenario=(\S+) target=(\S+) class=(\S+) bit=(\d+) '
 r'old=([01]) new=([01]) requested=(\d+)$',re.M)


def one(pattern:re.Pattern[str], text:str, kind:str)->re.Match[str]:
    hits=list(pattern.finditer(text))
    require(len(hits)==1, f'{kind}: expected exactly one event, got {len(hits)}')
    return hits[0]


def validate_frozen_inputs(repo:Path)->None:
    for name, expected in FROZEN_HASHES.items():
        require((repo/name).is_file() and sha256(repo/name)==expected,
                'Frozen Pilot400 source/hash mismatch: '+name)
    copy=repo/TB_REL
    require(copy.is_file() and sha256(copy)==PROBE_TB_SHA256,
            'V4b-C isolated testbench missing or changed')
    text=copy.read_text(encoding='utf-8')
    pat=r'(?ms)^    // BEGIN V4BC_ISOLATED_MONITOR\n.*?^    // END V4BC_ISOLATED_MONITOR\n\n'
    text,count=re.subn(pat,'',text)
    require(count==1,'V4b-C monitor insertion missing/duplicate')
    summ=(r'(?m)^        // BEGIN V4BC_ISOLATED_SUMMARY\n'
          r'        if \(target_id == "CTL_0001" \|\| target_id == "BUF_0030" \|\| target_id == "MEM_0001"\)\n'
          r'            v4bc_check\(\);\n'
          r'        // END V4BC_ISOLATED_SUMMARY\n\n')
    text,count=re.subn(summ,'',text)
    require(count==1,'V4b-C summary insertion missing/duplicate')
    require(text==(repo/'fi/tb'/TB_NAME).read_text(encoding='utf-8'),
            'V4b-C testbench changed original behavior outside isolated monitor')
    require('u_conv2_calc.i_window[7:0]' in copy.read_text(encoding='utf-8') and
            'FI_V4BC_CTL_DONE_BRANCH_NOT_SELECTED' in copy.read_text(encoding='utf-8'),
            'Missing specific pre-NBA MAC/control decision witnesses')


def selected_three(manifest:list[dict[str,str]])->list[dict[str,str]]:
    result=[r for r in manifest if r['target_id'] in TARGETS and r['sample_id']=='IMG0000']
    result.sort(key=lambda r:r['scenario_id'])
    require(tuple(x['scenario_id'] for x in result)==tuple(EXPECTED),
            'V4b-C expected exact six frozen scenario IDs')
    for row in result:
        tid,bit,offset,_=EXPECTED[row['scenario_id']]
        require((row['target_id'],row['target_class'],row['bit_index'],row['injection_offset']) ==
                (tid,tid.split('_')[0],bit,offset),
                'V4b-C source manifest tuple changed: '+row['scenario_id'])
    return result


def verify_existing_audit(repo:Path, results:Path, weights:Path,
                          v4_path:Path, v4b:Path, audit_json:Path)->dict:
    """Replay parent V4, V4b-A and all 12 V4b-B consumer claims read-only."""
    fresh,v4sha=verify_v4_ancestry(repo,results,weights,v4_path)
    obj=read_json(audit_json)
    require(obj.get('schema')==V4BB_SCHEMA and obj.get('status')==V4BB_STATUS and
            obj.get('source_authority')=='vivado' and
            obj.get('source_modified') is False and
            obj.get('target_status_modified') is False and
            obj.get('full400_authorized') is False and
            obj.get('evidence_rows')==12 and obj.get('direct_consumer_scenarios')==6 and
            obj.get('direct_consumer_targets')==sorted(PROVED) and
            obj.get('still_pending_targets')==list(PENDING),
            'V4b-B existing consumer audit metadata not approved')
    m=read_json(v4b/'v4b_manifest.json')
    require(m.get('schema')==V4BA_SCHEMA and m.get('status')==STATUS_DONE and
            m.get('consumer_qualified') is False and m.get('full400_authorized') is False and
            m.get('source_authority')=='vivado' and m.get('selected_count')==12,
            'V4b-A mechanism audit metadata not approved')
    require(obj['v4_audit_sha256']==v4sha and
            obj['v4b_a_manifest_sha256']==sha256(v4b/'v4b_manifest.json') and
            obj['v4b_a_mechanism_csv_sha256']==sha256(v4b/'mechanism_validation.csv') and
            obj['original_tb_sha256']==sha256(repo/'fi/tb'/TB_NAME) and
            obj['csv_sha256']==sha256(audit_json.parent/'existing_consumer_evidence.csv'),
            'V4b-A/B frozen hashes/provenance changed')
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    frozen=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    rows=six_scenarios(frozen,plan)
    evidence=read_csv(audit_json.parent/'existing_consumer_evidence.csv',V4BB_COLUMNS)
    require(len(evidence)==12 and len({x['scenario_id'] for x in evidence})==12,
            'V4b-B expected exactly 12 independently replayed rows')
    by_id={r['scenario_id']:r for r in evidence}
    for row in rows:
        sid=row['scenario_id'];tid=row['target_id']
        source=v4b/'logs'/(sid+'.log')
        require(source.is_file() and sha256(source)==by_id[sid]['log_sha256'],
                'V4b-A original log hash mismatch: '+sid)
        require(all(by_id[sid][k]==row[k] for k in FIELDS),
                'V4b-B selected scenario tuple differs: '+sid)
        replay=inspect_applied_and_result(source,row)
        require(replay['outcome']==by_id[sid]['outcome'],
                'V4b-B outcome changed from original mechanism: '+sid)
        if tid in PROVED:
            txt=source.read_text(encoding='utf-8',errors='replace')
            golden=repo/'fi/pilot400/samples/IMG0000/golden.hex'
            from pilot400_common import asset_lines
            golden0=asset_lines(golden,10,8)[0]
            proof,marker=prove_existing_consumer(txt,row,parse_one(source),golden0)
            require((proof,marker)==(by_id[sid]['consumer_evidence'],
                                      by_id[sid]['source_marker']),
                    'V4b-B consumer marker changed from authenticated replay: '+sid)
        else:
            require(by_id[sid]['consumer_evidence']=='NOT_PROVED_IN_V4B_A_LOGS' and
                    by_id[sid]['source_marker']=='NONE',
                    'Pending historical target incorrectly promoted: '+sid)
    # V4 provenance also authenticates V3a RTL and 28 previous V3b consumers.
    require(fresh['status']=='CROSS_EVIDENCE_PASS_FULL400_BLOCKED',
            'V4 source ancestry status wrong')
    return obj


def parse_consumer(log:Path,row:dict[str,str],original_v4ba_log:Path|None=None)->dict[str,str]:
    base=inspect_applied_and_result(log,row)
    parsed=parse_one(log)
    sid=row['scenario_id'];tid=row['target_id']
    require(sid in EXPECTED, 'Unexpected V4b-C scenario ID')
    require((tid,row['bit_index'],row['injection_offset'],row['sample_id'])==
            (EXPECTED[sid][0],EXPECTED[sid][1],EXPECTED[sid][2],'IMG0000'),
            'Unexpected V4b-C scenario tuple')
    require(base['outcome']==EXPECTED[sid][3],
            'Unexpected V4b-C final outcome; compare with V4b-A')
    if original_v4ba_log is not None:
        require(parsed==parse_one(original_v4ba_log),
                'V4b-C changed FI_RESULT_V2 compared with frozen V4b-A')
    txt=log.read_text(encoding='utf-8',errors='replace')
    applied=one(APPLIED,txt,sid+'/FI_APPLIED')
    a=applied.groups()
    cycle=int(parsed['actual_cycle']);req=int(parsed['requested_cycle'])
    require(a[0]==sid and a[1]==tid and a[2]==row['target_class'] and
            int(a[3])==int(row['bit_index']) and int(a[6])==req and
            req==cycle and int(a[4])^int(a[5])==1,
            'V4b-C XOR applied tuple/readback wrong')
    summary=one(SUMMARY_RE,txt,sid+'/summary')
    sm=summary.groups()
    require((sm[0],sm[1])==(sid,tid) and int(sm[2])==cycle and
            int(sm[9])==0, 'Invalid V4b-C summary identity or overwrite claim')
    write,read,capture,window,consume=map(int,(sm[4],sm[5],sm[6],sm[7],sm[8]))
    decision=int(sm[3])
    names=['CTL'] if tid=='CTL_0001' else (
        ['MEM_WRITE','INJECT','MEM_READ','BUF_CAPTURE','WINDOW','MAC']
        if tid=='MEM_0001' else ['BUF_CAPTURE','INJECT','WINDOW','MAC'])
    ev={name:one(CTL_RE if name=='CTL' else EV_RE[name],txt,sid+'/'+name)
        for name in names}
    marker_lines=[ln for ln in txt.splitlines() if ln.startswith('FI_V4BC_')]
    require(len(marker_lines)==len(names)+1,
            'V4b-C contains unknown, missing, duplicate or failure markers')
    positions=[x.start() for x in ev.values()]
    require(all(sid==m.group(1) and tid==m.group(2) for m in ev.values()) and
            positions==sorted(positions) and
            positions[0]<summary.start()<txt.find('FI_RESULT_V2,') and
            applied.start() < (ev['CTL'].start() if tid=='CTL_0001' else ev['INJECT'].start()),
            'V4b-C marker event order or identity incorrect')
    if tid=='CTL_0001':
        g=ev['CTL'].groups()
        ctlcycle,bit,old,new,post,valid,done,busy=map(int,g[2:])
        require((ctlcycle,bit,old,new,post,valid,done,busy)==
                (cycle,2,1,5,0,1,1,0) and
                (int(a[4]),int(a[5]))==(0,1) and
                decision==cycle and (write,read,capture,window,consume)==(-1,)*5,
                'CTL_0001 mutated state not sampled in actual premature DONE decision')
        original='01'; corrupted='05'
        proof='PRE_NBA_MUTATED_ST_CONV1_TO_ST_DONE_POST_NBA_DONE_PULSE'
    else:
        require(decision==-1 and window+1==consume and
                window>cycle and capture>0,
                'BUF/MEM stage chronology not proved')
        for name in names:
            if name in ('CTL','MEM_WRITE'):continue
            g=ev[name].groups()
            if name=='INJECT':
                inj,bit,before,after=int(g[2]),int(g[3]),int(g[4],16),int(g[5],16)
                require(inj==cycle and bit==0 and after==(before^1) and
                        (int(a[4]),int(a[5]))==(before&1,after&1),
                        'V4b-C injected byte not exact bit0 XOR')
                original=f'{before:02x}';corrupted=f'{after:02x}'
            elif name in ('WINDOW','MAC'):
                stagecycle,slot,byte=int(g[2]),int(g[3]),int(g[4],16)
                require(stagecycle==(window if name=='WINDOW' else consume) and
                        slot==0 and byte==int(corrupted,16),
                        'V4b-C Conv2 ch0 P0 value not demonstrated at MAC')
        cg=ev['BUF_CAPTURE'].groups();capcycle=int(cg[2]);capword=int(cg[3],16)
        require(capcycle==capture,'Conv2 capture cycle differs from summary')
        if tid=='BUF_0030':
            require((write,read)==(-1,-1) and
                    capture<cycle<window<consume and
                    (capword&255)==int(original,16),
                    'BUF_0030 consumer cannot be traced back to recorded buffer write')
            proof='PRE_NBA_CORRUPTED_CONV2_BUFFER_BYTE0_ACCEPTED_AT_MAC_P0'
        else:
            wg=ev['MEM_WRITE'].groups();rg=ev['MEM_READ'].groups()
            memwrite=int(wg[2]);memorig=int(wg[3],16)
            memread=int(rg[2]);memreadword=int(rg[3],16)
            require((memwrite,memread)==(write,read) and
                    write<cycle<read<capture<window<consume and
                    memreadword==(memorig^1) and
                    capword==memreadword and
                    memorig&255==int(original,16) and
                    memreadword&255==int(corrupted,16),
                    'MEM_0001 write/inject/read/capture evidence not byte/value consistent')
            proof='PRE_NBA_CORRUPTED_MEM1_WORD0_AT_CONV2_MAC_P0'
    result={k:row[k] for k in FIELDS}
    result.update({'outcome':base['outcome'],'consumer_kind':TARGETS[tid],
        'write_cycle':str(write),'inject_cycle':str(cycle),
        'read_cycle':str(read),'capture_cycle':str(capture),
        'window_cycle':str(window),'consume_cycle':str(consume),
        'decision_cycle':str(decision),'original_value':original,
        'corrupted_value':corrupted,
        'consumer_proof':proof,'log_sha256':sha256(log)})
    return result


def resolve_arg(repo:Path,path:Path)->Path:
    return path.resolve() if path.is_absolute() else (repo/path).resolve()


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--vivado',type=Path,required=True)
    ap.add_argument('--v4-audit',type=Path,default=Path('fi/results/v4_cross_evidence_audit_02/v4_audit.json'))
    ap.add_argument('--v4b-dir',type=Path,default=Path('fi/results/v4b_six_mechanism_01'))
    ap.add_argument('--v4b-existing-audit',type=Path,
                    default=Path('fi/results/v4b_existing_consumer_reaudit_01/existing_consumer_audit.json'))
    ap.add_argument('--results-dir',type=Path,default=Path('fi/results'))
    ap.add_argument('--v3a-dir',type=Path,default=Path('fi/results/pilot400_v3a_28_01'))
    ap.add_argument('--weights-dir',type=Path,default=Path('data'))
    ap.add_argument('--timing-audit',type=Path,
                    default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,
                    default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--out-dir',type=Path,default=Path('fi/results/v4bc_three_consumers_01'))
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--target',choices=sorted(TARGETS),help='Optional two-scenario smoke test')
    ap.add_argument('--execute',action='store_true')
    args=ap.parse_args()
    repo=args.repo.resolve()
    loc=lambda x:resolve_arg(repo,x)
    out,results,weights=loc(args.out_dir),loc(args.results_dir),loc(args.weights_dir)
    v4path,v4bpath,existing=loc(args.v4_audit),loc(args.v4b_dir),loc(args.v4b_existing_audit)
    require(not out.exists(), 'Refusing to overwrite existing output/evidence directory: '+str(out))
    require(args.timeout_seconds>=10,'Timeout must be at least 10 seconds')
    validate_frozen_inputs(repo)
    verify_existing_audit(repo,results,weights,v4path,v4bpath,existing)
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    selected=selected_three(manifest)
    if args.target:selected=[r for r in selected if r['target_id']==args.target]
    count=len(selected)
    require(count in (2,6),'V4b-C selection requires exactly 2 or 6 frozen configurations')
    for file,(n,digits) in ASSET_SIZES.items():
        asset_lines(repo/'fi/pilot400/samples/IMG0000'/file,n,digits)
    v3a,_,source_prj=validate_v3a(repo,loc(args.v3a_dir),manifest)
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_scenarios.csv',FIELDS,selected)
    metadata={'schema':SCHEMA,'created_utc':datetime.now(timezone.utc).isoformat(),
        'status':'DRY_RUN_ONLY','selected_count':count,
        'selected_sha256':sha256(out/'selected_scenarios.csv'),
        'source_authority':'vivado','original_tb_sha256':FROZEN_HASHES['fi/tb/'+TB_NAME],
        'isolated_tb_sha256':sha256(repo/TB_REL),
        'v4b_existing_consumer_audit_sha256':sha256(existing),
        'v4b_a_manifest_sha256':sha256(v4bpath/'v4b_manifest.json'),
        'v3a_manifest_sha256':sha256(loc(args.v3a_dir)/'validation_manifest.json'),
        'records':[], 'target_status_modified':False,'full400_authorized':False,
        'consumer_qualified':False}
    try:
        if not args.execute:
            print(f'V4BC_DRY_RUN_PASS: {count} frozen scenarios; V4/V4b-A/V4b-B ancestry replayed; 0 XSim; FULL400 BLOCKED',flush=True)
            return
        require(os.name=='nt','XSim execution is available only on Windows')
        vivado=loc(args.vivado)
        require(vivado.is_file(),'Vivado BAT not found: '+str(vivado))
        validate_control10_gate(repo,loc(args.control10_manifest),weights)
        validate_full_timing_gate(repo,loc(args.timing_audit),{r['target_id']:r for r in plan})
        sources=v3a['snapshot_build']['source_hashes']
        require(all(Path(p).is_file() and sha256(Path(p))==h for p,h in sources.items()),
                'Authoritative Vivado HDL differs from V3a sources')
        metadata['source_hashes']=sources
        root=vivado.parent.parent
        build=out/'isolated_v4bc_build';build.mkdir()
        tb=repo/TB_REL
        prj=build/'tb_v4bc_vlog.prj'
        prj.write_text(override_prj(source_prj.read_text(encoding='utf-8',errors='replace'),tb),encoding='utf-8')
        metadata['source_prj_sha256']=sha256(prj)
        stages={
          'xvlog':f'--relax -prj "{batch_safe(prj.resolve().as_posix())}"',
          'xelab':('--debug typical --relax --mt 2 -L xil_defaultlib -L unisims_ver '
                   '-L unimacro_ver -L secureip '
                   f'--snapshot {SNAPSHOT} xil_defaultlib.tb_cnn_mnist_fi_pilot400 '
                   'xil_defaultlib.glbl -log elaborate_v4bc.log'),
        }
        for stage,args_cmd in stages.items():
            stage_log=out/(stage+'.log')
            stage_cmd=out/(stage+'.cmd')
            stage_cmd.write_text(_cmd_stage(build,root,stage,args_cmd,stage_log),encoding='ascii')
            call_cmd(stage_cmd,build,out/(stage+'.wrapper.log'),args.timeout_seconds,'V4b-C '+stage)
            require(stage_log.is_file() and stage_log.stat().st_size>0,
                    'Missing V4b-C compilation/elaboration stage log: '+stage)
            text=stage_log.read_text(encoding='utf-8',errors='replace')
            require(not any(x.startswith(('ERROR:','FATAL:')) for x in text.splitlines()),
                    'V4b-C compilation/elaboration reported error: '+stage)
            metadata[stage+'_sha256']=sha256(stage_log)
        metadata['elaboration_mode']=classify_elaboration(
            (out/'xelab.log').read_text(errors='replace'),
            build/'xsim.dir'/SNAPSHOT,None,expected_snapshot=SNAPSHOT)
        tcl=out/'direct_runall.tcl'
        tcl.write_text('run all\nquit\n',encoding='ascii')
        (out/'logs').mkdir()
        metadata['status']='SNAPSHOT_BUILT'
        report=[]
        for n,row in enumerate(selected,1):
            sid=row['scenario_id'];tid=row['target_id']
            log=out/'logs'/(sid+'.log')
            console=out/(sid+'.console.log')
            run_cmd=make_cmd_run(build,root,row,repo/'fi/pilot400/samples/IMG0000',tcl,
                                 log,console,profile_only=False)
            old='tb_cnn_mnist_fi_pilot400_behav'
            require(run_cmd.count(f' {old} ')==1,'Unexpected XSim snapshot invocation')
            run_cmd=run_cmd.replace(f' {old} ',f' {SNAPSHOT} ')
            cmd=out/(sid+'.cmd');cmd.write_text(run_cmd,encoding='ascii')
            print(f'V4BC_XSIM {n}/{count} {sid} {tid} bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
            call_cmd(cmd,build,out/(sid+'.wrapper.log'),args.timeout_seconds,'V4b-C '+sid)
            require(console.is_file() and 'Expected a switch' not in console.read_text(errors='replace'),
                    'XSim CLI argument error: '+sid)
            verified=parse_consumer(log,row,v4bpath/'logs'/(sid+'.log'))
            report.append(verified)
            metadata['records'].append({'scenario_id':sid,'target_id':tid,
                 'log':str(log),'log_sha256':sha256(log),
                 'consumer_proof':verified['consumer_proof'],'outcome':verified['outcome']})
            require(all(Path(p).is_file() and sha256(Path(p))==h for p,h in sources.items()),
                    'Vivado RTL source changed during V4b-C')
            require(sha256(tb)==metadata['isolated_tb_sha256'] and
                    sha256(prj)==metadata['source_prj_sha256'],
                    'V4b-C monitor/source list modified during FI campaign')
            print(f'V4BC_SCENARIO_CONSUMER_PASS: {sid} {verified["outcome"]}',flush=True)
        write_csv(out/'consumer_validation.csv',COLUMNS,report)
        metadata['consumer_csv_sha256']=sha256(out/'consumer_validation.csv')
        metadata['status']=STATUS
        label='V4BC_CONSUMER_PASS' if count==6 else 'V4BC_TARGET_CONSUMER_PASS'
        print(f'{label}: {count}/{count} XSim scenarios; specific pre-NBA consumer/control decision verified; NOT automatic promotion; FULL400 BLOCKED',flush=True)
    except Exception:
        metadata['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'v4bc_manifest.json').write_text(json.dumps(metadata,indent=2,sort_keys=True)+'\n',encoding='utf-8')


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as e:
        raise SystemExit('V4BC_FAIL: '+str(e))

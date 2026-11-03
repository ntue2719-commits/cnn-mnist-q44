#!/usr/bin/env python3
"""Run one fault-free XSim timing profile and audit candidate injection windows.

Profile DOES NOT inject anything; timing assertions require real XSim log data.
Use before a full 400-run exploration. Never auto-adjusts a frozen manifest.
"""
from __future__ import annotations
import argparse
import csv
from collections import defaultdict
from pathlib import Path
import re
import subprocess
import sys

from pilot400_common import load_plan,verify_manifest,write_csv
from run_pilot400 import make_tcl

PAT=re.compile(r'^FI_PROFILE_EVENT,(WRITE|READ|FC_VALID),([A-Z]{3}_[0-9]{4}),(\d+),(\d+)$')


def assess(plan:list[dict[str,str]],events:list[tuple[str,str,int,int]])->list[dict[str,str]]:
    by=defaultdict(lambda:defaultdict(list))
    for kind,target,cyc,offset in events:
        by[target][kind].append(offset)
    report=[]
    for p in plan:
        target=p['target_id'];info=by[target]
        a,b=(int(p[k]) for k in ('offset_a','offset_b'))
        status=[]
        for offset in (a,b):
            writes=sorted(info['WRITE']);reads=sorted(info['READ'])
            if target.startswith('MEM_'):
                good=any(w < offset < r for w in writes for r in reads if w<r)
                check='WRITE_LT_INJECT_LT_READ' if good else 'FAIL_MEM_LIFETIME'
            elif target in {'BUF_0030','BUF_0031','BUF_0032','BUF_0007','BUF_0017'}:
                prior=[w for w in writes if w<offset]
                later=[w for w in writes if w>=offset]
                # BUF_0007 next consumer: Mem1 word84 write, as profiled.
                upper=min(by['MEM_0002']['WRITE']) if target=='BUF_0007' and by['MEM_0002']['WRITE'] else None
                good=bool(prior) and (not later or min(later)>offset) and (upper is None or offset<upper)
                check='AFTER_OBSERVED_WRITE' if good else 'FAIL_BUF_LIFETIME'
            elif target=='DAT_0010':
                good=bool(info['FC_VALID']) and offset>max(info['FC_VALID'])
                check='AFTER_FC_VALID' if good else 'FAIL_DAT_HANDOFF'
            else:
                good=None;check='NOT_PROFILED'  # never infer timing safety
            status.append(check)
        report.append({'target_id':target,'class':p['target_class'],'offset_a':str(a),
                       'offset_b':str(b),'observed_write_offsets':';'.join(map(str,sorted(info['WRITE']))),
                       'observed_read_offsets':';'.join(map(str,sorted(info['READ']))),
                       'status_a':status[0],'status_b':status[1]})
    return report


def parse_log(path:Path)->list[tuple[str,str,int,int]]:
    text=path.read_text(encoding='utf-8',errors='replace')
    if 'FI_PROFILE_PASS:' not in text or 'FI_DISABLED_CONTROL_PASS' not in text:
        raise ValueError('Profile needs complete fault-free XSim PASS markers; '+str(path))
    if 'FI_RESULT_V2,' in text or 'FI_APPLIED scenario=' in text:
        raise ValueError('Profile unexpectedly contains a fault injection')
    events=[]
    for line in text.splitlines():
        match=PAT.fullmatch(line.strip())
        if match:
            e=match.groups();events.append((e[0],e[1],int(e[2]),int(e[3])))
    if not events:
        raise ValueError('Profile PASS but zero FI_PROFILE_EVENT lines; ensure new TB is compiled')
    return events


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--vivado',default='vivado')
    p.add_argument('--fileset',default='sim_1')
    p.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_profile'))
    p.add_argument('--execute',action='store_true')
    p.add_argument('--timeout-seconds',type=int,default=900)
    p.add_argument('--analyze-log',type=Path,help='Analyze an existing REAL fault-free profile log without invoking Vivado')
    args=p.parse_args()
    repo=args.repo.resolve()
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    records=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    selected=next(r for r in records if r['sample_id']=='IMG0000')
    out=(args.out_dir if args.out_dir.is_absolute() else repo/args.out_dir).resolve()
    out.mkdir(parents=True,exist_ok=True)
    done=out/'profile.done';log=out/'profile.log';tcl=out/'profile.tcl'
    tcl.write_text(make_tcl(args.project.resolve(),selected,done,args.fileset,
               repo/'fi/pilot400/samples/IMG0000',profile=True),encoding='utf-8')
    if args.analyze_log:
        log=args.analyze_log
    elif args.execute:
        if not args.project.is_file():
            raise ValueError('Vivado .xpr does not exist: '+str(args.project))
        done.unlink(missing_ok=True)
        cmd=[args.vivado,'-mode','batch','-source',str(tcl),'-nolog','-nojournal']
        simlog=args.project.resolve().parent/(args.project.stem+'.sim')/'sim_1'/'behav'/'xsim'/'simulate.log'
        oldtime=simlog.stat().st_mtime_ns if simlog.is_file() else -1
        with log.open('w',encoding='utf-8',errors='replace') as f:
            cp=subprocess.run(cmd,cwd=args.project.resolve().parent,
                              stdout=f,stderr=subprocess.STDOUT,timeout=args.timeout_seconds)
        if 'FI_PROFILE_PASS:' not in log.read_text(encoding='utf-8',errors='replace') and simlog.is_file() and simlog.stat().st_mtime_ns!=oldtime:
            with log.open('a',encoding='utf-8') as f:
                f.write('\n=== FRESH XSIM PROFILE TRANSCRIPT ===\n'+simlog.read_text(encoding='utf-8',errors='replace')+'\n')
        if cp.returncode or not done.is_file() or done.read_text().strip()!='CNN_PEP_PILOT400_TCL_DONE':
            raise ValueError('Profile XSim/Tcl failed; inspect '+str(log))
    else:
        print('PILOT400_PROFILE_DRY_RUN: Tcl generated, no XSim executed:',tcl)
        return
    events=parse_log(log)
    report=assess(plan,events)
    cols=('target_id','class','offset_a','offset_b','observed_write_offsets',
          'observed_read_offsets','status_a','status_b')
    output=out/'timing_window_audit.csv'
    write_csv(output,cols,report)
    print(f'PILOT400_PROFILE_ANALYZED: {len(events)} events from actual log; report -> {output}')
    for r in report:
        print(f' {r["target_id"]}: offsets {r["offset_a"]}/{r["offset_b"]} -> {r["status_a"]}/{r["status_b"]}')
    if any(x.startswith('FAIL_') for r in report for x in (r['status_a'],r['status_b'])):
        print('TIMING_GATE_FAIL: Do not claim all planned offsets are active; revise and refreeze manifest before execution')

if __name__=='__main__':
    try:main()
    except (ValueError,subprocess.TimeoutExpired) as e:
        raise SystemExit('PILOT400_PROFILE_FAIL: '+str(e))

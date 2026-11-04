#!/usr/bin/env python3
"""CNN_PEP V4H read-only intake of Windows V4f4F/G planning and audit evidence.

The source artifact format for V4f4F/G was not in the earlier handoff. This
script deliberately does NOT infer or approve their invocation semantics.
No subprocess, Vivado, XSim, commands, recovery or Full400 execution.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

SCHEMA='CNN_PEP_V4H_V4F4FG_EVIDENCE_INTAKE_V1'
FROZEN={
 'fi/config/fi_targets_candidates_v2.csv':'d9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
 'fi/config/pilot400_targets.csv':'98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
 'fi/scenarios/pilot400.csv':'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
 'fi/tb/tb_cnn_mnist_fi_pilot400.v':'2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13'}
SCRIPTS=(
 'fi/v4/scripts/production_runner_v4f4f.py',
 'fi/v4/scripts/audit_production_v4f4f.py',
 'fi/v4/scripts/production_blueprint_v4f4g.py',
)
DIRS=(
 'fi/results/v4f4f_plan_01',
 'fi/results/v4f4f_rehearsal_01',
 'fi/results/v4f4f_rehearsal_audit_01',
 'fi/results/v4f4g_blueprint_winfix_01',
 'fi/results/v4f4g_blueprint_audit_winfix_01',
)
DENY_TRUE={'full400_authorized','full400_executed','full400_runner_qualified',
           'xsim_executed','injection_executed','design_outcome_observed'}
DENY_NONZERO={'new_xsim_runs'}
MAX_SINGLE_BYTES=128*1024*1024
MAX_FILES=2500


def need(condition:bool,why:str):
    if not condition:raise ValueError(why)


def hash_file(p:Path)->str:
    need(p.is_file() and not p.is_symlink(),'missing/symlink file: '+str(p))
    need(p.stat().st_size<=MAX_SINGLE_BYTES,'artifact too large for bounded intake: '+str(p))
    h=hashlib.sha256()
    with p.open('rb') as f:
        while True:
            b=f.read(1048576)
            if not b:break
            h.update(b)
    return h.hexdigest()


def duplicate_json_keys(pairs):
    out={}
    for k,v in pairs:
        need(k not in out,'duplicate JSON key in input')
        out[k]=v
    return out


def scan_no_claim(node:Any,source:str):
    if isinstance(node,dict):
        for k,v in node.items():
            if k in DENY_TRUE:
                need(v is False,'unauthorized execution/approval flag '+k+' in '+source)
            if k in DENY_NONZERO:
                need(type(v) is int and v==0,'new XSim runs in '+source)
            scan_no_claim(v,source)
    elif isinstance(node,list):
        for v in node:scan_no_claim(v,source)


def collect(repo:Path):
    repo=repo.resolve()
    results=repo/'fi/results'
    need(results.is_dir() and not results.is_symlink(),'invalid repository/results')
    for rel,sha in FROZEN.items():
        need(hash_file(repo/rel)==sha,'frozen source hash drift: '+rel)
    inventory=[]
    for rel in SCRIPTS:
        p=repo/rel
        inventory.append({'path':rel,'bytes':p.stat().st_size,'sha256':hash_file(p),
                          'role':'SOURCE_UNREVIEWED'})
    for rel in DIRS:
        directory=repo/rel
        need(directory.is_dir() and not directory.is_symlink(),'required output directory missing: '+rel)
        files=sorted(directory.rglob('*'))
        need(all(not p.is_symlink() for p in files), 'symlink in evidence tree: '+rel)
        for p in files:
            if p.is_dir():continue
            need(p.is_file(),'unexpected filesystem node: '+str(p))
            relative=p.relative_to(repo).as_posix()
            item={'path':relative,'bytes':p.stat().st_size,'sha256':hash_file(p),
                  'role':'PLANNING_OR_AUDIT_SOURCE_NOT_SEMANTICALLY_VERIFIED'}
            if p.suffix.lower()=='.json':
                try:
                    data=json.loads(p.read_text(encoding='utf-8'),object_pairs_hook=duplicate_json_keys)
                except (UnicodeDecodeError,json.JSONDecodeError) as e:
                    raise ValueError('malformed JSON input: '+relative) from e
                scan_no_claim(data,relative)
            inventory.append(item)
            need(len(inventory)<=MAX_FILES,'intake file-count budget exceeded')
    need(len({i['path'] for i in inventory})==len(inventory),'duplicate paths')
    return {'schema':SCHEMA,
        'status':'V4H_EVIDENCE_INTAKE_ONLY_NOT_SEMANTIC_G6_AUDIT',
        'source_scope':'V4F4F_PLAN_REHEARSAL_AND_V4F4G_BLUEPRINT_AUDIT',
        'source_files':inventory,
        'total_source_files':len(inventory),
        'frozen_hashes':FROZEN,
        'invocation_contracts_semantically_verified':False,
        'v4f4f_g_live_source_independently_reviewed':False,
        'full400_authorized':False,'full400_runner_qualified':False,
        'full400_executed':False,'new_xsim_runs':0,
        'required_next_review':['Examine actual V4f4F/G source code and exact result schemas',
            'Independently compare all 400 blueprint tuples, plusargs and source pins',
            'Negative-test release guard, tampered contracts and orphan/resume cases',
            'Complete G0/G5/G7 and independent G6 production sign-off before execution']}


def run(repo:Path,out:Path|None,mode:str):
    repo=repo.resolve();data=collect(repo)
    if mode=='check':return data
    need(mode=='inventory','unsupported mode')
    need(out is not None,'--out-dir required')
    out=out if out.is_absolute() else repo/out
    need(out.parent.resolve()==(repo/'fi/results').resolve() and
         out.name.startswith('v4h_') and not out.exists(),
         'out must be an unused fi/results/v4h_* path')
    out.mkdir(mode=0o700)
    payload=(json.dumps(data,indent=2,sort_keys=True,ensure_ascii=False)+'\n').encode('utf-8')
    with (out/'intake.json').open('xb') as f:
        f.write(payload);f.flush();os.fsync(f.fileno())
    with (out/'SHA256SUMS.txt').open('x',encoding='ascii',newline='\n') as f:
        f.write(hashlib.sha256(payload).hexdigest()+'  intake.json\n')
        f.flush();os.fsync(f.fileno())
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('check','inventory'),default='check')
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--out-dir',type=Path)
    args=p.parse_args()
    try:
        d=run(args.repo,args.out_dir,args.mode)
    except (ValueError,OSError) as e:
        p.exit(2,'V4H_INTAKE_REFUSED: '+str(e)+'\n')
    print('V4H_INTAKE_PASS_INVENTORY_ONLY: %d source/evidence files; 4/4 frozen pins; '
          'G6 semantic review PENDING; 0 XSim; FULL400 BLOCKED' % d['total_source_files'])

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Run TEN independent fault-free RTL/golden checks on current source checkout.

Uses the unchanged Pilot400 TB in +PILOT_PROFILE_ONLY=1 mode: every sample is
checked at Pool1, Pool2, FC logits, ArgMax and 1342-cycle nominal latency.
A Python/preflight PASS alone is NEVER a control10 XSim PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from pilot400_common import ASSET_SIZES, SAMPLES, asset_lines, load_plan, sha256, verify_manifest
from run_pilot400 import make_tcl
from profile_pilot400 import SCHEMA_MARKER

CONTROL_RE = re.compile(r'^FI_DISABLED_CONTROL_PASS latency=(\d+) '
                        r'pool1=(\d+) pool2=(\d+) logits=(\d+) prediction=(\d+)$', re.M)


def fingerprint(repo: Path, weights_dir: Path,
                rtl_source: str = 'repo') -> dict[str, str]:
    from export_pilot400_samples import PARAMS
    provenance = repo/'fi/pilot400/samples/sample_provenance.json'
    if not provenance.is_file():
        raise ValueError('Missing real sample_provenance.json')
    provenance_data = json.loads(provenance.read_text(encoding='utf-8'))
    for name in PARAMS:
        path = weights_dir/name
        if not path.is_file() or sha256(path) != provenance_data.get('weight_hashes',{}).get(name):
            raise ValueError('P0 ROM weight/bias differs from exported samples: '+name)
    if rtl_source not in ('repo', 'vivado'):
        raise ValueError('Unknown RTL source authority: '+rtl_source)
    # In Vivado-authority mode the actual P0 RTL is frozen from the XSim .prj,
    # NOT from stale/unrelated Git repo copies (which are deliberately ignored).
    rtl_dir = repo/'rtl'
    rtl = sorted(rtl_dir.rglob('*.v')) if (rtl_source == 'repo' and rtl_dir.is_dir()) else []
    if rtl_source == 'repo' and len(rtl) < 20:
        raise ValueError('Cannot establish current P0 RTL source hash: expected RTL tree in repo/rtl')
    inputs = [repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v',
              repo/'fi/scenarios/pilot400.csv', provenance]
    inputs.extend(rtl)
    inputs.extend(weights_dir/name for name in PARAMS)
    for sid,_ in SAMPLES:
        for name,(count,digits) in ASSET_SIZES.items():
            path = repo/'fi/pilot400/samples'/sid/name
            asset_lines(path,count,digits)
            inputs.append(path)
    return {str(path.resolve()):sha256(path) for path in inputs}


def check_control_log(log: Path, sample_id: str, expected: int) -> None:
    text=log.read_text(encoding='utf-8',errors='replace')
    if any(token in text for token in ('ERROR:', 'FATAL:', 'FI_PROFILE_FAIL',
                                       'FI_DISABLED_CONTROL_FAIL', 'FI_INFRA_ERROR',
                                       'FI_TIMELINE_FAIL', 'Expected a switch')):
        raise ValueError(f'{sample_id}: XSim reported an error/fatal condition')
    if text.count('FI_PROFILE_PASS:') != 1 or text.count(SCHEMA_MARKER) != 1:
        raise ValueError(f'{sample_id}: missing fresh fault-free schema-V2 profile PASS')
    if 'FI_APPLIED scenario=' in text or 'FI_RESULT_V2,' in text:
        raise ValueError(f'{sample_id}: fault injected during control-only check')
    m=CONTROL_RE.findall(text)
    if len(m)!=1 or tuple(map(int,m[0]))!=(1342,676,200,10,expected):
        raise ValueError(f'{sample_id}: mismatched RTL numerical checks/latency/prediction {m}')


def validate_control10_gate(repo: Path, manifest: Path,
                            weights_dir: Path) -> None:
    """Required by full400; verify hashes, logs and all ten original XSim passes."""
    if not manifest.is_file():
        raise ValueError('Full400 requires independent control10 XSim results; run run_pilot400_control10.py --execute')
    info=json.loads(manifest.read_text(encoding='utf-8'))
    if info.get('status')!='PASS_10_OF_10' or len(info.get('records',[]))!=10:
        raise ValueError('Control10 manifest does not certify 10/10 XSim')
    authority=info.get('rtl_source','repo')
    if authority not in ('repo','vivado'):
        raise ValueError('Unsupported control10 RTL source authority')
    if authority == 'vivado' and info.get('runner_mode') != 'DIRECT_FRESH_SNAPSHOT':
        raise ValueError('Vivado-authority control10 requires a fresh direct XSim build')
    if info.get('input_hashes') != fingerprint(repo, weights_dir, authority):
        raise ValueError('Control10 recorded inputs/weights/goldens changed; rerun control10')
    if info.get('runner_mode') == 'DIRECT_FRESH_SNAPSHOT':
        b=info.get('snapshot_build',{})
        count_field=('verified_vivado_rtl_count' if authority == 'vivado'
                     else 'verified_repo_rtl_count')
        if (b.get('status') != 'FRESH_COMPILE_ELAB_PASS'
                or b.get('source_authority','repo') != authority
                or b.get(count_field,0) < 20):
            raise ValueError('Direct control10 snapshot build/RTL authority metadata is incomplete')
        srcs=b.get('source_hashes',{})
        if len(srcs)<20:
            raise ValueError('Direct control10 lacks actual Vivado source hashes')
        for name,expected_hash in srcs.items():
            path=Path(name)
            if not path.is_file() or sha256(path)!=expected_hash:
                raise ValueError('Vivado snapshot source changed: '+name)
        prj=Path(b.get('compile_prj_copy',''))
        if not prj.is_file() or sha256(prj)!=b.get('compile_prj_sha256'):
            raise ValueError('XSim compile project provenance is missing/changed')
        from pilot400_direct_xsim import project_source_hashes
        actual_sources,actual_count=project_source_hashes(
            prj,repo,rtl_source=authority,project=Path(info['project']))
        if actual_sources != srcs or actual_count != b.get(count_field):
            raise ValueError('Control10 Vivado .prj source set/hash changed')
        for stage in ('compile','elaborate'):
            entry=b.get('logs',{}).get(stage,{})
            path=Path(entry.get('path',''))
            if not path.is_file() or sha256(path)!=entry.get('sha256'):
                raise ValueError('XSim build log missing/changed: '+stage)
    for record,(sid,_) in zip(info['records'],SAMPLES):
        log=Path(record.get('log',''))
        if record.get('sample_id')!=sid or not log.is_file() or sha256(log)!=record.get('sha256'):
            raise ValueError('Control10 log missing/changed for '+sid)
        expected=asset_lines(repo/'fi/pilot400/samples'/sid/'prediction.hex',1,2)[0]
        check_control_log(log,sid,expected)


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--vivado',default='vivado')
    p.add_argument('--fileset',default='sim_1')
    p.add_argument('--weights-dir',type=Path,
                   help='Actual original P0 data/ directory containing six ROM files')
    p.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400_control10_v2'))
    p.add_argument('--timeout-seconds',type=int,default=900)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--direct-xsim',action='store_true',
                   help='Windows: fresh scripts_only + compile/elaborate ONCE, then 10 independent quoted-plusarg XSim runs')
    p.add_argument('--rtl-source',choices=('repo','vivado'),default='repo',
                   help='Authority for synthesizable HDL: repo/rtl (strict default), or actual Simulation.srcs with archived SHA256')
    args=p.parse_args()
    if args.rtl_source == 'vivado' and not args.direct_xsim:
        p.error('--rtl-source vivado requires --direct-xsim')
    repo=args.repo.resolve()
    project=args.project.resolve()
    out=(args.out_dir if args.out_dir.is_absolute() else repo/args.out_dir).resolve()
    if out.exists():
        raise ValueError('Control10 output directory exists; use a NEW --out-dir to preserve evidence: '+str(out))
    plans=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    rows=verify_manifest(repo/'fi/scenarios/pilot400.csv',plans)
    by_sample={s:next(row for row in rows if row['sample_id']==s) for s,_ in SAMPLES}
    if args.execute:
        if not project.is_file() or not args.weights_dir:
            raise ValueError('--execute needs valid --project and --weights-dir')
        signatures=fingerprint(repo,args.weights_dir.resolve(),args.rtl_source)
    else:
        signatures={}
    out.mkdir(parents=True,exist_ok=False)
    records=[]
    build_info=None
    try:
        if args.execute and args.direct_xsim:
            from pilot400_direct_xsim import execute_direct_control10
            _,build_info=execute_direct_control10(
                repo,project,args.vivado,args.fileset,out,by_sample,
                args.timeout_seconds,check_control_log,records,
                rtl_source=args.rtl_source)
        else:
            for sid,_ in SAMPLES:
                sample_dir=repo/'fi/pilot400/samples'/sid
                done=out/(sid+'.done')
                tcl=out/(sid+'.tcl')
                log=out/(sid+'.log')
                tcl.write_text(make_tcl(project,by_sample[sid],done,args.fileset,
                                        sample_dir,profile=True),encoding='utf-8')
                if not args.execute:
                    continue
                done.unlink(missing_ok=True)
                simlog=(project.parent/(project.stem+'.sim')/'sim_1'/'behav'/'xsim'/'simulate.log')
                oldtime=simlog.stat().st_mtime_ns if simlog.is_file() else -1
                with log.open('w',encoding='utf-8',errors='replace') as f:
                    cp=subprocess.run([args.vivado,'-mode','batch','-source',str(tcl),
                                       '-nolog','-nojournal'],cwd=project.parent,
                                      stdout=f,stderr=subprocess.STDOUT,
                                      timeout=args.timeout_seconds,check=False)
                text=log.read_text(encoding='utf-8',errors='replace')
                if 'FI_PROFILE_PASS:' not in text and simlog.is_file() and simlog.stat().st_mtime_ns!=oldtime:
                    with log.open('a',encoding='utf-8') as f:
                        f.write('\n=== FRESH XSIM CONTROL TRANSCRIPT ===\n'+
                                simlog.read_text(encoding='utf-8',errors='replace')+'\n')
                if cp.returncode or not done.is_file() or done.read_text().strip()!='CNN_PEP_PILOT400_TCL_DONE':
                    raise ValueError('XSim/Tcl failed for '+sid+'; inspect '+str(log))
                expected=asset_lines(sample_dir/'prediction.hex',1,2)[0]
                check_control_log(log,sid,expected)
                records.append({'sample_id':sid,'prediction':expected,
                                'log':str(log),'sha256':sha256(log)})
                print('CONTROL10_XSIM_SAMPLE_PASS:',sid,flush=True)
        if not args.execute:
            print('CONTROL10_DRY_RUN: 10 Tcl files generated; 0 RTL simulations')
            return
        result={'status':'PASS_10_OF_10','finished_utc':datetime.now(timezone.utc).isoformat(),
                'project':str(project),'rtl_source':args.rtl_source,
                'input_hashes':signatures,'records':records}
        if build_info is not None:
            result['runner_mode']='DIRECT_FRESH_SNAPSHOT'
            result['snapshot_build']=build_info
        dest=out/'control10_manifest.json'
        dest.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        validate_control10_gate(repo,dest,args.weights_dir.resolve())
        print(f'CONTROL10_XSIM_PASS: 10/10 independent fault-free RTL/golden runs (rtl_source={args.rtl_source})',flush=True)
    except Exception:
        (out/'control10_failed_attempt.json').write_text(
            json.dumps({'status':'NOT_VERIFIED','records_completed':records},indent=2)+'\n',
            encoding='utf-8')
        raise


if __name__ == '__main__':
    try:main()
    except (ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit('CONTROL10_FAIL: '+str(exc))

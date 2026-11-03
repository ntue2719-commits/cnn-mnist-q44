#!/usr/bin/env python3
"""Serial Vivado/XSim runner for the deterministic 400-scenario selected pilot.

Default: DRY_RUN (Tcl and manifest generation only). --execute is required.
Reuses the locked FI_RESULT_V2 parser; no changes to P0 RTL or existing FI TBs.
Resume is allowed only when immutable content hashes match the earlier batch.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from pilot400_common import (FIELDS,ASSET_SIZES,sha256,load_plan,verify_manifest,
                             validate_assets,write_csv)
from preflight_pilot400 import preflight

TOP='tb_cnn_mnist_fi_pilot400'


def safe_tcl(value:str)->str:
    if '{' in value or '}' in value or '\n' in value or '\r' in value:
        raise ValueError('Unsafe Tcl braces/newline in path/plusarg')
    return '{'+value+'}'


def make_tcl(project:Path,row:dict[str,str],done:Path,fileset:str,sample_dir:Path,profile:bool=False)->str:
    opts=[f'-testplusarg FI_OFFSET={row["injection_offset"]}',
          f'-testplusarg SCENARIO_ID={row["scenario_id"]}',
          f'-testplusarg TARGET_ID={row["target_id"]}',
          f'-testplusarg FI_BIT={row["bit_index"]}',
          f'-testplusarg SAMPLE_ID={row["sample_id"]}']
    if profile:
        opts.append('-testplusarg PILOT_PROFILE_ONLY=1')
    paths={'INPUT_FILE':'input.hex','POOL1_FILE':'golden_pool1.hex',
           'POOL2_FILE':'golden_pool2.hex','LOGITS_FILE':'golden.hex',
           'PREDICTION_FILE':'prediction.hex'}
    for key,name in paths.items():
        path=(sample_dir/name).resolve().as_posix()
        if ' ' in path:
            raise ValueError('XSim testplusarg paths must have no spaces: '+path)
        opts.append(f'-testplusarg {key}={path}')
    if not fileset.isidentifier():
        raise ValueError('Invalid fileset name')
    return (
        '# PILOT400 single scenario; DOES NOT modify any P0 RTL.\n'
        f'open_project {safe_tcl(project.resolve().as_posix())}\n'
        'if {[catch {\n'
        f'    set sim [get_filesets {safe_tcl(fileset)}]\n'
        '    if {[llength $sim] != 1} {error {Expected one sim fileset}}\n'
        '    catch {close_sim -force}\n'
        f'    set_property top {TOP} $sim\n'
        '    set_property xsim.compile.incremental false $sim\n'
        '    catch {reset_simulation -mode behavioral sim_1}\n'
        '    update_compile_order -fileset $sim\n'
        '    set_property -name {xsim.simulate.xsim.more_options} -value '
        f'{safe_tcl(" ".join(opts))} -objects $sim\n'
        '    launch_simulation\n'
        '    run all\n'
        '} err opts]} {\n'
        '    catch {puts stderr "CNN_PEP_PILOT400_TCL_ERROR: $err"}\n'
        '    exit 1\n'
        '}\n'
        'catch {close_sim -force}\n'
        'close_project\n'
        f'set fp [open {safe_tcl(done.resolve().as_posix())} w]\n'
        'puts $fp {CNN_PEP_PILOT400_TCL_DONE}\n'
        'close $fp\n'
        '# No stdout Tcl puts after XSim $finish: stdout channel may be closed.\n'
        'exit 0\n'
    )


def run_one(vivado:str, project:Path, tcl:Path, done:Path, log:Path,
            timeout:int)->None:
    done.unlink(missing_ok=True)
    log.parent.mkdir(parents=True,exist_ok=True)
    cmd=[vivado,'-mode','batch','-source',str(tcl),'-nolog','-nojournal']
    # Avoid accepting stale Simulator transcript from earlier runs.
    simlog=project.parent/(project.stem+'.sim')/'sim_1'/'behav'/'xsim'/'simulate.log'
    oldtime=simlog.stat().st_mtime_ns if simlog.exists() else -1
    with log.open('w',encoding='utf-8',errors='replace') as f:
        try:
            cp=subprocess.run(cmd,cwd=project.parent,stdout=f,stderr=subprocess.STDOUT,
                              timeout=timeout,check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'XSim timed out after {timeout}s: {log}') from exc
        except OSError as exc:
            raise RuntimeError(f'Cannot start Vivado ({vivado}): {exc}') from exc
    content=log.read_text(encoding='utf-8',errors='replace')
    if 'FI_RESULT_V2,' not in content and simlog.is_file() and simlog.stat().st_mtime_ns!=oldtime:
        # Only use a transcript actually updated by this execution; never stale.
        transcript=simlog.read_text(encoding='utf-8',errors='replace')
        with log.open('a',encoding='utf-8') as f:
            f.write('\n=== XSIM TRANSCRIPT ===\n'+transcript+'\n')
        content=log.read_text(encoding='utf-8',errors='replace')
    if cp.returncode!=0 or not done.is_file() or done.read_text().strip()!='CNN_PEP_PILOT400_TCL_DONE':
        raise RuntimeError(f'Vivado/unique Tcl done-file failed (rc={cp.returncode}): {log}')
    if content.count('FI_RESULT_V2,')!=1 or content.count('FI_APPLIED scenario=')!=1:
        raise RuntimeError(f'Expected exactly one FI_APPLIED and FI_RESULT_V2: {log}')
    if 'FI_DISABLED_CONTROL_PASS' not in content or 'CNN_PEP FI CAMPAIGN V2: PASS' not in content:
        raise RuntimeError(f'Fault-free control or FI TB validation failed: {log}')
    if 'FI_TIMELINE_FAIL' in content or 'FI_INFRA_ERROR' in content:
        raise RuntimeError(f'Unexpected FI assertion/infrastructure error: {log}')
    # Strict parser verifies bit flip, offset, counts, valid outcome.
    from parse_fi_v2 import parse_one
    parse_one(log)


def digests_for_batch(repo:Path,selected:list[dict[str,str]])->dict:
    dependencies=['fi/config/pilot400_targets.csv','fi/config/fi_targets_candidates_v2.csv',
                  'fi/scenarios/pilot400.csv','fi/tb/tb_cnn_mnist_fi_pilot400.v']
    files={name:sha256(repo/name) for name in dependencies}
    for source in sorted((repo/'rtl').rglob('*.v')) if (repo/'rtl').is_dir() else []:
        name=source.relative_to(repo).as_posix()
        files['p0/'+name]=sha256(source)
    for source in sorted((repo/'data').glob('*.hex')) if (repo/'data').is_dir() else []:
        name=source.relative_to(repo).as_posix()
        files['p0/'+name]=sha256(source)
    golden=repo/'python/golden_model.py'
    if golden.is_file():
        files['p0/python/golden_model.py']=sha256(golden)
    for sid in sorted(set(r['sample_id'] for r in selected)):
        for name in ASSET_SIZES:
            fn=f'fi/pilot400/samples/{sid}/{name}'
            if (repo/fn).is_file():
                files[fn]=sha256(repo/fn)
    files['selected_scenarios_sha256']=hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest()
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--project',type=Path,required=True,help='Existing Simulation.xpr')
    p.add_argument('--vivado',default='vivado')
    p.add_argument('--fileset',default='sim_1')
    p.add_argument('--out-dir',type=Path,default=Path('fi/results/pilot400'))
    p.add_argument('--sample',default='',help='Optional IMG0000..IMG0009 filter (safe initial shakedown)')
    p.add_argument('--start',type=int,default=1,help='1-based index in selected set')
    p.add_argument('--count',type=int,default=0,help='0 means rest of selected set')
    p.add_argument('--repeats',type=int,choices=(1,2),default=1)
    p.add_argument('--timeout-seconds',type=int,default=900)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--p0-weights-dir',type=Path,help='Actual 6 P0 weight/bias HEX files used by ROMs')
    p.add_argument('--timing-audit',type=Path,default=Path('fi/results/pilot400_profile/timing_window_audit.csv'))
    p.add_argument('--allow-candidates',action='store_true',help='Accept unverified timing windows as exploration, not evidence')
    p.add_argument('--resume',action='store_true',help='Skip existing validated logs only with matching full hash lock')
    args=p.parse_args()
    repo=args.repo.resolve()
    project=args.project.resolve()
    rows=preflight(repo,require_assets=False)
    if args.sample:
        if args.sample not in {r['sample_id'] for r in rows}:
            raise ValueError('Unknown sample: '+args.sample)
        rows=[r for r in rows if r['sample_id']==args.sample]
    if args.start<1 or args.count<0:
        raise ValueError('start must be >=1, count >=0')
    end=None if args.count==0 else args.start-1+args.count
    selected=rows[args.start-1:end]
    if not selected:
        raise ValueError('No scenarios selected')
    if args.execute:
        if not project.is_file():
            raise ValueError(f'Vivado project absent: {project}')
        if not (repo/'fi/scripts/parse_fi_v2.py').is_file():
            raise ValueError('Original strict parser fi/scripts/parse_fi_v2.py missing')
        for sid in sorted(set(r['sample_id'] for r in selected)):
            for fname,(n,d) in ASSET_SIZES.items():
                from pilot400_common import asset_lines
                asset_lines(repo/'fi/pilot400/samples'/sid/fname,n,d)
        planned_targets={r['target_id']:r for r in load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')}
        questionable=sorted({r['target_id'] for r in selected
                             if planned_targets[r['target_id']]['evidence_status']=='CANDIDATE'})
        if len(selected)==400:
            if args.p0_weights_dir is None:
                raise ValueError('Full400 requires --p0-weights-dir to bind golden samples to P0 ROM weights')
            from export_pilot400_samples import PARAMS
            provenance=repo/'fi/pilot400/samples/sample_provenance.json'
            if not provenance.is_file():
                raise ValueError('Full400 requires real sample_provenance.json from exporter')
            info=json.loads(provenance.read_text(encoding='utf-8'))
            for name in PARAMS:
                actual=(args.p0_weights_dir/name).resolve()
                if not actual.is_file() or sha256(actual)!=info['weight_hashes'].get(name):
                    raise ValueError('P0 weights differ from exported golden sample weights: '+name)
            audit=(args.timing_audit if args.timing_audit.is_absolute() else repo/args.timing_audit)
            if not audit.is_file():
                raise ValueError('Full400 requires a real fault-free XSim timing profile: '+str(audit))
            from pilot400_common import read_csv
            profiles=read_csv(audit,('target_id','offset_a','offset_b','status_a','status_b'))
            by_id={x['target_id']:x for x in profiles}
            for target,plan in planned_targets.items():
                r=by_id.get(target)
                if r is None or r['offset_a']!=plan['offset_a'] or r['offset_b']!=plan['offset_b']:
                    raise ValueError('Timing profile is missing/outdated for '+target)
                if any(x.startswith('FAIL_') for x in (r['status_a'],r['status_b'])):
                    raise ValueError('Timing profile rejected planned injection window for '+target)
        if questionable and not args.allow_candidates:
            raise ValueError('Selected targets have unverified injection windows: '+', '.join(questionable)+
                             '; use --allow-candidates ONLY for exploratory runs')
    dest=(args.out_dir if args.out_dir.is_absolute() else repo/args.out_dir).resolve()
    digests=digests_for_batch(repo,selected)
    if args.p0_weights_dir is not None:
        from export_pilot400_samples import PARAMS
        for name in PARAMS:
            f=(args.p0_weights_dir/name).resolve()
            if not f.is_file(): raise ValueError('Missing P0 weight: '+str(f))
            digests['external_P0_weight/'+name]=sha256(f)
    meta_file=dest/'pilot400_run_manifest.json'
    if args.resume:
        if not meta_file.is_file():
            raise ValueError('Cannot resume: missing prior run metadata')
        previous=json.loads(meta_file.read_text(encoding='utf-8'))
        if previous['input_hashes']!=digests or previous['project']!=str(project) or previous['selected_count']!=len(selected) or previous['repeats']!=args.repeats:
            raise ValueError('RESUME REFUSED: project, assets, plan, TB, or selection changed')
    elif meta_file.exists():
        raise ValueError(f'Output already has a run manifest; choose another --out-dir or use --resume: {dest}')
    dest.mkdir(parents=True,exist_ok=True)
    if not args.resume:
        meta={'created_utc':datetime.now(timezone.utc).isoformat(),
              'status':'RUNNING' if args.execute else 'DRY_RUN_ONLY',
              'full_manifest_count':400,'selected_count':len(selected),
              'executed_count':0,'project':str(project),'vivado':args.vivado,
              'repeats':args.repeats,'input_hashes':digests}
        meta_file.write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
    else:
        meta=previous
        if args.execute:
            meta['status']='RUNNING'
    manifest=dest/'selected_scenarios.csv'
    if manifest.is_file() and args.resume:
        old=manifest.read_bytes()
        with tempfile_manifest(dest,selected) as expected:
            if old!=expected.read_bytes():
                raise ValueError('Selected subset CSV changed; cannot resume')
    else:
        write_csv(manifest,FIELDS,selected)
    print(f'PILOT400 {"EXECUTE" if args.execute else "DRY_RUN"}: {len(selected)}/400 selected rows, repeats={args.repeats}',flush=True)
    hashes=[]
    try:
        for iteration in range(1,args.repeats+1):
            run_dir=dest/f'repeat_{iteration:02d}'
            logs=run_dir/'logs'; tcls=run_dir/'tcl'; done_dir=run_dir/'done'
            for d in (tcls,done_dir): d.mkdir(parents=True,exist_ok=True)
            if args.execute: logs.mkdir(parents=True,exist_ok=True)
            for no,r in enumerate(selected,1):
                sid=r['scenario_id']; done=done_dir/f'{sid}.done'; log=logs/f'{sid}.log'
                tcl=tcls/f'{sid}.tcl';sample_dir=repo/'fi/pilot400/samples'/r['sample_id']
                contents=make_tcl(project,r,done,args.fileset,sample_dir)
                if tcl.is_file() and args.resume and tcl.read_text()!=contents:
                    raise ValueError(f'Tcl changed since previous run: {tcl}')
                tcl.write_text(contents,encoding='utf-8')
                if not args.execute: continue
                if args.resume and log.is_file():
                    from parse_fi_v2 import parse_one
                    try:
                        rec=parse_one(log)
                        if rec['scenario_id']==sid and done.is_file() and done.read_text().strip()=='CNN_PEP_PILOT400_TCL_DONE':
                            print(f'[{iteration}] RESUME verified {no}/{len(selected)} {sid}',flush=True)
                            continue
                    except (ValueError,OSError):
                        pass
                print(f'[{iteration}] RUN {no}/{len(selected)} {sid} {r["sample_id"]} {r["target_id"]}',flush=True)
                run_one(args.vivado,project,tcl,done,log,args.timeout_seconds)
                meta['executed_count']+=1
                meta_file.write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
            if args.execute:
                output=run_dir/'pilot400_results.csv'
                command=[sys.executable,str(repo/'fi/scripts/parse_fi_v2.py'),'--logs-dir',str(logs),
                         '--scenarios',str(manifest),'--out',str(output)]
                cp=subprocess.run(command,cwd=repo,capture_output=True,text=True,check=False)
                if cp.returncode:
                    raise RuntimeError('Strict parser rejected replay: '+cp.stdout+'\n'+cp.stderr)
                h=sha256(output);hashes.append(h)
                meta[f'repeat_{iteration:02d}_sha256']=h
                meta_file.write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
                print('  Strict parse PASS:',output,'SHA256',h,flush=True)
        if args.execute:
            if len(hashes)==2 and hashes[0]!=hashes[1]:
                raise RuntimeError('REPLAY NONDETERMINISTIC: normalized CSV bytes differ')
            meta['status']='EXECUTED_AND_PARSED_'+str(len(selected))
            print('PILOT400_XSIM_PASS: '+str(len(selected))+' selected scenarios validated'+
                  (' twice, byte-identical' if args.repeats==2 else '')+' (not necessarily all 400)')
        else:
            print('PILOT400_DRY_RUN_PASS: Tcl prepared only; 0 XSim executions')
    except Exception:
        meta['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        meta_file.write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')


# Create a temporary CSV only for comparison, without rewriting the locked selected file.
from contextlib import contextmanager
import tempfile
@contextmanager
def tempfile_manifest(dest,selected):
    with tempfile.TemporaryDirectory() as folder:
        f=Path(folder)/'selected.csv'
        write_csv(f,FIELDS,selected)
        yield f

if __name__=='__main__':
    try: main()
    except (ValueError,RuntimeError) as e: raise SystemExit('PILOT400_RUN_FAIL: '+str(e))

#!/usr/bin/env python3
"""Pilot400 V3a: directed XSim fault-injection integrity trials for CANDIDATE sites.

This tool DOES NOT turn a catalog CANDIDATE into a proven consumed fault.
It checks reproducible XOR injection + temporal eligibility + outcome, and
keeps downstream value acceptance UNKNOWN unless an explicit value-aware
monitor is already present. P0 RTL, FI TB and the frozen manifest are untouched.

Windows execute: freshly build ONE snapshot from Vivado-authoritative RTL,
then run independent XSim processes with quoted NAME=VALUE plusargs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys

from pilot400_common import (FIELDS, ASSET_SIZES, SAMPLES, asset_lines,
                             load_plan, sha256, verify_manifest, write_csv)
from pilot400_direct_xsim import (SNAPSHOT, TOP, call_checked, call_cmd,
                                  classify_elaboration, make_cmd_run,
                                  make_cmd_stage, prepare_tcl,
                                  project_source_hashes, snapshot_signature,
                                  verify_scripts_only_evidence)
from parse_fi_v2 import parse_one
from run_pilot400 import validate_full_timing_gate
from run_pilot400_control10 import validate_control10_gate

APPLIED_RE = re.compile(
    r'^FI_APPLIED scenario=(\S+) target=(\S+) class=(\S+) bit=(\d+) '
    r'old=([01]) new=([01]) requested=(\d+)$', re.M)
# Vivado verbose logs echo Tcl source lines containing e.g.
# '# catch {puts stderr \"...TCL_ERROR: ...\"}', which are NOT errors.
ERROR_LINE_RE = re.compile(r'^(?:ERROR:|FATAL:|Fatal:|\*\* Error:)', re.M)
BAD_MARKERS = ('FI_INFRA_ERROR:', 'FI_TIMELINE_FAIL:',
               'Expected a switch', 'FI_DISABLED_EQUIVALENCE_FAIL:')
REPORT_FIELDS = (
    *FIELDS, 'injection_verified', 'temporal_window_verified',
    'value_aware_consumer_proof', 'checkpoint_numeric_delta',
    'final_output_or_protocol_effect', 'pool1_mismatch', 'pool2_mismatch',
    'logits_mismatch', 'first_bad_stage', 'outcome', 'log_sha256')
TARGET_FIELDS = ('target_id', 'target_class', 'scenarios_planned',
                 'scenarios_executed', 'inject_pass', 'value_consumer_proofs',
                 'stage_checkpoint_deltas', 'final_or_protocol_effects',
                 'status')


def candidate_targets(plan: list[dict[str, str]],
                      explicit: tuple[str, ...] = ()) -> list[dict[str,str]]:
    """Select ONLY CANDIDATE targets; do not implicitly upgrade old catalog."""
    pending = {p['target_id']:p for p in plan if p['evidence_status'] == 'CANDIDATE'}
    if explicit:
        requested = set(explicit)
        if len(requested) != len(explicit) or requested - set(pending):
            raise ValueError('Invalid/duplicate/non-CANDIDATE requested target(s): '+
                             ', '.join(sorted(requested - set(pending))))
        return [p for p in plan if p['target_id'] in requested]
    if len(pending) != 14:
        raise ValueError('Expected 14 CANDIDATE sites in frozen Pilot400 plan; got '+
                         str(len(pending))+'. Review current source before proceeding.')
    return [p for p in plan if p['target_id'] in pending]


def select_scenarios(rows: list[dict[str,str]], targets: list[dict[str,str]],
                     samples: tuple[str,...]) -> list[dict[str,str]]:
    if not samples or len(set(samples)) != len(samples):
        raise ValueError('Provide one or more unique sample IDs')
    if any(x not in {s for s,_ in SAMPLES} for x in samples):
        raise ValueError('Unknown IMG sample: '+repr(samples))
    desired = {p['target_id']:p for p in targets}
    chosen = [r for r in rows if r['target_id'] in desired and r['sample_id'] in samples]
    for p in targets:
        for sample in samples:
            rr = [r for r in chosen if r['target_id'] == p['target_id'] and
                  r['sample_id'] == sample]
            expected = {(p['bit_index'],p['offset_a']),
                        (p['bit_index_b'],p['offset_b'])}
            actual = {(r['bit_index'],r['injection_offset']) for r in rr}
            if len(rr) != 2 or actual != expected:
                raise ValueError(f'{p["target_id"]}/{sample}: missing/altered paired FI configs')
    if len(chosen) != 2*len(targets)*len(samples):
        raise ValueError('Directed selection cardinality mismatch')
    return chosen


def inspect_applied_and_result(log: Path, scenario: dict[str,str]) -> dict[str,str]:
    """A strict FI parser PASS is a mechanism PASS, *not* consumer evidence."""
    txt = log.read_text(encoding='utf-8', errors='replace')
    if ERROR_LINE_RE.search(txt) or any(marker in txt for marker in BAD_MARKERS):
        raise ValueError(f'{scenario["scenario_id"]}: error/fatal marker in FI log')
    applied = APPLIED_RE.findall(txt)
    if len(applied) != 1:
        raise ValueError(f'{scenario["scenario_id"]}: expected exactly one FI_APPLIED, got {len(applied)}')
    sid, tid, cls, bit, before, after, requested = applied[0]
    if (sid != scenario['scenario_id'] or tid != scenario['target_id'] or
        cls != scenario['target_class'] or bit != scenario['bit_index'] or
        int(requested) < 0 or int(before) ^ int(after) != 1):
        raise ValueError(f'{scenario["scenario_id"]}: FI_APPLIED identity/XOR mismatch')
    parsed = parse_one(log)
    for field in FIELDS:
        if field in scenario and parsed[field] != scenario[field]:
            raise ValueError(f'{sid}: strict FI_RESULT mismatch: {field}')
    if int(parsed['requested_cycle']) != int(requested):
        raise ValueError(f'{sid}: applied requested cycle != FI_RESULT_V2')
    if txt.count('FI_DISABLED_CONTROL_PASS ') != 1:
        raise ValueError(f'{sid}: missing/duplicated fault-free precheck')
    if txt.count('CNN_PEP FI CAMPAIGN V2: PASS') != 1:
        raise ValueError(f'{sid}: missing/duplicated FI mechanism PASS')
    # These target-specific assertions are already validated by the existing TB.
    # For the 14 candidates they are normally absent, so do NOT claim a
    # changed consumer input from an ordinary checkpoint mismatch.
    value_proof = 'NOT_PROVED'
    if tid == 'MEM_0002' and 'FI_TIMELINE_MEM_PASS ' in txt:
        value_proof = 'VERIFIED_ARITHMETIC_INPUT'
    elif tid == 'BUF_0031' and 'FI_TIMELINE_BUF_PASS ' in txt:
        value_proof = 'VERIFIED_ARITHMETIC_INPUT'
    elif tid == 'DAT_0010' and 'FI_DAT_ARGMAX_CONSUME ' in txt:
        value_proof = 'VERIFIED_ARGMAX_INPUT'
    checkpoint = any(int(parsed[k]) > 0 for k in ('pool1_mismatch','pool2_mismatch'))
    downstream = (int(parsed['logits_mismatch']) > 0 or parsed['outcome'] in
                  ('NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL'))
    return {**{key:scenario[key] for key in FIELDS},
            'injection_verified':'1', 'temporal_window_verified':'1',
            'value_aware_consumer_proof':value_proof,
            'checkpoint_numeric_delta':str(int(checkpoint)),
            'final_output_or_protocol_effect':str(int(downstream)),
            'pool1_mismatch':parsed['pool1_mismatch'],
            'pool2_mismatch':parsed['pool2_mismatch'],
            'logits_mismatch':parsed['logits_mismatch'],
            'first_bad_stage':parsed['first_bad_stage'],
            'outcome':parsed['outcome'], 'log_sha256':sha256(log)}


def summarize_targets(targets: list[dict[str,str]], results: list[dict[str,str]],
                      n_samples: int) -> list[dict[str,str]]:
    summaries=[]
    for target in targets:
        tid=target['target_id']
        matching=[r for r in results if r['target_id']==tid]
        expected=2*n_samples
        summary={
            'target_id':tid, 'target_class':target['target_class'],
            'scenarios_planned':str(expected),
            'scenarios_executed':str(len(matching)),
            'inject_pass':str(sum(r['injection_verified']=='1' for r in matching)),
            'value_consumer_proofs':str(sum(r['value_aware_consumer_proof']!='NOT_PROVED' for r in matching)),
            'stage_checkpoint_deltas':str(sum(r['checkpoint_numeric_delta']=='1' for r in matching)),
            'final_or_protocol_effects':str(sum(r['final_output_or_protocol_effect']=='1' for r in matching)),
            'status':'RUNTIME_MECHANISM_VALIDATED_CONSUMER_UNPROVEN'
                     if len(matching)==expected and all(r['injection_verified']=='1' for r in matching)
                     else 'INCOMPLETE',
        }
        summaries.append(summary)
    return summaries


def evidence_md(targets: list[dict[str,str]], rows: list[dict[str,str]],
                summaries: list[dict[str,str]], executed: bool,
                hashes: dict[str,str]) -> str:
    lines=['# Pilot400 Target-Level V3a — Directed Injection Evidence','',
           '> This is a controlled, directed exploratory set. It is NOT the full 400, '
           'NOT a hardware SEU population estimate, and NOT an automatic target promotion.',
           '', f'- Selected scenarios: **{len(rows)}**',
           f'- XSim executions verified: **{sum(int(s["scenarios_executed"]) for s in summaries)}**',
           f'- Status: **{"EXECUTED" if executed else "DRY_RUN_ONLY"}**','',
           '| Class | Target | n | Injection | Consumer value proof | Checkpoint deltas | Final/protocol effects |',
           '|---|---|---:|---:|---:|---:|---:|']
    for s in summaries:
        lines.append(f'| {s["target_class"]} | {s["target_id"]} | {s["scenarios_executed"]}/{s["scenarios_planned"]} | '
                     f'{s["inject_pass"]} | {s["value_consumer_proofs"]} | '
                     f'{s["stage_checkpoint_deltas"]} | {s["final_or_protocol_effects"]} |')
    lines += ['', '## Interpretation and next gate','',
              '- Every `injection_verified=1` is a strict parsed single-bit-XOR deposit and immediate readback.',
              '- `temporal_window_verified=1` means *fault-free* edge eligibility was validated from an authenticated V2 profile.',
              '- `value_aware_consumer_proof=NOT_PROVED` means the changed value has **not** been demonstrated at a downstream consumer. It does NOT mean it was masked.',
              '- Pool1/Pool2 mismatches demonstrate numeric effects at those particular checkpoints, not necessarily an arithmetic-input acceptance proof.',
              '- `FINAL_OUTPUT_MATCH` alone does not imply no intermediate deviation; `WRONG_LOGITS_SAME_LABEL` is not a wrong prediction.',
              '- Do not change `evidence_status=CANDIDATE` solely from this report. Next: add fault-aware producer/consumer/lifetime instrumentation per target, then validate both bit/time configurations.',
              '- Do not launch full400 until target-level evidence requirements and preserved Control10/timing gates are rechecked.',
              '', '## Locked hashes', '']
    lines.extend(f'- `{name}`: `{digest}`' for name,digest in sorted(hashes.items()))
    lines.append('')
    return '\n'.join(lines)


def build_verified_snapshot(repo:Path, project:Path, vivado:str, fileset:str,
                            out:Path, timeout:int) -> dict:
    """One fresh Vivado .prj -> xvlog/xelab; accept FULL_BUILD or incremental relink."""
    if os.name != 'nt':
        raise ValueError('XSim execute requires Windows')
    binary=Path(shutil.which(vivado) or vivado).resolve()
    if not binary.is_file():
        raise ValueError('Vivado binary does not exist: '+str(binary))
    root=binary.parent.parent
    simdir=(project.parent/(project.stem+'.sim')/fileset/'behav'/'xsim').resolve()
    tcl=out/'prepare_snapshot.tcl'
    marker=out/'scripts_only_success.marker'
    marker.unlink(missing_ok=True)
    tcl.write_text(prepare_tcl(project,fileset,marker),encoding='ascii')
    prep_log=out/'prepare_snapshot.log'
    call_checked([str(binary),'-mode','batch','-source',str(tcl.resolve()),
                  '-nolog','-nojournal'],project.parent,prep_log,timeout,'Vivado scripts_only')
    verify_scripts_only_evidence(marker,prep_log)
    src_prj=simdir/(TOP+'_vlog.prj')
    for f in (src_prj, simdir/'compile.bat',simdir/'elaborate.bat'):
        if not f.is_file() or f.stat().st_size < 100:
            raise ValueError('Missing or truncated compile source/script: '+str(f))
    sources,rtl_count=project_source_hashes(src_prj,repo,rtl_source='vivado',project=project)
    archived=out/'source_vlog.prj'
    shutil.copy2(src_prj,archived)
    snapshot=simdir/'xsim.dir'/SNAPSHOT
    elab_mode=None
    for stage in ('compile','elaborate'):
        before=snapshot_signature(snapshot) if stage=='elaborate' else None
        cmd=out/(stage+'_snapshot.cmd')
        native=out/(stage+'_snapshot.log')
        wrapper=out/(stage+'_wrapper.log')
        cmd.write_text(make_cmd_stage(simdir,root,stage,native),encoding='ascii')
        call_cmd(cmd,project.parent,wrapper,timeout,'XSim '+stage)
        content=native.read_text(errors='replace') if native.is_file() else ''
        if 'ERROR:' in content or 'FATAL' in content or not content.strip():
            raise ValueError('Missing or failed XSim '+stage+' log: '+str(native))
        if stage=='elaborate':
            elab_mode=classify_elaboration(content,snapshot,before)
    if any(not Path(p).is_file() or sha256(Path(p))!=h for p,h in sources.items()):
        raise ValueError('HDL sources changed during snapshot build')
    return {'simdir':simdir, 'vivado_root':root,
            'snapshot':snapshot, 'elaboration_mode':elab_mode,
            'source_hashes':sources, 'rtl_count':rtl_count,
            'compile_prj_sha256':sha256(archived),
            'compile_log_sha256':sha256(out/'compile_snapshot.log'),
            'elaborate_log_sha256':sha256(out/'elaborate_snapshot.log')}


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--project',type=Path,required=False)
    ap.add_argument('--vivado',default='vivado')
    ap.add_argument('--fileset',default='sim_1')
    ap.add_argument('--weights-dir',type=Path,help='Actual P0 ROM weights/bias: data/')
    ap.add_argument('--timing-audit',type=Path,
                    default=Path('fi/results/pilot400_profile_after_bitpair_01/timing_window_audit.csv'))
    ap.add_argument('--control10-manifest',type=Path,
                    default=Path('fi/results/pilot400_control10_vivado_02/control10_manifest.json'))
    ap.add_argument('--sample',action='append',dest='samples',default=[],
                    help='IMG0000..IMG0009; repeat to include multiple; default IMG0000')
    ap.add_argument('--target',action='append',dest='targets',default=[],
                    help='Restrict to a specific CANDIDATE; default all 14')
    ap.add_argument('--out-dir',type=Path,
                    default=Path('fi/results/pilot400_target_validation_v3_01'))
    ap.add_argument('--timeout-seconds',type=int,default=900)
    ap.add_argument('--execute',action='store_true')
    args=ap.parse_args()
    repo=args.repo.resolve()
    project=args.project.resolve() if args.project else None
    if args.timeout_seconds < 10:
        ap.error('--timeout-seconds must be >=10')
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',
                   repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(repo/'fi/scenarios/pilot400.csv',plan)
    targets=candidate_targets(plan,tuple(args.targets))
    samples=tuple(args.samples or ['IMG0000'])
    selected=select_scenarios(manifest,targets,samples)
    if args.execute and samples != ('IMG0000',):
        raise ValueError('V3a --execute is restricted to IMG0000: current authenticated '
                         'baseline timing profile only covers IMG0000. Profile other samples '
                         'separately before claiming temporal eligibility for them.')
    out=(args.out_dir if args.out_dir.is_absolute() else repo/args.out_dir).resolve()
    if out.exists():
        raise ValueError('Refusing to overwrite evidence directory: '+str(out))
    for sample_id in samples:
        for fname,(count,digits) in ASSET_SIZES.items():
            asset_lines(repo/'fi/pilot400/samples'/sample_id/fname,count,digits)
    frozen={name:sha256(repo/name) for name in (
        'fi/config/pilot400_targets.csv',
        'fi/config/fi_targets_candidates_v2.csv',
        'fi/scenarios/pilot400.csv',
        'fi/tb/tb_cnn_mnist_fi_pilot400.v')}
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/'selected_scenarios.csv',FIELDS,selected)
    meta={'schema':'PILOT400_TARGET_VALIDATION_V3A',
          'created_utc':datetime.now(timezone.utc).isoformat(),
          'status':'DRY_RUN_ONLY', 'source_authority':'vivado',
          'selected_count':len(selected), 'sample_ids':list(samples),
          'target_ids':[t['target_id'] for t in targets],
          'source_hashes':frozen,
          'selected_csv_sha256':sha256(out/'selected_scenarios.csv'),
          'records':[]}
    records=[]
    try:
        if args.execute:
            if project is None or not project.is_file() or not args.weights_dir:
                raise ValueError('--execute requires valid --project and --weights-dir')
            weights=(args.weights_dir if args.weights_dir.is_absolute()
                     else repo/args.weights_dir).resolve()
            audit=(args.timing_audit if args.timing_audit.is_absolute()
                   else repo/args.timing_audit).resolve()
            control=(args.control10_manifest if args.control10_manifest.is_absolute()
                     else repo/args.control10_manifest).resolve()
            # Both gates inspect authenticated logs + input SHA256, not just PASS strings.
            validate_control10_gate(repo,control,weights)
            validate_full_timing_gate(repo,audit,{p['target_id']:p for p in plan})
            meta['control10_manifest_sha256']=sha256(control)
            meta['timing_audit_sha256']=sha256(audit)
            meta['status']='SNAPSHOT_PREPARING'
            snap=build_verified_snapshot(repo,project,args.vivado,args.fileset,out,args.timeout_seconds)
            meta['status']='SNAPSHOT_BUILT'
            meta['snapshot_build']={k:v for k,v in snap.items()
                                    if k not in ('simdir','vivado_root','snapshot')}
            meta['snapshot_build']['snapshot']=str(snap['snapshot'])
            meta['project']=str(project)
            run_tcl=out/'direct_runall.tcl'
            run_tcl.write_text('run all\nquit\n',encoding='ascii')
            log_dir=out/'logs'
            log_dir.mkdir()
            for index,row in enumerate(selected,1):
                sid=row['scenario_id']
                sample_dir=repo/'fi/pilot400/samples'/row['sample_id']
                log=log_dir/(sid+'.log')
                console=out/(sid+'.console.log')
                cmd=out/(sid+'.cmd')
                cmd.write_text(make_cmd_run(snap['simdir'],snap['vivado_root'],
                                           row,sample_dir,run_tcl,log,console,
                                           profile_only=False),encoding='ascii')
                print(f'V3A_XSIM {index}/{len(selected)} {sid} {row["target_id"]} '
                      f'bit={row["bit_index"]} offset={row["injection_offset"]}',flush=True)
                call_cmd(cmd,snap['simdir'],out/(sid+'.wrapper.log'),args.timeout_seconds,'V3a '+sid)
                if not log.is_file() or 'Expected a switch' in console.read_text(errors='replace'):
                    raise ValueError('XSim produced no valid FI log for '+sid)
                entry=inspect_applied_and_result(log,row)
                records.append(entry)
                meta['records'].append({'scenario_id':sid,'log':str(log),
                                        'log_sha256':sha256(log)})
                if any(not Path(p).is_file() or sha256(Path(p))!=h
                       for p,h in snap['source_hashes'].items()):
                    raise ValueError('Vivado HDL changed after injection run: '+sid)
                print('V3A_SCENARIO_MECHANISM_PASS:',sid,entry['outcome'],flush=True)
            meta['status']='DIRECTED_MECHANISM_VALIDATED'
        summaries=summarize_targets(targets,records,len(samples))
        write_csv(out/'target_validation.csv',REPORT_FIELDS,records)
        write_csv(out/'target_summary.csv',TARGET_FIELDS,summaries)
        (out/'target_validation_report.md').write_text(
            evidence_md(targets,selected,summaries,args.execute,frozen),encoding='utf-8')
        print(('V3A_TARGET_MECHANISM_PASS' if args.execute else 'V3A_DRY_RUN_PASS')+
              f': {len(records)}/{len(selected)} XSim-validated FI scenarios; '
              'consumer value propagation NOT certified',flush=True)
    except Exception:
        meta['status']='INTERRUPTED_OR_INVALID'
        raise
    finally:
        (out/'validation_manifest.json').write_text(json.dumps(meta,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,OSError) as e:
        raise SystemExit('PILOT400_V3A_FAIL: '+str(e))

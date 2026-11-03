#!/usr/bin/env python3
"""Evidence-guarded refreeze of DAT_0010 to two bits at ONE valid handoff.

Preconditions: original bit0 XSim consumer-fix PASS; independent provisional
bit1 XSim probe PASS; schema-V2 profile confirms offset 1340, rejects 1341.

This is a deliberate sampling-policy change, not an automatic timing repair.
Does not change P0 RTL, historical deterministic20, or existing FI logs.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from pilot400_common import (PLAN_FIELDS, load_plan, expected_rows, verify_manifest,
                             read_csv, sha256, write_csv)
from profile_pilot400 import assess, parse_log
from parse_fi_v2 import parse_one
from probe_pilot400_dat0010_bit1 import (PROBE_ROW, validate_probe_result,
                                         CONSUMED)

SOURCE_FIELDS_V2 = PLAN_FIELDS[:3] + ('bit_index_b',) + PLAN_FIELDS[3:]


def verify_bit0_log(log: Path) -> None:
    record = parse_one(log)
    expected = {'scenario_id':'FI_P400_0381', 'sample_id':'IMG0000',
                'target_id':'DAT_0010', 'target_class':'DAT',
                'bit_index':'0', 'injection_offset':'1340',
                'injection_ok':'1', 'protocol_ok':'1',
                'logits_mismatch':'1', 'outcome':'WRONG_LOGITS_SAME_LABEL',
                'first_bad_stage':'FC'}
    for key, value in expected.items():
        if record.get(key) != value:
            raise ValueError(f'Bit0 XSim record mismatch: {key}={record.get(key)}')
    text = log.read_text(encoding='utf-8', errors='replace')
    observed = CONSUMED.findall(text)
    if len(observed) != 1 or int(observed[0][0]) != int(record['actual_cycle']) or \
       int(observed[0][1],16) != (int(observed[0][2],16) ^ 1) or \
       int(observed[0][3]) < 1:
        raise ValueError('Bit0 log lacks value-aware ArgMax consumption of XOR(bit0)')


def verify_bit1_provenance(file: Path, repo: Path) -> dict:
    if not file.is_file():
        raise ValueError('Missing independent BIT1_XSIM_VERIFIED probe evidence')
    data=json.loads(file.read_text(encoding='utf-8'))
    if data.get('status') != 'BIT1_XSIM_VERIFIED' or data.get('probe') != PROBE_ROW:
        raise ValueError('Bit1 XSim probe evidence status or tuple invalid')
    required={'tb_sha256':repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v',
              'source_plan_sha256':repo/'fi/config/pilot400_targets.csv',
              'scenario_manifest_sha256':repo/'fi/scenarios/pilot400.csv'}
    for key,path in required.items():
        if data.get(key)!=sha256(path):
            raise ValueError('Bit1 probe provenance drift: '+key)
    log=Path(data.get('log',''))
    if not log.is_file() or data.get('log_sha256')!=sha256(log):
        raise ValueError('Bit1 probe log changed or absent')
    record=parse_one(log)
    consumer=validate_probe_result(record,log.read_text(encoding='utf-8',errors='replace'))
    if consumer!=data.get('consumer') or record['outcome']!=data.get('outcome'):
        raise ValueError('Bit1 probe manifest differs from verified XSim log')
    return data


def validate_handoff(profile_log: Path, p: dict[str,str]) -> int:
    events=parse_log(profile_log)  # schema-V2 XSim control-only, exactly one PASS
    dat=[e for e in events if e[1]=='DAT_0010']
    if any(e[0]=='CONSUME' and e[3]==1341 for e in dat):
        raise ValueError('Unexpected consume at offset1341: the experiment contract changed')
    expected=assess([p], events)[0]
    if expected['status_a']!='DAT_HANDOFF_AT_EDGE' or expected['status_b']!='FAIL_DAT_HANDOFF':
        raise ValueError('Source timing does not match expected single handoff at 1340')
    if len([e for e in dat if e[0]=='CONSUME' and e[3]==1340])!=1:
        raise ValueError('Expected exactly one DAT_0010 consume at offset1340')
    return len(events)


def proposed_rows(repo:Path) -> tuple[list[dict[str,str]],list[dict[str,str]]]:
    plans=load_plan(repo/'fi/config/pilot400_targets.csv',
                    repo/'fi/config/fi_targets_candidates_v2.csv')
    target=next(p for p in plans if p['target_id']=='DAT_0010')
    if (target['bit_index'], target['bit_index_b'], target['offset_a'], target['offset_b']) != \
       ('0','0','1340','1341'):
        raise ValueError('Expected original DAT_0010 bit0@1340 and bit0@1341 plan')
    revised=[dict(p) for p in plans]
    row=next(p for p in revised if p['target_id']=='DAT_0010')
    row['bit_index_b']='1'
    row['offset_b']='1340'
    row['selection_rationale']=(
        'FC output logits; A=bit0@1340 B=bit1@1340 at one verified '
        'FC-to-ArgMax consumer edge (nonuniform bit/time-pair design)')
    return revised, expected_rows(revised)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--profile-log',type=Path,
                    default=Path('fi/results/pilot400_profile_v2_01/profile.log'))
    ap.add_argument('--bit0-log',type=Path,
                    default=Path('fi/results/pilot400_dat0010_consumer_fix_01/repeat_01/logs/FI_P400_0381.log'))
    ap.add_argument('--bit1-evidence',type=Path,
                    default=Path('fi/results/pilot400_dat0010_bit1_probe_01/probe_evidence.json'))
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--reason',default='')
    args=ap.parse_args()
    repo=args.repo.resolve()
    locate=lambda p: p.resolve() if p.is_absolute() else (repo/p).resolve()
    source=repo/'fi/config/pilot400_targets.csv'
    manifest=repo/'fi/scenarios/pilot400.csv'
    generator=repo/'fi/scripts/generate_pilot400.py'
    if args.apply and len(args.reason.strip()) < 15:
        raise ValueError('--apply requires a meaningful 15+ character reason')
    current=load_plan(source,repo/'fi/config/fi_targets_candidates_v2.csv')
    verify_manifest(manifest,current)
    revised, new_manifest=proposed_rows(repo)
    orig=read_csv(source,PLAN_FIELDS)
    if any('bit_index_b' in r for r in orig):
        raise ValueError('Source already has bit_index_b; manual review required')
    count=validate_handoff(locate(args.profile_log),next(p for p in current if p['target_id']=='DAT_0010'))
    verify_bit0_log(locate(args.bit0_log))
    probe=verify_bit1_provenance(locate(args.bit1_evidence),repo)
    previous_rows=verify_manifest(manifest,current)
    changed=[(old,new) for old,new in zip(previous_rows,new_manifest) if old!=new]
    if len(changed)!=10 or any(a['target_id']!='DAT_0010' or \
        a['sample_id']!=b['sample_id'] or a['bit_index']!='0' or \
        a['injection_offset']!='1341' or b['bit_index']!='1' or \
        b['injection_offset']!='1340' for a,b in changed):
        raise ValueError('Unexpected manifest difference outside 10 DAT_0010 B scenarios')
    print('DAT0010_REFREEZE_PREFLIGHT_PASS: profile events=',count,
          'bit0/bit1 independent consumer proofs verified; exactly 10 B tuples change')
    print('  Before: bit0@1340 / bit0@1341 (second offset has no consumer)')
    print('  After : bit0@1340 / bit1@1340 (one consumer edge, two different state bits)')
    if not args.apply:
        print('DAT0010_REFREEZE_DRY_RUN: no source/manifest files changed')
        return
    old_plan_bytes=source.read_bytes()
    old_manifest_bytes=manifest.read_bytes()
    plan_hash,manifest_hash=sha256(source),sha256(manifest)
    history=repo/'fi/config/history'
    history.mkdir(parents=True,exist_ok=True)
    source_backup=history/f'pilot400_targets_{plan_hash[:16]}.csv'
    manifest_backup=repo/'fi/scenarios/history'/f'pilot400_{manifest_hash[:16]}.csv'
    manifest_backup.parent.mkdir(parents=True,exist_ok=True)
    if source_backup.is_file() and sha256(source_backup)!=plan_hash:
        raise ValueError('Existing source backup checksum conflict')
    if manifest_backup.is_file() and sha256(manifest_backup)!=manifest_hash:
        raise ValueError('Existing manifest backup checksum conflict')
    if not source_backup.is_file():
        shutil.copy2(source,source_backup)
    if not manifest_backup.is_file():
        shutil.copy2(manifest,manifest_backup)
    # Retain original row order; keep historical IDs 0381..0400 in the same slots.
    replacements={r['target_id']:r for r in revised}
    updated=[]
    for old in orig:
        row=replacements[old['target_id']]
        updated.append({k:row[k] for k in SOURCE_FIELDS_V2})
    try:
        temp=source.with_suffix('.csv.pending')
        write_csv(temp,SOURCE_FIELDS_V2,updated)
        os.replace(temp,source)
        subprocess.run([sys.executable,str(generator),'--refreeze','--reason',args.reason.strip()],
                       cwd=repo,check=True)
        subprocess.run([sys.executable,str(generator),'--verify'],cwd=repo,check=True)
        subprocess.run([sys.executable,str(repo/'fi/scripts/preflight_pilot400.py'),
                        '--require-assets'],cwd=repo,check=True)
        subprocess.run([sys.executable,'-m','unittest','discover','-s','fi/scripts',
                        '-p','test_pilot400.py','-q'],cwd=repo,check=True)
        if verify_manifest(manifest,load_plan(source,
           repo/'fi/config/fi_targets_candidates_v2.csv'))!=new_manifest:
            raise ValueError('Post-refreeze 400-row manifest differs from prevalidated proposal')
    except Exception:
        # Roll back both active files; historical evidence archives remain immutable.
        source.write_bytes(old_plan_bytes)
        manifest.write_bytes(old_manifest_bytes)
        raise
    record={
        'utc':datetime.now(timezone.utc).isoformat(),
        'reason':args.reason.strip(),
        'policy':'DAT_0010_ONLY_TWO_DISTINCT_BITS_ONE_HANDOFF; all other targets use two offsets',
        'profile_sha256':sha256(locate(args.profile_log)),
        'bit0_log_sha256':sha256(locate(args.bit0_log)),
        'bit1_probe_evidence_sha256':sha256(locate(args.bit1_evidence)),
        'bit1_probe_log_sha256':probe['log_sha256'],
        'source_before_sha256':plan_hash, 'source_after_sha256':sha256(source),
        'manifest_before_sha256':manifest_hash, 'manifest_after_sha256':sha256(manifest),
        'source_backup':str(source_backup.relative_to(repo)),
        'manifest_backup':str(manifest_backup.relative_to(repo)),
        'scenario_ids_changed':[r['scenario_id'] for _,r in changed],
        'source_status':'PASS_ONE_WINDOW_UNCHANGED',
        'claim':'BIT1_XSIM_PROBE_PASS; NOT_400_XSIM_PASS; 14_OTHER_TARGETS_CANDIDATE',
    }
    history_log=history/'pilot400_dat0010_bit_pair_refreeze.jsonl'
    with history_log.open('a',encoding='utf-8') as f:
        f.write(json.dumps(record,sort_keys=True)+'\n')
    print('DAT0010_REFREEZE_PASS: 400 rows, 10 B rows changed with archived SHA256 history')
    print('  Old results remain HISTORICAL and cannot certify the NEW bit1 rows')
    print('  Rerun schema-V2 fault-free profile and control10 after manifest change')

if __name__=='__main__':
    try:
        main()
    except (ValueError,KeyError,OSError,subprocess.CalledProcessError) as exc:
        raise SystemExit('DAT0010_REFREEZE_FAIL: '+str(exc))

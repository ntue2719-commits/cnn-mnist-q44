#!/usr/bin/env python3
"""Independent XSim qualification of DAT_0010 bit1 at the known ArgMax handoff.

IMPORTANT: this is a PROVISIONAL probe, NOT a row in frozen pilot400.csv.
It does not mutate the plan, 400-row manifest, or synthesizable RTL.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from pilot400_common import FIELDS, load_plan, sha256, verify_manifest, write_csv
from parse_fi_v2 import parse_one
from run_pilot400 import make_tcl, run_one

PROBE_ID = 'FI_P400_DAT0010_B1_PROBE'
PROBE_ROW = dict(scenario_id=PROBE_ID, sample_id='IMG0000',
                 target_id='DAT_0010', target_class='DAT',
                 bit_index='1', injection_offset='1340')
CONSUMED = re.compile(r'^FI_DAT_ARGMAX_CONSUME cycle=(\d+) '
                      r'logit0=([0-9a-fA-F]{8}) golden0=([0-9a-fA-F]{8}) '
                      r'mismatched_classes=(\d+)$', re.M)


def validate_probe_result(result: dict[str, str], log_text: str) -> dict[str, str]:
    for key, expected in PROBE_ROW.items():
        if result.get(key) != expected:
            raise ValueError(f'Bit1 probe mismatch {key}: {result.get(key)} != {expected}')
    if result.get('injection_ok') != '1' or result.get('fault_events') != '1':
        raise ValueError('Probe does not contain exactly one verified bit flip')
    if result.get('requested_cycle') != result.get('actual_cycle'):
        raise ValueError('Probe executed at wrong cycle')
    if result.get('outcome') != 'WRONG_LOGITS_SAME_LABEL' or \
       result.get('first_bad_stage') != 'FC' or \
       result.get('protocol_ok') != '1' or \
       int(result.get('logits_mismatch', '0')) < 1:
        raise ValueError('Bit1 probe did not observe changed FC logits at ArgMax')
    observed = CONSUMED.findall(log_text)
    if len(observed) != 1:
        raise ValueError('Expected precisely one value-aware ArgMax consumer marker')
    cycle, got, golden, mismatches = observed[0]
    if int(cycle) != int(result['actual_cycle']) or \
       int(got, 16) != (int(golden, 16) ^ 2) or \
       int(mismatches) < 1:
        raise ValueError('ArgMax logit0 does not contain the expected XOR(bit1) value')
    return dict(argmax_consume_cycle=cycle, argmax_logit0=got,
                golden_logit0=golden, mismatched_classes=mismatches)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--project', type=Path, required=True)
    ap.add_argument('--vivado', default='vivado')
    ap.add_argument('--fileset', default='sim_1')
    ap.add_argument('--timeout-seconds', type=int, default=900)
    ap.add_argument('--out-dir', type=Path,
                    default=Path('fi/results/pilot400_dat0010_bit1_probe_01'))
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    repo = args.repo.resolve()
    project = args.project.resolve()
    plan_file = repo/'fi/config/pilot400_targets.csv'
    manifest = repo/'fi/scenarios/pilot400.csv'
    tb = repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v'
    plans = load_plan(plan_file, repo/'fi/config/fi_targets_candidates_v2.csv')
    verify_manifest(manifest, plans)
    target = next(p for p in plans if p['target_id'] == 'DAT_0010')
    if (target['bit_index'], target['offset_a'], target['offset_b']) != ('0','1340','1341'):
        raise ValueError('Refusing probe on an unexpectedly changed DAT_0010 source plan')
    if 'FI_DAT_ARGMAX_CONSUME' not in tb.read_text(encoding='utf-8'):
        raise ValueError('DAT ArgMax PRE-NBA consumer fix must be installed before probing')
    out = (args.out_dir if args.out_dir.is_absolute() else repo/args.out_dir).resolve()
    if out.exists():
        raise ValueError('Probe output already exists; choose a fresh --out-dir: '+str(out))
    out.mkdir(parents=True, exist_ok=False)
    log = out / f'{PROBE_ID}.log'
    done = out / f'{PROBE_ID}.done'
    tcl = out / f'{PROBE_ID}.tcl'
    write_csv(out/'probe_only_not_in_pilot400.csv', FIELDS, [PROBE_ROW])
    tcl.write_text(make_tcl(project, PROBE_ROW, done, args.fileset,
                            repo/'fi/pilot400/samples/IMG0000'), encoding='utf-8')
    if not args.execute:
        print('PILOT400_DAT0010_BIT1_DRY_RUN: Tcl prepared; no XSim; no refreeze')
        return
    if not project.is_file():
        raise ValueError('Vivado Simulation.xpr missing: '+str(project))
    try:
        run_one(args.vivado, project, tcl, done, log, args.timeout_seconds)
        result = parse_one(log)
        consume = validate_probe_result(result, log.read_text(encoding='utf-8', errors='replace'))
        record = {'status':'BIT1_XSIM_VERIFIED',
                  'utc':datetime.now(timezone.utc).isoformat(),
                  'probe':dict(PROBE_ROW), 'outcome':result['outcome'],
                  'consumer':consume, 'log':str(log), 'log_sha256':sha256(log),
                  'tb_sha256':sha256(tb),
                  'source_plan_sha256':sha256(plan_file),
                  'scenario_manifest_sha256':sha256(manifest),
                  'project':str(project), 'tcl_sha256':sha256(tcl)}
        (out/'probe_evidence.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        print('PILOT400_DAT0010_BIT1_XSIM_PASS: bit1 XOR consumed by ArgMax at offset1340')
        print('  Log:',log)
        print('  Evidence:',out/'probe_evidence.json')
    except Exception:
        (out/'probe_failed.txt').write_text('NOT_VERIFIED; review XSim log and Tcl before retrying\n',encoding='utf-8')
        raise

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit('PILOT400_DAT0010_BIT1_PROBE_FAIL: '+str(exc))

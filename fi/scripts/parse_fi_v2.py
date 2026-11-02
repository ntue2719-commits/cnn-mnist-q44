#!/usr/bin/env python3
"""Strict parser / integrity gate for FI_RESULT_V2 in per-scenario XSim logs.

Example:
  python parse_fi_v2.py --logs-dir fi/results/raw/logs \
    --scenarios fi/scenarios/pilot_scenarios.csv \
    --out fi/results/parsed/results_v2.csv

Requires one FI_RESULT_V2 record and a PASS marker in each simulator log.
Simulator log success MUST NOT be mistaken for a valid injection.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

FIELDS = [
    'scenario_id', 'sample_id', 'target_id', 'target_class', 'bit_index',
    'injection_offset', 'requested_cycle', 'actual_cycle',
    'fault_events', 'injection_ok', 'completion_cycle', 'first_done_pixels',
    'pixels_driven', 'pixels_accepted', 'pool1_words', 'pool2_words',
    'fc_valid_count', 'argmax_count', 'done_count', 'protocol_ok',
    'pool1_mismatch', 'pool2_mismatch', 'logits_mismatch',
    'prediction', 'first_bad_stage', 'outcome'
]
NUMERIC = set(FIELDS) - {
    'scenario_id', 'sample_id', 'target_id', 'target_class',
    'first_bad_stage', 'outcome'
}
OUTCOMES = {
    'NONCOMPLETION', 'PROTOCOL_VIOLATION', 'WRONG_LABEL',
    'WRONG_LOGITS_SAME_LABEL', 'FINAL_OUTPUT_MATCH'
}


def parse_one(log_path: Path) -> dict[str, str]:
    content = log_path.read_text(encoding='utf-8', errors='replace')
    lines = [line.strip() for line in content.splitlines()
             if line.strip().startswith('FI_RESULT_V2,')]
    if len(lines) != 1:
        raise ValueError(f'{log_path}: expected exactly one FI_RESULT_V2; got {len(lines)}')
    if 'CNN_PEP FI CAMPAIGN V2: PASS' not in content:
        raise ValueError(f'{log_path}: missing campaign PASS marker')
    rec = next(csv.reader([lines[0]]))
    if rec[0] != 'FI_RESULT_V2' or len(rec) != len(FIELDS) + 1:
        raise ValueError(f'{log_path}: malformed record, {len(rec)} columns, expected {len(FIELDS)+1}')
    data = dict(zip(FIELDS, rec[1:]))
    for key in NUMERIC:
        try:
            int(data[key])
        except ValueError as exc:
            raise ValueError(f'{log_path}: field {key} must be integer') from exc
    for key in ('injection_ok', 'protocol_ok'):
        if int(data[key]) not in (0, 1):
            raise ValueError(f'{log_path}: {key} not boolean')
    if data['outcome'] not in OUTCOMES:
        raise ValueError(f'{log_path}: invalid outcome: {data["outcome"]}')
    if int(data['fault_events']) != 1 or int(data['injection_ok']) != 1:
        raise ValueError(f'{log_path}: injection is not single and verified')
    if data['actual_cycle'] != data['requested_cycle']:
        raise ValueError(f'{log_path}: injection cycle mismatch')
    if int(data['pixels_driven']) != 784:
        raise ValueError(f'{log_path}: pixel stimulus incomplete')
    if int(data['done_count']) == 0:
        if data['outcome'] != 'NONCOMPLETION':
            raise ValueError(f'{log_path}: missing done but outcome not NONCOMPLETION')
    elif int(data['protocol_ok']) == 0:
        if data['outcome'] != 'PROTOCOL_VIOLATION':
            raise ValueError(f'{log_path}: protocol invalid but outcome not PROTOCOL_VIOLATION')
    elif data['outcome'] in {'PROTOCOL_VIOLATION', 'NONCOMPLETION'}:
        raise ValueError(f'{log_path}: protocol/completion fields contradict outcome')
    if data['outcome'] == 'FINAL_OUTPUT_MATCH' and int(data['logits_mismatch']) != 0:
        raise ValueError(f'{log_path}: final-output-match but logits mismatch')
    if data['outcome'] == 'WRONG_LOGITS_SAME_LABEL' and int(data['logits_mismatch']) == 0:
        raise ValueError(f'{log_path}: wrong-logits outcome without logits mismatch')
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--logs-dir', required=True, type=Path)
    ap.add_argument('--scenarios', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()

    with args.scenarios.open(newline='', encoding='utf-8') as f:
        scenarios = {r['scenario_id']: r for r in csv.DictReader(f)
                     if (r.get('scenario_id') or '').strip()}
    if not scenarios:
        raise ValueError('empty scenarios file')

    records = {}
    for logfile in sorted(args.logs_dir.glob('*.log')):
        if logfile.stem not in scenarios:
            continue  # allow unrelated logs in the same folder
        data = parse_one(logfile)
        sid = data['scenario_id']
        if sid not in scenarios:
            raise ValueError(f'{logfile}: unexpected scenario ID {sid}')
        if sid != logfile.stem or sid in records:
            raise ValueError(f'{logfile}: scenario ID/filename mismatch or duplicate')
        scenario = scenarios[sid]
        for expected_key, result_key in (
            ('scenario_id', 'scenario_id'),
            ('sample_id', 'sample_id'),
            ('target_id', 'target_id'),
            ('target_class', 'target_class'),
            ('bit_index', 'bit_index'),
            ('injection_offset', 'injection_offset'),
        ):
            if expected_key not in scenario:
                raise ValueError(f'scenarios CSV missing {expected_key}')
            if str(scenario[expected_key]).strip() != data[result_key]:
                raise ValueError(f'{logfile}: scenario field mismatch {expected_key}: '
                                 f'{scenario[expected_key]!r} != {data[result_key]!r}')
        records[sid] = data

    missing = sorted(set(scenarios) - set(records))
    if missing:
        raise ValueError(f'missing scenario logs/results: {missing[:12]} ({len(missing)} total)')

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for sid in scenarios:  # preserve scenario order
            writer.writerow(records[sid])
    print(f'Validated {len(records)} verified FI scenarios -> {args.out}')


if __name__ == '__main__':
    main()

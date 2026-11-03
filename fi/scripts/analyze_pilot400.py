#!/usr/bin/env python3
"""Postprocess validated FI_RESULT_V2 CSV using paired observation levels.

No fabricated input-consumption data: only PASS assertions in actual logs count.
An absent monitor is UNKNOWN, not evidence of non-consumption.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import csv
from pathlib import Path
from pilot400_common import FIELDS,read_csv,write_csv

OBS_FIELDS=tuple(FIELDS)+('outcome','level0_prediction_changed','level1_output_or_protocol',
                        'level2_checkpoint_numeric_diff','arithmetic_input_accepted',
                        'level2_additional_over_level1','first_bad_stage',
                        'pool1_mismatch','pool2_mismatch','logits_mismatch')


def interpret(row:dict[str,str],log_path:Path|None=None)->dict[str,str]:
    outcome=row['outcome']
    if outcome not in {'NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL',
                       'WRONG_LOGITS_SAME_LABEL','FINAL_OUTPUT_MATCH'}:
        raise ValueError('Unrecognized outcome: '+outcome)
    good_protocol=(int(row['protocol_ok'])==1 and int(row['done_count'])>0)
    # A missing or protocol-invalid prediction is NOT a correct prediction.
    label_changed=('1' if outcome=='WRONG_LABEL' else '0') if good_protocol else 'NA'
    stage_numeric=int(row['pool1_mismatch'])>0 or int(row['pool2_mismatch'])>0
    logits_diff=int(row['logits_mismatch'])>0
    level1 = logits_diff or outcome in {'WRONG_LABEL','PROTOCOL_VIOLATION','NONCOMPLETION'}
    timeline_expected=row['target_id'] in {'MEM_0002','BUF_0031'} and int(row['bit_index'])==0
    monitor='NOT_INSTRUMENTED'
    if log_path is not None:
        if not log_path.is_file():
            raise ValueError('Missing raw FI log: '+str(log_path))
        log=log_path.read_text(encoding='utf-8',errors='replace')
        mem=log.count('FI_TIMELINE_MEM_PASS ')
        buf=log.count('FI_TIMELINE_BUF_PASS ')
        if mem+buf>1:
            raise ValueError('Multiple timeline PASS markers in '+str(log_path))
        if timeline_expected:
            marker='FI_TIMELINE_MEM_PASS ' if row['target_id']=='MEM_0002' else 'FI_TIMELINE_BUF_PASS '
            if log.count(marker)!=1 or 'FI_TIMELINE_FAIL' in log:
                raise ValueError('Expected target-specific timeline proof missing/failed: '+str(log_path))
            monitor='VERIFIED_INPUT_ACCEPTED'
        elif mem+buf:
            raise ValueError('Unexpected target-specific timeline marker in '+str(log_path))
    elif timeline_expected:
        monitor='UNKNOWN_NO_RAW_LOG'
    # Stage checkpoint mismatch is available for all scenarios from FI V2.
    level2_only = (stage_numeric or monitor=='VERIFIED_INPUT_ACCEPTED') and not level1
    return {**{key:row[key] for key in FIELDS},
            'outcome':outcome,'level0_prediction_changed':label_changed,
            'level1_output_or_protocol':str(int(level1)),
            'level2_checkpoint_numeric_diff':str(int(stage_numeric)),
            'arithmetic_input_accepted':monitor,
            'level2_additional_over_level1':str(int(level2_only)),
            'first_bad_stage':row['first_bad_stage'],
            'pool1_mismatch':row['pool1_mismatch'],
            'pool2_mismatch':row['pool2_mismatch'],
            'logits_mismatch':row['logits_mismatch']}


def run_analysis(rows:list[dict[str,str]],manifest:list[dict[str,str]],
                 logs:Path|None=None)->list[dict[str,str]]:
    planned={r['scenario_id']:r for r in manifest}
    if len(planned)!=len(manifest):
        raise ValueError('Duplicate scenario IDs in manifest')
    actual={r['scenario_id']:r for r in rows}
    if len(actual)!=len(rows) or set(actual)!=set(planned):
        raise ValueError('Results do not match selected scenario set (missing/duplicate/extra IDs)')
    observations=[]
    for sid in planned:
        r=actual[sid];plan=planned[sid]
        for field in FIELDS[1:]:
            if str(r[field])!=str(plan[field]):
                raise ValueError(f'{sid}: manifest/result mismatch in {field}')
        observations.append(interpret(r,logs/f'{sid}.log' if logs else None))
    return observations


def write_summary(path:Path,observations:list[dict[str,str]],planned_count:int=400):
    counts=Counter(r['outcome'] for r in observations)
    klass=defaultdict(Counter)
    for r in observations:
        klass[r['target_class']][r['outcome']]+=1
    metrics={
        'Prediction changed (only valid completed inference)':sum(r['level0_prediction_changed']=='1' for r in observations),
        'Prediction not comparable (protocol/noncompletion)':sum(r['level0_prediction_changed']=='NA' for r in observations),
        'Output/protocol-level effect':sum(r['level1_output_or_protocol']=='1' for r in observations),
        'Pool1 or Pool2 checkpoint difference':sum(r['level2_checkpoint_numeric_diff']=='1' for r in observations),
        'Arithmetic-input acceptance PROVED by dedicated monitor':sum(r['arithmetic_input_accepted']=='VERIFIED_INPUT_ACCEPTED' for r in observations),
        'Arithmetic-input acceptance not instrumented':sum(r['arithmetic_input_accepted']=='NOT_INSTRUMENTED' for r in observations),
        'Stage-only additional over output/protocol':sum(r['level2_additional_over_level1']=='1' for r in observations),
    }
    text=['# Pilot 400: Paired RQ-A/RQ-B analysis','',
          f'**Validated scenarios in this result set:** {len(observations)} / planned {planned_count}.',
          '', '> Only scenario configurations with actual parser-validated FI_RESULT_V2 records appear here.',
          '> Counts describe the selected directed sample, not population vulnerability or detector coverage.',
          '', '## RQ-A: outcomes by class','',
          '| Class | n | NONCOMPLETION | PROTOCOL_VIOLATION | WRONG_LABEL | WRONG_LOGITS_SAME_LABEL | FINAL_OUTPUT_MATCH |',
          '|---|---:|---:|---:|---:|---:|---:|']
    for cls in ('CTL','BUF','MEM','DAT'):
        c=klass[cls];n=sum(c.values())
        text.append('| '+cls+' | '+str(n)+' | '+' | '.join(str(c[k]) for k in ('NONCOMPLETION','PROTOCOL_VIOLATION','WRONG_LABEL','WRONG_LOGITS_SAME_LABEL','FINAL_OUTPUT_MATCH'))+' |')
    text.extend(['','## RQ-B: paired observability','',
                 '| Metric | Count |','|---|---:|'])
    text.extend(f'| {k} | {v} |' for k,v in metrics.items())
    text.extend(['','**Interpretation safeguards:**',
                 '- Prediction-only is `NA` when no protocol-valid inference completed; do not count as prediction correct.',
                 '- Stage-level numerical differences here mean Pool1 or Pool2 checkpoint differences only; raw Conv1/Conv2 accumulator differences are NOT measured by this TB.',
                 '- `arithmetic_input_accepted=VERIFIED_INPUT_ACCEPTED` requires the exact MEM_0002 / BUF_0031 target/bit and a dedicated timeline PASS in its raw XSim log.',
                 '- `NOT_INSTRUMENTED` or `UNKNOWN_NO_RAW_LOG` must not be treated as no propagation or no consumption.',
                 '- The balanced directed design (5 selected sites per class; 10 images; 2 offsets) does NOT represent a random sample of all FFs.',
                 '- Multiple scenarios on the same site and image are correlated; no ordinary binomial confidence interval without an appropriate sampling/cluster model.',
                 '- A PASS of the simulation means injection/log validation passed; it does NOT imply hardware robustness.',
                 ''])
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(text),encoding='utf-8')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--results',type=Path,required=True,help='Strict parse_fi_v2.py output CSV')
    ap.add_argument('--manifest',type=Path,required=True,help='Selected manifest CSV used for these logs')
    ap.add_argument('--logs-dir',type=Path,help='Raw logs needed to verify MEM_0002/BUF_0031 consumption')
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--require-full-400',action='store_true')
    args=ap.parse_args()
    rows=read_csv(args.results,tuple(FIELDS)+('outcome','protocol_ok','done_count',
                'pool1_mismatch','pool2_mismatch','logits_mismatch','first_bad_stage'))
    manifest=read_csv(args.manifest,FIELDS)
    if args.require_full_400 and (len(rows)!=400 or len(manifest)!=400):
        raise ValueError('Full-400 report refused: fewer than 400 validated result rows')
    obs=run_analysis(rows,manifest,args.logs_dir)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    write_csv(args.output_dir/'pilot400_observability.csv',OBS_FIELDS,obs)
    write_summary(args.output_dir/'pilot400_RQ_AB_summary.md',obs)
    print('PILOT400_ANALYSIS_PASS: ',len(obs),' XSim-validated records; output=',args.output_dir)

if __name__=='__main__':
    try:main()
    except ValueError as e:raise SystemExit('PILOT400_ANALYSIS_FAIL: '+str(e))

#!/usr/bin/env python3
"""Create/verify the frozen balanced 400-row pilot design. No XSim execution."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import shutil
from pilot400_common import sha256
from pathlib import Path
from pilot400_common import SAMPLES, FIELDS, expected_rows, load_plan, verify_manifest, write_csv, read_csv


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--verify',action='store_true',help='Fail if manifest differs; do not write')
    ap.add_argument('--refreeze',action='store_true',help='Explicitly replace manifest with archived history')
    ap.add_argument('--reason',default='',help='Mandatory explanation when --refreeze is used')
    args=ap.parse_args()
    if args.verify and args.refreeze:
        raise ValueError('--verify and --refreeze are mutually exclusive')
    if args.refreeze and not args.reason.strip():
        raise ValueError('--refreeze requires --reason explaining why offsets changed')
    repo=args.repo.resolve()
    plans=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    out=repo/'fi/scenarios/pilot400.csv'
    if args.verify:
        rows=verify_manifest(out, plans)
    else:
        rows=expected_rows(plans)
        if out.exists():
            if args.refreeze:
                old_sha=sha256(out)
                history=out.parent/'history'
                history.mkdir(parents=True,exist_ok=True)
                archive=history/f'pilot400_{old_sha[:16]}.csv'
                if not archive.is_file():
                    shutil.copy2(out,archive)
                write_csv(out,FIELDS,rows)
                record={'utc':datetime.now(timezone.utc).isoformat(),
                        'reason':args.reason.strip(),'previous_sha256':old_sha,
                        'current_sha256':sha256(out),'archive':str(archive.relative_to(repo))}
                with (history/'pilot400_refreeze.jsonl').open('a',encoding='utf-8') as log:
                    log.write(json.dumps(record)+'\n')
            else:
                # An existing manifest must match the plan byte for byte.
                verify_manifest(out, plans)
        else:
            write_csv(out, FIELDS, rows)
    by_class = {cls: sum(r['target_class']==cls for r in rows) for cls in ('CTL','BUF','MEM','DAT')}
    print('PILOT400_MANIFEST_PASS: 400 unique scenarios, 20 targets, 10 samples, 2 offsets; per-class=',by_class)
    print('  400 plan rows are NOT 400 XSim PASS executions.')
    for sid,idx in SAMPLES:
        print(f'  {sid}: MNIST test-set index {idx}')

if __name__=='__main__':
    try: main()
    except (ValueError,AssertionError) as e:
        raise SystemExit('PILOT400_MANIFEST_FAIL: '+str(e))

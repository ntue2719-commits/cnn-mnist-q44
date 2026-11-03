#!/usr/bin/env python3
"""Read-only gates for planned Pilot400 and supplied sample golden bundles."""
from __future__ import annotations
import argparse
from pathlib import Path
from pilot400_common import load_plan,verify_manifest,validate_assets,sha256,asset_lines


def preflight(repo:Path,require_assets:bool=False,verify_seed:bool=True)->list[dict[str,str]]:
    plan=load_plan(repo/'fi/config/pilot400_targets.csv',repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest=repo/'fi/scenarios/pilot400.csv'
    rows=verify_manifest(manifest,plan)
    count,missing=validate_assets(repo/'fi/pilot400/samples',require_all=require_assets)
    if verify_seed:
        # IMG0000 was copied from the user's previously supplied P0 checkpoints.
        # The frozen values are not arbitrary; existing P0 data should match.
        baseline=repo/'data'
        if baseline.is_dir() and all((baseline/x).is_file() for x in ('input.hex','golden_pool1.hex','golden_pool2.hex','golden.hex','prediction.hex')):
            for name in ('input.hex','golden_pool1.hex','golden_pool2.hex','golden.hex','prediction.hex'):
                a=asset_lines(baseline/name, *{'input.hex':(784,2),'golden_pool1.hex':(676,2),
                    'golden_pool2.hex':(200,2),'golden.hex':(10,8),'prediction.hex':(1,2)}[name])
                b=asset_lines(repo/'fi/pilot400/samples/IMG0000'/name,len(a),2 if name not in ('golden.hex',) else 8)
                if a!=b:
                    raise ValueError('Frozen IMG0000 does not match repo/data/'+name)
    print(f'PILOT400_PREFLIGHT_PASS: manifest=400, targets=20, supplied_samples={count}/10')
    if missing:
        print('  BLOCKED_FOR_FULL_EXECUTION: missing real MNIST/golden bundles for '+', '.join(missing))
    candidates=sum(p['evidence_status'] == 'CANDIDATE' for p in plan)
    print(f'  WARNING: {candidates}/20 selected targets have only catalog-level evidence; runtime timing unverified')
    print('  PREPARED != executed; XSim is required for FI validation.')
    return rows


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--require-assets',action='store_true')
    args=ap.parse_args()
    preflight(args.repo.resolve(),args.require_assets)

if __name__=='__main__':
    try: main()
    except ValueError as exc: raise SystemExit('PILOT400_PREFLIGHT_FAIL: '+str(exc))

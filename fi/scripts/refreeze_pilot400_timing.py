#!/usr/bin/env python3
"""Evidence-guarded Pilot400 timing candidate update (no P0 RTL changes).

USAGE:
  python pilot400_refreeze_timing_candidates.py
  python pilot400_refreeze_timing_candidates.py --apply --reason "XSim IMG0000 write/read profiling"

Only changes offset_a/offset_b for BUF_0007, MEM_0004, MEM_0005.
Does NOT promote CANDIDATE evidence to a verified state.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

CHANGES = {
    'BUF_0007': {'old': ('750', '755'), 'new': ('791', '792'),
                 'write': 790, 'read': None, 'upper_by_mem2_write': 877},
    'MEM_0004': {'old': ('1275', '1280'), 'new': ('1292', '1295'),
                 'write': 1288, 'read': 1314},
    'MEM_0005': {'old': ('1290', '1291'), 'new': ('1304', '1307'),
                 'write': 1300, 'read': 1326},
}
EVENT_RE = re.compile(r'^FI_PROFILE_EVENT,(WRITE|READ|FC_VALID),([A-Z]{3}_[0-9]{4}),(\d+),(\d+)$')
FIELDS = ('target_id', 'target_class', 'bit_index', 'offset_a', 'offset_b',
          'evidence_status', 'selection_rationale')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(args: list[str], repo: Path) -> None:
    print('RUN:', ' '.join(args), flush=True)
    subprocess.run(args, cwd=str(repo), check=True)


def load_profile(path: Path) -> dict[tuple[str, str], list[int]]:
    data = path.read_text(encoding='utf-8', errors='replace')
    if 'FI_PROFILE_PASS:' not in data or 'FI_DISABLED_CONTROL_PASS' not in data:
        raise ValueError('Missing successful fault-free XSim profile markers: ' + str(path))
    if 'FI_APPLIED scenario=' in data or 'FI_RESULT_V2,' in data:
        raise ValueError('The timing profile unexpectedly contains an FI execution')
    events = {}
    for line in data.splitlines():
        m = EVENT_RE.fullmatch(line.strip())
        if m:
            kind, target, absolute, relative = m.groups()
            absolute, relative = int(absolute), int(relative)
            if absolute - relative != 3:
                raise ValueError(f'Unexpected start_cycle at {target}: {absolute} != {relative}+3')
            events.setdefault((target, kind), []).append(relative)
    if len(events) < 3:
        raise ValueError('Too few real FI_PROFILE_EVENT records')
    return events


def check_windows(events: dict[tuple[str, str], list[int]]) -> None:
    for target, spec in CHANGES.items():
        writes = events.get((target, 'WRITE'), [])
        if spec['write'] not in writes:
            raise ValueError(f'{target}: expected WRITE offset {spec["write"]} missing: {writes}')
        for offset in map(int, spec['new']):
            if target == 'BUF_0007':
                # Only a preliminary write-before-candidate and downstream write bound.
                if spec['upper_by_mem2_write'] not in events.get(('MEM_0002', 'WRITE'), []):
                    raise ValueError('Missing MEM_0002 downstream write witness')
                if not spec['write'] < offset < spec['upper_by_mem2_write']:
                    raise ValueError(f'{target}: candidate offset {offset} outside observed bounds')
            else:
                reads = events.get((target, 'READ'), [])
                if spec['read'] not in reads:
                    raise ValueError(f'{target}: expected READ offset {spec["read"]} missing: {reads}')
                if not spec['write'] < offset < spec['read']:
                    raise ValueError(f'{target}: candidate offset {offset} outside write/read lifetime')


def read_plan(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open('r', encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames
        if columns is None or (tuple(columns) != FIELDS and
                tuple(columns) != FIELDS[:3] + ('bit_index_b',) + FIELDS[3:]):
            raise ValueError(f'Unexpected pilot400_targets.csv columns: {columns}')
        rows = list(reader)
    if len(rows) != 20 or len({r['target_id'] for r in rows}) != 20:
        raise ValueError('Expected 20 unique plan targets')
    return rows, columns


def write_plan(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    tmp = path.with_suffix('.csv.pending')
    try:
        with tmp.open('w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--profile-log', type=Path,
                    default=Path('fi/results/pilot400_profile/profile.log'))
    ap.add_argument('--apply', action='store_true', help='Write source plan, archive and refreeze')
    ap.add_argument('--reason', default='', help='Required audit reason when applying')
    args = ap.parse_args()
    if args.apply and len(args.reason.strip()) < 12:
        raise ValueError('--apply requires a specific --reason (12+ characters)')
    repo = args.repo.resolve()
    profile = args.profile_log if args.profile_log.is_absolute() else repo / args.profile_log
    events = load_profile(profile)
    check_windows(events)
    source = repo / 'fi/config/pilot400_targets.csv'
    manifest = repo / 'fi/scenarios/pilot400.csv'
    generator = repo / 'fi/scripts/generate_pilot400.py'
    for path in (source, manifest, generator):
        if not path.is_file():
            raise ValueError('Missing required existing Pilot400 file: ' + str(path))
    rows, fields = read_plan(source)
    by_id = {r['target_id']: r for r in rows}
    existing = []
    for target, spec in CHANGES.items():
        row = by_id[target]
        actual = (row['offset_a'], row['offset_b'])
        if actual not in (spec['old'], spec['new']):
            raise ValueError(f'{target}: unexpected offsets {actual}; refusing to overwrite')
        if row['evidence_status'] != 'CANDIDATE':
            raise ValueError(f'{target}: status {row["evidence_status"]} not CANDIDATE')
        if actual == spec['new']:
            existing.append(target)
        print(f'{target}: {actual[0]}/{actual[1]} -> {spec["new"][0]}/{spec["new"][1]} '
              '(CANDIDATE: no consumer/impact proof)')
    if not args.apply:
        print('PILOT400_TIMING_DRYRUN_PASS: profile checked; no files changed.')
        return
    if len(existing) == len(CHANGES):
        command([sys.executable, str(generator), '--verify'], repo)
        print('PILOT400_TIMING_ALREADY_APPLIED: no new refreeze performed')
        return
    if existing:
        raise ValueError('Partially updated plan found; refusing partial refreeze')
    # Baseline consistency before changing the source or manifest.
    command([sys.executable, str(generator), '--verify'], repo)
    old_plan_sha, old_manifest_sha = digest(source), digest(manifest)
    history = repo / 'fi/config/history'
    history.mkdir(parents=True, exist_ok=True)
    source_backup = history / f'pilot400_targets_{old_plan_sha[:16]}.csv'
    if not source_backup.is_file():
        shutil.copy2(source, source_backup)
    for target, spec in CHANGES.items():
        row = by_id[target]
        row['offset_a'], row['offset_b'] = spec['new']
        # Evidence status intentionally remains CANDIDATE.
    write_plan(source, rows, fields)
    command([sys.executable, str(generator), '--refreeze', '--reason', args.reason.strip()], repo)
    command([sys.executable, str(generator), '--verify'], repo)
    command([sys.executable, str(repo / 'fi/scripts/preflight_pilot400.py'), '--require-assets'], repo)
    command([sys.executable, '-m', 'unittest', 'discover', '-s', 'fi/scripts', '-p', 'test_pilot400.py'], repo)
    record = {
        'utc': datetime.now(timezone.utc).isoformat(),
        'reason': args.reason.strip(),
        'profile_path': str(profile), 'profile_sha256': digest(profile),
        'source_plan_before_sha256': old_plan_sha,
        'source_plan_after_sha256': digest(source),
        'manifest_before_sha256': old_manifest_sha,
        'manifest_after_sha256': digest(manifest),
        'source_backup': str(source_backup.relative_to(repo)),
        'changes': {k: {'old': v['old'], 'new': v['new']} for k, v in CHANGES.items()},
        'evidence': 'CANDIDATE_ONLY_NOT_FI_CONSUMPTION_PROOF',
    }
    with (history / 'pilot400_timing_candidate_changes.jsonl').open('a', encoding='utf-8') as file:
        file.write(json.dumps(record, ensure_ascii=True) + '\n')
    print('PILOT400_CANDIDATE_REFREEZE_PASS: 400 scenario plan regenerated and archived.')
    print('IMPORTANT: This is timing candidate preparation, NOT 400 FI XSim validation.')
    print('IMPORTANT: CTL/DAT NOT_PROFILED and other target windows still need proof.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit('PILOT400_CANDIDATE_REFREEZE_FAIL: ' + str(exc))

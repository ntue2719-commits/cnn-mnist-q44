#!/usr/bin/env python3
"""Fault-free XSim Pilot400 timing profile (schema V2) and eligibility audit.

The profile measures *baseline* producer/consumer edge semantics, not fault
propagation. FULL 400 execution must use a schema-V2 log and fully eligible
40-offset audit. No RTL changes or automatic manifest edits are made here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import json
import re
import subprocess

from pilot400_common import load_plan, verify_manifest, write_csv, sha256
from run_pilot400 import make_tcl

SCHEMA_MARKER = 'FI_PROFILE_SCHEMA_V2: ACTIVE PRODUCE CONSUME PRE_NBA'
PAT = re.compile(r'^FI_PROFILE_EVENT,(WRITE|READ|FC_VALID|ACTIVE|PRODUCE|CONSUME),'
                 r'([A-Z]{3}_[0-9]{4}),(\d+),(\d+)$')
ALLOWED = {
    'CTL': {'ACTIVE'},
    'DAT': {'PRODUCE', 'CONSUME', 'FC_VALID'},
    'MEM': {'WRITE', 'READ', 'CONSUME'},
    'BUF': {'WRITE', 'CONSUME'},
}
# These prove baseline *temporal eligibility*, NOT that the injected value
# changes a downstream arithmetic input, intermediate output or prediction.
ELIGIBLE_STATUSES = {
    'CTL': 'CTL_ACTIVE_AT_EDGE',
    'DAT': 'DAT_HANDOFF_AT_EDGE',
    'MEM': 'WRITE_READ_CONSUME_WINDOW',
    'BUF': 'BUF_WRITE_CONSUME_WINDOW',
}
COLUMNS = ('target_id', 'class', 'offset_a', 'offset_b',
           'observed_write_offsets', 'observed_read_offsets', 'status_a', 'status_b',
           'observed_active_count', 'observed_produce_count',
           'observed_consume_count', 'evidence_schema')


def assess(plan: list[dict[str, str]],
           events: list[tuple[str, str, int, int]]) -> list[dict[str, str]]:
    """Conservative, edge-based decisions against actual fault-free events.

    Deposit at falling edge before requested rising edge O; registered DAT
    value produced at P < O can be read at consumer edge O, even when that
    very edge also writes the register again via NBA.
    """
    by = defaultdict(lambda: defaultdict(list))
    for kind, target, cycle, offset in events:
        by[target][kind].append(offset)
    report = []
    for p in plan:
        target = p['target_id']
        cls = p['target_class']
        info = by[target]
        a, b = (int(p[k]) for k in ('offset_a', 'offset_b'))
        writes = sorted(info['WRITE'])
        reads = sorted(info['READ'])
        consumes = sorted(info['CONSUME'])
        produces = sorted(info['PRODUCE'])
        active = set(info['ACTIVE'])
        statuses = []
        for offset in (a, b):
            if cls == 'CTL':
                if offset in active:
                    check = ELIGIBLE_STATUSES[cls]
                else:
                    check = 'FAIL_CTL_INACTIVE' if active else 'NOT_PROFILED'
            elif cls == 'DAT':
                # Consumer must be sampling this specific registered value at
                # exactly the requested edge. A producer earlier than the
                # edge is mandatory, and the most recent producer is selected.
                last_producer = max((w for w in produces if w < offset), default=None)
                if last_producer is not None and offset in consumes:
                    check = ELIGIBLE_STATUSES[cls]
                else:
                    check = 'FAIL_DAT_HANDOFF' if (produces or consumes) else 'NOT_PROFILED'
            elif cls == 'MEM':
                # Require natural write, registered memory read, and the
                # following accepted word at downstream arithmetic pipeline.
                eligible = False
                for w in writes:
                    if w >= offset:
                        continue
                    for r in reads:
                        if r <= offset:
                            continue
                        for c in consumes:
                            if c > r and not any(w < newer <= c for newer in writes):
                                eligible = True
                                break
                        if eligible:
                            break
                    if eligible:
                        break
                if eligible:
                    check = ELIGIBLE_STATUSES[cls]
                elif any(w < offset < r for w in writes for r in reads):
                    check = 'WRITE_LT_INJECT_LT_READ'  # only partial evidence
                else:
                    check = 'FAIL_MEM_LIFETIME' if (writes or reads) else 'NOT_PROFILED'
            elif cls == 'BUF':
                # Registers used at the consuming edge are read pre-NBA. The
                # line-buffer slot may be overwritten on that same edge.
                eligible = False
                for w in writes:
                    if w >= offset:
                        continue
                    if any(c >= offset and not any(w < newer < c for newer in writes)
                           for c in consumes):
                        eligible = True
                        break
                if eligible:
                    check = ELIGIBLE_STATUSES[cls]
                elif any(w < offset for w in writes):
                    check = 'AFTER_OBSERVED_WRITE'  # consumer not established
                else:
                    check = 'FAIL_BUF_LIFETIME' if writes else 'NOT_PROFILED'
            else:
                raise ValueError('Unknown target class: ' + cls)
            statuses.append(check)
        report.append({
            'target_id': target, 'class': cls,
            'offset_a': str(a), 'offset_b': str(b),
            'observed_write_offsets': ';'.join(map(str, writes)),
            'observed_read_offsets': ';'.join(map(str, reads)),
            'status_a': statuses[0], 'status_b': statuses[1],
            'observed_active_count': str(len(active)),
            'observed_produce_count': str(len(produces)),
            'observed_consume_count': str(len(consumes)),
            'evidence_schema': 'V2',
        })
    return report


def timing_gate_pass(report: list[dict[str, str]]) -> bool:
    """UNKNOWN, partial evidence and FAIL_* are all fail-closed."""
    return bool(report) and all(
        r['status_a'] == ELIGIBLE_STATUSES[r['class']]
        and r['status_b'] == ELIGIBLE_STATUSES[r['class']]
        and r.get('evidence_schema') == 'V2'
        for r in report
    )


def parse_log(path: Path, *, allow_legacy: bool = False) -> list[tuple[str, str, int, int]]:
    text = path.read_text(encoding='utf-8', errors='replace')
    if text.count('FI_PROFILE_PASS:') != 1 or text.count('FI_DISABLED_CONTROL_PASS') != 1:
        raise ValueError('Profile needs exactly one completed fault-free control PASS: '+str(path))
    if 'FI_RESULT_V2,' in text or 'FI_APPLIED scenario=' in text:
        raise ValueError('Profile unexpectedly contains a fault injection')
    if SCHEMA_MARKER not in text and not allow_legacy:
        raise ValueError('Profile is legacy/incomplete; run NEW schema-V2 XSim profile, not old 45-event log')
    events = []
    seen = set()
    for line in text.splitlines():
        if not line.startswith('FI_PROFILE_EVENT,'):
            continue
        match = PAT.fullmatch(line.strip())
        if not match:
            raise ValueError('Malformed profile event: '+line[:160])
        kind, target, cycle_s, offset_s = match.groups()
        cycle, offset = int(cycle_s), int(offset_s)
        cls = target.split('_')[0]
        if cls not in ALLOWED or kind not in ALLOWED[cls] or cycle < 0 or offset < 0:
            raise ValueError('Invalid profile event/class: '+line)
        key = (kind, target, cycle, offset)
        if key in seen:
            raise ValueError('Duplicate event: '+line)
        seen.add(key)
        events.append((kind, target, cycle, offset))
    if not events:
        raise ValueError('Zero FI_PROFILE_EVENT lines; check compiled TB')
    origins = {cycle-offset for _,_,cycle,offset in events}
    if len(origins) != 1:
        raise ValueError('Inconsistent absolute-vs-relative event cycle origin: '+str(origins))
    return events


def write_provenance(out: Path, log: Path, tb: Path, plan: Path, manifest: Path,
                     audit: Path, events: list) -> None:
    """Tie the CSV to an exact raw XSim trace and source inputs via SHA256."""
    doc = {
        'schema': 'PILOT400_PROFILE_V2',
        'sample_id': 'IMG0000',
        'event_count': len(events),
        'raw_profile_log': str(log.resolve()),
        'raw_profile_log_sha256': sha256(log),
        'pilot400_tb_sha256': sha256(tb),
        'target_plan_sha256': sha256(plan),
        'scenario_manifest_sha256': sha256(manifest),
        'timing_audit_sha256': sha256(audit),
    }
    (out/'timing_profile_provenance.json').write_text(
        json.dumps(doc, indent=2, sort_keys=True)+'\n', encoding='utf-8')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--vivado', default='vivado')
    p.add_argument('--fileset', default='sim_1')
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/pilot400_profile_v2'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--timeout-seconds', type=int, default=900)
    p.add_argument('--analyze-log', type=Path,
                   help='Analyze a REAL existing schema-V2 fault-free XSim log')
    p.add_argument('--allow-legacy', action='store_true',
                   help='Diagnostic only; old log will never produce a V2 PASS gate')
    args = p.parse_args()
    if args.execute and args.analyze_log:
        raise ValueError('Choose either new --execute XSim or diagnostic --analyze-log, not both')
    repo = args.repo.resolve()
    plan_path = repo/'fi/config/pilot400_targets.csv'
    manifest_path = repo/'fi/scenarios/pilot400.csv'
    tb_path = repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v'
    plan = load_plan(plan_path, repo/'fi/config/fi_targets_candidates_v2.csv')
    records = verify_manifest(manifest_path, plan)
    selected = next(r for r in records if r['sample_id'] == 'IMG0000')
    out = (args.out_dir if args.out_dir.is_absolute() else repo/args.out_dir).resolve()
    if (out/'timing_profile_provenance.json').is_file() and not args.analyze_log:
        raise ValueError('Profile destination already archived; use NEW --out-dir')
    out.mkdir(parents=True, exist_ok=True)
    done, log, tcl = (out/name for name in ('profile.done', 'profile.log', 'profile.tcl'))
    tcl.write_text(make_tcl(args.project.resolve(), selected, done, args.fileset,
                             repo/'fi/pilot400/samples/IMG0000', profile=True), encoding='utf-8')
    if args.analyze_log:
        log = args.analyze_log.resolve()
    elif args.execute:
        if not args.project.is_file():
            raise ValueError('Vivado .xpr does not exist: '+str(args.project))
        done.unlink(missing_ok=True)
        cmd = [args.vivado, '-mode', 'batch', '-source', str(tcl), '-nolog', '-nojournal']
        simlog = (args.project.resolve().parent/(args.project.stem+'.sim') /
                  'sim_1'/'behav'/'xsim'/'simulate.log')
        oldtime = simlog.stat().st_mtime_ns if simlog.is_file() else -1
        with log.open('w', encoding='utf-8', errors='replace') as f:
            cp = subprocess.run(cmd, cwd=args.project.resolve().parent,
                                stdout=f, stderr=subprocess.STDOUT,
                                timeout=args.timeout_seconds, check=False)
        if 'FI_PROFILE_PASS:' not in log.read_text(encoding='utf-8', errors='replace') and \
                simlog.is_file() and simlog.stat().st_mtime_ns != oldtime:
            with log.open('a', encoding='utf-8') as f:
                f.write('\n=== FRESH XSIM PROFILE TRANSCRIPT ===\n'+
                        simlog.read_text(encoding='utf-8', errors='replace')+'\n')
        if cp.returncode or not done.is_file() or done.read_text().strip()!='CNN_PEP_PILOT400_TCL_DONE':
            raise ValueError('Profile XSim/Tcl failed; inspect '+str(log))
    else:
        print('PILOT400_PROFILE_DRY_RUN: Tcl generated, 0 XSim executions:', tcl)
        return
    has_v2 = SCHEMA_MARKER in log.read_text(encoding='utf-8', errors='replace')
    events = parse_log(log, allow_legacy=args.allow_legacy)
    report = assess(plan, events)
    if not has_v2:
        # No legacy-only profile may be promoted to V2 eligibility.
        for row in report:
            row['evidence_schema'] = 'LEGACY_UNVERIFIED'
    audit = out/'timing_window_audit.csv'
    write_csv(audit, COLUMNS, report)
    if has_v2 and args.execute:
        # Only a freshly run Vivado/XSim process with a validated .done file
        # may create the provenance required by the full400 runner.
        write_provenance(out, log, tb_path, plan_path, manifest_path, audit, events)
    print(f'PILOT400_PROFILE_ANALYZED: {len(events)} events, report -> {audit}')
    for row in report:
        print(f' {row["target_id"]}: {row["offset_a"]}/{row["offset_b"]} -> '
              f'{row["status_a"]}/{row["status_b"]}')
    if timing_gate_pass(report):
        print('TIMING_GATE_PASS: 40 baseline temporal windows; no FI exposure claim')
    else:
        print('TIMING_GATE_FAIL: unknown, invalid, legacy or partial evidence. '
              'DO NOT launch full 400.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.TimeoutExpired) as e:
        raise SystemExit('PILOT400_PROFILE_FAIL: '+str(e))

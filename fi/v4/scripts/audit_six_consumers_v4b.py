#!/usr/bin/env python3
"""V4b-B read-only two-configuration re-audit of existing consumer evidence.

No new XSim runs; only V4b-A logs with pinned original FI testbench.
Certifies only narrowly proven consumer values (MEM_0002, BUF_0031,
DAT_0010) when both source traces independently pass strict replay.
Never edits target statuses, RTL, manifests or input evidence. FULL400 BLOCKED.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from audit_v4 import read_json, require, unique_index, V3A_NAME
from pilot400_common import (FIELDS, asset_lines, load_plan, read_csv,
                             sha256, verify_manifest, write_csv)
from run_pilot400_target_validation_v3 import (REPORT_FIELDS,
                                               inspect_applied_and_result)
from parse_fi_v2 import parse_one
from run_six_v4b import (SCHEMA as V4B_SCHEMA, STATUS_DONE,
                         six_scenarios, verify_v4_ancestry)

PROVED = {
    'MEM_0002': 'PRE_NBA_CONV2_MAC_TWO_WINDOWS_FROM_MEM1_WORD84',
    'BUF_0031': 'PRE_NBA_CONV2_MAC_LAST_WINDOW_FROM_BUF_BYTE337',
    'DAT_0010': 'PRE_NBA_ARGMAX_CLASS0_LOGIT_EXACT_XOR',
}
PENDING = ('CTL_0001', 'BUF_0030', 'MEM_0001')
COLUMNS = (*FIELDS, 'outcome', 'consumer_evidence', 'source_marker',
           'log_sha256')
SCHEMA = 'CNN_PEP_V4B_EXISTING_CONSUMER_REAUDIT_V1'
STATUS = 'SIX_CONFIG_CONSUMERS_PROVED_THREE_TARGETS_REMAIN_PENDING'

# Strict full-line matching: text echoed in Vivado Tcl source is not evidence.
APPLIED = re.compile(
    r'^FI_APPLIED scenario=(\S+) target=(\S+) class=(\S+) bit=(\d+) '
    r'old=([01]) new=([01]) requested=(\d+)$', re.M)
DAT_CONSUME = re.compile(
    r'^FI_DAT_ARGMAX_CONSUME cycle=(\d+) logit0=([0-9a-fA-F]{8}) '
    r'golden0=([0-9a-fA-F]{8}) mismatched_classes=(\d+)$', re.M)
MEM_EVENTS = {
    'MEM_WRITE': r'cycle=(\d+) value=([0-9a-fA-F]{8})',
    'INJECT': r'cycle=(\d+) target=(\S+)',
    'MEM_READ': r'cycle=(\d+) word=([0-9a-fA-F]{8})',
    'BUF_CAPTURE': r'cycle=(\d+) data=([0-9a-fA-F]{8})',
    'WINDOW119_EMIT': r'cycle=(\d+) byte=([0-9a-fA-F]{2})',
    'WINDOW120_EMIT': r'cycle=(\d+) byte=([0-9a-fA-F]{2})',
    'WINDOW119_CONSUME': r'cycle=(\d+)',
    'WINDOW120_CONSUME': r'cycle=(\d+)',
    'MEM_PASS': (r'write=(\d+) inject=(\d+) read=(\d+) capture=(\d+) '
                 r'emit119=(\d+) consume119=(\d+) emit120=(\d+) consume120=(\d+)'),
}
BUF_EVENTS = {
    'BUF_CAPTURE': MEM_EVENTS['BUF_CAPTURE'],
    'INJECT': MEM_EVENTS['INJECT'],
    'WINDOW120_EMIT': MEM_EVENTS['WINDOW120_EMIT'],
    'WINDOW120_CONSUME': MEM_EVENTS['WINDOW120_CONSUME'],
    'BUF_PASS': r'capture=(\d+) inject=(\d+) emit120=(\d+) consume120=(\d+)',
}


def one(pattern: re.Pattern[str], txt: str, what: str) -> re.Match[str]:
    matches = list(pattern.finditer(txt))
    require(len(matches) == 1, f'{what}: expected exactly one strict log event, got {len(matches)}')
    return matches[0]


def timeline(txt: str, tid: str) -> dict[str, re.Match[str]]:
    patterns = MEM_EVENTS if tid == 'MEM_0002' else BUF_EVENTS
    events = {name: one(re.compile('^FI_TIMELINE_' + name + ' ' + desc + '$', re.M),
                        txt, tid + '/' + name)
              for name, desc in patterns.items()}
    lines = [line for line in txt.splitlines() if line.startswith('FI_TIMELINE_')]
    require(len(lines) == len(patterns), f'{tid}: unexpected/malformed FI_TIMELINE events')
    require([m.start() for m in events.values()] == sorted(m.start() for m in events.values()),
            f'{tid}: nonmonotonic timeline log order')
    require('FI_TIMELINE_FAIL:' not in txt, f'{tid}: explicit failed monitor')
    return events


def prove_existing_consumer(txt: str, row: dict[str, str],
                            parsed: dict[str, str], golden0: int) -> tuple[str, str]:
    """Replay value/cycle assertions in the frozen original FI testbench.

    V4b-A mechanism replay already checks exact single-XOR deposit/readback.
    This additionally replays the consumer-bound *value*, timing and golden
    contract rather than accepting the presence of a PASS string alone.
    """
    sid, tid, bit = row['scenario_id'], row['target_id'], int(row['bit_index'])
    require(tid in PENDING or tid in PROVED, f'{sid}: unknown historical target')
    require(parsed['scenario_id'] == sid and parsed['target_id'] == tid,
            f'{sid}: FI_RESULT identity differs')
    if tid in PENDING:
        return 'NOT_PROVED_IN_V4B_A_LOGS', 'NONE'
    a = one(APPLIED, txt, sid + '/FI_APPLIED')
    require(a.group(1) == sid and a.group(2) == tid and
            a.group(3) == row['target_class'] and int(a.group(4)) == bit and
            int(a.group(7)) == int(parsed['actual_cycle']) and
            (int(a.group(5)) ^ int(a.group(6))) == 1,
            f'{sid}: FI_APPLIED/source mismatch')
    old_bit, new_bit = int(a.group(5)), int(a.group(6))
    if tid == 'DAT_0010':
        require(bit in (0, 1) and int(row['injection_offset']) == 1340,
                f'{sid}: unexpected DAT_0010 bit/time pair')
        d = one(DAT_CONSUME, txt, sid + '/ArgMax consumer')
        cycle, observed, actual_golden, nbad = (int(d.group(1)),
            int(d.group(2), 16), int(d.group(3), 16), int(d.group(4)))
        require(actual_golden == golden0 and observed == (golden0 ^ (1 << bit)) and
                nbad == 1 and cycle >= int(parsed['actual_cycle']) and
                old_bit == ((golden0 >> bit) & 1) and
                new_bit == 1 - old_bit and
                parsed['outcome'] == 'WRONG_LOGITS_SAME_LABEL' and
                parsed['logits_mismatch'] == '1' and
                parsed['first_bad_stage'] == 'FC' and
                parsed['protocol_ok'] == '1',
                f'{sid}: exact ArgMax sampled mutated class0 value NOT proved')
        return PROVED[tid], 'FI_DAT_ARGMAX_CONSUME'

    require(bit == 0 and parsed['outcome'] == 'FINAL_OUTPUT_MATCH',
            f'{sid}: altered legacy BUF/MEM bit or final outcome')
    ev = timeline(txt, tid)
    inj = int(ev['INJECT'].group(1))
    require(ev['INJECT'].group(2) == tid and inj == int(parsed['actual_cycle']),
            f'{sid}: timeline injection cycle mismatch')
    if tid == 'MEM_0002':
        wc, original = int(ev['MEM_WRITE'].group(1)), int(ev['MEM_WRITE'].group(2), 16)
        rc, read_word = int(ev['MEM_READ'].group(1)), int(ev['MEM_READ'].group(2), 16)
        cc, capture = int(ev['BUF_CAPTURE'].group(1)), int(ev['BUF_CAPTURE'].group(2), 16)
        ee119, bb119 = int(ev['WINDOW119_EMIT'].group(1)), int(ev['WINDOW119_EMIT'].group(2), 16)
        ca119 = int(ev['WINDOW119_CONSUME'].group(1))
        ee120, bb120 = int(ev['WINDOW120_EMIT'].group(1)), int(ev['WINDOW120_EMIT'].group(2), 16)
        ca120 = int(ev['WINDOW120_CONSUME'].group(1))
        summary = tuple(map(int, ev['MEM_PASS'].groups()))
        flipped = original ^ 1
        require((wc < inj < rc < cc < ee119 < ca119 == ee120 < ca120) and
                ca120 == ee120 + 1 and ca119 == ee119 + 1 and
                (read_word, capture) == (flipped, flipped) and
                (bb119, bb120) == (flipped & 255, flipped & 255) and
                summary == (wc, inj, rc, cc, ee119, ca119, ee120, ca120) and
                old_bit == (original & 1) and new_bit == (flipped & 1),
                f'{sid}: corrupted Mem1 word84 not proved at Conv2 consumer')
        return PROVED[tid], 'FI_TIMELINE_MEM_PASS'

    cc, capture = int(ev['BUF_CAPTURE'].group(1)), int(ev['BUF_CAPTURE'].group(2), 16)
    ee, byte = int(ev['WINDOW120_EMIT'].group(1)), int(ev['WINDOW120_EMIT'].group(2), 16)
    consumed = int(ev['WINDOW120_CONSUME'].group(1))
    summary = tuple(map(int, ev['BUF_PASS'].groups()))
    orig_byte = (capture >> 8) & 255
    flipped_byte = orig_byte ^ 1
    require(cc < inj < ee < consumed and consumed == ee + 1 and
            byte == flipped_byte and summary == (cc, inj, ee, consumed) and
            old_bit == (orig_byte & 1) and new_bit == (flipped_byte & 1),
            f'{sid}: corrupted Conv2 buffer byte337 not proved at MAC consumer')
    return PROVED[tid], 'FI_TIMELINE_BUF_PASS'


def audit(repo: Path, results: Path, weights: Path, v4_audit: Path,
          v4b: Path, out: Path) -> dict:
    """Read-only audit; never modifies a single evidence/source file."""
    require(not out.exists(), f'Refusing to overwrite audit output directory: {out}')
    fresh_v4, v4_hash = verify_v4_ancestry(repo, results, weights, v4_audit)
    plan = load_plan(repo/'fi/config/pilot400_targets.csv',
                     repo/'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(repo/'fi/scenarios/pilot400.csv', plan)
    selected = six_scenarios(manifest, plan)
    sid_index = {r['scenario_id']:r for r in selected}
    require(len(sid_index) == 12, 'Six target selected scenario IDs not unique')
    meta = read_json(v4b/'v4b_manifest.json')
    require(meta.get('schema') == V4B_SCHEMA and meta.get('status') == STATUS_DONE and
            meta.get('consumer_qualified') is False and
            meta.get('full400_authorized') is False and
            meta.get('target_status_modified') is False and
            meta.get('selected_count') == 12 and
            meta.get('source_authority') == 'vivado',
            'V4b-A mechanism manifest is not valid/locked')
    require(meta.get('v4_cross_audit_sha256') == v4_hash and
            meta.get('frozen_plan_sha256') == sha256(repo/'fi/config/pilot400_targets.csv') and
            meta.get('frozen_manifest_sha256') == sha256(repo/'fi/scenarios/pilot400.csv') and
            meta.get('frozen_tb_sha256') == sha256(repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v') and
            meta.get('v3a_manifest_sha256') == fresh_v4['v3a_manifest_sha256'] and
            meta.get('timing_audit_sha256') == fresh_v4['timing_audit_sha256'] and
            meta.get('control10_manifest_sha256') == fresh_v4['control10_manifest_sha256'],
            'V4b-A cross-evidence provenance changed')
    selected_path = v4b/'selected_scenarios.csv'
    require(sha256(selected_path) == meta.get('selected_sha256') and
            read_csv(selected_path, FIELDS) == selected,
            'V4b-A selected file differs from frozen source')
    prior = read_json(results/V3A_NAME/'validation_manifest.json')
    require(meta.get('snapshot_build', {}).get('source_hashes') ==
            prior.get('snapshot_build', {}).get('source_hashes'),
            'V4b-A Vivado RTL SHA256 differs from V3a source')
    manifest_records = unique_index(meta.get('records', []), 'scenario_id', 12,
                                    'V4b-A manifest records')
    mechanism_csv = unique_index(read_csv(v4b/'mechanism_validation.csv', REPORT_FIELDS),
                                 'scenario_id', 12, 'V4b-A mechanism CSV')
    require(meta.get('mechanism_csv_sha256') == sha256(v4b/'mechanism_validation.csv') and
            meta.get('target_summary_sha256') == sha256(v4b/'target_summary.csv'),
            'V4b-A output CSV SHA256 mismatch')
    golden0 = asset_lines(repo/'fi/pilot400/samples/IMG0000/golden.hex', 10, 8)[0]
    report = []
    for row in selected:
        sid = row['scenario_id']; tid = row['target_id']
        log = (v4b/'logs'/(sid+'.log')).resolve()
        entry = manifest_records[sid]
        require(log.is_file() and Path(entry['log']).resolve() == log and
                entry['log_sha256'] == sha256(log),
                f'{sid}: V4b-A original log path or SHA256 mismatch')
        replay = inspect_applied_and_result(log, row)
        require(mechanism_csv[sid] == replay and entry['outcome'] == replay['outcome'] and
                entry.get('consumer_status') == 'NOT_INDEPENDENTLY_AUDITED',
                f'{sid}: V4b-A mechanism/result manifest differs from strict parser')
        txt = log.read_text(encoding='utf-8', errors='replace')
        proof, marker = prove_existing_consumer(txt, row, parse_one(log), golden0)
        report.append({**{k:row[k] for k in FIELDS},
                       'outcome':replay['outcome'], 'consumer_evidence':proof,
                       'source_marker':marker, 'log_sha256':sha256(log)})
    proved = [r for r in report if r['consumer_evidence'] in set(PROVED.values())]
    proved_targets = {r['target_id'] for r in proved}
    require(len(proved) == 6 and proved_targets == set(PROVED) and
            all(sum(r['target_id']==tid for r in proved)==2 for tid in PROVED),
            'Not all six value-aware V4b-B logs independently demonstrated consumer input')
    require(len(report)==12 and set(r['target_id'] for r in report) == set(PROVED)|set(PENDING),
            'Missing 6-target replay records')
    out.mkdir(parents=True, exist_ok=False)
    csv_path = out/'existing_consumer_evidence.csv'
    write_csv(csv_path, COLUMNS, report)
    summary = {
        'schema': SCHEMA, 'status': STATUS,
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'v4_audit_sha256':v4_hash,
        'v4b_a_manifest_sha256':sha256(v4b/'v4b_manifest.json'),
        'v4b_a_mechanism_csv_sha256':sha256(v4b/'mechanism_validation.csv'),
        'original_tb_sha256':sha256(repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v'),
        'source_authority':'vivado', 'evidence_rows':12,
        'direct_consumer_scenarios':6, 'direct_consumer_targets':sorted(PROVED),
        'still_pending_targets':list(PENDING),
        'csv_sha256':sha256(csv_path),
        'xsim_executions_added':0,
        'source_modified':False, 'target_status_modified':False,
        'full400_authorized':False,
    }
    (out/'existing_consumer_audit.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True)+'\n', encoding='utf-8')
    lines = [
        '# CNN_PEP V4b-B: Existing Consumer Evidence Re-Audit', '',
        '> Strict replay of V4b-A original logs. No new XSim runs.',
        '> All results are directed IMG0000 only; not SEU population estimates.', '',
        '- V4b-A mechanism: **12/12 verified**',
        '- Existing value-aware consumer evidence: **6/12 scenarios, 3/6 targets**',
        '- Remaining target-specific consumer/decision evidence: **3 targets**',
        '- Full400 authorization: **BLOCKED**', '',
        '| Target | Two scenarios | Consumer/decision proof |',
        '|---|---:|---|',
    ]
    for tid in ('CTL_0001','BUF_0030','BUF_0031','MEM_0001','MEM_0002','DAT_0010'):
        label = PROVED.get(tid, 'NOT_PROVED_IN_V4B_A_LOGS')
        lines.append(f'| `{tid}` | 2/2 | `{label}` |')
    lines += ['', '## Interpretation', '',
        '- MEM_0002 and BUF_0031: existing frozen testbench validates corrupted '
          'byte at Conv2 MAC input on rising edge PRE-NBA, plus lifetime chronology.',
        '- DAT_0010: exact class0 modified logit sampled by ArgMax PRE-NBA; '
          'bit0 and bit1 at the same offset are independently replayed.',
        '- CTL_0001: protocol outcome alone is NOT a control-decision proof.',
        '- BUF_0030 and MEM_0001: final match does NOT prove nonconsumption.',
        '- These are scenario-local proofs only, not automatic status promotion.',
        '- Do not launch full400 until remaining targets and independent gate audit pass.',
        '']
    (out/'existing_consumer_report.md').write_text('\n'.join(lines), encoding='utf-8')
    print('V4B_EXISTING_CONSUMER_REAUDIT_PASS: 6/12 scenarios, 3/6 historical '
          'targets consumer-value verified from original logs; 3 pending; '
          '0 additional XSim runs; FULL400 BLOCKED', flush=True)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--results-dir', type=Path, default=Path('fi/results'))
    ap.add_argument('--weights-dir', type=Path, default=Path('data'))
    ap.add_argument('--v4-audit', type=Path,
                    default=Path('fi/results/v4_cross_evidence_audit_02/v4_audit.json'))
    ap.add_argument('--v4b-dir', type=Path,
                    default=Path('fi/results/v4b_six_mechanism_01'))
    ap.add_argument('--out-dir', type=Path,
                    default=Path('fi/results/v4b_existing_consumer_reaudit_01'))
    args = ap.parse_args()
    repo = args.repo.resolve()
    locate = lambda x: x.resolve() if x.is_absolute() else (repo/x).resolve()
    try:
        audit(repo, locate(args.results_dir), locate(args.weights_dir),
              locate(args.v4_audit), locate(args.v4b_dir), locate(args.out_dir))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        raise SystemExit('V4B_EXISTING_CONSUMER_REAUDIT_FAIL: '+str(exc)) from exc


if __name__ == '__main__':
    main()

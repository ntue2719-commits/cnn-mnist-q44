#!/usr/bin/env python3
"""CNN_PEP V4P: immutable, offline historical observability + hypothesis audit.

This module NEVER invokes Vivado/XSim. Its output is a candidate scientific
analysis, not G0-G7 approval and not a production Full400 runner.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys


SCHEMA = 'CNN_PEP_V4P_HISTORICAL_OFFLINE_NOVELTY_CANDIDATE_V1'
EXPECTED_FROZEN = {
    'fi/config/fi_targets_candidates_v2.csv': 'd9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
    'fi/config/pilot400_targets.csv': '98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
    'fi/scenarios/pilot400.csv': 'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
    'fi/tb/tb_cnn_mnist_fi_pilot400.v': '2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
}
VALID_OUTCOMES = ('NONCOMPLETION', 'PROTOCOL_VIOLATION', 'WRONG_LABEL',
                  'WRONG_LOGITS_SAME_LABEL', 'FINAL_OUTPUT_MATCH')
L0_HARMFUL = frozenset(VALID_OUTCOMES[:3])
FIRST_STAGES = {'NONE', 'TIMEOUT', 'PROTOCOL', 'POOL1', 'POOL2', 'FC'}
RESULT_COLUMNS = (
    'scenario_id', 'sample_id', 'target_id', 'target_class', 'bit_index',
    'injection_offset', 'requested_cycle', 'actual_cycle', 'fi_events',
    'injection_ok', 'first_done_cycle', 'first_done_pixels', 'pixels_driven',
    'pixels_accepted', 'pool1words', 'pool2words', 'fcvalid', 'argmax', 'done',
    'protocol_ok', 'pool1_mismatch', 'pool2_mismatch', 'logits_mismatch',
    'prediction_at_done', 'first_bad_stage', 'outcome'
)
ROW_FIELDS = ('scenario_id', 'sample_id', 'target_id', 'target_class',
              'bit_index', 'injection_offset', 'outcome', 'first_bad_stage',
              'pool1words', 'pool2words', 'pool1_mismatch', 'pool2_mismatch',
              'logits_mismatch', 'L0', 'L1', 'L2', 'L0_P1', 'L0_P2',
              'L1_P1', 'L1_P2', 'proof_source', 'consumer_proof',
              'raw_log_sha256')
# These two DAT byte indices are predicted in the separate V4N analysis
# documented in PROJECT_PROGRESS (8).md, not independently located in a
# complete faulty RTL POOL1 dump bundled with the older handoff.
HYPOTHESES = (
    ('FI_P400_0101', 337, 'V3b logged accepted BUF byte337'),
    ('FI_P400_0102', 337, 'V3b logged accepted BUF byte337'),
    ('FI_P400_0301', 78, 'V4N historical arithmetic-predicted location; not directly measured faulty POOL1 byte'),
    ('FI_P400_0302', 119, 'V4N historical arithmetic-predicted location; not directly measured faulty POOL1 byte'),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2,
                       separators=(',', ': ')) + '\n').encode('ascii')


def canonical_csv(rows: list[dict[str, object]], fields: tuple[str, ...]) -> bytes:
    s = io.StringIO(newline='')
    w = csv.DictWriter(s, fieldnames=fields, lineterminator='\n')
    w.writeheader()
    for row in rows:
        w.writerow({k: row[k] for k in fields})
    return s.getvalue().encode('ascii')


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def manifest_rows(repo: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    pins = {}
    for rel, expected in EXPECTED_FROZEN.items():
        fp = repo / rel
        require(fp.is_file(), f'Missing frozen file: {rel}')
        digest = sha(fp)
        require(digest == expected, f'Frozen SHA mismatch: {rel}')
        pins[rel] = digest
    roster = csv_rows(repo / 'fi/scenarios/pilot400.csv')
    require(len(roster) == 400, 'Expected 400 exactly frozen scenarios')
    require(len({r['scenario_id'] for r in roster}) == 400, 'Duplicate frozen scenario ID')
    picked = [r for r in roster if r['sample_id'] == 'IMG0000']
    require(len(picked) == 40 and len({r['target_id'] for r in picked}) == 20,
            'Expected 20 targets / 40 IMG0000 scenarios')
    return picked, pins


def matrix_rows(repo: Path, picked: list[dict[str, str]]) -> tuple[list[dict[str, str]], str]:
    result_dir = repo / 'fi/results/v4c_readiness_audit_01'
    matrix_path = result_dir / 'v4c_target_evidence_matrix.csv'
    manifest_path = result_dir / 'v4c_manifest.json'
    require(matrix_path.is_file() and manifest_path.is_file(), 'Missing original V4c evidence matrix/manifest')
    m = json.loads(manifest_path.read_bytes())
    digest = sha(matrix_path)
    require(digest == m.get('target_matrix_sha256'), 'V4c evidence matrix has wrong SHA')
    require(m.get('directed_img0000_proofs') == 40 and m.get('full400_authorized') is False,
            'V4c scope marker changed')
    matrix = csv_rows(matrix_path)
    require(len(matrix) == 20 and len({x['target_id'] for x in matrix}) == 20,
            'Malformed 20-target V4c evidence matrix')
    by_id = {r['scenario_id']: r for r in picked}
    scenario_evidence = []
    for record in matrix:
        for config in ('a', 'b'):
            sid = record[f'config_{config}_scenario']
            require(sid in by_id, 'Matrix scenario not in frozen IMG0000 roster')
            raw = by_id[sid]
            expected = (record['target_id'], record['target_class'],
                        record[f'config_{config}_bit'], record[f'config_{config}_offset'])
            actual = (raw['target_id'], raw['target_class'],
                      raw['bit_index'], raw['injection_offset'])
            require(actual == expected, f'Matrix/frozen tuple disagreement: {sid}')
            proof_pair = record['proof_kind'].split(' | ')
            require(len(proof_pair) == 2 and all(proof_pair), 'Missing scenario-level consumer/decision proof')
            scenario_evidence.append({
                **raw,
                'outcome': record[f'config_{config}_outcome'],
                'log_sha256': record[f'config_{config}_log_sha256'],
                'proof_source': record['proof_source'],
                'consumer_proof': proof_pair[0 if config == 'a' else 1],
            })
    require(len(scenario_evidence) == 40 and len({r['scenario_id'] for r in scenario_evidence}) == 40,
            'Missing / repeated V4c scenario')
    return sorted(scenario_evidence, key=lambda r: r['scenario_id']), digest


def log_hash_paths(repo: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = defaultdict(list)
    for path in (repo / 'fi/results').rglob('FI_P400_*.log'):
        if path.is_file() and path.parent.name == 'logs':
            found[sha(path)].append(path)
    return found


def parse_log(data: bytes, expected: dict[str, str]) -> dict[str, object]:
    txt = data.decode('utf-8', errors='strict')
    lines = txt.splitlines()
    applied = [line for line in lines if line.startswith('FI_APPLIED ')]
    results = [line for line in lines if line.startswith('FI_RESULT_V2,')]
    first = [line for line in lines if line.startswith('OUTCOME=')]
    require(len(applied) == len(results) == len(first) == 1,
            f'Missing/duplicate strict E1/result/outcome markers for {expected["scenario_id"]}')
    match = re.fullmatch(
        r'FI_APPLIED scenario=(FI_P400_\d{4}) target=(\w+_\d{4}) class=(CTL|BUF|MEM|DAT) '
        r'bit=(\d+) old=(\d+) new=(\d+) requested=(\d+)', applied[0])
    require(match is not None, 'Bad FI_APPLIED marker')
    a_id, a_target, a_class, a_bit, old, new, requested = match.groups()
    require((a_id, a_target, a_class, a_bit) ==
            tuple(expected[k] for k in ('scenario_id', 'target_id', 'target_class', 'bit_index')),
            'Wrong FI_APPLIED scenario tuple')
    require(int(old) in (0, 1) and int(new) == (int(old) ^ 1), 'Not one XOR bit flip')
    values = results[0].split(',')
    require(len(values) == 27 and values[0] == 'FI_RESULT_V2', 'Malformed FI_RESULT_V2 length')
    raw = dict(zip(RESULT_COLUMNS, values[1:]))
    require(all(raw[k] == expected[k] for k in
                ('scenario_id', 'sample_id', 'target_id', 'target_class', 'bit_index', 'injection_offset')),
            'FI_RESULT_V2 is not the frozen tuple')
    numeric = {}
    for key in RESULT_COLUMNS[4:-2]:
        numeric[key] = int(raw[key])
    require(numeric['requested_cycle'] == numeric['actual_cycle'] == int(requested),
            'Injection cycle inconsistent with readback')
    require(numeric['fi_events'] == numeric['injection_ok'] == 1,
            'Expected exactly one verified injection')
    require(raw['outcome'] in VALID_OUTCOMES and raw['first_bad_stage'] in FIRST_STAGES,
            'Unexpected outcome or checkpoint stage')
    require(raw['outcome'] == expected['outcome'], 'V4c evidence outcome/raw log disagreement')
    require(first[0] == f"OUTCOME={raw['outcome']} FIRST_BAD_STAGE={raw['first_bad_stage']}",
            'FIRST_BAD_STAGE / OUTCOME line inconsistent')
    for k in ('pool1words', 'pool2words', 'pool1_mismatch', 'pool2_mismatch', 'logits_mismatch'):
        require(numeric[k] >= 0, f'Invalid negative counter: {k}')
    require(numeric['pool1words'] <= 169 and numeric['pool2words'] <= 25,
            'Checkpoint count exceeds defined stage')
    require(numeric['pool1_mismatch'] <= numeric['pool1words'] * 4 and
            numeric['pool2_mismatch'] <= numeric['pool2words'] * 8,
            'Mismatch count exceeds comparisons')
    # Local testbench increments mismatches PER BYTE, not per packed word.
    if raw['outcome'] == 'FINAL_OUTPUT_MATCH':
        require(numeric['logits_mismatch'] == 0 and
                numeric['pool1words'] == 169 and numeric['pool2words'] == 25,
                'Complete, final-match scenario has invalid stage/FC counters')
    return {**raw, **numeric}


def l0(outcome: str) -> bool:
    return outcome in L0_HARMFUL


def l1(outcome: str) -> bool:
    return l0(outcome) or outcome == 'WRONG_LOGITS_SAME_LABEL'


def stage_level(base: bool, count: int, mismatches: int, complete: int) -> str:
    if base or mismatches > 0:
        return 'TRUE'
    if count == complete and mismatches == 0:
        return 'FALSE'
    return 'INCONCLUSIVE'


def combined_level(base: bool, counts: list[tuple[int, int, int]]) -> str:
    if base or any(mismatches > 0 for _, mismatches, _ in counts):
        return 'TRUE'
    if all(count == complete and mismatches == 0 for count, mismatches, complete in counts):
        return 'FALSE'
    return 'INCONCLUSIVE'


def classify(parsed: dict[str, object], evidence: dict[str, str], log_digest: str) -> dict[str, object]:
    outcome = str(parsed['outcome'])
    c1, m1 = int(parsed['pool1words']), int(parsed['pool1_mismatch'])
    c2, m2 = int(parsed['pool2words']), int(parsed['pool2_mismatch'])
    x0, x1 = l0(outcome), l1(outcome)
    counters = [(c1, m1, 169), (c2, m2, 25)]
    r = {
        **{k: evidence[k] for k in ('scenario_id','sample_id','target_id','target_class','bit_index','injection_offset')},
        'outcome':outcome, 'first_bad_stage':str(parsed['first_bad_stage']),
        'pool1words':c1, 'pool2words':c2,
        'pool1_mismatch':m1, 'pool2_mismatch':m2,
        'logits_mismatch':int(parsed['logits_mismatch']),
        'L0':'TRUE' if x0 else 'FALSE', 'L1':'TRUE' if x1 else 'FALSE',
        'L2':combined_level(x1, counters),
        'L0_P1':stage_level(x0, c1, m1, 169),
        'L0_P2':stage_level(x0, c2, m2, 25),
        'L1_P1':stage_level(x1, c1, m1, 169),
        'L1_P2':stage_level(x1, c2, m2, 25),
        'proof_source':evidence['proof_source'],
        'consumer_proof':evidence['consumer_proof'],
        'raw_log_sha256':log_digest,
    }
    require(r['L2'] != 'INCONCLUSIVE', 'Historical V4c raw L2 missing checkpoint evidence')
    if r['L2'] == 'FALSE':
        require(c1 == 169 and c2 == 25 and m1 == m2 == 0,
                'L2-negative case without complete zero-mismatch stage counters')
    return r


def load_hex8(path: Path):
    import numpy as np
    vals = [int(s, 16) for s in path.read_text(encoding='ascii').splitlines()]
    require(all(0 <= x <= 255 for x in vals), 'Hex INT8 out of range')
    return np.asarray(vals, dtype=np.uint8).view(np.int8)


def load_hex32(path: Path):
    import numpy as np
    vals = [int(s, 16) for s in path.read_text(encoding='ascii').splitlines()]
    require(all(0 <= x < (1 << 32) for x in vals), 'Hex INT32 out of range')
    return np.asarray(vals, dtype=np.uint32).view(np.int32)


def bitexact_counterfactual(repo: Path, ids: set[str]) -> dict[str, object]:
    """Single-byte modeled tensor perturbation, NOT a measured faulty RTL dump."""
    import numpy as np
    sys.path.insert(0, str(repo / 'python'))
    try:
        import golden_model as model
    finally:
        sys.path.pop(0)
    golden_model = repo / 'python/golden_model.py'
    require(golden_model.is_file(), 'Missing bit-exact golden_model.py')
    baseline1 = load_hex8(repo / 'data/golden_pool1.hex').reshape(4, 13, 13)
    relu1 = load_hex8(repo / 'data/golden_relu1.hex').reshape(4, 26, 26)
    computed_pool1 = model.maxpool2x2_q44(relu1)
    require(np.array_equal(baseline1, computed_pool1), 'Independent 676-byte golden Pool1 failed')
    image_pool1 = load_hex8(repo / 'fi/pilot400/samples/IMG0000/golden_pool1.hex').reshape(4, 13, 13)
    require(np.array_equal(baseline1, image_pool1), 'Frozen sample/global Pool1 differ')
    w2 = load_hex8(repo / 'data/conv2_weight.hex').reshape(8, 4, 3, 3)
    b2 = load_hex8(repo / 'data/conv2_bias.hex')
    fcw = load_hex8(repo / 'data/fc_weight.hex').reshape(10, 200)
    fcb = load_hex8(repo / 'data/fc_bias.hex')
    raw2 = model.conv2d_acc_q88(baseline1, w2, b2)
    quant2 = model.requantize_q88_to_q44(raw2)
    relu2 = model.relu_q44(quant2)
    pool2 = model.maxpool2x2_q44(relu2)
    logits = model.fc_q44(pool2, fcw, fcb)
    checks = {
        'conv2_raw_int32': (raw2, load_hex32(repo / 'data/golden_conv2.hex')),
        'conv2_relu_int8': (relu2, load_hex8(repo / 'data/golden_relu2.hex')),
        'pool2_int8': (pool2, load_hex8(repo / 'data/golden_pool2.hex')),
        'fc_logits_int32': (logits, load_hex32(repo / 'data/golden.hex')),
    }
    for name, (actual, expected) in checks.items():
        require(np.array_equal(actual.reshape(-1), expected.reshape(-1)),
                f'Bit-exact golden checkpoint mismatch: {name}')
    rows = []
    for sid, index, provenance in HYPOTHESES:
        require(sid in ids, 'Hypothesis is not an authenticated V4c scenario')
        altered = baseline1.copy()
        old = int(altered.reshape(-1)[index])
        require(old == 0, 'Hypothesis is not original 0->1 byte mutation')
        altered.reshape(-1)[index] = 1
        altered_raw = model.conv2d_acc_q88(altered, w2, b2)
        altered_quant = model.requantize_q88_to_q44(altered_raw)
        altered_relu = model.relu_q44(altered_quant)
        altered_pool2 = model.maxpool2x2_q44(altered_relu)
        altered_logits = model.fc_q44(altered_pool2, fcw, fcb)
        quant_coords = np.argwhere(quant2 != altered_quant).tolist()
        relu_coords = np.argwhere(relu2 != altered_relu).tolist()
        # Explanatory hypotheses under one-byte-only tensor substitution.
        if len(relu_coords) and all(y >= 10 or x >= 10 for _, y, x in relu_coords):
            mechanism = 'UNSAMPLED_ODD_BOUNDARY_POOL2'
        elif len(quant_coords) and not len(relu_coords):
            mechanism = 'RELU_SUPPRESSION_IN_MODEL'
        elif len(relu_coords) and np.count_nonzero(altered_pool2 != pool2) == 0:
            mechanism = 'MAXPOOL_SUPPRESSION_IN_MODEL'
        else:
            mechanism = 'NO_CONCLUSIVE_SIMPLE_MECHANISM'
        rows.append({
            'scenario_id':sid, 'hypothesized_pool1_byte_index':index,
            'index_basis':provenance,
            'original_byte':old, 'modeled_byte':1,
            'conv2_raw_changed_values':int(np.count_nonzero(raw2 != altered_raw)),
            'conv2_requant_changed_values':int(np.count_nonzero(quant2 != altered_quant)),
            'conv2_post_relu_changed_values':int(np.count_nonzero(relu2 != altered_relu)),
            'pool2_changed_values':int(np.count_nonzero(pool2 != altered_pool2)),
            'post_relu_changed_coordinates_ch_y_x':relu_coords,
            'modeled_mechanism_hypothesis':mechanism,
            'logits_changed_values':int(np.count_nonzero(logits != altered_logits)),
            'interpretation':'COUNTERFACTUAL_HYPOTHESIS_NOT_COMPLETE_FAULTY_RTL_TRACE',
        })
    return {
        'evidence_tier':'INDEPENDENT_GOLDEN_BIT_EXACT_AND_SINGLE_BYTE_COUNTERFACTUAL_NOT_RTL_TRACE',
        'pool1_golden_matched_bytes':676,
        'golden_baseline_matched_values':{'conv2_raw':968,'relu2':968,'pool2':200,'logits':10},
        'model_sha256':sha(golden_model),
        'input_pins_sha256':{name:sha(repo / name) for name in (
            'data/golden_pool1.hex','data/golden_relu1.hex','data/golden_conv2.hex',
            'data/golden_relu2.hex','data/golden_pool2.hex','data/golden.hex',
            'data/conv2_weight.hex','data/conv2_bias.hex',
            'data/fc_weight.hex','data/fc_bias.hex',
            'fi/pilot400/samples/IMG0000/golden_pool1.hex')},
        'scenarios':rows,
    }


def summary_from_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    require(len(rows) == 40, 'Report requires 40 V4c original historical scenarios')
    summary = {
        'scenario_count':len(rows),
        'outcome_counts':dict(sorted(Counter(r['outcome'] for r in rows).items())),
        'first_bad_stage_counts':dict(sorted(Counter(r['first_bad_stage'] for r in rows).items())),
        'observability':{},
        'by_target_class':{},
        'pair_outcome_changes':[],
        'l2_only_scenarios':[],
        'l2_negative_scenarios':[],
        'limitations':[
            'Directed, single-image IMG0000; NOT a random physical SEU sampling rate.',
            'E3 provenance uses V4c scenario-level consumer/decision proof and original raw logs, not a new monitor execution.',
            'L2 stage mismatches are original testbench PER-BYTE counters; no complete independently captured faulty-stage dump.',
            'Counterfactual single-byte tensor replacement is not proof of exact faulty RTL tensor or causal masking.',
            'No automatic G7/G6 approval; zero new XSim runs; Full400 blocked.',
        ],
    }
    for level in ('L0','L1','L2','L0_P1','L0_P2','L1_P1','L1_P2'):
        summary['observability'][level] = dict(sorted(Counter(str(r[level]) for r in rows).items()))
    for cls in ('CTL','BUF','MEM','DAT'):
        sub = [r for r in rows if r['target_class'] == cls]
        summary['by_target_class'][cls] = {
            'scenarios':len(sub),
            **{x + '_true':sum(r[x] == 'TRUE' for r in sub) for x in ('L0','L1','L2')},
        }
    by_target = defaultdict(list)
    for r in rows:
        by_target[r['target_id']].append(r)
        if r['L1'] == 'FALSE' and r['L2'] == 'TRUE':
            summary['l2_only_scenarios'].append(r['scenario_id'])
        if r['L2'] == 'FALSE':
            summary['l2_negative_scenarios'].append(r['scenario_id'])
    for target, pair in sorted(by_target.items()):
        require(len(pair) == 2, f'Incorrect A/B pair for {target}')
        if pair[0]['outcome'] != pair[1]['outcome']:
            summary['pair_outcome_changes'].append({
                'target_id':target,
                'scenario_ids':[r['scenario_id'] for r in pair],
                'outcomes':[r['outcome'] for r in pair],
            })
    require(summary['observability']['L0'].get('TRUE') == 9 and
            summary['observability']['L1'].get('TRUE') == 20 and
            summary['observability']['L2'].get('TRUE') == 24 and
            len(summary['l2_only_scenarios']) == 4 and
            len(summary['l2_negative_scenarios']) == 16,
            'V4P historical V4c cohort is inconsistent with V4M/V4O numerical claims')
    require(summary['l2_only_scenarios'] ==
            ['FI_P400_0101','FI_P400_0102','FI_P400_0301','FI_P400_0302'],
            'V4P L2-only witness IDs changed')
    # No POOL2-only gains in this subset, but do not generalize that result.
    return summary


def calculate(repo: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    require(repo.is_dir(), f'Not a repo: {repo}')
    frozen, pins = manifest_rows(repo)
    selected, matrix_sha = matrix_rows(repo, frozen)
    paths = log_hash_paths(repo)
    rows = []
    sources = {}
    for ev in selected:
        digest = ev['log_sha256']
        matches = [path for path in paths.get(digest, []) if path.name == ev['scenario_id'] + '.log']
        require(matches, f'No raw log with exact historical SHA256: {ev["scenario_id"]}')
        matches.sort(key=lambda path: str(path))
        blob = matches[0].read_bytes()
        require(sha_bytes(blob) == digest, 'SHA drift while reading selected log')
        parsed = parse_log(blob, ev)
        rows.append(classify(parsed, ev, digest))
        sources[ev['scenario_id']] = {
            'sha256':digest,
            'relative_path':matches[0].relative_to(repo).as_posix(),
            'duplicate_matching_copies':len(matches),
            'proof_source':ev['proof_source'],
        }
    report = summary_from_rows(rows)
    report['schema'] = SCHEMA
    report['v4p_analysis_code_sha256'] = sha(Path(__file__))
    report['source_scope'] = 'V4C_HISTORICAL_IMG0000_40_ONLY'
    report['source_inputs_sha256'] = pins
    report['v4c_matrix_sha256'] = matrix_sha
    report['log_sources'] = sources
    report['counterfactual'] = bitexact_counterfactual(repo, {r['scenario_id'] for r in rows})
    report['xsim_new_runs'] = 0
    report['full400_authorized'] = False
    report['scientific_status'] = 'OFFLINE_CANDIDATE_NOT_G7_SIGNED'
    return rows, report


def markdown_summary(summary: dict[str, object]) -> bytes:
    obs = summary['observability']
    stages = summary['first_bad_stage_counts']
    cf = summary['counterfactual']
    lines = [
        '# V4P candidate: Consumer-qualified fault-effect and checkpoint ablation',
        '',
        '**Status:** OFFLINE_CANDIDATE_NOT_G7_SIGNED. No XSim, no FI runs, no release approval.',
        '',
        '## Measured historical IMG0000 results',
        '',
        '| Observer | TRUE | FALSE | INCONCLUSIVE |',
        '|---|---:|---:|---:|',
    ]
    for key, title in [('L0','L0 harmful outcomes'),('L1','L1 + wrong logits'),
                       ('L2','L2 + POOL1 and POOL2'),
                       ('L1_P1','L1 + POOL1 only'),('L1_P2','L1 + POOL2 only')]:
        row = obs[key]
        lines.append(f"| {title} | {row.get('TRUE',0)} | {row.get('FALSE',0)} | {row.get('INCONCLUSIVE',0)} |")
    lines += ['', 'These are **paired counts over the same 40 directed scenario IDs**, not a random upset rate.',
              'Checkpoint mismatches are per-byte testbench counters, not an on-chip detector.',
              '', '## First reported divergence (not complete internal first divergence)', '',
              '| Marker | Count |', '|---|---:|']
    for stage in ('TIMEOUT','PROTOCOL','POOL1','POOL2','FC','NONE'):
        lines.append(f"| {stage} | {stages.get(stage,0)} |")
    lines += ['', 'L2-only: ' + ', '.join(summary['l2_only_scenarios']) + '.',
              f"L2-negative complete scenarios: {len(summary['l2_negative_scenarios'])}/16.",
              'Paired A/B targets with outcome differences: ' +
              ', '.join(x['target_id'] for x in summary['pair_outcome_changes']) + '.',
              '', '## Independent bit-exact golden and conditional counterfactual', '',
              'Recomputed golden Pool1 676/676 bytes and Conv2 raw 968 / ReLU2 968 / Pool2 200 / logits 10 values.',
              '',
              '| Scenario | Assumed POOL1 byte | Conv2 raw changed | Requant changed | ReLU changed | POOL2 changed | Logits changed | Hypothesis |',
              '|---|---:|---:|---:|---:|---:|---:|---|']
    for r in cf['scenarios']:
        lines.append(f"| {r['scenario_id']} | {r['hypothesized_pool1_byte_index']} | "
                     f"{r['conv2_raw_changed_values']} | {r['conv2_requant_changed_values']} | "
                     f"{r['conv2_post_relu_changed_values']} | {r['pool2_changed_values']} | "
                     f"{r['logits_changed_values']} | {r['modeled_mechanism_hypothesis']} |")
    lines += ['', '**Critical limit:** This is a **one-byte substituted golden tensor**, not a full faulty RTL feature-map dump. '
              'DAT indices 78/119 are predictions documented by V4N, not locations directly measured from the older archive. '
              'No causal claim about exact RTL masking is established by these simulations alone.',
              '', '## Falsifiable next experiments (separately approved)', '',
              '1. Negative controls: E1-positive/E3-negative with complete edge monitoring and no hidden consumer.',
              '2. Trace four L2-only real RTL scenarios at POOL1 / Conv2 accumulator / quant / ReLU / Pool2 / FC boundaries, '
              'and compare every accepted element to a SHA-pinned golden.',
              '3. If authorized, repeat E3-qualified paired-observer analysis on IMG0001..IMG0009, keeping invalid/inconclusive explicit.',
              '', 'Frozen files, G7 core and existing evidence must not be changed. FULL400 BLOCKED.',
              '']
    return '\n'.join(lines).encode('ascii')


def write_new_output(folder: Path, rows: list[dict[str, object]], report: dict[str, object]) -> None:
    require(not folder.exists(), 'Output directory already exists; refuse overwrite')
    folder.mkdir(parents=True)
    outputs = {
        'V4P_SCENARIO_EVIDENCE.csv':canonical_csv(rows, ROW_FIELDS),
        'V4P_NOVELTY_SUMMARY.json':canonical_json(report),
        'V4P_NOVELTY_REPORT.md':markdown_summary(report),
    }
    for name, blob in outputs.items():
        with (folder / name).open('xb') as f:
            f.write(blob)
    file_hashes = {name:sha_bytes(blob) for name,blob in outputs.items()}
    (folder / 'V4P_OUTPUT_SHA256.json').write_bytes(canonical_json({
        'schema':SCHEMA, 'file_sha256':file_hashes,
        'xsim_new_runs':0, 'full400_authorized':False,
    }))


def audit_existing(folder: Path, rows: list[dict[str, object]], report: dict[str, object]) -> None:
    require(folder.is_dir(), 'Missing V4P output directory')
    expected = {
        'V4P_SCENARIO_EVIDENCE.csv':canonical_csv(rows, ROW_FIELDS),
        'V4P_NOVELTY_SUMMARY.json':canonical_json(report),
        'V4P_NOVELTY_REPORT.md':markdown_summary(report),
    }
    recorded = json.loads((folder / 'V4P_OUTPUT_SHA256.json').read_bytes())
    require(recorded.get('schema') == SCHEMA and recorded.get('xsim_new_runs') == 0 and
            recorded.get('full400_authorized') is False, 'Output receipt has invalid scope')
    require(set(recorded.get('file_sha256',{})) == set(expected), 'Incomplete output inventory')
    for name, content in expected.items():
        path = folder / name
        require(path.read_bytes() == content and recorded['file_sha256'][name] == sha_bytes(content),
                f'Independent regeneration failed: {name}')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True, help='Local repo containing original V4c raw logs')
    p.add_argument('--out-dir', type=Path, required=True, help='New immutable output path (or existing for audit)')
    p.add_argument('--mode', choices=('build', 'audit'), default='build')
    args = p.parse_args()
    try:
        rows, report = calculate(args.repo.resolve())
        if args.mode == 'build':
            write_new_output(args.out_dir, rows, report)
            print('V4P_OFFLINE_NOVELTY_PASS: 40/40 SHA-pinned V4c; L0=9 L1=20 L2=24; '
                  '4 L2-only, 16 full negatives; 676 golden bytes; 0 XSim; FULL400 BLOCKED')
        else:
            audit_existing(args.out_dir, rows, report)
            print('V4P_INDEPENDENT_REPLAY_PASS: same 40 raw logs / source pins / bit-exact counterfactual; '
                  '0 XSim; NOT G7 approval; FULL400 BLOCKED')
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(f'V4P_FAIL_CLOSED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

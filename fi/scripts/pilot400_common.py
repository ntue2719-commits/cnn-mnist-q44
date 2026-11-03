#!/usr/bin/env python3
"""Shared deterministic Pilot400 contracts. Standard library only.

The plan is a selected, balanced design, NOT a probability sample of RTL FFs.
The 400 rows are independent configurations, not 400 verified XSim results.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
import re

CLASSES = ('CTL', 'BUF', 'MEM', 'DAT')
SAMPLES = tuple((f'IMG{i:04d}', n) for i, n in enumerate((3, 0, 1, 2, 4, 5, 6, 7, 8, 9)))
FIELDS = ('scenario_id', 'sample_id', 'target_id', 'target_class', 'bit_index', 'injection_offset')
PLAN_FIELDS = ('target_id', 'target_class', 'bit_index', 'offset_a', 'offset_b', 'evidence_status', 'selection_rationale')
# Optional extended source-plan field. The six-column XSim scenario interface
# remains unchanged: each row always describes exactly ONE bit and ONE cycle.
PLAN_OPTIONAL_FIELDS = ('bit_index_b',)
ASSET_SIZES = {'input.hex': (784, 2), 'golden_pool1.hex': (676, 2),
               'golden_pool2.hex': (200, 2), 'golden.hex': (10, 8),
               'prediction.hex': (1, 2)}


def read_csv(path: Path, required) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f'Missing CSV: {path}')
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or any(x not in reader.fieldnames for x in required):
            raise ValueError(f'{path}: required columns absent: {required}')
        rows = list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError(f'{path}: malformed CSV row or wrong column count')
    return [{k: v.strip() for k, v in row.items()} for row in rows]


def write_csv(path: Path, columns, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in columns})


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(2**20), b''):
            h.update(b)
    return h.hexdigest()


def load_plan(plan_path: Path, catalog_path: Path) -> list[dict[str, str]]:
    plans = read_csv(plan_path, PLAN_FIELDS)
    # Explicitly reject unknown source-plan fields while retaining the older
    # seven-column plan so users can validate BEFORE a versioned refreeze.
    if plans and (set(plans[0]) - set(PLAN_FIELDS) - set(PLAN_OPTIONAL_FIELDS)):
        raise ValueError('Unknown Pilot400 source-plan column')
    has_bit_b = 'bit_index_b' in plans[0] if plans else False
    catalog = {r['target_id']: r for r in read_csv(catalog_path, ('target_id','class','width','bit_min','bit_max'))}
    if len(plans) != 20 or len(set(r['target_id'] for r in plans)) != 20:
        raise ValueError('Pilot400 must have exactly 20 different targets')
    for cls in CLASSES:
        if sum(r['target_class'] == cls for r in plans) != 5:
            raise ValueError(f'Pilot400 needs exactly 5 {cls} targets')
    for p in plans:
        t = catalog.get(p['target_id'])
        if t is None or t['class'] != p['target_class']:
            raise ValueError(f'Unknown/class-mismatched catalog target {p["target_id"]}')
        bit, a, b = (int(p[k]) for k in ('bit_index','offset_a','offset_b'))
        # For legacy plans the second configuration uses the same bit.
        # Extended plans set bit_index_b explicitly for every target.
        bit_b = int(p['bit_index_b']) if has_bit_b else bit
        p['bit_index_b'] = str(bit_b)
        minbit, maxbit, width = (int(t[k]) for k in ('bit_min','bit_max','width'))
        if not (0 <= minbit <= bit <= maxbit < width and minbit <= bit_b <= maxbit):
            raise ValueError(f'Bit outside catalog bounds: {p["target_id"]}')
        if not (3 <= a < 3000 and 3 <= b < 3000):
            raise ValueError(f'Invalid injection offsets: {p["target_id"]}')
        if a == b and not (p['target_id'] == 'DAT_0010' and bit != bit_b):
            raise ValueError(f'Duplicate bit/time tuple or unapproved equal-offset target: {p["target_id"]}')
        if a != b and bit_b != bit:
            raise ValueError(f'Bit variation outside approved DAT_0010 equal-offset policy: {p["target_id"]}')
        if not re.fullmatch(r'[A-Z]{3}_[0-9]{4}', p['target_id']):
            raise ValueError(f'Unsafe target ID: {p["target_id"]}')
    return sorted(plans, key=lambda r: (CLASSES.index(r['target_class']), r['target_id']))


def expected_rows(plans: list[dict[str, str]]) -> list[dict[str, str]]:
    records = []
    for p in plans:
        for sample_id, _ in SAMPLES:
            for offset_field, bit_field in (('offset_a', 'bit_index'),
                                            ('offset_b', 'bit_index_b')):
                records.append({
                    'scenario_id': f'FI_P400_{len(records)+1:04d}',
                    'sample_id': sample_id,
                    'target_id': p['target_id'],
                    'target_class': p['target_class'],
                    'bit_index': p[bit_field],
                    'injection_offset': p[offset_field],
                })
    if len(records) != 400 or len(set(tuple(r[k] for k in FIELDS[1:]) for r in records)) != 400:
        raise AssertionError('Nonunique 400 scenarios')
    return records


def verify_manifest(path: Path, plans: list[dict[str, str]]) -> list[dict[str, str]]:
    rows = read_csv(path, FIELDS)
    expected = expected_rows(plans)
    if rows != expected:
        for i, (a,b) in enumerate(zip(rows, expected)):
            if a != b:
                raise ValueError(f'Manifest differs at row {i+1}: {a} vs {b}')
        raise ValueError(f'Manifest has {len(rows)} rows (need {len(expected)})')
    return rows


def asset_lines(path: Path, count: int, digits: int) -> list[int]:
    if not path.is_file():
        raise ValueError(f'Missing asset: {path}')
    lines = [s.strip() for s in path.read_text(encoding='ascii').splitlines()]
    if len(lines) != count or any(not re.fullmatch(r'[0-9A-Fa-f]{'+str(digits)+r'}', s) for s in lines):
        raise ValueError(f'{path}: expected {count} exact {digits}-hex-digit lines')
    return [int(s, 16) for s in lines]


def validate_assets(samples_dir: Path, require_all: bool = True) -> tuple[int,list[str]]:
    missing, hashes = [], []
    existing = 0
    for sample_id, _ in SAMPLES:
        directory = samples_dir / sample_id
        available = all((directory / name).is_file() for name in ASSET_SIZES)
        if not available:
            missing.append(sample_id)
            continue
        data = {name: asset_lines(directory / name, *shape) for name,shape in ASSET_SIZES.items()}
        # The golden prediction must equal signed strict-> ArgMax of the logits.
        logits = [v if v < (1<<31) else v-(1<<32) for v in data['golden.hex']]
        expected_label = max(range(10), key=lambda i: logits[i])
        if data['prediction.hex'][0] != expected_label:
            raise ValueError(f'{sample_id}: prediction != golden logits ArgMax')
        hashes.append(sha256(directory / 'input.hex'))
        existing += 1
    if len(hashes) != len(set(hashes)):
        raise ValueError('Duplicate MNIST inputs found among sample folders')
    if require_all and missing:
        raise ValueError('Assets not complete; missing samples: '+', '.join(missing)+
                         '. Generate with export_pilot400_samples.py first.')
    return existing, missing

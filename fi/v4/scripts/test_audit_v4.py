#!/usr/bin/env python3
"""Negative tests for the read-only cross-evidence auditor.

All tests use temporary synthetic evidence; none assert actual Windows XSim
was executed or that the real V4 evidence has passed.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import audit_v4 as v4
from pilot400_common import FIELDS, write_csv


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class V4ContractTests(unittest.TestCase):
    def test_group_coverage_28_and_14_unique(self):
        self.assertEqual(sum(2 * len(g.targets) for g in v4.GROUPS), 28)
        all_targets = [t for g in v4.GROUPS for t in g.targets]
        self.assertEqual(len(all_targets), 14)
        self.assertEqual(len(set(all_targets)), 14)
        self.assertEqual(len({g.dirname for g in v4.GROUPS}), 9)

    def test_pin_is_exact_sha256(self):
        self.assertEqual(len(v4.INVENTORY_SHA256), 64)
        int(v4.INVENTORY_SHA256, 16)

    def test_unique_index_rejects_duplicate(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            v4.unique_index([{'k': 's'}, {'k': 's'}], 'k', 2, 'test')

    def test_unique_index_rejects_missing(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            v4.unique_index([{'k': ''}], 'k', 1, 'test')

    def test_unique_index_rejects_count(self):
        with self.assertRaisesRegex(ValueError, 'expected'):
            v4.unique_index([{'k': 'a'}], 'k', 2, 'test')

    def test_qualification_never_promotes(self):
        plan = [
            {'target_id': 'T1', 'target_class': 'MEM', 'evidence_status': 'CANDIDATE'},
            {'target_id': 'T2', 'target_class': 'CTL', 'evidence_status': 'PASS_ONE_WINDOW'},
        ]
        records = {'s1': {'scenario_id': 's1', 'target_id': 'T1'},
                   's2': {'scenario_id': 's2', 'target_id': 'T1'}}
        rows = v4.qualification(plan, records)
        self.assertEqual(rows[0]['qualification_gate'], 'READY_FOR_SCIENTIFIC_REVIEW')
        self.assertEqual(rows[0]['automatic_promotion'], 'NO')
        self.assertEqual(rows[1]['qualification_gate'], 'BLOCKED_NEEDS_ADDITIONAL_EVIDENCE')
        self.assertEqual(rows[1]['automatic_promotion'], 'NO')
        self.assertIn('FULL400 NOT AUTHORIZED', v4.render_report(
            'X', {'G': 2}, rows, {'file_count': 97, 'inventory_sha256': 'b'*64}, 'c'*64))

    def test_missing_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'Missing JSON'):
                v4.read_json(Path(d) / 'missing.json')


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / 'logs').mkdir()
        (self.base / 'logs' / 'a.log').write_bytes(b'original A\n')
        (self.base / 'evidence.txt').write_bytes(b'original B\n')
        self.inv = self.base / 'sha256_inventory.csv'
        self.write_inv()
        self.pin = digest(self.inv)

    def write_inv(self, extra=()):
        with self.inv.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=('File', 'SHA256'))
            writer.writeheader()
            for p in (self.base / 'evidence.txt', self.base / 'logs' / 'a.log'):
                writer.writerow({'File': p.relative_to(self.base).as_posix(), 'SHA256': digest(p)})
            for row in extra:
                writer.writerow(row)

    def test_inventory_ok(self):
        data = v4.check_ctl_inventory(self.base, self.pin, 2)
        self.assertEqual(data['file_count'], 2)
        self.assertEqual(data['status'], 'PASS')

    def test_inventory_tampered_file_rejected(self):
        (self.base / 'evidence.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            v4.check_ctl_inventory(self.base, self.pin, 2)

    def test_inventory_extra_file_rejected(self):
        (self.base / 'new.log').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'file set changed'):
            v4.check_ctl_inventory(self.base, self.pin, 2)

    def test_inventory_missing_file_rejected(self):
        (self.base / 'logs' / 'a.log').unlink()
        with self.assertRaisesRegex(ValueError, 'file set changed'):
            v4.check_ctl_inventory(self.base, self.pin, 2)

    def test_inventory_digest_rejected(self):
        with self.assertRaisesRegex(ValueError, 'inventory digest changed'):
            v4.check_ctl_inventory(self.base, '0'*64, 2)

    def test_inventory_count_rejected(self):
        with self.assertRaisesRegex(ValueError, 'expected 3'):
            v4.check_ctl_inventory(self.base, self.pin, 3)

    def test_inventory_path_escape_rejected(self):
        other = self.base.parent / 'outside.txt'
        # No need to create outside file: pinned inventory can't be trusted with traversal.
        self.write_inv(({'File': '../outside.txt', 'SHA256': 'f'*64},))
        with self.assertRaisesRegex(ValueError, 'file set changed|path escape'):
            v4.check_ctl_inventory(self.base, digest(self.inv), 3)


class SyntheticGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.results = self.root / 'results'
        self.group = v4.GROUPS[0]  # MEM2, four frozen-looking identifiers
        self.folder = self.results / self.group.dirname
        (self.folder / 'logs').mkdir(parents=True)
        v3a = self.results / v4.V3A_NAME
        v3a.mkdir(parents=True)
        (v3a / 'validation_manifest.json').write_text('{"schema":"mock"}\n')
        (v3a / 'source_vlog.prj').write_text('verilog xil_defaultlib \"C:/mock/tb_cnn_mnist_fi_pilot400.v\"\n')
        (self.results / v4.CONTROL_NAME).mkdir()
        (self.results / v4.CONTROL_NAME / 'control10_manifest.json').write_text('control10')
        (self.results / v4.PROFILE_NAME).mkdir()
        (self.results / v4.PROFILE_NAME / 'timing_window_audit.csv').write_text('timing')
        self.tb = self.repo / 'fi/v3b/tb/tb_cnn_mnist_fi_pilot400.v'
        self.tb.parent.mkdir(parents=True)
        self.tb.write_text('module mock; endmodule\n')
        build_prj = self.folder / v4.BUILD_PROJECTS[self.group.name]
        build_prj.parent.mkdir(parents=True)
        build_prj.write_text(v4.override_prj(
            (v3a / 'source_vlog.prj').read_text(), self.tb))
        (self.folder / 'xvlog.log').write_text('mock xvlog')
        (self.folder / 'xelab.log').write_text('mock xelab')
        self.specs = []
        for i, t in enumerate(('MEM_0004', 'MEM_0004', 'MEM_0005', 'MEM_0005'), 1):
            self.specs.append(dict(zip(FIELDS, (f'CASE_{i}', 'IMG0000', t,
                                                  'MEM', '0', str(i + 10)))))
        write_csv(self.folder / self.group.selected, FIELDS, self.specs)
        self.replayed = {}
        entries = []
        for row in self.specs:
            sid = row['scenario_id']
            path = self.folder / 'logs' / (sid + '.log')
            path.write_text('synthetic test log for ' + sid)
            replay = {**row, 'consumer_proof': 'VERIFIED_FC_INPUT',
                      'outcome': 'FINAL_OUTPUT_MATCH', 'log_sha256': digest(path)}
            self.replayed[sid] = replay
            entries.append({'scenario_id': sid, 'log': str(path.resolve()),
                            'log_sha256': digest(path)})
        cols = (*FIELDS, 'consumer_proof', 'outcome', 'log_sha256')
        write_csv(self.folder / 'consumer_validation.csv', cols, list(self.replayed.values()))
        self.meta = {
            'schema': self.group.schema, 'status': self.group.status,
            'source_authority': 'vivado',
            'original_v3a_manifest_sha256': digest(v3a / 'validation_manifest.json'),
            'original_v3a_prj_sha256': digest(v3a / 'source_vlog.prj'),
            'original_tb_sha256': v4.FROZEN_HASHES['fi/tb/tb_cnn_mnist_fi_pilot400.v'],
            'v3b_tb_sha256': digest(self.tb),
            'v3b_prj_sha256': digest(build_prj),
            'xvlog_log_sha256': digest(self.folder / 'xvlog.log'),
            'xelab_log_sha256': digest(self.folder / 'xelab.log'),
            'elaboration_mode': 'FULL_BUILD',
            'control10_manifest_sha256': digest(self.results / v4.CONTROL_NAME / 'control10_manifest.json'),
            'timing_audit_sha256': digest(self.results / v4.PROFILE_NAME / 'timing_window_audit.csv'),
            'selected_sha256': digest(self.folder / self.group.selected),
            'consumer_csv_sha256': digest(self.folder / 'consumer_validation.csv'),
            'records': entries,
        }
        self.write_meta()
        self.expected = {r['scenario_id']: {**r, 'outcome': 'FINAL_OUTPUT_MATCH'} for r in self.specs}
        self.mock_module = SimpleNamespace(
            TB_REL=Path('fi/v3b/tb/tb_cnn_mnist_fi_pilot400.v'),
            validate_frozen_inputs=lambda repo: None,
            parse_consumer=lambda log, row: self.replayed[row['scenario_id']],
        )

    def write_meta(self):
        (self.folder / self.group.manifest).write_text(json.dumps(self.meta))

    def check_group(self):
        with patch.object(v4.importlib, 'import_module', return_value=self.mock_module):
            with patch.object(v4, 'parse_one', return_value={'mock': 'same'}):
                return v4.validate_group_manifest(self.repo, self.folder, self.group,
                                                   {'snapshot_build': {'source_hashes': {}}}, self.expected)

    def test_strict_group_replay_ok(self):
        meta, rows = self.check_group()
        self.assertEqual(len(rows), 4)
        self.assertEqual(meta['status'], 'CONSUMER_PROVED_4_OF_4')

    def test_status_fail_closed(self):
        self.meta['status'] = 'PREPARED_NOT_EXECUTED'
        self.write_meta()
        with self.assertRaisesRegex(ValueError, 'status mismatch'):
            self.check_group()

    def test_ancestry_fail_closed(self):
        self.meta['original_v3a_manifest_sha256'] = '0'*64
        self.write_meta()
        with self.assertRaisesRegex(ValueError, 'ancestry digest mismatch'):
            self.check_group()

    def test_scenario_identity_fail_closed(self):
        self.expected['CASE_1']['injection_offset'] = '999'
        with self.assertRaisesRegex(ValueError, 'scenario contract'):
            self.check_group()

    def test_probe_tamper_fail_closed(self):
        self.tb.write_text('tampered tb')
        with self.assertRaisesRegex(ValueError, 'probe TB file/hash mismatch'):
            self.check_group()

    def test_build_prj_tamper_fail_closed(self):
        path = self.folder / v4.BUILD_PROJECTS[self.group.name]
        path.write_text(path.read_text() + 'verilog xil_defaultlib \"C:/evil.v\"\n')
        with self.assertRaisesRegex(ValueError, 'build .prj digest missing/changed'):
            self.check_group()

    def test_control10_ancestry_fail_closed(self):
        self.meta['control10_manifest_sha256'] = 'f'*64
        self.write_meta()
        with self.assertRaisesRegex(ValueError, 'Control10 ancestry digest mismatch'):
            self.check_group()

    def test_xvlog_tamper_fail_closed(self):
        (self.folder / 'xvlog.log').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'compile/elaboration log missing/changed'):
            self.check_group()

    def test_log_tamper_fail_closed(self):
        (self.folder / 'logs' / 'CASE_1.log').write_text('modified')
        with self.assertRaisesRegex(ValueError, 'changed log SHA256'):
            self.check_group()

    def test_consumer_csv_tamper_fail_closed(self):
        (self.folder / 'consumer_validation.csv').write_text('corrupted')
        with self.assertRaisesRegex(ValueError, 'consumer CSV SHA256 mismatch'):
            self.check_group()

    def test_strict_v3a_v3b_outcome_mismatch(self):
        self.expected['CASE_4']['outcome'] = 'WRONG_LABEL'
        with self.assertRaisesRegex(ValueError, 'V3a/V3b outcome mismatch'):
            self.check_group()

    def test_duplicate_manifest_record_rejected(self):
        self.meta['records'][1]['scenario_id'] = 'CASE_1'
        self.write_meta()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.check_group()

class V3aAllCandidateSelectionTests(unittest.TestCase):
    """Regression: V3b MEM1 helper returns two target-local rows, NOT V3a's 28."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.results = Path(self.tmp.name) / 'results'
        self.v3a = self.results / v4.V3A_NAME
        self.v3a.mkdir(parents=True)
        self.targets = [tid for group in v4.GROUPS for tid in group.targets]
        self.plan = [{'target_id': tid, 'target_class': tid.split('_')[0],
                      'evidence_status': 'CANDIDATE'} for tid in self.targets]
        self.selected = [
            dict(zip(FIELDS, (f'CASE_{idx:02d}_{config}', 'IMG0000', tid,
                              tid.split('_')[0], '0', str(900 + 2 * idx + config))))
            for idx, tid in enumerate(self.targets) for config in (0, 1)
        ]
        self.manifest = self.selected.copy()
        self.replayed = {
            spec['scenario_id']: {
                **spec, 'injection_verified': '1', 'temporal_window_verified': '1',
                'value_aware_consumer_proof': 'NOT_PROVED', 'checkpoint_numeric_delta': '0',
                'final_output_or_protocol_effect': '0', 'pool1_mismatch': '0',
                'pool2_mismatch': '0', 'logits_mismatch': '0', 'first_bad_stage': 'NONE',
                'outcome': 'FINAL_OUTPUT_MATCH', 'log_sha256': 'f'*64,
            } for spec in self.selected
        }
        write_csv(self.v3a / 'selected_scenarios.csv', FIELDS, self.selected)
        write_csv(self.v3a / 'target_validation.csv', v4.REPORT_FIELDS,
                  list(self.replayed.values()))
        write_csv(self.v3a / 'target_summary.csv', v4.TARGET_FIELDS,
                  v4.summarize_targets(self.plan, list(self.replayed.values()), 1))

    def audit(self):
        # Deliberately keep the production MEM1 helper's 2-row return contract.
        mem1_pair = [r for r in self.selected if r['target_id'] == 'MEM_0003']
        self.assertEqual(len(mem1_pair), 2)
        with patch.object(v4, 'validate_v3a', return_value=(
                {'selected_count': 28}, mem1_pair, self.v3a / 'source_vlog.prj')):
            with patch.object(v4, 'inspect_applied_and_result',
                              side_effect=lambda log, spec: self.replayed[spec['scenario_id']]):
                return v4.check_v3a(self.repo, self.results, self.plan, self.manifest)

    def test_v3a_all_28_even_though_mem1_helper_returns_only_two(self):
        meta, observed = self.audit()
        self.assertEqual(meta['selected_count'], 28)
        self.assertEqual(len(observed), 28)
        self.assertEqual({r['target_id'] for r in observed.values()}, set(self.targets))

    def test_selected_27_rows_rejected(self):
        write_csv(self.v3a / 'selected_scenarios.csv', FIELDS, self.selected[:-1])
        with self.assertRaisesRegex(ValueError, 'expected 28 rows, got 27'):
            self.audit()

    def test_wrong_frozen_scenario_rejected(self):
        altered = [r.copy() for r in self.selected]
        altered[0]['injection_offset'] = '9999'
        write_csv(self.v3a / 'selected_scenarios.csv', FIELDS, altered)
        with self.assertRaisesRegex(ValueError, 'differs from frozen manifest'):
            self.audit()

    def test_missing_target_pair_rejected(self):
        altered = [r.copy() for r in self.selected]
        altered[0]['target_id'] = altered[2]['target_id']
        # Keep manifest and replay mapping consistent to isolate the two-per-target gate.
        self.manifest = altered.copy()
        write_csv(self.v3a / 'selected_scenarios.csv', FIELDS, altered)
        with self.assertRaisesRegex(ValueError, 'expected both frozen configurations'):
            self.audit()

    def test_v3a_non_candidate_row_rejected(self):
        altered = [r.copy() for r in self.selected]
        altered[0]['target_id'] = 'CTL_0001'
        self.manifest = altered.copy()
        write_csv(self.v3a / 'selected_scenarios.csv', FIELDS, altered)
        with self.assertRaisesRegex(ValueError, 'not a candidate/IMG0000'):
            self.audit()

    def test_v3a_wrong_image_rejected(self):
        altered = [r.copy() for r in self.selected]
        altered[0]['sample_id'] = 'IMG0001'
        self.manifest = altered.copy()
        write_csv(self.v3a / 'selected_scenarios.csv', FIELDS, altered)
        with self.assertRaisesRegex(ValueError, 'not a candidate/IMG0000'):
            self.audit()


if __name__ == '__main__':
    unittest.main()

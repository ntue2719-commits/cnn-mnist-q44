#!/usr/bin/env python3
"""Offline contract/negative tests for six-target V4b directed FI review."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_six_v4b as v4b
from pilot400_common import load_plan, verify_manifest, sha256

REPO = Path(__file__).resolve().parents[3]
PLAN_PATH = REPO / 'fi/config/pilot400_targets.csv'
CATALOG = REPO / 'fi/config/fi_targets_candidates_v2.csv'
MANIFEST_PATH = REPO / 'fi/scenarios/pilot400.csv'


def fixture_plan():
    return load_plan(PLAN_PATH, CATALOG)


def fixture_rows(plan):
    return verify_manifest(MANIFEST_PATH, plan)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.plan = fixture_plan()
        self.manifest = fixture_rows(self.plan)

    def test_exactly_six_targets(self):
        selected = v4b.six_targets(self.plan)
        self.assertEqual({r['target_id'] for r in selected}, set(v4b.PRE_EXISTING))
        self.assertEqual(len(selected), 6)

    def test_frozen_statuses(self):
        actual = {r['target_id']: r['evidence_status'] for r in v4b.six_targets(self.plan)}
        self.assertEqual(actual, v4b.PRE_EXISTING)

    def test_12_exact_scenarios(self):
        selected = v4b.six_scenarios(self.manifest, self.plan)
        self.assertEqual([r['scenario_id'] for r in selected], [
            'FI_P400_0001', 'FI_P400_0002',
            'FI_P400_0141', 'FI_P400_0142',
            'FI_P400_0161', 'FI_P400_0162',
            'FI_P400_0201', 'FI_P400_0202',
            'FI_P400_0221', 'FI_P400_0222',
            'FI_P400_0381', 'FI_P400_0382',
        ])

    def test_all_img0000(self):
        self.assertEqual({r['sample_id'] for r in v4b.six_scenarios(self.manifest, self.plan)},
                         {'IMG0000'})

    def test_dat0010_bitpair_same_edge(self):
        rows = [r for r in v4b.six_scenarios(self.manifest, self.plan)
                if r['target_id'] == 'DAT_0010']
        self.assertEqual({(r['bit_index'], r['injection_offset']) for r in rows},
                         {('0', '1340'), ('1', '1340')})

    def test_status_mutation_rejected(self):
        p = copy.deepcopy(self.plan)
        next(r for r in p if r['target_id'] == 'BUF_0031')['evidence_status'] = 'CANDIDATE'
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            v4b.six_targets(p)

    def test_missing_selected_target_rejected(self):
        p = [r for r in self.plan if r['target_id'] != 'MEM_0001']
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            v4b.six_targets(p)

    def test_missing_second_configuration_rejected(self):
        rows = [r for r in self.manifest if r['scenario_id'] != 'FI_P400_0222']
        with self.assertRaisesRegex(ValueError, 'missing/altered paired'):
            v4b.six_scenarios(rows, self.plan)

    def test_bad_dat_bit_rejected(self):
        rows = copy.deepcopy(self.manifest)
        next(r for r in rows if r['scenario_id'] == 'FI_P400_0382')['bit_index'] = '0'
        with self.assertRaises(ValueError):
            v4b.six_scenarios(rows, self.plan)

    def test_bad_dat_offset_rejected(self):
        rows = copy.deepcopy(self.manifest)
        next(r for r in rows if r['scenario_id'] == 'FI_P400_0382')['injection_offset'] = '1341'
        with self.assertRaises(ValueError):
            v4b.six_scenarios(rows, self.plan)

    def test_duplicate_manifest_scenario_rejected(self):
        rows = copy.deepcopy(self.manifest)
        bad = next(r for r in rows if r['scenario_id'] == 'FI_P400_0382')
        bad['scenario_id'] = 'FI_P400_0381'
        with self.assertRaisesRegex(ValueError, 'Duplicate scenario ID'):
            v4b.six_scenarios(rows, self.plan)

    def test_no_automatic_promotion_in_report(self):
        report = v4b.evidence_report(v4b.six_scenarios(self.manifest, self.plan),
                                     [], False, 'DRY_RUN_ONLY')
        self.assertIn('NOT automatic target promotion', report)
        self.assertIn('Full400 remains BLOCKED', report)
        self.assertIn('DAT_0010', report)


class V4AncestryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.audit = self.folder / 'v4_audit.json'
        self.original = {
            'schema': 'CNN_PEP_V4_CROSS_EVIDENCE_V1',
            'status': 'CROSS_EVIDENCE_PASS_FULL400_BLOCKED',
            'v3a_manifest_sha256': 'a'*64,
            'control10_manifest_sha256': 'b'*64,
            'timing_audit_sha256': 'c'*64,
            'selected_plan_sha256': 'd'*64,
            'ctl_inventory': {'status': 'PASS', 'file_count': 97},
            'v3b_group_counts': {'MEM2': 4},
            'v3a_scenarios_replayed': 28,
            'v3b_scenarios_replayed': 28,
            'review_ready_candidates': 14,
            'targets_requiring_further_qualification': 6,
            'source_authority': 'vivado',
            'full400_authorized': False,
            'target_status_modified': False,
            'targets': [],
        }
        self.write()

    def write(self):
        self.audit.write_text(json.dumps(self.original), encoding='utf-8')

    def check(self, fresh=None):
        with patch.object(v4b, 'run_audit', return_value=fresh or self.original):
            return v4b.verify_v4_ancestry(REPO, self.folder, REPO / 'data', self.audit)

    def test_pass_and_digest(self):
        metadata, digest = self.check()
        self.assertEqual(metadata['status'], 'CROSS_EVIDENCE_PASS_FULL400_BLOCKED')
        self.assertEqual(digest, sha256(self.audit))

    def test_saved_status_mismatch_rejected(self):
        self.original['status'] = 'FULL400_PASS'
        self.write()
        with self.assertRaisesRegex(ValueError, 'differs from replay'):
            self.check(fresh={**self.original, 'status': 'CROSS_EVIDENCE_PASS_FULL400_BLOCKED'})

    def test_modified_control10_digest_rejected(self):
        self.original['control10_manifest_sha256'] = 'e'*64
        self.write()
        with self.assertRaisesRegex(ValueError, 'control10_manifest_sha256'):
            self.check(fresh={**self.original, 'control10_manifest_sha256': 'b'*64})

    def test_replay_claims_full400_allowed_rejected(self):
        self.original['full400_authorized'] = True
        self.write()
        with self.assertRaisesRegex(ValueError, 'qualification baseline'):
            self.check()

    def test_partial_target_proofs_rejected(self):
        self.original['review_ready_candidates'] = 13
        self.write()
        with self.assertRaisesRegex(ValueError, 'qualification baseline'):
            self.check()

    def test_missing_audit_rejected(self):
        self.audit.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing JSON'):
            self.check()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.results = self.folder / 'results'
        self.results.mkdir()
        v3a_folder = self.results / v4b.V3A_NAME
        v3a_folder.mkdir()
        (v3a_folder / 'validation_manifest.json').write_text(json.dumps({
            'snapshot_build': {'source_hashes': {'C:/sim/source.v': 'a'*64}}
        }), encoding='utf-8')
        self.marker = self.folder / 'audit.json'
        self.marker.write_text('{}', encoding='utf-8')
        self.project = self.folder / 'Simulation.xpr'
        self.project.write_text('mock project', encoding='utf-8')
        self.out = self.folder / 'v4b_output'
        self.args = argparse.Namespace(
            repo=REPO, results_dir=self.results, weights_dir=Path('data'),
            v4_audit=self.marker, out_dir=self.out, project=self.project,
            vivado='vivado', fileset='sim_1', timeout_seconds=900, execute=False,
        )
        self.ancestry = {
            'v3a_manifest_sha256': 'a'*64,
            'timing_audit_sha256': 'b'*64,
            'control10_manifest_sha256': 'c'*64,
        }
        self.patch_ancestry = patch.object(v4b, 'verify_v4_ancestry',
                                           return_value=(self.ancestry, 'd'*64))
        self.patch_ancestry.start()
        self.addCleanup(self.patch_ancestry.stop)

    def test_dry_run_no_xsim(self):
        with patch.object(v4b, 'build_verified_snapshot', side_effect=AssertionError('no compile')):
            result = v4b.run(self.args)
        self.assertEqual(result['selected_count'], 12)
        self.assertEqual(result['status'], 'DRY_RUN_ONLY')
        self.assertFalse(result['full400_authorized'])
        self.assertTrue((self.out / 'selected_scenarios.csv').is_file())
        self.assertTrue((self.out / 'v4b_report.md').is_file())

    def test_refuses_existing_out_dir(self):
        self.out.mkdir()
        with self.assertRaisesRegex(ValueError, 'Refusing to overwrite'):
            v4b.run(self.args)

    def test_execute_requires_project(self):
        self.args.execute = True
        self.project.unlink()
        with self.assertRaisesRegex(ValueError, 'valid --project'):
            v4b.run(self.args)

    def test_small_timeout_fails_before_any_write(self):
        self.args.timeout_seconds = 2
        with self.assertRaisesRegex(ValueError, 'Timeout'):
            v4b.run(self.args)
        self.assertFalse(self.out.exists())

    def make_snap(self, change=False):
        simdir = self.folder / 'sim'
        simdir.mkdir(exist_ok=True)
        return {
            'source_hashes': {'C:/sim/source.v': ('f'*64 if change else 'a'*64)},
            'snapshot': simdir / 'xsim.dir' / 'tb_cnn_mnist_fi_pilot400_behav',
            'simdir': simdir, 'vivado_root': self.folder,
            'elaboration_mode': 'FULL_BUILD', 'rtl_count': 27,
            'compile_prj_sha256': '1'*64,
        }

    def test_execute_rejects_changed_vivado_source(self):
        self.args.execute = True
        with patch.object(v4b, 'build_verified_snapshot', return_value=self.make_snap(True)):
            with self.assertRaisesRegex(ValueError, 'differ from V3a'):
                v4b.run(self.args)
        m = json.loads((self.out / 'v4b_manifest.json').read_text())
        self.assertEqual(m['status'], 'INTERRUPTED_OR_INVALID')
        self.assertFalse((self.out / 'logs').exists())

    def test_execute_12_mock_verified_and_still_blocked(self):
        self.args.execute = True
        fake_source = self.folder / 'hdl.v'
        fake_source.write_text('RTL')
        h = sha256(fake_source)
        prior = self.results / v4b.V3A_NAME / 'validation_manifest.json'
        prior.write_text(json.dumps({'snapshot_build': {
            'source_hashes': {str(fake_source): h}
        }}))
        snap = self.make_snap()
        snap['source_hashes'] = {str(fake_source): h}
        def make_logs(cmd, cwd, out, timeout, stage):
            sid = stage.split()[-1]
            (self.out / 'logs' / (sid + '.log')).write_text('fake FI log '+sid)
            (self.out / (sid + '.console.log')).write_text('test console')
        def result(log, row):
            return {
                **row, 'injection_verified': '1', 'temporal_window_verified': '1',
                'value_aware_consumer_proof': 'NOT_PROVED',
                'checkpoint_numeric_delta': '0',
                'final_output_or_protocol_effect': '0',
                'pool1_mismatch': '0', 'pool2_mismatch': '0',
                'logits_mismatch': '0', 'first_bad_stage': 'NONE',
                'outcome': 'FINAL_OUTPUT_MATCH', 'log_sha256': sha256(log),
            }
        with patch.object(v4b, 'build_verified_snapshot', return_value=snap), \
             patch.object(v4b, 'make_cmd_run', return_value='@echo off\n'), \
             patch.object(v4b, 'call_cmd', side_effect=make_logs), \
             patch.object(v4b, 'inspect_applied_and_result', side_effect=result):
            data = v4b.run(self.args)
        self.assertEqual(data['status'], v4b.STATUS_DONE)
        self.assertEqual(len(data['records']), 12)
        self.assertFalse(data['consumer_qualified'])
        self.assertFalse(data['full400_authorized'])
        self.assertEqual(len((self.out / 'mechanism_validation.csv').read_text().splitlines()), 13)

    def test_execute_preserves_failed_partial_logs(self):
        self.args.execute = True
        src = self.folder / 'valid.v'
        src.write_text('valid RTL')
        source_hash = {str(src): sha256(src)}
        prior = self.results / v4b.V3A_NAME / 'validation_manifest.json'
        prior.write_text(json.dumps({'snapshot_build': {'source_hashes': source_hash}}))
        snap = self.make_snap()
        snap['source_hashes'] = source_hash
        count = 0
        def failure(cmd, cwd, wrapper, timeout, stage):
            nonlocal count
            count += 1
            sid = stage.split()[-1]
            if count == 2:
                raise ValueError('injected second-xsim failure')
            (self.out / 'logs' / (sid + '.log')).write_text('first scenario retained')
            (self.out / (sid + '.console.log')).write_text('console')
        def good_result(log, row):
            return {
                **row, 'injection_verified': '1', 'temporal_window_verified': '1',
                'value_aware_consumer_proof': 'NOT_PROVED',
                'checkpoint_numeric_delta': '0', 'final_output_or_protocol_effect': '0',
                'pool1_mismatch': '0', 'pool2_mismatch': '0', 'logits_mismatch': '0',
                'first_bad_stage': 'NONE', 'outcome': 'FINAL_OUTPUT_MATCH',
                'log_sha256': sha256(log),
            }
        with patch.object(v4b, 'build_verified_snapshot', return_value=snap), \
             patch.object(v4b, 'make_cmd_run', return_value='@echo off\n'), \
             patch.object(v4b, 'call_cmd', side_effect=failure), \
             patch.object(v4b, 'inspect_applied_and_result', side_effect=good_result):
            with self.assertRaisesRegex(ValueError, 'second-xsim failure'):
                v4b.run(self.args)
        self.assertEqual(count, 2)
        meta = json.loads((self.out / 'v4b_manifest.json').read_text())
        self.assertEqual(meta['status'], 'INTERRUPTED_OR_INVALID')
        self.assertEqual(len(meta['records']), 1)
        self.assertTrue((self.out / 'logs' / 'FI_P400_0001.log').exists())
        self.assertTrue((self.out / 'selected_scenarios.csv').exists())


if __name__ == '__main__':
    unittest.main()

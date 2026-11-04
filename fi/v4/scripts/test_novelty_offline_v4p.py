"""Offline V4P tests. No Vivado/XSim invocations, no write into frozen repo."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name('novelty_offline_v4p.py')
spec = importlib.util.spec_from_file_location('novelty_offline_v4p', MODULE)
v4p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4p)


class V4PTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The source checkout can be supplied through environment for an
        # ordinary standalone patch runner on Windows. Unit tests can run
        # without the original 40 raw logs; evidence-dependent tests are
        # separately enabled via V4P_TEST_REPO.
        import os
        path = os.environ.get('V4P_TEST_REPO')
        cls.repo = Path(path).resolve() if path else None

    def test_non_fi_source_properties(self):
        code = MODULE.read_text(encoding='utf-8')
        self.assertNotIn('subprocess.', code)
        self.assertNotIn('os.system(', code)
        self.assertNotIn('launch_simulation', code)
        self.assertIn('FULL400 BLOCKED', code)

    def test_three_valued_l2(self):
        self.assertEqual(v4p.combined_level(False, [(0,0,169),(0,0,25)]), 'INCONCLUSIVE')
        self.assertEqual(v4p.combined_level(False, [(169,0,169),(25,0,25)]), 'FALSE')
        self.assertEqual(v4p.combined_level(False, [(169,1,169),(25,0,25)]), 'TRUE')
        self.assertEqual(v4p.combined_level(True, [(0,0,169),(0,0,25)]), 'TRUE')
        self.assertEqual(v4p.stage_level(False, 0, 0, 169), 'INCONCLUSIVE')

    def test_harmful_and_logits_different(self):
        self.assertFalse(v4p.l0('WRONG_LOGITS_SAME_LABEL'))
        self.assertTrue(v4p.l1('WRONG_LOGITS_SAME_LABEL'))
        self.assertFalse(v4p.l1('FINAL_OUTPUT_MATCH'))
        self.assertTrue(v4p.l0('WRONG_LABEL'))

    def test_counter_which_is_partial_cannot_be_negative(self):
        p = {'outcome':'FINAL_OUTPUT_MATCH','first_bad_stage':'NONE',
             'pool1words':168,'pool2words':25,'pool1_mismatch':0,'pool2_mismatch':0,
             'logits_mismatch':0}
        ev = {'scenario_id':'FI_P400_0101','sample_id':'IMG0000',
              'target_id':'BUF_0007','target_class':'BUF','bit_index':'0',
              'injection_offset':'791','proof_source':'V3B_ISOLATED',
              'consumer_proof':'TEST_WITNESS'}
        with self.assertRaisesRegex(ValueError, 'L2 missing checkpoint evidence'):
            v4p.classify(p, ev, '0'*64)

    def test_canonical_bytes_lf(self):
        self.assertEqual(v4p.canonical_json({'x':'y'}), b'{\n  "x": "y"\n}\n')
        self.assertNotIn(b'\r', v4p.canonical_json({'value':2}))

    def test_source_hash_mutation_fails_closed(self):
        if self.repo is None:
            self.skipTest('Set V4P_TEST_REPO for historical evidence tests')
        data = (self.repo / 'fi/config/fi_targets_candidates_v2.csv').read_bytes()
        self.assertNotEqual(v4p.sha_bytes(data + b'X'), v4p.EXPECTED_FROZEN['fi/config/fi_targets_candidates_v2.csv'])

    def test_verified_original_historical_dataset(self):
        if self.repo is None:
            self.skipTest('Set V4P_TEST_REPO for historical evidence tests')
        rows, report = v4p.calculate(self.repo)
        self.assertEqual(len(rows), 40)
        self.assertEqual(report['observability']['L0']['TRUE'], 9)
        self.assertEqual(report['observability']['L1']['TRUE'], 20)
        self.assertEqual(report['observability']['L2']['TRUE'], 24)
        self.assertEqual(report['observability']['L1_P1']['TRUE'], 24)
        self.assertEqual(report['observability']['L1_P2']['TRUE'], 20)
        self.assertEqual(report['observability']['L2']['INCONCLUSIVE'] if 'INCONCLUSIVE' in report['observability']['L2'] else 0, 0)
        self.assertEqual(report['counterfactual']['pool1_golden_matched_bytes'], 676)
        self.assertEqual([x['conv2_raw_changed_values'] for x in report['counterfactual']['scenarios']], [8,8,23,67])
        self.assertEqual([x['conv2_post_relu_changed_values'] for x in report['counterfactual']['scenarios']], [1,1,0,3])
        self.assertEqual([x['pool2_changed_values'] for x in report['counterfactual']['scenarios']], [0,0,0,0])
        self.assertEqual(report['xsim_new_runs'], 0)
        self.assertFalse(report['full400_authorized'])

    def test_mutated_raw_e1_marker_rejected(self):
        if self.repo is None:
            self.skipTest('Set V4P_TEST_REPO for historical evidence tests')
        frozen, _ = v4p.manifest_rows(self.repo)
        ev, _ = v4p.matrix_rows(self.repo, frozen)
        target = next(r for r in ev if r['scenario_id'] == 'FI_P400_0101')
        path = self.repo / 'fi/results/pilot400_v3b_buf0007_01/logs/FI_P400_0101.log'
        blob = path.read_bytes()
        self.assertEqual(v4p.sha_bytes(blob), target['log_sha256'])
        v4p.parse_log(blob, target)
        with self.assertRaisesRegex(ValueError, 'Not one XOR'):
            v4p.parse_log(blob.replace(b'bit=0 old=0 new=1 requested=794',
                                       b'bit=0 old=0 new=0 requested=794'), target)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            v4p.parse_log(blob + b'\nFI_APPLIED scenario=FI_P400_0101 target=BUF_0007 class=BUF bit=0 old=0 new=1 requested=794\n', target)
        with self.assertRaisesRegex(ValueError, 'Malformed FI_RESULT_V2'):
            v4p.parse_log(blob.replace(b'FI_RESULT_V2,', b'FI_RESULT_V2,BAD,'), target)

    def test_fresh_output_and_replay_tamper(self):
        if self.repo is None:
            self.skipTest('Set V4P_TEST_REPO for historical evidence tests')
        rows, report = v4p.calculate(self.repo)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'fresh_result'
            v4p.write_new_output(output, rows, report)
            v4p.audit_existing(output, rows, report)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                v4p.write_new_output(output, rows, report)
            file = output / 'V4P_SCENARIO_EVIDENCE.csv'
            file.write_bytes(file.read_bytes() + b'forged,new,result\n')
            with self.assertRaisesRegex(ValueError, 'Independent regeneration'):
                v4p.audit_existing(output, rows, report)


if __name__ == '__main__':
    unittest.main()

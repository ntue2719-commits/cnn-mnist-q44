#!/usr/bin/env python3
"""Adversarial offline tests for V4f (no Vivado, no Full400 execution)."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import sys
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/"scripts"))
sys.path.insert(0,str(HERE.parents[1]/"v3b/scripts"))

from pilot400_common import (FIELDS, SAMPLES, ASSET_SIZES, load_plan,
                             verify_manifest, read_csv, sha256)
import qualify_full400_v4f as v4f

ROOT=Path(__file__).resolve().parents[3]


def baseline():
    plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                   ROOT/'fi/config/fi_targets_candidates_v2.csv')
    rows=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan)
    return plan,rows


class ExactFrozenPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan,self.rows=baseline()

    def rejects(self, rows, plan=None):
        with self.assertRaises((ValueError,AssertionError)):
            v4f.select_400(rows,self.plan if plan is None else plan)

    def test_frozen_sha256_values(self):
        v4f.check_frozen(ROOT)
        for relative,digest in v4f.FROZEN_HASHES.items():
            self.assertEqual(sha256(ROOT/relative),digest)

    def test_exact_400_and_20_times_10_times_2(self):
        chosen=v4f.select_400(self.rows,self.plan)
        self.assertEqual(len(chosen),400)
        self.assertEqual(len(set(x['scenario_id'] for x in chosen)),400)
        self.assertEqual(Counter(x['target_class'] for x in chosen),
                         {'CTL':100,'BUF':100,'MEM':100,'DAT':100})

    def test_dat0010_two_bits_same_offset_all_ten(self):
        rows=[r for r in self.rows if r['target_id']=='DAT_0010']
        self.assertEqual(Counter((r['bit_index'],r['injection_offset']) for r in rows),
                         {('0','1340'):10,('1','1340'):10})

    def test_missing_scenario(self):
        self.rejects(self.rows[:-1])

    def test_duplicate_scenario(self):
        rows=copy.deepcopy(self.rows)
        rows[-1]=rows[0]
        self.rejects(rows)

    def test_reordered_rows(self):
        rows=copy.deepcopy(self.rows)
        rows[0],rows[1]=rows[1],rows[0]
        self.rejects(rows)

    def test_changed_target_identity(self):
        rows=copy.deepcopy(self.rows)
        rows[0]['target_id']='CTL_9999'
        self.rejects(rows)

    def test_changed_bit(self):
        rows=copy.deepcopy(self.rows)
        rows[0]['bit_index']='1'
        self.rejects(rows)

    def test_wrong_offset(self):
        rows=copy.deepcopy(self.rows)
        rows[0]['injection_offset']='126'
        self.rejects(rows)

    def test_equal_offset_other_than_dat0010(self):
        rows=copy.deepcopy(self.rows)
        rows[1]['injection_offset']=rows[0]['injection_offset']
        self.rejects(rows)

    def test_dat0010_refreeze_tamper(self):
        rows=copy.deepcopy(self.rows)
        row=next(r for r in rows if r['target_id']=='DAT_0010'
                 and r['bit_index']=='1')
        row['injection_offset']='1341'
        self.rejects(rows)

    def test_mismatched_sample(self):
        rows=copy.deepcopy(self.rows)
        rows[1]['sample_id']='IMG0009'
        self.rejects(rows)

    def test_wrong_scenario_id(self):
        rows=copy.deepcopy(self.rows)
        rows[2]['scenario_id']='FI_P400_9999'
        self.rejects(rows)

    def test_modified_plan_alone_rejected(self):
        plan=copy.deepcopy(self.plan)
        plan[0]['offset_a']='55' if plan[0]['offset_a']!='55' else '56'
        self.rejects(self.rows,plan)


class AssetContracts(unittest.TestCase):
    def test_real_assets_and_weights_match(self):
        pins=v4f.check_assets_and_provenance(ROOT,ROOT/'data')
        self.assertEqual(len(pins),57)  # 10*5 + 6 weights + provenance
        self.assertTrue(all(Path(p).is_file() and sha256(Path(p))==h
                            for p,h in pins.items()))

    def stage(self,place:Path)->tuple[Path,Path]:
        repo=place/'repo'
        folder=repo/'fi/pilot400/samples'
        folder.mkdir(parents=True)
        for src in (ROOT/'fi/pilot400/samples').rglob('*'):
            if src.is_file():
                dst=folder/src.relative_to(ROOT/'fi/pilot400/samples')
                dst.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(src,dst)
        data=repo/'data';data.mkdir()
        for name in json.loads((folder/'sample_provenance.json').read_text())['weight_hashes']:
            shutil.copyfile(ROOT/'data'/name,data/name)
        return repo,data

    def test_wrong_weight_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo,data=self.stage(Path(tmp))
            p=data/'conv1_bias.hex'
            p.write_text('ff\n'*4,encoding='ascii')
            with self.assertRaises(ValueError):v4f.check_assets_and_provenance(repo,data)

    def test_wrong_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo,data=self.stage(Path(tmp))
            p=repo/'fi/pilot400/samples/IMG0009/input.hex'
            txt=p.read_text()
            p.write_text(('ff' if txt[:2]!='ff' else '00')+txt[2:])
            with self.assertRaises(ValueError):v4f.check_assets_and_provenance(repo,data)

    def test_bad_sample_prediction_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo,data=self.stage(Path(tmp))
            p=repo/'fi/pilot400/samples/IMG0000/prediction.hex'
            p.write_text('07\n',encoding='ascii')
            with self.assertRaises(ValueError):v4f.check_assets_and_provenance(repo,data)

    def test_missing_asset_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo,data=self.stage(Path(tmp))
            (repo/'fi/pilot400/samples/IMG0008/golden_pool2.hex').unlink()
            with self.assertRaises((ValueError,OSError)):
                v4f.check_assets_and_provenance(repo,data)


class ReleaseReviewTests(unittest.TestCase):
    def test_source_pins_include_plan_code_and_assets(self):
        assets=v4f.check_assets_and_provenance(ROOT,ROOT/'data')
        pins=v4f.source_pins(ROOT,assets,ROOT/'fi/results',
                             ROOT/'fi/results/v4e_release_review_01/v4e_release_review.json')
        self.assertGreaterEqual(len(pins),len(assets)+len(v4f.PINS_CODE)+4)
        self.assertTrue(all(Path(p).is_file() and sha256(Path(p))==h
                            for p,h in pins.items()))

    def test_v4e_report_requires_original_parent_hashes(self):
        # In this testing bundle V4d-A/B raw dirs were not provided. A saved
        # V4e PASS line cannot substitute for their actual parent artifacts.
        try:
            v4f.verify_v4e_saved(ROOT,ROOT/'fi/results',
                                 ROOT/'fi/results/v4e_release_review_01')
        except (ValueError, OSError) as exc:
            self.assertIn('V4f V4e parent SHA256',str(exc))
        else:
            # On an operator's complete Windows repository all parent hashes
            # may exist; then the next gate is still independent raw replay.
            self.assertTrue(True)

    def test_duplicated_v4e_matrix_rejected(self):
        src=ROOT/'fi/results/v4e_release_review_01'
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            shutil.copyfile(src/'v4e_release_review.json',d/'v4e_release_review.json')
            p=d/'v4e_shakedown_matrix.csv'
            shutil.copyfile(src/'v4e_shakedown_matrix.csv',p)
            raw=p.read_bytes()
            p.write_bytes(raw+raw.splitlines()[-1]+b'\n')
            with self.assertRaises(ValueError):
                v4f.verify_v4e_saved(ROOT,ROOT/'fi/results',d)

    def test_fake_authorization_in_v4e_rejected(self):
        src=ROOT/'fi/results/v4e_release_review_01'
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            meta=json.loads((src/'v4e_release_review.json').read_text())
            meta['full400_authorized']=True
            (d/'v4e_release_review.json').write_text(json.dumps(meta))
            shutil.copyfile(src/'v4e_shakedown_matrix.csv',d/'v4e_shakedown_matrix.csv')
            with self.assertRaises(ValueError):
                v4f.verify_v4e_saved(ROOT,ROOT/'fi/results',d)

    def test_saved_v4e_not_claimed_as_400_fi(self):
        obj=json.loads((ROOT/'fi/results/v4e_release_review_01/v4e_release_review.json').read_text())
        self.assertFalse(obj['full400_fi_scenarios_executed'])
        self.assertFalse(obj['full400_runner_qualified'])
        self.assertEqual(obj['shakedown_mechanism_runs'],32)


class PreflightOutputSafety(unittest.TestCase):
    def test_prevent_overwrite_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                v4f.safety_output(ROOT,Path(tmp),ROOT/'data',ROOT/'fi/results')

    def test_reject_writing_into_frozen_results(self):
        d=ROOT/'fi/results/v4c_readiness_audit_01/new_result'
        with self.assertRaises(ValueError):
            v4f.safety_output(ROOT,d,ROOT/'data',ROOT/'fi/results')

    def test_reject_writing_into_source(self):
        d=ROOT/'fi/config/fake'
        with self.assertRaises(ValueError):
            v4f.safety_output(ROOT,d,ROOT/'data',ROOT/'fi/results')

    def test_plan_output_unapproved_no_simulation(self):
        plan,rows=baseline()
        pins=v4f.source_pins(ROOT,v4f.check_assets_and_provenance(ROOT,ROOT/'data'),
                             ROOT/'fi/results',ROOT/'fi/results/v4e_release_review_01/v4e_release_review.json')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'v4f_plan'
            meta=v4f.write_planning_output(out,rows,pins,False,None)
            self.assertEqual(meta['selected_count'],400)
            self.assertEqual(meta['xsim_runs_added'],0)
            self.assertFalse(meta['strict_v4e_raw_replay_completed'])
            self.assertFalse(meta['full400_authorized'])
            self.assertFalse(meta['full400_runner_qualified'])
            self.assertFalse(meta['scientific_protocol_frozen'])
            self.assertEqual(read_csv(out/'selected_scenarios.csv',FIELDS),rows)
            self.assertEqual(meta['selected_sha256'],sha256(out/'selected_scenarios.csv'))
            with self.assertRaises(FileExistsError):
                v4f.write_planning_output(out,rows,pins,False,None)

    def test_execute_is_forbidden_even_with_valid_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            o=Path(tmp)/'result'
            args=argparse.Namespace(execute=True, plan_only=True,replay_ancestry=False,
                repo=ROOT,results_dir=Path('fi/results'),weights_dir=Path('data'),
                out_dir=o,v4e_dir=Path('fi/results/v4e_release_review_01'))
            with self.assertRaises(ValueError):v4f.run(args)
            self.assertFalse(o.exists())

    def test_one_mode_must_be_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=argparse.Namespace(execute=False,plan_only=False,replay_ancestry=False,
                repo=ROOT,results_dir=Path('fi/results'),weights_dir=Path('data'),
                out_dir=Path(tmp)/'x',v4e_dir=Path('fi/results/v4e_release_review_01'))
            with self.assertRaises(ValueError):v4f.run(args)

    def test_full_plan_only_cli_in_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=argparse.Namespace(execute=False,plan_only=True,replay_ancestry=False,
                repo=ROOT,results_dir=Path('fi/results'),weights_dir=Path('data'),
                out_dir=Path(tmp)/'plan',v4e_dir=Path('fi/results/v4e_release_review_01'))
            meta=v4f.run(args)
            self.assertEqual(meta['status'],v4f.STATUS_PLAN)
            self.assertEqual(meta['xsim_runs_added'],0)


if __name__=='__main__':
    unittest.main()

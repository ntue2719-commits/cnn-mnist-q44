#!/usr/bin/env python3
"""Static/host-side tests, NOT an XSim/P0 pass claim."""
from __future__ import annotations
import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from pilot400_common import (FIELDS,load_plan,expected_rows,verify_manifest,
                             validate_assets,write_csv,read_csv)
from preflight_pilot400 import preflight
from run_pilot400 import make_tcl
from analyze_pilot400 import interpret,run_analysis
from profile_pilot400 import assess

REPO=Path(__file__).resolve().parents[2]


def make_result(**values):
    r=dict(scenario_id='FI_P400_0001',sample_id='IMG0000',target_id='CTL_0001',
           target_class='CTL',bit_index='2',injection_offset='125',
           outcome='FINAL_OUTPUT_MATCH',protocol_ok='1',done_count='1',
           pool1_mismatch='0',pool2_mismatch='0',logits_mismatch='0',first_bad_stage='NONE')
    r.update({k:str(v) for k,v in values.items()})
    return r


class Pilot400Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans=load_plan(REPO/'fi/config/pilot400_targets.csv',REPO/'fi/config/fi_targets_candidates_v2.csv')
        cls.rows=verify_manifest(REPO/'fi/scenarios/pilot400.csv',cls.plans)

    def test_exact_400_balanced(self):
        self.assertEqual(len(self.rows),400)
        self.assertEqual(len(set(r['scenario_id'] for r in self.rows)),400)
        self.assertEqual(len(set((r['sample_id'],r['target_id'],r['bit_index'],r['injection_offset']) for r in self.rows)),400)
        for cls in ('CTL','BUF','MEM','DAT'):
            self.assertEqual(sum(r['target_class']==cls for r in self.rows),100)
        self.assertEqual(len(set(r['sample_id'] for r in self.rows)),10)
        self.assertEqual(len(set(r['target_id'] for r in self.rows)),20)

    def test_manifest_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'malicious.csv'
            rows=[r.copy() for r in self.rows]
            rows[0]['injection_offset']='2999'
            write_csv(p,FIELDS,rows)
            with self.assertRaises(ValueError):verify_manifest(p,self.plans)

    def test_duplicate_target_plan_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad_plan.csv'
            rows=[r.copy() for r in self.plans]
            rows[1]['target_id']=rows[0]['target_id']
            write_csv(path,tuple(rows[0]),rows)
            with self.assertRaises(ValueError):load_plan(path,REPO/'fi/config/fi_targets_candidates_v2.csv')

    def test_seed_sample_is_valid_and_missing_are_obvious(self):
        # Must remain valid after the user has exported IMG0001..IMG0009.
        count,missing=validate_assets(REPO/'fi/pilot400/samples',require_all=False)
        self.assertGreaterEqual(count,1)
        self.assertLessEqual(count,10)
        self.assertNotIn('IMG0000',missing)
        self.assertEqual(count+len(missing),10)
        if missing:
            with self.assertRaises(ValueError):validate_assets(REPO/'fi/pilot400/samples',require_all=True)
        else:
            self.assertEqual(validate_assets(REPO/'fi/pilot400/samples',require_all=True)[0],10)

    def test_sample_baseline_int8_checkpoint_preflight(self):
        self.assertEqual(len(preflight(REPO,require_assets=False)),400)

    def test_tcl_top_and_sample_paths(self):
        r=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            tcl=make_tcl(Path(folder)/'Simulation.xpr',r,Path(folder)/'gate.done',
                         'sim_1',REPO/'fi/pilot400/samples/IMG0000')
            self.assertIn('set_property top tb_cnn_mnist_fi_pilot400',tcl)
            for part in ('FI_BIT=','INPUT_FILE=','POOL1_FILE=','POOL2_FILE=',
                         'LOGITS_FILE=','PREDICTION_FILE=','CNN_PEP_PILOT400_TCL_DONE','xsim.compile.incremental false'):
                self.assertIn(part,tcl)
            self.assertNotIn('PILOT_PROFILE_ONLY',tcl)
            prof=make_tcl(Path(folder)/'Simulation.xpr',r,Path(folder)/'gate.done',
                         'sim_1',REPO/'fi/pilot400/samples/IMG0000',profile=True)
            self.assertIn('PILOT_PROFILE_ONLY=1',prof)

    def test_noncompletion_not_mislabeled_correct_prediction(self):
        obs=interpret(make_result(outcome='NONCOMPLETION',protocol_ok='0',done_count='0'))
        self.assertEqual(obs['level0_prediction_changed'],'NA')
        self.assertEqual(obs['level1_output_or_protocol'],'1')

    def test_wrong_logits_not_wrong_label(self):
        obs=interpret(make_result(outcome='WRONG_LOGITS_SAME_LABEL',logits_mismatch=1))
        self.assertEqual(obs['level0_prediction_changed'],'0')
        self.assertEqual(obs['level1_output_or_protocol'],'1')
        self.assertEqual(obs['level2_additional_over_level1'],'0')

    def test_stage_checkpoint_extra_in_output_match(self):
        obs=interpret(make_result(pool2_mismatch=1))
        self.assertEqual(obs['level1_output_or_protocol'],'0')
        self.assertEqual(obs['level2_checkpoint_numeric_diff'],'1')
        self.assertEqual(obs['level2_additional_over_level1'],'1')

    def test_absent_timeline_is_unknown_not_false(self):
        obs=interpret(make_result(target_id='MEM_0002',target_class='MEM',bit_index=0))
        self.assertEqual(obs['arithmetic_input_accepted'],'UNKNOWN_NO_RAW_LOG')
        obs=interpret(make_result(target_id='BUF_0030',target_class='BUF',bit_index=0))
        self.assertEqual(obs['arithmetic_input_accepted'],'NOT_INSTRUMENTED')

    def test_timeline_proof_enforced_for_selected_target(self):
        r=make_result(target_id='MEM_0002',target_class='MEM',bit_index=0)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'case.log';p.write_text('FI_TIMELINE_MEM_PASS write=880 inject=881 read=1050\n')
            obs=interpret(r,p)
            self.assertEqual(obs['arithmetic_input_accepted'],'VERIFIED_INPUT_ACCEPTED')
            self.assertEqual(obs['level2_additional_over_level1'],'1')
            p.write_text('FI_APPLIED only; NO TIMELINE proof\n')
            with self.assertRaises(ValueError):interpret(r,p)

    def test_no_extra_rows_or_mismatched_config_accepted(self):
        r=make_result()
        obs=run_analysis([r],[{k:r[k] for k in FIELDS}])
        self.assertEqual(len(obs),1)
        with self.assertRaises(ValueError):run_analysis([r],[])
        wrong={k:r[k] for k in FIELDS};wrong['bit_index']='1'
        with self.assertRaises(ValueError):run_analysis([r],[wrong])

    def test_profile_window_flags_invalid_write_read_order(self):
        plan=[p for p in self.plans if p['target_id']=='MEM_0002']
        valid=[('WRITE','MEM_0002',880,877),('READ','MEM_0002',1050,1047)]
        output=assess(plan,valid)
        self.assertEqual(output[0]['status_a'],'WRITE_LT_INJECT_LT_READ')
        invalid=[('WRITE','MEM_0002',1050,1047),('READ','MEM_0002',1100,1097)]
        output=assess(plan,invalid)
        self.assertEqual(output[0]['status_a'],'FAIL_MEM_LIFETIME')

    def test_pilot_tb_preserves_static_timeline_checks(self):
        tb=(REPO/'fi/tb/tb_cnn_mnist_fi_pilot400.v').read_text()
        self.assertIn('module tb_cnn_mnist_fi_pilot400;',tb)
        self.assertIn('FI_TIMELINE_MEM_PASS',tb)
        self.assertIn('FI_TIMELINE_BUF_PASS',tb)
        self.assertIn('FI_PROFILE_EVENT',tb)
        self.assertIn('PILOT_PROFILE_ONLY',tb)
        self.assertIn('timeline_check();',tb)
        self.assertIn('FI_DISABLED_CONTROL_PASS',tb)

if __name__=='__main__':unittest.main(verbosity=2)

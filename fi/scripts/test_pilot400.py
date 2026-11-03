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
from run_pilot400 import make_tcl, validate_full_timing_gate
from analyze_pilot400 import interpret,run_analysis
from profile_pilot400 import assess, parse_log, timing_gate_pass, SCHEMA_MARKER
from run_pilot400_control10 import check_control_log, validate_control10_gate

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

    def test_dat0010_argmax_consumer_is_checked_pre_nba(self):
        # Static regression only. The targeted XSim replay is required for
        # runtime verification of the simulator's deposit/edge ordering.
        tb=(REPO/'fi/tb/tb_cnn_mnist_fi_pilot400.v').read_text()
        self.assertIn('argmax_input_pre_valid = i_rst_n && monitor_enable &&',tb)
        self.assertIn('dut.u_argmax.i_valid;',tb)
        self.assertIn('argmax_input_pre_logits = dut.u_argmax.i_logits;',tb)
        self.assertIn('if (argmax_input_pre_valid) begin',tb)
        self.assertIn('FI_DAT_ARGMAX_CONSUME',tb)
        self.assertIn('logits_bad_seen[j]',tb)

    def test_pilot_tb_preserves_static_timeline_checks(self):
        tb=(REPO/'fi/tb/tb_cnn_mnist_fi_pilot400.v').read_text()
        self.assertIn('module tb_cnn_mnist_fi_pilot400;',tb)
        self.assertIn('FI_TIMELINE_MEM_PASS',tb)
        self.assertIn('FI_TIMELINE_BUF_PASS',tb)
        self.assertIn('FI_PROFILE_EVENT',tb)
        self.assertIn('PILOT_PROFILE_ONLY',tb)
        self.assertIn('timeline_check();',tb)
        self.assertIn('FI_DISABLED_CONTROL_PASS',tb)



class ProfileV2Tests(unittest.TestCase):
    """Synthetic UNIT tests: none of these constitutes Vivado/XSim evidence."""
    @classmethod
    def setUpClass(cls):
        cls.plans=load_plan(REPO/'fi/config/pilot400_targets.csv',
                            REPO/'fi/config/fi_targets_candidates_v2.csv')

    @classmethod
    def target(cls, name):
        return [p for p in cls.plans if p['target_id']==name]

    @staticmethod
    def ev(kind, target, offset, start=3):
        return (kind,target,offset+start,offset)

    def test_ctl_both_active_edges(self):
        events=[self.ev('ACTIVE','CTL_0001',125),
                self.ev('ACTIVE','CTL_0001',150)]
        rows=assess(self.target('CTL_0001'),events)
        self.assertEqual(rows[0]['status_a'],'CTL_ACTIVE_AT_EDGE')
        self.assertEqual(rows[0]['status_b'],'CTL_ACTIVE_AT_EDGE')
        self.assertTrue(timing_gate_pass(rows))

    def test_ctl_missing_one_active_edge_must_fail(self):
        r=assess(self.target('CTL_0012'),[self.ev('ACTIVE','CTL_0012',850)])[0]
        self.assertEqual(r['status_a'],'CTL_ACTIVE_AT_EDGE')
        self.assertEqual(r['status_b'],'FAIL_CTL_INACTIVE')
        self.assertFalse(timing_gate_pass([r]))
        self.assertEqual(assess(self.target('CTL_0012'),[])[0]['status_a'],'NOT_PROFILED')

    def test_dat_producer_before_exact_consumer_edge(self):
        # DAT_0010: FC registers logits at offset1339; ArgMax consumes at
        # offset1340. The second configured offset1341 is NOT a handoff.
        events=[self.ev('PRODUCE','DAT_0010',1339),
                self.ev('CONSUME','DAT_0010',1340),
                self.ev('FC_VALID','DAT_0010',1340)]
        r=assess(self.target('DAT_0010'),events)[0]
        self.assertEqual(r['status_a'],'DAT_HANDOFF_AT_EDGE')
        self.assertEqual(r['status_b'],'FAIL_DAT_HANDOFF')
        self.assertFalse(timing_gate_pass([r]))

    def test_dat_producer_not_on_consumer_edge(self):
        events=[self.ev('PRODUCE','DAT_0004',1200),
                self.ev('CONSUME','DAT_0004',1220)]
        r=assess(self.target('DAT_0004'),events)[0]
        self.assertEqual(r['status_b'],'DAT_HANDOFF_AT_EDGE')
        self.assertEqual(r['status_a'],'FAIL_DAT_HANDOFF')
        self.assertEqual(assess(self.target('DAT_0004'),[])[0]['status_b'],'NOT_PROFILED')

    def test_dat_production_only_or_consume_only_not_enough(self):
        for kind in ('PRODUCE','CONSUME'):
            r=assess(self.target('DAT_0010'),[self.ev(kind,'DAT_0010',1340)])[0]
            self.assertNotEqual(r['status_a'],'DAT_HANDOFF_AT_EDGE')

    def test_mem2_requires_downstream_consumer_after_read(self):
        events=[self.ev('WRITE','MEM_0004',1288),
                self.ev('READ','MEM_0004',1314),
                self.ev('CONSUME','MEM_0004',1315)]
        r=assess(self.target('MEM_0004'),events)[0]
        self.assertEqual(r['status_a'],'WRITE_READ_CONSUME_WINDOW')
        self.assertEqual(r['status_b'],'WRITE_READ_CONSUME_WINDOW')
        r=assess(self.target('MEM_0004'),events[:2])[0]
        self.assertEqual(r['status_a'],'WRITE_LT_INJECT_LT_READ')
        self.assertFalse(timing_gate_pass([r]))

    def test_mem2_overwritten_before_consumer_not_eligible(self):
        events=[self.ev('WRITE','MEM_0004',1288),
                self.ev('WRITE','MEM_0004',1300),
                self.ev('READ','MEM_0004',1314),
                self.ev('CONSUME','MEM_0004',1315)]
        # Both configured offsets 1292 and1295 are before overwrite at1300.
        r=assess(self.target('MEM_0004'),events)[0]
        self.assertEqual(r['status_a'],'WRITE_LT_INJECT_LT_READ')
        self.assertFalse(timing_gate_pass([r]))

    def test_buf_line_buffer_read_before_same_edge_overwrite(self):
        events=[self.ev('WRITE','BUF_0017',30),
                self.ev('WRITE','BUF_0017',58),
                self.ev('CONSUME','BUF_0017',58)]
        r=assess(self.target('BUF_0017'),events)[0]
        self.assertEqual(r['status_a'],'BUF_WRITE_CONSUME_WINDOW')
        self.assertEqual(r['status_b'],'BUF_WRITE_CONSUME_WINDOW')

    def test_buf_reorder_and_conv2_consumers(self):
        for target,w,c in [('BUF_0007',790,877),('BUF_0031',1048,1255)]:
            r=assess(self.target(target),[self.ev('WRITE',target,w),
                                          self.ev('CONSUME',target,c)])[0]
            self.assertEqual(r['status_a'],'BUF_WRITE_CONSUME_WINDOW')
            self.assertEqual(r['status_b'],'BUF_WRITE_CONSUME_WINDOW')
            self.assertTrue(timing_gate_pass([r]))

    def test_buf_intervening_write_blocks_consume(self):
        events=[self.ev('WRITE','BUF_0007',790),
                self.ev('WRITE','BUF_0007',820),
                self.ev('CONSUME','BUF_0007',877)]
        r=assess(self.target('BUF_0007'),events)[0]
        self.assertEqual(r['status_a'],'AFTER_OBSERVED_WRITE')
        self.assertFalse(timing_gate_pass([r]))

    @staticmethod
    def log_with(*events, schema=True):
        lines=['FI_DISABLED_CONTROL_PASS latency=1342']
        if schema:lines.append(SCHEMA_MARKER)
        lines.extend(events)
        lines.append('FI_PROFILE_PASS: baseline completed, no fault injected')
        return '\n'.join(lines)+'\n'

    def test_profile_parser_real_schema_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'sample.log'
            event='FI_PROFILE_EVENT,ACTIVE,CTL_0001,128,125'
            f.write_text(self.log_with(event))
            self.assertEqual(len(parse_log(f)),1)
            f.write_text(self.log_with(event,event))
            with self.assertRaisesRegex(ValueError,'Duplicate'):parse_log(f)
            f.write_text(self.log_with(event,schema=False))
            with self.assertRaisesRegex(ValueError,'legacy'):parse_log(f)
            self.assertEqual(len(parse_log(f,allow_legacy=True)),1)

    def test_profile_parser_rejects_bad_event_class_and_cycles(self):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'sample.log'
            f.write_text(self.log_with('FI_PROFILE_EVENT,PRODUCE,CTL_0001,128,125'))
            with self.assertRaises(ValueError):parse_log(f)
            f.write_text(self.log_with('FI_PROFILE_EVENT,ACTIVE,CTL_0001,128,125',
                                       'FI_PROFILE_EVENT,ACTIVE,CTL_0001,129,125'))
            with self.assertRaisesRegex(ValueError,'origin'):parse_log(f)
            f.write_text(self.log_with('FI_PROFILE_EVENT,ACTIVE,CTL_0001,128,bad'))
            with self.assertRaisesRegex(ValueError,'Malformed'):parse_log(f)
            f.write_text(self.log_with('FI_PROFILE_EVENT,ACTIVE,CTL_0001,128,125')+
                         'FI_APPLIED scenario=test')
            with self.assertRaisesRegex(ValueError,'fault injection'):parse_log(f)

    def test_control10_requires_exact_golden_and_no_fault(self):
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'IMG0001.log'
            good=('FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 '
                  'logits=10 prediction=7\n'+SCHEMA_MARKER+'\n'
                  'FI_PROFILE_PASS: baseline completed, no fault injected\n')
            log.write_text(good)
            check_control_log(log,'IMG0001',7)
            with self.assertRaises(ValueError):check_control_log(log,'IMG0001',0)
            log.write_text(good+'FI_APPLIED scenario=unexpected\n')
            with self.assertRaises(ValueError):check_control_log(log,'IMG0001',7)

    def test_control10_missing_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'requires independent control10'):
                validate_control10_gate(REPO,Path(folder)/'missing.json',Path(folder))

    def test_full_runner_refuses_missing_v2_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'schema-V2'):
                validate_full_timing_gate(REPO,Path(folder)/'missing_audit.csv',
                                          {r['target_id']:r for r in self.plans})

    def test_installer_rejects_wrong_sim1_tb_path(self):
        src=(REPO/'fi/scripts/install_pilot400_tb.py').read_text()
        self.assertIn('Stale Pilot400 TB in sim_1',src)
        self.assertIn('Multiple Pilot400 TB copies in sim_1',src)
        self.assertIn('add_files -fileset sim_1 -norecurse $src',src)

    def test_tb_has_profilev2_pre_nba_handoff_qualifiers(self):
        tb=(REPO/'fi/tb/tb_cnn_mnist_fi_pilot400.v').read_text()
        for token in ('FI_PROFILE_SCHEMA_V2:', 'FI_PROFILE_EVENT,ACTIVE,CTL_0001',
                      'FI_PROFILE_EVENT,ACTIVE,CTL_0028',
                      'FI_PROFILE_EVENT,PRODUCE,DAT_0010',
                      'FI_PROFILE_EVENT,CONSUME,DAT_0010',
                      'dut.u_argmax.i_valid', 'dut.u_conv2_layer.u_conv2_calc.i_valid',
                      'FI_PROFILE_EVENT,CONSUME,BUF_0007',
                      'FI_PROFILE_EVENT,CONSUME,MEM_0005'):
            self.assertIn(token,tb)

if __name__=='__main__':unittest.main(verbosity=2)

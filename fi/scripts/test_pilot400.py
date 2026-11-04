#!/usr/bin/env python3
"""Static/host-side tests, NOT an XSim/P0 pass claim."""
from __future__ import annotations
import csv
from pathlib import Path
import shutil
import subprocess
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
        # offset1340. The original B=1341 is invalid; the approved refreeze
        # has B=1340 at a DIFFERENT bit of the SAME stored logits register.
        events=[self.ev('PRODUCE','DAT_0010',1339),
                self.ev('CONSUME','DAT_0010',1340),
                self.ev('FC_VALID','DAT_0010',1340)]
        r=assess(self.target('DAT_0010'),events)[0]
        self.assertEqual(r['status_a'],'DAT_HANDOFF_AT_EDGE')
        original_b=int(self.target('DAT_0010')[0]['offset_b'])
        self.assertEqual(r['status_b'],
                         'DAT_HANDOFF_AT_EDGE' if original_b==1340 else 'FAIL_DAT_HANDOFF')
        self.assertEqual(timing_gate_pass([r]),original_b==1340)

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



class Dat0010BitPairPolicyTests(unittest.TestCase):
    """Offline contract tests: fake marker strings are NOT real XSim evidence."""
    def test_legacy_row_0381_0382_remains_original_until_explicit_refreeze(self):
        plans=load_plan(REPO/'fi/config/pilot400_targets.csv',
                        REPO/'fi/config/fi_targets_candidates_v2.csv')
        rows=expected_rows(plans)
        self.assertEqual((rows[380]['bit_index'],rows[380]['injection_offset']),('0','1340'))
        # Both the original frozen plan and its later versioned refreeze
        # must be supported by the SAME test suite.
        self.assertIn((rows[381]['bit_index'],rows[381]['injection_offset']),
                      {('0','1341'),('1','1340')})

    def test_extended_policy_has_unique_bit_time_tuples_at_one_handoff(self):
        from refreeze_pilot400_dat0010 import proposed_rows
        old=load_plan(REPO/'fi/config/pilot400_targets.csv',
                      REPO/'fi/config/fi_targets_candidates_v2.csv')
        target=next(p for p in old if p['target_id']=='DAT_0010')
        if target['bit_index_b']=='1' and target['offset_b']=='1340':
            plans,rows=old,expected_rows(old)
        else:
            plans,rows=proposed_rows(REPO)
        self.assertEqual(len(rows),400)
        self.assertEqual((rows[380]['bit_index'],rows[380]['injection_offset']),('0','1340'))
        self.assertEqual((rows[381]['bit_index'],rows[381]['injection_offset']),('1','1340'))
        self.assertEqual(rows[381]['scenario_id'],'FI_P400_0382')
        before=expected_rows(load_plan(REPO/'fi/config/pilot400_targets.csv',
                              REPO/'fi/config/fi_targets_candidates_v2.csv'))
        if target['bit_index_b']=='1':
            self.assertEqual(before,rows)  # post-refreeze regression
        else:
            self.assertEqual(sum(a!=b for a,b in zip(before,rows)),10)
        self.assertEqual(len({tuple(r[k] for k in FIELDS[1:]) for r in rows}),400)
        self.assertEqual(sum(r['target_class']=='DAT' for r in rows),100)

    def test_same_cycle_same_bit_fails(self):
        from pilot400_common import PLAN_FIELDS
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source.csv'
            rows=read_csv(REPO/'fi/config/pilot400_targets.csv',PLAN_FIELDS)
            for r in rows:
                if r['target_id']=='DAT_0010':
                    r['offset_b']='1340'
            write_csv(p,PLAN_FIELDS,rows)
            with self.assertRaisesRegex(ValueError,'Duplicate bit/time tuple'):
                load_plan(p,REPO/'fi/config/fi_targets_candidates_v2.csv')

    def test_equal_cycle_other_target_is_forbidden_even_with_other_bit(self):
        from pilot400_common import PLAN_FIELDS
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source.csv'
            rows=read_csv(REPO/'fi/config/pilot400_targets.csv',PLAN_FIELDS)
            for r in rows:
                r['bit_index_b']=r['bit_index']
                if r['target_id']=='CTL_0001':
                    r['bit_index_b']='1'
                    r['offset_b']=r['offset_a']
            cols=PLAN_FIELDS[:3]+('bit_index_b',)+PLAN_FIELDS[3:]
            write_csv(p,cols,rows)
            with self.assertRaisesRegex(ValueError,'unapproved equal-offset'):
                load_plan(p,REPO/'fi/config/fi_targets_candidates_v2.csv')

    def test_bit1_probe_value_aware_contract(self):
        from probe_pilot400_dat0010_bit1 import (PROBE_ROW,validate_probe_result)
        r=dict(PROBE_ROW)
        r.update(fault_events='1',injection_ok='1',requested_cycle='1343',
                 actual_cycle='1343',outcome='WRONG_LOGITS_SAME_LABEL',
                 first_bad_stage='FC',protocol_ok='1',logits_mismatch='1')
        marker='FI_DAT_ARGMAX_CONSUME cycle=1343 logit0=00000ac4 golden0=00000ac6 mismatched_classes=1\n'
        witness=validate_probe_result(r,marker)
        self.assertEqual(witness['argmax_logit0'],'00000ac4')
        with self.assertRaisesRegex(ValueError,'XOR'):
            validate_probe_result(r,marker.replace('00000ac4','00000ac7'))
        r['bit_index']='0'
        with self.assertRaisesRegex(ValueError,'bit_index'):
            validate_probe_result(r,marker)

    def test_profile_identifies_single_consumer_for_dat0010(self):
        from refreeze_pilot400_dat0010 import validate_handoff
        from profile_pilot400 import SCHEMA_MARKER
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'profile.log'
            p.write_text('FI_DISABLED_CONTROL_PASS\n'+SCHEMA_MARKER+'\n'+
                         'FI_PROFILE_EVENT,PRODUCE,DAT_0010,1342,1339\n'+
                         'FI_PROFILE_EVENT,CONSUME,DAT_0010,1343,1340\n'+
                         'FI_PROFILE_EVENT,FC_VALID,DAT_0010,1343,1340\n'+
                         'FI_PROFILE_PASS: baseline completed, no fault injected\n')
            old=dict(next(t for t in load_plan(REPO/'fi/config/pilot400_targets.csv',
                        REPO/'fi/config/fi_targets_candidates_v2.csv')
                        if t['target_id']=='DAT_0010'))
            old['offset_b']='1341'  # explicitly test original invalid B time
            self.assertEqual(validate_handoff(p,old),3)
            p.write_text(p.read_text().replace('FI_PROFILE_PASS:',
                'FI_PROFILE_EVENT,CONSUME,DAT_0010,1344,1341\nFI_PROFILE_PASS:'))
            with self.assertRaisesRegex(ValueError,'Unexpected consume'):
                validate_handoff(p,old)

    def test_new_manifest_tcl_propagates_bit1(self):
        from refreeze_pilot400_dat0010 import proposed_rows
        old=load_plan(REPO/'fi/config/pilot400_targets.csv',
                      REPO/'fi/config/fi_targets_candidates_v2.csv')
        target=next(p for p in old if p['target_id']=='DAT_0010')
        rows=(expected_rows(old) if target['bit_index_b']=='1'
              else proposed_rows(REPO)[1])
        with tempfile.TemporaryDirectory() as tmp:
            tcl=make_tcl(Path(tmp)/'Simulation.xpr',rows[381],
                         Path(tmp)/'probe.done','sim_1',
                         REPO/'fi/pilot400/samples/IMG0000')
            self.assertIn('FI_BIT=1',tcl)
            self.assertIn('FI_OFFSET=1340',tcl)
            self.assertIn('SCENARIO_ID=FI_P400_0382',tcl)

class DirectControl10BackendTests(unittest.TestCase):
    """Static/dry-run checks, not Windows Vivado/XSim execution."""

    def test_prepare_generates_scripts_once_without_reset(self):
        from pilot400_direct_xsim import prepare_tcl
        txt=prepare_tcl(Path('/temp/Simulation.xpr'),'sim_1',
                        Path('/temp/scripts_only_success.marker'))
        self.assertIn('launch_simulation -scripts_only -absolute_path -noclean_dir',txt)
        self.assertIn('puts $success_channel {CONTROL10_SCRIPT_GENERATION_PASS}',txt)
        self.assertNotIn('puts "CONTROL10_SCRIPT_GENERATION_PASS"',txt)
        self.assertIn('file delete -force -- $success_marker',txt)
        self.assertIn('set_property top tb_cnn_mnist_fi_pilot400',txt)
        self.assertNotIn('reset_simulation',txt)
        self.assertNotIn('run all',txt)

    def test_scripts_only_marker_requires_real_file_not_vivado_source_echo(self):
        from pilot400_direct_xsim import (SCRIPTS_ONLY_MARKER,
                                          verify_scripts_only_evidence)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            stamp=root/'scripts_only_success.marker'
            log=root/'prepare.log'
            log.write_text('## puts $success_channel {CONTROL10_SCRIPT_GENERATION_PASS}\n'
                           'INFO: [USF-XSim-60] Script generated:/tmp/compile.bat\n'
                           'INFO: [USF-XSim-60] Script generated:/tmp/elaborate.bat\n'
                           'INFO: [USF-XSim-60] Script generated:/tmp/simulate.bat\n')
            with self.assertRaisesRegex(ValueError,'Missing scripts_only'):
                verify_scripts_only_evidence(stamp,log)
            stamp.write_text(SCRIPTS_ONLY_MARKER+'\n')
            verify_scripts_only_evidence(stamp,log)
            stamp.write_text('not the success token')
            with self.assertRaisesRegex(ValueError,'Invalid scripts_only'):
                verify_scripts_only_evidence(stamp,log)
            stamp.write_text(SCRIPTS_ONLY_MARKER)
            log.write_text('## puts $success_channel {CONTROL10_SCRIPT_GENERATION_PASS}\n')
            with self.assertRaisesRegex(ValueError,'did not report generating'):
                verify_scripts_only_evidence(stamp,log)
            log.write_text('INFO: [USF-XSim-60] Script generated:/tmp/compile.bat\n'
                           'INFO: [USF-XSim-60] Script generated:/tmp/elaborate.bat\n'
                           'INFO: [USF-XSim-60] Script generated:/tmp/simulate.bat\n'
                           'ERROR: broken channel\n')
            with self.assertRaisesRegex(ValueError,'contains error'):
                verify_scripts_only_evidence(stamp,log)

    @unittest.skipUnless(shutil.which('tclsh'), 'tclsh not available on host')
    def test_prepare_tcl_survives_vivado_closing_stdout(self):
        """Reproduce the exact failure: Tcl stdout closed inside scripts_only."""
        from pilot400_direct_xsim import prepare_tcl, SCRIPTS_ONLY_MARKER
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            marker=root/'success.marker'
            source=root/'prepare.tcl'
            source.write_text(prepare_tcl(root/'Simulation.xpr','sim_1',marker))
            mock='''proc open_project {args} {}
proc get_filesets {args} {return [list sim_1]}
proc set_property {args} {}
proc update_compile_order {args} {}
proc launch_simulation {args} {close stdout}
'''
            script=root/'driver.tcl'
            script.write_text(mock+'\nsource {'+source.as_posix()+'}\n')
            run=subprocess.run(['tclsh',str(script)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(marker.read_text().strip(),SCRIPTS_ONLY_MARKER)

    @unittest.skipUnless(shutil.which('tclsh'), 'tclsh not available on host')
    def test_failed_scripts_only_clears_stale_marker_and_exits_nonzero(self):
        from pilot400_direct_xsim import prepare_tcl, SCRIPTS_ONLY_MARKER
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            marker=root/'success.marker'
            marker.write_text(SCRIPTS_ONLY_MARKER)
            source=root/'prepare.tcl'
            source.write_text(prepare_tcl(root/'Simulation.xpr','sim_1',marker))
            mock='''proc open_project {args} {}
proc get_filesets {args} {return [list sim_1]}
proc set_property {args} {}
proc update_compile_order {args} {}
proc launch_simulation {args} {error {mock Vivado generation failure}}
'''
            script=root/'driver.tcl'
            script.write_text(mock+'\nsource {'+source.as_posix()+'}\n')
            run=subprocess.run(['tclsh',str(script)],capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0)
            self.assertFalse(marker.exists())

    def test_direct_run_quotes_entire_plusarg_tokens(self):
        from pilot400_direct_xsim import make_cmd_run
        row=dict(scenario_id='FI_P400_0001',target_id='CTL_0001',
                 sample_id='IMG0000',bit_index='2',injection_offset='125')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            txt=make_cmd_run(root/'xsim',root/'vivado',row,root/'samples',
                             root/'test.tcl',root/'run.log',root/'console.log')
        self.assertIn('-testplusarg "PILOT_PROFILE_ONLY=1"',txt)
        self.assertIn('-testplusarg "FI_OFFSET=125"',txt)
        self.assertIn('-testplusarg "SCENARIO_ID=FI_P400_0001"',txt)
        self.assertIn('-testplusarg "INPUT_FILE=',txt)
        self.assertIn('set "RC=%ERRORLEVEL%"',txt)
        self.assertIn('exit /b %RC%',txt)
        self.assertNotIn('settings64.bat',txt)
        self.assertNotIn('reset_simulation',txt)

    def test_stage_batch_uses_explicit_windows_environment(self):
        from pilot400_direct_xsim import make_cmd_stage
        txt=make_cmd_stage(Path('/sim'),Path('/vivado'),'compile',Path('/tmp/console.log'))
        self.assertIn('set "XILINX_VIVADO=',txt)
        self.assertIn('set "PATH=%XILINX_VIVADO%/bin;%PATH%"',txt)
        self.assertIn('call "compile.bat"',txt)
        self.assertIn('exit /b %RC%',txt)

    def test_cmd_special_chars_are_rejected(self):
        from pilot400_direct_xsim import batch_safe
        for arg in ('C:/evil%PATH%', 'x&whoami', 'x!TEMP!', 'x"y', 'x^y'):
            with self.assertRaises(ValueError):batch_safe(arg)
        self.assertEqual(batch_safe('D:\\foo\\bar'), 'D:/foo/bar')

    def test_prj_matches_actual_repo_rtl_and_tb(self):
        from pilot400_direct_xsim import project_source_hashes
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rtl=root/'rtl';rtl.mkdir()
            sim=root/'Simulation';sim.mkdir()
            tb=root/'fi/tb';tb.mkdir(parents=True)
            tb_file=tb/'tb_cnn_mnist_fi_pilot400.v'
            tb_file.write_text('module test; endmodule\n')
            copies=[]
            for i in range(21):
                filename='source_%02d.v'%i
                original=rtl/filename
                original.write_text('module x%02d; endmodule\n'%i)
                copy=sim/filename
                copy.write_bytes(original.read_bytes())
                copies.append(copy)
            prj=root/'xsim_vlog.prj'
            prj.write_text('\n'.join('verilog xil_defaultlib "'+str(p)+'"' for p in [*copies,tb_file]))
            hashes,count=project_source_hashes(prj,root)
            self.assertEqual(count,21)
            self.assertEqual(len(hashes),22)
            copies[3].write_text('module changed; endmodule\n')
            with self.assertRaisesRegex(ValueError,'differs from current repo RTL'):
                project_source_hashes(prj,root)

    @staticmethod
    def make_vivado_authority_fixture(folder):
        """A real-looking .prj with 24 Vivado RTL files, TB, and glbl."""
        from pilot400_common import sha256
        root=Path(folder)
        repo=root/'repo';(repo/'rtl').mkdir(parents=True)
        tb=(repo/'fi/tb/tb_cnn_mnist_fi_pilot400.v')
        tb.parent.mkdir(parents=True)
        tb.write_text('module tb_cnn_mnist_fi_pilot400; endmodule\n')
        project_dir=root/'Vivado';project_dir.mkdir()
        project=project_dir/'Simulation.xpr';project.write_text('project stub\n')
        vsrc=project_dir/'Simulation.srcs/sources_1/new';vsrc.mkdir(parents=True)
        required=['cnn_mnist_top.v','top_fsm.v','argmax.v',
                  'fully_connected.v','feature_mem1.v','feature_mem2.v',
                  'conv1_layer.v','conv2_layer.v']
        names=required+[f'rtl_component_{i:02d}.v' for i in range(16)]
        rtl=[]
        for name in names:
            v=vsrc/name;v.write_text(f'// authoritative Vivado {name}\n')
            (repo/'rtl'/name).write_text(f'// stale Git {name}\n')
            rtl.append(v)
        glbl=root/'xsim.dir/glbl.v';glbl.parent.mkdir();glbl.write_text('module glbl; endmodule\n')
        prj=root/'xsim_vlog.prj'
        prj.write_text('\n'.join('verilog xil_defaultlib "'+str(v)+'"'
                                for v in [*rtl,tb,glbl])+'\n')
        return repo,project,prj,rtl,tb

    def test_opt_in_vivado_authority_accepts_stale_git_and_hashes_actual_hdl(self):
        from pilot400_direct_xsim import project_source_hashes
        from pilot400_common import sha256
        with tempfile.TemporaryDirectory() as tmp:
            repo,project,prj,rtl,tb=self.make_vivado_authority_fixture(tmp)
            with self.assertRaisesRegex(ValueError,'differs from current repo RTL'):
                project_source_hashes(prj,repo)
            actual,count=project_source_hashes(prj,repo,'vivado',project)
            self.assertEqual(count,24)
            self.assertEqual(len(actual),26)  # 24 RTL + 1 TB + 1 glbl
            self.assertEqual(actual[str(rtl[0].resolve())],sha256(rtl[0]))
            self.assertIn(str(tb.resolve()),actual)
            self.assertIn(str((Path(tmp)/'xsim.dir/glbl.v').resolve()),actual)

    def test_opt_in_vivado_authority_rejects_external_or_missing_critical_rtl(self):
        from pilot400_direct_xsim import project_source_hashes
        with tempfile.TemporaryDirectory() as tmp:
            repo,project,prj,rtl,tb=self.make_vivado_authority_fixture(tmp)
            external=Path(tmp)/'external.v';external.write_text('// outside source tree\n')
            original=prj.read_text()
            prj.write_text(original.replace(str(rtl[0]),str(external)))
            with self.assertRaisesRegex(ValueError,'outside selected project .srcs'):
                project_source_hashes(prj,repo,'vivado',project)
            prj.write_text(original.replace(str(rtl[0])+'"',str(rtl[1])+'"'))
            with self.assertRaises(ValueError):
                project_source_hashes(prj,repo,'vivado',project)
            prj.write_text(original.replace(str(rtl[0])+'"',str(rtl[0])+'"'))
            rtl[0].unlink()
            with self.assertRaisesRegex(ValueError,'missing HDL source'):
                project_source_hashes(prj,repo,'vivado',project)

    def test_opt_in_vivado_authority_requires_exact_fi_testbench_and_project(self):
        from pilot400_direct_xsim import project_source_hashes
        with tempfile.TemporaryDirectory() as tmp:
            repo,project,prj,rtl,tb=self.make_vivado_authority_fixture(tmp)
            with self.assertRaisesRegex(ValueError,'existing --project'):
                project_source_hashes(prj,repo,'vivado',None)
            other=Path(tmp)/'different_tb.v';other.write_text('// changed TB\n')
            prj.write_text(prj.read_text().replace(str(tb),str(other)))
            with self.assertRaisesRegex(ValueError,'outside selected project .srcs'):
                project_source_hashes(prj,repo,'vivado',project)
            prj.write_text(prj.read_text().replace(str(other),str(tb)))
            tb.write_text('// modified TB\n')
            # Same TB file is authoritative in repo and in the .prj: no mismatch.
            hashes,count=project_source_hashes(prj,repo,'vivado',project)
            self.assertEqual(count,24)

    def test_vivado_authority_rejects_duplicate_RTL_name_and_tamper(self):
        from pilot400_direct_xsim import project_source_hashes
        with tempfile.TemporaryDirectory() as tmp:
            repo,project,prj,rtl,tb=self.make_vivado_authority_fixture(tmp)
            duplicate=(project.parent/'Simulation.srcs/sources_2/new'/rtl[0].name)
            duplicate.parent.mkdir(parents=True)
            duplicate.write_text('// copy under same basename\n')
            prj.write_text(prj.read_text()+f'verilog xil_defaultlib "{duplicate}"\n')
            with self.assertRaisesRegex(ValueError,'Duplicate Vivado RTL basename'):
                project_source_hashes(prj,repo,'vivado',project)

    def test_cli_disallows_vivado_authority_without_direct_backend(self):
        from run_pilot400_control10 import main
        from unittest.mock import patch
        with patch('sys.argv',['run_pilot400_control10.py',
                               '--project','fake.xpr','--rtl-source','vivado']):
            with self.assertRaises(SystemExit) as context:
                main()
            self.assertEqual(context.exception.code,2)

    def test_xelab_incremental_relink_with_updated_snapshot_is_accepted(self):
        from pilot400_direct_xsim import (classify_elaboration, snapshot_signature,
                                          ELAB_RELINK_MARKER)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)/'xsim.dir'/'tb_cnn_mnist_fi_pilot400_behav'
            snapshot.mkdir(parents=True)
            kernel = snapshot/'xsimk.dll'
            kernel.write_bytes(b'previous kernel')
            before = snapshot_signature(snapshot)
            kernel.write_bytes(b'relinked kernel with new content')
            text = ('Running: C:/Vivado/xelab.exe --incr ...\n'
                    'Starting static elaboration\n'
                    'Completed static elaboration\n'
                    + ELAB_RELINK_MARKER + '\n')
            self.assertEqual(classify_elaboration(text, snapshot, before),
                             'INCREMENTAL_RELINK')

    def test_xelab_full_build_and_relink_are_distinguished(self):
        from pilot400_direct_xsim import (classify_elaboration, snapshot_signature,
                                          ELAB_BUILT_MARKER, ELAB_RELINK_MARKER)
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp)/'snapshot'; snap.mkdir()
            kernel = snap/'xsimk.dll';kernel.write_bytes(b'old')
            before = snapshot_signature(snap)
            kernel.write_bytes(b'new kernel')
            prefix = ('Running: C:/Vivado/xelab.exe\nStarting static elaboration\n'
                      'Completed static elaboration\n')
            self.assertEqual(classify_elaboration(prefix+ELAB_BUILT_MARKER,snap,before),
                             'FULL_BUILD')
            with self.assertRaisesRegex(ValueError,'not refreshed'):
                classify_elaboration(prefix+ELAB_RELINK_MARKER,snap,snapshot_signature(snap))
            with self.assertRaisesRegex(ValueError,'neither'):
                classify_elaboration(prefix+'unrecognized build output',snap,before)
            with self.assertRaisesRegex(ValueError,'error/fatal'):
                classify_elaboration(prefix+ELAB_RELINK_MARKER+'\nERROR: linker failed',snap,before)
            with self.assertRaisesRegex(ValueError,'completed'):
                classify_elaboration(ELAB_RELINK_MARKER,snap,before)

    def test_error_cannot_be_labeled_control_pass(self):
        from run_pilot400_control10 import check_control_log
        from profile_pilot400 import SCHEMA_MARKER
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.log'
            clean=('FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 '
                   'pool2=200 logits=10 prediction=0\n'+SCHEMA_MARKER+'\n'
                   'FI_PROFILE_PASS: baseline completed, no fault injected\n')
            p.write_text(clean)
            check_control_log(p,'IMG0000',0)
            p.write_text(clean+'ERROR: injected tool failure\n')
            with self.assertRaisesRegex(ValueError,'error/fatal'):
                check_control_log(p,'IMG0000',0)
            p.write_text(clean+'FI_APPLIED scenario=UNEXPECTED\n')
            with self.assertRaisesRegex(ValueError,'fault injected'):
                check_control_log(p,'IMG0000',0)

if __name__=='__main__':unittest.main(verbosity=2)

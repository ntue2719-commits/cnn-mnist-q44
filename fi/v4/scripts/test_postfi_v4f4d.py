#!/usr/bin/env python3
"""Offline/negative tests only. NO Vivado/XSim invocation."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr
from io import StringIO

import qualify_postfi_v4f4d as d
import audit_postfi_v4f4d as a
import qualify_xsim_failures_v4f4c as q


class TclSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_tcl_inject_condition_is_frozen_tb(self):
        content=d.generate_tcl(self.root/'proof.txt')
        self.assertIn('/tb_cnn_mnist_fi_pilot400/fi_injection_ok == 1',content)
        self.assertIn('add_condition -name '+d.GUARD,content)
        self.assertIn('stop',content)
        self.assertIn('remove_conditions '+d.GUARD,content)
        self.assertIn('run 1ns',content)
        self.assertIn('get_value -radix unsigned',content)
        self.assertNotIn('get_value -radix bin',content)
        self.assertIn('vwait ::v4f4d_external_release_never',content)
        self.assertNotIn('force ',content)
        self.assertNotIn('set_value',content)
        self.assertNotIn('run all\nquit',content)

    def test_integer_one_uses_decimal_unsigned_readback(self):
        # A 32-bit HDL integer with value one could be represented as
        # 00000000000000000000000000000001 in -radix bin.
        content=d.generate_tcl(self.root/'proof.txt')
        self.assertIn('get_value -radix unsigned /tb_cnn_mnist_fi_pilot400/fi_injection_ok',content)
        self.assertIn('if {$::v4f4d_value ne "1"}',content)
        self.assertNotIn('get_value -radix bin',content)

    def test_tcl_sidecar_paths_are_frozen(self):
        path=self.root/'receipt.txt'
        tcl=d.generate_tcl(path)
        self.assertIn(path.resolve().as_posix(),tcl)
        self.assertIn('flush $::v4f4d_f',tcl)
        self.assertIn('close $::v4f4d_f',tcl)
        self.assertIn('file rename',tcl)
        self.assertIn('open {'+path.resolve().as_posix()+'.tmp} w',tcl)

    def test_tcl_rejects_braces_in_path(self):
        with self.assertRaisesRegex(ValueError,'Unsupported Tcl receipt path'):
            d.generate_tcl(self.root/'bad{path}.txt')

    def test_tcl_rejects_quote_in_path(self):
        with self.assertRaises(ValueError):
            d.generate_tcl(self.root/'bad"path.txt')

    def test_tcl_rejects_newline_in_path(self):
        with self.assertRaises(ValueError):
            d.generate_tcl(self.root/'bad\npath.txt')

    def test_tcl_readback_after_one_ns(self):
        tcl=d.generate_tcl(self.root/'pause.txt')
        self.assertLess(tcl.index('remove_conditions'),tcl.index('run 1ns'))
        self.assertLess(tcl.index('run 1ns'),tcl.index('get_value -radix unsigned'))
        self.assertLess(tcl.index('get_value -radix unsigned'),tcl.index('V4F4D_POSTFI_PAUSE_READY'))

    def test_tcl_no_result_emission(self):
        tcl=d.generate_tcl(self.root/'pause.txt')
        self.assertNotIn('FI_RESULT_V2',tcl)
        self.assertNotIn('FI_APPLIED',tcl)  # must come from frozen TB

    def test_success_handshake_exact_bytes(self):
        path=self.root/'pause.txt'
        path.write_bytes(d.PAUSE_TEXT.encode('ascii'))
        self.assertTrue(d.handshake_file_valid(path))

    def test_handshake_rejects_corrupted_or_missing(self):
        path=self.root/'pause.txt'
        self.assertFalse(d.handshake_file_valid(path))
        for invalid in (b'',b'V4F4D_POSTFI_PAUSE_READY\n',b'FI_INJECTION_OK=1\n',
                        d.PAUSE_TEXT.encode()+b'X',b'0\n'):
            with self.subTest(data=invalid):
                path.write_bytes(invalid)
                self.assertFalse(d.handshake_file_valid(path))

    def test_handshake_rejects_symlink(self):
        raw=self.root/'original.txt';raw.write_text(d.PAUSE_TEXT)
        alias=self.root/'alias'
        try:alias.symlink_to(raw)
        except (OSError,NotImplementedError):self.skipTest('Symlink creation unavailable')
        self.assertFalse(d.handshake_file_valid(alias))


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.pause=self.root/'receipt.txt'
        self.pause.write_bytes(d.PAUSE_TEXT.encode())
        self.log=self.root/'fi.log'
        self.log.write_text('FI_APPLIED scenario=FI_P400_0167 target=BUF_0031 class=BUF bit=0\n')
        self.good={'termination_requested':True,'external_timeout':False,
                   'owned_active_processes_after':0,'child_started':True,
                   'tcl_handshake_seen':True,'tcl_handshake_valid':True,
                   'log_marker_confirmed_before_termination':True,
                   'postfi_kill_cause':'ACK_AND_MARKER',
                   'owned_active_processes_before_termination':3}

    def test_valid(self):
        self.assertEqual(d.classify_probe(self.good,self.pause,self.log),d.PROBE_MARKER)

    def test_no_termination(self):
        p=dict(self.good,termination_requested=False)
        self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_timeout_is_not_postfi(self):
        p=dict(self.good,external_timeout=True)
        self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_no_child(self):
        p=dict(self.good,child_started=False)
        self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_owned_job_processes_must_be_zero(self):
        p=dict(self.good,owned_active_processes_after=1)
        self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_no_handshake(self):
        for key in ('tcl_handshake_seen','tcl_handshake_valid'):
            with self.subTest(key=key):
                p=dict(self.good);p[key]=False
                self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_missing_confirmed_marker_before_kill(self):
        p=dict(self.good,log_marker_confirmed_before_termination=False)
        self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_unproven_termination_decision(self):
        p=dict(self.good,postfi_kill_cause='NO_MARKER')
        self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_launcher_alive_but_child_gone_does_not_qualify(self):
        p=dict(self.good,owned_active_processes_before_termination=1)
        self.assertNotEqual(d.classify_probe(p,self.pause,self.log),d.PROBE_MARKER)

    def test_missing_raw_log(self):
        self.log.unlink()
        self.assertNotEqual(d.classify_probe(self.good,self.pause,self.log),d.PROBE_MARKER)

    def test_no_applied_marker(self):
        self.log.write_text('xsim started but no FI marker\n')
        self.assertNotEqual(d.classify_probe(self.good,self.pause,self.log),d.PROBE_MARKER)

    def test_two_applied_markers(self):
        self.log.write_text('FI_APPLIED scenario=a\nFI_APPLIED scenario=b\n')
        self.assertNotEqual(d.classify_probe(self.good,self.pause,self.log),d.PROBE_MARKER)

    def test_finished_result_invalidates_postfi(self):
        self.log.write_text(self.log.read_text()+'FI_RESULT_V2,FI_P400_0167,...\n')
        self.assertNotEqual(d.classify_probe(self.good,self.pause,self.log),d.PROBE_MARKER)

    def test_malformed_pause_invalidates_postfi(self):
        self.pause.write_text('forged')
        self.assertNotEqual(d.classify_probe(self.good,self.pause,self.log),d.PROBE_MARKER)

    def test_replay_classification_consistency(self):
        process={**self.good,'return_code':87,'marker_evidence':[1,0]}
        self.assertEqual(q.classify_observed_process('terminate-after-fi',process),d.PROBE_MARKER)

    def test_synthetic_result_not_committed(self):
        self.assertEqual(d.PROBE_MARKER,'INTERRUPTED_AFTER_FI_NO_RESULT')
        self.assertEqual(d.ATTEMPT_NAME,'v4f4c_postfi_det_01')
        self.assertEqual(d.SELECTED_ID,'FI_P400_0167')


class DeterministicGateTests(unittest.TestCase):
    def test_marker_alone_never_kills(self):
        self.assertEqual(d.postfi_termination_decision(False,False,(1,0),99),'WAIT')

    def test_ack_but_marker_not_yet_visible_waits(self):
        self.assertEqual(d.postfi_termination_decision(True,True,(0,0),.2),'WAIT')

    def test_ack_and_exact_one_marker_terminates(self):
        self.assertEqual(d.postfi_termination_decision(True,True,(1,0),.001),'ACK_AND_MARKER')

    def test_ack_and_two_marker_is_never_positive(self):
        self.assertEqual(d.postfi_termination_decision(True,True,(1,1),1),'WAIT')
        self.assertEqual(d.postfi_termination_decision(True,True,(1,1),5),'NO_MARKER')

    def test_partial_receipt_grace_period(self):
        self.assertEqual(d.postfi_termination_decision(True,False,(1,0),.1),'WAIT')
        self.assertEqual(d.postfi_termination_decision(True,False,(1,0),2),'INVALID_ACK')

    def test_missing_raw_marker_bounded_timeout(self):
        self.assertEqual(d.postfi_termination_decision(True,True,(0,0),4.9),'WAIT')
        self.assertEqual(d.postfi_termination_decision(True,True,(0,0),5),'NO_MARKER')

    def test_duplicate_marker_not_positive(self):
        self.assertEqual(d.postfi_termination_decision(True,True,(2,0),5),'NO_MARKER')


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results=Path(self.tmp.name)
        self.expected={
          'v4f4c_healthy_01':('FI_P400_0007','healthy',q.SUCCESS),
          'v4f4c_terminate_before_01':('FI_P400_0014','terminate-before-fi',q.BLOCKED),
          'v4f4c_terminate_after_01':('FI_P400_0167','terminate-after-fi',q.BLOCKED),
          'v4f4c_crash_before_01':('FI_P400_0174','crash-before-commit',q.RUNNING),
          'v4f4c_timeout_01':('FI_P400_0227','external-timeout',q.BLOCKED),
          'v4f4c_recovery_01':('FI_P400_0227','healthy',q.SUCCESS),
        }
        for name,(sid,trial,status) in self.expected.items():
            p=self.results/name;p.mkdir()
            self._write(p,{'schema':q.SCHEMA,'max_xsim_processes':1,
                           'xsim_invocations_committed':1 if status==q.SUCCESS else 0,
                           'scenario_id':sid,'trial':trial,'status':status})

    def _write(self,path,item):
        (path/'attempt.json').write_text(json.dumps(item))

    def test_exact_six(self):
        self.assertEqual(len(d.historical_attempts(self.results)),6)

    def test_missing_one_fails(self):
        (self.results/'v4f4c_healthy_01'/'attempt.json').unlink()
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_tampered_status_fails(self):
        p=self.results/'v4f4c_crash_before_01'
        item=json.loads((p/'attempt.json').read_text());item['status']=q.SUCCESS
        self._write(p,item)
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_tampered_trial_fails(self):
        p=self.results/'v4f4c_terminate_before_01'
        item=json.loads((p/'attempt.json').read_text());item['trial']='healthy'
        self._write(p,item)
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_tampered_id_fails(self):
        p=self.results/'v4f4c_terminate_after_01'
        item=json.loads((p/'attempt.json').read_text());item['scenario_id']='FI_P400_0001'
        self._write(p,item)
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_unknown_seventh_fails(self):
        p=self.results/'v4f4c_unknown_01';p.mkdir()
        self._write(p,{'schema':q.SCHEMA,'max_xsim_processes':1,'xsim_invocations_committed':0})
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_corrupted_schema_fails(self):
        p=self.results/'v4f4c_timeout_01'
        item=json.loads((p/'attempt.json').read_text());item['schema']='FRAUD'
        self._write(p,item)
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_extra_output_counts_but_is_not_free(self):
        p=self.results/d.ATTEMPT_NAME;p.mkdir()
        self._write(p,{'schema':q.SCHEMA,'max_xsim_processes':1,
                       'xsim_invocations_committed':0,'qualification_mode':d.SCHEMA,
                       'scenario_id':d.SELECTED_ID})
        self.assertEqual(len(d.historical_attempts(self.results)),7)
        q.enforce_aggregate_budget(self.results)

    def test_orphan_run_refused(self):
        p=self.results/'v4f4c_extra_02';(p/'runs').mkdir(parents=True)
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_no_outputs_for_plan(self):
        p=self.results/'v4f4d_plan_01';p.mkdir()
        self.assertEqual(len(d.historical_attempts(self.results)),6)

    def test_two_reserved_slots_hard_stop(self):
        p=self.results/d.ATTEMPT_NAME;p.mkdir()
        self._write(p,{'schema':q.SCHEMA,'max_xsim_processes':1,
                       'xsim_invocations_committed':0,'qualification_mode':d.SCHEMA,
                       'scenario_id':d.SELECTED_ID})
        other=self.results/'v4f4c_unknown_02';other.mkdir()
        self._write(other,{'schema':q.SCHEMA,'max_xsim_processes':1,'xsim_invocations_committed':0})
        with self.assertRaises(ValueError):d.historical_attempts(self.results)

    def test_inflight_attempt_counts(self):
        p=self.results/'v4f4c_crash_before_01'
        self.assertEqual(json.loads((p/'attempt.json').read_text())['status'],q.RUNNING)
        self.assertEqual(len(d.historical_attempts(self.results)),6)

    def test_existing_attempt_dir_cannot_be_used_for_new(self):
        self.assertTrue((self.results/'v4f4c_timeout_01').exists())


class AuditAndCliTests(unittest.TestCase):
    def test_denies_full400_even_with_execute(self):
        with mock.patch('sys.argv',['prog','--mode','full400','--execute']):
            with self.assertRaisesRegex(SystemExit,'Full400 BLOCKED'):
                d.main()

    def test_denies_other_scenario(self):
        with mock.patch('sys.argv',['prog','--mode','execute','--execute',
                                    '--scenario-id','FI_P400_0387']):
            with self.assertRaisesRegex(SystemExit,'Only exact frozen scenario'):
                d.main()

    def test_execute_without_windows_fails_before_io(self):
        if os.name=='nt':self.skipTest('OS-independent negative path checks run on Linux')
        with mock.patch('sys.argv',['prog','--mode','execute','--execute']):
            with self.assertRaisesRegex(SystemExit,'requires Windows'):
                d.main()

    def test_execute_requires_project_and_vivado(self):
        if os.name=='nt':self.skipTest('Linux-only negative path')
        with mock.patch('sys.argv',['prog','--mode','execute']):
            with self.assertRaises(SystemExit):d.main()

    def test_auditor_rejects_absent_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);(repo/'fi/results').mkdir(parents=True)
            with self.assertRaises(ValueError):
                a.audit_attempt(repo,repo/'fi/results',repo/'fi/results'/d.ATTEMPT_NAME,
                                repo/'fi/results/v4f4d_audit_01')

    def test_auditor_rejects_other_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);res=repo/'fi/results';res.mkdir(parents=True)
            other=res/'v4f4c_healthy_01';other.mkdir()
            with self.assertRaises(ValueError):
                a.audit_attempt(repo,res,other,res/'v4f4d_audit_01')

    def test_auditor_rejects_existing_audit_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);res=repo/'fi/results';res.mkdir(parents=True)
            attempt=res/d.ATTEMPT_NAME;attempt.mkdir()
            out=res/'v4f4d_audit_01';out.mkdir()
            with self.assertRaises(ValueError):a.audit_attempt(repo,res,attempt,out)

    def test_audit_schema_separate(self):
        self.assertIn('READ_ONLY_AUDIT',a.AUDIT_SCHEMA)
        self.assertNotEqual(a.AUDIT_SCHEMA,d.SCHEMA)

    def test_budget_lock_reuses_original(self):
        self.assertEqual(d.q.global_campaign_lock,q.global_campaign_lock)
        self.assertEqual(d.q.AGGREGATE_ATTEMPT_BUDGET,8)

    def test_source_prefix_preserves_original_budget(self):
        self.assertTrue(d.ATTEMPT_NAME.startswith('v4f4c_'))
        self.assertFalse(d.ATTEMPT_NAME.endswith('plan_01'))

    def test_no_release_flag_in_plan(self):
        self.assertIn('FULL400_BLOCKED',d.PLAN_STATUS)


if __name__=='__main__':unittest.main()

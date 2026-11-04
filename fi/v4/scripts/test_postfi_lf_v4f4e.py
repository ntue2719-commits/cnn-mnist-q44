#!/usr/bin/env python3
"""V4f4E: offline, negative-only tests; never execute Vivado/XSim."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import qualify_postfi_lf_v4f4e as p
import audit_postfi_lf_v4f4e as a
import qualify_postfi_v4f4d as old
import qualify_xsim_failures_v4f4c as q


class TclLFCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.receipt=self.root/'receipt.txt'
        self.tcl=p.generate_tcl(self.receipt)

    def test_exact_tcl_lf_writer(self):
        self.assertIn('fconfigure $::v4f4d_f -translation lf -encoding ascii',self.tcl)
        self.assertLess(self.tcl.index('set ::v4f4d_f [open '),self.tcl.index('fconfigure '))
        self.assertLess(self.tcl.index('fconfigure '),self.tcl.index('puts $::v4f4d_f "V4F4D_POSTFI_PAUSE_READY"'))
        self.assertLess(self.tcl.index('fconfigure '),self.tcl.index('file rename '))

    def test_unchanged_old_tcl_for_seventh_attempt(self):
        self.assertNotIn('fconfigure $::v4f4d_f -translation lf',old.generate_tcl(self.receipt))
        self.assertEqual(old.PAUSE_TEXT,p.PAUSE_TEXT)
        self.assertEqual(old.PAUSE_FILE,p.PAUSE_FILE)
        self.assertNotEqual(old.SCHEMA,p.SCHEMA)
        self.assertNotEqual(old.ATTEMPT_NAME,p.ATTEMPT_NAME)

    def test_lf_exact_bytes_accepted(self):
        self.receipt.write_bytes(b'V4F4D_POSTFI_PAUSE_READY\nFI_INJECTION_OK=1\n')
        self.assertTrue(p.handshake_file_valid(self.receipt))

    def test_historical_crlf_is_rejected_not_reclassified(self):
        self.receipt.write_bytes(b'V4F4D_POSTFI_PAUSE_READY\r\nFI_INJECTION_OK=1\r\n')
        self.assertFalse(p.handshake_file_valid(self.receipt))
        self.assertFalse(old.handshake_file_valid(self.receipt))

    def test_bad_receipt_bytes_fail_closed(self):
        for raw in (b'',b'FI_INJECTION_OK=1\n',b'V4F4D_POSTFI_PAUSE_READY\n',
                    p.PAUSE_TEXT.encode()+b'1',b'V4F4D_POSTFI_PAUSE_READY\nFI_INJECTION_OK=0\n'):
            with self.subTest(raw=raw):
                self.receipt.write_bytes(raw)
                self.assertFalse(p.handshake_file_valid(self.receipt))

    def test_tcl_guard_only_reads_frozen_signal(self):
        self.assertIn('add_condition -name '+p.GUARD,self.tcl)
        self.assertIn('/tb_cnn_mnist_fi_pilot400/fi_injection_ok == 1',self.tcl)
        self.assertIn('get_value -radix unsigned',self.tcl)
        self.assertNotIn('get_value -radix bin',self.tcl)
        self.assertIn('run 1ns',self.tcl)
        self.assertIn('vwait ::v4f4d_external_release_never',self.tcl)
        self.assertNotIn('set_value',self.tcl)
        self.assertNotIn('force ',self.tcl)
        self.assertNotIn('FI_RESULT_V2',self.tcl)

    def test_tcl_rejects_malformed_receipt_path(self):
        for name in ('bad{path}.txt','bad"quote.txt','bad\nnewline.txt'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                p.generate_tcl(self.root/name)

    def test_lf_tcl_interpreter_stub(self):
        """Tcl-only syntax exercise; NOT a Vivado/XSim functional test."""
        tclsh=shutil.which('tclsh')
        if not tclsh:self.skipTest('tclsh not installed')
        source=self.root/'stub.tcl'
        source.write_text('''
proc add_condition {opt name expression block} {set ::guard_body $block}
proc stop {} {}
proc run {args} {if {$args eq "all"} {uplevel #0 $::guard_body}}
proc remove_conditions {args} {}
proc get_value {args} {return "1"}
proc vwait {name} {error "STUB_REACHED_PAUSE"}
''' + self.tcl,encoding='ascii')
        proc=subprocess.run([tclsh,str(source)],capture_output=True,text=True,timeout=5)
        self.assertIn('STUB_REACHED_PAUSE',proc.stderr)
        self.assertTrue(p.handshake_file_valid(self.receipt))
        self.assertFalse((self.root/'receipt.txt.tmp').exists())


class EvidenceClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.receipt=self.root/'receipt.txt';self.receipt.write_bytes(p.PAUSE_TEXT.encode())
        self.log=self.root/'fi.log';self.log.write_text('FI_APPLIED scenario=FI_P400_0167\n')
        self.proc={'termination_requested':True,'external_timeout':False,
                   'owned_active_processes_after':0,'child_started':True,
                   'tcl_handshake_seen':True,'tcl_handshake_valid':True,
                   'log_marker_confirmed_before_termination':True,
                   'postfi_kill_cause':'ACK_AND_MARKER',
                   'owned_active_processes_before_termination':6}

    def test_complete_proof(self):
        self.assertEqual(p.classify_probe(self.proc,self.receipt,self.log),p.PROBE_MARKER)

    def test_no_conflation_with_seventh_attempt(self):
        bad=dict(self.proc,tcl_handshake_valid=False,postfi_kill_cause='INVALID_ACK',
                 log_marker_confirmed_before_termination=False)
        self.receipt.write_bytes(p.PAUSE_TEXT.encode().replace(b'\n',b'\r\n'))
        self.assertEqual(p.classify_probe(bad,self.receipt,self.log),
                         'POSTFI_TCL_PAUSE_NOT_REPRODUCED')

    def test_no_result_is_required(self):
        self.log.write_text(self.log.read_text()+'FI_RESULT_V2,FI_P400_0167,...\n')
        self.assertNotEqual(p.classify_probe(self.proc,self.receipt,self.log),p.PROBE_MARKER)

    def test_wrong_marker_count_fails(self):
        self.log.write_text(self.log.read_text()*2)
        self.assertNotEqual(p.classify_probe(self.proc,self.receipt,self.log),p.PROBE_MARKER)

    def test_requires_real_termination_and_zero_children(self):
        for key,value in (('termination_requested',False),('external_timeout',True),
                          ('owned_active_processes_after',1),
                          ('owned_active_processes_before_termination',1),
                          ('child_started',False)):
            with self.subTest(key=key):
                self.assertNotEqual(p.classify_probe(dict(self.proc,**{key:value}),
                                  self.receipt,self.log),p.PROBE_MARKER)

    def test_ack_and_marker_only_termination_decision(self):
        self.assertEqual(p.postfi_termination_decision(True,True,(1,0),.1),'ACK_AND_MARKER')
        self.assertEqual(p.postfi_termination_decision(True,False,(1,0),.1),'WAIT')
        self.assertEqual(p.postfi_termination_decision(True,False,(1,0),2),'INVALID_ACK')
        self.assertEqual(p.postfi_termination_decision(False,False,(1,0),5),'WAIT')
        self.assertEqual(p.postfi_termination_decision(True,True,(1,1),5),'NO_MARKER')


class GlobalBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.results=Path(self.temp.name)
        self.previous={
           'v4f4c_healthy_01':('FI_P400_0007','healthy',q.SUCCESS),
           'v4f4c_terminate_before_01':('FI_P400_0014','terminate-before-fi',q.BLOCKED),
           'v4f4c_terminate_after_01':('FI_P400_0167','terminate-after-fi',q.BLOCKED),
           'v4f4c_crash_before_01':('FI_P400_0174','crash-before-commit',q.RUNNING),
           'v4f4c_timeout_01':('FI_P400_0227','external-timeout',q.BLOCKED),
           'v4f4c_recovery_01':('FI_P400_0227','healthy',q.SUCCESS),
           'v4f4c_postfi_det_01':('FI_P400_0167','terminate-after-fi',q.BLOCKED),
        }
        for name,(sid,trial,status) in self.previous.items():
            d=self.results/name;d.mkdir()
            obj={'schema':q.SCHEMA,'max_xsim_processes':1,
                 'xsim_invocations_committed':int(status==q.SUCCESS),
                 'scenario_id':sid,'trial':trial,'status':status}
            if name=='v4f4c_postfi_det_01':
                obj.update(qualification_mode='CNN_PEP_V4F4D_POSTFI_TCL_PAUSE_PROBE_V1',
                           reserved_phase4c_slot=7,
                           failure_reason='POSTFI_TCL_PAUSE_NOT_REPRODUCED',
                           xsim_invocations_committed=0,
                           process={'marker_evidence':[1,0],
                                    'postfi_kill_cause':'INVALID_ACK',
                                    'tcl_handshake_seen':True,'tcl_handshake_valid':False,
                                    'owned_active_processes_after':0})
            (d/'attempt.json').write_text(json.dumps(obj))

    def test_exact_seven_expected_attempts(self):
        self.assertEqual(len(p.historical_attempts(self.results)),7)
        q.enforce_aggregate_budget(self.results)

    def test_missing_old_slot_is_rejected(self):
        (self.results/'v4f4c_postfi_det_01'/'attempt.json').unlink()
        with self.assertRaises(ValueError):p.historical_attempts(self.results)

    def test_malformed_old_slot_is_rejected(self):
        f=self.results/'v4f4c_postfi_det_01'/'attempt.json'
        obj=json.loads(f.read_text());obj['process']['tcl_handshake_valid']=True
        f.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):p.historical_attempts(self.results)

    def test_missing_historical_healthy_denied(self):
        (self.results/'v4f4c_healthy_01'/'attempt.json').unlink()
        with self.assertRaises(ValueError):p.historical_attempts(self.results)

    def test_unknown_extra_denied(self):
        d=self.results/'v4f4c_unknown_01';d.mkdir()
        (d/'attempt.json').write_text(json.dumps({'schema':q.SCHEMA,
                                                  'max_xsim_processes':1,
                                                  'xsim_invocations_committed':0}))
        with self.assertRaises(ValueError):p.historical_attempts(self.results)

    def test_last_slot_counts_against_original_budget(self):
        d=self.results/p.ATTEMPT_NAME;d.mkdir()
        (d/'attempt.json').write_text(json.dumps({'schema':q.SCHEMA,
           'max_xsim_processes':1,'xsim_invocations_committed':0,
           'qualification_mode':p.SCHEMA,'scenario_id':p.SELECTED_ID}))
        self.assertEqual(len(p.historical_attempts(self.results)),8)
        with self.assertRaisesRegex(ValueError,'budget exhausted'):
            q.enforce_aggregate_budget(self.results)

    def test_new_attempt_cannot_reuse_old_path(self):
        self.assertEqual(p.ATTEMPT_NAME,'v4f4c_postfi_det_02')
        self.assertNotEqual(p.ATTEMPT_NAME,old.ATTEMPT_NAME)
        self.assertEqual(p.SELECTED_ID,old.SELECTED_ID)
        self.assertTrue(p.ATTEMPT_NAME.startswith('v4f4c_'))


class AuditSafetyTests(unittest.TestCase):
    def test_audit_is_read_only_and_has_fresh_receipt(self):
        self.assertIn('READ_ONLY_AUDIT',a.AUDIT_SCHEMA)
        self.assertNotEqual(a.AUDIT_SCHEMA,p.SCHEMA)
        self.assertEqual(p.q.global_campaign_lock,q.global_campaign_lock)
        self.assertEqual(p.q.AGGREGATE_ATTEMPT_BUDGET,8)

    def test_rejects_nonexistent_attempt(self):
        with tempfile.TemporaryDirectory() as t:
            repo=Path(t);(repo/'fi/results').mkdir(parents=True)
            result=repo/'fi/results'
            with self.assertRaises(ValueError):
                a.audit_attempt(repo,result,result/p.ATTEMPT_NAME,result/'v4f4e_audit_01')

    def test_refuses_full400(self):
        with mock.patch('sys.argv',['test','--mode','full400','--execute']):
            with self.assertRaisesRegex(SystemExit,'Full400 BLOCKED'):
                p.main()

    def test_refuses_other_scenario(self):
        with mock.patch('sys.argv',['test','--mode','execute','--scenario-id','FI_P400_0001','--execute']):
            with self.assertRaisesRegex(SystemExit,'Only exact frozen scenario'):
                p.main()

    def test_no_windows_execute_on_linux(self):
        if os.name=='nt':self.skipTest('Non-Windows negative path')
        with mock.patch('sys.argv',['test','--mode','execute','--execute']):
            with self.assertRaisesRegex(SystemExit,'requires Windows'):
                p.main()


if __name__=='__main__':unittest.main()

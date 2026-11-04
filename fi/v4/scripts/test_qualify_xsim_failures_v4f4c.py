"""Unit/fixture tests for V4f4C; these tests NEVER launch XSim."""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import qualify_xsim_failures_v4f4c as q
from pilot400_common import FIELDS,read_csv,write_csv,sha256
import audit_v4f4c as aud
import qualify_runner_v4d as v4da

REPO=HERE.parents[2]


class CanarySelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=read_csv(REPO/'fi/scenarios/pilot400.csv',FIELDS)
    def test_exact_frozen_canary(self):
        rows=q.select_canary(self.rows)
        self.assertEqual([x['scenario_id'] for x in rows],list(q.CANARY_IDS))
        self.assertEqual({x['sample_id'] for x in rows},set(q.CANARY_SAMPLES))
        self.assertEqual({x['target_id'] for x in rows},set(q.CANARY_TARGETS))
    def test_no_missing_scenario(self):
        with self.assertRaises(ValueError):q.select_canary(self.rows[:-1])
    def test_no_duplicate_id(self):
        bad=list(self.rows);bad[23]=bad[22]
        with self.assertRaises(ValueError):q.select_canary(bad)
    def test_no_offset_drift(self):
        bad=[dict(row) for row in self.rows]
        bad[6]['injection_offset']='126'
        with self.assertRaises(ValueError):q.select_canary(bad)
    def test_no_dat_pair_drift(self):
        bad=[dict(row) for row in self.rows]
        bad[393]['bit_index']='0'
        with self.assertRaises(ValueError):q.select_canary(bad)
    def test_plan_does_not_launch_xsim(self):
        self.assertEqual(len(q.CANARY_IDS),8)
        self.assertEqual(q.PLAN.count('ZERO_XSIM') if 'ZERO_XSIM' in q.PLAN else 0,1)


class ClassificationTests(unittest.TestCase):
    def test_healthy_rc_zero(self):
        self.assertEqual(q.classify_failure('healthy',0,False,1,1,False),
                         'RAW_RESULT_ELIGIBLE')
    def test_nonzero_external_is_not_design_noncompletion(self):
        self.assertEqual(q.classify_failure('healthy',11,False,1,0,False),
                         'INFRASTRUCTURE_FAILED')
    def test_timeout_not_noncompletion(self):
        self.assertEqual(q.classify_failure('external-timeout',87,True,0,0,True),
                         'INFRASTRUCTURE_TIMEOUT')
    def test_timeout_missed(self):
        self.assertIn('NOT_REPRODUCED',q.classify_failure('external-timeout',0,False,1,1,False))
    def test_before_fi_expected(self):
        self.assertEqual(q.classify_failure('terminate-before-fi',87,False,0,0,True),
                         'INTERRUPTED_PRE_FI_NO_OUTCOME')
    def test_before_fi_late_not_reproduced(self):
        self.assertIn('NOT_REPRODUCED',q.classify_failure('terminate-before-fi',87,False,1,0,True))
    def test_after_fi_expected(self):
        self.assertEqual(q.classify_failure('terminate-after-fi',87,False,1,0,True),
                         'INTERRUPTED_AFTER_FI_NO_RESULT')
    def test_after_fi_late_result_not_reproduced(self):
        self.assertIn('NOT_REPRODUCED',q.classify_failure('terminate-after-fi',87,False,1,1,True))
    def test_after_fi_without_marker_not_reproduced(self):
        self.assertIn('NOT_REPRODUCED',q.classify_failure('terminate-after-fi',87,False,0,0,True))
    def test_unexpected_trial(self):
        with self.assertRaises(ValueError):q.classify_failure('all400',0,False,1,1,False)
    def test_crash_requires_clean_raw_exit(self):
        self.assertEqual(q.classify_failure('crash-before-commit',0,False,1,1,False),
                         'RAW_RESULT_ELIGIBLE')


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
    def test_zeroed_badges_not_release(self):
        obj={'full400_authorized':False,'full400_runner_qualified':False,
             'full400_executed':False,'scientific_protocol_approved':False,
             'max_xsim_processes':1,'full400_gate':'BLOCKED_G0_G7_REQUIRED'}
        q.no_release(obj)
    def test_auto_approval_rejected(self):
        obj={'full400_authorized':True,'full400_runner_qualified':False,
             'full400_executed':False,'scientific_protocol_approved':False,
             'max_xsim_processes':1,'full400_gate':'BLOCKED_G0_G7_REQUIRED'}
        with self.assertRaises(ValueError):q.no_release(obj)
    def test_unbounded_max_rejected(self):
        obj={'full400_authorized':False,'full400_runner_qualified':False,
             'full400_executed':False,'scientific_protocol_approved':False,
             'max_xsim_processes':400,'full400_gate':'BLOCKED_G0_G7_REQUIRED'}
        with self.assertRaises(ValueError):q.no_release(obj)
    def test_incomplete_pins_rejected(self):
        with self.assertRaises(ValueError):q.check_pins({})
    def test_source_drift_rejected(self):
        p=self.base/'src';p.write_text('a')
        pins={str(p):sha256(p)}
        q.check_pins(pins)
        p.write_text('b')
        with self.assertRaises(ValueError):q.check_pins(pins)
    def test_output_must_be_new_sibling(self):
        repo=self.base
        results=repo/'fi/results';results.mkdir(parents=True)
        weights=repo/'data';weights.mkdir()
        q.safe_out(repo,results,weights,results/'v4f4c_new_01')
        (results/'v4f4c_old_01').mkdir()
        with self.assertRaises(ValueError):q.safe_out(repo,results,weights,results/'v4f4c_old_01')
    def test_wrong_output_prefix(self):
        results=self.base/'fi/results';results.mkdir(parents=True)
        weights=self.base/'data';weights.mkdir()
        with self.assertRaises(ValueError):q.safe_out(self.base,results,weights,results/'v4f2_overwrite')
    def test_marker_counts_not_double_counted(self):
        p=self.base/'log'
        p.write_text('FI_APPLIED scenario=S\nFI_RESULT_V2,A,...\nnot FI_APPLIED\n')
        self.assertEqual(q.marker_counts(p),(1,1))
    def test_missing_marker_file(self):
        self.assertEqual(q.marker_counts(self.base/'missing.log'),(0,0))
    def test_blocked_exec_non_windows(self):
        if os.name=='nt':self.skipTest('Linux fail-closed branch only')
        repo=REPO
        args=argparse.Namespace(execute=True,trial='healthy',scenario_id=q.CANARY_IDS[0])
        with self.assertRaises(ValueError):q.execute(args,repo,repo/'fi/results',repo/'data',
                                                     repo/'fi/results/v4f4c_unsafe',repo/'fi/results/v4e_release_review_01')


class AggregateBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.results=Path(self.temp.name)
    def register(self,i,*,valid=True):
        p=self.results/f'v4f4c_attempt_{i:02d}';p.mkdir()
        obj={'schema':q.SCHEMA if valid else 'CORRUPT',
             'max_xsim_processes':1,'xsim_invocations_committed':0}
        v4da.atomic_write_json(p/'attempt.json',obj)
    def test_zero_attempts_allowed(self):
        q.enforce_aggregate_budget(self.results)
    def test_seven_attempts_allowed(self):
        for i in range(7):self.register(i)
        q.enforce_aggregate_budget(self.results)
    def test_eight_attempts_blocked(self):
        for i in range(8):self.register(i)
        with self.assertRaises(ValueError):q.enforce_aggregate_budget(self.results)
    def test_corrupt_inventory_rejected(self):
        self.register(0,valid=False)
        with self.assertRaises(ValueError):q.enforce_aggregate_budget(self.results)
    def test_orphan_directory_rejected(self):
        p=self.results/'v4f4c_orphan';p.mkdir();(p/'runs').mkdir()
        with self.assertRaises(ValueError):q.enforce_aggregate_budget(self.results)
    def test_exclusive_lock_not_reentrant(self):
        with q.global_campaign_lock(self.results):
            with self.assertRaises(ValueError):
                with q.global_campaign_lock(self.results):pass
    def test_persistent_lock_remains(self):
        with q.global_campaign_lock(self.results):pass
        self.assertTrue((self.results/'.v4f4c_global.lock').is_file())


class CommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.row=q.select_canary(read_csv(REPO/'fi/scenarios/pilot400.csv',FIELDS))[0]
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/'scenario.cmd'
    def make_cmd(self,extra=''):
        r=self.row
        self.file.write_text('call "C:/Vivado/bin/xsim.bat" -tclbatch "run.tcl" '
              + ' '.join(f'-testplusarg "{k}={v}"' for k,v in {
              'FI_OFFSET':r['injection_offset'],'SCENARIO_ID':r['scenario_id'],
              'TARGET_ID':r['target_id'],'FI_BIT':r['bit_index'],
              'SAMPLE_ID':r['sample_id']}.items())+' '+extra)
    def test_good_command(self):
        self.make_cmd();aud.check_command(self.file,self.row)
    def test_wrong_bit(self):
        self.make_cmd();self.file.write_text(self.file.read_text().replace('FI_BIT=2','FI_BIT=1'))
        with self.assertRaises(ValueError):aud.check_command(self.file,self.row)
    def test_duplicate_scenario_plusarg(self):
        self.make_cmd('-testplusarg "SCENARIO_ID=FI_P400_0007"')
        with self.assertRaises(ValueError):aud.check_command(self.file,self.row)
    def test_profile_only_forbidden(self):
        self.make_cmd('-testplusarg "PILOT_PROFILE_ONLY=1"')
        with self.assertRaises(ValueError):aud.check_command(self.file,self.row)
    def test_missing_plusarg(self):
        self.make_cmd();self.file.write_text(self.file.read_text().replace('-testplusarg "FI_OFFSET=125"',''))
        with self.assertRaises(ValueError):aud.check_command(self.file,self.row)


class AuditFixtureTests(unittest.TestCase):
    """All evidence here is SYNTHETIC fixture; never count as actual FI."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        self.results=self.repo/'fi/results';self.results.mkdir(parents=True)
        scenarios=self.repo/'fi/scenarios';scenarios.mkdir(parents=True)
        shutil.copyfile(REPO/'fi/scenarios/pilot400.csv',scenarios/'pilot400.csv')
        (self.repo/'data').mkdir()
        self.src=self.repo/'fake_pin.txt';self.src.write_text('pinned')
        self.rows=q.select_canary(read_csv(self.repo/'fi/scenarios/pilot400.csv',FIELDS))
        self.row=self.rows[0]
        self.attempt=self.results/'v4f4c_fixture_01';self.attempt.mkdir()
        write_csv(self.attempt/'canary_selection.csv',FIELDS,self.rows)
        self.meta={'schema':q.SCHEMA,'status':q.BLOCKED,
              'execution_scope':'ONE_FROZEN_CANARY_XSIM_MAX',
              'source_authority':'vivado','trial':'terminate-before-fi',
              'scenario_id':self.row['scenario_id'],'scenario':self.row,
              'canary_sha256':sha256(self.attempt/'canary_selection.csv'),
              'input_pins':{str(self.src):sha256(self.src)},
              'max_xsim_processes':1,'xsim_invocations_budgeted':1,
              'xsim_invocations_committed':0,
              'full400_authorized':False,'full400_runner_qualified':False,
              'full400_executed':False,'scientific_protocol_approved':False,
              'full400_gate':'BLOCKED_G0_G7_REQUIRED',
              'validation':None,'build':None,'process':None,
              'process_receipt_sha256':None}
        self._commit()
    def _commit(self):v4da.atomic_write_json(self.attempt/'attempt.json',self.meta)
    def _audit(self,suffix='a'):
        return aud.audit_attempt(self.repo,self.results,self.attempt,
                            self.results/('v4f4c_audit_'+suffix))
    def test_blocked_attempt_audit_does_not_count_fi(self):
        record=self._audit()
        self.assertEqual(record['result'],'INVALID_INFRASTRUCTURE_NOT_FI_OUTCOME')
        self.assertEqual(record['xsim_invocations_committed'],0)
    def test_inflight_marker_replayed(self):
        runs=self.attempt/'runs';runs.mkdir()
        cmd=runs/(self.row['scenario_id']+'.cmd')
        r=self.row
        cmd.write_text('call "C:/V/xsim.bat" -tclbatch "test" '+
                       ' '.join(f'-testplusarg "{k}={v}"' for k,v in
                           {'FI_OFFSET':r['injection_offset'],
                            'SCENARIO_ID':r['scenario_id'],
                            'TARGET_ID':r['target_id'],'FI_BIT':r['bit_index'],
                            'SAMPLE_ID':r['sample_id'],
                            'INPUT_FILE':(self.repo/'fi/pilot400/samples'/r['sample_id']/'input.hex').resolve().as_posix(),
                            'POOL1_FILE':(self.repo/'fi/pilot400/samples'/r['sample_id']/'golden_pool1.hex').resolve().as_posix(),
                            'POOL2_FILE':(self.repo/'fi/pilot400/samples'/r['sample_id']/'golden_pool2.hex').resolve().as_posix(),
                            'LOGITS_FILE':(self.repo/'fi/pilot400/samples'/r['sample_id']/'golden.hex').resolve().as_posix(),
                            'PREDICTION_FILE':(self.repo/'fi/pilot400/samples'/r['sample_id']/'prediction.hex').resolve().as_posix()}.items()))
        v4da.atomic_write_json(self.attempt/q.INFLIGHT,{
            'schema':'V4F4C_INFLIGHT_V1','trial':self.meta['trial'],
            'scenario_id':r['scenario_id'],'cmd_sha256':sha256(cmd),
            'max_xsim_processes':1})
        self.assertEqual(self._audit()['xsim_invocations_committed'],0)
        cmd.write_text('tampered')
        with self.assertRaises(ValueError):self._audit('changed')
    def test_modified_meta_release_flag_fails(self):
        self.meta['full400_authorized']=True;self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_changed_canary_fails(self):
        p=self.attempt/'canary_selection.csv'
        p.write_text(p.read_text()+'\n')
        with self.assertRaises(ValueError):self._audit()
    def test_changed_input_pin_fails(self):
        self.src.write_text('changed')
        with self.assertRaises(ValueError):self._audit()
    def test_attempt_inflight_is_not_committed(self):
        self.meta['status']=q.RUNNING;self._commit()
        self.assertEqual(self._audit()['xsim_invocations_committed'],0)
    def test_bad_scenario_row_fails(self):
        self.meta['scenario']['bit_index']='0';self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_malformed_trial_rejected(self):
        self.meta['trial']='full400';self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_duplicate_record_rejected(self):
        self.meta['xsim_invocations_committed']=2;self._commit()
        with self.assertRaises(ValueError):self._audit()
    def test_false_healthy_status_fails(self):
        self.meta['status']=q.SUCCESS;self._commit()
        with self.assertRaises(ValueError):self._audit()


class CliGuards(unittest.TestCase):
    def test_full400_fails_before_any_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'v4f4c_out'
            cp=subprocess.run([sys.executable,str(HERE/'qualify_xsim_failures_v4f4c.py'),
                       '--mode','full400','--execute','--out-dir',str(target)],
                       cwd=REPO,capture_output=True,text=True)
            self.assertNotEqual(cp.returncode,0)
            self.assertIn('FULL400 BLOCKED',cp.stderr)
            self.assertFalse(target.exists())
    def test_execute_without_flag_fails_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'v4f4c_out'
            cp=subprocess.run([sys.executable,str(HERE/'qualify_xsim_failures_v4f4c.py'),
                       '--mode','execute','--out-dir',str(target)],
                       cwd=REPO,capture_output=True,text=True)
            self.assertNotEqual(cp.returncode,0)
            self.assertIn('explicit --execute',cp.stderr)
            self.assertFalse(target.exists())
    def test_plan_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'v4f4c_out'
            cp=subprocess.run([sys.executable,str(HERE/'qualify_xsim_failures_v4f4c.py'),
                       '--mode','plan','--execute','--out-dir',str(target)],
                       cwd=REPO,capture_output=True,text=True)
            self.assertNotEqual(cp.returncode,0)
            self.assertFalse(target.exists())
    @unittest.skipUnless(os.name=='nt','Windows private job test; no XSim')
    def test_windows_job_object_owns_only_spawned_child(self):
        p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],
                    creationflags=subprocess.CREATE_NO_WINDOW)
        job=None
        try:
            job=q.WindowsKillJob(p)
            self.assertGreaterEqual(job.active(),1)
            job.terminate()
            p.wait(timeout=5)
            self.assertEqual(job.active(),0)
        finally:
            if job:job.close()
            if p.poll() is None:p.kill();p.wait(timeout=5)

if __name__=='__main__':unittest.main()

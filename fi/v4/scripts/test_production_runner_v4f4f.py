#!/usr/bin/env python3
"""Adversarial tests for V4f4F OFFLINE production-contract and release blocker."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE=Path(__file__).resolve().parent
for folder in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))

import production_runner_v4f4f as prod
import audit_production_v4f4f as audit

ROOT=HERE.parents[2]


def args(mode='execute', **values):
    defaults={
        'repo':Path('/path-that-does-not-exist'),
        'results_dir':Path('fi/results'),'weights_dir':Path('data'),
        'out_dir':Path('fi/results/v4f4f_should_not_be_created'),
        'mode':mode,'resume':False,'pause_after':0,
        'project':None,'vivado':None,'execute':False,'approve':False,
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


class StaticPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=prod.frozen_schedule(ROOT)

    def test_400_exact_rows(self):self.assertEqual(len(self.rows),400)
    def test_16_batches(self):self.assertEqual(len(prod.shadow.schedule_bundles(self.rows)),16)
    def test_every_batch_has_25(self):
        self.assertEqual([r['batch_number'] for r in self.rows[::25]],[str(i) for i in range(1,17)])
    def test_first_last_id(self):
        self.assertEqual((self.rows[0]['scenario_id'],self.rows[-1]['scenario_id']),
                         ('FI_P400_0001','FI_P400_0400'))
    def test_all_unique(self):
        self.assertEqual(len({x['scenario_id'] for x in self.rows}),400)
    def test_class_balance(self):
        self.assertEqual(Counter(x['target_class'] for x in self.rows),
                         {'CTL':100,'BUF':100,'MEM':100,'DAT':100})
    def test_image_balance(self):
        self.assertEqual(Counter(x['sample_id'] for x in self.rows),
                         {f'IMG{i:04d}':40 for i in range(10)})
    def test_dat0010_frozen_pair(self):
        dat=[x for x in self.rows if x['target_id']=='DAT_0010']
        self.assertEqual(Counter(x['bit_index'] for x in dat),{'0':10,'1':10})
        self.assertEqual({x['injection_offset'] for x in dat},{'1340'})
    def test_exact_ten_plusargs(self):
        x=prod.expected_plusargs(self.rows[0],ROOT)
        self.assertEqual(tuple(x),prod.PLUSARG_KEYS)
        self.assertNotIn('PILOT_PROFILE_ONLY',x)
    def test_plusargs_against_row(self):
        x=prod.expected_plusargs(self.rows[0],ROOT)
        prod.assert_plusargs(x,self.rows[0],ROOT)
    def test_plusarg_missing_denied(self):
        x=prod.expected_plusargs(self.rows[0],ROOT);x.pop('LOGITS_FILE')
        with self.assertRaises(ValueError):prod.assert_plusargs(x,self.rows[0],ROOT)
    def test_plusarg_extra_denied(self):
        x=prod.expected_plusargs(self.rows[0],ROOT);x['PILOT_PROFILE_ONLY']='1'
        with self.assertRaises(ValueError):prod.assert_plusargs(x,self.rows[0],ROOT)
    def test_plusarg_wrong_offset_denied(self):
        x=prod.expected_plusargs(self.rows[0],ROOT);x['FI_OFFSET']='999'
        with self.assertRaises(ValueError):prod.assert_plusargs(x,self.rows[0],ROOT)
    def test_plusarg_wrong_sample_path_denied(self):
        x=prod.expected_plusargs(self.rows[0],ROOT);x['INPUT_FILE']='/tmp/forged.hex'
        with self.assertRaises(ValueError):prod.assert_plusargs(x,self.rows[0],ROOT)
    def test_v4f_source_pins_pass(self):
        pins=prod.pinned_inputs(ROOT,ROOT/'data')
        self.assertGreaterEqual(len(pins),55)
        prod.check_pins(pins)
    def test_mutating_schedule_changes_digest(self):
        x=copy.deepcopy(self.rows);x[0]['bit_index']='1'
        self.assertNotEqual(prod.schedule_digest(x),prod.schedule_digest(self.rows))
    def test_event_has_no_fi_fields(self):
        e=prod.event_for(self.rows[0],0,'0'*64)
        self.assertNotIn('outcome',e)
        self.assertFalse(e['simulation_executed'])
        self.assertFalse(e['fault_injected'])
        self.assertFalse(e['raw_xsim_log_exists'])
    def test_source_lock_has_10_keys(self):
        self.assertEqual(len(prod.PLUSARG_KEYS),10)
    def test_never_use_profile_plusarg(self):
        self.assertNotIn('PILOT_PROFILE_ONLY',prod.PLUSARG_KEYS)


class ReleaseDenyTests(unittest.TestCase):
    def test_execute_denied_before_path_check(self):
        with self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
            prod.dispatch(args(mode='execute'))
    def test_full400_denied_before_path_check(self):
        with self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
            prod.dispatch(args(mode='full400'))
    def test_plan_with_execute_flag_denied(self):
        with self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
            prod.dispatch(args(mode='plan',execute=True))
    def test_plan_with_approve_flag_denied(self):
        with self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
            prod.dispatch(args(mode='plan',approve=True))
    def test_full400_rejected_even_when_approve_provided(self):
        with self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
            prod.dispatch(args(mode='full400',execute=True,approve=True))
    def test_audit_execute_denied(self):
        with self.assertRaisesRegex(ValueError,'forbids execute'):
            audit.dispatch(args(mode='release-review',execute=True))
    def test_audit_approve_denied(self):
        with self.assertRaisesRegex(ValueError,'forbids execute'):
            audit.dispatch(args(mode='release-review',approve=True))
    def test_path_symlink_denied_before_resolve(self):
        with tempfile.TemporaryDirectory() as t:
            src=Path(t)/'file';src.write_text('hello')
            link=Path(t)/'link'
            try:link.symlink_to(src)
            except (OSError,NotImplementedError):self.skipTest('Symlinks unavailable')
            with self.assertRaisesRegex(ValueError,'Symlink'):
                prod.path_from(Path(t),link)


class LedgerTransactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=prod.frozen_schedule(ROOT)

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.repo=Path(temp.name)/'repo'
        self.results=self.repo/'fi/results';self.results.mkdir(parents=True)
        (self.repo/'data').mkdir()
        self.out=self.results/'v4f4f_rehearsal_tests'
        # Synthetic local source pin fixture: every byte is checked on resume.
        self.pins={}
        for i in range(56):
            p=self.repo/f'pin_{i:03d}.txt';p.write_text(f'source-{i}',encoding='utf-8')
            self.pins[str(p.resolve())]=prod.sha256(p)
        self.meta=prod.make_metadata(self.out,self.rows,self.pins,self.repo)

    def verify(self,complete=False):
        prod.verify_ledger(self.out,self.rows,self.pins,self.meta,complete=complete)

    def step(self,n=3):
        with redirect_stdout(io.StringIO()):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta,pause_after=n)
        self.meta=prod.strict_json(self.out/prod.LEDGER_NAME)
        return self.meta

    def finish(self):
        with redirect_stdout(io.StringIO()):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta)
        self.meta=prod.strict_json(self.out/prod.LEDGER_NAME)
        return self.meta

    def test_initial_plan(self):self.verify()
    def test_zero_real_fi(self):self.assertEqual(self.meta['real_fi_scenarios_executed'],0)
    def test_zero_new_xsim(self):self.assertEqual(self.meta['xsim_runs_committed'],0)
    def test_no_release(self):self.assertIs(self.meta['full400_authorized'],False)
    def test_8_attempt_budget_exhausted(self):
        self.assertIs(self.meta['attempt_budget_8_of_8_exhausted'],True)
    def test_schedule_unchanged(self):
        self.assertEqual(prod.read_csv(self.out/'schedule_400.csv',prod.SCHEDULE_FIELDS),self.rows)
    def test_rehearsal_first_3(self):
        self.step(3);self.assertEqual(self.meta['synthetic_events_committed'],3)
        self.verify()
    def test_rehearsal_cross_batch(self):
        self.step(26);self.assertEqual(self.meta['records'][25]['batch_number'],2)
    def test_pause_at_137(self):
        self.step(137);self.assertEqual(self.meta['records'][-1]['scenario_id'],'FI_P400_0137')
    def test_resume_3_to_400(self):
        self.step(3);self.finish();self.verify(complete=True)
        self.assertEqual(self.meta['synthetic_events_committed'],400)
    def test_resume_137_to_400(self):
        self.step(137);self.finish();self.verify(complete=True)
        self.assertEqual(self.meta['synthetic_events_committed'],400)
    def test_audit_rehearsal_readonly(self):
        self.step(3);self.finish()
        before=prod.sha256(self.out/prod.LEDGER_NAME)
        with patch.object(prod,'frozen_schedule',return_value=self.rows), \
             patch.object(prod,'pinned_inputs',return_value=self.pins):
            obj=audit.audit_rehearsal(self.repo,self.results,self.out)
        self.assertEqual(obj['synthetic_events_rehashed'],400)
        self.assertEqual(before,prod.sha256(self.out/prod.LEDGER_NAME))
    def test_missing_checkpoint_denied(self):
        self.step(3);self.meta['records'].pop()
        with self.assertRaises(ValueError):self.verify()
    def test_duplicate_event_id_denied(self):
        self.step(3);self.meta['records'][1]['scenario_id']=self.meta['records'][0]['scenario_id']
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_batch_denied(self):
        self.step(26);self.meta['records'][25]['batch_number']=1
        with self.assertRaises(ValueError):self.verify()
    def test_fake_outcome_field_denied(self):
        self.step(1)
        event=self.out/prod.DATA_FOLDER/'FI_P400_0001.json'
        obj=json.loads(event.read_text());obj['outcome']='FINAL_OUTPUT_MATCH'
        event.write_bytes(prod.encoded(obj))
        with self.assertRaises(ValueError):self.verify()
    def test_tampered_event_byte_denied(self):
        self.step(1);p=self.out/prod.DATA_FOLDER/'FI_P400_0001.json'
        p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.verify()
    def test_orphan_event_denied(self):
        self.step(1);p=self.out/prod.DATA_FOLDER/'FI_P400_0002.json'
        p.write_text('{}',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'Orphan'):
            self.verify()
    def test_missing_event_denied(self):
        self.step(1);(self.out/prod.DATA_FOLDER/'FI_P400_0001.json').unlink()
        with self.assertRaises(ValueError):self.verify()
    def test_extra_raw_log_denied(self):
        self.step(1);(self.out/'unexpected_fi.log').write_text('FI_APPLIED',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'Unexpected root'):
            self.verify()
    def test_symlink_event_denied(self):
        self.step(1)
        p=self.out/prod.DATA_FOLDER/'FI_P400_0001.json'
        p_bak=p.with_name('backup')
        p.rename(p_bak)
        try:p.symlink_to(p_bak)
        except (OSError,NotImplementedError):self.skipTest('Symlink unsupported')
        with self.assertRaises(ValueError):self.verify()
    def test_inflight_denied(self):
        self.step(1);(self.out/'inflight.json').write_text('orphan',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'Uncommitted'):
            self.verify()
    def test_atomic_temp_denied(self):
        self.step(1);(self.out/(prod.LEDGER_NAME+'.tmp')).write_text('half',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'Uncommitted'):
            self.verify()
    def test_source_hash_drift_denied(self):
        self.step(1);p=next(iter(self.pins));Path(p).write_text('changed')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.verify()
    def test_source_pin_metadata_drift_denied(self):
        self.meta['input_pins'][next(iter(self.pins))]='0'*64
        with self.assertRaises(ValueError):self.verify()
    def test_frozen_schedule_sha_drift_denied(self):
        p=self.out/'schedule_400.csv';p.write_text(p.read_text()+'\n')
        with self.assertRaises(ValueError):self.verify()
    def test_source_semantic_sha_drift_denied(self):
        self.meta['schedule_semantic_sha256']='0'*64
        with self.assertRaises(ValueError):self.verify()
    def test_forced_authorization_denied(self):
        self.meta['full400_authorized']=True
        with self.assertRaisesRegex(ValueError,'unauthorized'):
            self.verify()
    def test_fake_g6_qualification_denied(self):
        self.meta['G6_production']='QUALIFIED'
        with self.assertRaisesRegex(ValueError,'unauthorized'):
            self.verify()
    def test_fake_g7_seal_denied(self):
        self.meta['G7']='APPROVED'
        with self.assertRaisesRegex(ValueError,'unauthorized'):
            self.verify()
    def test_fake_xsim_run_denied(self):
        self.meta['xsim_runs_committed']=1
        with self.assertRaisesRegex(ValueError,'unauthorized'):
            self.verify()
    def test_fake_power_loss_proof_denied(self):
        self.meta['power_loss_recovery_proved']=True
        with self.assertRaisesRegex(ValueError,'unauthorized'):
            self.verify()
    def test_source_authority_fake_denied(self):
        self.meta['source_authority']='VIVADO_PROVEN'
        with self.assertRaisesRegex(ValueError,'unauthorized'):
            self.verify()
    def test_lock_symlink_denied(self):
        target=self.repo/'testpin';target.write_text('x')
        try:(self.out/prod.LOCK_FILE).symlink_to(target)
        except (OSError,NotImplementedError):self.skipTest('Symlink unavailable')
        with self.assertRaises(ValueError):
            with prod.exclusive_lock(self.out):pass
    def test_symlink_lock_rejected_by_readonly_verifier(self):
        self.step(1)
        lock=self.out/prod.LOCK_FILE
        if lock.exists():lock.unlink()
        target=self.repo/'not_a_lock';target.write_text('x')
        try:lock.symlink_to(target)
        except (OSError,NotImplementedError):self.skipTest('Symlink unavailable')
        with self.assertRaisesRegex(ValueError,'Symlinked ledger lock'):
            self.verify()
    def test_nested_concurrent_writer_rejected(self):
        with prod.exclusive_lock(self.out):
            with self.assertRaisesRegex(ValueError,'concurrent writer'):
                with prod.exclusive_lock(self.out):
                    pass
    def test_pause_after_400_denied(self):
        with self.assertRaisesRegex(ValueError,'pause-after'):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta,pause_after=400)
    def test_pause_after_negative_denied(self):
        with self.assertRaisesRegex(ValueError,'pause-after'):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta,pause_after=-1)
    def test_resume_pause_at_lower_prefix_denied(self):
        self.step(3)
        with self.assertRaisesRegex(ValueError,'pause-after'):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta,pause_after=2)
    def test_double_start_same_output_denied(self):
        with self.assertRaises(ValueError):
            prod.make_metadata(self.out,self.rows,self.pins,self.repo)
    def test_complete_cannot_be_resumed(self):
        self.finish()
        with self.assertRaises(ValueError):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta)
    def test_rehearsal_crash_before_receipt_denied(self):
        def fail(stage,i):
            if stage=='inflight':raise RuntimeError('synthetic crash')
        with self.assertRaisesRegex(RuntimeError,'synthetic crash'):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta,hook=fail)
        with self.assertRaises(ValueError):
            prod.verify_ledger(self.out,self.rows,self.pins,
                               prod.strict_json(self.out/prod.LEDGER_NAME))
    def test_rehearsal_crash_after_receipt_denied(self):
        def fail(stage,i):
            if stage=='receipt':raise RuntimeError('synthetic crash')
        with self.assertRaisesRegex(RuntimeError,'synthetic crash'):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta,hook=fail)
        with self.assertRaises(ValueError):
            prod.verify_ledger(self.out,self.rows,self.pins,
                               prod.strict_json(self.out/prod.LEDGER_NAME))
    def test_rehearsal_crash_after_checkpoint_denied(self):
        def fail(stage,i):
            if stage=='checkpoint':raise RuntimeError('synthetic crash')
        with self.assertRaisesRegex(RuntimeError,'synthetic crash'):
            prod.rehearsal(self.out,self.rows,self.pins,self.meta,hook=fail)
        with self.assertRaises(ValueError):
            prod.verify_ledger(self.out,self.rows,self.pins,
                               prod.strict_json(self.out/prod.LEDGER_NAME))
    def test_nonserial_event_chain_denied(self):
        self.step(2);p=self.out/prod.DATA_FOLDER/'FI_P400_0002.json'
        event=json.loads(p.read_text());event['previous_event_sha256']='0'*64
        p.write_bytes(prod.encoded(event))
        self.meta['records'][1]['event_sha256']=prod.sha256(p)
        with self.assertRaises(ValueError):self.verify()
    def test_bogus_stage_fields_denied(self):
        self.meta['execution_scope']='REAL_XSIM'
        with self.assertRaises(ValueError):self.verify()
    def test_unexpected_symlink_root_denied(self):
        self.step(1);target=self.repo/'target';target.write_text('x')
        try:(self.out/'linked').symlink_to(target)
        except (OSError,NotImplementedError):self.skipTest('Symlink unavailable')
        with self.assertRaises(ValueError):self.verify()


class ArchiveSecurityTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.archive=self.root/'v4f4c_g6_evidence_01.zip'

    def make_zip(self,entries:list[tuple[str,bytes]]):
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,data in entries:z.writestr(name,data)
        return prod.sha256(self.archive)

    def check(self,entries,expected_success):
        h=self.make_zip(entries)
        with patch.dict(audit.HISTORICAL_HASHES,{'v4f4c_g6_evidence_01.zip':h}):
            if expected_success:
                return audit.archive_items(self.archive)
            with self.assertRaises(ValueError):audit.archive_items(self.archive)

    def test_simple_archive_reads(self):
        self.assertEqual(self.check([('folder/ok.txt',b'test')],True),{'folder/ok.txt':b'test'})
    def test_backslash_paths_are_normalized(self):
        self.assertIn('folder/ok.txt',self.check([('folder\\ok.txt',b'test')],True))
    def test_parent_escape_denied(self):
        self.check([('folder/../secret',b'bad')],False)
    def test_absolute_path_denied(self):
        self.check([('/absolute/file',b'bad')],False)
    def test_single_component_denied(self):
        self.check([('rootfile',b'bad')],False)
    def test_case_collision_denied(self):
        self.check([('folder/Test',b'one'),('folder/test',b'two')],False)
    def test_duplicate_denied(self):
        self.check([('folder/Test',b'one'),('folder/Test',b'two')],False)
    def test_archive_sha_mutation_denied(self):
        self.make_zip([('folder/test',b'one')])
        with self.assertRaises(ValueError):audit.archive_items(self.archive)
    def test_wrong_archive_name_denied(self):
        self.make_zip([('folder/test',b'one')]);p=self.root/'renamed.zip'
        self.archive.rename(p)
        with self.assertRaises(ValueError):audit.archive_items(p)
    def test_zipped_symlink_denied(self):
        with zipfile.ZipFile(self.archive,'w') as z:
            i=zipfile.ZipInfo('folder/link')
            i.create_system=3
            i.external_attr=(0o120777<<16)
            z.writestr(i,b'../secret')
        h=prod.sha256(self.archive)
        with patch.dict(audit.HISTORICAL_HASHES,{'v4f4c_g6_evidence_01.zip':h}):
            with self.assertRaises(ValueError):audit.archive_items(self.archive)

    def test_history_archive_pins_were_frozen(self):
        self.assertRegex(audit.HISTORICAL_HASHES['v4f4c_g6_evidence_01.zip'],r'^[a-f0-9]{64}$')
    def test_exhausted_budget_has_eight_slots(self):
        self.assertEqual(len(audit.HISTORY),8)
    def test_not_reproduced_not_promoted(self):
        self.assertEqual([x[3] for x in audit.HISTORY].count('NOT_REPRODUCED'),2)
    def test_exactly_two_bounded_commits(self):
        self.assertEqual(sum(x[-1] for x in audit.HISTORY),2)


class SourceAndReleaseTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.repo=Path(t.name)/'repo';(self.repo/'fi/results').mkdir(parents=True)
        self.results=self.repo/'fi/results'

    def make_source(self):
        vivado=self.repo/'vivado/bin/vivado.bat';vivado.parent.mkdir(parents=True)
        for name in ('vivado.bat','xvlog.bat','xelab.bat','xsim.bat'):
            (vivado.parent/name).write_text('tool '+name)
        project=self.repo/'Simulation/Simulation.xpr';project.parent.mkdir()
        project.write_text('VIVADO PROJECT',encoding='utf-8')
        (project.parent/'Simulation.srcs').mkdir()
        v3a=self.results/prod.V3A_NAME;v3a.mkdir()
        prj=v3a/'source_vlog.prj';prj.write_text('FROZEN_PRJ',encoding='utf-8')
        source={'x':'frozen'}
        (v3a/'validation_manifest.json').write_text(json.dumps({'snapshot_build':{
            'compile_prj_sha256':prod.sha256(prj),'source_hashes':source}}))
        return project,vivado,source

    def test_source_preflight_positive_with_verified_mock(self):
        project,vivado,src=self.make_source()
        with patch.object(prod,'project_source_hashes',return_value=(src,26)):
            result=prod.check_source_prj(self.repo,self.results,project,vivado)
        self.assertEqual(result['rtl_count'],26)
        self.assertFalse(result['built_snapshot'])
        self.assertFalse(result['full400_authorized'])
        self.assertEqual(result['new_xsim_runs'],0)
    def test_v3a_prj_sha_tamper_denied(self):
        project,vivado,src=self.make_source()
        (self.results/prod.V3A_NAME/'source_vlog.prj').write_text('CHANGED')
        with self.assertRaisesRegex(ValueError,'SHA256'):
            prod.check_source_prj(self.repo,self.results,project,vivado)
    def test_vivado_source_hash_drift_denied(self):
        project,vivado,src=self.make_source()
        with patch.object(prod,'project_source_hashes',return_value=({'fake':'changed'},26)):
            with self.assertRaisesRegex(ValueError,'Vivado-authoritative'):
                prod.check_source_prj(self.repo,self.results,project,vivado)
    def test_missing_toolchain_denied(self):
        project,vivado,src=self.make_source()
        (vivado.parent/'xelab.bat').unlink()
        with self.assertRaisesRegex(ValueError,'Missing pinned'):
            prod.check_source_prj(self.repo,self.results,project,vivado)
    def test_missing_project_denied(self):
        project,vivado,src=self.make_source();project.unlink()
        with self.assertRaisesRegex(ValueError,'Vivado'):
            prod.check_source_prj(self.repo,self.results,project,vivado)
    def test_source_file_path_not_vivado_denied(self):
        project,vivado,src=self.make_source()
        new=vivado.with_name('python.bat');new.write_text('no')
        with self.assertRaisesRegex(ValueError,'Vivado 2022.2'):
            prod.check_source_prj(self.repo,self.results,project,new)
    def test_release_without_receipts_stays_blocked(self):
        report=audit.release_review(self.repo,self.results,None,None,None)
        self.assertFalse(report['full400_authorized'])
        self.assertEqual(report['G6'],'OFFLINE_CONTRACT_NOT_PRODUCTION_QUALIFIED')
        self.assertEqual(report['G7'],'PREPARED_NOT_APPROVED_NO_TWO_PERSON_SEAL_VERIFIED')
    def test_release_gap_missing_evidence(self):
        report=audit.release_review(self.repo,self.results,None,None,None)
        self.assertTrue(all(v['status']=='NOT_SUPPLIED_NO_INFERENCE'
                            for v in report['read_only_evidence_refs'].values()))
    def test_release_rejects_fake_approved_receipt(self):
        source=self.results/'v4f4f_source_01';source.mkdir()
        (source/'v4f4f_source_preflight.json').write_text(json.dumps({
            'schema':prod.SOURCE_SCHEMA,'full400_authorized':True,
            'G6_production':'NOT_QUALIFIED','new_xsim_runs':0}))
        with self.assertRaises(ValueError):audit.release_review(self.repo,self.results,source,None,None)
    def test_release_with_receipts_still_blocked(self):
        names=(('src','v4f4f_source_preflight.json',prod.SOURCE_SCHEMA,'new_xsim_runs'),
               ('hist','v4f4f_historical_audit.json',audit.SCHEMA_HISTORY,'new_xsim_runs_during_audit'),
               ('rehearse','v4f4f_rehearsal_audit.json',audit.SCHEMA_REHEARSAL,'new_xsim_runs_during_audit'))
        paths=[]
        for name,file,schema,zero in names:
            p=self.results/('v4f4f_'+name);p.mkdir()
            obj={'schema':schema,'full400_authorized':False,
                 'G6_production':'NOT_QUALIFIED',zero:0,
                 'attempt_slots_rechecked':8,'raw_artifacts_sha256_rechecked':590,
                 'synthetic_events_rehashed':400}
            (p/file).write_text(json.dumps(obj))
            paths.append(p)
        result=audit.release_review(self.repo,self.results,*paths)
        self.assertIs(result['full400_authorized'],False)
        self.assertFalse(result['G0_G7_signed_authorization'])
    def test_release_rejects_duplicate_json_key(self):
        p=self.results/'fake.json';p.write_text('{"a":1,"a":2}')
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            prod.strict_json(p)
    def test_not_reproduced_historical_gaps_remain(self):
        self.assertEqual([x[0] for x in audit.HISTORY if x[3]=='NOT_REPRODUCED'],
                         ['v4f4c_terminate_after_01','v4f4c_postfi_det_01'])


if __name__=='__main__':unittest.main()

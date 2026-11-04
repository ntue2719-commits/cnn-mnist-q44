#!/usr/bin/env python3
"""V4f4b adversarial offline tests: synthetic scheduler != 400 real XSim FI."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
for p in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))

from pilot400_common import FIELDS,load_plan,verify_manifest,sha256
import qualify_g6_v4f4b as g6

ROOT=Path(__file__).resolve().parents[3]


def frozen_plan():
    plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                   ROOT/'fi/config/fi_targets_candidates_v2.csv')
    return plan,verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan)


class FrozenScheduling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.frozen=frozen_plan()
        cls.rows=g6.schedule(cls.frozen)

    def test_exact_cardinality(self):self.assertEqual(len(self.rows),400)
    def test_exact_batch_count(self):self.assertEqual(len(g6.schedule_bundles(self.rows)),16)
    def test_each_batch_size_25(self):
        self.assertTrue(all(b['tuple_count']==25 for b in g6.schedule_bundles(self.rows)))
    def test_first_boundary(self):
        self.assertEqual((self.rows[0]['scenario_id'],self.rows[0]['batch_number'],
                          self.rows[0]['batch_position']),('FI_P400_0001','1','1'))
    def test_last_boundary(self):
        self.assertEqual((self.rows[-1]['scenario_id'],self.rows[-1]['batch_number'],
                          self.rows[-1]['batch_position']),('FI_P400_0400','16','25'))
    def test_middle_batch_boundary(self):
        self.assertEqual((self.rows[24]['batch_number'],self.rows[25]['batch_number']),('1','2'))
    def test_all_target_class_coverage(self):
        self.assertEqual(Counter(r['target_class'] for r in self.rows),
                         {'CTL':100,'BUF':100,'MEM':100,'DAT':100})
    def test_all_image_coverage(self):
        self.assertEqual(Counter(r['sample_id'] for r in self.rows),
                         {f'IMG{i:04d}':40 for i in range(10)})
    def test_each_frozen_id_once(self):
        self.assertEqual(set(r['scenario_id'] for r in self.rows),
                         {f'FI_P400_{i:04d}' for i in range(1,401)})
    def test_synthetic_event_contains_no_outcome(self):
        event=g6.synthetic_event(self.rows[0],0)
        self.assertNotIn('outcome',event)
        self.assertFalse(event['xsim_executed'])
        self.assertFalse(event['injection_executed'])
        self.assertFalse(event['design_outcome_observed'])
    def test_dat_pins(self):
        dat=[r for r in self.rows if r['target_id']=='DAT_0010']
        self.assertEqual({r['injection_offset'] for r in dat},{'1340'})
        self.assertEqual(Counter(r['bit_index'] for r in dat),{'0':10,'1':10})
    def test_missing_row_rejected(self):
        with self.assertRaises(ValueError):g6.schedule(self.frozen[:-1])
    def test_swapped_rows_rejected(self):
        rows=copy.deepcopy(self.frozen);rows[2],rows[3]=rows[3],rows[2]
        with self.assertRaises(ValueError):g6.schedule(rows)
    def test_duplicate_id_rejected(self):
        rows=copy.deepcopy(self.frozen);rows[1]['scenario_id']=rows[0]['scenario_id']
        with self.assertRaises(ValueError):g6.schedule(rows)
    def test_batch_digest_unique(self):
        batches=g6.schedule_bundles(self.rows)
        self.assertEqual(len({b['batch_sha256'] for b in batches}),16)
    def test_batch_tamper_changes_hash(self):
        edited=copy.deepcopy(self.rows)
        edited[24]['injection_offset']='9999'
        self.assertNotEqual(g6.schedule_fingerprint(edited),g6.schedule_fingerprint(self.rows))
    def test_cross_target_reordered_fingerprint_differs(self):
        edited=copy.deepcopy(self.rows)
        edited[100],edited[101]=edited[101],edited[100]
        self.assertNotEqual(g6.schedule_fingerprint(edited),g6.schedule_fingerprint(self.rows))


class LedgerFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.frozen=frozen_plan()
        cls.rows=g6.schedule(cls.frozen)

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.repo=Path(temp.name)/'repo';self.repo.mkdir()
        self.results=self.repo/'fi/results';self.results.mkdir(parents=True)
        (self.repo/'data').mkdir()
        self.out=self.results/'v4f4b_probe_01'
        self.pin=self.repo/'pinned_source.txt';self.pin.write_text('unchanged',encoding='ascii')
        self.pins={str(self.pin.resolve()):sha256(self.pin)}
        self.parents={'dummy_parent_sha256':'0'*64}
        self.meta=g6.make_metadata(self.out,self.rows,self.pins,self.parents)

    def verify(self,*,complete=False):
        g6.verify_ledger(self.out,self.rows,self.meta,self.pins,self.parents,complete=complete)

    def paused(self,n=3):
        with redirect_stdout(io.StringIO()):
            g6.probe(self.out,self.rows,self.meta,self.pins,self.parents,pause_after=n)
        self.meta=g6.strict_json(self.out/'v4f4b_ledger.json')
        return self.meta

    def complete(self):
        with redirect_stdout(io.StringIO()):
            g6.probe(self.out,self.rows,self.meta,self.pins,self.parents)
        self.meta=g6.strict_json(self.out/'v4f4b_ledger.json')
        return self.meta

    def test_empty_ledger_valid(self):self.verify()
    def test_count_zero(self):self.assertEqual(self.meta['synthetic_events_committed'],0)
    def test_bogus_authorized_flag_rejected(self):
        self.meta['full400_authorized']=True
        with self.assertRaises(ValueError):self.verify()
    def test_bogus_runner_qualified_rejected(self):
        self.meta['full400_runner_qualified']=True
        with self.assertRaises(ValueError):self.verify()
    def test_bogus_full400_executed_rejected(self):
        self.meta['full400_executed']=True
        with self.assertRaises(ValueError):self.verify()
    def test_bogus_science_seal_rejected(self):
        self.meta['scientific_protocol_frozen']=True
        with self.assertRaises(ValueError):self.verify()
    def test_bogus_xsim_count_rejected(self):
        self.meta['new_xsim_runs']=1
        with self.assertRaises(ValueError):self.verify()
    def test_bogus_power_loss_claim_rejected(self):
        self.meta['power_loss_recovery_proved']=True
        with self.assertRaises(ValueError):self.verify()
    def test_batch_contract_tamper_rejected(self):
        self.meta['batch_contract'][0]['start_scenario_id']='FI_P400_9999'
        with self.assertRaises(ValueError):self.verify()
    def test_schedule_csv_tamper_rejected(self):
        p=self.out/'schedule_400.csv';p.write_text(p.read_text()+'\n')
        with self.assertRaises(ValueError):self.verify()
    def test_input_pin_tamper_rejected(self):
        self.pin.write_text('source drift')
        with self.assertRaisesRegex(ValueError,'source/pinned'):
            self.verify()
    def test_metadata_pin_tamper_rejected(self):
        self.meta['input_pins'][str(self.pin.resolve())]='0'*64
        with self.assertRaises(ValueError):self.verify()
    def test_orphan_event_without_checkpoint_rejected(self):
        events=self.out/'probe_events';events.mkdir()
        (events/'FI_P400_0001.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'orphan'):
            self.verify()
    def test_orphan_root_file_rejected(self):
        (self.out/'unexpected.log').write_text('FI_RESULT_V2,forged')
        with self.assertRaisesRegex(ValueError,'unexpected root'):
            self.verify()
    def test_inflight_rejected(self):
        (self.out/'inflight.json').write_text('simulated interrupted call')
        with self.assertRaisesRegex(ValueError,'partial transaction'):
            self.verify()
    def test_tmp_checkpoint_rejected(self):
        (self.out/'v4f4b_ledger.json.tmp').write_text('partial commit')
        with self.assertRaisesRegex(ValueError,'partial transaction'):
            self.verify()
    def test_unexpected_record_rejected(self):
        self.meta['records']=[{'scenario_id':'FI_P400_0001'}]
        with self.assertRaises(ValueError):self.verify()
    def test_duplicate_json_keys_rejected(self):
        p=self.repo/'malformed.json';p.write_text('{"count":1,"count":2}')
        with self.assertRaisesRegex(ValueError,'Duplicate JSON'):
            g6.strict_json(p)
    def test_output_outside_results_rejected(self):
        with self.assertRaisesRegex(ValueError,'sibling'):
            g6.safe_out(self.repo,self.results,self.repo/'foo',exists=False)
    def test_output_overwrite_rejected(self):
        with self.assertRaises(ValueError):
            g6.safe_out(self.repo,self.results,self.out,exists=False)
    def test_out_path_symlink_rejected(self):
        alias=self.results/'v4f4b_alias'
        try:alias.symlink_to(self.out,target_is_directory=True)
        except OSError:self.skipTest('Symlink not permitted by OS')
        with self.assertRaises(ValueError):
            g6.safe_out(self.repo,self.results,alias,exists=True)
    def test_two_writers_rejected(self):
        with g6.exclusive_lock(self.out):
            with self.assertRaisesRegex(ValueError,'already locked'):
                with g6.exclusive_lock(self.out):pass
    def test_paused_prefix_3(self):
        self.paused(3)
        self.assertEqual(self.meta['synthetic_events_committed'],3)
        self.assertEqual(self.meta['status'],g6.PAUSED)
        self.verify()
    def test_paused_boundary_25(self):
        self.paused(25)
        self.assertEqual(self.meta['records'][-1]['scenario_id'],'FI_P400_0025')
        self.verify()
    def test_paused_nonboundary_137(self):
        self.paused(137)
        self.assertEqual(self.meta['records'][-1]['scenario_id'],'FI_P400_0137')
        self.verify()
    def test_resume_3_plus_397(self):
        self.paused(3)
        prior=[sha256(self.out/'probe_events'/(f'FI_P400_{i:04d}.json')) for i in range(1,4)]
        self.complete()
        self.verify(complete=True)
        self.assertEqual(self.meta['synthetic_events_committed'],400)
        self.assertEqual(prior,[sha256(self.out/'probe_events'/(f'FI_P400_{i:04d}.json')) for i in range(1,4)])
    def test_resume_source_drift_refused(self):
        self.paused(3)
        self.pin.write_text('changed')
        with self.assertRaises(ValueError):self.complete()
    def test_resume_rewrite_event_refused(self):
        self.paused(3)
        (self.out/'probe_events/FI_P400_0002.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'corrupted'):
            self.complete()
    def test_resume_extra_event_refused(self):
        self.paused(3)
        (self.out/'probe_events/FI_P400_0004.json').write_text('orphan')
        with self.assertRaisesRegex(ValueError,'orphan'):
            self.complete()
    def test_resume_prefix_tamper_refused(self):
        self.paused(3)
        self.meta['records'][0]['scenario_id']='FI_P400_0400'
        with self.assertRaises(ValueError):self.complete()
    def test_checkpoint_after_400_cannot_resume(self):
        self.complete()
        with self.assertRaisesRegex(ValueError,'cannot be resumed'):
            self.complete()
    def test_orphan_after_event_failure(self):
        def fault(stage,n):
            if stage=='event' and n==2:raise RuntimeError('synthetic interrupted event')
        with self.assertRaisesRegex(RuntimeError,'synthetic interrupted'):
            with redirect_stdout(io.StringIO()):
                g6.probe(self.out,self.rows,self.meta,self.pins,self.parents,hook=fault)
        actual=g6.strict_json(self.out/'v4f4b_ledger.json')
        self.assertEqual(actual['synthetic_events_committed'],1)
        with self.assertRaisesRegex(ValueError,'partial transaction'):
            g6.verify_ledger(self.out,self.rows,actual,self.pins,self.parents)
    def test_crash_after_checkpoint_still_fail_closed(self):
        def fault(stage,n):
            if stage=='checkpoint' and n==2:raise RuntimeError('synthetic post-commit crash')
        with self.assertRaisesRegex(RuntimeError,'post-commit'):
            with redirect_stdout(io.StringIO()):
                g6.probe(self.out,self.rows,self.meta,self.pins,self.parents,hook=fault)
        actual=g6.strict_json(self.out/'v4f4b_ledger.json')
        self.assertEqual(actual['synthetic_events_committed'],2)
        with self.assertRaisesRegex(ValueError,'partial transaction'):
            g6.verify_ledger(self.out,self.rows,actual,self.pins,self.parents)
    def test_crash_after_inflight(self):
        def fault(stage,n):
            if stage=='inflight' and n==1:raise TimeoutError('synthetic process timeout')
        with self.assertRaisesRegex(TimeoutError,'timeout'):
            with redirect_stdout(io.StringIO()):
                g6.probe(self.out,self.rows,self.meta,self.pins,self.parents,hook=fault)
        with self.assertRaisesRegex(ValueError,'partial transaction'):
            self.verify()
    def test_audit_requires_full_400(self):
        self.paused(3)
        with self.assertRaisesRegex(ValueError,'incomplete'):
            g6.review_probe(self.out,self.rows,self.meta,self.pins,self.parents,
                            self.results/'v4f4b_audit_01')
    def test_audit_only_synthetic(self):
        self.complete()
        with redirect_stdout(io.StringIO()):
            report=g6.review_probe(self.out,self.rows,self.meta,self.pins,self.parents,
                                   self.results/'v4f4b_audit_01')
        self.assertEqual(report['synthetic_events_verified'],400)
        self.assertEqual(report['new_xsim_runs'],0)
        self.assertFalse(report['full400_runner_qualified'])
        self.assertFalse(report['full400_authorized'])
        self.assertEqual(report['real_fi_scenarios_executed'],0)
    def test_audit_refuses_overwrite(self):
        self.complete()
        target=self.results/'v4f4b_audit_01';target.mkdir()
        with self.assertRaisesRegex(ValueError,'existing'):
            g6.review_probe(self.out,self.rows,self.meta,self.pins,self.parents,target)
    def test_event_extra_field_rejected(self):
        self.paused(3)
        event=self.out/'probe_events/FI_P400_0001.json'
        raw=json.loads(event.read_text());raw['FI_RESULT_V2']='FAKE'
        event.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError,'corrupted'):
            self.verify()
    def test_plan_fingerprint_not_just_row_count(self):
        wrong=copy.deepcopy(self.rows)
        wrong[100]['bit_index']='99'
        with self.assertRaisesRegex(ValueError,'schedule/source'):
            g6.verify_ledger(self.out,wrong,self.meta,self.pins,self.parents)


    def test_pause_twice_then_finish(self):
        self.paused(25)
        with redirect_stdout(io.StringIO()):
            g6.probe(self.out,self.rows,self.meta,self.pins,self.parents,pause_after=137)
        self.meta=g6.strict_json(self.out/'v4f4b_ledger.json')
        self.assertEqual(self.meta['synthetic_events_committed'],137)
        self.complete()
        self.verify(complete=True)
    def test_pause_before_current_prefix_rejected(self):
        self.paused(25)
        with self.assertRaisesRegex(ValueError,'exceed the committed'):
            g6.probe(self.out,self.rows,self.meta,self.pins,self.parents,pause_after=10)
    def test_pause_at_current_prefix_rejected(self):
        self.paused(25)
        with self.assertRaisesRegex(ValueError,'exceed the committed'):
            g6.probe(self.out,self.rows,self.meta,self.pins,self.parents,pause_after=25)


class CliFailClosed(unittest.TestCase):
    def args(self,**changes):
        defaults=dict(mode='plan',repo=ROOT,results_dir=Path('fi/results'),
            weights_dir=Path('data'),v4e_dir=Path('fi/results/v4e_release_review_01'),
            v4f1_dir=Path('fi/results/v4f_ancestry_01'),
            smoke_dir=Path('fi/results/v4f2_smoke_01'),
            smoke_audit_dir=Path('fi/results/v4f2_smoke_audit_01'),
            v4f3_dir=Path('fi/results/v4f3_release_review_01'),
            g7_prepared_dir=Path('fi/results/v4f4_protocol_prepare_01'),
            out_dir=Path('fi/results/v4f4b_probe_01'),
            probe_dir=None,pause_after=0,resume=False,execute=False,approve=False)
        defaults.update(changes)
        return argparse.Namespace(**defaults)
    def test_forbid_execute_before_io(self):
        with self.assertRaisesRegex(ValueError,'forbids'):
            g6.dispatch(self.args(execute=True))
    def test_forbid_approve_before_io(self):
        with self.assertRaisesRegex(ValueError,'forbids'):
            g6.dispatch(self.args(approve=True))
    def test_forbid_full400_before_io(self):
        with self.assertRaisesRegex(ValueError,'forbids'):
            g6.dispatch(self.args(mode='full400'))
    def test_forbid_resume_plan(self):
        with self.assertRaisesRegex(ValueError,'resume'):
            g6.dispatch(self.args(resume=True))
    def test_forbid_pause_on_plan(self):
        with self.assertRaisesRegex(ValueError,'pause-after'):
            g6.dispatch(self.args(pause_after=3))
    def test_forbid_resume_with_unknown_mode(self):
        with self.assertRaisesRegex(ValueError,'resume'):
            g6.dispatch(self.args(mode='audit',resume=True))


class EndToEndOfflineCli(unittest.TestCase):
    """Process-level dispatch with a mock ANCESTRY preflight, not fake XSim."""
    @classmethod
    def setUpClass(cls):
        _,cls.frozen=frozen_plan()

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.repo=Path(temp.name)/'repo';self.repo.mkdir()
        self.results=self.repo/'fi/results';self.results.mkdir(parents=True)
        (self.repo/'data').mkdir()
        self.pin=self.repo/'pinned.txt';self.pin.write_text('source')
        self.pins={str(self.pin):sha256(self.pin)}
        self.parents={'v4f3_review_sha256':'a'*64}

    def args(self,mode,dir_name='v4f4b_shadow_01',**changes):
        defaults=dict(repo=self.repo,results_dir=Path('fi/results'),weights_dir=Path('data'),
            v4e_dir=Path('fi/results/v4e_release_review_01'),
            v4f1_dir=Path('fi/results/v4f_ancestry_01'),
            smoke_dir=Path('fi/results/v4f2_smoke_01'),
            smoke_audit_dir=Path('fi/results/v4f2_smoke_audit_01'),
            v4f3_dir=Path('fi/results/v4f3_release_review_01'),
            g7_prepared_dir=Path('fi/results/v4f4_protocol_prepare_01'),
            out_dir=Path('fi/results')/dir_name,mode=mode,
            probe_dir=None,pause_after=0,resume=False,execute=False,approve=False)
        defaults.update(changes)
        return argparse.Namespace(**defaults)

    def test_dispatch_plan_then_probe_resume_and_audit(self):
        with patch.object(g6,'trusted_preflight',return_value=(self.frozen,self.pins,self.parents)):
            with redirect_stdout(io.StringIO()):
                plan=g6.dispatch(self.args('plan'))
                self.assertEqual(plan['planned_count'],400)
                paused=g6.dispatch(self.args('probe',resume=True,pause_after=137))
                self.assertEqual(paused['synthetic_events_committed'],137)
                final=g6.dispatch(self.args('probe',resume=True))
                self.assertEqual(final['synthetic_events_committed'],400)
                audited=g6.dispatch(self.args('audit',dir_name='v4f4b_audit_01',
                                                probe_dir=Path('fi/results/v4f4b_shadow_01')))
                self.assertEqual(audited['synthetic_events_verified'],400)
                self.assertFalse(audited['full400_authorized'])

    def test_dispatch_new_probe_not_an_XSim_attempt(self):
        with patch.object(g6,'trusted_preflight',return_value=(self.frozen,self.pins,self.parents)):
            with redirect_stdout(io.StringIO()):
                report=g6.dispatch(self.args('probe',pause_after=3))
        self.assertEqual(report['new_xsim_runs'],0)
        self.assertEqual(report['synthetic_events_committed'],3)
        self.assertEqual(report['execution_scope'],'SYNTHETIC_SCHEDULER_EVENTS_ONLY')

    def test_dispatch_incomplete_audit_denied(self):
        with patch.object(g6,'trusted_preflight',return_value=(self.frozen,self.pins,self.parents)):
            with redirect_stdout(io.StringIO()):
                g6.dispatch(self.args('probe',pause_after=3))
            with self.assertRaisesRegex(ValueError,'incomplete'):
                g6.dispatch(self.args('audit',dir_name='v4f4b_audit_01',
                                     probe_dir=Path('fi/results/v4f4b_shadow_01')))
        self.assertFalse((self.results/'v4f4b_audit_01').exists())

    def test_dispatch_no_overwrite_existing(self):
        with patch.object(g6,'trusted_preflight',return_value=(self.frozen,self.pins,self.parents)):
            with redirect_stdout(io.StringIO()):
                g6.dispatch(self.args('plan'))
            with self.assertRaisesRegex(ValueError,'new'):
                g6.dispatch(self.args('plan'))


if __name__=='__main__': unittest.main()

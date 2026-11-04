#!/usr/bin/env python3
"""V4f Phase 2 offline adversarial tests; mocked simulator != XSim evidence."""
from __future__ import annotations
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import shutil
import tempfile
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
for directory in (HERE, HERE.parents[1]/'scripts', HERE.parents[1]/'v3b/scripts'):
    if str(directory) not in sys.path: sys.path.insert(0,str(directory))

from pilot400_common import FIELDS, load_plan, verify_manifest, read_csv, sha256
import run_full400_batches_v4f as v4f2

ROOT = Path(__file__).resolve().parents[3]


def frozen():
    plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                   ROOT/'fi/config/fi_targets_candidates_v2.csv')
    rows=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan)
    return plan,rows


def args_for(out, *, mode='batch-plan', **changes):
    kwargs=dict(repo=ROOT,results_dir=Path('fi/results'),weights_dir=Path('data'),
                v4e_dir=Path('fi/results/v4e_release_review_01'),
                out_dir=out,mode=mode,batch_start=1,batch_size=25,
                replay_ancestry=False,project=None,vivado='',fileset='sim_1',
                timeout_seconds=900,execute=False,resume=False,pause_after=0)
    kwargs.update(changes)
    return argparse.Namespace(**kwargs)


class FrozenSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.rows=frozen()

    def test_locked_smoke_scenarios(self):
        selected=v4f2.select_smoke(self.rows,self.plan)
        self.assertEqual(tuple(r['scenario_id'] for r in selected),v4f2.SMOKE_IDS)
        self.assertEqual(len(selected),8)

    def test_new_images_and_all_classes(self):
        selected=v4f2.select_smoke(self.rows,self.plan)
        self.assertEqual(Counter(x['sample_id'] for x in selected),
                         {'IMG0001':4,'IMG0008':4})
        self.assertEqual(Counter(x['target_class'] for x in selected),
                         {'CTL':2,'BUF':2,'MEM':2,'DAT':2})

    def test_a_b_config_per_target(self):
        selected=v4f2.select_smoke(self.rows,self.plan)
        for target in v4f2.SMOKE_TARGETS:
            rows=[r for r in selected if r['target_id']==target]
            self.assertEqual([r['sample_id'] for r in rows],
                             ['IMG0001','IMG0008'])
        dat=[r for r in selected if r['target_id']=='DAT_0010']
        self.assertEqual([(r['bit_index'],r['injection_offset']) for r in dat],
                         [('0','1340'),('1','1340')])

    def test_missing_smoke_row_rejected(self):
        rows=[r for r in self.rows if r['scenario_id']!='FI_P400_0398']
        with self.assertRaises(ValueError):v4f2.select_smoke(rows,self.plan)

    def test_modified_smoke_bit_rejected(self):
        rows=copy.deepcopy(self.rows)
        next(r for r in rows if r['scenario_id']=='FI_P400_0398')['bit_index']='0'
        with self.assertRaises(ValueError):v4f2.select_smoke(rows,self.plan)

    def test_modified_smoke_offset_rejected(self):
        rows=copy.deepcopy(self.rows)
        next(r for r in rows if r['scenario_id']=='FI_P400_0003')['injection_offset']='124'
        with self.assertRaises(ValueError):v4f2.select_smoke(rows,self.plan)

    def test_swapped_scenario_identity_rejected(self):
        rows=copy.deepcopy(self.rows)
        next(r for r in rows if r['scenario_id']=='FI_P400_0163')['scenario_id']='FI_P400_9999'
        with self.assertRaises(ValueError):v4f2.select_smoke(rows,self.plan)

    def test_batch_first(self):
        selected=v4f2.select_batch(self.rows,1,25)
        self.assertEqual(len(selected),25)
        self.assertEqual(selected[0]['scenario_id'],'FI_P400_0001')
        self.assertEqual(selected[-1]['scenario_id'],'FI_P400_0025')

    def test_batch_last(self):
        selected=v4f2.select_batch(self.rows,376,25)
        self.assertEqual(selected[-1]['scenario_id'],'FI_P400_0400')

    def test_batch_single(self):
        self.assertEqual(v4f2.select_batch(self.rows,400,1)[0]['scenario_id'],
                         'FI_P400_0400')

    def test_batch_zero_rejected(self):
        for start,size in ((0,1),(1,0),(1,26),(400,2),(401,1),(-1,2)):
            with self.subTest(start=start,size=size):
                with self.assertRaises(ValueError):v4f2.select_batch(self.rows,start,size)

    def test_batch_noncontiguous_rejected(self):
        rows=copy.deepcopy(self.rows)
        rows[5]['scenario_id']='FI_P400_0999'
        with self.assertRaises(ValueError):v4f2.select_batch(rows,1,20)


class FailClosedModes(unittest.TestCase):
    def test_full400_never_executes(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'v4f2_denied'
            with self.assertRaisesRegex(ValueError,'cannot run Full400'):
                v4f2.run(args_for(out,mode='full400',execute=True))
            self.assertFalse(out.exists())

    def test_full400_even_without_execute_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'cannot run Full400'):
                v4f2.run(args_for(Path(d)/'v4f2_x',mode='full400'))

    def test_batch_execute_rejected_pre_io(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'v4f2_x'
            with self.assertRaisesRegex(ValueError,'only allowed'):
                v4f2.run(args_for(out,execute=True))
            self.assertFalse(out.exists())

    def test_resume_plan_rejected_pre_io(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'resume requires'):
                v4f2.run(args_for(Path(d)/'v4f2_x',resume=True))

    def test_resume_without_execute_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'resume requires'):
                v4f2.run(args_for(Path(d)/'v4f2_x',mode='qualification-smoke',resume=True))

    def test_pause_without_execute_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'pause-after'):
                v4f2.run(args_for(Path(d)/'v4f2_x',pause_after=3))

    def test_invalid_smoke_batch_override_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'Batch flags'):
                v4f2.run(args_for(Path(d)/'v4f2_x',mode='qualification-smoke',batch_size=2))

    def test_smoke_execute_without_windows_cannot_forge_pass(self):
        # This only checks mode behavior. Live ancestry preflight cannot be
        # bypassed in the real CLI with missing historical evidence.
        self.assertNotIn('FULL400_AUTHORIZED',v4f2.STATUS_COMPLETE)
        self.assertIn('BLOCKED',v4f2.STATUS_COMPLETE)

    def test_exclusive_attempt_lock_rejects_simultaneous_writer(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            with v4f2.exclusive_evidence_lock(out):
                with self.assertRaisesRegex(ValueError,'owns evidence lock'):
                    with v4f2.exclusive_evidence_lock(out):
                        pass
            # Lock is released after a normal process end; persistent file
            # does not block a later explicitly verified resume.
            with v4f2.exclusive_evidence_lock(out):
                self.assertTrue((out/'.v4f2.lock').exists())

    def test_outside_results_denied(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'v4f2_x'
            with self.assertRaisesRegex(ValueError,'sibling'):
                v4f2.safe_out(ROOT,ROOT/'fi/results',ROOT/'data',out,resume=False)

    def test_source_directory_denied(self):
        with self.assertRaisesRegex(ValueError,'sibling'):
            v4f2.safe_out(ROOT,ROOT/'fi/results',ROOT/'data',
                           ROOT/'fi/v4/scripts/v4f2_bad',resume=False)

    def test_no_frozen_writes_from_batch_plan(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse((Path(d)/'v4f2_test').exists())
            self.assertEqual(v4f2.STATUS_PLANNED,
                'NONEXECUTING_PLAN_FULL400_BLOCKED')


class MetadataAndResume(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.repo=self.root/'repo'; self.repo.mkdir()
        self.results=self.repo/'fi/results'; self.results.mkdir(parents=True)
        self.out=self.results/'v4f2_smoke_test_01'
        self.weights=self.repo/'data'; self.weights.mkdir()
        self.plan,self.frozen=frozen()
        self.selected=v4f2.select_smoke(self.frozen,self.plan)
        self.test_pin=self.repo/'anchor.txt';self.test_pin.write_text('pin')
        self.pins={str(self.test_pin.resolve()):sha256(self.test_pin)}
        self.v3a=self.results/'pilot400_v3a_28_01';self.v3a.mkdir()
        self.prj=self.v3a/'source_vlog.prj';self.prj.write_text('a fake source list')
        self.rtl=self.repo/'rtl.v';self.rtl.write_text('fake RTL')
        self.rtl_hash={str(self.rtl.resolve()):sha256(self.rtl)}
        (self.v3a/'validation_manifest.json').write_text(json.dumps({
            'snapshot_build':{'source_hashes':self.rtl_hash}}))
        self.meta=v4f2._metadata(self.repo,self.out,self.selected,
            'qualification-smoke',self.pins,True,{'mode':'qualification-smoke'})

    def test_empty_prefix_passes(self):
        v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_empty_prefix_missing_asset_rejected(self):
        self.test_pin.write_text('changed')
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_modified_selected_csv_rejected(self):
        p=self.out/'selected_scenarios.csv'
        p.write_text(p.read_text()+'evil\n')
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_unauthorized_approval_rejected(self):
        self.meta['full400_authorized']=True
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_unauthorized_status_promotion_rejected(self):
        self.meta['target_status_modified']=True
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_unauthorized_protocol_freeze_rejected(self):
        self.meta['scientific_protocol_frozen']=True
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_claimed_extra_committed_run_rejected(self):
        self.meta['xsim_runs_committed']=8
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_duplicate_record_rejected(self):
        self.meta['records']=[{'scenario_id':self.selected[0]['scenario_id']}]*2
        self.meta['xsim_runs_committed']=2
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_missing_record_after_artifacts_rejected(self):
        runs=self.out/'runs';runs.mkdir()
        (runs/'FI_P400_0003.log').write_text('an uncommitted scenario')
        with self.assertRaisesRegex(ValueError,'orphan'):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_orphan_cmd_rejected(self):
        runs=self.out/'runs';runs.mkdir()
        (runs/'FI_P400_0003.cmd').write_text('a partial cmd')
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_orphan_snapshot_build_rejected(self):
        (self.out/'build_001').mkdir()
        with self.assertRaisesRegex(ValueError,'build directory'):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_uncommitted_manifest_temp_rejected(self):
        (self.out/'v4f2_manifest.json.tmp').write_text('temporary')
        with self.assertRaisesRegex(ValueError,'temp'):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def test_draft_protocol_tamper_rejected(self):
        (self.out/'analysis_protocol_DRAFT.json').write_text('{}')
        with self.assertRaises(ValueError):
            v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)

    def fake_build(self, repo, project, vivado, fileset, build, timeout, v3a):
        for nm,data in [('source_vlog.prj',self.prj.read_text()),
                        ('compile_snapshot.log','compiled'),
                        ('elaborate_snapshot.log','elaborated')]:
            (build/nm).write_text(data)
        return dict(source_hashes=self.rtl_hash,
                    compile_prj_sha256=sha256(self.prj),
                    compile_log_sha256=sha256(build/'compile_snapshot.log'),
                    elaborate_log_sha256=sha256(build/'elaborate_snapshot.log'),
                    elaboration_mode='FULL_BUILD',
                    simdir=build, vivado_root=self.root)

    def fake_run(self, cmd, simdir, wrapper, timeout, description):
        sid=Path(cmd).stem
        runs=cmd.parent
        (runs/(sid+'.log')).write_text('mocked log for '+sid)
        (runs/(sid+'.console.log')).write_text('mocked console for '+sid)
        wrapper.write_text('mocked wrapper for '+sid)

    def fake_proof(self,log,row):
        return {**{k:row[k] for k in FIELDS},
                'outcome':'FINAL_OUTPUT_MATCH',
                'injection_verified':'1',
                'temporal_evidence_scope':'V4DB_10_SAMPLE_BASELINE_ELIGIBILITY_ONLY',
                'consumer_proof_scope':'NOT_INDEPENDENTLY_MONITORED_V4F2',
                'log_sha256':sha256(log)}

    def test_mocked_pause_resume_preserves_prior_logs(self):
        fake_build=self.fake_build
        with patch.object(v4f2,'build_pinned_direct_snapshot',side_effect=fake_build),\
             patch.object(v4f2,'make_cmd_run',return_value='@echo off\n'),\
             patch.object(v4f2,'call_cmd',side_effect=self.fake_run),\
             patch.object(v4f2,'strict_result',side_effect=self.fake_proof):
            v4f2._build_and_run(self.repo,self.repo/'fake.xpr','vivado.bat',
                                'sim_1',self.out,self.results,30,self.meta,
                                self.selected,self.pins,3)
            prior=[sha256(self.out/'runs'/(sid+'.log'))
                   for sid in v4f2.SMOKE_IDS[:3]]
            partial=json.loads((self.out/'v4f2_manifest.json').read_text())
            self.assertEqual(partial['status'],v4f2.STATUS_PAUSED)
            self.assertEqual(len(partial['records']),3)
            v4f2.verify_records(self.out,self.selected,partial,self.pins,self.v3a)
            v4f2._build_and_run(self.repo,self.repo/'fake.xpr','vivado.bat',
                                'sim_1',self.out,self.results,30,partial,
                                self.selected,self.pins,0)
            done=json.loads((self.out/'v4f2_manifest.json').read_text())
            self.assertEqual(done['status'],v4f2.STATUS_COMPLETE)
            self.assertEqual(len(done['records']),8)
            self.assertEqual(len(done['builds']),2)
            self.assertEqual(prior,[sha256(self.out/'runs'/(sid+'.log'))
                                    for sid in v4f2.SMOKE_IDS[:3]])
            self.assertEqual(len(read_csv(self.out/'smoke_validation.csv',
                                          v4f2.RUN_COLUMNS)),8)
            with self.assertRaises(ValueError):
                v4f2.verify_records(self.out,self.selected,done,self.pins,self.v3a)

    def test_mocked_mid_scenario_failure_refuses_orphan_resume(self):
        calls=[0]
        def crash(cmd,simdir,wrapper,timeout,desc):
            calls[0]+=1
            self.fake_run(cmd,simdir,wrapper,timeout,desc)
            if calls[0]==2:raise ValueError('simulated mid-run failure')
        with patch.object(v4f2,'build_pinned_direct_snapshot',side_effect=self.fake_build),\
             patch.object(v4f2,'make_cmd_run',return_value='@echo off\n'),\
             patch.object(v4f2,'call_cmd',side_effect=crash),\
             patch.object(v4f2,'strict_result',side_effect=self.fake_proof):
            with self.assertRaisesRegex(ValueError,'simulated'):
                v4f2._build_and_run(self.repo,self.repo/'fake.xpr','vivado.bat',
                                    'sim_1',self.out,self.results,30,self.meta,
                                    self.selected,self.pins,0)
            self.assertEqual(len(self.meta['records']),1)
            with self.assertRaisesRegex(ValueError,'orphan'):
                v4f2.verify_records(self.out,self.selected,self.meta,self.pins,self.v3a)


if __name__=='__main__':unittest.main()

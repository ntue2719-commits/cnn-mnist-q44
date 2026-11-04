"""Offline V4d-A canary tests. All files/logs in these tests are synthetic.

These tests do NOT represent actual XSim execution or V4c audit completion.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qualify_runner_v4d as v
from pilot400_common import FIELDS, load_plan, verify_manifest, sha256, write_csv

ROOT=Path(__file__).resolve().parents[3]


def frozen():
    plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                   ROOT/'fi/config/fi_targets_candidates_v2.csv')
    return plan,verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan)


class CanarySelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.rows=frozen()
        cls.selected=v.canary_selection(cls.rows)

    def test_shape_16(self):
        self.assertEqual(len(self.selected),16)
        self.assertEqual(len(set(x['scenario_id'] for x in self.selected)),16)

    def test_frozen_manifest_order(self):
        indexed={r['scenario_id']:i for i,r in enumerate(self.rows)}
        self.assertEqual([indexed[x['scenario_id']] for x in self.selected],
                         sorted(indexed[x['scenario_id']] for x in self.selected))

    def test_four_classes(self):
        self.assertEqual(Counter(x['target_class'] for x in self.selected),
                         {'CTL':4,'BUF':4,'MEM':4,'DAT':4})

    def test_two_samples(self):
        self.assertEqual(Counter(x['sample_id'] for x in self.selected),
                         {'IMG0000':8,'IMG0009':8})

    def test_targets(self):
        self.assertEqual({x['target_id'] for x in self.selected},set(v.CANARY_TARGETS))

    def test_dat0010_bitpair(self):
        for sample in v.CANARY_SAMPLES:
            pair=[x for x in self.selected if x['target_id']=='DAT_0010' and
                  x['sample_id']==sample]
            self.assertEqual({(r['bit_index'],r['injection_offset']) for r in pair},
                             {('0','1340'),('1','1340')})

    def test_missing_scenario_rejected(self):
        missing=[r for r in self.rows if r['scenario_id']!=self.selected[0]['scenario_id']]
        with self.assertRaisesRegex(ValueError,'sixteen'):
            v.canary_selection(missing)

    def test_duplicate_scenario_rejected(self):
        duplicate=list(self.rows)+[dict(self.selected[0])]
        with self.assertRaisesRegex(ValueError,'sixteen'):
            v.canary_selection(duplicate)

    def test_duplicate_pair_rejected(self):
        mutate=deepcopy(self.rows)
        target=[r for r in mutate if r['target_id']=='DAT_0010' and r['sample_id']=='IMG0009']
        target[1]['bit_index']='0'
        with self.assertRaisesRegex(ValueError,'pair missing or duplicated'):
            v.canary_selection(mutate)

    def test_wrong_class_rejected(self):
        mutate=deepcopy(self.rows)
        next(r for r in mutate if r['scenario_id']==self.selected[0]['scenario_id'])['target_class']='DAT'
        with self.assertRaisesRegex(ValueError,'target class mismatch'):
            v.canary_selection(mutate)

    def test_wrong_dat_offset_rejected(self):
        mutate=deepcopy(self.rows)
        next(r for r in mutate if r['scenario_id']=='FI_P400_0381')['injection_offset']='1341'
        with self.assertRaisesRegex(ValueError,'DAT_0010'):
            v.canary_selection(mutate)


class ScopeAndLock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _,all_rows=frozen()
        cls.rows=v.canary_selection(all_rows)

    def test_scope_img0000(self):
        row=next(x for x in self.rows if x['sample_id']=='IMG0000')
        with patch.object(v,'inspect_applied_and_result',return_value={
            'injection_verified':'1','outcome':'WRONG_LABEL'}), patch.object(v,'sha256',return_value='a'*64):
            val=v.strict_result(Path('/fake.log'),row)
        self.assertEqual(val['temporal_evidence_scope'],'PROFILED_IMG0000_ONLY')
        self.assertEqual(val['consumer_proof_scope'],'NOT_INDEPENDENTLY_MONITORED_V4D')

    def test_scope_img0009_not_certified(self):
        row=next(x for x in self.rows if x['sample_id']=='IMG0009')
        with patch.object(v,'inspect_applied_and_result',return_value={
            'injection_verified':'1','outcome':'FINAL_OUTPUT_MATCH'}), patch.object(v,'sha256',return_value='a'*64):
            val=v.strict_result(Path('/fake.log'),row)
        self.assertEqual(val['temporal_evidence_scope'],
                         'OTHER_SAMPLE_TEMPORAL_EXPOSURE_NOT_CERTIFIED')

    def test_reject_injection_not_verified(self):
        with patch.object(v,'inspect_applied_and_result',return_value={
            'injection_verified':'0','outcome':'FINAL_OUTPUT_MATCH'}):
            with self.assertRaisesRegex(ValueError,'mechanism'):
                v.strict_result(Path('/fake.log'),self.rows[0])

    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'manifest.json'
            v.atomic_write_json(path,{'a':1,'records':[]})
            self.assertEqual(json.loads(path.read_text())['a'],1)
            self.assertFalse(path.with_name(path.name+'.tmp').exists())

    def test_input_lock_pass_then_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.v'; p.write_text('v1')
            pins={str(p):sha256(p)}
            v.check_input_lock(pins)
            p.write_text('v2')
            with self.assertRaisesRegex(ValueError,'input changed'):
                v.check_input_lock(pins)

    def test_empty_lock_rejected(self):
        with self.assertRaisesRegex(ValueError,'Missing V4d input lock'):
            v.check_input_lock({})

    def test_report_disclaims_full400(self):
        text=v.report_text({'status':'DRY_RUN_ONLY','records':[]},self.rows)
        self.assertIn('NOT full400',text)
        self.assertIn('NOT certified',text)
        self.assertIn('resume',text)

    def test_no_auto_promotion(self):
        self.assertIn('BLOCKED',v.PASS_STATUS)
        self.assertIn('CANARY',v.SCHEMA)
        self.assertNotIn('PASS_ONE_WINDOW',v.PASS_STATUS)


class ResumeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _,all_rows=frozen()
        cls.rows=v.canary_selection(all_rows)

    def create(self,base,row):
        runs=base/'runs';runs.mkdir(exist_ok=True)
        sid=row['scenario_id']
        for ext in ('.log','.console.log','.wrapper.log','.cmd'):
            (runs/(sid+ext)).write_text(sid+ext)
        val={**{k:row[k] for k in FIELDS},'outcome':'FINAL_OUTPUT_MATCH',
             'injection_verified':'1',
             'temporal_evidence_scope':'PROFILED_IMG0000_ONLY',
             'consumer_proof_scope':'NOT_INDEPENDENTLY_MONITORED_V4D',
             'log_sha256':sha256(runs/(sid+'.log'))}
        record={'scenario_id':sid,'validation':val,'build_id':1}
        for key,ext in [('log_sha256','.log'),('console_sha256','.console.log'),
                        ('wrapper_sha256','.wrapper.log'),('cmd_sha256','.cmd')]:
            record[key]=sha256(runs/(sid+ext))
        return record,val

    def test_one_record_pass(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            rec,val=self.create(out,self.rows[0])
            with patch.object(v,'strict_result',return_value=val), patch.object(v,'check_input_lock'):
                v.verify_done_records(out,self.rows,[rec],{'source':'hash'})

    def test_corrupt_log_hash_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);rec,val=self.create(out,self.rows[0])
            (out/'runs'/(rec['scenario_id']+'.log')).write_text('tampered')
            with self.assertRaisesRegex(ValueError,'artifact missing/changed'):
                v.verify_done_records(out,self.rows,[rec],{'source':'hash'})

    def test_orphan_log_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);rec,val=self.create(out,self.rows[0])
            (out/'runs'/'FI_UNKNOWN.log').write_text('stale')
            with self.assertRaisesRegex(ValueError,'Unrecorded'):
                v.verify_done_records(out,self.rows,[rec],{'source':'hash'})

    def test_missing_console_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);rec,val=self.create(out,self.rows[0])
            (out/'runs'/(rec['scenario_id']+'.console.log')).unlink()
            with self.assertRaisesRegex(ValueError,'artifact missing/changed'):
                v.verify_done_records(out,self.rows,[rec],{'source':'hash'})

    def test_nonprefix_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);rec,val=self.create(out,self.rows[1])
            with self.assertRaisesRegex(ValueError,'contiguous frozen prefix'):
                v.verify_done_records(out,self.rows,[rec],{'source':'hash'})

    def test_bad_replayed_result_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);rec,val=self.create(out,self.rows[0])
            inconsistent={**val,'outcome':'WRONG_LABEL'}
            with patch.object(v,'strict_result',return_value=inconsistent):
                with self.assertRaisesRegex(ValueError,'disagrees'):
                    v.verify_done_records(out,self.rows,[rec],{'source':'hash'})

    def test_two_valid_records(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);a,va=self.create(out,self.rows[0]);b,vb=self.create(out,self.rows[1])
            def read(log,row): return va if row['scenario_id']==a['scenario_id'] else vb
            with patch.object(v,'strict_result',side_effect=read), patch.object(v,'check_input_lock'):
                v.verify_done_records(out,self.rows,[a,b],{'source':'hash'})

    def test_oversized_records_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            with self.assertRaisesRegex(ValueError,'Excess'):
                v.verify_done_records(out,self.rows,[{'scenario_id':'x'}]*17,{'source':'hash'})


class DryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.manifest=frozen()

    def args(self,d):
        return argparse.Namespace(repo=ROOT,results_dir=Path('fi/results'),
            weights_dir=Path('data'),v4c_audit=Path(d)/'v4c.json',
            v4_audit=Path(d)/'v4.json',v4b_a_dir=Path(d)/'v4ba',
            v4b_b_audit=Path(d)/'v4bb.json',v4b_c_dir=Path(d)/'v4bc',
            out_dir=Path(d)/'canary',project=None,vivado='vivado',fileset='sim_1',
            execute=False,resume=False,timeout_seconds=900)

    def test_dryrun_create_only_scenarios_and_block(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(d)
            args.v4c_audit.write_text('{}')
            args.v4_audit.write_text(json.dumps({
                'control10_manifest_sha256':'a'*64,
                'timing_audit_sha256':'b'*64}))
            with patch.object(v,'replay_v4c',return_value={'full400_authorized':False}),\
                 patch.object(v,'locked_inputs',return_value={'mock':'hash'}):
                data=v.run(args)
            self.assertEqual(data['status'],'DRY_RUN_ONLY')
            self.assertEqual(data['records'],[])
            self.assertFalse(data['full400_authorized'])
            self.assertEqual(len(v.read_csv(Path(d)/'canary/selected_scenarios.csv',FIELDS)),16)
            self.assertTrue((Path(d)/'canary/v4d_canary_manifest.json').is_file())

    def test_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(d)
            args.out_dir.mkdir()
            with self.assertRaisesRegex(ValueError,'Refusing to overwrite'):
                v.run(args)

    def test_timeout_too_short_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(d);args.timeout_seconds=1
            with self.assertRaisesRegex(ValueError,'timeout'):
                v.run(args)

    def test_resume_requires_execute(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(d);args.resume=True
            with self.assertRaisesRegex(ValueError,'requires --execute'):
                v.run(args)

    def test_missing_v4c_fails_without_writing_output(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args(d)
            with self.assertRaises(Exception):
                v.run(args)
            self.assertFalse(args.out_dir.exists())


class MockedRunnerLifecycle(unittest.TestCase):
    """Fake process I/O to test orchestration only; no claims about XSim."""
    def run_fake(self,root,fail_at=None):
        out=root/'canary'
        project=root/'Simulation.xpr';project.write_text('fake project')
        v4=root/'v4.json';v4.write_text(json.dumps({
            'control10_manifest_sha256':'a'*64,
            'timing_audit_sha256':'b'*64}))
        v4c=root/'v4c.json';v4c.write_text('{}')
        args=argparse.Namespace(repo=ROOT,results_dir=Path('fi/results'),
            weights_dir=Path('data'),v4c_audit=v4c,v4_audit=v4,
            v4b_a_dir=root/'v4ba',v4b_b_audit=root/'v4bb.json',
            v4b_c_dir=root/'v4bc',out_dir=out,project=project,
            vivado='C:/Vivado/vivado.bat',fileset='sim_1',
            execute=True,resume=False,timeout_seconds=900)
        original_sha=v.sha256
        pinned_source=ROOT/'fi/scenarios/pilot400.csv'
        signatures={str(pinned_source.resolve()):original_sha(pinned_source)}
        simdir=root/'simdir';simdir.mkdir()
        built={'simdir':simdir,'vivado_root':root/'vivado',
               'snapshot':simdir/'xsim.dir'/'tb_cnn_mnist_fi_pilot400_behav',
               'source_hashes':{},'compile_prj_sha256':'a'*64,
               'compile_log_sha256':'b'*64,'elaborate_log_sha256':'c'*64,
               'elaboration_mode':'FULL_BUILD'}
        counter=[0]
        def sha(path):
            if Path(path).name=='source_vlog.prj': return 'a'*64
            return original_sha(path)
        def launch(cmd,cwd,wrapper,timeout,stage):
            counter[0]+=1
            if counter[0]==fail_at:
                raise ValueError('synthetic XSim crash')
            cmd=Path(cmd);sid=cmd.stem
            cmd.with_suffix('.log').write_text('synthetic FI log '+sid)
            (cmd.parent/(sid+'.console.log')).write_text('synthetic console')
            Path(wrapper).write_text('wrapper')
        def parse(log,row):
            return {**{k:row[k] for k in FIELDS},
                    'outcome':'FINAL_OUTPUT_MATCH','injection_verified':'1',
                    'temporal_evidence_scope':(
                        'PROFILED_IMG0000_ONLY' if row['sample_id']=='IMG0000'
                        else 'OTHER_SAMPLE_TEMPORAL_EXPOSURE_NOT_CERTIFIED'),
                    'consumer_proof_scope':'NOT_INDEPENDENTLY_MONITORED_V4D',
                    'log_sha256':original_sha(log)}
        def read(p):
            if Path(p).name=='validation_manifest.json':
                return {'snapshot_build':{'source_hashes':{}}}
            return json.loads(Path(p).read_text())
        with patch.object(v,'replay_v4c',return_value={'full400_authorized':False}),\
             patch.object(v,'locked_inputs',return_value=signatures),\
             patch.object(v,'build_verified_snapshot',return_value=built),\
             patch.object(v,'call_cmd',side_effect=launch),\
             patch.object(v,'strict_result',side_effect=parse),\
             patch.object(v,'read_json',side_effect=read),\
             patch.object(v,'sha256',side_effect=sha):
            if fail_at:
                with self.assertRaisesRegex(ValueError,'synthetic XSim crash'):
                    v.run(args)
                return json.loads((out/'v4d_canary_manifest.json').read_text()),out
            return v.run(args),out

    def test_all_16_mocked_runners_finish_no_full400(self):
        with tempfile.TemporaryDirectory() as d:
            result,out=self.run_fake(Path(d))
            self.assertEqual(result['status'],v.PASS_STATUS)
            self.assertEqual(len(result['records']),16)
            self.assertEqual(len(result['builds']),1)
            self.assertFalse(result['full400_authorized'])
            self.assertFalse(result['full400_runner_qualified'])
            self.assertEqual(len(v.read_csv(out/'canary_validation.csv',v.COLUMNS)),16)

    def test_mocked_crash_preserves_partial_records_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            result,out=self.run_fake(Path(d),fail_at=3)
            self.assertEqual(result['status'],'INTERRUPTED_OR_INVALID')
            self.assertEqual(len(result['records']),2)
            self.assertTrue((out/'runs'/(
                result['records'][0]['scenario_id']+'.log')).is_file())
            self.assertFalse((out/'canary_validation.csv').is_file())
            self.assertFalse(result['full400_authorized'])


if __name__=='__main__':
    unittest.main()

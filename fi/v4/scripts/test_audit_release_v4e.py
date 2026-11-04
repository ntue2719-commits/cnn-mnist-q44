"""Offline-only V4e audit tests. Synthetic evidence is NOT Windows XSim evidence."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import audit_release_v4e as m
from pilot400_common import FIELDS, load_plan, verify_manifest, sha256, read_csv, write_csv

ROOT = Path(__file__).resolve().parents[3]


def frozen():
    p = load_plan(ROOT / 'fi/config/pilot400_targets.csv',
                  ROOT / 'fi/config/fi_targets_candidates_v2.csv')
    return p, verify_manifest(ROOT / 'fi/scenarios/pilot400.csv', p)


def meta_for(stage: str, rows: list[dict[str, str]]) -> dict:
    records = []
    for r in rows:
        h = 'a' * 64
        validation = {**{key: r[key] for key in FIELDS},
                      'outcome': 'WRONG_LOGITS_SAME_LABEL' if r['target_class'] == 'DAT'
                      else 'FINAL_OUTPUT_MATCH',
                      'injection_verified': '1',
                      'consumer_proof_scope': 'NOT_INDEPENDENTLY_MONITORED_V4D',
                      'log_sha256': h}
        records.append({'scenario_id': r['scenario_id'], 'build_id': 1,
                        'validation': validation, 'log_sha256': h})
    return {'records': records}


class FrozenStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, cls.manifest = frozen()
        cls.a = m.v4da.canary_selection(cls.manifest)
        cls.c = m.v4dc.selection(cls.manifest, cls.plan)
        cls.d = m.v4dd.selection(cls.manifest, cls.plan)

    def make_inputs(self):
        return [('V4D_A', meta_for('A', self.a), deepcopy(self.a)),
                ('V4D_C', meta_for('C', self.c), deepcopy(self.c)),
                ('V4D_D', meta_for('D', self.d), deepcopy(self.d))]

    def test_stage_counts(self):
        self.assertEqual((len(self.a), len(self.c), len(self.d)), (16, 8, 8))

    def test_six_samples(self):
        self.assertEqual({r['sample_id'] for r in self.a+self.c+self.d},
                         {'IMG0000','IMG0009','IMG0004','IMG0005','IMG0002','IMG0007'})

    def test_no_duplicate_scenario_ids(self):
        self.assertEqual(len({r['scenario_id'] for r in self.a+self.c+self.d}), 32)

    def test_all_classes(self):
        self.assertEqual(Counter(r['target_class'] for r in self.a+self.c+self.d),
                         {'CTL':8,'BUF':8,'MEM':8,'DAT':8})

    def test_dat0010_bitpair(self):
        dat = [r for r in self.a+self.c+self.d if r['target_id'] == 'DAT_0010']
        self.assertEqual(len(dat), 8)
        self.assertEqual({r['bit_index'] for r in dat}, {'0','1'})
        self.assertEqual({r['injection_offset'] for r in dat}, {'1340'})

    def test_collect_32_verified_only(self):
        rows = m.collect_shakedowns(self.manifest, self.make_inputs())
        self.assertEqual(len(rows), 32)
        self.assertEqual(Counter(r['evidence_stage'] for r in rows),
                         {'V4D_A':16,'V4D_C':8,'V4D_D':8})
        self.assertTrue(all(r['independent_consumer_proof'] == 'NO' for r in rows))
        self.assertTrue(all(r['baseline_temporal_eligibility'].startswith('V4DB_') for r in rows))

    def test_wrong_stage_name_rejected(self):
        data = self.make_inputs()
        data[0] = ('V4D_B', data[0][1], data[0][2])
        with self.assertRaisesRegex(ValueError, 'stage or count'):
            m.collect_shakedowns(self.manifest, data)

    def test_wrong_stage_count_rejected(self):
        data = self.make_inputs()
        data[1] = (data[1][0], data[1][1], data[1][2][:-1])
        with self.assertRaisesRegex(ValueError, 'stage or count'):
            m.collect_shakedowns(self.manifest, data)

    def test_unexpected_stage_sample_rejected(self):
        data = self.make_inputs()
        data[0][2][0]['sample_id'] = 'IMG0007'
        with self.assertRaisesRegex(ValueError, 'unexpected samples'):
            m.collect_shakedowns(self.manifest, data)

    def test_frozen_row_mutation_rejected(self):
        data = deepcopy(self.make_inputs())
        data[2][2][0]['injection_offset'] = '151'
        with self.assertRaisesRegex(ValueError, 'differs from frozen'):
            m.collect_shakedowns(self.manifest, data)

    def test_mechanism_must_be_verified(self):
        data = self.make_inputs()
        data[2][1]['records'][0]['validation']['injection_verified'] = '0'
        with self.assertRaisesRegex(ValueError, 'not independently mechanism verified'):
            m.collect_shakedowns(self.manifest, data)

    def test_no_fake_consumer_proof(self):
        data = self.make_inputs()
        data[2][1]['records'][0]['validation']['consumer_proof_scope'] = 'CONSUMER_PROVED'
        with self.assertRaisesRegex(ValueError, 'not independently mechanism verified'):
            m.collect_shakedowns(self.manifest, data)

    def test_wrong_log_hash_rejected(self):
        data = self.make_inputs()
        data[1][1]['records'][0]['log_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'not independently mechanism verified'):
            m.collect_shakedowns(self.manifest, data)

    def test_record_order_mismatch_rejected(self):
        data = self.make_inputs()
        data[2][1]['records'][:2] = data[2][1]['records'][:2][::-1]
        with self.assertRaisesRegex(ValueError, 'ordering'):
            m.collect_shakedowns(self.manifest, data)

    def test_duplicate_across_stages_rejected(self):
        data = deepcopy(self.make_inputs())
        data[2][2][0] = data[1][2][0]
        data[2][1]['records'][0] = data[1][1]['records'][0]
        with self.assertRaises(ValueError):
            m.collect_shakedowns(self.manifest, data)

    def test_report_preserves_scope(self):
        txt=m.report_text({'status':m.STATUS})
        for key in ('FULL400 GATE: BLOCKED','40/40 IMG0000','400/400',
                    '32/32', 'power loss', 'receipt'):
            self.assertIn(key, txt)


class ReleaseMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name)
        self.completed = p / 'completed'
        self.temporal = p / 'temporal'
        self.completed.mkdir()
        self.temporal.mkdir()
        (self.completed / 'v4dc_resume_manifest.json').write_text('parent')
        (self.temporal / 'v4db_temporal_manifest.json').write_text('timing')
        self.prior = {'input_signature': {'project': 'D:/Simulation.xpr',
                                         'vivado':'D:/vivado.bat','fileset':'sim_1'}}
        self.pins = {'/temp/source.v':'0'*64}
        self.meta = {
            'schema': m.v4dd.SCHEMA, 'status':m.v4dd.STATUS_PASS,
            'full400_gate':m.v4dd.FULL400_GATE,
            'full400_authorized':False, 'full400_runner_qualified':False,
            'target_status_modified':False,
            'run_scope':'V4D_D_EIGHT_FI_INTERRUPTED_CHECKPOINT_ONLY',
            'termination_plan':{'mode':'PYTHON_OS_EXIT_AFTER_COMMITTED_RECORD',
                                'after_n':3,'exit_code':86},
            'builds':[{'build_id':1},{'build_id':2}],
            'records':[{'build_id':x} for x in [1]*3+[2]*5],
            'input_signature':{'pins':self.pins,'project':'D:/Simulation.xpr',
                               'vivado':'D:/vivado.bat','fileset':'sim_1',
                               'selection_scope':'FOUR_CLASSES_SECOND_CONFIG_IMG0002_IMG0007',
                               'v4dc_sha256':sha256(self.completed/'v4dc_resume_manifest.json'),
                               'v4db_sha256':sha256(self.temporal/'v4db_temporal_manifest.json')}
        }

    def verify(self, meta=None):
        m.strict_release_meta(meta or self.meta,self.prior,self.pins,
                              self.completed,self.temporal)

    def test_valid(self):
        self.verify()

    def test_reject_false_success_status(self):
        self.meta['status']='RUNNING'
        with self.assertRaisesRegex(ValueError,'BLOCKED V4d-D'):
            self.verify()

    def test_reject_auto_authorization(self):
        self.meta['full400_authorized']=True
        with self.assertRaisesRegex(ValueError,'BLOCKED V4d-D'):
            self.verify()

    def test_reject_promoted_target(self):
        self.meta['target_status_modified']=True
        with self.assertRaisesRegex(ValueError,'BLOCKED V4d-D'):
            self.verify()

    def test_reject_runner_qualification_claim(self):
        self.meta['full400_runner_qualified']=True
        with self.assertRaisesRegex(ValueError,'BLOCKED V4d-D'):
            self.verify()

    def test_reject_wrong_exit_code(self):
        self.meta['termination_plan']['exit_code']=0
        with self.assertRaisesRegex(ValueError,'exit-code 86'):
            self.verify()

    def test_reject_wrong_checkpoint_count(self):
        self.meta['termination_plan']['after_n']=4
        with self.assertRaisesRegex(ValueError,'3-record'):
            self.verify()

    def test_reject_missing_build(self):
        self.meta['builds']=[{'build_id':1}]
        with self.assertRaisesRegex(ValueError,'three records'):
            self.verify()

    def test_reject_wrong_build_segmentation(self):
        self.meta['records'][2]['build_id']=2
        with self.assertRaisesRegex(ValueError,'three records'):
            self.verify()

    def test_reject_changed_parent_temporal(self):
        (self.temporal/'v4db_temporal_manifest.json').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'signature changed'):
            self.verify()

    def test_reject_wrong_8dot3_source_signature(self):
        self.meta['input_signature']['pins']={'/alternative/source.v':'0'*64}
        with self.assertRaisesRegex(ValueError,'signature changed'):
            self.verify()

    def test_reject_different_vivado(self):
        self.meta['input_signature']['vivado']='D:/another.bat'
        with self.assertRaisesRegex(ValueError,'signature changed'):
            self.verify()

    def test_reject_missing_project(self):
        self.meta['input_signature']['project']=None
        with self.assertRaisesRegex(ValueError,'signature changed'):
            self.verify()


class OutputSafetyTests(unittest.TestCase):
    def test_fresh_directory(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            m.protect_out(p/'new', [p/'evidence'])

    def test_existing_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(ValueError,'refuses to overwrite'):
                m.protect_out(p,[])

    def test_output_inside_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(ValueError,'overlaps'):
                m.protect_out(p/'evidence'/'new',[p/'evidence'])

    def test_output_parent_of_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(ValueError,'overlaps'):
                m.protect_out(p/'new',[p/'new'/'existing'])

    def test_no_windows_evidence_fails_closed_without_creating_output(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'release'
            # Frozen manifest is present; Windows parent evidence does not exist.
            args = __import__('argparse').Namespace(
                repo=ROOT, results_dir=Path(d)/'missing-results',
                weights_dir=Path(d)/'missing-data',
                v4_audit=Path(d)/'v4.json',
                v4b_a_dir=Path(d)/'ba',v4b_b_audit=Path(d)/'bb.json',
                v4b_c_dir=Path(d)/'bc',v4c_audit=Path(d)/'c.json',
                v4d_canary_dir=Path(d)/'ca',v4db_dir=Path(d)/'temporal',
                v4dc_dir=Path(d)/'completed',v4dd_dir=Path(d)/'abrupt',
                out_dir=out)
            with self.assertRaises((ValueError,OSError,FileNotFoundError)):
                m.run(args)
            self.assertFalse(out.exists())



class ReplayArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.v3a = self.root / 'v3a'
        self.v3a.mkdir()
        (self.v3a/'source_vlog.prj').write_text('verified source prj')
        (self.v3a/'validation_manifest.json').write_text(json.dumps({
            'snapshot_build':{'compile_prj_sha256':sha256(self.v3a/'source_vlog.prj'),
                              'source_hashes':{'source.v':'a'*64}}}))
        self.out = self.root/'recovery'
        self.out.mkdir()
        plan, manifest = frozen()
        self.selected = m.v4dd.selection(manifest, plan)
        write_csv(self.out/'selected_scenarios.csv',FIELDS,self.selected)
        self.records=[]
        for r in self.selected:
            val = {**{k:r[k] for k in FIELDS},'outcome':'FINAL_OUTPUT_MATCH',
                   'injection_verified':'1',
                   'temporal_evidence_scope':'V4DB_TEN_SAMPLE_BASELINE_ELIGIBILITY',
                   'consumer_proof_scope':'NOT_INDEPENDENTLY_MONITORED_V4D',
                   'log_sha256':'a'*64}
            self.records.append({'scenario_id':r['scenario_id'],'log_sha256':'a'*64,
                                 'validation':val,'build_id':1})
        write_csv(self.out/'recovery_validation.csv',m.v4dd.COLUMNS,
                  [r['validation'] for r in self.records])
        self.meta={'records':self.records,
                   'selected_sha256':sha256(self.out/'selected_scenarios.csv'),
                   'validation_csv_sha256':sha256(self.out/'recovery_validation.csv'),
                   'builds':[{'build_id':1},{'build_id':2}]}
        for i in (1,2):
            b=self.out/f'build_{i:03d}'
            b.mkdir()
            (b/'runall.tcl').write_bytes(b'run all\r\nquit\r\n')
            for phase in ('compile','elaborate'):
                (b/(phase+'_snapshot.cmd')).write_text('echo compiled')
                # CMD wrapper may legitimately be empty when native stdout redirected
                (b/(phase+'_wrapper.log')).write_bytes(b'')

    def replay(self):
        with patch.object(m.v4dd,'replay_records') as recheck:
            m.check_v4dd_evidence(self.out,self.meta,self.selected,{},self.root,self.v3a)
        recheck.assert_called_once()

    def test_crlf_tcl_empty_wrapper_accepted(self):
        self.replay()

    def test_missing_compiler_cmd_rejected(self):
        (self.out/'build_001/compile_snapshot.cmd').unlink()
        with self.assertRaisesRegex(ValueError,'wrapper missing'):
            self.replay()

    def test_tampered_tcl_rejected(self):
        (self.out/'build_002/runall.tcl').write_text('run 100ns\nquit\n')
        with self.assertRaisesRegex(ValueError,'Tcl changed'):
            self.replay()

    def test_tampered_recovery_csv_rejected(self):
        file = self.out/'recovery_validation.csv'
        file.write_bytes(file.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError,'recovery CSV'):
            self.replay()


class MockedReadOnlyPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, cls.manifest = frozen()

    def test_mocked_pipeline_writes_only_new_review_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            evidence=base/'evidence'
            evidence.mkdir()
            paths={
                'v4': evidence/'v4/v4.json',
                'v4bb': evidence/'v4bb/audit.json',
                'v4c': evidence/'v4c/v4c.json',
                'canary':evidence/'canary',
                'temporal':evidence/'temporal',
                'completed':evidence/'completed',
                'abrupt':evidence/'abrupt',
            }
            for p in (paths['v4'],paths['v4bb'],paths['v4c']):
                p.parent.mkdir(parents=True)
                p.write_text('{}')
            sel_a=m.v4da.canary_selection(self.manifest)
            sel_c=m.v4dc.selection(self.manifest,self.plan)
            sel_d=m.v4dd.selection(self.manifest,self.plan)
            for key,filename,rows in (
                ('canary','v4d_canary_manifest.json',sel_a),
                ('completed','v4dc_resume_manifest.json',sel_c),
                ('abrupt','v4dd_recovery_manifest.json',sel_d)):
                paths[key].mkdir()
                stage_meta = meta_for(key,rows)
                if key == 'abrupt':
                    stage_meta['termination_plan'] = {
                        'mode':'PYTHON_OS_EXIT_AFTER_COMMITTED_RECORD',
                        'after_n':3, 'exit_code':86}
                (paths[key]/filename).write_text(json.dumps(stage_meta))
            paths['temporal'].mkdir()
            (paths['temporal']/'v4db_temporal_manifest.json').write_text('{}')
            args=argparse.Namespace(
                repo=ROOT, results_dir=evidence/'results',
                weights_dir=evidence/'weights',
                v4_audit=paths['v4'], v4b_a_dir=evidence/'ba',
                v4b_b_audit=paths['v4bb'], v4b_c_dir=evidence/'bc',
                v4c_audit=paths['v4c'], v4d_canary_dir=paths['canary'],
                v4db_dir=paths['temporal'], v4dc_dir=paths['completed'],
                v4dd_dir=paths['abrupt'], out_dir=base/'release_output')
            with patch.object(m.v4dd,'verify_v4dc_ancestry',return_value={
                    'input_signature':{'project':'synthetic'}}),\
                 patch.object(m.v4dd,'input_pins',return_value={'mock':'hash'}),\
                 patch.object(m,'strict_release_meta'),\
                 patch.object(m,'check_v4dd_evidence'),\
                 patch.object(m.v4da,'call_cmd',side_effect=AssertionError('XSim forbidden')):
                result=m.run(args)
            self.assertEqual(result['shakedown_mechanism_runs'],32)
            self.assertFalse(result['full400_authorized'])
            self.assertFalse(result['full400_runner_qualified'])
            self.assertEqual(result['baseline_timing_checks'],400)
            matrix=read_csv(args.out_dir/'v4e_shakedown_matrix.csv',m.MATRIX_COLUMNS)
            self.assertEqual(len(matrix),32)
            self.assertEqual(sha256(args.out_dir/'v4e_shakedown_matrix.csv'),
                             result['shakedown_matrix_sha256'])
            self.assertTrue((args.out_dir/'v4e_release_review.json').is_file())
            self.assertTrue((args.out_dir/'v4e_report.md').is_file())
            with self.assertRaisesRegex(ValueError,'refuses to overwrite'):
                m.run(args)


if __name__=='__main__':
    unittest.main()

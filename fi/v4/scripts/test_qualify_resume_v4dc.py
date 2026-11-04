"""Offline-only contract tests for V4d-C. No Vivado or XSim invoked."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qualify_resume_v4dc as m
from pilot400_common import FIELDS, load_plan, verify_manifest, sha256, read_csv

ROOT = Path(__file__).resolve().parents[3]


def frozen():
    plan = load_plan(ROOT / 'fi/config/pilot400_targets.csv',
                     ROOT / 'fi/config/fi_targets_candidates_v2.csv')
    return plan, verify_manifest(ROOT / 'fi/scenarios/pilot400.csv', plan)


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, cls.manifest = frozen()
        cls.selected = m.selection(cls.manifest, cls.plan)

    def test_eight(self):
        self.assertEqual(len(self.selected), 8)
        self.assertEqual(len({r['scenario_id'] for r in self.selected}), 8)

    def test_two_new_samples(self):
        self.assertEqual(Counter(r['sample_id'] for r in self.selected),
                         {'IMG0004': 4, 'IMG0005': 4})
        self.assertEqual({r['sample_id'] for r in self.selected},
                         set(m.SAMPLES_USED))

    def test_four_classes(self):
        self.assertEqual(Counter(r['target_class'] for r in self.selected),
                         {'CTL': 2, 'BUF': 2, 'MEM': 2, 'DAT': 2})

    def test_four_frozen_targets(self):
        self.assertEqual({r['target_id'] for r in self.selected}, set(m.TARGETS))

    def test_first_config_only(self):
        by_target = {r['target_id']: r for r in self.plan}
        for r in self.selected:
            p = by_target[r['target_id']]
            self.assertEqual((r['bit_index'], r['injection_offset']),
                             (p['bit_index'], p['offset_a']))

    def test_dat0010_bit_is_zero_at_1340(self):
        for r in self.selected:
            if r['target_id'] == 'DAT_0010':
                self.assertEqual((r['bit_index'], r['injection_offset']),
                                 ('0', '1340'))
        self.assertEqual([r['sample_id'] for r in self.selected[4:]],
                         ['IMG0005'] * 4)

    def test_selection_matches_frozen_manifest(self):
        index = {r['scenario_id']: r for r in self.manifest}
        for r in self.selected:
            self.assertEqual(r, index[r['scenario_id']])

    def test_missing_first_config_rejected(self):
        bad = [r for r in self.manifest if r != self.selected[0]]
        with self.assertRaisesRegex(ValueError, 'two frozen'):
            m.selection(bad, self.plan)

    def test_wrong_bit_rejected(self):
        bad = deepcopy(self.manifest)
        next(r for r in bad if r['scenario_id'] == self.selected[1]['scenario_id'])['bit_index'] = '4'
        with self.assertRaisesRegex(ValueError, 'first configuration'):
            m.selection(bad, self.plan)

    def test_duplicate_target_sample_rejected(self):
        bad = self.manifest + [self.selected[0]]
        with self.assertRaisesRegex(ValueError, 'two frozen'):
            m.selection(bad, self.plan)

    def test_full400_not_authorized(self):
        self.assertIn('BLOCKED', m.FULL400_GATE)
        self.assertIn('BLOCKED', m.STATUS_PASS)
        self.assertEqual(m.SCENARIOS, 8)
        self.assertEqual(m.SCHEMA.endswith('V1'), True)

    def test_report_disclaims_scope(self):
        report = m.report_text({'status': m.STATUS_PASS, 'records': [None] * 8})
        self.assertIn('NOT the full 400', report)
        self.assertIn('NOT power-loss', report)
        self.assertIn('FULL400', report)


class ResultParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p, rows = frozen()
        cls.row = m.selection(rows, p)[0]

    def test_preserves_strict_result_and_temporal_scope(self):
        prior = {**{k: self.row[k] for k in FIELDS}, 'outcome': 'PROTOCOL_VIOLATION',
                 'injection_verified': '1', 'consumer_proof_scope': 'NOT_INDEPENDENTLY_MONITORED_V4D',
                 'temporal_evidence_scope': 'OTHER_SAMPLE_TEMPORAL_EXPOSURE_NOT_CERTIFIED',
                 'log_sha256': 'ab' * 32}
        with patch.object(m.v4d, 'strict_result', return_value=prior):
            got = m.result_from_log(Path('synthetic.log'), self.row)
        self.assertEqual(got['outcome'], 'PROTOCOL_VIOLATION')
        self.assertEqual(got['temporal_evidence_scope'],
                         'V4DB_TEN_SAMPLE_BASELINE_ELIGIBILITY')

    def test_missing_injection_rejected(self):
        with patch.object(m.v4d, 'strict_result', return_value={
                'injection_verified': '0', 'consumer_proof_scope':
                'NOT_INDEPENDENTLY_MONITORED_V4D'}):
            with self.assertRaisesRegex(ValueError, 'injection'):
                m.result_from_log(Path('synthetic.log'), self.row)

    def test_fake_consumer_claim_rejected(self):
        with patch.object(m.v4d, 'strict_result', return_value={
                'injection_verified': '1', 'consumer_proof_scope':
                'PROVED_BY_SOME_OTHER_MONITOR'}):
            with self.assertRaisesRegex(ValueError, 'consumer'):
                m.result_from_log(Path('synthetic.log'), self.row)


class RecordIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p, rows = frozen()
        cls.selected = m.selection(rows, p)

    def fixture(self, root: Path):
        build = root / 'build_001'; build.mkdir()
        (build / 'compile_snapshot.log').write_text('compiled')
        (build / 'elaborate_snapshot.log').write_text('elaborated')
        (build / 'source_vlog.prj').write_text('prj')
        prj_hash = sha256(build / 'source_vlog.prj')
        runs = root / 'runs'; runs.mkdir()
        meta = {'builds': [{'build_id': 1, 'source_hashes': {'rtl.v': 'hash'},
                'compile_prj_sha256': prj_hash,
                'compile_log_sha256': sha256(build / 'compile_snapshot.log'),
                'elaborate_log_sha256': sha256(build / 'elaborate_snapshot.log')}],
                'records': []}
        for row in self.selected[:4]:
            sid = row['scenario_id']
            artifacts = {}
            for ext, key in (('.log','log_sha256'),('.console.log','console_sha256'),
                             ('.wrapper.log','wrapper_sha256'),('.cmd','cmd_sha256')):
                p = runs / (sid+ext)
                p.write_text(sid+ext)
                artifacts[key] = sha256(p)
            meta['records'].append({'scenario_id': sid,
                 'build_id': 1, 'validation': {'scenario_id': sid}, **artifacts})
        return meta, prj_hash

    def check(self, root: Path, meta: dict, prj_hash: str):
        def parsed(log, row):
            return {'scenario_id': row['scenario_id']}
        with patch.object(m, 'result_from_log', side_effect=parsed), \
             patch.object(m.v4d, 'check_input_lock'):
            m.verify_run_records(root, self.selected, meta, {}, ROOT,
                                 {'rtl.v':'hash'}, prj_hash)

    def test_valid_four_scenario_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            self.check(root,meta,digest)

    def test_orphan_log_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            (root/'runs/FI_P400_9999.log').write_text('partial')
            with self.assertRaisesRegex(ValueError, 'orphan'):
                self.check(root,meta,digest)

    def test_tampered_log_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            (root/'runs'/(meta['records'][0]['scenario_id']+'.log')).write_text('changed')
            with self.assertRaisesRegex(ValueError, 'missing/changed'):
                self.check(root,meta,digest)

    def test_missing_console_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            (root/'runs'/(meta['records'][0]['scenario_id']+'.console.log')).unlink()
            with self.assertRaisesRegex(ValueError, 'missing/changed'):
                self.check(root,meta,digest)

    def test_wrong_first_scenario_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            meta['records'][0]['scenario_id']='FI_P400_9999'
            with self.assertRaisesRegex(ValueError, 'contiguous'):
                self.check(root,meta,digest)

    def test_wrong_build_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            meta['records'][0]['build_id']=2
            with self.assertRaisesRegex(ValueError, 'missing build'):
                self.check(root,meta,digest)

    def test_corrupted_compile_log_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            (root/'build_001/compile_snapshot.log').write_text('modified')
            with self.assertRaisesRegex(ValueError, 'snapshot log changed'):
                self.check(root,meta,digest)

    def test_corrupted_source_list_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            (root/'build_001/source_vlog.prj').write_text('modified')
            with self.assertRaisesRegex(ValueError, 'source_vlog.prj|compile'):
                self.check(root,meta,digest)

    def test_source_hash_set_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            meta['builds'][0]['source_hashes']={'rtl.v': 'different'}
            with self.assertRaisesRegex(ValueError, 'source provenance'):
                self.check(root,meta,digest)

    def test_reparsed_log_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            with patch.object(m, 'result_from_log', return_value={'scenario_id': 'WRONG'}), \
                 patch.object(m.v4d, 'check_input_lock'):
                with self.assertRaisesRegex(ValueError, 'does not replay'):
                    m.verify_run_records(root,self.selected,meta,{},ROOT,
                                         {'rtl.v':'hash'},digest)

    def test_bad_record_cardinality_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); meta,digest=self.fixture(root)
            meta['records'] *= 3
            with self.assertRaisesRegex(ValueError, 'contiguous|prefix'):
                self.check(root,meta,digest)


class ReadinessMetadataTests(unittest.TestCase):
    def test_no_auto_release(self):
        self.assertIn('BLOCKED',m.FULL400_GATE)
        self.assertNotIn('FULL400_AUTHORIZED',m.STATUS_PASS)

    def test_resolved_relative(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(m.resolved(Path(d),Path('x')),Path(d,'x').resolve())


if __name__=='__main__':
    unittest.main()


class TwoStageLifecycleTests(unittest.TestCase):
    """Synthetic process/log tests. They are NOT evidence from actual Vivado."""

    def args(self, base, stage, execute=True):
        return argparse.Namespace(
            repo=ROOT, results_dir=base / 'results', weights_dir=base / 'weights',
            v4_audit=base / 'v4.json', v4b_a_dir=base / 'v4ba',
            v4b_b_audit=base / 'v4bb.json', v4b_c_dir=base / 'v4bc',
            v4c_audit=base / 'v4c_audit/v4c.json', v4d_canary_dir=base / 'canary',
            v4db_dir=base / 'temporal', out_dir=base / 'shakedown',
            project=base / 'Simulation.xpr', vivado='fake-vivado.bat',
            fileset='sim_1', timeout_seconds=900, stage=stage,
            execute=execute)

    def setup_environment(self, base):
        base.joinpath('results', m.V3A_NAME).mkdir(parents=True)
        v3a = base / 'results' / m.V3A_NAME
        (base / 'Simulation.xpr').write_text('fake project')
        (base / 'v4c_audit').mkdir()
        (base / 'v4c_audit/v4c.json').write_text('{}')
        (base / 'temporal').mkdir()
        (base / 'temporal/v4db_temporal_manifest.json').write_text('{}')
        (v3a / 'source_vlog.prj').write_text('fake pinned sources')
        # Canonicalize the fixture's source path: Windows Temp can use an 8.3
        # alias (TRITUE~1) while Path.resolve() expands it (Tri Tue).
        src = (base / 'locked_rtl.v').resolve(); src.write_text('fake HDL')
        hashes = {str(src): sha256(src)}
        (v3a / 'validation_manifest.json').write_text(json.dumps({
            'snapshot_build': {'source_hashes': hashes}}))
        return hashes

    def fake_pins(self):
        pin = ROOT / 'fi/scenarios/pilot400.csv'
        return {str(pin): sha256(pin)}

    def fake_build(self, repo, project, vivado, fs, output, timeout, v3a):
        source = v3a / 'source_vlog.prj'
        text = source.read_bytes()
        (output / 'source_vlog.prj').write_bytes(text)
        (output / 'compile_snapshot.log').write_text('fake compile success')
        (output / 'elaborate_snapshot.log').write_text('fake elaborate success')
        simdir = output / 'direct_sim'; simdir.mkdir()
        # Match the runner's canonical source-key convention; do not relax
        # its exact source-hash/provenance guard.
        src = (output.parent.parent / 'locked_rtl.v').resolve()
        return {'simdir': simdir, 'vivado_root': repo,
                'source_hashes': {str(src):sha256(src)},
                'compile_prj_sha256': sha256(output / 'source_vlog.prj'),
                'compile_log_sha256': sha256(output / 'compile_snapshot.log'),
                'elaborate_log_sha256': sha256(output / 'elaborate_snapshot.log'),
                'elaboration_mode': 'FULL_BUILD'}

    def fake_call(self, cmd, simdir, wrapper, timeout, description):
        sid = cmd.stem
        root = cmd.parent
        (root / (sid + '.log')).write_text('SYNTHETIC_' + sid + '\n')
        (root / (sid + '.console.log')).write_text('no errors\n')
        wrapper.write_text('synthetic wrapper\n')

    def fake_result(self, log, row):
        return {**{k: row[k] for k in FIELDS},
                'outcome': 'FINAL_OUTPUT_MATCH',
                'injection_verified': '1',
                'temporal_evidence_scope': 'V4DB_TEN_SAMPLE_BASELINE_ELIGIBILITY',
                'consumer_proof_scope': 'NOT_INDEPENDENTLY_MONITORED_V4D',
                'log_sha256': sha256(log)}

    def patches(self, args, pins):
        from contextlib import ExitStack, redirect_stdout
        import io
        stack = ExitStack()
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(patch.object(m, 'verify_v4db', return_value={
            'input_signature': {'project': str(args.project.resolve())}}))
        stack.enter_context(patch.object(m, 'source_pins', return_value=pins))
        stack.enter_context(patch.object(m, 'build_pinned_direct_snapshot',
                                   side_effect=self.fake_build))
        stack.enter_context(patch.object(m, 'make_cmd_run', return_value='synthetic command'))
        stack.enter_context(patch.object(m, 'call_cmd', side_effect=self.fake_call))
        stack.enter_context(patch.object(m, 'result_from_log', side_effect=self.fake_result))
        return stack

    def test_fake_build_uses_same_canonical_source_key_as_fixture(self):
        """Prevent Windows 8.3 Temp alias from breaking lifecycle tests."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            expected = self.setup_environment(base)
            output = base / 'shakedown' / 'build_001'
            output.mkdir(parents=True)
            build = self.fake_build(
                ROOT, base / 'Simulation.xpr', 'fake-vivado.bat',
                'sim_1', output, 900,
                base / 'results' / m.V3A_NAME)
            self.assertEqual(build['source_hashes'], expected)
            self.assertEqual(
                set(build['source_hashes']),
                {str((base / 'locked_rtl.v').resolve())})

    def test_first_stage_still_rejects_forged_snapshot_source_hash(self):
        """The hotfix must not make real runner provenance permissive."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup_environment(base)
            args = self.args(base, 'first')
            def forged_build(*call_args):
                build = self.fake_build(*call_args)
                build['source_hashes'] = {
                    key: '0' * 64 for key in build['source_hashes']}
                return build
            with self.patches(args, self.fake_pins()):
                with patch.object(m, 'build_pinned_direct_snapshot',
                                  side_effect=forged_build):
                    with self.assertRaisesRegex(ValueError,
                                                'snapshot source differs'):
                        m.run(args)
            self.assertFalse((base / 'shakedown' / 'runs').exists())
            self.assertEqual(json.loads((base / 'shakedown' /
                             'v4dc_resume_manifest.json').read_text())['status'],
                             'INTERRUPTED_OR_INVALID')

    def test_dry_run_creates_no_xsim_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); self.setup_environment(base)
            args = self.args(base, 'dry-run', execute=False)
            with self.patches(args, self.fake_pins()):
                result = m.run(args)
            self.assertEqual(result['status'], 'DRY_RUN_ONLY')
            self.assertEqual(result['records'], [])
            self.assertFalse((base / 'shakedown/build_001').exists())

    def test_realistic_two_invocation_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); self.setup_environment(base)
            args=self.args(base,'first')
            with self.patches(args,self.fake_pins()):
                first=m.run(args)
                self.assertEqual(first['status'], m.STATUS_PAUSED)
                self.assertEqual(len(first['records']),4)
                previous={r['scenario_id']:r['log_sha256'] for r in first['records']}
                second=m.run(self.args(base,'resume'))
            self.assertEqual(second['status'],m.STATUS_PASS)
            self.assertEqual(len(second['records']),8)
            self.assertEqual(len(second['builds']),2)
            self.assertTrue((base/'shakedown/build_002/source_vlog.prj').is_file())
            self.assertEqual({r['scenario_id']:r['log_sha256'] for r in second['records'][:4]},
                             previous)
            self.assertEqual(len(read_csv(base/'shakedown/shakedown_validation.csv',
                                          m.COLUMNS)),8)
            self.assertFalse(second['full400_authorized'])

    def test_resume_rejects_modified_existing_log_before_build(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); self.setup_environment(base)
            args=self.args(base,'first')
            with self.patches(args,self.fake_pins()):
                meta=m.run(args)
                p=(base/'shakedown/runs'/(meta['records'][0]['scenario_id']+'.log'))
                p.write_text('tampered after stage1')
                with self.assertRaisesRegex(ValueError,'missing/changed'):
                    m.run(self.args(base,'resume'))
            self.assertFalse((base/'shakedown/build_002').exists())

    def test_resume_rejects_different_source_pin(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); self.setup_environment(base)
            args=self.args(base,'first')
            with self.patches(args,self.fake_pins()):
                m.run(args)
            changed={str(ROOT/'fi/scenarios/pilot400.csv'): 'x'*64}
            with self.patches(args,changed):
                with self.assertRaisesRegex(ValueError,'resume signature'):
                    m.run(self.args(base,'resume'))
            self.assertFalse((base/'shakedown/build_002').exists())

    def test_resume_rejects_orphan_log_without_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); self.setup_environment(base)
            args=self.args(base,'first')
            with self.patches(args,self.fake_pins()):
                m.run(args)
                orphan=base/'shakedown/runs/FI_P400_9999.log'
                orphan.write_text('partial')
                with self.assertRaisesRegex(ValueError,'orphan'):
                    m.run(self.args(base,'resume'))
                self.assertEqual(orphan.read_text(),'partial')
            self.assertFalse((base/'shakedown/build_002').exists())

    def test_resume_requires_completed_first_stage(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); self.setup_environment(base)
            args=self.args(base,'dry-run',execute=False)
            with self.patches(args,self.fake_pins()):
                m.run(args)
                with self.assertRaisesRegex(ValueError,'resume signature'):
                    m.run(self.args(base,'resume'))

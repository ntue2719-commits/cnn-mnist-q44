"""Offline contract tests for V4d-D; all XSim executions are mocked."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qualify_abrupt_resume_v4dd as m
from pilot400_common import FIELDS, load_plan, verify_manifest, read_csv, sha256

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

    def test_eight_unique_scenarios(self):
        self.assertEqual(len(self.selected), 8)
        self.assertEqual(len({r['scenario_id'] for r in self.selected}), 8)

    def test_samples_not_used_by_v4dc(self):
        self.assertEqual(Counter(r['sample_id'] for r in self.selected),
                         {'IMG0002': 4, 'IMG0007': 4})
        self.assertFalse(set(m.SAMPLES) & set(m.v4dc.SAMPLES_USED))

    def test_four_target_classes(self):
        self.assertEqual(Counter(r['target_class'] for r in self.selected),
                         {'CTL': 2, 'BUF': 2, 'MEM': 2, 'DAT': 2})

    def test_frozen_second_bit_offset_for_all(self):
        by_target = {r['target_id']: r for r in self.plan}
        for row in self.selected:
            p = by_target[row['target_id']]
            self.assertEqual((row['bit_index'], row['injection_offset']),
                             (p['bit_index_b'], p['offset_b']))

    def test_dat0010_special_bitpair(self):
        dats = [r for r in self.selected if r['target_id'] == 'DAT_0010']
        self.assertEqual(len(dats), 2)
        self.assertTrue(all((r['bit_index'], r['injection_offset']) == ('1', '1340')
                            for r in dats))

    def test_frozen_manifest_rows_identical(self):
        index = {r['scenario_id']: r for r in self.manifest}
        self.assertTrue(all(index[r['scenario_id']] == r for r in self.selected))

    def test_dropped_row_rejected(self):
        row = self.selected[0]
        broken = [r for r in self.manifest if r['scenario_id'] != row['scenario_id']]
        with self.assertRaisesRegex(ValueError, 'two frozen'):
            m.selection(broken, self.plan)

    def test_wrong_second_bit_rejected(self):
        broken = deepcopy(self.manifest)
        next(r for r in broken if r['scenario_id'] == self.selected[-1]['scenario_id'])['bit_index'] = '7'
        with self.assertRaisesRegex(ValueError, 'second bit/offset'):
            m.selection(broken, self.plan)

    def test_extra_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError, 'two frozen'):
            m.selection(self.manifest + [self.selected[1]], self.plan)

    def test_gate_remains_blocked(self):
        self.assertIn('BLOCKED', m.FULL400_GATE)
        self.assertIn('BLOCKED', m.STATUS_PASS)
        self.assertEqual(m.N_RUNS, 8)

    def test_report_scope_limits(self):
        report = m.report_text({'status': m.STATUS_PASS, 'records': [None] * 8})
        self.assertIn('not during XSim', report)
        self.assertIn('FULL400', report)
        self.assertIn('power-loss', report)

    def test_normalized_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(m.resolved(Path(d), Path('fi')),
                             (Path(d) / 'fi').resolve())


class RecordTests(unittest.TestCase):
    def test_orphan_console_alone_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / 'runs'
            run.mkdir()
            (run / 'FI_P400_9999.console.log').write_text('partial')
            with self.assertRaisesRegex(ValueError, 'orphan'):
                m.exact_artifacts(Path(d), [])

    def test_orphan_command_alone_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / 'runs'
            run.mkdir()
            (run / 'FI_P400_9999.cmd').write_text('partial')
            with self.assertRaisesRegex(ValueError, 'orphan'):
                m.exact_artifacts(Path(d), [])

    def test_empty_run_dir_with_empty_records(self):
        with tempfile.TemporaryDirectory() as d:
            m.exact_artifacts(Path(d), [])

    def test_unexpected_filename_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / 'runs'
            run.mkdir()
            (run / 'unregistered.tmp').write_text('other')
            with self.assertRaisesRegex(ValueError, 'unexpected'):
                m.exact_artifacts(Path(d), [])

    def test_genuine_hard_exit_uses_os_exit(self):
        with patch.object(m.os, '_exit') as goodbye:
            m.hard_exit(m.EXIT_CODE)
        goodbye.assert_called_once_with(86)


class SyntheticHardStop(BaseException):
    """Model os._exit without actually exiting unittest interpreter."""


class RecoveryLifecycleTests(unittest.TestCase):
    def args(self, base, execute=True, resume=False, forced=0):
        return argparse.Namespace(
            repo=ROOT, results_dir=base / 'results', weights_dir=base / 'weights',
            v4_audit=base / 'v4.json', v4b_a_dir=base / 'v4ba',
            v4b_b_audit=base / 'v4bb.json', v4b_c_dir=base / 'v4bc',
            v4c_audit=base / 'v4c/v4c.json', v4d_canary_dir=base / 'canary',
            v4db_dir=base / 'temporal', v4dc_dir=base / 'completed',
            out_dir=base / 'recovery', project=base / 'Simulation.xpr',
            vivado='fake-vivado.bat', fileset='sim_1', timeout_seconds=900,
            force_exit_after=forced, execute=execute, resume=resume)

    def setup(self, base):
        v3a = base / 'results' / m.V3A_NAME
        v3a.mkdir(parents=True)
        (v3a / 'source_vlog.prj').write_text('verified prj')
        (base / 'completed').mkdir()
        (base / 'completed/v4dc_resume_manifest.json').write_text('{"status":"test"}')
        (base / 'temporal').mkdir()
        (base / 'temporal/v4db_temporal_manifest.json').write_text('{"status":"test"}')
        (base / 'Simulation.xpr').write_text('fake project')
        src = (base / 'locked_rtl.v').resolve()
        src.write_text('verified HDL')
        hashes = {str(src): sha256(src)}
        (v3a / 'validation_manifest.json').write_text(json.dumps({
            'snapshot_build': {'source_hashes': hashes}}))
        return hashes

    def pins(self):
        path = ROOT / 'fi/scenarios/pilot400.csv'
        return {str(path.resolve()): sha256(path)}

    def fake_build(self, repo, project, vivado, fileset, out, timeout, v3a):
        (out / 'source_vlog.prj').write_bytes((v3a / 'source_vlog.prj').read_bytes())
        (out / 'compile_snapshot.log').write_text('synthetic compile')
        (out / 'elaborate_snapshot.log').write_text('synthetic elaborate')
        simdir = out / 'direct_sim'
        simdir.mkdir()
        src = (out.parent.parent / 'locked_rtl.v').resolve()
        return {'simdir': simdir, 'vivado_root': repo,
                'source_hashes': {str(src): sha256(src)},
                'compile_prj_sha256': sha256(out / 'source_vlog.prj'),
                'compile_log_sha256': sha256(out / 'compile_snapshot.log'),
                'elaborate_log_sha256': sha256(out / 'elaborate_snapshot.log'),
                'elaboration_mode': 'FULL_BUILD'}

    def fake_call(self, cmd, simdir, wrapper, timeout, text):
        sid = cmd.stem
        (cmd.parent / (sid + '.log')).write_text('SYNTHETIC_' + sid)
        (cmd.parent / (sid + '.console.log')).write_text('no errors')
        wrapper.write_text('synthetic wrapper')

    def fake_result(self, log, row):
        return {**{k: row[k] for k in FIELDS},
                'outcome': 'FINAL_OUTPUT_MATCH', 'injection_verified': '1',
                'temporal_evidence_scope': 'V4DB_TEN_SAMPLE_BASELINE_ELIGIBILITY',
                'consumer_proof_scope': 'NOT_INDEPENDENTLY_MONITORED_V4D',
                'log_sha256': sha256(log)}

    def patches(self, args, pins=None):
        context = ExitStack()
        context.enter_context(redirect_stdout(io.StringIO()))
        context.enter_context(patch.object(m, 'verify_v4dc_ancestry',
            return_value={'input_signature': {'project': str(args.project.resolve())}}))
        context.enter_context(patch.object(m, 'input_pins', return_value=pins or self.pins()))
        context.enter_context(patch.object(m, 'validate_assets', return_value=(10, [])))
        context.enter_context(patch.object(m, 'build_pinned_direct_snapshot', side_effect=self.fake_build))
        context.enter_context(patch.object(m, 'make_cmd_run', return_value='synthetic command'))
        context.enter_context(patch.object(m, 'call_cmd', side_effect=self.fake_call))
        context.enter_context(patch.object(m.v4dc, 'result_from_log', side_effect=self.fake_result))
        return context

    def stop_after_three(self, base):
        args = self.args(base, forced=3)
        with self.patches(args):
            with patch.object(m, 'hard_exit', side_effect=SyntheticHardStop):
                with self.assertRaises(SyntheticHardStop):
                    m.run(args)
        meta = json.loads((base / 'recovery/v4dd_recovery_manifest.json').read_text())
        self.assertEqual(meta['status'], 'RUNNING')
        self.assertEqual(len(meta['records']), 3)
        self.assertEqual(len(meta['builds']), 1)
        self.assertEqual(meta['termination_plan']['after_n'], 3)
        return meta

    def complete(self, base):
        args = self.args(base, resume=True)
        with self.patches(args):
            return m.run(args)

    def test_dryrun_without_any_xsim(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            args = self.args(base, execute=False)
            with self.patches(args):
                result = m.run(args)
            self.assertEqual(result['status'], 'DRY_RUN_ONLY')
            self.assertFalse((base / 'recovery/build_001').exists())

    def test_initial_execution_must_force_crash(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            args = self.args(base, forced=0)
            with self.patches(args):
                with self.assertRaisesRegex(ValueError, 'REQUIRES'):
                    m.run(args)
            self.assertFalse((base / 'recovery').exists())

    def test_forced_crash_does_not_mark_pass(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            meta = self.stop_after_three(base)
            self.assertFalse(meta['full400_authorized'])
            self.assertFalse((base / 'recovery/recovery_validation.csv').exists())

    def test_new_process_resume_verifies_and_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            old = self.stop_after_three(base)
            old_hashes = [r['log_sha256'] for r in old['records']]
            done = self.complete(base)
            self.assertEqual(done['status'], m.STATUS_PASS)
            self.assertEqual(len(done['records']), 8)
            self.assertEqual(len(done['builds']), 2)
            self.assertEqual([r['log_sha256'] for r in done['records'][:3]], old_hashes)
            self.assertEqual(len(read_csv(base / 'recovery/recovery_validation.csv', m.COLUMNS)), 8)
            self.assertFalse(done['full400_authorized'])
            self.assertFalse(done['full400_runner_qualified'])

    def test_changed_prior_log_rejected_before_new_build(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            meta = self.stop_after_three(base)
            sid = meta['records'][0]['scenario_id']
            (base / 'recovery/runs' / (sid + '.log')).write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'missing/changed'):
                self.complete(base)
            self.assertFalse((base / 'recovery/build_002').exists())

    def test_orphan_cmd_rejected_before_new_build(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            path = base / 'recovery/runs/FI_P400_9999.cmd'
            path.write_text('orphan')
            with self.assertRaisesRegex(ValueError, 'orphan'):
                self.complete(base)
            self.assertTrue(path.exists())
            self.assertFalse((base / 'recovery/build_002').exists())

    def test_orphan_log_rejected_before_new_build(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            path = base / 'recovery/runs/FI_P400_9999.log'
            path.write_text('partial')
            with self.assertRaisesRegex(ValueError, 'orphan'):
                self.complete(base)
            self.assertEqual(path.read_text(), 'partial')
            self.assertFalse((base / 'recovery/build_002').exists())

    def test_modified_build_log_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            (base / 'recovery/build_001/compile_snapshot.log').write_text('edited')
            with self.assertRaisesRegex(ValueError, 'snapshot log changed'):
                self.complete(base)
            self.assertFalse((base / 'recovery/build_002').exists())

    def test_wrong_first_source_hash_during_build_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            args = self.args(base, forced=3)
            def forged(*x):
                result = self.fake_build(*x)
                result['source_hashes'] = {k: '0'*64 for k in result['source_hashes']}
                return result
            with self.patches(args):
                with patch.object(m, 'build_pinned_direct_snapshot', side_effect=forged):
                    with self.assertRaisesRegex(ValueError, 'source differs'):
                        m.run(args)
            meta = json.loads((base / 'recovery/v4dd_recovery_manifest.json').read_text())
            self.assertEqual(meta['status'], 'INTERRUPTED_OR_INVALID')
            self.assertEqual(len(meta['records']), 0)

    def test_resume_pin_signature_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            args = self.args(base, resume=True)
            with self.patches(args, pins={str(ROOT / 'fi/scenarios/pilot400.csv'): '0'*64}):
                with self.assertRaisesRegex(ValueError, 'signature'):
                    m.run(args)
            self.assertFalse((base / 'recovery/build_002').exists())

    def test_resume_different_project_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            bad = self.args(base, resume=True)
            bad.project = base / 'other.xpr'
            bad.project.write_text('different project')
            with self.patches(bad):
                with self.assertRaisesRegex(ValueError, 'signature|project differs'):
                    m.run(bad)
            self.assertFalse((base / 'recovery/build_002').exists())

    def test_resume_denied_if_plan_not_observed(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            p = base / 'recovery/v4dd_recovery_manifest.json'
            meta = json.loads(p.read_text())
            meta['termination_plan']['after_n'] = 4
            p.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, 'checkpoint count'):
                self.complete(base)
            self.assertFalse((base / 'recovery/build_002').exists())

    def test_resume_denied_when_status_pass_forged(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            p = base / 'recovery/v4dd_recovery_manifest.json'
            meta = json.loads(p.read_text())
            meta['status'] = m.STATUS_PASS
            p.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, 'checkpoint count'):
                self.complete(base)

    def test_resume_denied_when_full400_auto_promoted(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            p = base / 'recovery/v4dd_recovery_manifest.json'
            meta = json.loads(p.read_text())
            meta['full400_authorized'] = True
            p.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, 'checkpoint count'):
                self.complete(base)

    def test_no_second_resume_after_pass(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            self.stop_after_three(base)
            self.complete(base)
            with self.assertRaisesRegex(ValueError, 'checkpoint count'):
                self.complete(base)

    def test_refuses_existing_output_without_resume(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self.setup(base)
            (base / 'recovery').mkdir()
            args = self.args(base, forced=3)
            with self.patches(args):
                with self.assertRaisesRegex(ValueError, 'new output'):
                    m.run(args)


if __name__ == '__main__':
    unittest.main()

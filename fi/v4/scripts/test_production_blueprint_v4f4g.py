#!/usr/bin/env python3
"""Adversarial tests: V4f4G blueprint is exact and always non-executable."""
from __future__ import annotations

import argparse
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

HERE = Path(__file__).resolve().parent
for folder in (HERE, HERE.parents[1] / 'scripts', HERE.parents[1] / 'v3b/scripts'):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
import production_runner_v4f4f as prod
import production_blueprint_v4f4g as bp

ROOT = HERE.parents[2]


def options(mode='blueprint', repo=Path('/intentionally-missing'), **kwargs):
    initial = dict(mode=mode, repo=repo, results_dir=Path('fi/results'),
                   weights_dir=Path('data'),
                   out_dir=Path('fi/results/v4f4g_blueprint_case'),
                   plan_dir=Path('fi/results/v4f4f_plan_case'),
                   blueprint_dir=None, execute=False, approve=False)
    initial.update(kwargs)
    return argparse.Namespace(**initial)


class StaticContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = prod.frozen_schedule(ROOT)

    def test_exact_400(self):
        self.assertEqual(len(self.rows), 400)

    def test_16_batches(self):
        self.assertEqual({r['batch_number'] for r in self.rows},
                         {str(i) for i in range(1, 17)})

    def test_first_last(self):
        self.assertEqual((self.rows[0]['scenario_id'], self.rows[-1]['scenario_id']),
                         ('FI_P400_0001', 'FI_P400_0400'))

    def test_no_profile_arg(self):
        self.assertNotIn('PILOT_PROFILE_ONLY', prod.PLUSARG_KEYS)

    def test_all_400_specs_use_ten_args(self):
        for idx, row in enumerate(self.rows, 1):
            spec = bp.scenario_record(idx, row, ROOT)
            self.assertEqual(tuple(spec['plusargs_exact']), prod.PLUSARG_KEYS)
            self.assertFalse(spec['production_fi_executed'])
            self.assertEqual(spec['ordinal'], idx)

    def test_frozen_dat_pair(self):
        dat = [r for r in self.rows if r['target_id'] == 'DAT_0010']
        self.assertEqual(len(dat), 20)
        self.assertEqual({r['injection_offset'] for r in dat}, {'1340'})
        self.assertEqual({r['bit_index'] for r in dat}, {'0', '1'})

    def test_bad_ordinal(self):
        with self.assertRaises(ValueError):
            bp.scenario_record(2, self.rows[0], ROOT)

    def test_bad_sid(self):
        row = copy.deepcopy(self.rows[0]); row['scenario_id'] = '../bad'
        with self.assertRaises(ValueError):
            bp.scenario_record(1, row, ROOT)

    def test_bad_plusarg_source(self):
        row = copy.deepcopy(self.rows[0]); row['sample_id'] = 'IMG9999'
        # Expected plusargs are deterministically generated, but frozen row
        # equality is enforced when the entire blueprint is independently replayed.
        self.assertEqual(bp.scenario_record(1, row, ROOT)['plusargs_exact']['SAMPLE_ID'],
                         'IMG9999')


class DenyExecution(unittest.TestCase):
    def test_execute_mode_denied_before_io(self):
        with self.assertRaisesRegex(ValueError, 'DENIED BEFORE IO'):
            bp.dispatch(options(mode='execute'))

    def test_full400_mode_denied_before_io(self):
        with self.assertRaisesRegex(ValueError, 'DENIED BEFORE IO'):
            bp.dispatch(options(mode='full400'))

    def test_execute_flag_denied_before_io(self):
        with self.assertRaisesRegex(ValueError, 'DENIED BEFORE IO'):
            bp.dispatch(options(execute=True))

    def test_approve_flag_denied_before_io(self):
        with self.assertRaisesRegex(ValueError, 'DENIED BEFORE IO'):
            bp.dispatch(options(approve=True))


class BlueprintIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = prod.frozen_schedule(ROOT)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / 'repo'
        self.results = self.repo / 'fi/results'
        self.results.mkdir(parents=True)
        (self.repo / 'data').mkdir()
        self.plan = self.results / 'v4f4f_plan_case'
        self.bpdir = self.results / 'v4f4g_blueprint_case'
        self.auditdir = self.results / 'v4f4g_audit_case'
        self.pins = {}
        for i in range(56):
            source = self.repo / f'source_pin_{i:03d}.txt'
            source.write_text(str(i), encoding='ascii')
            self.pins[str(source.resolve())] = prod.sha256(source)
        prod.make_metadata(self.plan, self.rows, self.pins, self.repo)
        self.mocks = [patch.object(prod, 'frozen_schedule', return_value=self.rows),
                      patch.object(prod, 'pinned_inputs', return_value=self.pins)]
        for m in self.mocks:
            m.start(); self.addCleanup(m.stop)

    def build(self):
        with redirect_stdout(io.StringIO()):
            bp.dispatch(options(repo=self.repo))
        self.assertTrue((self.bpdir / bp.SOURCE_NAME).is_file())
        return self.read()

    def read(self):
        return prod.strict_json(self.bpdir / bp.SOURCE_NAME)

    def write(self, data, refresh_receipt=False):
        prod.atomic_json(self.bpdir / bp.SOURCE_NAME, data)
        if refresh_receipt:
            receipt = prod.strict_json(self.bpdir / bp.RECEIPT_NAME)
            receipt['blueprint_sha256'] = prod.sha256(self.bpdir / bp.SOURCE_NAME)
            prod.atomic_json(self.bpdir / bp.RECEIPT_NAME, receipt)

    def replay(self):
        return bp.verify_blueprint(self.repo, self.results, self.bpdir,
                                   self.plan, self.rows, self.pins)

    def test_blueprint_passes(self):
        self.build(); obj = self.replay()
        self.assertEqual(obj['records_verified'], 400)
        self.assertEqual(obj['new_xsim_runs'], 0)

    def test_canonical_binary_lf_ignores_legacy_windows_text_writer(self):
        # Windows text mode translates LF to CRLF. Phase 4G must never call
        # the historical V4f4F text-mode writer to create its own artifacts.
        with patch.object(prod, 'atomic_json',
                          side_effect=AssertionError('legacy writer used')):
            self.build()
        for name in (bp.SOURCE_NAME, bp.RECEIPT_NAME):
            path = self.bpdir / name
            self.assertEqual(path.read_bytes(), prod.encoded(prod.strict_json(path)))
            self.assertNotIn(b'\r\n', path.read_bytes())
        self.replay()

    def test_crlf_blueprint_rejected_even_when_semantically_equal(self):
        self.build()
        path = self.bpdir / bp.SOURCE_NAME
        canonical = path.read_bytes()
        translated = canonical.replace(b'\n', b'\r\n')
        self.assertNotEqual(translated, canonical)
        path.write_bytes(translated)
        self.assertEqual(prod.strict_json(path), json.loads(canonical.decode('utf-8')))
        with self.assertRaisesRegex(ValueError, '400 invocation records'):
            self.replay()

    def test_crlf_receipt_rejected_even_when_semantically_equal(self):
        self.build()
        path = self.bpdir / bp.RECEIPT_NAME
        canonical = path.read_bytes()
        translated = canonical.replace(b'\n', b'\r\n')
        path.write_bytes(translated)
        self.assertEqual(prod.strict_json(path),
                         json.loads(canonical.decode('utf-8')))
        with self.assertRaisesRegex(ValueError, 'receipt must be canonical'):
            self.replay()

    def test_cli_audit(self):
        self.build()
        with redirect_stdout(io.StringIO()):
            bp.dispatch(options(mode='audit', repo=self.repo,
                                out_dir=Path('fi/results/v4f4g_audit_case'),
                                blueprint_dir=Path('fi/results/v4f4g_blueprint_case')))
        self.assertTrue((self.auditdir / bp.AUDIT_NAME).is_file())

    def test_exact_ten_plusargs_for_every_scenario(self):
        d = self.build()
        self.assertEqual(len(d['invocations']), 400)
        self.assertTrue(all(len(x['plusargs_exact']) == 10 for x in d['invocations']))

    def test_false_release(self):
        d = self.build()
        self.assertFalse(d['full400_authorized'])
        self.assertFalse(d['build_once_executed'])
        self.assertEqual(d['actual_fi_runs'], 0)

    def test_parent_plan_changed(self):
        self.build()
        p = self.plan / prod.LEDGER_NAME
        p.write_bytes(p.read_bytes() + b' ')
        with self.assertRaises(ValueError): self.replay()

    def test_inflight_in_parent(self):
        self.build()
        (self.plan / 'inflight.json').write_text('orphan', encoding='ascii')
        with self.assertRaises(ValueError): self.replay()

    def test_missing_invocation(self):
        d = self.build(); d['invocations'].pop()
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_duplicate_scenario(self):
        d = self.build(); d['invocations'][1]['scenario_id'] = d['invocations'][0]['scenario_id']
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_incorrect_bit(self):
        d = self.build(); d['invocations'][0]['plusargs_exact']['FI_BIT'] = '7'
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_incorrect_offset(self):
        d = self.build(); d['invocations'][0]['plusargs_exact']['FI_OFFSET'] = '999'
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_incorrect_input_path(self):
        d = self.build(); d['invocations'][0]['plusargs_exact']['INPUT_FILE'] = 'fake.hex'
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_profile_arg_added(self):
        d = self.build(); d['invocations'][0]['plusargs_exact']['PILOT_PROFILE_ONLY'] = '1'
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_reordered_batches(self):
        d = self.build(); d['invocations'][24], d['invocations'][25] = (
            d['invocations'][25], d['invocations'][24])
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_forged_release_flag(self):
        d = self.build(); d['full400_authorized'] = True
        self.write(d, True)
        with self.assertRaises(ValueError): self.replay()

    def test_receipt_hash_mismatch(self):
        d = self.build(); d['build_once_required'] = False
        self.write(d, False)
        with self.assertRaises(ValueError): self.replay()

    def test_receipt_release_forged(self):
        self.build(); p = self.bpdir / bp.RECEIPT_NAME
        r = prod.strict_json(p); r['G6_production'] = 'QUALIFIED'
        prod.atomic_json(p, r)
        with self.assertRaises(ValueError): self.replay()

    def test_extra_orphan_file(self):
        self.build(); (self.bpdir / 'extra.log').write_text('FI_RESULT_V2', encoding='ascii')
        with self.assertRaises(ValueError): self.replay()

    def test_missing_receipt(self):
        self.build(); (self.bpdir / bp.RECEIPT_NAME).unlink()
        with self.assertRaises(ValueError): self.replay()

    def test_source_drift(self):
        self.build(); path = Path(next(iter(self.pins)))
        path.write_text('tampered', encoding='ascii')
        with self.assertRaises(ValueError):
            prod.check_pins(self.pins)

    def test_symlink_blueprint_denied(self):
        self.build()
        p = self.bpdir / bp.SOURCE_NAME
        old = self.bpdir / 'original.tmp'
        p.rename(old)
        try:
            p.symlink_to(old)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation denied by host')
        with self.assertRaises(ValueError): self.replay()

    def test_second_blueprint_output_cannot_overwrite(self):
        self.build()
        with self.assertRaises(ValueError):
            bp.dispatch(options(repo=self.repo))

    def test_duplicate_json_key_denied(self):
        self.build(); p = self.bpdir / bp.RECEIPT_NAME
        data = p.read_text()
        data = data.replace('"schema": ', '"schema": "duplicate", "schema": ', 1)
        p.write_text(data, encoding='utf-8')
        with self.assertRaises(ValueError): self.replay()


if __name__ == '__main__':
    unittest.main()

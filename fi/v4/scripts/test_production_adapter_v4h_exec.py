#!/usr/bin/env python3
"""V4H-EXEC offline safety tests. MOCK inputs only; 0 Vivado/XSim."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import production_adapter_v4h_exec as prod
import audit_production_adapter_v4h_exec as audit


def raw_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mock_blueprint() -> dict:
    records = []
    for i in range(1, 401):
        sid = f'FI_P400_{i:04d}'
        batch = (i - 1) // 25 + 1
        namespace = f'runs/batch_{batch:02d}/{sid}'
        outputs = {
            'command': namespace + '/invocation.cmd',
            'raw_xsim_log': namespace + '/xsim.log',
            'console': namespace + '/console.log',
            'wrapper': namespace + '/wrapper.log',
            'process_receipt': namespace + '/process_receipt.json',
            'job_launch': namespace + '/job_launch.json',
            'child_lifecycle': namespace + '/child_lifecycle.json',
            'strict_validation': namespace + '/strict_validation.json',
        }
        plus = {
            'FI_OFFSET': '125', 'SCENARIO_ID': sid, 'TARGET_ID': 'CTL_0001',
            'FI_BIT': '2', 'SAMPLE_ID': 'IMG0000',
            'INPUT_FILE': '/mock/input.hex', 'POOL1_FILE': '/mock/pool1.hex',
            'POOL2_FILE': '/mock/pool2.hex', 'LOGITS_FILE': '/mock/golden.hex',
            'PREDICTION_FILE': '/mock/pred.hex',
        }
        records.append({
            'ordinal': i, 'scenario_id': sid, 'batch_number': batch,
            'batch_position': (i - 1) % 25 + 1,
            'frozen_tuple_sha256': '1'*64,
            'plusargs_order': list(prod.PLUSARG_KEYS),
            'plusargs_exact': plus,
            'future_snapshot_reference': 'BUILD_ONCE_SNAPSHOT_REQUIRED_NOT_PRESENT',
            'future_run_directory': namespace,
            'future_outputs': outputs,
            'job_object_required': True,
            'per_scenario_snapshot_rebuild_forbidden': True,
            'production_fi_executed': False,
            'scientific_outcome_available': False,
        })
    return {
        'schema': 'CNN_PEP_V4F4G_OFFLINE_XSIM_INVOCATION_BLUEPRINT_V1',
        'scope': 'INVOCATION_CONTRACT_ONLY_NO_EXECUTABLE_COMMANDS_NO_XSIM',
        'frozen_scenario_count': 400, 'batch_count': 16, 'batch_size': 25,
        'serial_max_active_xsim': 1, 'build_once_required': True,
        'build_once_executed': False, 'private_windows_job_object_per_run': True,
        'actual_fi_runs': 0, 'human_approved_scientific_protocol': False,
        'g6_production_qualified': False, 'full400_authorized': False,
        'full400_executed': False, 'strict_fi_parser_required_for_commit': True,
        'infrastructure_error_is_design_noncompletion': False,
        'immutable_run_namespace_required': True,
        'fail_closed_on_orphan_timeout_or_source_drift': True,
        'source_input_fingerprint': 'a'*64,
        'parent_v4f4f_plan_sha256': 'b'*64,
        'parent_v4f4f_schedule_sha256': 'c'*64,
        'invocations': records,
    }


def make(bp: dict | None = None) -> dict:
    return prod.make_candidate(bp or mock_blueprint(), 'b'*64, 'c'*64,
                               'd'*64, 'e'*64, 'f'*64, '0'*64)


def receipt(m: dict) -> dict:
    return {
        'schema': audit.RECEIPT_SCHEMA,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'adapter_plan_sha256': raw_hash(prod.canonical(m)),
        'adapter_generator_sha256': 'f'*64,
        'scope': m['scope'], 'new_xsim_runs': 0,
        'source_snapshot_built': False,
        'G6_production_qualified': False,
        'G7_scientific_approved': False,
        'full400_authorized': False,
        'release_gate': prod.STOP,
    }


def check(m: dict, r: dict, bp: dict):
    return audit.verify_semantics(
        m, r, bp, plan_hash='b'*64, schedule_hash='c'*64,
        blueprint_hash='d'*64, blueprint_receipt_hash='e'*64,
        generator_hash='f'*64, blueprint_audit_hash='0'*64,
        adapter_file_hash=raw_hash(prod.canonical(m)))


class PureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bp = mock_blueprint()
        cls.m = make(cls.bp)
        cls.r = receipt(cls.m)

    def test_400_and_3200_contracts(self):
        self.assertEqual(len(self.m['invocations']), 400)
        self.assertEqual(check(self.m, self.r, self.bp), 3200)

    def test_exact_batch_boundaries(self):
        self.assertEqual([self.m['invocations'][i]['batch_number'] for i in (0,24,25,399)],
                         [1,1,2,16])

    def test_all_false_not_real_fi(self):
        self.assertTrue(all(not row['ready_to_execute'] and
                            not row['checkpoint_committed'] and
                            not row['design_outcome_observed']
                            for row in self.m['invocations']))

    def test_reject_full400_mode_before_io(self):
        for mode in ('build','execute','full400'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'DENIED BEFORE IO'):
                prod.check_args_before_io(mode, False, False)

    def test_reject_approval_before_io(self):
        for a,b in ((True,False),(False,True)):
            with self.assertRaises(ValueError):
                prod.check_args_before_io('plan', a, b)

    def test_malformed_count(self):
        bp = copy.deepcopy(self.bp)
        bp['invocations'].pop()
        with self.assertRaises(ValueError): make(bp)

    def test_changed_bit(self):
        bp = copy.deepcopy(self.bp)
        bp['invocations'][0]['plusargs_exact']['FI_BIT'] = ''
        with self.assertRaises(ValueError): make(bp)

    def test_changed_tuple_claim(self):
        bp = copy.deepcopy(self.bp)
        bp['invocations'][13]['scenario_id'] = 'FI_P400_9999'
        with self.assertRaises(ValueError): make(bp)

    def test_changed_offset_unsupported_string(self):
        bp = copy.deepcopy(self.bp)
        bp['invocations'][2]['plusargs_exact']['FI_OFFSET'] = '-5'
        with self.assertRaises(ValueError): make(bp)

    def test_duplicate_future_output(self):
        bp = copy.deepcopy(self.bp)
        x = bp['invocations'][0]['future_outputs']
        x['wrapper'] = x['console']
        with self.assertRaises(ValueError): make(bp)

    def test_path_traversal(self):
        bp = copy.deepcopy(self.bp)
        ns = bp['invocations'][0]['future_run_directory']
        bp['invocations'][0]['future_outputs']['wrapper'] = ns + '/../bad.log'
        with self.assertRaises(ValueError): make(bp)

    def test_disable_job_object(self):
        bp = copy.deepcopy(self.bp)
        bp['invocations'][0]['job_object_required'] = False
        with self.assertRaises(ValueError): make(bp)

    def test_forged_release_blueprint(self):
        bp = copy.deepcopy(self.bp)
        bp['full400_authorized'] = True
        with self.assertRaises(ValueError): make(bp)

    def test_forged_build_once(self):
        bp = copy.deepcopy(self.bp)
        bp['build_once_executed'] = True
        with self.assertRaises(ValueError): make(bp)

    def test_forged_noncompletion_mapping(self):
        bp = copy.deepcopy(self.bp)
        bp['infrastructure_error_is_design_noncompletion'] = True
        with self.assertRaises(ValueError): make(bp)

    def test_wrong_parent_hash(self):
        with self.assertRaises(ValueError):
            prod.make_candidate(self.bp, '1'*64, 'c'*64, 'd'*64, 'e'*64,'f'*64,'0'*64)

    def test_double_json_key(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)/'x.json'
            p.write_bytes(b'{"a":1,"a":2}\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                prod.strict_json(p)

    def test_crlf_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)/'x.json'
            p.write_bytes(b'{"a": 1}\r\n')
            with self.assertRaisesRegex(ValueError, 'canonical'):
                prod.strict_json(p, canonical_required=True)

    def test_atomic_no_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)/'x.json'
            prod.write_new(p, b'first')
            with self.assertRaises(ValueError): prod.write_new(p, b'second')
            self.assertEqual(p.read_bytes(), b'first')

    def test_atomic_orphan_tmp_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)/'x.json'
            (Path(t)/'x.json.tmp').write_bytes(b'orphan')
            with self.assertRaises(ValueError): prod.write_new(p, b'x')

    def test_audit_reject_forged_approval(self):
        m = copy.deepcopy(self.m)
        m['full400_authorized'] = True
        with self.assertRaises(ValueError): check(m, self.r, self.bp)

    def test_audit_reject_replaced_checkpoint(self):
        m = copy.deepcopy(self.m)
        m['invocations'][10]['checkpoint_committed'] = True
        with self.assertRaises(ValueError): check(m, self.r, self.bp)

    def test_audit_reject_changed_plusargs_hash(self):
        m = copy.deepcopy(self.m)
        m['invocations'][0]['xsim_plusargs_data_sha256'] = '0'*64
        with self.assertRaises(ValueError): check(m, self.r, self.bp)

    def test_audit_reject_generator_drift(self):
        m = copy.deepcopy(self.m)
        m['adapter_generator_sha256'] = '0'*64
        with self.assertRaises(ValueError): check(m, self.r, self.bp)

    def test_audit_reject_receipt_bad_hash(self):
        r = copy.deepcopy(self.r)
        r['adapter_plan_sha256'] = '0'*64
        with self.assertRaises(ValueError): check(self.m, r, self.bp)

    def test_audit_reject_extra_field(self):
        r = copy.deepcopy(self.r)
        r['executed'] = 400
        with self.assertRaises(ValueError): check(self.m, r, self.bp)

    def test_audit_reject_stale_blueprint(self):
        bp = copy.deepcopy(self.bp)
        bp['invocations'][0]['plusargs_exact']['FI_BIT'] = '3'
        with self.assertRaises(ValueError): check(self.m, self.r, bp)

    def test_audit_exec_guard(self):
        args = types.SimpleNamespace(mode='execute', execute=False, approve=False)
        with self.assertRaisesRegex(ValueError, 'DENIED BEFORE IO'):
            audit.run_audit(args)

    def test_name_namespace_rejection(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            (repo/'fi/results').mkdir(parents=True)
            (repo/'fi/results/v4f4f_plan_01').mkdir()
            (repo/'fi/results/v4f4g_blueprint_01').mkdir()
            with self.assertRaises(ValueError):
                prod.repository_paths(repo, Path('fi/results/v4f4f_plan_01'),
                    Path('fi/results/v4f4g_blueprint_01'),
                    Path('fi/results/v4h_exec_bogus/../../evil'))


class MockCLIPipeline(unittest.TestCase):
    """Exercises CLI files with MOCK upstream oracles: never real FI."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.results = self.repo/'fi/results'
        self.results.mkdir(parents=True)
        self.plan = self.results/'v4f4f_plan_mock'
        self.blueprint = self.results/'v4f4g_blueprint_mock'
        self.bp_audit = self.results/'v4f4g_audit_mock'
        for p in (self.plan, self.blueprint, self.bp_audit):p.mkdir()
        self.bp = mock_blueprint()
        (self.plan/'v4f4f_production_contract.json').write_bytes(prod.canonical({'plan': 'mock'}))
        (self.plan/'schedule_400.csv').write_bytes(b'MOCK_ONLY\n')
        self.bp['parent_v4f4f_plan_sha256'] = prod.sha_file(self.plan/'v4f4f_production_contract.json')
        self.bp['parent_v4f4f_schedule_sha256'] = prod.sha_file(self.plan/'schedule_400.csv')
        (self.blueprint/'invocation_blueprint_400.json').write_bytes(prod.canonical(self.bp))
        (self.blueprint/'blueprint_receipt.json').write_bytes(prod.canonical({'receipt': 'mock'}))
        (self.bp_audit/'v4f4g_blueprint_audit.json').write_bytes(prod.canonical({
            'schema': 'CNN_PEP_V4F4G_INDEPENDENT_BLUEPRINT_AUDIT_V1',
            'records_verified': 400, 'batches_verified': 16,
            'exact_plusargs_verified_per_record': 10,
            'plan_sha256': prod.sha_file(self.plan/'v4f4f_production_contract.json'),
            'blueprint_sha256': prod.sha_file(self.blueprint/'invocation_blueprint_400.json'),
            'receipt_sha256': prod.sha_file(self.blueprint/'blueprint_receipt.json'),
            'new_xsim_runs': 0, 'full400_authorized': False,
            'G6_production': 'NOT_QUALIFIED',
        }))
        self.f4f = types.ModuleType('production_runner_v4f4f')
        self.f4f.frozen_schedule = lambda repo: [{}]*400
        self.f4f.pinned_inputs = lambda repo, weights: {}
        self.f4f.check_pins = lambda pins: None
        self.f4f.LEDGER_NAME = 'v4f4f_production_contract.json'
        self.f4g = types.ModuleType('production_blueprint_v4f4g')
        self.f4g.verify_blueprint = lambda *args: True
        self.f4g.SOURCE_NAME = 'invocation_blueprint_400.json'
        self.f4g.RECEIPT_NAME = 'blueprint_receipt.json'
        self.f4g.AUDIT_NAME = 'v4f4g_blueprint_audit.json'
        self.f4g.RECEIPT_SCHEMA = 'CNN_PEP_V4F4G_INDEPENDENT_BLUEPRINT_AUDIT_V1'
        patches = [patch.dict(sys.modules, {'production_runner_v4f4f': self.f4f,
                                              'production_blueprint_v4f4g': self.f4g})]
        for p in patches:
            p.start();self.addCleanup(p.stop)

    def args(self):
        return types.SimpleNamespace(mode='plan',execute=False,approve=False,
            repo=self.repo,plan_dir=Path('fi/results/v4f4f_plan_mock'),
            blueprint_dir=Path('fi/results/v4f4g_blueprint_mock'),
            blueprint_audit_dir=Path('fi/results/v4f4g_audit_mock'),
            out_dir=Path('fi/results/v4h_exec_adapter_plan_mock'))

    def test_plan_then_independent_audit_mock(self):
        args = self.args()
        prod.run_plan(args)
        args.mode = 'audit'
        args.adapter_dir = args.out_dir
        args.out_dir = Path('fi/results/v4h_exec_adapter_audit_mock')
        result = audit.run_audit(args)
        self.assertEqual(result['mapped_contracts_verified'], 400)
        self.assertEqual(result['new_xsim_runs'], 0)

    def test_plan_cannot_repeat_into_same_dir(self):
        args = self.args()
        prod.run_plan(args)
        with self.assertRaises(ValueError):prod.run_plan(args)

    def test_orphan_file_refused_by_auditor(self):
        args = self.args()
        prod.run_plan(args)
        (self.results/'v4h_exec_adapter_plan_mock'/'orphan.tmp').write_bytes(b'oops')
        args.mode = 'audit'
        args.adapter_dir = args.out_dir
        args.out_dir = Path('fi/results/v4h_exec_adapter_audit_mock')
        with self.assertRaises(ValueError): audit.run_audit(args)

    def test_mutated_receipt_refused(self):
        args = self.args()
        prod.run_plan(args)
        rp = self.results/'v4h_exec_adapter_plan_mock'/prod.RECEIPT_NAME
        r = json.loads(rp.read_text())
        r['full400_authorized'] = True
        rp.write_bytes(prod.canonical(r))
        args.mode='audit';args.adapter_dir=args.out_dir
        args.out_dir=Path('fi/results/v4h_exec_adapter_audit_mock')
        with self.assertRaises(ValueError):audit.run_audit(args)


if __name__ == '__main__':
    unittest.main()

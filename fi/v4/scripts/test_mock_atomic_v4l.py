#!/usr/bin/env python3
"""V4L mock-only atomic journal tests: zero Vivado/XSim/real FI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import mock_atomic_ledger_v4l as p
import audit_mock_atomic_v4l as a


class MockFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.results = self.repo / 'fi/results'
        self.results.mkdir(parents=True)
        self.ad = self.results / 'v4h_exec_adapter_plan_01'
        self.ad.mkdir()
        self.aa = self.results / 'v4h_exec_adapter_audit_01'
        self.aa.mkdir()
        self.out = self.results / 'v4l_mocktxn_01'
        self.audit_out = self.results / 'v4l_mocktxn_audit_01'
        self.make_adapter()

    def make_adapter(self) -> None:
        rows = []
        for i in range(1, 401):
            rows.append({'ordinal': i, 'scenario_id':f'FI_P400_{i:04d}',
                         'batch_number':(i-1)//25+1, 'frozen_tuple_sha256': p.digest(str(i).encode()),
                         'attempt_state':'UNCREATED_CONTRACT_ONLY',
                         'checkpoint_committed':False,'raw_xsim_log_observed':False,
                         'design_outcome_observed':False,'ready_to_execute':False})
        adapter = {'schema':'CNN_PEP_V4H_EXEC_ADAPTER_CONTRACT_CANDIDATE_V1',
                   'scope':'OFFLINE_ENGINEERING_CANDIDATE_NO_BUILD_NO_LAUNCH_NO_FI',
                   'frozen_scenarios':400,'batch_count':16,'batch_size':25,
                   'raw_evidence_committed':0,'new_xsim_runs':0,'real_fi_results':0,
                   'source_snapshot_built':False,'attempt_budget_8_of_8_exhausted':True,
                   'full400_authorized':False,'full400_executed':False,
                   'G6_production_qualified':False,'G7_scientific_approved':False,
                   'G0_G7_signed_authorization':False,'adapter_generator_sha256':p.digest(b'v4h-adapter'),
                   'release_gate':'FULL400_BLOCKED_G6_EXECUTOR_NOT_QUALIFIED_G7_NOT_APPROVED',
                   'invocations':rows}
        self.write(self.ad/p.ADAPTER_NAME, adapter)
        receipt = {'schema':'CNN_PEP_V4H_EXEC_ADAPTER_RECEIPT_CANDIDATE_V1',
                   'adapter_plan_sha256':p.hash_file(self.ad/p.ADAPTER_NAME),
                   'full400_authorized':False,'new_xsim_runs':0,'source_snapshot_built':False}
        self.write(self.ad/p.ADAPTER_RECEIPT_NAME, receipt)
        audit = {'schema':'CNN_PEP_V4H_EXEC_INDEPENDENT_OFFLINE_AUDIT_V1',
                 'adapter_sha256':p.hash_file(self.ad/p.ADAPTER_NAME),
                 'adapter_receipt_sha256':p.hash_file(self.ad/p.ADAPTER_RECEIPT_NAME),
                 'mapped_contracts_verified':400,'future_artifact_paths_verified':3200,
                 'full400_authorized':False,'new_xsim_runs':0,'build_once_executed':False,
                 'G6_production_qualified':False}
        self.write(self.aa/p.ADAPTER_AUDIT_NAME, audit)

    @staticmethod
    def write(path: Path, obj: dict) -> None:
        path.write_bytes(p.canonical(obj))

    def probe_args(self, **kw: object) -> argparse.Namespace:
        args = {'mode':'probe','repo':self.repo,'adapter_dir':Path('fi/results/v4h_exec_adapter_plan_01'),
                'adapter_audit_dir':Path('fi/results/v4h_exec_adapter_audit_01'),
                'out_dir':Path('fi/results/v4l_mocktxn_01'), 'execute':False,'approve':False}
        args.update(kw)
        return argparse.Namespace(**args)

    def audit_args(self, **kw: object) -> argparse.Namespace:
        args = {'mode':'audit','repo':self.repo,'adapter_dir':Path('fi/results/v4h_exec_adapter_plan_01'),
                'adapter_audit_dir':Path('fi/results/v4h_exec_adapter_audit_01'),
                'probe_dir':Path('fi/results/v4l_mocktxn_01'),
                'out_dir':Path('fi/results/v4l_mocktxn_audit_01'),
                'execute':False,'approve':False}
        args.update(kw)
        return argparse.Namespace(**args)

    def probe(self) -> None:
        p.run_probe(self.probe_args())

    def audit(self) -> None:
        a.run_audit(self.audit_args())

    def mutate(self, path: Path, fn) -> None:
        obj = json.loads(path.read_text(encoding='utf-8'))
        fn(obj)
        self.write(path,obj)

    def attempt(self, n: int, sid: str) -> Path:
        return self.out/'attempts'/f'{n:04d}_{sid}'

    def assert_audit_rejects(self) -> None:
        with self.assertRaises((ValueError,OSError,TypeError,KeyError)):
            self.audit()
        self.assertFalse(self.audit_out.exists())


class PolicyTests(MockFixture):
    def test_scope_lock_rejects_execute(self):
        with self.assertRaisesRegex(ValueError,'rejects execute'):
            p.run_probe(self.probe_args(execute=True))
        self.assertFalse(self.out.exists())

    def test_scope_lock_rejects_approve(self):
        with self.assertRaises(ValueError):p.run_probe(self.probe_args(approve=True))
        self.assertFalse(self.out.exists())

    def test_scope_lock_rejects_full400_mode(self):
        with self.assertRaises(ValueError):p.run_probe(self.probe_args(mode='full400'))

    def test_scope_lock_rejects_build_mode(self):
        with self.assertRaises(ValueError):p.run_probe(self.probe_args(mode='build'))

    def test_audit_scope_lock_rejects_execute(self):
        with self.assertRaises(ValueError):a.run_audit(self.audit_args(mode='execute'))

    def test_audit_scope_lock_rejects_approve(self):
        with self.assertRaises(ValueError):a.run_audit(self.audit_args(approve=True))

    def test_outside_repo_refused(self):
        with self.assertRaises(ValueError):p.run_probe(self.probe_args(out_dir=Path(self.temp.name)/'elsewhere'))

    def test_path_traversal_refused(self):
        with self.assertRaises(ValueError):p.run_probe(self.probe_args(out_dir=Path('fi/results/../v4l_mocktxn_fake')))

    def test_wrong_namespace_refused(self):
        with self.assertRaises(ValueError):p.run_probe(self.probe_args(out_dir=Path('fi/results/old_fi_01')))

    def test_output_exists_no_overwrite(self):
        self.out.mkdir()
        with self.assertRaises(ValueError):self.probe()

    def test_inflight_exists_no_overwrite(self):
        (self.results/'.v4l_mocktxn_01.inflight').mkdir()
        with self.assertRaises(ValueError):self.probe()

    def test_missing_adapter_refused(self):
        (self.ad/p.ADAPTER_NAME).unlink()
        with self.assertRaises(ValueError):self.probe()

    def test_forged_adapter_release_refused(self):
        self.mutate(self.ad/p.ADAPTER_NAME, lambda x:x.update(full400_authorized=True))
        with self.assertRaises(ValueError):self.probe()

    def test_modified_frozen_tuple_refused_by_parent_audit(self):
        self.mutate(self.ad/p.ADAPTER_NAME,
                    lambda x:x['invocations'][0].update(frozen_tuple_sha256=p.digest(b'forged')))
        with self.assertRaises(ValueError):self.probe()

    def test_duplicate_adapter_scenario_refused(self):
        self.mutate(self.ad/p.ADAPTER_NAME,
                    lambda x:x['invocations'][1].update(scenario_id='FI_P400_0001'))
        with self.assertRaises(ValueError):self.probe()

    def test_bad_adapter_batch_refused(self):
        self.mutate(self.ad/p.ADAPTER_NAME,
                    lambda x:x['invocations'][25].update(batch_number=1))
        with self.assertRaises(ValueError):self.probe()

    def test_mutated_parent_audit_refused(self):
        self.mutate(self.aa/p.ADAPTER_AUDIT_NAME, lambda x:x.update(mapped_contracts_verified=399))
        with self.assertRaises(ValueError):self.probe()

    def test_adapter_receipt_hash_changed_refused(self):
        self.mutate(self.ad/p.ADAPTER_RECEIPT_NAME, lambda x:x.update(new_xsim_runs=1))
        with self.assertRaises(ValueError):self.probe()

    def test_duplicate_json_key_rejected(self):
        path = self.ad/p.ADAPTER_RECEIPT_NAME
        path.write_bytes(b'{"schema":1,"schema":2}\n')
        with self.assertRaises(ValueError):self.probe()

    def test_noncanonical_crlf_rejected(self):
        path = self.ad/p.ADAPTER_RECEIPT_NAME
        path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
        with self.assertRaises(ValueError):self.probe()

    def test_symlink_parent_denied_when_supported(self):
        folder = self.results/'v4l_mocktxn_alias'
        try:
            folder.symlink_to(self.ad, target_is_directory=True)
        except (OSError,NotImplementedError) as e:
            self.skipTest('symlink creation denied by host: '+str(e))
        with self.assertRaises(ValueError):
            p.run_probe(self.probe_args(adapter_dir=Path('fi/results/v4l_mocktxn_alias')))

    def test_no_simulator_imports_in_producer(self):
        src = (SCRIPT_DIR/'mock_atomic_ledger_v4l.py').read_text(encoding='utf-8')
        self.assertNotIn('import subprocess',src)
        self.assertNotIn('import ctypes',src)
        self.assertNotIn('os._exit(',src)


class JournalTests(MockFixture):
    def setUp(self):
        super().setUp()
        self.probe()

    def test_full_four_case_audit(self):
        self.audit()
        self.assertTrue((self.audit_out/'v4l_mock_atomic_independent_audit.json').is_file())

    def test_four_attempts_three_scenarios(self):
        manifest = p.strict_json(self.out/p.MANIFEST_NAME)
        self.assertEqual(len(manifest['attempts']),4)
        self.assertEqual(len(set(x['scenario_id'] for x in manifest['attempts'])),3)

    def test_uncommitted_validated_never_committed(self):
        path = self.attempt(2,'FI_P400_0002')
        self.assertFalse((path/'state_03_mock_committed.json').exists())
        self.assertTrue((path/'state_02_validated_mock_only.json').exists())

    def test_timeout_not_design_noncompletion(self):
        path = self.attempt(3,'FI_P400_0003')
        self.assertTrue((path/'state_02_invalid_infrastructure.json').exists())
        self.assertFalse((path/'raw_mock.json').exists())

    def test_recovery_is_new_attempt_parent_sha(self):
        failed = self.attempt(3,'FI_P400_0003')/'state_02_invalid_infrastructure.json'
        recovered = self.attempt(4,'FI_P400_0003')/'intent.json'
        self.assertEqual(p.strict_json(recovered)['retry_parent_failure_sha256'], p.hash_file(failed))

    def test_no_rewrite_same_output(self):
        with self.assertRaises(ValueError): self.probe()

    def test_audit_no_rewrite_same_output(self):
        self.audit()
        with self.assertRaises(ValueError): self.audit()

    def test_orphan_probe_root_refused(self):
        (self.out/'unexpected.txt').write_text('orphan')
        self.assert_audit_rejects()

    def test_orphan_attempt_refused(self):
        (self.attempt(1,'FI_P400_0001')/'orphan.log').write_text('unexpected')
        self.assert_audit_rejects()

    def test_missing_attempt_refused(self):
        shutil.rmtree(self.attempt(2,'FI_P400_0002'))
        self.assert_audit_rejects()

    def test_missing_raw_refused(self):
        (self.attempt(1,'FI_P400_0001')/'raw_mock.json').unlink()
        self.assert_audit_rejects()

    def test_mutated_raw_refused(self):
        self.mutate(self.attempt(1,'FI_P400_0001')/'raw_mock.json',
                    lambda x:x.update(mock_observation=p.digest(b'fake')))
        self.assert_audit_rejects()

    def test_raw_claim_fi_refused(self):
        self.mutate(self.attempt(1,'FI_P400_0001')/'raw_mock.json',
                    lambda x:x.update(fi_applied=True))
        self.assert_audit_rejects()

    def test_raw_claim_design_outcome_refused(self):
        self.mutate(self.attempt(1,'FI_P400_0001')/'raw_mock.json',
                    lambda x:x.update(classification_available=True))
        self.assert_audit_rejects()

    def test_interrupted_promoted_to_commit_refused(self):
        path = self.attempt(2,'FI_P400_0002')/'state_03_mock_committed.json'
        self.write(path,{'status':'MOCK_COMMITTED'})
        self.assert_audit_rejects()

    def test_timeout_promoted_to_noncompletion_refused(self):
        path = self.attempt(3,'FI_P400_0003')/'state_02_invalid_infrastructure.json'
        self.mutate(path,lambda x:x.update(status='NONCOMPLETION'))
        self.assert_audit_rejects()

    def test_wrong_event_order_refused(self):
        path = self.attempt(1,'FI_P400_0001')/'state_01_inflight.json'
        self.mutate(path,lambda x:x.update(status='PENDING'))
        self.assert_audit_rejects()

    def test_event_sha_chain_refused(self):
        path = self.attempt(1,'FI_P400_0001')/'state_02_validated_mock_only.json'
        self.mutate(path,lambda x:x.update(previous_event_sha256=p.digest(b'drift')))
        self.assert_audit_rejects()

    def test_parent_failed_attempt_hash_refused(self):
        path = self.attempt(4,'FI_P400_0003')/'intent.json'
        self.mutate(path,lambda x:x.update(retry_parent_failure_sha256=p.digest(b'forged')))
        self.assert_audit_rejects()

    def test_forged_mock_committed_counter_refused(self):
        path = self.out/p.MANIFEST_NAME
        self.mutate(path,lambda x:x.update(mock_committed=4))
        self.assert_audit_rejects()

    def test_forged_full400_authorization_refused(self):
        path = self.out/p.MANIFEST_NAME
        self.mutate(path,lambda x:x.update(full400_authorized=True))
        self.assert_audit_rejects()

    def test_fake_fi_results_refused(self):
        path = self.out/p.MANIFEST_NAME
        self.mutate(path,lambda x:x.update(real_fi_results=2))
        self.assert_audit_rejects()

    def test_manifest_extra_field_refused(self):
        path = self.out/p.MANIFEST_NAME
        self.mutate(path,lambda x:x.update(execute=True))
        self.assert_audit_rejects()

    def test_modified_receipt_sha_refused(self):
        path = self.out/p.RECEIPT_NAME
        self.mutate(path,lambda x:x.update(manifest_sha256=p.digest(b'wrong')))
        self.assert_audit_rejects()

    def test_noncanonical_receipt_crlf_refused(self):
        path = self.out/p.RECEIPT_NAME
        path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
        self.assert_audit_rejects()

    def test_duplicate_manifest_json_key_refused(self):
        (self.out/p.MANIFEST_NAME).write_bytes(b'{"schema":1,"schema":2}\n')
        self.assert_audit_rejects()

    def test_source_adapter_drift_post_probe_refused(self):
        self.mutate(self.ad/p.ADAPTER_NAME,
                    lambda x:x['invocations'][1].update(frozen_tuple_sha256=p.digest(b'drift')))
        self.assert_audit_rejects()

    def test_source_audit_drift_post_probe_refused(self):
        self.mutate(self.aa/p.ADAPTER_AUDIT_NAME,
                    lambda x:x.update(new_xsim_runs=1))
        self.assert_audit_rejects()

    def test_generator_hash_in_manifest_refused(self):
        path = self.out/p.MANIFEST_NAME
        self.mutate(path,lambda x:x.update(transaction_generator_sha256=p.digest(b'wrong')))
        self.assert_audit_rejects()

    def test_attempt_intent_scenario_mutation_refused(self):
        path = self.attempt(4,'FI_P400_0003')/'intent.json'
        self.mutate(path,lambda x:x.update(scenario_id='FI_P400_0004'))
        self.assert_audit_rejects()

    def test_attempt_unexpected_json_key_refused(self):
        path = self.attempt(1,'FI_P400_0001')/'state_03_mock_committed.json'
        self.mutate(path,lambda x:x.update(production_result='WRONG_LABEL'))
        self.assert_audit_rejects()

    def test_inflight_probe_stage_refused(self):
        (self.results/'.v4l_mocktxn_01.inflight').mkdir()
        self.assert_audit_rejects()

    def test_symlink_payload_denied_if_supported(self):
        root = self.attempt(1,'FI_P400_0001')
        file = root/'raw_mock.json'
        moved = root/'raw_saved.txt'
        file.rename(moved)
        try: file.symlink_to(moved)
        except (OSError,NotImplementedError) as e:
            self.skipTest('symlink creation denied by host: '+str(e))
        self.assert_audit_rejects()


if __name__ == '__main__':
    unittest.main()

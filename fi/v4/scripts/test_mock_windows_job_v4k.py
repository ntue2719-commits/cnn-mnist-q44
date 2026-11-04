#!/usr/bin/env python3
"""V4K test-only mock supervisor: no Vivado, XSim, FI or real campaign."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import mock_windows_job_v4k as p
import audit_mock_windows_job_v4k as a


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='v4k_tests_')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / 'fi/results').mkdir(parents=True)
        self.out = self.repo / 'fi/results/v4k_mockjob_01'
        self.out.mkdir()
        source = p.source_path(HERE / p.CHILD_NAME)
        self.source_sha = p.sha_file(source)
        self.events = [self.event(kind, i+101) for i,kind in enumerate(('healthy','timeout','tree'))]
        self.probe = {
            'schema':p.SCHEMA, 'scope':'BENIGN_FIXED_PYTHON_CHILD_WINDOWS_JOB_OBJECT_ONLY_NOT_XSIM',
            'created_utc':'2026-11-04T00:00:00+00:00', 'mock_child_source_sha256':self.source_sha,
            'supervisor_source_sha256':p.sha_file(Path(p.__file__)),
            'host_platform':'win32','python_version':[3,14,0], 'job_object_cases':self.events,
            'planned_fi_scenarios':400, 'real_fi_runs_executed':0,
            'vivado_builds_executed':0,'xsim_invocations_executed':0,
            'scientific_outcomes_committed':0,'g6_production_qualified':False,
            'g7_scientific_approved':False,'full400_authorized':False,'release_gate':p.STOP,
        }
        self.flush()

    def event(self, kind: str, pid: int):
        return {
            'case':kind,'mock_python_child':True,'python_pid':pid,
            'child_source_sha256':self.source_sha,
            'active_processes_initial_observed':0 if kind=='healthy' else 1,
            'active_processes_before_termination':None if kind=='healthy' else (2 if kind=='tree' else 1),
            'active_processes_after_completion':0,
            'terminated_job_object':kind!='healthy',
            'infrastructure_timeout':kind=='timeout',
            'design_noncompletion':False,'fi_applied_marker_observed':False,
            'fi_result_marker_observed':False,
            'mock_handshake_confirmed':kind!='timeout',
            'elapsed_seconds':0.22,
            'exit_code_after_completion':0 if kind=='healthy' else 87,
        }

    def flush(self):
        (self.out / p.PROBE_NAME).write_bytes(p.canonical(self.probe))
        receipt = {'schema':p.RECEIPT_SCHEMA,
                   'probe_sha256':p.sha_file(self.out / p.PROBE_NAME),
                   'case_count':3, 'mock_source_sha256':self.source_sha,
                   'fi_outcomes_committed':0,'new_xsim_runs':0,
                   'full400_authorized':False,'release_gate':p.STOP}
        (self.out / p.RECEIPT_NAME).write_bytes(p.canonical(receipt))
        (self.out / p.ACKS[0]).write_bytes(b'V4K_PARENT_AND_CHILD_READY\n')
        (self.out / p.ACKS[1]).write_bytes(b'V4K_GRANDCHILD_ALIVE\n')

    def validate(self):
        return a.audit(self.repo, self.out, HERE / p.CHILD_NAME)


class AuditorGuards(Fixture):
    def test_happy(self):
        self.assertEqual(self.validate()['fixed_mock_cases_replayed'], 3)

    def test_wrong_source_hash_rejected(self):
        self.probe['mock_child_source_sha256'] = '0'*64
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_supervisor_source_drift_rejected(self):
        self.probe['supervisor_source_sha256'] = 'a'*64
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_contradictory_extra_approval_rejected(self):
        self.probe['release_approved'] = True
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_xsim_claim_rejected(self):
        self.probe['xsim_invocations_executed'] = 1
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_g6_claim_rejected(self):
        self.probe['g6_production_qualified'] = True
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_full400_claim_rejected(self):
        self.probe['full400_authorized'] = True
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_fi_result_rejected(self):
        self.events[0]['fi_result_marker_observed'] = True
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_mock_timeout_is_not_rtl(self):
        self.events[1]['design_noncompletion'] = True
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_extra_event_field_rejected(self):
        self.events[0]['fault_injection_performed'] = True
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_timeout_misclassified_rejected(self):
        self.events[1]['infrastructure_timeout'] = False
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_uncontained_grandchild_rejected(self):
        self.events[2]['active_processes_before_termination'] = 1
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_not_killed_rejected(self):
        self.events[2]['terminated_job_object'] = False
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_leak_rejected(self):
        self.events[2]['active_processes_after_completion'] = 1
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_invalid_exit_code_rejected(self):
        self.events[1]['exit_code_after_completion'] = 0
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_duplicate_pid_rejected(self):
        self.events[1]['python_pid'] = self.events[0]['python_pid']
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_reordered_events_rejected(self):
        self.probe['job_object_cases'].reverse()
        self.flush()
        with self.assertRaises(ValueError): self.validate()

    def test_receipt_hash_mutation_rejected(self):
        (self.out / p.PROBE_NAME).write_bytes((self.out / p.PROBE_NAME).read_bytes() + b' ')
        with self.assertRaises(ValueError): self.validate()

    def test_duplicate_json_key_rejected(self):
        raw = (self.out / p.PROBE_NAME).read_bytes()
        raw = raw.replace(b'"schema":', b'"schema": "forged", "schema":',1)
        (self.out / p.PROBE_NAME).write_bytes(raw)
        with self.assertRaises(ValueError): self.validate()

    def test_crlf_rejected(self):
        raw = (self.out / p.PROBE_NAME).read_bytes().replace(b'\n',b'\r\n')
        (self.out / p.PROBE_NAME).write_bytes(raw)
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_ack_rejected(self):
        (self.out / p.ACKS[1]).write_bytes(b'forged\n')
        with self.assertRaises(ValueError): self.validate()

    def test_missing_ack_rejected(self):
        (self.out / p.ACKS[0]).unlink()
        with self.assertRaises(ValueError): self.validate()

    def test_extra_orphan_rejected(self):
        (self.out / 'orphan.txt').write_text('orphan')
        with self.assertRaises(ValueError): self.validate()

    def test_large_file_rejected(self):
        (self.out / p.PROBE_NAME).write_bytes(b'a' * 65537)
        with self.assertRaises(ValueError): self.validate()

    def test_audit_does_not_modify_existing_evidence(self):
        before = {x.name:p.sha_file(x) for x in self.out.iterdir()}
        self.validate()
        self.assertEqual(before,{x.name:p.sha_file(x) for x in self.out.iterdir()})

    def test_output_wrong_namespace_rejected(self):
        with self.assertRaises(ValueError):
            p.new_output(self.repo, Path('fi/results/v4j_wrong_01'))

    def test_output_collision_rejected(self):
        with self.assertRaises(ValueError):
            p.new_output(self.repo, self.out)

    def test_inflight_collision_rejected(self):
        path = self.repo / 'fi/results/.v4k_mockjob_04.inflight'
        path.mkdir()
        with self.assertRaises(ValueError):
            p.new_output(self.repo, Path('fi/results/v4k_mockjob_04'))

    def test_future_output_valid(self):
        o,s = p.new_output(self.repo, Path('fi/results/v4k_mockjob_04'))
        self.assertEqual(o.name, 'v4k_mockjob_04')
        self.assertEqual(s.name,'.v4k_mockjob_04.inflight')

    def test_symlink_or_junction_rejected_if_supported(self):
        testlink = self.repo / 'fi/results/v4k_mockjob_symlink'
        try:
            testlink.symlink_to(self.out, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation denied by host')
        with self.assertRaises(ValueError):
            p.new_output(self.repo, testlink)


class CliGuards(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(HERE/'mock_windows_job_v4k.py'), *args],
                              text=True, capture_output=True, timeout=10)

    def test_execute_mode_rejected(self):
        v = self.run_cli('--mode','execute')
        self.assertNotEqual(v.returncode,0)
        self.assertIn('BLOCKED', v.stderr.upper())

    def test_full400_mode_rejected(self):
        v = self.run_cli('--mode','full400')
        self.assertNotEqual(v.returncode,0)

    def test_approval_flag_rejected(self):
        v = self.run_cli('--mode','probe','--approve')
        self.assertNotEqual(v.returncode,0)

    def test_execute_flag_rejected(self):
        v = self.run_cli('--mode','probe','--execute')
        self.assertNotEqual(v.returncode,0)

    def test_no_dynamic_executable_argument(self):
        v = self.run_cli('--mode','probe','--command','xsim')
        self.assertNotEqual(v.returncode,0)

    @unittest.skipUnless(sys.platform == 'win32', 'Actual Job Object OS probe must run on Windows')
    def test_windows_job_api_present(self):
        self.assertIsNotNone(p.K)
        self.assertIsNotNone(p.CreateJobObjectW if hasattr(p,'CreateJobObjectW') else p.K.CreateJobObjectW)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""V4Q offline mock and fail-closed tests; no Vivado, XSim or RTL execution."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("consumer_negative_controls_v4q", HERE / "consumer_negative_controls_v4q.py")
v4q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4q)


class V4QTest(unittest.TestCase):
    def fixture(self, purpose="NC1_NOT_CONSUMED", injected=True, accept=None):
        return v4q.synthetic_fixture("V4Q_NC1", purpose, inject=injected, accepted_at=accept)

    def assert_rejected(self, record):
        with self.assertRaises(v4q.TraceError):
            v4q.validate_trace(record)

    def test_expected_mock_classes(self):
        rows = [v4q.validate_trace(x, v4q.digest(v4q.canonical(x))) for x in v4q.demo_cases()]
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["observed_status"], "NO_INJECTION_BASELINE_NOT_AN_E3_NEGATIVE")
        self.assertEqual(rows[1]["observed_status"], "E3_NO_CONSUMPTION_IN_DECLARED_SCOPE_MOCK_ONLY")
        self.assertEqual(rows[2]["observed_status"], "E3_TRUE_WITNESS_MOCK_ONLY")
        self.assertEqual(rows[3]["observed_status"], "E3_TRUE_WITNESS_MOCK_ONLY")
        self.assertEqual(rows[4]["observed_status"], "E3_TRUE_WITNESS_MOCK_ONLY")
        self.assertEqual(rows[5]["observed_status"], "E3_NOT_MONITORED")
        self.assertEqual(rows[6]["observed_status"], "E3_INCONCLUSIVE_PARTIAL_COVERAGE")
        self.assertTrue(all(not x["real_e3_qualified"] for x in rows))

    def test_all_mock_proof_flags_false(self):
        for trace in v4q.demo_cases():
            result = v4q.validate_trace(trace)
            self.assertFalse(result["full400_authorized"])
            self.assertFalse(result["g7_scientific_approved"])
            self.assertFalse(result["g6_production_qualified"])
            self.assertEqual(result["real_rtl_injections"], 0)

    def test_negative_never_inferred_from_no_markers(self):
        t = self.fixture()
        t["timeline"]["edges"] = []
        t["timeline"]["terminal"] = "WINDOW_OPEN"
        self.assertEqual(v4q.validate_trace(t)["observed_status"], "E3_NOT_MONITORED")

    def test_missing_middle_clock_not_negative(self):
        t = self.fixture()
        t["timeline"]["edges"].pop(1)
        self.assertEqual(v4q.validate_trace(t)["observed_status"], "E3_INCONCLUSIVE_PARTIAL_COVERAGE")

    def test_missing_declared_consumer_not_negative(self):
        t = self.fixture()
        t["timeline"]["edges"][1]["samples"].pop()
        self.assertEqual(v4q.validate_trace(t)["observed_status"], "E3_INCONCLUSIVE_PARTIAL_COVERAGE")

    def test_open_window_not_negative(self):
        t = self.fixture()
        t["timeline"]["terminal"] = "WINDOW_OPEN"
        t["timeline"]["edges"][-1]["overwrite_after_edge"] = False
        self.assertEqual(v4q.validate_trace(t)["observed_status"], "E3_INCONCLUSIVE_PARTIAL_COVERAGE")

    def test_complete_inference_end_allowed_in_mock_only(self):
        t = self.fixture()
        t["timeline"]["terminal"] = "INFERENCE_END"
        t["timeline"]["edges"][-1]["overwrite_after_edge"] = False
        result = v4q.validate_trace(t)
        self.assertTrue(result["coverage_complete"])
        self.assertFalse(result["real_e3_qualified"])

    def test_external_never_auto_promotes(self):
        t = self.fixture()
        t["evidence_kind"] = "EXTERNAL_TRACE_UNVERIFIED"
        result = v4q.validate_trace(t)
        self.assertEqual(result["observed_status"], "E3_FALSE_CANDIDATE_UNAPPROVED_UNVERIFIED")
        self.assertFalse(result["real_e3_qualified"])
        self.assertFalse(result["full400_authorized"])

    def test_external_positive_also_unverified(self):
        t = self.fixture(accept=(5, "C1"))
        t["evidence_kind"] = "EXTERNAL_TRACE_UNVERIFIED"
        result = v4q.validate_trace(t)
        self.assertEqual(result["observed_status"], "E3_TRUE_CANDIDATE_UNVERIFIED")
        self.assertFalse(result["real_e3_qualified"])

    def test_readback_must_match_xor(self):
        t = self.fixture()
        t["fault"]["readback"] = "00"
        self.assert_rejected(t)

    def test_fault_is_exactly_one_bit(self):
        t = self.fixture()
        t["fault"]["new"] = t["fault"]["readback"] = "03"
        self.assert_rejected(t)

    def test_fault_event_must_be_one(self):
        t = self.fixture()
        t["fault"]["event_count"] = 0
        self.assert_rejected(t)

    def test_no_injection_cannot_fake_fault(self):
        t = self.fixture(injected=False)
        t["fault"]["event_count"] = 1
        self.assert_rejected(t)

    def test_wrong_phase_rejected(self):
        t = self.fixture()
        t["timeline"]["edges"][0]["phase"] = "POST_NBA"
        self.assert_rejected(t)

    def test_same_cycle_sampling_rejected(self):
        t = self.fixture()
        t["timeline"]["start_cycle"] = 3
        self.assert_rejected(t)

    def test_noncontiguous_source_state_rejected(self):
        t = self.fixture()
        t["timeline"]["edges"][1]["source_faulty"] = "00"
        self.assert_rejected(t)

    def test_unknown_value_x_rejected(self):
        t = self.fixture(accept=(4, "C0"))
        t["timeline"]["edges"][0]["samples"][0]["faulty_value"] = "0x"
        self.assert_rejected(t)

    def test_accepted_values_must_match_clocked_bus(self):
        t = self.fixture(accept=(4, "C0"))
        t["timeline"]["edges"][0]["samples"][0]["golden_value"] = "01"
        self.assert_rejected(t)

    def test_unaccepted_does_not_have_a_sample(self):
        t = self.fixture()
        t["timeline"]["edges"][0]["samples"][0]["faulty_value"] = "01"
        self.assert_rejected(t)

    def test_duplicate_consumer_id_rejected(self):
        t = self.fixture()
        t["coverage"]["declared_channels"][1]["id"] = "C0"
        self.assert_rejected(t)

    def test_duplicate_sample_rejected(self):
        t = self.fixture()
        t["timeline"]["edges"][0]["samples"].append(copy.deepcopy(t["timeline"]["edges"][0]["samples"][0]))
        self.assert_rejected(t)

    def test_duplicate_edge_cycle_rejected(self):
        t = self.fixture()
        t["timeline"]["edges"][1]["cycle"] = 4
        self.assert_rejected(t)

    def test_second_overwrite_rejected(self):
        t = self.fixture()
        t["timeline"]["edges"][0]["overwrite_after_edge"] = True
        self.assert_rejected(t)

    def test_unknown_consumer_is_rejected(self):
        t = self.fixture()
        t["timeline"]["edges"][0]["samples"][0]["consumer_id"] = "C99"
        self.assert_rejected(t)

    def test_unreviewed_scope_cannot_be_auto_approved(self):
        t = self.fixture()
        t["coverage"]["mapping_review"] = "REVIEWED_BY_AI"
        self.assert_rejected(t)

    def test_control_decision_not_supported_by_data_model(self):
        t = self.fixture()
        t["coverage"]["declared_channels"][0]["kind"] = "CONTROL_DECISION"
        self.assert_rejected(t)

    def test_forged_outcome_not_considered_e3(self):
        t = self.fixture()
        t["outcome"]["class"] = "WRONG_LABEL"
        self.assertEqual(v4q.validate_trace(t)["observed_status"],
                         "E3_NO_CONSUMPTION_IN_DECLARED_SCOPE_MOCK_ONLY")

    def test_outcome_completeness_cannot_be_faked(self):
        t = self.fixture()
        t["outcome"]["observed"] = False
        self.assert_rejected(t)

    def test_canonical_json_and_duplicate_keys(self):
        t = self.fixture()
        raw = v4q.canonical(t)
        self.assertTrue(raw.endswith(b"\n") and b"\r" not in raw)
        self.assertEqual(v4q.read_json_unique(raw), t)
        with self.assertRaises(v4q.TraceError):
            v4q.read_json_unique(b'{"schema":"x", "schema":"y"}')

    def test_invalid_json_constant_rejected(self):
        with self.assertRaises(v4q.TraceError):
            v4q.read_json_unique(b'{"a":NaN}')

    def test_demo_output_and_audit_and_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fresh_v4q"
            v4q.build(out)
            v4q.audit(out)
            self.assertEqual(len(list((out / "cases").glob("*.json"))), 7)
            self.assertEqual(len(list((out / "case_results").glob("*.json"))), 7)
            with self.assertRaises(v4q.TraceError):
                v4q.build(out)
            path = out / "case_results" / "V4Q_NC1.json"
            path.write_bytes(path.read_bytes().replace(b"MOCK_ONLY", b"RTL_TRUE"))
            with self.assertRaises(v4q.TraceError):
                v4q.audit(out)

    def test_symlink_extra_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "proof"
            v4q.build(out)
            try:
                (out / "fake_link").symlink_to(Path(td) / "nonexistent")
            except (NotImplementedError, OSError):
                self.skipTest("symlink unsupported on host")
            with self.assertRaises(v4q.TraceError):
                v4q.audit(out)

    def test_user_cli_never_accepts_execute(self):
        p = subprocess.run([sys.executable, str(HERE / "consumer_negative_controls_v4q.py"),
                            "--mode", "execute"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("invalid choice", p.stderr)

    def test_candidate_json_inspect_no_files_written(self):
        with tempfile.TemporaryDirectory() as td:
            t = self.fixture()
            t["evidence_kind"] = "EXTERNAL_TRACE_UNVERIFIED"
            path = Path(td) / "V4Q_TRACE.json"
            path.write_bytes(v4q.canonical(t))
            p = subprocess.run([sys.executable, str(HERE / "consumer_negative_controls_v4q.py"),
                                "--mode", "inspect", "--trace", str(path)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            report = json.loads(p.stdout)
            self.assertFalse(report["real_e3_qualified"])
            self.assertEqual(report["observed_status"], "E3_FALSE_CANDIDATE_UNAPPROVED_UNVERIFIED")
            self.assertEqual(len(list(Path(td).iterdir())), 1)

    def test_inspect_crlf_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "trace.json"
            path.write_bytes(v4q.canonical(self.fixture()).replace(b"\n", b"\r\n"))
            p = subprocess.run([sys.executable, str(HERE / "consumer_negative_controls_v4q.py"),
                                "--mode", "inspect", "--trace", str(path)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn("V4Q_FAIL_CLOSED", p.stderr)

    def test_complete_missing_edge_with_positive_witness(self):
        t = self.fixture(accept=(4, "C0"))
        t["timeline"]["edges"].pop(1)
        r = v4q.validate_trace(t)
        self.assertEqual(r["observed_status"], "E3_TRUE_WITNESS_MOCK_ONLY")
        self.assertFalse(r["coverage_complete"])

    def test_summary_never_claims_real_fi(self):
        files = v4q.expected_artifacts()
        summary = json.loads(files["V4Q_SUMMARY.json"])
        self.assertEqual(summary["real_fi_injections"], 0)
        self.assertEqual(summary["real_e3_negative_proofs"], 0)
        self.assertFalse(summary["full400_authorized"])
        self.assertEqual(len(files), 18)


if __name__ == "__main__":
    unittest.main()

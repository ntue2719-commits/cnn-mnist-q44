#!/usr/bin/env python3
"""Independent V4Q auditor regression, including re-hashed forgery cases."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, HERE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v4q = load("consumer_negative_controls_v4q.py", "v4q_builder_testing")
auditor = load("audit_consumer_negative_controls_v4q.py", "v4q_independent_testing")


class IndependentAuditTests(unittest.TestCase):
    def with_fresh(self, fn):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "mock_out"
            v4q.build(folder)
            fn(folder)

    def rehash_manifest(self, folder, target):
        manifest_path = folder / "V4Q_OUTPUT_SHA256.json"
        manifest = json.loads(manifest_path.read_bytes())
        rel = target.relative_to(folder).as_posix()
        manifest["files"][rel] = v4q.digest(target.read_bytes())
        manifest_path.write_bytes(v4q.canonical(manifest))

    def test_real_independent_replay(self):
        self.with_fresh(lambda folder: self.assertEqual(auditor.audit(folder)["verified_cases"], 7))

    def test_forged_status_with_rehashed_manifest_fails(self):
        def verify(folder):
            target = folder / "case_results/V4Q_NC1.json"
            data = json.loads(target.read_bytes())
            data["observed_status"] = "E3_TRUE_WITNESS_MOCK_ONLY"
            target.write_bytes(v4q.canonical(data))
            self.rehash_manifest(folder, target)
            with self.assertRaises(auditor.AuditError):
                auditor.audit(folder)
        self.with_fresh(verify)

    def test_forged_authorization_with_rehashed_manifest_fails(self):
        def verify(folder):
            target = folder / "case_results/V4Q_NC1.json"
            data = json.loads(target.read_bytes())
            data["full400_authorized"] = True
            target.write_bytes(v4q.canonical(data))
            self.rehash_manifest(folder, target)
            with self.assertRaises(auditor.AuditError):
                auditor.audit(folder)
        self.with_fresh(verify)

    def test_forged_report_real_claim_fails(self):
        def verify(folder):
            target = folder / "V4Q_REPORT.md"
            target.write_bytes(target.read_bytes().replace(b"NOT RTL evidence", b"REAL E3 PROVED  "))
            self.rehash_manifest(folder, target)
            with self.assertRaises(auditor.AuditError):
                auditor.audit(folder)
        self.with_fresh(verify)

    def test_forged_summary_proof_count_fails(self):
        def verify(folder):
            target = folder / "V4Q_SUMMARY.json"
            data = json.loads(target.read_bytes())
            data["real_e3_negative_proofs"] = 1
            target.write_bytes(v4q.canonical(data))
            self.rehash_manifest(folder, target)
            with self.assertRaises(auditor.AuditError):
                auditor.audit(folder)
        self.with_fresh(verify)

    def test_extra_file_rejected(self):
        def verify(folder):
            (folder / "fake_e3.json").write_text("{}\n")
            with self.assertRaises(auditor.AuditError):
                auditor.audit(folder)
        self.with_fresh(verify)

    def test_missing_clock_in_fixture_self_rehashed_fails(self):
        def verify(folder):
            target = folder / "cases/V4Q_NC1.json"
            data = json.loads(target.read_bytes())
            data["timeline"]["edges"].pop(1)
            target.write_bytes(v4q.canonical(data))
            self.rehash_manifest(folder, target)
            with self.assertRaises(auditor.AuditError):
                auditor.audit(folder)
        self.with_fresh(verify)

    def test_missing_csv_record_rejected(self):
        def verify(folder):
            target = folder / "V4Q_CASES.csv"
            raw = target.read_bytes().splitlines(keepends=True)
            target.write_bytes(b"".join(raw[:-1]))
            self.rehash_manifest(folder, target)
            with self.assertRaises(auditor.AuditError):
                auditor.audit(folder)
        self.with_fresh(verify)


if __name__ == "__main__":
    unittest.main()

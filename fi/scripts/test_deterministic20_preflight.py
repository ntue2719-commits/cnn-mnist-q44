#!/usr/bin/env python3
"""No-simulator preflight tests for the CNN_PEP 20-directed-scenario runner."""
import csv
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

import run_deterministic20 as r
import run_four_class_smoke as legacy

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "fi/scenarios/deterministic20.csv"
CATALOG = ROOT / "fi/config/fi_targets_candidates_v2.csv"


class Directed20Preflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = r.validate(SCENARIOS, CATALOG)

    def replace_row(self, index=0, **fields):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.csv"
            rows = [dict(x) for x in self.data]
            rows[index].update(fields)
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=r.REQUIRED_FIELDS)
                w.writeheader()
                w.writerows(rows)
            with self.assertRaises(ValueError):
                r.validate(path, CATALOG)

    def test_per_class_counts_and_locked(self):
        self.assertEqual(len(self.data), 20)
        self.assertEqual({c: sum(x["target_class"] == c for x in self.data)
                          for c in r.CLASSES}, {c: 5 for c in r.CLASSES})
        self.assertEqual(set(legacy.SPECS) & {x["scenario_id"] for x in self.data},
                         set(legacy.SPECS))

    def test_generated_tcl_uses_appropriate_mode(self):
        project = Path("D:/Share_Data_ubuntu_window/Github/Simulation/Simulation.xpr")
        for row in self.data:
            tcl = r.make_tcl(project, row, "sim_1")
            self.assertIn("run all", tcl)
            self.assertIn("set_property xsim.compile.incremental false $sim", tcl)
            self.assertIn("CNN_PEP_RUNNER_TCL_COMPLETED", tcl)
            self.assertIn("puts $cnn_pep_f {CNN_PEP_RUNNER_TCL_COMPLETED}", tcl)
            self.assertNotIn("puts {CNN_PEP_RUNNER_TCL_COMPLETED}", tcl)
            sid = row["scenario_id"]
            if sid in legacy.SPECS:
                expected = legacy.SPECS[sid]
                self.assertIn("set_property top " + expected["top"], tcl)
                if expected["auto"]:
                    self.assertIn(expected["auto"] + "=1", tcl)
                else:
                    self.assertIn("FI_OFFSET=" + str(expected["offset"]), tcl)
            else:
                self.assertIn("set_property top " + r.GENERIC_TOP, tcl)
                self.assertIn("FI_OFFSET=" + row["injection_offset"], tcl)
                self.assertNotIn("_AUTO=1", tcl)

    def test_invalid_bit_is_rejected(self):
        self.replace_row(bit_index="3")  # CTL_0001 is only 3 bits wide (0..2)

    def test_locked_scenario_cannot_be_retargeted(self):
        self.replace_row(injection_offset="126")

    def test_wrong_class_is_rejected(self):
        self.replace_row(target_class="BUF")

    def test_unknown_site_is_rejected(self):
        self.replace_row(target_id="CTL_9999")

    def test_duplicate_tuple_is_rejected(self):
        self.replace_row(index=2, injection_offset="25")  # FI_CTL_0007 collides with FI_CTL_0006


    def test_no_false_pass_if_stdout_echoes_completion(self):
        # Script echoing its own 'puts' line was previously mistaken for success.
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d / "fake.log"
            done = d / "fake.done"
            done.write_text("CNN_PEP_RUNNER_TCL_COMPLETED\n", encoding="utf-8")
            echoed = "# puts {CNN_PEP_RUNNER_TCL_COMPLETED}\nFI_DISABLED_CONTROL_PASS\nFI_RESULT_V2,FAKE\n"
            cmd = [sys.executable, "-c", "import sys; sys.stdout.write(" + repr(echoed) + ")"]
            with self.assertRaisesRegex(RuntimeError, "completion file"):
                legacy.run_or_fail(cmd, d, log, 30, done)
            self.assertFalse(done.exists(), "Stale marker must be deleted")

    def test_file_backed_marker_and_log_are_both_required(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d / "real.log"
            done = d / "real.done"
            data = "FI_DISABLED_CONTROL_PASS\nFI_RESULT_V2,FI_CTL_0005,\n"
            cmd = [sys.executable, "-c", (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).write_text('CNN_PEP_RUNNER_TCL_COMPLETED\\n'); "
                "sys.stdout.write(sys.argv[2])"
            ), str(done), data]
            legacy.run_or_fail(cmd, d, log, 30, done)
            self.assertTrue(done.is_file())

    def test_strict_log_rejects_fake_success(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "FI_CTL_0005.log"
            bad.write_text("CNN_PEP_RUNNER_TCL_COMPLETED\nFI_RESULT_V2,FI_CTL_0005,\n")
            with self.assertRaises(RuntimeError):
                r.strict_log_check(bad, "FI_CTL_0005")


if __name__ == "__main__":
    unittest.main(verbosity=2)

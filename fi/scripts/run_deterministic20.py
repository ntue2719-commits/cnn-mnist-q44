#!/usr/bin/env python3
"""CNN_PEP 20-scenario directed FI regression (4 locked + 16 candidates).

Safe default is preparation only; --execute runs Vivado/XSim serially. Does not
modify P0 RTL or overwrite the already validated four-class replay directory.
The 16 new scenarios are candidates until simulator/strict-parser acceptance.

Requires run_four_class_smoke.py, parse_fi_v2.py and summarize_campaign.py in
fi/scripts/, and the four smoke TBs + generic campaign TB in Vivado sim_1.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

# Existing four-class replay is the single authoritative source for locked
# scenario parameters, testbench tops and expected outcomes.
import run_four_class_smoke as locked

REQUIRED_FIELDS = ("scenario_id", "sample_id", "target_id", "target_class",
                   "bit_index", "injection_offset")
CLASSES = ("CTL", "MEM", "BUF", "DAT")
GENERIC_TOP = "tb_cnn_mnist_fi_campaign_v2"
# These two offsets are estimates derived from sequential writes, not yet
# confirmed by a per-target event/consumer temporal monitor in XSim.
PROVISIONAL_TIMING = {"FI_MEM1_0005", "FI_BUF_0005"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"Missing CSV: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not all(k in reader.fieldnames for k in required):
            raise ValueError(f"CSV missing columns {required}: {path}")
        rows = [{k: (value or "").strip() for k, value in r.items() if k}
                for r in reader]
    return rows


def validate(scenario_file: Path, catalog_file: Path) -> list[dict[str, str]]:
    rows = read_csv(scenario_file, REQUIRED_FIELDS)
    targets = read_csv(catalog_file, ("target_id", "class", "width", "bit_min", "bit_max"))
    target_by_id = {}
    for target in targets:
        tid = target["target_id"]
        if not tid or tid in target_by_id:
            raise ValueError(f"Invalid/duplicate target ID in catalog: {tid!r}")
        target_by_id[tid] = target
    if len(rows) != 20:
        raise ValueError(f"Expected 20 scenarios, found {len(rows)}")
    ids = [r["scenario_id"] for r in rows]
    if any(not re.fullmatch(r"FI_[A-Z0-9_]+", sid) for sid in ids):
        raise ValueError("Scenario ID contains unsafe filename/Tcl characters")
    if len(set(ids)) != 20:
        raise ValueError("Duplicate scenario IDs")
    counts = {c: 0 for c in CLASSES}
    seen_tuples = set()
    for r in rows:
        sid = r["scenario_id"]
        klass = r["target_class"]
        if klass not in counts:
            raise ValueError(f"{sid}: unknown class {klass!r}")
        counts[klass] += 1
        if r["sample_id"] != "IMG0000":
            raise ValueError(f"{sid}: only IMG0000 is verified with current HEX data")
        if r["target_id"] not in target_by_id:
            raise ValueError(f"{sid}: target {r['target_id']} missing in catalog")
        target = target_by_id[r["target_id"]]
        if target["class"] != klass:
            raise ValueError(f"{sid}: class mismatch with target catalog")
        try:
            bit = int(r["bit_index"])
            offset = int(r["injection_offset"])
            width = int(target["width"])
            lo = int(target["bit_min"])
            hi = int(target["bit_max"])
        except ValueError as exc:
            raise ValueError(f"{sid}: non-integer catalog/scenario number") from exc
        if not (width > 0 and 0 <= lo <= bit <= hi < width):
            raise ValueError(f"{sid}: bit {bit} outside [{lo},{hi}] of width {width}")
        if not 3 <= offset < 3000:
            raise ValueError(f"{sid}: FI_OFFSET {offset} outside testbench [3,2999]")
        key = (r["sample_id"], r["target_id"], bit, offset)
        if key in seen_tuples:
            raise ValueError(f"{sid}: duplicate sample/site/bit/offset tuple")
        seen_tuples.add(key)
        if sid in locked.SPECS:
            expected = locked.SPECS[sid]
            if (klass, r["target_id"], bit, offset) != (
                expected["class"], expected["target"], expected["bit"], expected["offset"]
            ):
                raise ValueError(f"{sid}: locked smoke scenario was modified")
    if any(counts[c] != 5 for c in CLASSES):
        raise ValueError(f"Need 5 scenarios per class; got {counts}")
    if set(locked.SPECS) - set(ids):
        raise ValueError("The four locked smoke scenario IDs must be retained")
    return rows


def make_tcl(project: Path, row: dict[str, str], fileset: str) -> str:
    sid = row["scenario_id"]
    if sid in locked.SPECS:
        return locked.make_tcl(project, row, fileset)
    plusargs = [
        f"-testplusarg FI_OFFSET={int(row['injection_offset'])}",
        f"-testplusarg SCENARIO_ID={sid}",
        f"-testplusarg TARGET_ID={row['target_id']}",
        f"-testplusarg FI_BIT={int(row['bit_index'])}",
        f"-testplusarg SAMPLE_ID={row['sample_id']}",
    ]
    return (
        "# Directed FI: new candidate scenario, generic TB; P0 RTL unchanged.\n"
        "# A file-backed marker proves Tcl reached the end; stdout can close after $finish.\n"
        "set cnn_pep_done_file \"[file rootname [info script]].done\"\n"
        "catch {file delete -force -- $cnn_pep_done_file}\n"
        f"open_project {locked.tcl_brace(project.resolve().as_posix())}\n"
        "if {[catch {\n"
        f"    set sim [get_filesets {locked.tcl_brace(fileset)}]\n"
        "    if {[llength $sim] != 1} {error {Expected one simulation fileset}}\n"
        "    catch {close_sim -force}\n"
        f"    set_property top {GENERIC_TOP} $sim\n"
        "    set_property xsim.compile.incremental false $sim\n"
        "    update_compile_order -fileset $sim\n"
        "    set_property -name {xsim.simulate.xsim.more_options} -value "
        f"{locked.tcl_brace(' '.join(plusargs))} -objects $sim\n"
        "    launch_simulation\n"
        "    run all\n"
        "    catch {close_sim -force}\n"
        "    close_project\n"
        "    set cnn_pep_f [open $cnn_pep_done_file w]\n"
        "    puts $cnn_pep_f {CNN_PEP_RUNNER_TCL_COMPLETED}\n"
        "    close $cnn_pep_f\n"
        "} err opts]} {\n"
        '    catch {puts stderr "CNN_PEP_RUNNER_TCL_ERROR: $err"}\n'
        "    exit 1\n"
        "}\n"
        "exit 0\n"
    )


def strict_log_check(log: Path, sid: str) -> None:
    data = log.read_text(encoding="utf-8", errors="replace")
    required = ("FI_DISABLED_CONTROL_PASS",
                "FI_RESULT_V2,", "FI_APPLIED scenario=",
                "CNN_PEP FI CAMPAIGN V2: PASS")
    for marker in required:
        if marker not in data:
            raise RuntimeError(f"{sid}: missing marker {marker!r} in {log}")
    result_lines = [s.strip() for s in data.splitlines() if s.startswith("FI_RESULT_V2,")]
    applied_lines = [s.strip() for s in data.splitlines() if s.startswith("FI_APPLIED ")]
    if len(result_lines) != 1 or len(applied_lines) != 1:
        raise RuntimeError(f"{sid}: expected one FI_RESULT_V2 and FI_APPLIED in {log}")
    if not result_lines[0].startswith(f"FI_RESULT_V2,{sid},"):
        raise RuntimeError(f"{sid}: incorrect result ID in {log}")
    if f"scenario={sid} " not in applied_lines[0]:
        raise RuntimeError(f"{sid}: incorrect applied ID in {log}")


def parse_and_summarize(repo: Path, scenario_file: Path, logs: Path, run_dir: Path,
                        specs: list[dict[str, str]]) -> Path:
    parser = repo / "fi/scripts/parse_fi_v2.py"
    summary = repo / "fi/scripts/summarize_campaign.py"
    for file in (parser, summary):
        if not file.is_file():
            raise ValueError(f"Missing required script: {file}")
    out = run_dir / "deterministic20_results.csv"
    cp = subprocess.run([sys.executable, str(parser), "--logs-dir", str(logs),
                         "--scenarios", str(scenario_file), "--out", str(out)],
                        cwd=repo, capture_output=True, text=True, check=False)
    if cp.returncode:
        raise RuntimeError(f"Strict parser rejected run: {cp.stdout}\n{cp.stderr}")
    print("  " + cp.stdout.strip(), flush=True)
    parsed = read_csv(out, ("scenario_id", "target_id", "target_class", "outcome"))
    if len(parsed) != 20 or {r['scenario_id'] for r in parsed} != {r['scenario_id'] for r in specs}:
        raise RuntimeError("Unexpected parser result count/IDs")
    for r in parsed:
        if r["scenario_id"] in locked.SPECS:
            expected = locked.SPECS[r["scenario_id"]]["outcome"]
            if r["outcome"] != expected:
                raise RuntimeError(f"Regression changed: {r['scenario_id']} outcome {r['outcome']} != {expected}")
    summary_path = run_dir / "deterministic20_summary.txt"
    cp = subprocess.run([sys.executable, str(summary), "--input", str(out),
                         "--output", str(summary_path)], cwd=repo,
                        capture_output=True, text=True, check=False)
    if cp.returncode:
        raise RuntimeError(f"Summary failed: {cp.stdout}\n{cp.stderr}")
    print(f"  Summary saved -> {summary_path}", flush=True)
    return out


def git_revision(repo: Path) -> str | None:
    try:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                            capture_output=True, text=True, check=False)
        return cp.stdout.strip() if cp.returncode == 0 else None
    except OSError:
        return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--vivado", default="vivado")
    p.add_argument("--fileset", default="sim_1")
    p.add_argument("--scenarios", type=Path,
                   default=Path("fi/scenarios/deterministic20.csv"))
    p.add_argument("--catalog", type=Path,
                   default=Path("fi/config/fi_targets_candidates_v2.csv"))
    p.add_argument("--out-dir", type=Path,
                   default=Path("fi/results/deterministic20"))
    p.add_argument("--repeats", type=int, choices=(1, 2), default=1,
                   help="Run one repeat for discovery; use 2 for replay gate")
    p.add_argument("--timeout-seconds", type=int, default=900)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()

    repo = args.repo.resolve()
    scenario_file = args.scenarios if args.scenarios.is_absolute() else repo / args.scenarios
    catalog_file = args.catalog if args.catalog.is_absolute() else repo / args.catalog
    out_dir = args.out_dir if args.out_dir.is_absolute() else repo / args.out_dir
    project = args.project.resolve()
    rows = validate(scenario_file, catalog_file)
    if args.execute and not project.is_file():
        raise ValueError(f"Vivado .xpr not found: {project}")
    if args.execute:
        for needed in (repo / "fi/scripts/run_four_class_smoke.py",
                       repo / "fi/scripts/parse_fi_v2.py",
                       repo / "fi/scripts/summarize_campaign.py"):
            if not needed.is_file():
                raise ValueError(f"Missing dependency: {needed}")

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "runner_manifest.json"
    metadata = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "status": "RUNNING" if args.execute else "DRY_RUN_ONLY",
        "project": str(project), "vivado": args.vivado, "fileset": args.fileset,
        "repeats": args.repeats, "count": len(rows),
        "scenarios_sha256": digest(scenario_file),
        "targets_sha256": digest(catalog_file),
        "git_head": git_revision(repo),
        "provisional_timing_ids": sorted(PROVISIONAL_TIMING),
        "simulated_repeats": 0,
    }
    manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY_RUN'} | "+
          f"20 scenarios (5/class) | repeats={args.repeats}", flush=True)
    hashes = []
    try:
        for run_number in range(1, args.repeats + 1):
            run_dir = out_dir / f"repeat_{run_number:02d}"
            logs = run_dir / "logs"
            tcls = run_dir / "tcl"
            tcls.mkdir(parents=True, exist_ok=True)
            if args.execute:
                logs.mkdir(parents=True, exist_ok=True)
            for row in rows:
                sid = row["scenario_id"]
                target_top = locked.SPECS[sid]["top"] if sid in locked.SPECS else GENERIC_TOP
                script = tcls / f"{sid}.tcl"
                script.write_text(make_tcl(project, row, args.fileset), encoding="utf-8")
                print(f"[{run_number}/{args.repeats}] {sid} -> {target_top}", flush=True)
                if not args.execute:
                    continue
                log = logs / f"{sid}.log"
                command = [args.vivado, "-mode", "batch", "-source", str(script),
                           "-nolog", "-nojournal"]
                locked.run_or_fail(command, project.parent, log, args.timeout_seconds,
                                   script.with_suffix(".done"))
                strict_log_check(log, sid)
                print(f"  XSim output captured -> {log}", flush=True)
            if args.execute:
                parsed = parse_and_summarize(repo, scenario_file, logs, run_dir, rows)
                hashes.append(digest(parsed))
                metadata["simulated_repeats"] = run_number
                metadata[f"repeat_{run_number:02d}_results_sha256"] = hashes[-1]
                manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        if args.execute:
            if len(hashes) == 2 and hashes[0] != hashes[1]:
                raise RuntimeError(f"REPLAY MISMATCH: {hashes[0]} != {hashes[1]}")
            metadata["status"] = "PASS_2_REPEATS" if len(hashes) == 2 else "PASS_1_REPEAT"
            manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            print("PASS: 20/20 XSim scenarios verified" +
                  ("; two normalized CSVs byte-identical" if len(hashes) == 2 else ""),
                  flush=True)
            if PROVISIONAL_TIMING:
                print("NOTE: MEM1_0005/BUF_0005 offsets still need target-specific write/read timeline proof.",
                      flush=True)
        else:
            print("DRY_RUN PASS: 20 Tcl files per repeat prepared. XSim NOT executed.", flush=True)
    except (ValueError, RuntimeError, OSError) as exc:
        metadata["status"] = "FAILED"
        metadata["error"] = str(exc)
        manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

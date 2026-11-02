#!/usr/bin/env python3
"""Replay the 4 locked CNN_PEP FI smoke scenarios via Vivado batch.

Dry-run by default. --execute explicitly launches Vivado/XSim. Does not
modify baseline RTL or the four manually validated logs/results.

Requirements on the user's Windows machine:
  * Vivado 2022.2 and a valid .xpr project
  * all four FI TB source files already included in sim_1
  * RTL and ROM/golden HEX paths resolvable by the Vivado simulation project
  * unified fi/scripts/parse_fi_v2.py and summarize_campaign.py
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SPECS = {
    "FI_CTL_0005": {
        "class": "CTL", "target": "CTL_0001", "bit": 2, "offset": 125,
        "top": "tb_cnn_mnist_fi_campaign_v2", "auto": None,
        "outcome": "PROTOCOL_VIOLATION",
    },
    "FI_MEM1_0001": {
        "class": "MEM", "target": "MEM_0001", "bit": 0, "offset": 794,
        "top": "tb_cnn_mnist_fi_mem1_smoke", "auto": "MEM1_AUTO",
        "outcome": "FINAL_OUTPUT_MATCH",
    },
    "FI_BUF_0001": {
        "class": "BUF", "target": "BUF_0030", "bit": 0, "offset": 965,
        "top": "tb_cnn_mnist_fi_buf_smoke", "auto": "BUF_AUTO",
        "outcome": "FINAL_OUTPUT_MATCH",
    },
    "FI_DAT_0001": {
        "class": "DAT", "target": "DAT_0010", "bit": 0, "offset": 1340,
        "top": "tb_cnn_mnist_fi_dat_smoke", "auto": "DAT_AUTO",
        "outcome": "WRONG_LOGITS_SAME_LABEL",
    },
}

FIELDS = ("scenario_id", "sample_id", "target_id", "target_class",
          "bit_index", "injection_offset")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_scenarios(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"Missing scenario CSV: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or any(k not in reader.fieldnames for k in FIELDS):
            raise ValueError(f"CSV missing required fields {FIELDS}")
        rows = list(reader)
    if len(rows) != 4:
        raise ValueError(f"Need exactly 4 regression scenarios; found {len(rows)}")
    ids = [str(row["scenario_id"]).strip() for row in rows]
    if len(set(ids)) != 4 or set(ids) != set(SPECS):
        raise ValueError(f"Regression scenario IDs changed: {ids}")
    for row in rows:
        sid = row["scenario_id"].strip()
        spec = SPECS[sid]
        expected = ("IMG0000", spec["target"], spec["class"],
                    str(spec["bit"]), str(spec["offset"]))
        actual = tuple((row.get(k) or "").strip() for k in FIELDS[1:])
        if actual != expected:
            raise ValueError(f"{sid} locked scenario mismatch: {actual} != {expected}")
    return rows


def tcl_brace(value: str) -> str:
    if "{" in value or "}" in value or "\n" in value:
        raise ValueError("Project path or plusarg contains unsafe Tcl braces/newline")
    return "{" + value + "}"


def make_tcl(project: Path, row: dict[str, str], fileset: str) -> str:
    spec = SPECS[row["scenario_id"]]
    opts = [f"-testplusarg SCENARIO_ID={row['scenario_id']}",
            f"-testplusarg TARGET_ID={spec['target']}",
            f"-testplusarg FI_BIT={spec['bit']}",
            f"-testplusarg SAMPLE_ID=IMG0000"]
    if spec["auto"]:
        opts.insert(0, f"-testplusarg {spec['auto']}=1")
    else:
        opts.insert(0, f"-testplusarg FI_OFFSET={spec['offset']}")
    # 'run all' is the exact Tcl command demonstrated in the user's XSim logs.
    return (
        "# Generated for ONE existing CNN_PEP deterministic FI smoke scenario.\n"
        "# Read-only to DUT RTL; only simulation fileset properties are changed.\n"
        f"open_project {tcl_brace(project.resolve().as_posix())}\n"
        "if {[catch {\n"
        f"    set sim [get_filesets {tcl_brace(fileset)}]\n"
        "    if {[llength $sim] != 1} {error {Expected one simulation fileset}}\n"
        "    catch {close_sim -force}\n"
        f"    set_property top {spec['top']} $sim\n"
        "    update_compile_order -fileset $sim\n"
        f"    set_property -name {{xsim.simulate.xsim.more_options}} -value {tcl_brace(' '.join(opts))} -objects $sim\n"
        "    launch_simulation\n"
        "    run all\n"
        "} err opts]} {\n"
        "    puts stderr \"CNN_PEP_RUNNER_TCL_ERROR: $err\"\n"
        "    exit 1\n"
        "}\n"
        "puts {CNN_PEP_RUNNER_TCL_COMPLETED}\n"
        "catch {close_sim -force}\n"
        "close_project\n"
        "exit 0\n"
    )


def run_or_fail(cmd: list[str], cwd: Path, log_file: Path,
                timeout: int) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8", errors="replace") as output:
        try:
            cp = subprocess.run(cmd, cwd=cwd, stdout=output,
                                stderr=subprocess.STDOUT, timeout=timeout,
                                check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"TIMEOUT: {log_file} exceeded {timeout}s") from exc
        except OSError as exc:
            raise RuntimeError(f"Unable to launch Vivado: {cmd[0]}: {exc}") from exc
    contents = log_file.read_text(encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(f"Nonzero subprocess status {cp.returncode}; see {log_file}")
    if "CNN_PEP_RUNNER_TCL_COMPLETED" not in contents:
        raise RuntimeError(f"Tcl completion marker missing; see {log_file}")
    if "FI_RESULT_V2," not in contents or "FI_DISABLED_CONTROL_PASS" not in contents:
        raise RuntimeError(f"FI verification markers missing; see {log_file}")


def run_parser(repo: Path, scenarios: Path, logs: Path, output: Path) -> None:
    parser = repo / "fi/scripts/parse_fi_v2.py"
    summary = repo / "fi/scripts/summarize_campaign.py"
    if not parser.is_file() or not summary.is_file():
        raise ValueError("Missing repo parser or summarizer in fi/scripts")
    cp = subprocess.run([sys.executable, str(parser), "--logs-dir", str(logs),
                         "--scenarios", str(scenarios), "--out", str(output)],
                        cwd=repo, capture_output=True, text=True)
    if cp.returncode:
        raise RuntimeError(f"FI parser REJECTED run: {cp.stdout}\n{cp.stderr}")
    print(cp.stdout.strip())
    with output.open(newline="", encoding="utf-8-sig") as f:
        parsed = list(csv.DictReader(f))
    if len(parsed) != 4:
        raise RuntimeError(f"Parser produced {len(parsed)} results, not 4")
    for row in parsed:
        expect = SPECS[row["scenario_id"]]["outcome"]
        if row["outcome"] != expect:
            raise RuntimeError(f"{row['scenario_id']} changed outcome: {row['outcome']} != {expect}")
    summary_out = output.parent / "four_class_smoke_summary.txt"
    cp = subprocess.run([sys.executable, str(summary), "--input", str(output),
                         "--output", str(summary_out)], cwd=repo,
                        capture_output=True, text=True)
    if cp.returncode:
        raise RuntimeError(f"FI summary REJECTED run: {cp.stdout}\n{cp.stderr}")
    print(f"Summary saved: {summary_out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, required=True,
                    help="Absolute path to user's Vivado .xpr project")
    ap.add_argument("--repo", type=Path, default=Path.cwd(),
                    help="CNN repository root (default: current directory)")
    ap.add_argument("--vivado", default="vivado", help="Vivado executable or .bat path")
    ap.add_argument("--fileset", default="sim_1")
    ap.add_argument("--scenarios", type=Path,
                    default=Path("fi/scenarios/four_class_smoke.csv"))
    ap.add_argument("--out-dir", type=Path,
                    default=Path("fi/results/automation_replay"))
    ap.add_argument("--repeats", type=int, choices=(1, 2), default=2)
    ap.add_argument("--timeout-seconds", type=int, default=900)
    ap.add_argument("--execute", action="store_true",
                    help="Actually run Vivado (without this, only prepare/dry-run)")
    args = ap.parse_args()

    repo = args.repo.resolve()
    scenarios = args.scenarios if args.scenarios.is_absolute() else repo / args.scenarios
    out_dir = args.out_dir if args.out_dir.is_absolute() else repo / args.out_dir
    project = args.project.resolve()
    rows = verify_scenarios(scenarios)
    if args.execute and not project.is_file():
        raise ValueError(f"Vivado project does not exist: {project}")

    meta = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "EXECUTE" if args.execute else "DRY_RUN",
            "vivado": args.vivado, "project": str(project),
            "scenario_sha256": sha256(scenarios), "repeats": args.repeats,
            "fileset": args.fileset, "python": sys.version.split()[0]}
    if args.execute:
        meta["project_sha256"] = sha256(project)
    print(f"Mode: {meta['mode']} | 4 locked smoke scenarios | repeats={args.repeats}")
    hashes = []
    for run_no in range(1, args.repeats + 1):
        run_dir = out_dir / f"repeat_{run_no:02d}"
        logs = run_dir / "logs"
        tcl_dir = run_dir / "tcl"
        logs.mkdir(parents=True, exist_ok=True)
        tcl_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            sid = row["scenario_id"]
            tcl_file = tcl_dir / f"{sid}.tcl"
            tcl_file.write_text(make_tcl(project, row, args.fileset), encoding="utf-8")
            log_file = logs / f"{sid}.log"
            cmd = [args.vivado, "-mode", "batch", "-source", str(tcl_file),
                   "-nolog", "-nojournal"]
            print(f"[{run_no}/{args.repeats}] {sid} -> {SPECS[sid]['top']}")
            if not args.execute:
                print("  " + subprocess.list2cmdline(cmd))
                continue
            run_or_fail(cmd, project.parent, log_file, args.timeout_seconds)
            print(f"  XSim output captured -> {log_file}")
        if args.execute:
            parsed = run_dir / "four_class_smoke_results.csv"
            run_parser(repo, scenarios, logs, parsed)
            hashes.append(sha256(parsed))
            print(f"  Parsed SHA256: {hashes[-1]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "runner_manifest.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.execute:
        if len(hashes) > 1 and len(set(hashes)) != 1:
            raise RuntimeError("REPLAY MISMATCH: normalized CSV differs between repeated runs")
        print("PASS: four XSim scenarios parsed and outcome-checked" +
              ("; two normalized CSVs byte-identical" if len(hashes) > 1 else ""))
    else:
        print("DRY_RUN PASS: Tcl files generated. No XSim was executed or validated.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

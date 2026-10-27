#!/usr/bin/env python3
"""Run FI scenarios through a user-supplied simulator command template.

This runner intentionally does not hard-code a Vivado project path or XSim
snapshot name. Supply a command template once the campaign-ready testbench
accepts FI offset/scenario arguments.

Example concept:
  python fi/scripts/run_campaign.py \
    --scenarios fi/scenarios/pilot_scenarios.csv \
    --command-template 'xsim tb_snapshot --runall ...'

Available placeholders:
  {scenario_id}
  {injection_offset}
  {bit_index}
  {target_id}
  {sample_id}
"""

from __future__ import annotations
import argparse
import csv
import subprocess
from pathlib import Path

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenarios",
        default="fi/scenarios/pilot_scenarios.csv",
    )
    ap.add_argument(
        "--command-template",
        required=True,
        help="Shell command template with scenario placeholders.",
    )
    ap.add_argument(
        "--log-dir",
        default="fi/results/raw/logs",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
    )
    args = ap.parse_args()

    scenarios = Path(args.scenarios)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    with scenarios.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    rows = [r for r in rows if (r.get("scenario_id") or "").strip()]

    for i, row in enumerate(rows, start=1):
        command = args.command_template.format(**row)
        sid = row["scenario_id"]
        print(f"[{i}/{len(rows)}] {sid}")
        print(f"  {command}")

        if args.dry_run:
            continue

        proc = subprocess.run(
            command,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        log_path = log_dir / f"{sid}.log"
        log_path.write_text(proc.stdout or "", encoding="utf-8")

        if proc.returncode != 0:
            raise SystemExit(
                f"{sid} failed with return code {proc.returncode}; "
                f"see {log_path}"
            )

    print(f"Completed {len(rows)} scenarios.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate deterministic or randomized FI scenario CSV files.

Default use creates a controlled CTL timing sweep for CTL_0001.
No RTL is modified by this script.
"""

from __future__ import annotations
import argparse
import csv
import random
from pathlib import Path

FIELDS = [
    "scenario_id","sample_id","target_id","target_class","hier_signal",
    "bit_index","reference_event","injection_offset","fault_type",
    "fault_events","expected_pre_state","expected_fault_state","purpose"
]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fi/scenarios/pilot_scenarios.csv")
    ap.add_argument("--mode", choices=["deterministic","random"], default="deterministic")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--min-offset", type=int, default=25)
    ap.add_argument("--max-offset", type=int, default=125)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "deterministic":
        if args.count == 1:
            offsets = [args.min_offset]
        else:
            span = args.max_offset - args.min_offset
            offsets = [
                round(args.min_offset + i * span / (args.count - 1))
                for i in range(args.count)
            ]
    else:
        rng = random.Random(args.seed)
        offsets = [
            rng.randint(args.min_offset, args.max_offset)
            for _ in range(args.count)
        ]

    rows = []
    for i, offset in enumerate(offsets, 1):
        rows.append({
            "scenario_id": f"FI_CTL_{i:04d}",
            "sample_id": "IMG0000",
            "target_id": "CTL_0001",
            "target_class": "CTL",
            "hier_signal": "dut.u_top_fsm.state",
            "bit_index": 2,
            "reference_event": "accepted_i_start",
            "injection_offset": offset,
            "fault_type": "SEU_SINGLE_BIT",
            "fault_events": 1,
            "expected_pre_state": "001",
            "expected_fault_state": "101",
            "purpose": f"{args.mode} CTL smoke scenario",
        })

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} scenarios -> {out}")

if __name__ == "__main__":
    main()

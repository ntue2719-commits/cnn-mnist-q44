#!/usr/bin/env python3
"""Validate and normalize raw CNN_PEP FI result CSV."""

from __future__ import annotations
import argparse
import csv
from pathlib import Path

VALID_OUTCOMES = {
    "NONCOMPLETION",
    "PROTOCOL_VIOLATION",
    "WRONG_LABEL",
    "WRONG_LOGITS_SAME_LABEL",
    "FINAL_OUTPUT_MATCH",
}

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="fi/results/raw/results.csv")
    ap.add_argument("--output", default="fi/results/parsed/results_normalized.csv")
    args = ap.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with src.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = []
        for line_no, row in enumerate(reader, start=2):
            if not (row.get("scenario_id") or "").strip():
                continue
            outcome = (row.get("outcome") or "").strip()
            if outcome not in VALID_OUTCOMES:
                raise ValueError(
                    f"line {line_no}: invalid outcome {outcome!r}"
                )
            rows.append({k: (v or "").strip() for k, v in row.items()})

    with dst.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Validated {len(rows)} results -> {dst}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Summarize CNN_PEP RTL fault-injection result CSV."""

from __future__ import annotations
import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

HARMFUL = {"NONCOMPLETION", "PROTOCOL_VIOLATION", "WRONG_LABEL"}

def pct(n: int, d: int) -> float:
    return 0.0 if d == 0 else 100.0 * n / d

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="fi/results/raw/results.csv")
    ap.add_argument("--output", default="fi/results/summary/campaign_summary.txt")
    args = ap.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with src.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Ignore a header-only file.
    rows = [r for r in rows if (r.get("scenario_id") or "").strip()]

    total = len(rows)
    outcomes = Counter((r.get("outcome") or "UNKNOWN").strip() for r in rows)
    by_class = defaultdict(list)
    for r in rows:
        by_class[(r.get("target_class") or "UNKNOWN").strip()].append(r)

    lines = []
    lines.append("CNN_PEP RTL FI CAMPAIGN SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Total scenarios: {total}")
    lines.append("")

    lines.append("Outcome distribution")
    lines.append("-" * 60)
    for name in [
        "NONCOMPLETION",
        "PROTOCOL_VIOLATION",
        "WRONG_LABEL",
        "WRONG_LOGITS_SAME_LABEL",
        "FINAL_OUTPUT_MATCH",
        "UNKNOWN",
    ]:
        n = outcomes.get(name, 0)
        if n or name != "UNKNOWN":
            lines.append(f"{name:26s} {n:6d}  {pct(n,total):8.3f}%")

    harmful = sum(outcomes.get(x, 0) for x in HARMFUL)
    lines.append("")
    lines.append(f"Harmful outcomes: {harmful}/{total} = {pct(harmful,total):.3f}%")
    lines.append("Definition: NONCOMPLETION + PROTOCOL_VIOLATION + WRONG_LABEL")
    lines.append("")

    lines.append("Per target class")
    lines.append("-" * 60)
    for cls in sorted(by_class):
        rs = by_class[cls]
        c = Counter((r.get("outcome") or "UNKNOWN").strip() for r in rs)
        h = sum(c.get(x, 0) for x in HARMFUL)
        lines.append(
            f"{cls:8s} N={len(rs):6d} harmful={h:6d} "
            f"rate={pct(h,len(rs)):.3f}%"
        )

    text = "\n".join(lines) + "\n"
    dst.write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote summary -> {dst}")

if __name__ == "__main__":
    main()

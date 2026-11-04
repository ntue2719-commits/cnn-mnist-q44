#!/usr/bin/env python3
"""Separate read-only V4Q mock receipt auditor. NEVER certifies real RTL E3."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys


class AuditError(ValueError):
    pass


def check(ok, reason):
    if not ok:
        raise AuditError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse(raw, desc):
    check(b"\r" not in raw and raw.endswith(b"\n"), f"non-LF canonical bytes: {desc}")
    def unique(pairs):
        d = {}
        for k, v in pairs:
            check(k not in d, f"duplicate key: {desc}/{k}")
            d[k] = v
        return d
    obj = json.loads(raw, object_pairs_hook=unique)
    check(raw == (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"),
          f"noncanonical JSON: {desc}")
    return obj


def independent_case_check(case, result, trace_sha):
    check(case.get("evidence_kind") == "SYNTHETIC_MODEL_ONLY", "real evidence intermingled")
    check(case["experiment_id"] == result["experiment_id"], "ID mismatch")
    check(result["raw_trace_sha256"] == trace_sha, "trace SHA mismatch")
    check(result["real_rtl_injections"] == 0 and result["real_e3_qualified"] is False,
          "false real E3 or FI claim")
    for fld in ("g6_production_qualified", "g7_scientific_approved", "full400_authorized"):
        check(result[fld] is False, "unauthorized release flag")
    width = case["target"]["width_bits"]
    bit = case["target"]["bit_index"]
    fault = case["fault"]
    check(fault["phase"] == "POST_NBA", "injection phase changed")
    if fault["applied"]:
        check(fault["event_count"] == 1, "E1 count")
        check(int(fault["new"], 16) == (int(fault["old"], 16) ^ (1 << bit)), "XOR")
        check(fault["new"] == fault["readback"], "readback")
    else:
        check(fault["event_count"] == 0 and fault["new"] == fault["old"] == fault["readback"],
              "baseline E1 forged")
    check(width in (8, 16, 32, 64), "width")
    coverage = case["coverage"]
    check(coverage["mapping_review"] == "UNREVIEWED", "unapproved consumer inventory")
    ids = [x["id"] for x in coverage["declared_channels"]]
    check(len(ids) == len(set(ids)) and len(ids) >= 1, "duplicate/empty consumers")
    window = case["timeline"]
    check(window["start_cycle"] == fault["injection_cycle"] + 1, "start edge")
    full_edges = set(range(window["start_cycle"], window["last_cycle"] + 1))
    observed = set()
    all_consumers_present = True
    direct_witnesses = []
    last_overwrite = []
    for e in window["edges"]:
        c = e["cycle"]
        check(c in full_edges and c not in observed, "invalid/duplicate edge")
        observed.add(c)
        check(e["phase"] == "PRE_NBA", "wrong monitor phase")
        check(int(e["source_faulty"], 16) == int(fault["new"], 16), "source faulty state mismatch")
        check(int(e["source_golden"], 16) == int(fault["old"], 16), "source golden state mismatch")
        if e["overwrite_after_edge"]:
            last_overwrite.append(c)
        names = [s["consumer_id"] for s in e["samples"]]
        check(len(names) == len(set(names)) and set(names).issubset(ids), "unexpected/duplicate channel")
        if set(names) != set(ids):
            all_consumers_present = False
        for s in e["samples"]:
            if s["accepted"]:
                check(s["faulty_value"] is not None and s["golden_value"] is not None,
                      "accepted but missing data")
                check(int(s["faulty_value"], 16) == int(e["source_faulty"], 16),
                      "faulty sampled bus mismatch")
                check(int(s["golden_value"], 16) == int(e["source_golden"], 16),
                      "golden sampled bus mismatch")
                if s["faulty_value"] != s["golden_value"]:
                    direct_witnesses.append((c, s["consumer_id"]))
            else:
                check(s["faulty_value"] is None and s["golden_value"] is None,
                      "false sample with gate low")
    closed = window["terminal"] == "INFERENCE_END" or (
        window["terminal"] == "OVERWRITE_OBSERVED" and last_overwrite == [window["last_cycle"]])
    check(not last_overwrite or last_overwrite == [window["last_cycle"]], "overwrite ordering")
    complete = (observed == full_edges and all_consumers_present and closed)
    if not fault["applied"]:
        status = "NO_INJECTION_BASELINE_NOT_AN_E3_NEGATIVE"
    elif direct_witnesses:
        status = "E3_TRUE_WITNESS_MOCK_ONLY"
    elif not observed:
        status = "E3_NOT_MONITORED"
    elif not complete:
        status = "E3_INCONCLUSIVE_PARTIAL_COVERAGE"
    else:
        status = "E3_NO_CONSUMPTION_IN_DECLARED_SCOPE_MOCK_ONLY"
    check(result["observed_status"] == status, "derived E3 classification mismatch")
    check(result["evidence_kind"] == "SYNTHETIC_MODEL_ONLY", "result source tampering")
    check(result["coverage_complete"] == complete, "coverage flag mismatch")
    check(result["edges_expected"] == len(full_edges), "edge denominator mismatch")
    check(result["edges_observed"] == len(observed), "edge numerator mismatch")
    check(result["channels_expected"] == len(ids), "channel denominator mismatch")
    check(len(result["e3_witnesses"]) == len(direct_witnesses), "witness count mismatch")
    for idx, witness in enumerate(result["e3_witnesses"]):
        check((witness["cycle"], witness["consumer_id"]) == direct_witnesses[idx], "witness identity mismatch")
    return status


def audit(base: Path):
    check(base.is_dir() and not base.is_symlink(), "invalid evidence root")
    all_files = {}
    for p in base.rglob("*"):
        check(not p.is_symlink(), "symlink in evidence archive")
        if p.is_file():
            all_files[p.relative_to(base).as_posix()] = p.read_bytes()
    manifest_raw = all_files.pop("V4Q_OUTPUT_SHA256.json", None)
    check(manifest_raw is not None, "missing pin manifest")
    manifest = parse(manifest_raw, "manifest")
    check(manifest["schema"] == "CNN_PEP_V4Q_OUTPUT_HASH_MANIFEST_V1", "manifest schema")
    check(set(all_files) == set(manifest["files"]), "manifest inventory drift")
    for name, raw in all_files.items():
        check(sha(raw) == manifest["files"][name], "raw SHA mismatch: " + name)
    case_names = sorted(n for n in all_files if n.startswith("cases/") and n.endswith(".json"))
    check(len(case_names) == 7, "expected seven explicit model cases")
    expected_ids = {"V4Q_NC0", "V4Q_NC1", "V4Q_NC2", "V4Q_NC3", "V4Q_NC4",
                    "V4Q_GAP_NO_MONITOR", "V4Q_GAP_PARTIAL"}
    check({Path(n).stem for n in case_names} == expected_ids, "test catalog changed")
    counts = {}
    results = []
    for name in case_names:
        trace = parse(all_files[name], name)
        result_name = "case_results/" + Path(name).name
        check(result_name in all_files, "missing scenario result")
        result = parse(all_files[result_name], result_name)
        status = independent_case_check(trace, result, sha(all_files[name]))
        counts[status] = counts.get(status, 0) + 1
        results.append(result)
    check(len([n for n in all_files if n.startswith("case_results/")]) == 7, "extra results")
    summary = parse(all_files["V4Q_SUMMARY.json"], "summary")
    check(summary["synthetic_cases"] == 7 and summary["status_counts"] == counts,
          "summary not derived from scenario results")
    check(summary["real_fi_injections"] == 0 and summary["new_xsim_runs"] == 0 and
          summary["real_e3_negative_proofs"] == 0 and summary["full400_authorized"] is False,
          "fake real-FI claim")
    csv_raw = all_files["V4Q_CASES.csv"]
    check(b"\r" not in csv_raw and csv_raw.endswith(b"\n"), "CSV newline")
    csv_rows = list(csv.DictReader(io.StringIO(csv_raw.decode("ascii"))))
    check(len(csv_rows) == 7, "CSV length")
    by_id = {r["experiment_id"]: r for r in csv_rows}
    check(set(by_id) == expected_ids, "CSV catalog")
    for result in results:
        row = by_id[result["experiment_id"]]
        check(row["observed_status"] == result["observed_status"] and
              row["raw_trace_sha256"] == result["raw_trace_sha256"] and
              row["coverage_complete"] == str(result["coverage_complete"]).lower(),
              "CSV summary mismatch")
    check(b"NOT RTL evidence" in all_files["V4Q_REPORT.md"], "missing safety scope in report")
    return {"schema": "CNN_PEP_V4Q_INDEPENDENT_MOCK_AUDIT_RECEIPT_V1", "status": "PASS_MOCK_ONLY",
            "verified_sha256_files": len(all_files), "verified_cases": len(results),
            "status_counts": dict(sorted(counts.items())), "new_xsim_runs": 0,
            "real_e3_proofs": 0, "g6_qualified": False, "g7_approved": False,
            "full400_authorized": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, required=True, help="existing mock output (read-only)")
    args = ap.parse_args(argv)
    try:
        result = audit(args.out_dir)
    except (AuditError, OSError, ValueError, KeyError, TypeError) as exc:
        print("V4Q_INDEPENDENT_AUDIT_FAIL_CLOSED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    print("V4Q_INDEPENDENT_AUDIT_PASS: 7 synthetic cases; 0 RTL evidence; FULL400 BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

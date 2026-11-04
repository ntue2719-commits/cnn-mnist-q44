#!/usr/bin/env python3
"""CNN_PEP V4Q: offline consumer-exposure negative-control contract.

No Vivado/XSim commands, fault injection, release signatures, or edits to P0.
This version can ONLY generate synthetic model fixtures, inspect candidate trace
files without promotion, and audit deterministic output bytes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

TRACE_SCHEMA = "CNN_PEP_V4Q_DIRECT_REGISTER_TRACE_V1"
REPORT_SCHEMA = "CNN_PEP_V4Q_MOCK_NEGATIVE_CONTROL_AUDIT_V1"
STATUS_SCOPE = "OFFLINE_MOCK_ONLY_NOT_REAL_RTL_E3_EVIDENCE"
KINDS = {"SYNTHETIC_MODEL_ONLY", "EXTERNAL_TRACE_UNVERIFIED"}
VALID_OUTCOMES = {"FINAL_OUTPUT_MATCH", "WRONG_LOGITS_SAME_LABEL", "WRONG_LABEL",
                  "NONCOMPLETION", "PROTOCOL_VIOLATION", "NOT_OBSERVED"}
HEX = re.compile(r"[0-9a-f]+\Z")
ID = re.compile(r"[A-Z0-9_]{2,64}\Z")
RESULT_FIELDS = ("experiment_id", "case_purpose", "evidence_kind", "observed_status",
                 "e1_injected", "e3_witnesses", "edges_expected", "edges_observed",
                 "channels_expected", "coverage_complete", "fault_lifetime_closed",
                 "final_outcome", "real_e3_qualified", "raw_trace_sha256")


class TraceError(ValueError):
    pass


def need(condition: bool, message: str) -> None:
    if not condition:
        raise TraceError(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def read_json_unique(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise TraceError("duplicate JSON key: " + key)
            result[key] = value
        return result

    try:
        val = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                         parse_constant=lambda token: (_ for _ in ()).throw(TraceError("NaN/Infinity")))
    except (UnicodeError, json.JSONDecodeError) as e:
        raise TraceError("malformed UTF-8/JSON") from e
    need(isinstance(val, dict), "top-level JSON must be an object")
    return val


def exact_keys(obj: object, keys: set[str], context: str) -> None:
    need(isinstance(obj, dict) and set(obj) == keys,
         f"{context}: keys must be exactly {sorted(keys)}")


def check_int(value, low: int, high: int, name: str) -> None:
    need(type(value) is int and low <= value <= high, f"invalid integer {name}")


def check_bool(value, name: str) -> None:
    need(type(value) is bool, f"invalid boolean {name}")


def check_hex(value: object, width: int, name: str) -> int:
    need(isinstance(value, str) and len(value) == width // 4 and HEX.fullmatch(value) is not None,
         f"noncanonical hex or X/Z in {name}")
    return int(value, 16)


def hexval(value: int, width: int = 8) -> str:
    return f"{value:0{width // 4}x}"


def validate_trace(trace: dict, raw_sha256: str = "") -> dict:
    """Validate a bounded, direct-register observation contract.

    A complete *declared* channel list does NOT establish that it covers all
    possible real RTL consumers. Real E3 proof always requires separately
    reviewed signal mapping, exact raw XSim ancestry and a human approval.
    """
    exact_keys(trace, {"schema", "evidence_kind", "experiment_id", "case_purpose",
                       "target", "fault", "coverage", "timeline", "outcome"}, "trace")
    need(trace["schema"] == TRACE_SCHEMA, "unsupported trace schema")
    kind = trace["evidence_kind"]
    need(kind in KINDS, "unsupported evidence kind")
    exp = trace["experiment_id"]
    need(isinstance(exp, str) and ID.fullmatch(exp) is not None, "experiment ID invalid")
    purpose = trace["case_purpose"]
    need(purpose in {"NC0_BASELINE", "NC1_NOT_CONSUMED", "NC2_CONSUMED_FINAL_MATCH",
                     "NC3_CONSUMED_STAGE_ONLY", "NC4_CONSUMED_NUMERIC",
                     "GAP_NOT_MONITORED", "GAP_PARTIAL_WINDOW"}, "case purpose invalid")
    target = trace["target"]
    exact_keys(target, {"target_id", "class", "model", "width_bits", "bit_index", "rtl_path"}, "target")
    need(target["model"] == "DIRECT_REGISTER_SOURCE_ONLY", "only direct-register comparisons supported")
    need(target["class"] in {"CTL", "BUF", "MEM", "DAT"}, "target class invalid")
    need(isinstance(target["target_id"], str) and ID.fullmatch(target["target_id"]), "target ID invalid")
    need(isinstance(target["rtl_path"], str) and 3 <= len(target["rtl_path"]) <= 200,
         "RTL source path invalid")
    width = target["width_bits"]
    need(type(width) is int and width in (8, 16, 32, 64), "width must be 8/16/32/64")
    check_int(target["bit_index"], 0, width - 1, "bit_index")
    fault = trace["fault"]
    exact_keys(fault, {"applied", "event_count", "injection_cycle", "phase", "old",
                       "new", "readback"}, "fault")
    check_bool(fault["applied"], "fault.applied")
    check_int(fault["event_count"], 0, 1, "event_count")
    check_int(fault["injection_cycle"], 0, 1000000, "injection_cycle")
    need(fault["phase"] == "POST_NBA", "only POST_NBA injection before next PRE_NBA sampling supported")
    old = check_hex(fault["old"], width, "fault.old")
    new = check_hex(fault["new"], width, "fault.new")
    back = check_hex(fault["readback"], width, "fault.readback")
    if fault["applied"]:
        need(fault["event_count"] == 1, "injected trace needs exactly one event")
        need(new == (old ^ (1 << target["bit_index"])) and back == new,
             "E1 XOR/readback invalid")
    else:
        need(fault["event_count"] == 0 and old == new == back,
             "baseline must have no bit flip")

    coverage = trace["coverage"]
    exact_keys(coverage, {"channel_contract", "declared_channels", "mapping_review"}, "coverage")
    need(coverage["channel_contract"] == "ALL_DIRECT_CONSUMERS_DECLARED_CANDIDATE",
         "unsupported channel scope")
    # Guardrail: THIS PROGRAM MUST NEVER ACCEPT A HUMAN REVIEW ASSERTION AS PROOF.
    need(coverage["mapping_review"] == "UNREVIEWED", "no automated human review or signoff")
    channels = coverage["declared_channels"]
    need(isinstance(channels, list) and 1 <= len(channels) <= 32, "declared consumers required")
    names = set()
    for c in channels:
        exact_keys(c, {"id", "rtl_path", "phase", "kind"}, "declared consumer")
        need(isinstance(c["id"], str) and ID.fullmatch(c["id"]) is not None, "consumer ID invalid")
        need(c["id"] not in names, "duplicate consumer ID")
        names.add(c["id"])
        need(c["kind"] == "DIRECT_VALUE" and c["phase"] == "PRE_NBA",
             "unqualified decision/transformed consumer: unsupported")
        need(isinstance(c["rtl_path"], str) and 3 <= len(c["rtl_path"]) <= 200,
             "consumer RTL path invalid")

    timeline = trace["timeline"]
    exact_keys(timeline, {"start_cycle", "last_cycle", "terminal", "edges"}, "timeline")
    start = timeline["start_cycle"]
    last = timeline["last_cycle"]
    check_int(start, 0, 1000001, "timeline.start_cycle")
    check_int(last, 0, 1000001, "timeline.last_cycle")
    need(start == fault["injection_cycle"] + 1, "first monitored edge must follow POST_NBA injection")
    need(last >= start and last - start <= 10000, "window range invalid")
    need(timeline["terminal"] in {"OVERWRITE_OBSERVED", "INFERENCE_END", "WINDOW_OPEN"},
         "invalid timeline termination")
    edges = timeline["edges"]
    need(isinstance(edges, list) and len(edges) <= 10001, "edges type/range invalid")
    observed_cycles = set()
    hits = []
    has_missing = False
    overwrite_cycle = None
    expected_cycles = set(range(start, last + 1))
    for edge in edges:
        exact_keys(edge, {"cycle", "phase", "source_faulty", "source_golden", "samples",
                          "overwrite_after_edge"}, "edge")
        cycle = edge["cycle"]
        check_int(cycle, start, last, "edge cycle")
        need(cycle not in observed_cycles, "duplicate clock cycle")
        observed_cycles.add(cycle)
        need(edge["phase"] == "PRE_NBA", "wrong sampling phase")
        sf = check_hex(edge["source_faulty"], width, "edge source_faulty")
        sg = check_hex(edge["source_golden"], width, "edge source_golden")
        # Model scope: the source remains fault-affected until first overwrite.
        if fault["applied"]:
            need(sf == new and sg == old, "source state inconsistent before overwrite")
        else:
            need(sf == old and sg == old, "baseline source state changed")
        check_bool(edge["overwrite_after_edge"], "overwrite_after_edge")
        if edge["overwrite_after_edge"]:
            need(overwrite_cycle is None, "duplicate overwrite")
            overwrite_cycle = cycle
        samples = edge["samples"]
        need(isinstance(samples, list) and len(samples) <= 32, "samples invalid")
        seen_names = set()
        for s in samples:
            exact_keys(s, {"consumer_id", "accepted", "faulty_value", "golden_value"}, "sample")
            name = s["consumer_id"]
            need(isinstance(name, str) and name in names, "undeclared consumer")
            need(name not in seen_names, "duplicate sample on clock edge")
            seen_names.add(name)
            check_bool(s["accepted"], "accepted")
            if s["accepted"]:
                f = check_hex(s["faulty_value"], width, "accepted faulty sample")
                g = check_hex(s["golden_value"], width, "accepted golden sample")
                need(f == sf and g == sg,
                     "accepted data does not match source PRE_NBA value")
                if f != g:
                    hits.append({"cycle": cycle, "consumer_id": name,
                                 "faulty_value": s["faulty_value"],
                                 "golden_value": s["golden_value"]})
            else:
                need(s["faulty_value"] is None and s["golden_value"] is None,
                     "unaccepted sample may not assert observed value")
        if seen_names != names:
            has_missing = True
    need(observed_cycles.issubset(expected_cycles), "sample outside bounded window")
    if overwrite_cycle is not None:
        need(overwrite_cycle == last, "edge records continue after overwrite")
        need(timeline["terminal"] == "OVERWRITE_OBSERVED", "overwrite termination mismatch")
    elif timeline["terminal"] == "OVERWRITE_OBSERVED":
        raise TraceError("overwrite terminal without overwrite evidence")
    closed = timeline["terminal"] in {"OVERWRITE_OBSERVED", "INFERENCE_END"}
    complete = observed_cycles == expected_cycles and not has_missing and closed
    outcome = trace["outcome"]
    exact_keys(outcome, {"class", "observed"}, "outcome")
    need(outcome["class"] in VALID_OUTCOMES, "outcome invalid")
    check_bool(outcome["observed"], "outcome observed")
    need(outcome["observed"] == (outcome["class"] != "NOT_OBSERVED"), "outcome completeness mismatch")
    # Outcome is a user supplied assertion, not E4 proof; must not use it to infer E3.
    if not fault["applied"]:
        status = "NO_INJECTION_BASELINE_NOT_AN_E3_NEGATIVE"
    elif hits:
        status = "E3_TRUE_WITNESS_MOCK_ONLY" if kind == "SYNTHETIC_MODEL_ONLY" else "E3_TRUE_CANDIDATE_UNVERIFIED"
    elif not edges:
        status = "E3_NOT_MONITORED"
    elif not complete:
        status = "E3_INCONCLUSIVE_PARTIAL_COVERAGE"
    else:
        status = ("E3_NO_CONSUMPTION_IN_DECLARED_SCOPE_MOCK_ONLY"
                  if kind == "SYNTHETIC_MODEL_ONLY" else
                  "E3_FALSE_CANDIDATE_UNAPPROVED_UNVERIFIED")
    return {
        "schema": "CNN_PEP_V4Q_PER_CASE_RESULT_V1",
        "experiment_id": exp,
        "case_purpose": purpose,
        "evidence_kind": kind,
        "observed_status": status,
        "e1_injected": fault["applied"],
        "e3_witnesses": hits,
        "edges_expected": len(expected_cycles),
        "edges_observed": len(observed_cycles),
        "channels_expected": len(names),
        "coverage_complete": complete,
        "fault_lifetime_closed": closed,
        "final_outcome": outcome["class"],
        "real_e3_qualified": False,
        "real_rtl_injections": 0,
        "g6_production_qualified": False,
        "g7_scientific_approved": False,
        "full400_authorized": False,
        "proof_limitation": "Unreviewed consumer enumeration; no raw RTL source, XSim, E1/E3 or human approval independently verified",
        "raw_trace_sha256": raw_sha256,
    }


def _sample(name: str, accepted: bool, faulty: int, reference: int):
    return {"consumer_id": name, "accepted": accepted,
            "faulty_value": hexval(faulty) if accepted else None,
            "golden_value": hexval(reference) if accepted else None}


def synthetic_fixture(identifier: str, purpose: str, *, inject: bool = True,
                      accepted_at: tuple[int, str] | None = None,
                      partial: bool = False, no_edges: bool = False,
                      outcome: str = "FINAL_OUTPUT_MATCH") -> dict:
    """Synthetic two-channel conceptual mock; never sourced from CNN RTL."""
    channels = [
        {"id": "C0", "rtl_path": "model.consumer0.data_i", "phase": "PRE_NBA", "kind": "DIRECT_VALUE"},
        {"id": "C1", "rtl_path": "model.consumer1.data_i", "phase": "PRE_NBA", "kind": "DIRECT_VALUE"},
    ]
    edges = []
    for cycle in (4, 5, 6):
        if no_edges or (partial and cycle == 5):
            continue
        samples = [_sample(name, accepted_at == (cycle, name), int(inject), 0)
                   for name in ("C0", "C1")]
        edges.append({"cycle": cycle, "phase": "PRE_NBA", "source_faulty": hexval(int(inject)),
                      "source_golden": "00", "samples": samples,
                      "overwrite_after_edge": cycle == 6})
    # If all edges are missing, do not forge an overwrite marker.
    return {
        "schema": TRACE_SCHEMA,
        "evidence_kind": "SYNTHETIC_MODEL_ONLY",
        "experiment_id": identifier,
        "case_purpose": purpose,
        "target": {"target_id": "MOCK_REG_0", "class": "DAT",
                   "model": "DIRECT_REGISTER_SOURCE_ONLY", "width_bits": 8,
                   "bit_index": 0, "rtl_path": "model.reg_q"},
        "fault": {"applied": inject, "event_count": int(inject),
                  "injection_cycle": 3, "phase": "POST_NBA", "old": "00",
                  "new": hexval(int(inject)), "readback": hexval(int(inject))},
        "coverage": {"channel_contract": "ALL_DIRECT_CONSUMERS_DECLARED_CANDIDATE",
                     "declared_channels": channels, "mapping_review": "UNREVIEWED"},
        "timeline": {"start_cycle": 4, "last_cycle": 6,
                     "terminal": "WINDOW_OPEN" if no_edges else "OVERWRITE_OBSERVED",
                     "edges": edges},
        "outcome": {"class": outcome, "observed": outcome != "NOT_OBSERVED"},
    }


def demo_cases() -> list[dict]:
    return [
        synthetic_fixture("V4Q_NC0", "NC0_BASELINE", inject=False),
        synthetic_fixture("V4Q_NC1", "NC1_NOT_CONSUMED"),
        synthetic_fixture("V4Q_NC2", "NC2_CONSUMED_FINAL_MATCH", accepted_at=(4, "C0")),
        synthetic_fixture("V4Q_NC3", "NC3_CONSUMED_STAGE_ONLY", accepted_at=(5, "C1")),
        synthetic_fixture("V4Q_NC4", "NC4_CONSUMED_NUMERIC", accepted_at=(6, "C0"),
                          outcome="WRONG_LOGITS_SAME_LABEL"),
        synthetic_fixture("V4Q_GAP_NO_MONITOR", "GAP_NOT_MONITORED", no_edges=True,
                          outcome="NOT_OBSERVED"),
        synthetic_fixture("V4Q_GAP_PARTIAL", "GAP_PARTIAL_WINDOW", partial=True,
                          outcome="NOT_OBSERVED"),
    ]


def csv_bytes(rows: list[dict]) -> bytes:
    out = io.StringIO(newline="")
    w = csv.DictWriter(out, fieldnames=RESULT_FIELDS, lineterminator="\n")
    w.writeheader()
    for result in rows:
        row = dict(result)
        row["e3_witnesses"] = len(result["e3_witnesses"])
        for key in ("e1_injected", "coverage_complete", "fault_lifetime_closed", "real_e3_qualified"):
            row[key] = str(row[key]).lower()
        w.writerow({key: row[key] for key in RESULT_FIELDS})
    return out.getvalue().encode("ascii")


def report_text(rows: list[dict]) -> bytes:
    lines = [
        "# CNN_PEP V4Q: Synthetic Consumer Negative Controls (NOT RTL evidence)",
        "", "This report is an offline mock validation of a proposed E1/E3 trace contract.",
        "It provides **zero** real XSim invocations and **zero** scientific E3-negative proofs.",
        "Consumer enumeration and signal mapping are NOT independently reviewed.",
        "", "| Case | E1 | Classification (synthetic / unverified) | E3 witnesses |",
        "|---|:---:|---|---:|",
    ]
    for row in rows:
        lines.append(f"| `{row['experiment_id']}` | {int(row['e1_injected'])} | `{row['observed_status']}` | {len(row['e3_witnesses'])} |")
    lines += ["", "## What this does NOT establish", "",
              "- No real RTL E3-negative or consumer exhaustive monitoring result.",
              "- No on-chip detector, physical upset-rate, G6/G7 approval, or Full400 FI.",
              "- A declared consumer list is not evidence that all actual RTL consumers are covered.",
              "- `E3_NOT_MONITORED` and incomplete coverage are never mapped to E3 FALSE.",
              "", "## Next real-experiment requirements", "",
              "Independent signal-map review, bound raw XSim plusargs/source hashes,",
              "cycle-accurate PRE_NBA edge traces, source overwrite/lifetime closure,",
              "complete per-consumer sampling gates, and a distinct authorized run budget.",
              "No such execution is provided or authorized by this package.", ""]
    return "\n".join(lines).encode("ascii")


def expected_artifacts() -> dict[str, bytes]:
    cases = demo_cases()
    names = set()
    artifacts = {}
    rows = []
    for case in cases:
        exp = case["experiment_id"]
        need(exp not in names, "duplicate synthetic fixture ID")
        names.add(exp)
        raw = canonical(case)
        need(read_json_unique(raw) == case, "canonical fixture not stable")
        artifacts[f"cases/{exp}.json"] = raw
        row = validate_trace(case, digest(raw))
        rows.append(row)
        artifacts[f"case_results/{exp}.json"] = canonical(row)
    counts: dict[str, int] = {}
    for row in rows:
        key = row["observed_status"]
        counts[key] = counts.get(key, 0) + 1
    summary = {"schema": REPORT_SCHEMA, "scope": STATUS_SCOPE,
               "synthetic_cases": len(rows), "status_counts": dict(sorted(counts.items())),
               "real_fi_injections": 0, "new_xsim_runs": 0, "real_e3_negative_proofs": 0,
               "g6_production_qualified": False, "g7_scientific_approved": False,
               "full400_authorized": False,
               "proof_boundary": "all fixtures and model traces synthetic; no independently reviewed real consumer enumeration"}
    artifacts["V4Q_SUMMARY.json"] = canonical(summary)
    artifacts["V4Q_CASES.csv"] = csv_bytes(rows)
    artifacts["V4Q_REPORT.md"] = report_text(rows)
    manifest = {"schema": "CNN_PEP_V4Q_OUTPUT_HASH_MANIFEST_V1",
                "files": {name: digest(raw) for name, raw in sorted(artifacts.items())}}
    artifacts["V4Q_OUTPUT_SHA256.json"] = canonical(manifest)
    return artifacts


def _safe_write(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".v4q_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def build(out_dir: Path):
    need(not out_dir.exists(), "fresh output required; never overwrite existing evidence")
    out_dir.mkdir(parents=True, exist_ok=False)
    artifacts = expected_artifacts()
    for name, raw in sorted(artifacts.items()):
        _safe_write(out_dir / name, raw)
    audit(out_dir)
    print("V4Q_SYNTHETIC_CONTRACT_PASS: 7/7 mock fixtures; NC1 declared-scope negative TEST_ONLY; "
          "0 XSim; no real E3-negative proof; FULL400 BLOCKED")


def audit(out_dir: Path):
    need(out_dir.is_dir() and not out_dir.is_symlink(), "missing or symlink output directory")
    artifacts = expected_artifacts()
    found = {}
    for path in out_dir.rglob("*"):
        need(not path.is_symlink(), "symlink in evidence output")
        if path.is_file():
            found[path.relative_to(out_dir).as_posix()] = path.read_bytes()
    need(set(found) == set(artifacts), "missing/extra artifact or output set drift")
    for name, expected in artifacts.items():
        need(found[name] == expected, "output artifact drift: " + name)
    print("V4Q_INDEPENDENT_MOCK_REPLAY_PASS: 7/7 byte-identical fixtures/results, SHA manifest "
          "rechecked; 0 XSim; no real E3 evidence; FULL400 BLOCKED")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True, choices=("demo", "audit", "inspect"))
    ap.add_argument("--out-dir", type=Path, help="fresh directory for demo; existing one for audit")
    ap.add_argument("--trace", type=Path, help="candidate trace for read-only inspect (NOT real qualified)")
    args = ap.parse_args(argv)
    try:
        if args.mode == "demo":
            need(args.out_dir is not None and args.trace is None, "demo requires --out-dir only")
            build(args.out_dir)
        elif args.mode == "audit":
            need(args.out_dir is not None and args.trace is None, "audit requires --out-dir only")
            audit(args.out_dir)
        else:
            need(args.trace is not None and args.out_dir is None, "inspect requires --trace only")
            need(args.trace.is_file() and not args.trace.is_symlink(), "missing/symlink trace")
            raw = args.trace.read_bytes()
            need(b"\r" not in raw, "new V4Q trace requires canonical LF, not CRLF")
            trace = read_json_unique(raw)
            need(raw == canonical(trace), "trace must be byte-canonical UTF-8 ASCII JSON")
            report = validate_trace(trace, digest(raw))
            sys.stdout.buffer.write(canonical(report))
    except (TraceError, OSError) as exc:
        print(f"V4Q_FAIL_CLOSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

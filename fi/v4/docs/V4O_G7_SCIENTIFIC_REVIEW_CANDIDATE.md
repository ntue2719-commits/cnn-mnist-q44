# CNN_PEP — V4O G7 Scientific Evidence Review (CANDIDATE)

**Status:** `SCIENTIFIC_EVIDENCE_REVIEW_CANDIDATE_ONLY` — **NOT G7 APPROVED**, **G6 production NOT QUALIFIED**, **FULL400 BLOCKED**. No new Vivado/XSim run. This review does not amend, overwrite or sign the pinned V4f4 scientific protocol core.

## 1. Exact provenance and review boundary

This candidate read the historical `CNN_PEP_AI_READY_FULL_HANDOFF_2026-11-04.zip`, the V4M 40-case historical raw-L2 results ZIP and the V4N independent golden/trace witness ZIP. Their SHA256 values, immutable frozen file SHA256 and the V4f4 `protocol_core.json` SHA256 are recorded in `V4O_G7_REVIEW_CANDIDATE.json`.

Scope independently cross-checked: **40 IMG0000 frozen tuple IDs**, 40 original raw XSim logs and their SHA256, single `FI_APPLIED`/`FI_RESULT_V2` markers with selected semantic fields, outcome and checkpoint counters, V4M raw row labels, V4N witness IDs, 16 L2-negative rows with full declared stage counters, and **676/676 independently computed golden POOL1 bytes** using channel-major 2×2 max-pooling.

**This is a cross-evidence integrity and scientific-interpretation check, not a fresh XSim run, complete consumer E3 remonitoring, full-output byte-level faulty RTL comparison, or G7 approval.** Historical source provenance and comparator ancestry must still be assessed independently at final G0/G5 review.

## 2. RQ-B — Same-scenario observability

| Level | TRUE | FALSE | INCONCLUSIVE | Numerator/denominator |
|---|---:|---:|---:|---:|
| L0 — harmful outcome (design NONCOMPLETION, protocol violation, wrong label) | 9 | 31 | 0 | 9/40 |
| L1 — L0 OR wrong final raw logits | 20 | 20 | 0 | 20/40 |
| L2 — L1 OR validated declared stage mismatch | 24 | 16 | 0 | 24/40 |

- Increment `L1 minus L0`: **11/40, +27.5 percentage points** on the same evaluable scenario IDs.
- Increment `L2 minus L1`: **4/40, +10.0 percentage points** on the same evaluable scenario IDs.
- Four incremental cases = `FI_P400_0101`, `FI_P400_0102` (`BUF_0007`, config A/B) and `FI_P400_0301`, `FI_P400_0302` (`DAT_0002`, config A/B).
- Both POOL1 and POOL2 were fully counted in the 16 L2=FALSE cases: **169/169 words and 25/25 words, zero mismatches**. L2=FALSE means a fully observed negative under the TB counter contract, not an independent per-byte faulty RTL dump.
- All **20 final-output-match cases** split into 4 with TB-reported POOL1 mismatch and 16 with complete POOL1/POOL2 TB-counter match. `FINAL_OUTPUT_MATCH` does not by itself prove absence of intermediate corruption or consumer exposure.
- `L2=TRUE` includes the 20 inherited L1 positives; it **does not** mean 24 cases with an independently detected internal checkpoint mismatch.

By structural class:

| Class (10 tuples each) | L0 TRUE | L1 TRUE | L2 TRUE | L2-only |
|---|---:|---:|---:|---:|
| CTL | 9 | 10 | 10 | 0 |
| BUF | 0 | 0 | 2 | 2 |
| MEM | 0 | 4 | 4 | 0 |
| DAT | 0 | 6 | 8 | 2 |
| **Total** | **9** | **20** | **24** | **4** |

These are *directed, selected IMG0000 configurations*. The four L2-only tuples arise from **two target IDs**, each with A/B configurations. They are not four independent physical upset samples. The planned Full400 (20 targets × 10 images × 2 configurations) is **NOT EXECUTED**.

## 3. Four witnesses — what exactly is proven

| Case | Evidence supporting extra L2 observation | Scope limitation |
|---|---|---|
| BUF_0007 A/B (`0101`, `0102`) | POOL1 golden byte index 337 = `0x00`; V3b trace observes corrupted `0x01` at accepted downstream consumer; TB counters report POOL1 169 words with one mismatch, POOL2 25 words with none; final logits/label match. | No complete independent faulty POOL1 byte dump; one stage-mismatch count is TB-reported. |
| DAT_0002 A (`0301`) | ReLU1 accepted value `0x00 -> 0x01`; independent 2×2 max-pool predicts POOL1 index 78 `0x00 -> 0x01`; TB counters report one POOL1 mismatch and none at POOL2; final logits/label match. | Predicted POOL1 value is **not** a directly logged output sample. |
| DAT_0002 B (`0302`) | Same method for POOL1 index 119. | Same limitation. |

The independent golden 2×2 max-pool recomputation matched all **676/676 bytes**, confirming the fault-free reference arithmetic/ordering. It does **not** independently replay 676 faulty RTL output bytes.

## 4. G7 interpretation decisions required of human reviewers

| Decision | Proposed interpretation | Required action |
|---|---|---|
| **R1: Stage scope** | **Only POOL1 (169 × 32 bit) and POOL2 (25 × 64 bit)** are declared for this Pilot400 L2 result. | Compare with core V1's generic text `other predeclared stage checkpoint mismatch`. Decide whether the annex merely clarifies the already-pinned scope or materially changes it. If material, create **versioned V2 core and fresh prepare/attestations**; never edit V1 bytes. |
| **R2: Stage status** | `MATCH` only after full comparison with zero mismatches; positive validated mismatches imply `MISMATCH` (report partial coverage separately); zero words means `NOT_OBSERVED`; partial zero-mismatch means `INCONCLUSIVE`; invalid counts are rejected. | Approve exact RTL-counter-to-stage mapping and checker boundary; preserve missing/invalid counts. |
| **R3: Outcome and infrastructure** | Preserve precedence `NONCOMPLETION > PROTOCOL_VIOLATION > WRONG_LABEL > WRONG_LOGITS_SAME_LABEL > FINAL_OUTPUT_MATCH`. Host timeout/crash/kill must never become RTL `NONCOMPLETION` (design watchdog = 3000 cycles relative to `start_cycle`). | Review strict original TB and log parser semantics; no implicit repair. |
| **R4: Denominator** | Compare levels using **same evaluable scenario IDs**. Planned=400, actual analyzed=40 historic IMG0000 directed tuples; present missing and unassessed separately. E3 assessed ≠ all simulated scenarios in future campaigns. | Approve planned, executed, injected, classifiable, E3-assessed, Lk-evaluable, missing, invalid and inconclusive schema. |
| **R5: Value vs stage claim** | Four stage discoveries are **TB-counter-based**, strengthened by golden computation and value-aware traces. | Do not describe them as complete independent faulty-feature-map measurement or hardware detector coverage. |
| **R6: Formal protocol seal** | Core V1 SHA256 `30a5ea2e66bda0d38e38d0a45635da5abe5ddbcf846672ef9d2e20cf546cf733`. | Two real, distinct operator/reviewer signoffs on the exact agreed protocol version and annex; no AI identity attestation. G7 approval **does not** authorize G6 or Full400. |

**Unresolved at present:** R1–R6 require actual review/attestation. `G7_PREPARED_NOT_APPROVED` remains the correct status.

## 5. Paper claims — safe wording vs prohibited extrapolations

**Permitted with stated scope:** "In 40 directed RTL fault scenarios on one MNIST image, output-level observability identified 20/40 cases at L1, while adding declared POOL1/POOL2 checkpoint counters identified four additional cases (24/40 at L2). The four added cases had final-output matches and are supported by historical value-aware traces and independent golden-stage arithmetic checks."

**Do not claim:** random/physical SEU rates, population fault tolerance estimates, 400 real FI executions, 24 independently sampled faulty stages, detection coverage of an on-chip circuit, independent per-byte faulty feature dump, or completed G7/G6 authorization.

## 6. Next controlled action

1. A human technical reviewer evaluates R1–R5 on the already frozen core and candidate annex; record decisions *without editing historical evidence*.
2. If no semantic change: preserve the exact V1 SHA and establish version-bound independent human approvals using the existing `qualify_science_v4f4.py` review protocol. If a semantic change is necessary: new V2 core, new prepare, then human approvals.
3. Keep G0/G5/G6 production gaps separate. **Do not run Full400 until genuinely signed final G0–G7 authorization is independently verified.**

*All results in this candidate are read-only analyses of user-supplied historical evidence. They do not upgrade the original official protocol or create new FI outcomes.*

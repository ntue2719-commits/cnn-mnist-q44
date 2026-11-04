# CNN_PEP V4f Phase 4F — Production Full400 Contract & Offline Qualification

**Version:** V1 candidate, 2026-11-04. **Not a G6 release approval.**
**Full400:** BLOCKED / NOT EXECUTED. **G7:** PREPARED / NOT APPROVED.
**Failure canary attempt budget:** 8/8 exhausted. No ninth canary, no hidden override.

## A. Purpose and non-goals

This patch packages a fail-closed **production runner contract** but not a live
production executor. Its `execute` / `full400` modes and `--execute` / `--approve`
flags are rejected before filesystem access. It cannot call Vivado/XSim,
launch another canary, or approve G0–G7. There is deliberately no `subprocess`
execution function in either Phase 4F script.

The existing V4f4B shadow contract validates scheduler boundaries and atomic
checkpoint logic. V4f4C/D/E proves a bounded Windows Job Object failure
qualification (8/8 attempts, exactly two committed healthy canaries). Phase 4F
adds an explicit 400-tuple production contract, a separate **read-only** Vivado
source / four-tool SHA preflight, transactional no-FI rehearsal, independently
rechecked history ZIP archives, and a release-gap report.

## B. Frozen scientific and engineering inputs

- `fi/config/fi_targets_candidates_v2.csv` pinned SHA256
  `d9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d`
- `fi/config/pilot400_targets.csv` pinned SHA256
  `98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25`
- `fi/scenarios/pilot400.csv` pinned SHA256
  `cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4`
- `fi/tb/tb_cnn_mnist_fi_pilot400.v` pinned SHA256
  `2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13`
- The 10 real sample asset bundles and six P0 weights/biases must match
  `sample_provenance.json` SHA256 and structural contracts, without rewriting.
- Exactly 20 frozen targets × 10 sample IDs × 2 bits/times = 400 **selected**
  configurations. Ordered ID range `FI_P400_0001..0400`, 16 × 25 batches.
- DAT_0010 A is bit0 @1340 and B bit1 @1340, unchanged.
- Every future production FI run must pass exactly **10** plusargs:
  `FI_OFFSET`, `SCENARIO_ID`, `TARGET_ID`, `FI_BIT`, `SAMPLE_ID`,
  `INPUT_FILE`, `POOL1_FILE`, `POOL2_FILE`, `LOGITS_FILE`, `PREDICTION_FILE`.
  The `PILOT_PROFILE_ONLY` plusarg is forbidden for actual FI.

## C. Phase 4F command modes (all offline)

### `production_runner_v4f4f.py --mode plan`

Creates a fresh `fi/results/v4f4f_*` output with exact 400-row CSV, 16 batch
hashes, 400 tuple semantic SHA and SHA256 inventory of source and sample files.
Explicitly marks G6 production NOT QUALIFIED, G7 not approved and all
release/execute flags false. No XSim.

### `--mode rehearsal [--pause-after N | --resume]`

Exercises a synthetic 400-transaction state machine **without injecting faults**:

1. Validate pinned input bytes and the entire prior committed prefix.
2. Create and flush `inflight.json` atomically (exclusive create) for next ID.
3. Exclusive-create a no-XSim receipt with frozen tuple hash and chain digest.
4. Atomically fsync+replace the ledger with the one new event hash.
5. Remove inflight marker; maintain deterministic 16 × 25 boundaries.

Any crash at any unclean transaction edge leaves an inflight, orphan receipt,
missing receipt, ledger temp, changed source pin, unexpected file, or broken
hash chain that causes FAIL-CLOSED on resume/audit. The code **does not**
repair these automatically; use a separately investigated new output attempt.
Controlled `--pause-after` is allowed only after a clean commit; `--resume`
checks every previously committed byte. Unit tests simulate crashes and must
not be presented as real OS/XSim recovery.

### `--mode source-preflight --project ... --vivado ...`

No compile/elaborate/XSim. Verify the Vivado-authoritative `.srcs` files listed
in the exact V3a pinned `.prj` (`source_vlog.prj` and validation manifest),
including the frozen FI TB and generated `glbl.v`. Hash the Vivado executable,
`xvlog.bat`, `xelab.bat`, `xsim.bat`, original `.xpr`, each HDL file and `.prj`.
An archive lacking `glbl.v` must **fail** rather than fabricate it.
The recorded preflight is not a build-once qualification, nor a Vivado
simulation result, nor an authorization. Do not use repo/rtl as assumed P0
source authority in place of Vivado project's `.srcs` tree.

### `audit_production_v4f4f.py --mode rehearsal`

Read-only replay of 400 synthetic receipts, exact hash chain, source pin
inventory, canonical frozen schedule and release flags. Never count synthetic
events as FI outcomes.

### `audit_production_v4f4f.py --mode history`

Read both independently archived historical G6 ZIPs, matching SHA256 from the
2026-11-04 eight-attempt independent review, verify all 8 attempt/receipt
pairs and **590** raw artifact SHA256, preserve slot3 and slot7
`NOT_REPRODUCED`, preserve timeout-parent SHA in recovery and the slot8 LF ACK.
**No new XSim**. Archive byte hash identifies exactly the previously reviewed
ZIP, not arbitrary repacked ZIP files.

### `audit_production_v4f4f.py --mode release-review`

Summarize supplied source, history and rehearsal evidence and list missing
G0–G7 approvals. **Always BLOCKED**, even when all three receipts pass.

## D. Architectural requirements for a *future* executable runner (NOT IMPLEMENTED)

A later separately approved patch, not this Phase 4F candidate, must:

- Have a separate signed immutable authorization document binding the exact
  frozen plan, protocol seal SHA, source `.prj`, Vivado toolchain SHA,
  production runner SHA, intended FI budget (400) and explicit G0–G7 review.
- Implement **build-once**, full SHA256 provenance checking at creation and
  before/after every re-used XSim process. Re-compile only after a documented
  attempt restart in a fresh output namespace; never silently reuse an old
  snapshot.
- Run sequentially in a private Windows Job Object with verified child PID,
  lifecycle and kill-on-close. Never kill unrelated Vivado or XSim processes.
- Have strict per-scenario ID+10-plusarg+FI_APPLIED+FI_RESULT_V2 replay,
  injection verification, and outcome priority (NONCOMPLETION,
  PROTOCOL_VIOLATION, WRONG_LABEL, WRONG_LOGITS_SAME_LABEL,
  FINAL_OUTPUT_MATCH). A failed XSim process is **infrastructure invalid**,
  never a design NONCOMPLETION.
- Require immutable per-attempt logs and artifact SHA before commit; every
  resumed committed scenario is re-verified independently. Orphan and
  post-commit inflight markers block, never auto-heal.
- Preserve `FINAL_OUTPUT_MATCH != masked/no consumer exposure`; never infer
  E3 from a mechanism-only shakedown or count fault-free temporal checks as FI.
- Emit explicit `planned/executed/valid/classifiable/E3_assessed/evaluable`
  denominators as separately frozen by human-approved G7. Missing, absent
  intermediate checkpoints and infrastructure-invalid data are never zeros.
- Require a new independent G6 engineering qualification/release decision;
  the bounded eight canaries alone cannot sign off a new 400-run backend.

## E. Required next release gates

G0 and G5 provenance/scientific mapping: still PARTIAL. G6 production runner:
**NOT QUALIFIED**. G7: operator + independent reviewer must genuinely approve
an exact-version scientific core and annex; `prepare` SHA alone is not seal.
Final independent signed G0–G7 authorization artifact: **ABSENT**.
Therefore 400 actual XSim runs remain **BLOCKED**. No automatic target
promotion, source modification, new canary attempts, or G7 self-approval.

# V4f4G — Immutable Invocation Contract (Candidate / Not Released)

## Contract purpose

V4f4F has independently rehearsed 400 synthetic transactions on Windows,
but its `execute`/`full400` modes are deliberately prohibited. V4f4G fills a
specific **interface gap**, not an execution-safety gap: encode the exact
future XSim inputs and evidence layout for each frozen FI scenario in a
reviewable non-executable artifact.

## Input provenance

- Exact 400 rows in the frozen `fi/scenarios/pilot400.csv`, verified against
  `fi/config/pilot400_targets.csv` and `fi/config/fi_targets_candidates_v2.csv`.
- Existing **pure plan** from V4f4F (`v4f4f_production_contract.json`),
  verified using the V4f4F ledger auditor, including source and asset pins.
  A completed rehearsal ledger is **not** a pure plan and is rejected.
- Every future invocation binds its unique `scenario_id`, target class/ID,
  bit, absolute injection offset and the five per-image data/golden paths.

## Exact per-scenario plusargs

1. FI_OFFSET
2. SCENARIO_ID
3. TARGET_ID
4. FI_BIT
5. SAMPLE_ID
6. INPUT_FILE
7. POOL1_FILE
8. POOL2_FILE
9. LOGITS_FILE
10. PREDICTION_FILE

`PILOT_PROFILE_ONLY` is **forbidden** in actual FI runs. All 400 argument
maps are reconstructed from the frozen plan during read-only audit.

## Required future executor guarantees (NOT IMPLEMENTED)

- Bind a separately approved signed G0–G7 release to the exact script,
  G7 protocol core + interpretation annex, Vivado source `.prj`, XPR, toolchain
  SHA256, and approved **400** FI budget. Fail closed if any hash changes.
- Build-once compile/elaborate from Vivado-authoritative `.prj`; verify
  source/toolchain hashes before and after snapshot use. Never silently
  rebuild in-place during recovery.
- One private Windows Job Object per sequential XSim invocation; verified
  child PIDs and termination, finite timeout, no foreign-process kill.
- Strict process/plusargs/log verification: one `FI_APPLIED` and one
  `FI_RESULT_V2`, correct scenario and offset, injection verified, outcome
  precedence. Host timeout/crash is **invalid infrastructure evidence**, not
  design NONCOMPLETION.
- Write raw logs/process receipts immutably; only atomic-commit after all
  source, XSim process and parser checks pass. Refuse orphan or half-committed
  checkpoint; never auto-heal or overwrite failed attempts.
- Separate denominators for planned/executed/valid/classifiable/E3-qualified
  scenarios, according to **human-approved G7**. A final output match alone
  does not demonstrate fault masking or absence of propagation.

## Output / audit

`invocation_blueprint_400.json` contains 400 ordered rows, no executable
commands, no actual run directory or XSim snapshot. It includes future
log/process receipt namespaces only. `blueprint_receipt.json` pins the parent
plan, blueprint, source input fingerprint and generator bytes via SHA256.
Auditor checks every byte and recomputes from frozen source; no hidden
release switch can be set. The full output is **OFFLINE** and always
`G6_production=NOT_QUALIFIED`, `G7=PREPARED_NOT_APPROVED`, and
`full400_authorized=false`.

## Evidence hierarchy / scientific boundary

- 400 V4f4F rehearsal events and 400 V4f4G invocation specs: **NO FI**.
- 8 V4f2 XSim shakedowns and 8 bounded V4f4C–E attempts: **real bounded
  tests**, but not the 400-scenario dataset.
- V4dB 400 fault-free temporal windows: **not 400 FI**.
- `FINAL_OUTPUT_MATCH` is not proof of E3 masking; unmonitored stage
  consumers remain unknown, not zero.

This candidate does not cure missing Vivado `glbl.v`, independent G7 approval,
or production executor G6 qualification; Full400 stays BLOCKED.

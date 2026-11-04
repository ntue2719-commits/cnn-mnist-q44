# CNN_PEP V4f4 — G7 Interpretation Annex (CANDIDATE, NOT FROZEN)

**Purpose:** resolve interpretation points found in the independent review of V4f4 scientific protocol. This annex is a **proposal for human scientific review**, not a modification of an already SHA256-pinned core, not G7 seal and not Full400 authorization. If any rule materially changes the official measurement contract, issue a **new versioned protocol core and re-run prepare/attestation** rather than editing previous evidence.

**Parent protocol core SHA256:** `30a5ea2e66bda0d38e38d0a45635da5abe5ddbcf846672ef9d2e20cf546cf733`.

**Frozen Pilot400 TB SHA256:** `2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13`.

## 1. Outcome priority and E1/E2/E3

Keep outcome precedence `NONCOMPLETION > PROTOCOL_VIOLATION > WRONG_LABEL > WRONG_LOGITS_SAME_LABEL > FINAL_OUTPUT_MATCH` and harmful set `{NONCOMPLETION, PROTOCOL_VIOLATION, WRONG_LABEL}`. `WRONG_LOGITS_SAME_LABEL` means numeric corruption without misclassification. A valid `FI_APPLIED` plus `FI_RESULT_V2` establishes E1/mechanism and evaluated outcome; it **does not** establish independent, value-aware E3 consumer exposure. `NOT_INDEPENDENTLY_MONITORED_V4F2` means *not assessed*, not *consumer did not see the upset*.

An infrastructure timeout, XSim crash, missing/incomplete log or hash mismatch is `INVALID_EVIDENCE` / `INCONCLUSIVE`, **never** a design `NONCOMPLETION` outcome. Do not promote historic scenario target evidence status. Repeated XSim runs are reproducibility checks, not additional independent scenario IDs.

## 2. Current L2 measurement contract: ONLY POOL1 and POOL2

The frozen TB uses `POOL1_WORDS = 169`, four 8-bit comparisons per word (676 bytes), and `POOL2_WORDS = 25`, eight 8-bit comparisons per word (200 bytes). It outputs counters `p1_words`, `p2_words`, `pool1_mismatch` and `pool2_mismatch` in `FI_RESULT_V2`. Interpret **only these two declared checkpoint stages**; "other checkpoint mismatch" is not authorized for this Pilot400 analysis without a new protocol version.

For a given stage, after confirming that one valid `FI_RESULT_V2` is present, its count is a nonnegative integer, and the checked data was actually compared to the golden for the same `sample_id`:

| Record condition | Stage status |
|---|---|
| `words = expected`, `mismatch = 0`, complete comparison validated | `MATCH` |
| `0 < words <= expected` and `0 < mismatch <= words × channels`, comparisons valid | `MISMATCH`, and **report partial coverage separately** |
| `words = 0`, `mismatch = 0` | `NOT_OBSERVED` |
| `0 < words < expected`, `mismatch = 0` | `INCONCLUSIVE`: partial negative observation cannot prove match |
| Counter negative, words exceed maximum, mismatch exceeds compared bytes, or contradictory record | `INVALID_EVIDENCE` (or formally documented `INCONCLUSIVE`), never `MATCH` |

`NOT_APPLICABLE` is allowed only if a stage is explicitly outside the versioned instrumented scope for that scenario; do not use it to hide a missing or interrupted checkpoint. The TB always declares both Pool stages, so no silent `NOT_APPLICABLE` defaults.

Apply three-valued logic (`TRUE`, `FALSE`, `INCONCLUSIVE`) **after** validating the outcome:

- `L0 = TRUE` for `NONCOMPLETION`, `PROTOCOL_VIOLATION`, `WRONG_LABEL`; otherwise `FALSE` for valid classified outcomes.
- `L1 = TRUE` when `L0 = TRUE` or the outcome is `WRONG_LOGITS_SAME_LABEL`; otherwise `FALSE` only for a valid `FINAL_OUTPUT_MATCH`.
- `L2 = TRUE` whenever `L1 = TRUE`, or a validated POOL1/POOL2 status is `MISMATCH`.
- `L2 = FALSE` only when `L1 = FALSE` and both declared stages are validated `MATCH`.
- Else `L2 = INCONCLUSIVE`; no stage can be silently filled with a zero mismatch count when it was not observed.

Example: an observed `PROTOCOL_VIOLATION` with POOL2 `NOT_OBSERVED` still has `L0=L1=L2=TRUE` by the union definition, but POOL2 must remain `NOT_OBSERVED` in the raw stage report.

## 3. Predeclared descriptive denominators

Let `P` be the fixed set of 400 frozen scenario IDs. Let `V` be the subset with strict, hash-validated E1 + outcome evidence from the **design experiment**. Let `C` be outcome-classifiable IDs in `V` (a valid design NONCOMPLETION is classifiable). Let `E_k` be the IDs within `C` for which observation level `Lk` is evaluable (`TRUE` or `FALSE`). Let `E3` be independently monitored and interpretable consumer cases, not all E1 cases.

- Always report `|P| = 400`, `|V|`, `|C|`, `|P \ V|` (missing/invalid separately) and `|C \ E_k|` (inconclusive for each Lk).
- By class/target/image: outcome count `n_o` divided by `|C_g|` **only as a descriptive conditional fraction**; also show `|P_g|` and missing count. Do not silently use 400 for class-specific rates or shrink the planned denominator in an unlabelled way.
- Lk observable fraction = `n(Lk=TRUE)/|E_k|`, reporting `|E_k|` and excluded/missing counts. When `|E_k|=0`, mark `NOT_ESTIMABLE`, not 0%.
- Increment `L1\L0`: number of scenario IDs with `L1=TRUE AND L0=FALSE` divided by IDs with **both** values evaluable. Likewise `L2\L1` requires both evaluable; report the increment count and denominator, not only a percentage.
- E3 consumer categorization is conditioned on `|E3|`; `E3_NOT_MONITORED` is a separate count, **not** nonexposure.
- For paired A/B comparisons, the pair key is `(target_id, sample_id)`; count only pairs with **both** frozen configurations present and evaluable for the specific paired metric. Maximum paired units = `20 × 10 = 200`.

These are descriptive rates on a **directed selected sample**. Do not report confidence intervals or population SEU rates under a random-FI or physically representative interpretation.

## 4. Watchdog vs external runtime timeout

In the frozen TB, `MAX_WAIT_CYCLES=3000` relative to `start_cycle` in its design-observation task. The TB declares legal FI offsets in `[3,2999]`. A `NONCOMPLETION` outcome may be classified only from the **valid design-level TB observation** following the locked watchdog semantics, with required strict FI evidence. An outer Python/subprocess XSim timeout, OS kill, `xvlog`/`xelab`/`xsim` tool crash, or a partial/orphan log is an infrastructure failure. It must not increase any design harmful count. `first_bad_stage` identifies the earliest **observed** bad condition, not the root physical cause.

## 5. Review and version control

The operator and genuinely independent scientific reviewer should agree on this mapping and on whether it changes the semantics of the existing byte-pinned V4f4 core. If material, create a new versioned core, new SHA256, new `prepare` and new manual approvals; do **not** edit existing V4f4 preparation or attest to a protocol whose interpretation is unresolved.

**Current state:** review proposal only. G7 remains **PREPARED, NOT APPROVED**. Full400 remains **BLOCKED**.

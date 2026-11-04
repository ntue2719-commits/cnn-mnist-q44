# CNN_PEP V4M — Read-only G7-L2 historical raw-evidence analysis (CANDIDATE)

## Decision

This addition addresses the scientific G7 L2 evidence gap using **existing, historical V4c IMG0000 raw XSim logs**. It is a read-only, offline analysis. It does not run Vivado, XSim, compile, perform new FI, create an executor, modify the frozen protocol, approve G7, or authorize Full400.

V4H previously analyzed only the V4c **matrix**, so it conservatively marked the 20 L1-negative scenarios as L2 INCONCLUSIVE. V4M additionally reads the **actual SHA256-pinned FI_RESULT_V2 counters** and the frozen testbench comparison logic. These are distinct analysis-evidence tiers, and the older matrix-only report must not be silently rewritten.

## Source qualification

- SHA256 of the original 2026-11-04 handoff archive reviewed locally: `3101ebba94822e0ef92566860f8ecdbc7570f02de59bbc8e1b4deaf65bb4891a`.
- `fi/scenarios/pilot400.csv` SHA256 `cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4`.
- `fi/config/pilot400_targets.csv` SHA256 `98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25`.
- Frozen original `fi/tb/tb_cnn_mnist_fi_pilot400.v` SHA256 `2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13`.
- V4c matrix SHA256 `36633692e0edee31f5de87682a49499bf192e1b65648737aa84e8c9178f19d36`, linked by the V4c manifest.
- `IMG0000/prediction.hex` SHA256 `56a468fa75d7e429e99b3f560bbdcef20821e79f63a70a7ade6660fac6617682`.
- Strictly find exactly 40 **raw XSim logs** whose **actual bytes** match each matrix log SHA256; replay exactly one `FI_APPLIED` and one `FI_RESULT_V2`, freeze target, sample, bit and offset, and check result/outcome consistency.
- For the four L2-only scenarios, compare the stage-counter code block in the two V3b isolated TBs (`BUF_0007`, `DAT_0002`) **byte-identically** with the original frozen TB, and check isolated TB SHA256 against V3b manifests.

## Proposed L2 semantics (candidate Annex, NOT an approved protocol core)

A checkpoint's `words == expected` and `mismatch == 0` is MATCH; `mismatch > 0` with valid compared bytes is MISMATCH; no stage observations is NOT_OBSERVED; incomplete negative observations are INCONCLUSIVE; impossible counters are rejected. `POOL1` has 169 words x 4 bytes and `POOL2` has 25 words x 8 bytes. The stage monitor comparison is inside the frozen RTL testbench. Stage status here does **not** claim standalone replay of the actual intermediate feature values by an independent Python golden comparator.

L0 TRUE = NONCOMPLETION, PROTOCOL_VIOLATION or WRONG_LABEL. L1 TRUE = L0 TRUE or WRONG_LOGITS_SAME_LABEL. L2 TRUE = L1 TRUE or validated stage MISMATCH. L2 FALSE needs L1 FALSE and both stages complete MATCH, else INCONCLUSIVE. Tool/OS timeout and incomplete raw logs cannot be reclassified as design NONCOMPLETION. E3 remains separate: V4c had isolated E3/decision proof, but this analysis did not repeat it.

## Historical directed IMG0000 results

| Class | L0 TRUE | L1 TRUE | L2 TRUE | Configurations |
|---|---:|---:|---:|---:|
| CTL | 9 | 10 | 10 | 10 |
| BUF | 0 | 0 | 2 | 10 |
| MEM | 0 | 4 | 4 | 10 |
| DAT | 0 | 6 | 8 | 10 |
| **All** | **9** | **20** | **24** | **40** |

- L1 additional vs L0: 11/40 scenarios, +27.5 percentage points.
- **L2 additional vs L1: 4/40 scenarios, +10 percentage points**.
- Of 20 valid L1 FALSE cases, 4 show stage mismatch while all logits/label remain equal, and 16 have complete POOL1/POOL2 MATCH.
- Four L2-only tuples: `FI_P400_0101`, `FI_P400_0102` (target `BUF_0007`, A/B), `FI_P400_0301`, `FI_P400_0302` (target `DAT_0002`, A/B). Each has `p1_words=169, p1_mismatch=1, p2_words=25, p2_mismatch=0`, outcome `FINAL_OUTPUT_MATCH`.
- This is **two targets with two directed configs each**, NOT four statistically independent sampled SEUs. No confidence interval/population SEU inference.

## Usage on Windows (no XSim)

Install the additive patch files after checking the archive SHA256 and `MANIFEST.json`. Do not overwrite files with identical names; do not modify frozen input/RTL/raw evidence. At the repository root:

```powershell
python -m unittest discover -s fi/v4/scripts -p "test_v4c_l2_v4m.py" -v

python fi/v4/scripts/analyze_v4c_l2_v4m.py --repo-root . --mode check

python fi/v4/scripts/analyze_v4c_l2_v4m.py `
  --repo-root . --mode analyze `
  --out-dir fi/results/v4m_g7_l2_img0000_01

python fi/v4/scripts/audit_v4c_l2_v4m.py `
  --repo-root . `
  --analysis-dir fi/results/v4m_g7_l2_img0000_01 `
  --out-dir fi/results/v4m_g7_l2_audit_01
```

Alternative: change `--repo-root .` to `--handoff-zip CNN_PEP_AI_READY_FULL_HANDOFF_2026-11-04.zip`, **only if that exact original archive exists**. Missing, incomplete, modified or inconsistent source results in `FAIL_CLOSED`, not in zero detections. Output directories must be new and start with `v4m_`; there is no overwrite mode. Run each step only after its predecessor exits with code zero.

## Review caveats / next scientific task

1. Results rely on **TB-reported stage counters** plus a byte-verified comparison block; they are **not** an independent feature-by-feature numerical replay of the two stages.
2. V4c target evidence matrices and isolated V3b manifests provide source/E3 ancestry, but this offline pass does not reproduce all compile/launch/G0 provenance or real-time consumer observations.
3. These 40 tuples are all **IMG0000**, not the planned Full400 across 10 images, and the selected A/B tuples are not random physical SEUs.
4. Scientific operator/reviewer must approve L2 semantics and signed core versioning if material; **G7 remains PREPARED_NOT_APPROVED**.
5. **G6 production NOT QUALIFIED; Full400 BLOCKED** regardless of the success of this offline read-only analysis.

**Suggested version label:** `V4M_G7L2_40_HISTORICAL_LOGS_READONLY_REPLAY_PASS__G7_NOT_APPROVED__FULL400_BLOCKED`.

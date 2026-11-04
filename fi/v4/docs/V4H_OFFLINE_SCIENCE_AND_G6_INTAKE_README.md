# CNN_PEP V4H — offline evidence intake + directed observability analysis

**State:** NEW ADDITIVE PATCH / LOCAL LINUX-OFFLINE TESTED / WINDOWS NOT YET VERIFIED.
**Release:** G6 production NOT QUALIFIED; G7 NOT APPROVED; FULL400 BLOCKED.
**Actual FI/XSim runs performed by this patch:** **0**.

## Purpose and scientific boundaries

After the operator-reported V4f4F 400/400 synthetic rehearsal and V4f4G 400/400 blueprint audit, the earlier uploaded handoff archive still contains code only through V4f4B. The updated V4f4F/V4f4G Python scripts and raw Windows output were not provided in this handoff. **Do not assert independent V4f4G invocation-contract verification from these markers alone.**

This patch deliberately provides two separately scoped workflows:

1. **V4c scientific analysis** (`analyze_v4c_observability_v4h.py` + `audit_v4h_science.py`): re-evaluate the 20-target/40-scenario IMG0000 V4c *summary matrix* against the exact frozen 400 rows and its pinned V4c manifest; report observed L0/L1 and logically determined/undetermined L2. It does **not** reparse historical XSim raw logs or prove new E3 evidence.
2. **V4f4F/G read-only intake** (`intake_v4f4fg_v4h.py`): verify four frozen SHA256 files, inventory/fingerprint the specific Windows source scripts and five recent plan/rehearsal/blueprint output directories, refuse forbidden release/real-XSim claims in JSON. It **does not** understand or independently certify the as-yet-unshared V4f4G blueprint schema, compile project, or run commands.

**Never merge** V4c directed findings with 400 synthetic checkpoints, the 8/8 V4f4C/D/E failure-qualification trials, or physical SEU population statistics.

## Additive files

- `fi/v4/scripts/analyze_v4c_observability_v4h.py`
- `fi/v4/scripts/audit_v4h_science.py`
- `fi/v4/scripts/intake_v4f4fg_v4h.py`
- `fi/v4/scripts/test_v4h_science.py`
- `fi/v4/scripts/test_intake_v4f4fg_v4h.py`
- `fi/v4/docs/V4H_OFFLINE_SCIENCE_AND_G6_INTAKE_README.md`
- `fi/v4/docs/V4H_PROGRESS_ADDENDUM_CANDIDATE.md`

No original RTL, FI TB, frozen CSVs, model data, prior runners, receipts or historical result files are included or changed.

## Windows PowerShell: offline instructions (from repository root)

These commands **never call Vivado/XSim**. Files named by the earlier user terminal must already be present locally. Use a new output directory on every production run; check `Test-Path` before execution.

```powershell
# 1. Offline unit and negative-fixture checks, no XSim.
python -m unittest discover -s fi/v4/scripts -p "test_*v4h*.py" -v

# 2. Validate frozen source + V4c evidence without writing results.
python fi/v4/scripts/analyze_v4c_observability_v4h.py --mode check

# 3. Create fresh V4H science outputs (only once in this namespace).
python fi/v4/scripts/analyze_v4c_observability_v4h.py `
  --mode analyze `
  --out-dir fi/results/v4h_science_img0000_01

# 4. Read-only audit of V4H-generated result and pinned V4c matrix.
python fi/v4/scripts/audit_v4h_science.py `
  --analysis-dir fi/results/v4h_science_img0000_01

# 5. Check source files + directories of operator-reported V4f4F/G results.
python fi/v4/scripts/intake_v4f4fg_v4h.py --mode check

# 6. Produce SHA256 inventory in a new directory; still 0 XSim.
python fi/v4/scripts/intake_v4f4fg_v4h.py `
  --mode inventory `
  --out-dir fi/results/v4h_g6_intake_01
```

`--mode check` is **read-only** and creates no directory. `--mode analyze` and `--mode inventory` create only a **new** `fi/results/v4h_*` directory. A pre-existing output is rejected; never run them into historical result namespaces.

For substantive independent V4f4G source+contract review, the following exact files/directories should be included in an operator-approved, read-only archive **without editing the originals**:

```powershell
$Inputs = @(
  "fi/v4/scripts/production_runner_v4f4f.py",
  "fi/v4/scripts/audit_production_v4f4f.py",
  "fi/v4/scripts/production_blueprint_v4f4g.py",
  "fi/results/v4f4f_plan_01",
  "fi/results/v4f4f_rehearsal_01",
  "fi/results/v4f4f_rehearsal_audit_01",
  "fi/results/v4f4g_blueprint_winfix_01",
  "fi/results/v4f4g_blueprint_audit_winfix_01"
)
$Archive = "v4f4fg_independent_review_01.zip"
if (Test-Path -LiteralPath $Archive) { throw "Archive exists; do not overwrite" }
foreach ($P in $Inputs) { if (-not (Test-Path -LiteralPath $P)) { throw "Missing: $P" } }
tar -a -c -f $Archive @Inputs
Get-FileHash -Algorithm SHA256 $Archive
```

`tar` creates a portable ZIP from the selected paths. **No Vivado environment setup is required for these offline checks.** The observed Vivado executable and project location remain known from the preceding Windows terminal; do not confuse PATH existence checks with source hash/preflight integrity checks.

## Expected scientific output from old handoff copy

Analysis successfully repeated on the *uploaded historical handoff copy* (not on the new Windows repository source):

| Class | N IMG0000 | L0 TRUE | L1 TRUE | L1 additional | final output match | L2 INCONCLUSIVE |
|---|---:|---:|---:|---:|---:|---:|
| CTL | 10 | 9 | 10 | 1 | 0 | 0 |
| BUF | 10 | 0 | 0 | 0 | 10 | 10 |
| MEM | 10 | 0 | 4 | 4 | 6 | 6 |
| DAT | 10 | 0 | 6 | 6 | 4 | 4 |
| **Total** | **40** | **9** | **20** | **11** | **20** | **20** |

**L2 semantics:** When L1 is `TRUE`, L2 is `TRUE` even without recorded stage checkpoints. When L1 is `FALSE`, the V4c matrix alone cannot establish both complete POOL1 and POOL2 `MATCH`, so L2 is `INCONCLUSIVE` for those 20 cases. It is **not** scientifically valid to report L2 incremental observability over all 40 until missing checkpoint evidence is resolved. On the 20 L2-evaluable cases alone, L1 was already `TRUE`; this does **not** imply zero L2 benefit across all 40.

The E3 record is **matrix-reported**, not a new independent reparse of the original consumer-monitor logs. Any journal-paper claim of E3/E4 pairing should cite/replay the original source logs and their source SHA256, not just this summary.

## Offline tests and acceptance boundary

Locally (Linux/Python), 26 unit/negative-fixture tests passed: `Ran 26 tests — OK`, no skipped tests. A separate mutation test changed a generated scenario outcome **and re-generated its SHA256SUMS**; the read-only auditor still rejected it against the immutable V4c matrix. Read-only scientific analysis + re-audit both PASS against the uploaded 2026-11-04 archive (0 XSim).

**Not verified by this patch:** actual Windows V4f4F/G source internals, 400 blueprint schema semantics, 10 plusargs per invocation, generated `.prj` hashes or genuine human sign-off. V4H intake must be read as `INVENTORY_ONLY_NOT_SEMANTIC_G6_AUDIT`, never G6 qualification.

**Paper next:** independently reconstruct E3+E4 pairs from raw logs, recover validated POOL1/POOL2 checkpoints if present, then after G0–G7 approval acquire Full400 real-FI outcome data to answer RQ-A/RQ-B. Do not run a ninth bounded failure canary or unlock Full400 from this patch.

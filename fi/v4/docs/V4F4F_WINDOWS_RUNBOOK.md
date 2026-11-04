# CNN_PEP — V4f Phase 4F Windows Runbook

**Version:** 2026-11-04, G6 production contract **OFFLINE ONLY**.

## 0. Guardrails

- Original RTL P0, frozen FI TB, 4 frozen CSV/TB SHA256, sample assets and
  historical results must remain byte-for-byte unchanged.
- **8/8 failure-qualification attempt slots are exhausted**. Do not run a
  ninth canary, nor any actual Full400 FI via Phase 4F scripts.
- No Phase 4F command invokes Vivado; the read-only source preflight hashes
  Vivado source and tools without executing them.
- `--mode execute`, `--mode full400`, `--execute`, and `--approve` are
  intentionally forbidden before filesystem work. No approval is implied by
  any Phase 4F `PASS` marker.
- G6 production runner NOT QUALIFIED, G7 PREPARED NOT APPROVED, Full400 BLOCKED.

## 1. Install and test

From the repo root `D:\Share_Data_ubuntu_window\Github\cnn-mnist-q44-rtl`, copy
`cnn_pep_v4f_phase4f_production_contract_patch.zip` into the repo root and
check that none of its six new target paths already exists (if present,
STOP and review; do not overwrite):

```powershell
$New = @(
  'fi/v4/scripts/production_runner_v4f4f.py',
  'fi/v4/scripts/audit_production_v4f4f.py',
  'fi/v4/scripts/test_production_runner_v4f4f.py',
  'fi/v4/docs/V4F4F_EXECUTION_SAFETY_SPEC.md',
  'fi/v4/docs/V4F4F_WINDOWS_RUNBOOK.md',
  'fi/v4/docs/V4F4F_PROGRESS_ADDENDUM_CANDIDATE.md'
)
$Existing = $New | Where-Object { Test-Path -LiteralPath $_ }
if ($Existing) { throw "New files already exist: $($Existing -join ', ')" }
Expand-Archive -Path '.\cnn_pep_v4f_phase4f_production_contract_patch.zip' -DestinationPath .
python -m unittest discover -s fi/v4/scripts -p 'test_production_runner_v4f4f.py' -q
```

Then, if appropriate, run full historical Python regression:

```powershell
python -m unittest discover -s fi/v4/scripts -p 'test_*.py' -q
python -m unittest discover -s fi/v3b/scripts -p 'test_*.py' -q
python -m unittest discover -s fi/scripts -p 'test_*.py' -q
```

Note: unittest fixtures may print XSim-looking markers from fake processes.
They are not new FI evidence. Some OS-specific/symlink tests may be skipped.

## 2. Frozen production plan — 0 XSim

```powershell
python fi/v4/scripts/production_runner_v4f4f.py `
  --mode plan `
  --out-dir fi/results/v4f4f_plan_01
```

Expected: `V4F4F_PLAN_PASS: 400/400 frozen tuples; 16 x 25; ... 0 XSim; FULL400 BLOCKED`.
Source and asset SHA256 pins are calculated and recorded; output folder must
be absent prior to first invocation. Never edit it after creation.

## 3. Transaction rehearsal — 0 XSim

Use a **different** fresh output folder:

```powershell
python fi/v4/scripts/production_runner_v4f4f.py `
  --mode rehearsal `
  --out-dir fi/results/v4f4f_rehearsal_01 `
  --pause-after 137

python fi/v4/scripts/production_runner_v4f4f.py `
  --mode rehearsal `
  --out-dir fi/results/v4f4f_rehearsal_01 `
  --resume
```

Expected first `V4F4F_REHEARSAL_PAUSED: 137/400 synthetic transactions` and
then `V4F4F_REHEARSAL_PASS: 400/400 synthetic production transactions`.
No real XSim/FI is produced.

Read-only audit of the finished rehearsal:

```powershell
python fi/v4/scripts/audit_production_v4f4f.py `
  --mode rehearsal `
  --rehearsal-dir fi/results/v4f4f_rehearsal_01 `
  --out-dir fi/results/v4f4f_rehearsal_audit_01
```

Expected `V4F4F_REHEARSAL_AUDIT_PASS: 400 synthetic receipts; 0 XSim; FULL400 BLOCKED`.
The auditor must reject orphan/missing/reordered receipts and source drift.

## 4. Independent historical eight-attempt ZIP replay — 0 XSim

Use the original two exact archive files (the ZIP SHA256 is pinned to the
2026-11-04 independent review). If you re-compress them, ZIP bytes may differ
and this auditor will **fail by design**.

```powershell
python fi/v4/scripts/audit_production_v4f4f.py `
  --mode history `
  --zip-c v4f4c_g6_evidence_01.zip `
  --zip-de v4f4de_postfi_evidence_01.zip `
  --out-dir fi/results/v4f4f_history_audit_01
```

Expected `V4F4F_HISTORY_AUDIT_PASS: 8/8 attempts, 590/590 raw hashes; 0 XSim; FULL400 BLOCKED`.
Slot 3 and slot 7 remain `NOT_REPRODUCED`; slot 8 PASS, two healthy
attempts produced committed FI results but not a Full400 campaign.

## 5. Vivado-authoritative source preflight (read-only, 0 XSim)

This checks an original `.prj` validated in V3a against actual Vivado project
`.srcs` content, including the exact FI TB and generated `glbl.v`, and
hashes Vivado/XSim binaries. It never calls `xvlog`, `xelab`, `xsim` or Vivado.

```powershell
$Project = 'D:\Share_Data_ubuntu_window\Github\Simulation\Simulation.xpr'
$Vivado = 'D:\vivado_2022.2\Vivado\2022.2\bin\vivado.bat'
if (-not (Test-Path -LiteralPath $Project)) { throw 'Missing Vivado project' }
if (-not (Test-Path -LiteralPath $Vivado)) { throw 'Missing Vivado executable' }

python fi/v4/scripts/production_runner_v4f4f.py `
  --mode source-preflight `
  --project "$Project" `
  --vivado "$Vivado" `
  --out-dir fi/results/v4f4f_source_preflight_01
```

Expected on fully available authoritative Windows sources:
`V4F4F_SOURCE_PREFLIGHT_PASS: Vivado authoritative source + toolchain pinned; 0 XSim ...`.
If `glbl.v` or the exact historical source `.prj` is unavailable, or any hash
changes, it must FAIL. Preserve failure evidence; do NOT copy a different
`glbl.v` or alter locked HDL to force a green marker.

**Important:** source preflight is *not* build-once execution qualification.

## 6. Final release-gap report — always BLOCKED

```powershell
python fi/v4/scripts/audit_production_v4f4f.py `
  --mode release-review `
  --source-dir fi/results/v4f4f_source_preflight_01 `
  --history-dir fi/results/v4f4f_history_audit_01 `
  --rehearsal-audit-dir fi/results/v4f4f_rehearsal_audit_01 `
  --out-dir fi/results/v4f4f_release_review_01
```

Omit `--source-dir` if step 5 was unable to verify exact Vivado source. The
report must still say `G6 NOT QUALIFIED`, `G7 NOT APPROVED`, `FULL400 BLOCKED`.

## 7. Feedback and next phase

Share Windows terminal output and (for independent checking) ZIP of the four
new `fi/results/v4f4f_*` receipt folders. Never delete failed or orphaned
artifacts, never reuse completed output paths, never edit historical results.

**Next real development milestone (after this offline candidate is reviewed):**
versioned executable backend engineering, build-once snapshot and Windows
Job Object integration, strict per-FI log commit, G7 human-approved scientific
protocol, G0/G5 provenance and final independent G0–G7 release signoff.
Phase 4F does NOT implement or approve that real backend.

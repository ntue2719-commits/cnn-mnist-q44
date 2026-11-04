# CNN_PEP V4I-SRC: Read-only authoritative source inventory (CANDIDATE)

**Safety state:** `V4H_EXEC_ADAPTER_WINDOWS_PASS / V4I_SOURCE_INVENTORY_CANDIDATE / G0_PARTIAL / G5_PARTIAL / G6_PRODUCTION_NOT_QUALIFIED / G7_PREPARED_NOT_APPROVED / FULL400_BLOCKED`.

## Why this comes next

The Windows terminal for V4H-EXEC reports 33/33 offline tests PASS, a 400/400 invocation-to-adapter mapping and 3,200 future artifact paths. Existing V4f4F history/source/rehearsal/release receipts passed 15/15 read-only consistency checks. These results are **not a build-once Vivado snapshot**, a live Job Object test or a production Full400 approval.

This additive patch advances G0 **provenance preparation**, not G6 qualification. It reads the **existing** Windows Vivado-authoritative `.xpr`, the archived V3a `source_vlog.prj`, every HDL entry **in compile order**, original frozen FI testbench and exact generated `glbl.v`, all V4f4F pinned input/source assets, and `vivado.bat` / `xvlog.bat` / `xelab.bat` / `xsim.bat` bytes. It re-hashes all of them against the historical V4f4F source-preflight and plan receipts, validates frozen four SHA256 pins, and checks linkage to the current V4f4F release gap plus V4H-EXEC independent audit.

It creates a **new, non-executable inventory manifest**, not a copy of the source files, and not a compiled source/elaboration snapshot. The next G6 engineering work (actual pinned build-once snapshot and Job Object supervision) remains unimplemented.

## Files

- `fi/v4/scripts/production_source_snapshot_v4i.py`: `--mode plan` only. Validates inputs before writing `v4i_source_inventory.json` and `v4i_source_inventory_receipt.json` into a **fresh** `fi/results/v4i_*` directory.
- `fi/v4/scripts/audit_source_snapshot_v4i.py`: `--mode audit` only. A second pass re-hashes current source/tool/data bytes, replays all parent links, checks all manifest fields and canonical UTF-8/LF output, and writes one audit receipt into a **different fresh** directory.
- `fi/v4/scripts/test_source_snapshot_v4i.py`: 41 tests with **mock** Vivado project, `.prj`, HDL, generated `glbl`, 60 dummy pins, tool binaries and receipts. No real Vivado, XSim, real FI or source modifications.

Both CLI entry points reject `--mode build/execute/full400`, `--execute`, and `--approve` before any project IO. No code imports `subprocess` or provides a launcher. The product cannot self-approve G0/G5/G6/G7.

## Windows instructions: one-time additive install

Use the repository root `D:\Share_Data_ubuntu_window\Github\cnn-mnist-q44-rtl`. First inspect the ZIP and **do not overwrite an existing V4I file**:

```powershell
$Zip = '.\cnn_pep_v4i_source_inventory_patch_01.zip'
$Stage = '.\_v4i_source_patch_stage_01'
if (Test-Path -LiteralPath $Stage) { throw 'Staging folder exists' }
Expand-Archive -LiteralPath $Zip -DestinationPath $Stage
$Files = @(
  'fi/v4/scripts/production_source_snapshot_v4i.py',
  'fi/v4/scripts/audit_source_snapshot_v4i.py',
  'fi/v4/scripts/test_source_snapshot_v4i.py',
  'fi/v4/docs/V4I_READONLY_SOURCE_INVENTORY_CANDIDATE.md'
)
foreach ($File in $Files) {
  if (Test-Path -LiteralPath $File) { throw "Refusing overwrite: $File" }
}
foreach ($File in $Files) {
  Copy-Item -LiteralPath (Join-Path $Stage $File) -Destination $File
}
```

Run offline tests:

```powershell
python -m unittest discover -s fi/v4/scripts -p 'test_source_snapshot_v4i.py' -v
if ($LASTEXITCODE -ne 0) { throw 'V4I mock tests failed' }
```

**Do not rerun V4f4F/G rehearsal/blueprint or V4H-EXEC plan/audit just to generate a new PASS.** The following commands use the existing read-only receipts from those stages:

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $Project = 'D:\Share_Data_ubuntu_window\Github\Simulation\Simulation.xpr'
  $Vivado  = 'D:\vivado_2022.2\Vivado\2022.2\bin\vivado.bat'
  $OutPlan  = 'fi/results/v4i_source_snapshot_01'
  $OutAudit = 'fi/results/v4i_source_snapshot_audit_01'
  if (Test-Path -LiteralPath $OutPlan)  { throw "Output exists: $OutPlan" }
  if (Test-Path -LiteralPath $OutAudit) { throw "Output exists: $OutAudit" }

  python fi/v4/scripts/production_source_snapshot_v4i.py `
    --mode plan --project "$Project" --vivado "$Vivado" `
    --out-dir "$OutPlan"
  if ($LASTEXITCODE -ne 0) { throw 'STOP: V4I source inventory failed' }

  python fi/v4/scripts/audit_source_snapshot_v4i.py `
    --mode audit --project "$Project" --vivado "$Vivado" `
    --inventory-dir "$OutPlan" --out-dir "$OutAudit"
  if ($LASTEXITCODE -ne 0) { throw 'STOP: V4I audit failed' }
}
```

The default compile `.prj` is `fi/results/pilot400_v3a_28_01/source_vlog.prj`, the same historic V3a source list referenced in the V4f4F read-only source preflight. The default historical input directories are `v4f4f_{source_preflight,release_review,history_audit,rehearsal_audit,plan}_01` plus `v4h_exec_adapter_{plan,audit}_01`. Change a path argument **only if the canonical evidence is genuinely in another directory**; never repair original receipts or rewrite `.prj` to force PASS.

Expected output marker format on success (counts depend on source/pin inventory):

```text
V4I_SOURCE_INVENTORY_PASS: <N> compiled source entries; <M> V4f4F pins rehashed; 4/4 frozen; 0 build; 0 XSim; FULL400 BLOCKED
V4I_SOURCE_INVENTORY_AUDIT_PASS: <N> compiled sources; <M> V4f4F pins; 0 build; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED
```

If anything fails, **retain evidence**, inspect the actual mismatch and do not run the following step. A fresh attempt requires an unused output suffix; never delete a historical `PASS` or failed output just to reuse the name.

## What this establishes, and what it cannot establish

**Proven only after a successful Windows run:** during these two checks, the source files named by the historical compile `.prj` and all pinned input/tool bytes matched their existing receipts; the compile order and original `glbl.v` were present; frozen inputs and adapter release locks stayed consistent. The source could still change later; production would have to pin and verify again at build/execute boundaries.

**Not established:** executable compiled snapshot, actual build-once `xvlog/xelab`, Windows Job Object process-tree isolation, raw FI commits/recovery, G0 final portable provenance archive, G5 production scientific result contract, G6 sign-off, two-person G7 approval, Full400 execution, or any new RQ-A/RQ-B numerical result. The eight historical bounded canary attempt slots remain exhausted (8/8).

This auditor uses a **second read-only hash/reconstruction pass** with validation rules shared with the candidate. It is not a substitute for a separate auditor implementation and signed G0/G6 final review. The local mock suite is not evidence from the Windows Vivado toolchain.

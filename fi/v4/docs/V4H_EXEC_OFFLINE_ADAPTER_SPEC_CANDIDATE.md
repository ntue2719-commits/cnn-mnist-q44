# CNN_PEP V4H-EXEC - Offline Production Adapter Contract (CANDIDATE)

**Release status:** `G0_PARTIAL / G5_PARTIAL / G6_PRODUCTION_NOT_QUALIFIED / G7_PREPARED_NOT_APPROVED / FULL400_BLOCKED`.

**Scope:** This patch adds **only** an offline, independently audited adapter-contract representation for the already frozen V4f4F/G 400-scenario plan. It does not create a compiled Vivado snapshot, build binaries, start Job Objects, run XSim, observe new hardware outcomes, alter RTL, or authorize Full400.

## Source of authority and provenance

- Frozen `fi/scenarios/pilot400.csv`, `fi/config/{fi_targets_candidates_v2,pilot400_targets}.csv`, original `fi/tb/tb_cnn_mnist_fi_pilot400.v` and their four historical SHA256 pins remain unchanged.
- The input is the **existing**, canonical, verified V4f4F plan (`v4f4f_plan_01`), V4f4G byte-exact blueprint (`v4f4g_blueprint_winfix_01`) and its auditor (`v4f4g_blueprint_audit_winfix_01`). The source preflight/history/release receipts from V4f4F have separately passed the user's 15/15 read-only consistency checks; they are **not** treated as execution approval.
- The candidate imports V4f4F/G validators at run time to revalidate the 400 frozen tuples, pinned repo assets, source generator SHA, 10 exact plusargs per scenario, parent plan SHA and blueprint canonical bytes. The new auditor then independently checks its own 400 mapped adapter records, 3,200 future evidence paths, canonical UTF-8/LF and SHA bindings. **It does not replace the upstream raw XSim replay.**
- Historical 8/8 bounded failure attempts are exhausted; slots 3 and 7 remain `NOT_REPRODUCED`. Never create slot 9 from this patch.

## Candidate interface

The two new files are:

- `fi/v4/scripts/production_adapter_v4h_exec.py` - `--mode plan` **only**, outputs a non-executable `v4h_exec_adapter_400.json` and `v4h_exec_receipt.json` under a fresh `fi/results/v4h_exec_*` directory.
- `fi/v4/scripts/audit_production_adapter_v4h_exec.py` - `--mode audit` **only**, validates 400 entries and 3,200 paths before writing a separate audit receipt to a fresh `fi/results/v4h_exec_*` directory.

Both reject `--mode execute`, `--mode build`, `--mode full400`, `--execute`, and `--approve` **before any filesystem lookup or output creation**. Neither module imports `subprocess` nor provides a Vivado/XSim launcher.

An adapter record specifies the immutable scenario ID, batch, tuple hash, exact plusargs data digest, future run and artifact digests, and explicitly false flags for `ready_to_execute`, `source_snapshot_built`, `windows_job_object_started`, `raw_xsim_log_observed`, `checkpoint_committed`, and `design_outcome_observed`. This is **planning data only**, never a process receipt.

## Safe Windows commands (0 Vivado/XSim)

Run at repo root `D:\Share_Data_ubuntu_window\Github\cnn-mnist-q44-rtl` after inspecting the patch ZIP. **Do not overwrite any existing V4H science script or output.** Only new filenames are included.

```powershell
# Verify both V4H-EXEC result directories are absent before first run.
if (Test-Path -LiteralPath fi/results/v4h_exec_adapter_plan_01) { throw 'Plan output exists' }
if (Test-Path -LiteralPath fi/results/v4h_exec_adapter_audit_01) { throw 'Audit output exists' }

python -m unittest discover -s fi/v4/scripts -p 'test_*v4h_exec.py' -v
if ($LASTEXITCODE -ne 0) { throw 'V4H-EXEC offline unit tests failed' }

python fi/v4/scripts/production_adapter_v4h_exec.py `
  --mode plan `
  --plan-dir fi/results/v4f4f_plan_01 `
  --blueprint-dir fi/results/v4f4g_blueprint_winfix_01 `
  --blueprint-audit-dir fi/results/v4f4g_blueprint_audit_winfix_01 `
  --out-dir fi/results/v4h_exec_adapter_plan_01
if ($LASTEXITCODE -ne 0) { throw 'STOP: V4H-EXEC adapter mapping failed' }

python fi/v4/scripts/audit_production_adapter_v4h_exec.py `
  --mode audit `
  --plan-dir fi/results/v4f4f_plan_01 `
  --blueprint-dir fi/results/v4f4g_blueprint_winfix_01 `
  --blueprint-audit-dir fi/results/v4f4g_blueprint_audit_winfix_01 `
  --adapter-dir fi/results/v4h_exec_adapter_plan_01 `
  --out-dir fi/results/v4h_exec_adapter_audit_01
if ($LASTEXITCODE -ne 0) { throw 'STOP: V4H-EXEC independent audit failed' }
```

If any command fails, preserve the partial directory and report the exact error. **Do not** remove/reuse the same directory or patch the frozen inputs to force PASS. A new attempt requires a new unused result suffix and a separately recorded cause.

## Static acceptance / negative tests

- Exactly 400 canonical ordinal IDs (`FI_P400_0001` through `_0400`) in 16 batches of 25, requiring exactly 10 plusarg names and 3,200 unique relative future artifact locations.
- Parent plan, schedule, blueprint, blueprint receipt, blueprint audit and generator source SHA256 pinned in the new record. Changed upstream source/inputs trigger refusal through V4f4F/G validators.
- Binary canonical JSON with exact LF bytes; duplicate keys rejected; `.tmp` leftovers and existing files rejected; out-of-namespace, path traversal, and symlink output targets rejected.
- Forged FI output, runtime build, Job Object, checkpoint, or authorization flags rejected.
- Infrastructure timeout is never presented as RTL `NONCOMPLETION`. E3 consumer proof and L2 checkpoint semantics remain separate research workstreams.
- The test suite includes pure negative fixtures and an end-to-end **mock upstream** plan/audit pair. Test output is **not new XSim**, source-preflight proof, or G6 qualification.

## What remains genuinely unimplemented

1. A real **build-once pinned snapshot**, exact `.prj` compile order and generated `glbl.v` archive with executable hashes, source drift checks pre/post build. V4f4F Windows source preflight is read-only; it is not a build-once snapshot.
2. A private Windows **Job Object process supervisor** for xvlog/xelab/xsim with owned process-tree cleanup and strict per-run timeouts; no process launch was tested here.
3. Validated raw FI log parsing, atomic evidence commit, blocked retry/recovery state machine, and full independent raw audit for a production executor. These are future engineering tasks, not claimed by mapped future output paths.
4. G0/G5 remaining provenance, G6 independent final safety qualification, real two-person G7 scientific approval, and a **distinct signed Full400 authorization**. These cannot be generated automatically by this patch.
5. L2 stage checkpoint raw evidence and later 400 directed FI scientific measurements, if/when expressly authorized. 400 synthetic receipts and 400 static invocations are not 400 FI outcomes.

**Local development verification:** 33 mock/pure unit tests PASS with 0 skipped; separately, the raw V4f4G Windows blueprint was used for a pure 400-record mapping smoke. **The integrated V4f4F/G source-pin checks still need to be run on the user's authoritative Windows repository.**

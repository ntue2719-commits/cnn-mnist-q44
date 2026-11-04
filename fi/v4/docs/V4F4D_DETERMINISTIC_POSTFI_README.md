# CNN_PEP V4f Phase 4D — Deterministic post-FI interruption candidate

**Status:** Python/code qualification candidate; **Vivado 2022.2 XSim not yet tested for this Tcl flow**. **G6 NOT QUALIFIED. G7 PREPARED_NOT_APPROVED. FULL400 BLOCKED.**

## Why a separate attempt?

The prior V4f4C `terminate-after-fi` on `FI_P400_0167` completed normally (`return_code=0`, `FI_APPLIED=1`, `FI_RESULT_V2=1`, `termination_requested=False`). Polling the native log every 25 ms cannot guarantee catching the narrow interval. We do not replace or rewrite that evidence, rerun the same attempt directory, change P0, or claim PASS retrospectively.

## Candidate mechanism

1. Reuse the unmodified V4f4C Vivado-authoritative pinned `.prj`, fresh `xvlog`/`xelab`, and private Windows Kill-on-Close Job Objects.
2. Run the frozen `FI_P400_0167` canary (BUF_0031, IMG0003, bit 0, offset 1049) **once** in a fresh attempt.
3. Via XSim Tcl `add_condition`, detect the frozen TB's `/tb_cnn_mnist_fi_pilot400/fi_injection_ok == 1`, call `stop`; continue exactly 1 ns after removing the condition so the existing `$display("FI_APPLIED ...")` has executed; verify `get_value -radix bin ... == 1`.
4. Write an on-disk Tcl-owned pause receipt and **hold the Tcl event loop without advancing simulation time**.
5. Python observes the receipt, waits (up to 5 s) for the native `FI_APPLIED` log while XSim is paused, then calls `TerminateJobObject` on ONLY the private attempt group. It never uses global `taskkill /IM`.
6. A *qualified* probe requires all of: exactly one raw `FI_APPLIED`, no `FI_RESULT_V2`, valid pause receipt, positive termination request, no timeout, child started, zero owned live processes, 0 committed outcomes. Anything else is `POSTFI_TCL_PAUSE_NOT_REPRODUCED` or a fail-closed execution error. Missing flush, hidden Tcl signal, unsupported `add_condition`, or early XSim finish **must not** be silently treated as success.
7. Separate `audit_postfi_v4f4d.py` validates SHA256/source pins/Verilog frozen schedule/plusargs/XSim artifacts/process receipts/pause Tcl, creates a read-only receipt and never starts XSim.

**Evidence type:** intentional infrastructure interruption after verified injection; even if the raw FI marker is present, **this attempt is NOT a valid scientific FI outcome**, does not count in Full400 denominator, and does not qualify Full400 automatically.

## Strict attempt budget

The *new* attempt directory is deliberately named `fi/results/v4f4c_postfi_det_01`, not `v4f4d_attempt_...`. The untouched V4f4C aggregate budget counter therefore sees its `attempt.json`, preserving the 8-slot hard limit. Six V4f4C attempts must exist beforehand; this is slot **7 of 8**, with one left. Do not execute Phase 4C and Phase 4D concurrently. All runs take the same global lock.

## Windows commands (PowerShell, repo root)

Unzip into a temporary directory first; confirm no existing paths get overwritten. Copy only `fi/v4/scripts/qualify_postfi_v4f4d.py`, `fi/v4/scripts/audit_postfi_v4f4d.py`, `fi/v4/scripts/test_postfi_v4f4d.py` and this README.

```powershell
python -m unittest discover -s fi/v4/scripts -p 'test_postfi_v4f4d.py' -v

python fi/v4/scripts/qualify_postfi_v4f4d.py `
  --mode plan `
  --out-dir fi/results/v4f4d_plan_01
```

**Stop here until reviewing the Tcl candidate**: open `fi/results/v4f4d_plan_01/candidate_postfi.tcl` and inspect the signal path and `add_condition` syntax using Vivado 2022.2 documentation/interactive Tcl on the actual machine. The plan does not consume an XSim slot. If the Tcl feature/signal hierarchy is not accessible, do not use the last two slots.

After Python tests and Tcl preflight are accepted and the real output path does NOT already exist:

```powershell
$Project = 'D:\Share_Data_ubuntu_window\Github\Simulation\Simulation.xpr'
$Vivado = 'D:\vivado_2022.2\Vivado\2022.2\bin\vivado.bat'
Test-Path -LiteralPath $Project
Test-Path -LiteralPath $Vivado
Test-Path 'fi/results/v4f4c_postfi_det_01'  # MUST be False

python fi/v4/scripts/qualify_postfi_v4f4d.py `
  --mode execute --execute `
  --project "$Project" --vivado "$Vivado" `
  --timeout-seconds 900 `
  --out-dir fi/results/v4f4c_postfi_det_01
```

Regardless of outcome (and only when OS child processes have finished), use **fresh** audit dir:

```powershell
python fi/v4/scripts/audit_postfi_v4f4d.py `
  --attempt-dir fi/results/v4f4c_postfi_det_01 `
  --out-dir fi/results/v4f4d_postfi_audit_01
```

Expected *if actually reproduced*: `V4F4D_POSTFI_PROBE: INTERRUPTED_AFTER_FI_NO_RESULT; 0 committed FI; FULL400 BLOCKED` and `V4F4D_AUDIT_POSTFI_INTERRUPT_PASS`. These markers are **expectations, not observed results**.

## What this does NOT prove

- No live XSim execution evidence exists for V4f4D until run on Windows.
- Does not prove mid-power-loss recovery, consumer E3 for these samples, production 400 runner safety, any G0–G7 release or scientific G7 sign-off.
- Even after success, require aggregate evidence review, G0/G5/G6 production runner qualification, G7 human review, and independent release authorization.

## Official reference

AMD/Xilinx Vivado UG835 (2022.2), `add_condition`, `stop`, `remove_conditions`, `get_value`, and UG900 (2022.1) simulation Tcl controls. Confirm exact behavior on Vivado 2022.2 before use.

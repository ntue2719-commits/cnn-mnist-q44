# CNN_PEP V4f4E: versioned Windows Tcl LF receipt fix (G6 bounded qualification)

## Root cause (from user's Windows slot-7 evidence)

The frozen FI testbench's Tcl condition fired: the XSim log contains
`V4F4D_CONDITION_FI_DETECTED`, `FI_APPLIED`, `V4F4D_TCL_PAUSE_READY`, and
`vwait ::v4f4d_external_release_never`; no `FI_RESULT_V2` was observed.
The exact Tcl receipt bytes on Windows ended each line in **CRLF (`0D 0A`)**,
while the original strict Python validator required **LF (`0A`)**. The recorded
failure was `POSTFI_TCL_PAUSE_NOT_REPRODUCED`, `postfi_kill_cause=INVALID_ACK`,
with zero FI outcome commits and zero owned processes remaining.

This is evidence of a failed handshake validation, **not permission** to
retroactively promote slot 7 to PASS.

## Patch scope / invariants

New files **only**:

- `fi/v4/scripts/qualify_postfi_lf_v4f4e.py`
- `fi/v4/scripts/audit_postfi_lf_v4f4e.py`
- `fi/v4/scripts/test_postfi_lf_v4f4e.py`
- `fi/v4/docs/V4F4E_POSTFI_LF_README.md`

The original `qualify_postfi_v4f4d.py`, `audit_postfi_v4f4d.py`, historical
attempt 7, and every P0/frozen RTL/TB/manifest, FI log and evidence are
**unchanged**. This is necessary because the original auditor reconstructs the
old Tcl bytes from the historical source.

The only intentional runtime Tcl change is immediately after `open`:

```tcl
fconfigure $::v4f4d_f -translation lf -encoding ascii
```

The pause receipt remains a strict, exact-byte LF file. The protocol still
requires exactly one `FI_APPLIED`, zero `FI_RESULT_V2`, valid Tcl receipt,
Windows private Job Object termination, zero survivors, no committed FI
outcome and a read-only independent audit.

Global Phase 4C budget remains **eight attempt slots**. Seven have already
been consumed. The new runner reserves **the eighth and final slot** at
`fi/results/v4f4c_postfi_det_02`; no automatic retrial or new budget.
The seven historical attempts must be present, and attempt 7 must retain its
recorded CRLF/INVALID_ACK failure classification. On any missing/corrupted
receipt the new runner refuses to execute.

**Full400 remains BLOCKED. G6 not qualified. G7 not approved.**

## Windows: safe preflight, no XSim

Install ONLY the four NEW files from this ZIP into the repository. Do not
extract blindly over existing files; do not overwrite old V4f4D scripts.

```powershell
python -m unittest discover `
  -s fi/v4/scripts -p "test_postfi_lf_v4f4e.py" -v

Test-Path "fi/results/v4f4e_plan_01"
# Only if False:
python fi/v4/scripts/qualify_postfi_lf_v4f4e.py `
  --mode plan `
  --out-dir fi/results/v4f4e_plan_01

Select-String `
  -Path "fi/results/v4f4e_plan_01/candidate_postfi.tcl" `
  -Pattern "fconfigure", "add_condition", "get_value", "vwait"
```

Expected candidate: `fconfigure $::v4f4d_f -translation lf -encoding ascii`
BEFORE the first `puts $::v4f4d_f`.

## NOT YET: one last real XSim attempt

Do not consume slot 8 without reviewing the new source, running tests,
checking the historical attempt-7 evidence and ensuring no extra
`v4f4c_*` attempt exists. The one-shot command, if separately authorized after
preflight, is:

```powershell
$Project = "D:\Share_Data_ubuntu_window\Github\Simulation\Simulation.xpr"
$Vivado  = "D:\vivado_2022.2\Vivado\2022.2\bin\vivado.bat"

# All checks must pass; specifically attempt output MUST NOT exist:
Test-Path "fi/results/v4f4c_postfi_det_02"  # must be False

python fi/v4/scripts/qualify_postfi_lf_v4f4e.py `
  --mode execute `
  --scenario-id FI_P400_0167 `
  --project "$Project" --vivado "$Vivado" `
  --timeout-seconds 900 `
  --out-dir fi/results/v4f4c_postfi_det_02 `
  --execute
```

No retry in the same output directory. Do not modify any raw artifacts.
If execution has finished and all attempt-owned processes are gone, audit to
another fresh directory:

```powershell
python fi/v4/scripts/audit_postfi_lf_v4f4e.py `
  --attempt-dir fi/results/v4f4c_postfi_det_02 `
  --out-dir fi/results/v4f4e_postfi_audit_01
```

Expected PASS **only if raw evidence supports it**:
`V4F4E_POSTFI_PROBE: INTERRUPTED_AFTER_FI_NO_RESULT` and
`V4F4E_AUDIT_POSTFI_INTERRUPT_PASS`. If either is NOT REPRODUCED, preserve
all bytes, record the gap and DO NOT consume any further attempt slot.

The first command to run is the **unit tests + plan only**.

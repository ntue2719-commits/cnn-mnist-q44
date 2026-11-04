# CNN_PEP V4K - Windows Job Object mock safety probe (candidate)

## Scientific and execution scope

V4J captured and audited 28 compiled source entries, 71 V4f4F input pins, and 97 unique source blobs on the user's Windows machine. V4K begins **Windows process-lifecycle engineering** without touching the frozen RTL, Vivado installation, compiled snapshot, XSim, FI TB, or historical eight attempts.

V4K is **not a production FI executor**. It runs three fixed Python-only Job Object cases and records OS-reported lifecycle counts. The only permitted entrypoint is `--mode probe`. It never uses a user-provided executable, command line, script, PID, design, XSim plusargs, or FI scenario. It has no build/execute/Full400 code path. This mock probe is a new infrastructure experiment, **not** a ninth real-XSim qualification attempt.

### Fixed benign OS cases

| Case | Behavior | Acceptance (Windows API receipts) |
| --- | --- | --- |
| `healthy` | One fixed child Python process exits 0 | Wait completes, exit=0, ActiveProcesses=0; no termination needed |
| `timeout` | Fixed child sleeps; bounded supervisor wait expires | ActiveProcesses >=1 before TerminateJobObject; post-terminate ActiveProcesses=0, process exit=87; categorized **INFRASTRUCTURE**, not RTL noncompletion |
| `tree` | Fixed parent spawns a grandchild that writes a bounded handshake and sleeps | ActiveProcesses >=2 before TerminateJobObject, exact parent+child receipt bytes, ActiveProcesses=0, process exit=87; tests inherited Job Object containment |

The process supervisor uses `CreateProcessW` with `CREATE_SUSPENDED | CREATE_NO_WINDOW`, assigns the new Python child to a private `CreateJobObjectW` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and only then calls `ResumeThread`. Each case has its **own** private Job Object. No unrelated process is targeted. If assignment fails, the supervisor attempts to terminate its unassigned suspended child directly; the original attempt is not treated as a PASS.

**Windows OS measurements cannot be independently reproduced on Linux.** Local unit tests exercise pure guards, mocked canonical receipts and deliberate tampering. The actual Win32 API calls and process-tree cleanup need to be exercised on the user's Windows host. Recorded active-process counters are OS-observed at probe time; the read-only auditor only replays and hashes those receipts, it does not independently observe the OS.

## Output and crash safety

A fresh `fi/results/v4k_mockjob_*` direct sibling is mandatory. The tool creates `.v4k_mockjob_<suffix>.inflight` first. If the mock test fails or Windows interrupts the supervisor, preserve the inflight directory and **do not rerun to the same output**. Only after all 3 cases pass does it write canonical UTF-8/LF JSON receipts and atomically rename the stage to the final directory. No input or previous result is overwritten.

Successful output directory contains exactly:
- `v4k_mock_job_probe.json`
- `v4k_mock_job_receipt.json`
- `parent_ready.txt`
- `grandchild_ready.txt`

The independent auditor creates a separate fresh `fi/results/v4k_mockjob_audit_*` and validates source hashes, exact canonical JSON, strict field sets, 3 case states, unique positive PIDs, 2 handshake byte sequences and the recorded exit/count semantics. Missing/corrupt/duplicate JSON keys, unexpected files, modified source, fabricated approval, FI outcome, incorrect infrastructure flags, nonzero remaining active processes, and invalid SHA256 are rejected. It is a **receipt consistency** check, not independent OS-attestation.

## Install on Windows - additive only

From the repo root, put `cnn_pep_v4k_mock_windows_job_patch_01.zip` in the working directory. Verify its SHA256 against the value supplied alongside the archive. Do not extract into the repo directly without checking collisions.

```powershell
$Zip = '.\cnn_pep_v4k_mock_windows_job_patch_01.zip'
$Stage = '.\_v4k_mockjob_patch_stage_01'
if (Test-Path -LiteralPath $Stage) { throw 'Staging directory already exists' }
Expand-Archive -LiteralPath $Zip -DestinationPath $Stage
$Files = @(
  'fi/v4/scripts/mock_job_child_v4k.py',
  'fi/v4/scripts/mock_windows_job_v4k.py',
  'fi/v4/scripts/audit_mock_windows_job_v4k.py',
  'fi/v4/scripts/test_mock_windows_job_v4k.py',
  'fi/v4/docs/V4K_WINDOWS_JOB_OBJECT_MOCK_PROBE_CANDIDATE.md'
)
foreach ($F in $Files) {
  if (Test-Path -LiteralPath $F) { throw "Would overwrite: $F" }
  $Manifest = Get-Content -LiteralPath (Join-Path $Stage 'MANIFEST.json') -Raw | ConvertFrom-Json
  $Rec = $Manifest.files | Where-Object { $_.path -eq $F }
  if (@($Rec).Count -ne 1) { throw "Missing manifest entry: $F" }
  if ((Get-FileHash -LiteralPath (Join-Path $Stage $F) -Algorithm SHA256).Hash -ne $Rec.sha256) {
    throw "File SHA mismatch: $F"
  }
}
foreach ($F in $Files) { Copy-Item -LiteralPath (Join-Path $Stage $F) -Destination $F }
```

### Step 1 - Pure tests: no mock Job Object is created

```powershell
python -m unittest discover `
  -s fi/v4/scripts `
  -p 'test_mock_windows_job_v4k.py' `
  -v
if ($LASTEXITCODE -ne 0) { throw 'V4K unit tests failed' }
```

One symlink-specific test can SKIP if Windows disallows symlink creation. The Win32 API import smoke test runs on Windows; local Linux unit tests do not exercise Win32 APIs.

### Step 2 - Windows-only benign subprocess probe (explicit opt-in, 0 XSim)

```powershell
$Probe = 'fi/results/v4k_mockjob_01'
$Audit = 'fi/results/v4k_mockjob_audit_01'
$Inflight = 'fi/results/.v4k_mockjob_01.inflight'
foreach ($P in @($Probe,$Audit,$Inflight)) {
  if (Test-Path -LiteralPath $P) { throw "Existing evidence, preserve: $P" }
}
python fi/v4/scripts/mock_windows_job_v4k.py `
  --mode probe --out-dir $Probe
if ($LASTEXITCODE -ne 0) { throw 'Stop - V4K Job Object mock probe blocked' }

python fi/v4/scripts/audit_mock_windows_job_v4k.py `
  --mode audit --probe-dir $Probe --out-dir $Audit
if ($LASTEXITCODE -ne 0) { throw 'Stop - V4K mock audit blocked' }
```

Expected successful stdout:

```text
V4K_WINDOWS_JOB_MOCK_PROBE_PASS: 3/3 fixed Python mock cases; healthy, infrastructure timeout, descendant cleanup; 0 Vivado build; 0 XSim; FULL400 BLOCKED
V4K_WINDOWS_JOB_MOCK_AUDIT_PASS: 3/3 mock lifecycle receipts; 0 build; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED
```

Failures must remain visible. **Do not bypass job assignment**, kill unrelated PIDs, edit/process old evidence, or use an XSim runner as a substitute for this benign probe. On Windows environments with restrictive parent job assignments, a clean refusal is a valid unqualified outcome; never claim PASS.

## Release gates / what is still missing

- G0: preserve the V4J 97-blob original bundle + exact external SHA record.
- G5: still need production strict `FI_APPLIED`/`FI_RESULT_V2` validation, intermediate checkpoint decisions and clear missing/invalid/inconclusive denominators.
- G6: only test-only Job Object behavior; actual build-once Vivado snapshot, bounded XSim backend, atomic raw commit/resume and independent production qualification remain **NOT IMPLEMENTED/NOT QUALIFIED**.
- G7: human scientific protocol operator/reviewer attestations, seal/verify and independent release are **NOT APPROVED**.
- Historical 8/8 bounded XSim attempts remain exhausted, with 2 valid committed outcomes, slots 3/7 NOT_REPRODUCED.
- FULL400 remains BLOCKED/NOT EXECUTED. No numeric fault-propagation result is added by V4K.

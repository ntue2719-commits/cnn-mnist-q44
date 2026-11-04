# CNN_PEP V4L — Offline Atomic Mock Journal (CANDIDATE)

**Version:** 2026-11-04 / V4L-mocktxn-v1
**Status:** ENGINEERING CANDIDATE; no real FI, no Vivado build, no XSim, no Full400 authorization.

## Purpose and scientific interpretation

Continue from V4f4F/G offline contracts, the V4H-EXEC 400-record adapter, V4I/V4J source pinning, and V4K's separately verified Windows Python Job Object mock probe. V4L tests **transaction-state bookkeeping** with four *fixed synthetic* attempts. The tool neither invokes nor wraps the V4K process supervisor and does not simulate real RTL bit flips.

**Do not claim:** 400 actual FI results, physical SEU tolerance, a real XSim commit, process crash recovery, durable power-loss recovery, a real build-once snapshot, or G6 production qualification. `MOCK_COMMITTED` is a journal state and **does not mean a valid FI outcome**. The user has already exhausted the historical **8/8 bounded real-XSim failure-safety attempt slots**; V4L does not allocate a ninth attempt.

## Input source and boundary

The producer requires these already-existing **read-only** Windows evidence folders:

- `fi/results/v4h_exec_adapter_plan_01/`: `v4h_exec_adapter_400.json`, `v4h_exec_receipt.json`
- `fi/results/v4h_exec_adapter_audit_01/`: `v4h_exec_independent_audit.json`

It validates source schema, canonical UTF-8/LF bytes, all 400 scenario IDs in exact order, 16x25 batch positions, tuple digest shape, frozen `UNCREATED_CONTRACT_ONLY`, negative execution/approval flags and SHA256 references between the V4H adapter and its audit. The V4H independent auditor remains the authority for deeper raw-blueprint/plusargs and 3,200 output-path equivalence; V4L is **not** an additional independent raw V4f4G replay. External pinning of source ZIP digests is required for authenticity; internal cross-hashes alone do not provide cryptographic signing.

V4L accepts **no user-supplied executable, FI plusargs, simulator argument, PID, fault value or outcome**. It has no calls to `subprocess`, `ctypes`, Vivado or XSim. The only producer mode is `--mode probe`. `--execute`, `--approve`, `build`, `release`, and `full400` refuse before creating output.

## Four fixed, mock-only transactions

| Attempt | Frozen scenario | Case | Last recorded state | Count as real FI? |
|---|---|---|---|---|
| 1 | `FI_P400_0001` | Healthy mock | `MOCK_COMMITTED` | No |
| 2 | `FI_P400_0002` | Interrupted after mock validation, before commit | `VALIDATED_MOCK_ONLY` | No |
| 3 | `FI_P400_0003` | Mock infrastructure timeout | `INVALID_INFRASTRUCTURE` | No |
| 4 | `FI_P400_0003` | Fresh attempt following attempt 3, linked by SHA256 | `MOCK_COMMITTED` | No |

Attempts 3 and 4 deliberately share a frozen *scenario* but have separate immutable *attempt IDs*. This is not an accidental duplicate execution and does not create an extra scenario outcome. The recovery is a **scripted new mock attempt**; it is not a live crash/restart protocol. Attempt 4 points to attempt 3's immutable final `INVALID_INFRASTRUCTURE` event SHA256. No retry edits attempt 3.

**State transition model:**

```text
PENDING -> INFLIGHT -> VALIDATED_MOCK_ONLY -> MOCK_COMMITTED
                               \-> stop without commit (mock interruption)
PENDING -> INFLIGHT -> INVALID_INFRASTRUCTURE (mock timeout)
```

`raw_mock.json` is a canonical synthetic token, with `fi_applied=false`, `xsim_executed=false`, `classification_available=false`. It is **not** an `FI_APPLIED` or `FI_RESULT_V2` log. A validated mock is not committed until an independent `state_03_mock_committed.json` event is present and hash-verified. An infrastructure timeout must never become RTL `NONCOMPLETION`.

## Atomic and append-only file semantics

- Fresh output names must be direct `fi/results/v4l_mocktxn_*` siblings; existing output and `.v4l_mocktxn_*.inflight` are preserved and refused. No symlinks, junctions, or traversal are permitted.
- A fresh `.inflight` directory is created with exclusive `mkdir` to separate incomplete output from committed bundle. Every event is written with exclusive creation and `fsync` at file level; the final mock-commit event uses a temporary file and atomic rename. A root manifest and receipt are written and the *whole directory* is renamed from `.inflight` to its fresh final name.
- This provides a test model of **single-writer, no-overwrite, file-level atomic visibility**. It does **not** demonstrate filesystem crash durability across power-loss, multiprocess arbitrary adversaries, Windows sharing violations, or all disk error windows. Directory metadata fsync and real process interruption are not part of the tested contract.
- If the mock probe fails after creating `.inflight`, preserve the directory; **do not delete it, `--resume` it, salvage a mock outcome, or reuse the same output name**. A new separately named attempt and independent investigation are required.
- The independent auditor recomputes, **without importing the producer**, the exact intent/raw/state payloads; exact event-chain SHA256; timeout semantics; missing/extra files; immutable parent retry SHA; manifest/receipt/source-code hashes; and source adapter pins. It rejects tampering, counterfeit FI counts, fake release flags and orphan files. The auditor only writes its own new `fi/results/v4l_mocktxn_audit_*` directory after successful verification.

## Windows install — additive, do not overwrite

Put the ZIP in repo root and verify the ZIP SHA256 supplied with the download. Inspect `MANIFEST.json` and `SHA256SUMS.txt`. Extraction to a fresh staging folder is recommended:

```powershell
$Zip = '.\cnn_pep_v4l_mock_atomic_patch_01.zip'
$Stage = '.\_v4l_mock_atomic_patch_stage_01'
if (Test-Path -LiteralPath $Stage) { throw 'Existing stage; refuse overwrite' }
Expand-Archive -LiteralPath $Zip -DestinationPath $Stage
$M = Get-Content -LiteralPath (Join-Path $Stage 'MANIFEST.json') -Raw | ConvertFrom-Json
foreach ($F in $M.files) {
    if (Test-Path -LiteralPath $F.path) { throw "Would overwrite: $($F.path)" }
    $Actual = (Get-FileHash -LiteralPath (Join-Path $Stage $F.path) -Algorithm SHA256).Hash
    if ($Actual -ne $F.sha256) { throw "Patch file SHA mismatch: $($F.path)" }
}
foreach ($F in $M.files) {
    Copy-Item -LiteralPath (Join-Path $Stage $F.path) -Destination $F.path
}
```

The package carries **three** `.py` files and **two** Markdown docs. Its `MANIFEST.json` lists only those files. The `SHA256SUMS.txt` is a text inventory, not an executable.

### 1. Unit tests (mock, cross-platform, zero XSim)

```powershell
python -m unittest discover `
  -s fi/v4/scripts `
  -p 'test_mock_atomic_v4l.py' `
  -v
if ($LASTEXITCODE -ne 0) { throw 'STOP: V4L unit tests failed' }
```

55 tests are discovered in the initial candidate. On Windows, tests requiring symlink creation may SKIP due OS permissions; report the exact `Ran ... / OK (skipped=...)` result, not `55/55 executed` if skipped.

### 2. Mock probe and independent audit (no Vivado, no FI)

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $Probe = 'fi/results/v4l_mocktxn_01'
    $Audit = 'fi/results/v4l_mocktxn_audit_01'
    $Inflight = 'fi/results/.v4l_mocktxn_01.inflight'
    foreach ($P in @($Probe,$Audit,$Inflight)) {
        if (Test-Path -LiteralPath $P) { throw "Evidence already exists; preserve: $P" }
    }
    python fi/v4/scripts/mock_atomic_ledger_v4l.py `
      --mode probe `
      --adapter-dir fi/results/v4h_exec_adapter_plan_01 `
      --adapter-audit-dir fi/results/v4h_exec_adapter_audit_01 `
      --out-dir $Probe
    if ($LASTEXITCODE -ne 0) { throw 'STOP: V4L mock probe failed' }

    python fi/v4/scripts/audit_mock_atomic_v4l.py `
      --mode audit `
      --adapter-dir fi/results/v4h_exec_adapter_plan_01 `
      --adapter-audit-dir fi/results/v4h_exec_adapter_audit_01 `
      --probe-dir $Probe `
      --out-dir $Audit
    if ($LASTEXITCODE -ne 0) { throw 'STOP: V4L independent audit failed' }
}
```

Expected markers:

```text
V4L_MOCK_ATOMIC_PROBE_PASS: 4 mock attempts; 2 mock committed; 1 interrupted; 1 infrastructure timeout; 0 build; 0 XSim; FULL400 BLOCKED
V4L_MOCK_ATOMIC_AUDIT_PASS: 4/4 attempt journals; 2 MOCK commits; 1 interrupted; 1 infrastructure invalid; 0 XSim; FULL400 BLOCKED
```

## G0/G5/G6/G7 scientific and safety gates

- **G0:** V4I/V4J authoritative source pins remain historical source snapshots; V4L source references must match the pinned V4H adapter and independent audit. No new authoritative `.prj` is created.
- **G5:** Mock states never enter the FI numerator or denominator; no real design watchdog or staged numerical outcome is implied.
- **G6:** The transaction journal is an **offline model**, not a real production XSim executor nor a real process-crash/power-loss qualification. V4K's Windows Job Object evidence is a separate mock-only scoped test. **G6 NOT QUALIFIED**.
- **G7:** Scientific protocol remains PREPARED_NOT_APPROVED. No AI/operator authorization is produced. **FULL400 BLOCKED**.

## Next phase after a Windows PASS

1. Independent review of V4L artifact bytes and negative test scope; record the SHA256 of final bundle externally.
2. For real production integration, design an independently approved, versioned build-once XSim adapter, real-process interruption tests under a new approved budget, and strict `FI_APPLIED`/`FI_RESULT_V2` stage-aware validator. **Do not activate from V4L mock PASS.**
3. In parallel, close the scientific G7-L2 *interpretation* on existing raw checkpoints. For the 40 IMG0000 scenarios, L0 TRUE=9, L1 TRUE=20; L2 TRUE=20 **only inherited from L1**, and remaining 20 L2 INCONCLUSIVE pending genuinely validated POOL1/POOL2 checkpoints. Do not turn missing observations into MATCH.

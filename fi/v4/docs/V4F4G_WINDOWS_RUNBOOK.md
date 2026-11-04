# CNN_PEP V4f4G — Windows runbook (0 XSim)

**Precondition:** V4f4F `plan`/`rehearsal`/`rehearsal-audit` have PASS on
Windows. The 100 V4f4F unit tests passed (5 skipped) in 186.805 seconds.
No reason to repeat long synthetic tests just to move to the next gate.

## 1 — Finish remaining V4f4F independent evidence checks

Copy the **original** exact `v4f4c_g6_evidence_01.zip` and
`v4f4de_postfi_evidence_01.zip` to your repo root, if they are not already
there. Re-compressed archives may differ in bytes and must fail.

```powershell
python fi/v4/scripts/audit_production_v4f4f.py `
  --mode history `
  --zip-c v4f4c_g6_evidence_01.zip `
  --zip-de v4f4de_postfi_evidence_01.zip `
  --out-dir fi/results/v4f4f_history_audit_01
```

Then source preflight **read-only**, using actual Vivado sources:

```powershell
$Project = 'D:\Share_Data_ubuntu_window\Github\Simulation\Simulation.xpr'
$Vivado  = 'D:\vivado_2022.2\Vivado\2022.2\bin\vivado.bat'
if (!(Test-Path -LiteralPath $Project)) { throw 'Missing Vivado .xpr' }
if (!(Test-Path -LiteralPath $Vivado))  { throw 'Missing Vivado tool' }

python fi/v4/scripts/production_runner_v4f4f.py `
  --mode source-preflight `
  --project "$Project" `
  --vivado "$Vivado" `
  --out-dir fi/results/v4f4f_source_preflight_01
```

If source-preflight fails, **do not** copy other HDL/glbl bytes into place
or change frozen inputs just to make it pass. Report the exact error. Do
not reuse a failed output path. An evidence mismatch is a gate blocker.

If both PASS, run the read-only G0–G7 release-gap report:

```powershell
python fi/v4/scripts/audit_production_v4f4f.py `
  --mode release-review `
  --source-dir fi/results/v4f4f_source_preflight_01 `
  --history-dir fi/results/v4f4f_history_audit_01 `
  --rehearsal-audit-dir fi/results/v4f4f_rehearsal_audit_01 `
  --out-dir fi/results/v4f4f_release_review_01
```

It **must** say `G6 NOT QUALIFIED`, `G7 NOT APPROVED`, `FULL400 BLOCKED`.

## 2 — Install V4f4G new-file-only patch

Extract patch only after checking the following 4 target files do not exist:

```powershell
$New = @(
  'fi/v4/scripts/production_blueprint_v4f4g.py',
  'fi/v4/scripts/test_production_blueprint_v4f4g.py',
  'fi/v4/docs/V4F4G_PRODUCTION_BLUEPRINT_SPEC.md',
  'fi/v4/docs/V4F4G_WINDOWS_RUNBOOK.md'
)
$Existing = $New | Where-Object { Test-Path -LiteralPath $_ }
if ($Existing) { throw "Target files already exist: $($Existing -join ', ')" }
Expand-Archive `
  -Path '.\cnn_pep_v4f_phase4g_production_invocation_blueprint_patch.zip' `
  -DestinationPath .

python -m unittest discover `
  -s fi/v4/scripts `
  -p 'test_production_blueprint_v4f4g.py' -q
```

## 3 — Generate exact 400-record blueprint (NO XSim)

```powershell
python fi/v4/scripts/production_blueprint_v4f4g.py `
  --mode blueprint `
  --plan-dir fi/results/v4f4f_plan_01 `
  --out-dir fi/results/v4f4g_blueprint_01
```

Expected:
`V4F4G_BLUEPRINT_PASS: 400/400 exact frozen XSim invocation contracts; no executable commands; 0 XSim; FULL400 BLOCKED`.

## 4 — Independent byte-exact read-only audit

```powershell
python fi/v4/scripts/production_blueprint_v4f4g.py `
  --mode audit `
  --plan-dir fi/results/v4f4f_plan_01 `
  --blueprint-dir fi/results/v4f4g_blueprint_01 `
  --out-dir fi/results/v4f4g_blueprint_audit_01
```

Expected:
`V4F4G_BLUEPRINT_AUDIT_PASS: 400/400 invocation contracts replayed; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED`.

All output paths must be absent before first use. Do not overwrite old
receipts; if a command fails, preserve its artifacts and send the error
rather than guessing its cause. `execute`, `full400`, `--execute`,
`--approve` are unavailable in V4f4G. No new canary is permitted (8/8 used).

## 5 — Hand back

Share the terminals from history, source-preflight, release-review and
blueprint-audit. For a byte-level second review, ZIP their output folders
and send the original ZIP separately. Next phase: separately versioned
execution backend qualification and G7 manual protocol approval. No release
is automatic.

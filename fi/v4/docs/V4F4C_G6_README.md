# CNN_PEP V4f Phase 4C — Bounded Real-XSim Failure Qualification

**NOT RELEASED.** This is a new, additive one-scenario-per-attempt test harness, not
production Full400 and not an approval of G6/G7. Windows XSim tests below require
explicit human review of the new source and a real authoritative Vivado `.xpr`.

## Evidence and scope

- Immutable frozen manifest/targets/original TB hashes are checked by prior V4f validators.
- Exact 8 **candidate** scenarios on IMG0003/IMG0006, four classes; each separate
  execution attempt selects **exactly one** tuple, hard-coded maximum **one XSim
  invocation**. **Total budget: 8 reserved attempt slots across the entire Phase4C
  namespace**, enforced under one persistent cross-process lock. Fresh `xvlog`
  and `xelab` are each isolated in their own kill-on-close Windows job.
- Read-only raw V4e ancestry and V3a pin are checked before Windows execution.
- Windows subprocess isolation uses a private **Job Object** with kill-on-close:
  the Python launcher cannot spawn `cmd.exe` until assigned to its job. If job
  assignment is unavailable, do not launch XSim. No `taskkill /IM` or global
  process-name kills. The parent polls exact log markers, exit and job accounting.
- `--trial healthy` commits ONLY after XSim exit 0, strict FI parser, source
  integrity and log hashes. `terminate-before-fi`, `terminate-after-fi`, and
  `external-timeout` leave the attempt BLOCKED even if the interruption was
  reproduced. `crash-before-commit` calls `os._exit(86)` after a completed raw
  log and intentionally leaves orphan artifacts; never salvage automatically.
- No automated in-place resume. Retry only with a **new attempt directory** and
  `--parent-failed-dir` referring to a **BLOCKED, finished** parent attempt.
  A still-`RUNNING` orphan parent is never auto-salvaged or auto-retried; it
  requires independent OS/process review and a separate recovery decision.
- A successful Python mock test does **NOT** qualify Windows Job Objects,
  mid-XSim kill, OS crash or power-loss behavior. Run bounded Windows tests
  and review raw receipts independently before making those claims.
- External timeout/crash are INVALID infrastructure events, not RTL
  `NONCOMPLETION` and not harmful FI outcome.

## Windows first step (NO Vivado or XSim)

Run in existing repository after reviewing files copied from the patch. Do not
extract an archive over `fi/results` or frozen source folders:

```powershell
python -m unittest discover -s fi/v4/scripts -p "test_*v4f4c.py" -q
python fi/v4/scripts/qualify_xsim_failures_v4f4c.py `
  --mode plan `
  --out-dir fi/results/v4f4c_plan_01
```

Expected: `V4F4C_PLAN_PASS: 8/8 frozen IMG0003/IMG0006 canaries; ... 0 XSim; FULL400 BLOCKED`.

## Optional ONE real XSim healthy attempt (after code review)

The command below explicitly allows **one** XSim run (plus one fresh xvlog and
one fresh xelab, all three in isolated Windows Jobs). Ensure `$Project` is the known-good Vivado `.xpr` and
`$Vivado` identifies the correct installed Vivado executable. The source pin
and V4e ancestry are rechecked before any child process is launched:

```powershell
python fi/v4/scripts/qualify_xsim_failures_v4f4c.py `
  --mode execute `
  --trial healthy `
  --scenario-id FI_P400_0007 `
  --project "$Project" --vivado "$Vivado" `
  --timeout-seconds 900 `
  --out-dir fi/results/v4f4c_healthy_01 `
  --execute
```

On true XSim success, run the **zero-XSim** auditor:

```powershell
python fi/v4/scripts/audit_v4f4c.py `
  --attempt-dir fi/results/v4f4c_healthy_01 `
  --out-dir fi/results/v4f4c_healthy_audit_01
```

Do not start interruption trials until the healthy result and Windows PID/job
lifecycle receipt are reviewed. On such trials keep a NEW output directory,
change `--trial` to one of the bounded failure modes, and check whether the
expected marker boundary was actually observed. `*_NOT_REPRODUCED` is **not**
a pass. Do not increase budgets or trial counts without writing a new plan.

An optional, destructive-to-its-own-process ONLY `--trial crash-before-commit`
requires `--confirm-abrupt-exit`. It exits the Python runner with code 86
without changing any other attempt; failed/orphan files are preserved. This
is not an operating-system or power-loss test.

## Checkpoint, classification and audit rules

- `inflight.json` is created before execution and is intentionally retained
  if the runner aborts. `attempt.json` is fsync/atomic-replaced via V4d helper.
- Valid healthy attempt: `status=ONE_BOUNDED_XSIM_STRICTLY_VERIFIED_FULL400_BLOCKED`,
  1 committed mechanism, no in-flight marker, validated logs.
- Aborted attempt: `status=ATTEMPT_BLOCKED_INVALID_INFRASTRUCTURE_FULL400_BLOCKED`
  or left `ATTEMPT_INFLIGHT_NOT_CLASSIFIABLE_FULL400_BLOCKED` after abrupt exit;
  0 committed mechanisms. Read-only auditor inventories it as INVALID only.
- Any untracked log, command, wrapper, source drift or checksum mismatch is
  rejected, not automatically deleted or counted.
- Full400 execution **blocked**; independent G0–G7 release and G7 manual
  attestations still absent. Maintain separate real XSim, synthetic, mock and
  fault-free temporal denominators.

## Known non-claims / caveats

- Failure-mode interruption depends on when log markers become visible; a fast
  XSim may finish before a trigger is observed. Report NOT_REPRODUCED, not PASS.
- Windows Job Object availability is not proven by Linux/offline tests. If
  policy disallows nested jobs, abort safely before opening the launcher gate.
- Compiling + running on the user's Windows project, plus raw replay, is still
  necessary before calling this G6 **bounded real** qualification successful.
- Two independent human G7 attestations are NOT fabricated or implied.

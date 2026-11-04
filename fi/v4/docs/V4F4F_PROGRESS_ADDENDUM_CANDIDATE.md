# CNN_PEP — V4f Phase 4F Candidate Progress Addendum (2026-11-04)

Append after canonical `PROJECT_PROGRESS.md` only **after** reviewing this
patch on the original Windows repo. Do not label this candidate a final G6
production qualification.

## Source and local offline outcomes

- New source: `production_runner_v4f4f.py`, `audit_production_v4f4f.py`,
  `test_production_runner_v4f4f.py`; new execution-safety spec and Windows runbook.
- Core contract: exact 400 frozen tuples → deterministic 16 × 25 batches,
  original four hashes, all ten real samples, six frozen weights/biases and
  exact ten plusargs for a *future* FI runner.
- `plan`: nonexecuting 400 schedule and source/asset pin inventory.
- `rehearsal`: local synthetic 137/400 pause → strict resume to 400/400,
  atomic checkpoint/event hash chain; no XSim and no FI outcomes.
- `audit rehearsal`: strict read-only replay of 400 synthetic receipts.
- `audit history`: read both original G6 ZIP archives, re-verify 8/8 attempt
  statuses and 590/590 raw artifact SHA256; retain slot 3 and 7 NOT_REPRODUCED,
  slot 8 PASS, two bounded FI results not part of Full400.
- `source-preflight`: **code implemented, needs original Windows project**;
  no local certificate for Vivado source toolchain is claimed.
- `release-review`: confirms no signed G0-G7 authorization and all execute
  and approve requests blocked before I/O.
- Unit tests on current offline workspace: see attached runbook and final
  regression logs; **none** represent actual XSim simulation.

## G0–G7 status after patch

- G0: source-preflight candidate implemented; final Windows verification pending.
- G1–G4: historical evidence unchanged; no new FI produced.
- G5: scientific/outcome contract needs final cross-validation and signoff.
- G6: bounded eight-attempt safety case PASS; Phase4F synthetic **offline**
  contract and tests PASS; **production executable runner NOT IMPLEMENTED,
  NOT QUALIFIED**.
- G7: SHA-pinned PREPARED; independent human operator/reviewer approvals and
  seal/verify remain pending.
- Final signed authorization: absent; **Full400 BLOCKED**.

## Next actions

1. Windows install patch, run tests/plan/rehearsal/history audit/source preflight.
2. Independent review of artifacts, source bytes, README and failure test suite.
3. Implement separately versioned real backend + G6 execution qualification
   and fail-closed release mechanism; this phase contains no enabled FI executor.
4. Complete independent G7 two-person signoff and G0/G5 artifacts.
5. Only after final independent G0–G7 authorization, consider 400 FI campaign.

**Never rerun any of the exhausted 8/8 bounded failure attempts.**

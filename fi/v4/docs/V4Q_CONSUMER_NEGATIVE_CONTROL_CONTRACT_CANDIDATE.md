# CNN_PEP V4Q — Consumer-Exposure Negative-Control Contract (CANDIDATE)

**Version**: `CNN_PEP_V4Q_DIRECT_REGISTER_TRACE_V1`
**Scope**: offline/mock trace model and strict audit only; no Vivado/XSim jobs; no FI campaign.
**Approval state**: `CANDIDATE_NOT_REVIEWED`, G0/G5 partial; G6 production NOT QUALIFIED; G7 PREPARED_NOT_APPROVED; Full400 BLOCKED.
**Source authority**: `PROJECT_PROGRESS (8).md` (2026-11-04); V4P Windows independently replayed; frozen historical V4c E1/E3 scope IMG0000. This patch is **not** based on newly observed real E3-negative traces.

## Why V4Q exists

The 40 directed V4c IMG0000 scenarios already have scenario-level E3 value-aware sampling/decision evidence. No observed *real* E1-positive / E3-negative counterpart is established by these 40. `FINAL_OUTPUT_MATCH` alone cannot establish E3-negative; neither can an unmonitored signal. The target methodological experiment is to distinguish:

1. E1 successful single-bit upset confirmed by old/new/readback, but **verified not sampled in corrupted form** by a reviewed exhaustive consumer set before overwrite or end of inference;
2. E1 successful and E3 accepted corrupted value, including output-match cases;
3. E3 unmonitored/incomplete, which must remain unknown, never silently called negative.

These outcomes should eventually be explored in separate, independently authorized isolated RTL experiments, not retrofitted into Pilot400's frozen catalog/denominators.

## Current implementation and limits

- `consumer_negative_controls_v4q.py`: synthetic two-channel model fixture generator, strict E1/readback and clock-phase trace schema, per-cycle consumer coverage, read-only candidate `inspect`, deterministic LF outputs with a self-replay `audit` mode.
- `audit_consumer_negative_controls_v4q.py`: separate source implementation; recomputes semantic status, scenario/CSV/summary integrity and SHA manifest without importing the generator.
- `test_*v4q.py`: mutation/negative tests, including self-rehashed forged results and authorization status.
- `offline_reference/v4q_synthetic_01/`: 7 model-only records (not even a miniature real CNN or XSim run).

**Caution**: `EXTERNAL_TRACE_UNVERIFIED` is never eligible for real E3 TRUE or FALSE release. It is a format/logic intake candidate only. The validator hard-rejects changing `mapping_review` from `UNREVIEWED` to any supposed approval. G7 sign-offs are a separate human process.

## Trace model — explicit assumptions

The V4Q V1 schema supports only `DIRECT_REGISTER_SOURCE_ONLY` with 8,16,32,64-bit unsigned bus values. This is a bounded *toy* state trace, **not** a general observer for CNN_PEP MEM/CTL/DAT/BUF. In particular, it does NOT qualify a control-decision E3 using data-value comparison, and it does NOT handle transformed/packed or multi-cycle MAC consumer semantics. Such adapters require versioned schemas and independently reviewed mapping. Do not silently use this parser for an actual CTL target.

The trace's `fault` asserts a **POST_NBA** deposit at cycle `t`, so sample edges begin at `t+1`, **PRE_NBA**. Real V3b/V4c testbench NBA semantics must be mapped and independently verified before translating a real offset to this model. There is no assumption that original Pilot400 FI offsets are POST_NBA.

Within the declared fault lifetime, each observed clock edge lists the source faulty and golden register value and one observation for **every declared** directly connected consumer. Each consumer sample carries its acceptance gate and, if accepted, the faulty and golden values. `overwrite_after_edge` closes source lifetime only after the *PRE_NBA* sampling edge; `INFERENCE_END` closes a window only by model assertion. A real experiment needs independent raw evidence of overwrite and done, not this field alone.

For a **mock negative**, all of the following must hold:

- E1 exactly one bit XOR with readback, no X/Z or duplicate marker;
- source remains different during the complete monitored interval;
- no accepted corrupted sample in every **declared** consumer channel at every edge;
- all clock cycles from first eligible edge to the true overwrite/end are present, without gaps or wrong sampling phase;
- no unknown valid/readiness/acceptance condition;
- full *reviewed* enumeration of actual RTL consumers (NOT PROVIDED by V4Q mock).

The synthetic model can establish **only** that its own declared two-channel trace satisfies the rule. It cannot prove actual CNN consumer absence. An incomplete source/edge/channel or an open window yields `E3_NOT_MONITORED` / `E3_INCONCLUSIVE_PARTIAL_COVERAGE` rather than FALSE.

### The seven synthetic cases

| ID | Purpose | Expected code status | Meaning |
|---|---|---|---|
| `V4Q_NC0` | No FI, baseline | `NO_INJECTION_BASELINE_NOT_AN_E3_NEGATIVE` | Separate false-positive control |
| `V4Q_NC1` | E1-positive, no accepted corrupted direct sample | `E3_NO_CONSUMPTION_IN_DECLARED_SCOPE_MOCK_ONLY` | **Mock only**, NOT a real negative |
| `V4Q_NC2` | E1-positive, accepted wrong value, final match annotated | `E3_TRUE_WITNESS_MOCK_ONLY` | Accepted-sample synthetic witness |
| `V4Q_NC3` | E1-positive, accepted wrong value, stage-only outcome hypothesis | `E3_TRUE_WITNESS_MOCK_ONLY` | **Stage mismatch not independently established in V4Q schema**; purpose is illustrative |
| `V4Q_NC4` | E1-positive, accepted wrong value, wrong-logit outcome annotated | `E3_TRUE_WITNESS_MOCK_ONLY` | Outcome is mock metadata, not actual E4 log |
| `V4Q_GAP_NO_MONITOR` | No monitor edges | `E3_NOT_MONITORED` | Must never be called negative |
| `V4Q_GAP_PARTIAL` | Missing intermediate edge | `E3_INCONCLUSIVE_PARTIAL_COVERAGE` | Must never be called negative |

**Scientific caveat:** The `outcome` field in V4Q is a synthetic annotation only, not a numerical checkpoint with raw supporting data. Neither the NC3 stage-only designation nor NC4 wrong logits is independently computed by V4Q. V4P remains the source for actual historical, separately audited L0/L1/L2 conclusions.

## Frozen and provenance boundaries

Do not modify these files or their bytes:

| Frozen file | SHA256 |
|---|---|
| `fi/config/fi_targets_candidates_v2.csv` | `d9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d` |
| `fi/config/pilot400_targets.csv` | `98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25` |
| `fi/scenarios/pilot400.csv` | `cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4` |
| `fi/tb/tb_cnn_mnist_fi_pilot400.v` | `2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13` |

G7 V1 core SHA `30a5ea2e66bda0d38e38d0a45635da5abe5ddbcf846672ef9d2e20cf546cf733` remains pinned. It cannot be changed for V4Q; if experiment semantics change materially, submit a separate versioned review. V4f4C/D/E budget **8/8 exhausted**; neither V4Q nor V4P provides a ninth attempt or Full400 release authorization.

## Real RTL follow-on acceptance checklist (NOT IMPLEMENTED/EXECUTED)

1. **New experiment IDs**: e.g. `NC_V4Q_0001`, distinct from `FI_P400_0001..0400`. Resolve exact target register, bit, clock, accepted-sample timing from separate **fault-free authoritative baseline** before launching FI. Do not change original Pilot400 frozen catalog.
2. **Reviewed signal map**: source signal and EVERY possible consumer register/interface, all relevant validity/ready/enables, control decision edges and exact phases. Review against Vivado-authoritative `.prj`, HDL source and synthesized interfaces. Full dataflow coverage requires review **by a human**; a file calling itself `complete` is insufficient.
3. **Source input pinning**: independent digest for isolated TB, compiled `.prj`, 26 RTL files + `glbl.v`, toolchain, six HEX, sample/golden, plusargs and exact schema/version. The source snapshot must be the one used to run the experiment.
4. **Real E1**: one actual XSim FI_APPLIED marker, old/new/readback, requested/actual cycle, sampled source and event timing; E1 must not be inferred from a plusarg or a log echo.
5. **Real E3**: consumer-value sampling at correct pre/post NBA edge for every declared channel; explicit check of first/last eligible event and every intervening edge; demonstrated overwrite or inference termination. Accepted samples must have golden counterpart at the same logical edge.
6. **Negative tests**: missing channel/clock, X-state, invalid gate, wrong pre/post NBA alignment, consumer in a different always block, memory read port, extra RTL consumer, overwritten-before-sampling behavior, malformed/duplicate FI marker, outer timeout or orphan process.
7. **Real evidence release**: separate authorized bounded XSim budget, immutable attempt directories and raw file SHA, independent source/evidence audit, **genuine human scientific review**. Validation from a mock or an unreviewed external trace is not sufficient.
8. **Interpretation**: E3 FALSE, if approved, applies **only** to independently established full consumer scope at a specific image/target/bit/offset. It never follows just because final logits match and never estimates a physical SEU prevalence rate.

### Required paper-ready comparison once genuine NC data exists

Compare (a) `E1_valid` count, (b) `E3_assessed`, (c) `E3_true`, (d) `E3_false_verified`, (e) `E3_inconclusive`, (f) L0/L1/L2 evaluable counts and outcomes, stratified by the separately frozen experimental plan. Do not mix these new control scenarios into existing 40 IMG0000 selected-consumer denominator or the planned Full400 numerator.

## Required engineering stop conditions

- No **raw, reviewed, complete** consumer enumeration => no real E3-negative.
- No real source- and artifact-hashed XSim evidence => no real E3-positive or E3-negative from V4Q.
- Any missing edge/channel, X value, false clock phase, invalid E1 or tool/OS failure => no negative claim.
- No G0–G7 final approval => Full400 remains BLOCKED, regardless of mock validator tests.

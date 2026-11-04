# CNN_PEP V4P - Novelty experiments and falsifiable scientific claims

Status: `CANDIDATE_NOT_APPROVED`, 2026-11-05. Scope: read-only analysis plus **proposed** isolated RTL experiments. No new XSim authorization, no change to G7 protocol V1 SHA, no Full400 permission.

## A. Research gap sharpened after prior work

This work must NOT claim novelty for (1) RTL injection itself, (2) generic numerical masking by activation/pooling, (3) inter-stage monitoring, (4) hardware-aware FI, or (5) on-chip detection. Chen et al. 2024 analyze CNN control sensitivity and activation/pooling effects; Belle II 2026 compares stage/liveness monitors; Tanaka et al. 2025 develop in-situ control detectors; Saca-FI and ENFOR-SA investigate hardware-aware propagation and acceleration.

The falsifiable combination sought here is **per-scenario E1 injection proof + E3 accepted-consumer/control-decision proof + bit-exact E4 stage comparison + measured first/last changed element across hardware stage boundaries**, with the same scenario IDs used in paired observer comparisons. The current V4P offline module proves the first three within the frozen V4c/V4M-style scope and evaluates arithmetic counterfactuals, but **does not prove full RTL causal masking**.

## B. Existing evidence (do not rerun for a new PASS marker)

- Frozen P0, 4 frozen SHA256, 400 planned tuples, original FI TB preserved.
- V4c: 40/40 directed IMG0000 E3-qualified proofs across 20 targets, 2 configurations per target.
- Historical raw logs re-parsed by V4P: L0=9, L1=20, L2=24; POOL1-only incremental L2 gain=4, POOL2-only gain=0 **in this selected 40**.
- 4 L2-only: FI_P400_0101 / 0102 (BUF_0007) and FI_P400_0301 / 0302 (DAT_0002); POOL1 has 1 **byte** mismatch each, final logits same.
- All 16 negative L2 cases have full POOL1 (169 packed words x 4 bytes) and POOL2 (25 packed words x 8 bytes) comparison counts with zero reported mismatch. E3 remains valid; L2=FALSE is not E3=FALSE.
- Model-based single-byte counterfactual (IMG0000, same frozen weights) predicts different *possible* suppression mechanisms. The entire faulty RTL POOL1 tensor was not independently dumped; avoid causal overclaim.

## C. E1: Negative controls for why E1 alone is not E3

**Objective:** establish at least one scenario where a bit flip is verified (E1) but a complete, value-aware monitor proves it is not accepted by *any monitored consumer* before overwrite/end of relevant lifetime. This addresses the strongest methodological gap in the original 40 all-E3-positive cohort.

| Proposed control | Injected? | Required proof | What can be reported |
|---|---|---|---|
| NC0 fault-free control | NO | Full baseline and golden; no E1 markers | Monitor false-positive control only |
| NC1 overwrite/no accepted consumer | YES | Exactly one E1; enumerate all possible consumers; ordered writes/reads and accept/valid edges; complete lifetime; zero corrupted accepted values | `E3_NEGATIVE_VERIFIED` **only after exhaustive monitor review** |
| NC2 consumed, final match | YES | Exactly one E1; pre-/post-NBA verified accepted value; full final comparison | `E3_TRUE_FINAL_MATCH` |
| NC3 consumed, stage mismatch, final match | YES | Same E3 proof + complete intermediate byte-diff + full final comparison | `E3_TRUE_STAGE_ONLY` |
| NC4 consumed, logits/label effect | YES | E3 proof + integer logits/argmax comparisons | `E3_TRUE_NUMERIC_OR_HARMFUL` |

**Mandatory negative controls:** missing consumer channels, wrong NBA edge, zero read samples, X states, incomplete monitoring window, invalid FI, host timeout, and synthetic markers must all result in `INCONCLUSIVE` or `INVALID_INFRASTRUCTURE`, **not** `E3_NEGATIVE_VERIFIED`.

The new control IDs MUST be in a new versioned experimental catalog (`NC_V4P_xxxx`), never inserted into `fi/scenarios/pilot400.csv` or used to alter its denominator. Exact target/bit/offset selection needs independent review and a new execution budget. No campaign launch commands are supplied here.

## D. E2: Complete faulty-stage trace for 4 L2-only witnesses

The original target and bit/offset are retained; use a new versioned isolated monitor TB with a *separate source hash*. Do **not** change P0 RTL or frozen original FI TB to force a monitor PASS.

For each of the four real scenarios, capture and SHA-pin:

1. E1 event with old/new/readback/requested/actual cycle and injection target.
2. Enumerated consumers, accepted valid/ready, pre-NBA sampled corrupted value and post-NBA state; edge order and complete source-to-consumer lifetime.
3. Full POOL1 faulty byte array, `676` INT8 bytes in `[c][y][x]` order, corresponding golden and first mismatch indices/values.
4. Conv2 raw accumulator `968` signed INT32 Q8.8 values; post-RNE/saturation quant `968` INT8; post-ReLU `968` INT8.
5. POOL2 faulty `200` INT8 bytes, FC faulty `10` signed INT32 logits and argmax/done/protocol.
6. Checkpoint counts and exact XSim process/status receipts; every raw file has absolute byte count, SHA256 and source-version pin.

**Analysis:** compare every stage against the exact frozen golden. First/last mismatches and changed element sets must be measured from **actual RTL dumps**, not guessed by golden counterfactual. A disappearing mismatch is evidence of a stage boundary where it is absent; asserting a particular masking mechanism requires a matched operator-level trace or equivalent derivation.

### Current mechanistic hypotheses from Python one-byte replacement

| Scenario | POOL1 model change | Candidate explanation | How to falsify |
|---|---|---|---|
| BUF_0007 A/B | byte337 00->01 | Modified Conv2 post-ReLU at `(ch7,10,10)`, outside 5x5 POOL2's consumed 10x10 region | Faulty POOL1/Conv2 raw/ReLU dumps disagree with modeled single-byte tensor |
| DAT_0002 A | byte78 00->01 (V4N predicted, not direct faulty dump) | Conv2 quant output changes but post-ReLU tensor matches | Some faulty RTL post-ReLU value differs, or faulty POOL1 has other changes |
| DAT_0002 B | byte119 00->01 (V4N predicted) | Three Conv2 post-ReLU changes disappear at max-pool output | Faulty POOL2 mismatch or competing root cause appears |

The older handoff snapshot lacks V4N/V4O final result ZIPs. Therefore the DAT byte indices are **document-supported hypotheses**, not independently re-verified faulty RTL indices in this patch. New full-dump execution must be separately authorized.

## E. E3: Checkpoint ablation and diagnostic contribution

Do **paired** comparisons over exactly the same evaluable scenario IDs. Keep 3-valued `TRUE/FALSE/INCONCLUSIVE` whenever some checkpoint is absent. Do not count invalid infrastructure as design noncompletion.

- L0: design NONCOMPLETION / PROTOCOL_VIOLATION / WRONG_LABEL.
- L1: L0 or WRONG_LOGITS_SAME_LABEL.
- L1+P1: L1 or valid POOL1 mismatch.
- L1+P2: L1 or valid POOL2 mismatch.
- L2: L1 or either declared checkpoint mismatch; L2 FALSE only with all declared checkpoints complete and zero mismatches.

**Report unique incremental detection separately from diagnostic localization.** In existing IMG0000 evidence P1 adds 4 and P2 adds 0 beyond L1, but POOL2 first-bad markers occur in 3 scenarios whose output label is wrong. Never conclude a physical POOL2 monitor has zero benefit globally.

## F. E4: Optional multi-image experiment (not authorized)

Frozen 400 = 20 target x 10 MNIST images x 2 directed configs, NOT 400 FI outcomes. If authorized after G0-G7 and qualified G6, maintain `planned`, `executed`, `E1_valid`, `E3_assessed`, `Lk_evaluable`, `invalid`, `missing`, `inconclusive` per image/target/target class and paired `(target,image)` grouping. Every new image needs its own consumer E3 proof if the paper claims all exposures verified. No population-rate confidence interval or SEU MTTF without representative random sampling.

## G. Mandatory evidence/release separation

- Existing 8/8 bounded G6 canary budget exhausted; no ninth slot without separate approved budget.
- V4f4F/G, V4H-EXEC, V4I/J, V4K/L were offline/mock contracts, not a real production executor.
- G0/G5 partial, G6 production not qualified, G7 prepared but not two-person approved; Full400 blocked.
- New analytic V4P `PASS` remains `OFFLINE_CANDIDATE_NOT_G7_SIGNED`, cannot sign R1-R6 or set `full400_authorized` true.
- All frozen RTL/TB/CSV, V1 G7 protocol-core digest, historical receipts and raw XSim logs are immutable.

## H. Paper-safe contribution and acceptance criteria

Present contribution 1: scenario-level E1+E3 evidence and integer numerical outcomes on a directed set.
Present contribution 2: paired, counter-complete L0/L1/L2 and individual stage ablation (with conditional denominator and evidence tier).
Present contribution 3 **only if E2 complete RTL trace is implemented**: precise stage-boundary disappearance matched to actual faulty tensors, validated against independent model.
Present methodological effect of negative controls **only if NC1 exhaustive no-consumption is proved**, not from `E3_NOT_MONITORED`.

This module measures a conditioned directed set; it does not generalize a physical FF upset rate, on-chip detector coverage, or global classification accuracy degradation.

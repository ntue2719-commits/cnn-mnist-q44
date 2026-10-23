# CNN_PEP — RTL Fault Injection Specification

**Project:** CNN MNIST Q4.4 RTL Accelerator  
**Phase:** Phase 2 — RTL Fault Injection  
**Baseline status:** P0 fault-free RTL locked and verified  
**Target platform:** AMD/Xilinx Kria K26 / KR260  
**Vivado:** 2022.2  
**Clock target:** 100 MHz  
**Primary goal:** characterize transient single-bit faults across CTL / BUF / MEM / DAT structures before adding protection.

---

# 1. Purpose

This specification defines the fault model, target taxonomy, injection procedure, observability, outcome classification, experiment logging, pilot campaign, and acceptance criteria for RTL fault injection on the verified CNN_PEP baseline.

The intent is to keep the experimental conditions controlled:

```text
same RTL baseline
same numerical contract
same input workload
same clock
same observation points
same fault model
same scenario format
```

Only the injected target, bit, cycle, and later the protection configuration are varied.

---

# 2. Locked P0 Baseline

The fault-injection infrastructure must preserve the current fault-free behavior.

## 2.1 Functional baseline

```text
CNN_MNIST_TOP TEST: PASS
Frames tested       : 2
Input pixels total  : 1568
Conv1 words total   : 338
Conv2 words total   : 50
FC output events    : 2
Prediction events   : 2
Expected digit      : 0
Final DUT digit     : 0
Total checks        : 1808
Errors              : 0
```

## 2.2 Performance baseline at 100 MHz

Nominal continuous-input frame:

| Metric | P0 baseline |
|---|---:|
| End-to-end latency | 1342 cycles |
| End-to-end latency | 13.420 us |
| Post-input latency | 557 cycles |
| Post-input latency | 5.570 us |
| Initiation interval | 1343 cycles |
| Initiation interval | 13.430 us |
| Sustained non-overlapped throughput | 74,460.16 images/s |

Back-to-back verification:

```text
Frame 0 latency = 1342 cycles
Frame 1 latency = 1342 cycles
Frame 2 latency = 1342 cycles

II 0->1 = 1343 cycles
II 1->2 = 1343 cycles
```

## 2.3 Post-implementation baseline

| Metric | P0 baseline |
|---|---:|
| LUT | 2566 |
| FF | 813 |
| DSP | 0 |
| BRAM | 0 |
| WNS | +0.466 ns |
| TNS | 0.000 ns |
| Timing | MET |
| Clock | 100 MHz |

These numbers are the reference for all later P1/P2/P3 comparisons.

---

# 3. Numerical and Protocol Contract

The FI-enabled design must preserve the existing numerical contract when injection is disabled.

## 3.1 Numerical contract

```text
Input / feature / weight / bias : signed INT8 Q4.4
Q4.4 scale                     : 16
INT8 × INT8                    : signed Q8.8
Conv / FC accumulator          : signed INT32 Q8.8
Bias alignment                 : sign-extend INT8, then <<< 4
Conv requantization            : round-to-nearest-even, >> 4
Saturation                     : [-128, 127]
ReLU                           : after saturation
FC output                      : raw signed INT32 Q8.8 logits
ArgMax                         : signed strict >
Tie                            : lower class index wins
```

## 3.2 Canonical tensor order

```text
[channel][y][x]

index = c*(H*W) + y*W + x
```

Low byte is packed first.

## 3.3 Protocol

```text
i_valid = 1 -> transaction accepted on rising edge

legal behavior:
- valid gaps are allowed
- counters/buffers advance only on accepted transactions
- i_start is accepted only while the relevant block is idle
- no active frame may be overwritten by a new frame
```

---

# 4. Primary Fault Model

The initial campaign studies **single transient bit upsets in RTL state**.

## 4.1 Fault definition

For selected state `s` and bit `b`:

```text
s_faulty = s XOR (1 << b)
```

The selected bit is flipped at one chosen clock event.

## 4.2 Injection pulse versus fault persistence

The FI control pulse is asserted for one injection event only.

```text
FI command duration = one injection event / one cycle
```

After the flip is applied, the state returns to normal RTL control.

The resulting corrupted value may remain stored until that register or memory location is naturally overwritten.

Therefore:

```text
injection action       = one transient bit flip
fault-effect lifetime  = until normal overwrite / state update
```

This distinction must be preserved in the paper and experiment logs.

## 4.3 Faults per inference

Core campaign:

```text
1 injected fault / inference
```

This enables direct attribution between one scenario and one observed outcome.

## 4.4 Out of scope for the core campaign

The initial campaign does not include:

```text
- permanent stuck-at behavior
- simultaneous multi-bit upsets
- physical irradiation testing
- FPGA configuration-memory corruption
- gate-level fault injection
- board-level power fault injection
```

These may be future work.

---

# 5. Structural Target Taxonomy

Every target must belong to exactly one primary structural class.

## 5.1 CTL — Control

Examples:

```text
FSM state
phase/state registers
write/read pointers used for sequencing
loop counters
spatial counters
channel counters
start/done sequencing state
valid-generation state
top_fsm state
```

Typical effects:

```text
missing transactions
extra transactions
wrong sequencing
premature completion
non-completion
protocol violation
wrong stage activation
```

## 5.2 BUF — Streaming / Line Buffers

Examples:

```text
conv1 line/window storage
pool1 buffering state
conv2 captured feature storage used as window source
pool2 buffering state
reorder temporary storage
stream staging registers
```

Typical effects:

```text
local feature corruption
window corruption
corruption propagated to downstream stages
possible multiple-output contamination before overwrite
```

## 5.3 MEM — Writable Feature Memories

Primary targets:

```text
feature_mem1
feature_mem2
```

Examples:

```text
stored feature word
stored feature byte
read/write address state if classified as memory-local control
```

Data storage bits should remain MEM.

Memory-control registers may be tagged CTL if their primary semantic role is sequencing rather than payload storage.

Typical effects:

```text
persistent feature corruption
downstream Conv2 / FC numerical corruption
wrong logits
wrong label
```

## 5.4 DAT — Datapath State

Examples:

```text
Conv1 accumulator registers
Conv2 accumulator registers
FC accumulator registers
registered quantization outputs
registered pooling outputs
arithmetic pipeline state
logit registers
```

Typical effects:

```text
numerical mismatch
masked numerical error
wrong logits
wrong label
```

---

# 6. Target Catalog

Do not automate large campaigns before a target catalog exists.

Recommended CSV:

```text
fi_targets.csv
```

Schema:

```csv
target_id,class,module,hier_signal,width,bit_min,bit_max,semantic_role,notes
```

Example:

```csv
CTL_0001,CTL,top_fsm,dut.u_top_fsm.state_reg,3,0,2,top sequencing,FSM state
MEM_0001,MEM,feature_mem1,dut.u_feature_mem1.mem[0],32,0,31,Pool1 feature storage,word 0
DAT_0001,DAT,fully_connected,dut.u_fully_connected.acc_reg[0],32,0,31,class-0 FC accumulator,Q8.8
```

The exact hierarchical names must come from the compiled RTL design.

---

# 7. Injection Mechanism

## 7.1 Development principle

Do not begin with a large generic injector.

First prove one deterministic injection:

```text
one known register
one known bit
one known cycle
one known input
```

## 7.2 Preferred simulation strategy

For XSim RTL experiments, begin with testbench-driven hierarchical corruption using a deterministic FI task or simulator force/deposit mechanism.

The first mechanism must support:

```text
target identification
bit selection
cycle selection
single-event trigger
waveform observability
scenario logging
```

## 7.3 Required proof

For the first injected scenario:

```text
1. run golden baseline
2. select target
3. select bit
4. select cycle
5. inject once
6. inspect waveform
7. prove intended bit changed
8. prove unrelated targets did not change due to injector
9. release / return to normal RTL behavior
10. record resulting outcome
```

Do not scale before this proof is complete.

---

# 8. FI-Disabled Equivalence Gate

Before any real campaign:

```text
FI-enabled RTL + FI disabled
==
frozen P0 baseline
```

Minimum acceptance criteria:

```text
golden Pool1        PASS
golden Pool2        PASS
golden FC logits    PASS
prediction          PASS
latency             1342 cycles nominal
II                  1343 cycles back-to-back
functional checks   PASS
```

Resource usage may change slightly if instrumentation is synthesized, but simulation behavior must remain bit-exact.

If FI instrumentation changes fault-free numerical behavior, stop and fix FI infrastructure.

---

# 9. Injection Scenario Definition

Each experiment must be reproducible.

Recommended scenario schema:

```csv
scenario_id,
sample_id,
target_id,
target_class,
hier_signal,
bit_index,
injection_cycle,
fault_type,
injection_events,
seed
```

Core values:

```text
fault_type       = SEU_SINGLE_BIT
injection_events = 1
```

Example:

```csv
S000001,IMG0000,CTL_0001,CTL,dut.u_top_fsm.state_reg,1,920,SEU_SINGLE_BIT,1,12345
```

---

# 10. Observation Points

Observe both numerical correctness and execution behavior.

## 10.1 Internal numerical checkpoints

```text
Conv1 / Pool1 canonical output
Conv2 / Pool2 canonical output
FC logits
prediction
```

Recommended first-bad-stage labels:

```text
NONE
POOL1
POOL2
FC
ARGMAX
PROTOCOL
TIMEOUT
```

## 10.2 Execution-flow observability

Record:

```text
input accepted count
Conv1 output count
Conv2 output count
FC output event count
prediction event count
done event count
o_busy behavior
o_valid behavior
completion cycle
```

## 10.3 Protection observability

Later P1/P2/P3 experiments additionally record:

```text
alarm asserted
alarm cycle
corrected
masked
uncorrectable detected
```

---

# 11. Outcome Taxonomy

The main paper-level taxonomy should distinguish numerical and execution failures.

Use the following mutually exclusive final outcome classes.

## 11.1 NONCOMPLETION

Condition:

```text
inference does not complete before TIMEOUT_LIMIT
```

Examples:

```text
deadlock
stalled FSM
lost completion
pipeline no longer progresses
```

## 11.2 PROTOCOL_VIOLATION

Inference may terminate, but the transaction behavior is invalid.

Examples:

```text
missing output
extra output
invalid transaction count
wrong order
unexpected valid/done behavior
```

## 11.3 WRONG_LABEL

Condition:

```text
inference completes
protocol is acceptable
faulty prediction != golden prediction
```

## 11.4 WRONG_LOGITS_SAME_LABEL

Condition:

```text
inference completes
protocol is acceptable
prediction == golden prediction
logits != golden logits
```

## 11.5 FINAL_OUTPUT_MATCH

Condition:

```text
protocol acceptable
completion acceptable
final logits and prediction match golden
```

Internal mismatches may still be logged separately.

---

# 12. Outcome Precedence

To keep each scenario assigned to one final category, use this precedence:

```text
1. NONCOMPLETION
2. PROTOCOL_VIOLATION
3. WRONG_LABEL
4. WRONG_LOGITS_SAME_LABEL
5. FINAL_OUTPUT_MATCH
```

Reason:

An inference that never completes cannot also be classified by final logits.

A protocol-invalid run must not be treated as a normal numerical inference.

---

# 13. Additional Per-Scenario Fields

Recommended result log:

```csv
scenario_id,
sample_id,
target_id,
target_class,
bit_index,
injection_cycle,
golden_digit,
faulty_digit,
completed,
completion_cycle,
latency_cycles,
protocol_ok,
pool1_match,
pool2_match,
logits_match,
first_bad_stage,
missing_events,
extra_events,
outcome,
alarm,
alarm_cycle,
corrected,
masked
```

For P0:

```text
alarm       = N/A
alarm_cycle = N/A
corrected   = N/A
masked      = N/A
```

---

# 14. Timeout Definition

The timeout limit must be fixed before the campaign.

P0 nominal:

```text
1342 cycles start -> done
```

Recommended initial FI timeout:

```text
TIMEOUT_LIMIT = 2 × nominal latency
              = 2684 cycles
```

or a slightly rounded fixed value:

```text
3000 cycles after accepted i_start
```

Choose one and keep it fixed for the entire P0 campaign.

Recommended choice for simplicity:

```text
TIMEOUT_LIMIT = 3000 cycles
```

A scenario that has not completed by this limit is classified as:

```text
NONCOMPLETION
```

---

# 15. Pilot Campaign

The pilot campaign validates methodology, not final statistical conclusions.

## 15.1 Stage A — deterministic smoke tests

Minimum:

```text
CTL : 5 scenarios
BUF : 5 scenarios
MEM : 5 scenarios
DAT : 5 scenarios
```

Total:

```text
20 deterministic scenarios
```

Purpose:

```text
prove all four classes are injectable
prove logs are correct
prove timeout works
prove first_bad_stage works
prove classifications are deterministic
```

## 15.2 Stage B — small randomized pilot

Suggested starting point:

```text
100 scenarios / class
```

Total:

```text
400 fault scenarios
```

Sampling dimensions:

```text
target
bit
cycle
```

Use a recorded seed.

The pilot is used to estimate:

```text
runtime
outcome distribution
masked rate
harmful-event frequency
rare classes
needed full-campaign sample size
```

Do not claim final vulnerability ranking from a tiny pilot.

---

# 16. Sampling Rules

Cross-class comparison must make denominators explicit.

Do not simply compare raw harmful-event counts if the number of possible bits differs greatly between classes.

At minimum report:

```text
N_injected per class
N_harmful per class
harmful_rate = N_harmful / N_injected
```

For later population-aware analysis, also record:

```text
eligible target bits per class
```

Possible analysis levels:

```text
per injected scenario
per target bit
per module
per structural class
```

The paper must clearly state which denominator is used.

---

# 17. Core Reliability Metrics

For class/configuration `k`:

```text
P_noncompletion(k)
  = N_noncompletion(k) / N_total(k)

P_protocol(k)
  = N_protocol_violation(k) / N_total(k)

P_wrong_label(k)
  = N_wrong_label(k) / N_total(k)

P_wrong_logits_same_label(k)
  = N_wrong_logits_same_label(k) / N_total(k)

P_final_match(k)
  = N_final_output_match(k) / N_total(k)
```

Define harmful outcomes explicitly.

Recommended:

```text
harmful =
    NONCOMPLETION
  + PROTOCOL_VIOLATION
  + WRONG_LABEL
```

Keep `WRONG_LOGITS_SAME_LABEL` separate because the prediction is still correct but numerical state is corrupted.

---

# 18. First-Bad-Stage Analysis

For each scenario, compare the nearest available checkpoints in order:

```text
Pool1
  ↓
Pool2
  ↓
FC logits
  ↓
prediction
```

The first checkpoint that differs from golden becomes:

```text
first_bad_stage
```

Control failures may instead be labeled:

```text
PROTOCOL
TIMEOUT
```

This metric is important because final prediction alone can hide internal corruption.

---

# 19. Automation Architecture

Recommended structure:

```text
fi/
├── config/
│   ├── fi_targets.csv
│   └── campaign_config.json
│
├── scenarios/
│   ├── pilot_scenarios.csv
│   └── full_scenarios.csv
│
├── scripts/
│   ├── generate_scenarios.py
│   ├── run_campaign.py
│   ├── parse_results.py
│   └── summarize_campaign.py
│
├── results/
│   ├── raw/
│   ├── parsed/
│   └── summary/
│
└── docs/
    └── FI_SPEC.md
```

---

# 20. Campaign Execution Flow

For each scenario:

```text
load scenario
↓
reset DUT
↓
start inference
↓
feed same golden input
↓
wait until injection_cycle
↓
flip selected bit once
↓
continue normal execution
↓
observe checkpoints / protocol
↓
complete or timeout
↓
classify outcome
↓
append CSV result
```

Do not manually edit RTL between scenarios.

---

# 21. Reproducibility Requirements

Every campaign must record:

```text
campaign_id
date
git commit
baseline tag
Vivado/XSim version
top module
clock
fault model version
target catalog version
scenario file hash or version
random seed
number of scenarios
timeout
```

Recommended baseline tag:

```text
baseline_bitexact_phase1
```

Recommended FI branch:

```text
fi-infrastructure
```

---

# 22. Protection Configurations

Protection is not implemented until the P0 FI infrastructure is validated.

Planned configurations:

## P0 — Baseline

```text
no protection
```

## P1 — Flow/Liveness Monitor

Purpose:

```text
detect abnormal progress / transaction behavior
```

Metrics:

```text
detection coverage
detection latency
false positive rate
resource overhead
```

## P2 — SEC-DED Feature Memory

Purpose:

```text
correct single-bit writable-memory errors
detect double-bit errors
```

Primary targets:

```text
Feature Mem1
Feature Mem2
```

Metrics:

```text
correction rate
residual harmful rate
LUT/FF/BRAM overhead
timing overhead
```

## P3 — Selective Control TMR

Purpose:

```text
mask single control-state upsets
```

Target selection must be based on P0 vulnerability results.

Metrics:

```text
masking rate
residual harmful rate
LUT/FF overhead
timing overhead
```

---

# 23. Reliability vs PPA Comparison

All configurations must use the same:

```text
FPGA target
Vivado version
clock target
XDC methodology
input workload
fault scenario set
timeout
result-classification rules
```

Final comparison table:

| Metric | P0 | P1 | P2 | P3 |
|---|---:|---:|---:|---:|
| Harmful outcome rate | TBD | TBD | TBD | TBD |
| Wrong-label rate | TBD | TBD | TBD | TBD |
| Protocol violation rate | TBD | TBD | TBD | TBD |
| Non-completion rate | TBD | TBD | TBD | TBD |
| Detection coverage | N/A | TBD | TBD | TBD |
| LUT | 2566 | TBD | TBD | TBD |
| FF | 813 | TBD | TBD | TBD |
| DSP | 0 | TBD | TBD | TBD |
| BRAM | 0 | TBD | TBD | TBD |
| WNS @ 100 MHz | +0.466 ns | TBD | TBD | TBD |
| Latency | 1342 cyc | TBD | TBD | TBD |
| II | 1343 cyc | TBD | TBD | TBD |
| Throughput | 74460.16 img/s | TBD | TBD | TBD |

---

# 24. Acceptance Criteria Before Full Campaign

The FI framework is ready for scale only when all of the following PASS.

## A. Baseline equivalence

```text
FI disabled == P0
PASS
```

## B. Deterministic injection

```text
known target
known bit
known cycle
waveform proves correct flip
PASS
```

## C. No unintended injector corruption

```text
only intended target modified by FI command
PASS
```

## D. Reproducibility

```text
same scenario
same result
repeated runs
PASS
```

## E. Timeout

```text
known non-completing scenario
classified NONCOMPLETION
PASS
```

## F. Protocol checker

```text
known invalid-event scenario
classified PROTOCOL_VIOLATION
PASS
```

## G. Numerical classification

At least one scenario demonstrating:

```text
FINAL_OUTPUT_MATCH
WRONG_LOGITS_SAME_LABEL or numerical mismatch
```

If a pilot naturally produces `WRONG_LABEL`, record it; do not artificially tune experiments only to force desired research results.

---

# 25. First Implementation Milestone

Do this before any random campaign.

Recommended first target:

```text
a simple control counter / FSM bit
```

Why:

```text
easy to locate
easy to flip
easy to observe in waveform
easy to verify cycle accuracy
```

First milestone:

```text
Scenario FI_SMOKE_0001

target       = one CTL register
bit          = one selected bit
cycle        = fixed known cycle
fault count  = 1
input        = current verified MNIST sample
```

Required evidence:

```text
waveform before injection
waveform at injection
waveform after injection
final outcome
scenario CSV row
result CSV row
```

---

# 26. Recommended Development Order

```text
DONE
----
P0 bit-exact RTL
P0 full-top verification
P0 synthesis
P0 implementation
P0 timing @ 100 MHz
P0 latency
P0 back-to-back throughput

NEXT
----
1. tag baseline_bitexact_phase1
2. create fi-infrastructure branch
3. create fi_targets.csv
4. select first CTL target
5. implement deterministic injection task
6. verify waveform of one bit flip
7. prove FI-disabled equivalence
8. implement timeout + outcome classifier
9. implement CSV scenario/result logger
10. run 20-scenario deterministic smoke test
11. run 400-scenario pilot
12. inspect class distributions
13. choose full-campaign sample size
14. run P0 full campaign
15. analyze vulnerability
16. select P1/P2/P3 targets
17. implement protection
18. replay identical scenario set
19. synthesize P1/P2/P3
20. reliability vs PPA analysis
```

---

# 27. Research Interpretation Rules

Do not assume:

```text
CTL is always most vulnerable
MEM faults are always harmful
DAT faults always change prediction
TMR is always the best protection
```

Those are experimental questions.

Report:

```text
what was injected
where it was injected
how it was sampled
what denominator was used
what outcome was observed
```

Keep these concepts separate:

```text
Detection != Correction != Masking
```

A monitor that detects a failure does not automatically correct it.

A fault that changes internal data but not the final result is not equivalent to a fault that never propagated.

---

# 28. Immediate Next Task

The immediate coding task is:

```text
create first deterministic CTL injection
```

Recommended workflow:

```text
choose target
→ record exact hierarchical signal
→ choose bit
→ choose fixed cycle
→ create FI task
→ simulate
→ inspect waveform
→ log outcome
```

Do not begin P1/P2/P3 yet.

---

# 29. Current Research State

```text
PHASE 1
=======
bit-exact RTL         COMPLETE
full top verification PASS
synthesis             PASS
implementation        PASS
timing @ 100 MHz      PASS
latency               MEASURED
throughput            MEASURED

PHASE 2
=======
FI specification      DEFINED
target catalog        NEXT
deterministic FI      NEXT
pilot campaign        PENDING
full campaign         PENDING

PHASE 3
=======
selective protection  NOT STARTED
```

**Exact continuation point:** create the target catalog and demonstrate one deterministic transient bit flip on a CTL state element while preserving P0 behavior when FI is disabled.

# CNN_PEP / CNN MNIST Q4.4 — Project Progress, Locked P0 Baseline & AI Handoff

> **Updated:** 2026-11-03 (Asia/Ho_Chi_Minh)  
> **Project/repository:** `cnn-mnist-q44-rtl` — CNN_PEP RTL fault-injection research  
> **Primary implementation:** Verilog RTL; Python bit-exact golden model; AMD/Xilinx Vivado/XSim 2022.2  
> **Target:** AMD/Xilinx Kria K26 (KR260 workflow), `xck26-sfvc784-2LV-c`  
> **Status:** **Phase 1 (P0) LOCKED**; Phase 2 FI infrastructure **four-class deterministic smoke + parser PASS**; automatic XSim campaign runner **NOT YET VERIFIED**.  
> **Source of this update:** earlier `PROJECT_PROGRESS` versions, `FI_SPEC.md`, uploaded RTL/Python/HEX source, and user-provided P0 implementation / simulation and four-class XSim + Python logs. Claims are qualified by the evidence level indicated below.

## 0. START HERE — instructions for the next AI / engineer

1. **Do not redesign, reorder, change numerical rules, or optimize P0 RTL.** It is the bit-exact baseline for all later P1/P2/P3 experiments. Fix P0 only for an independently demonstrated functional defect, with complete golden regression.
2. Read **this file, `fi/docs/FI_SPEC.md` (or wherever the current FI spec is stored), `rtl/.../cnn_mnist_top.v`, `top_fsm.v`, `python/golden_model.py`, and the four FI smoke testbenches**. Confirm actual paths using the repository tree; paths below are logical examples where directory placement has not been rechecked.
3. **Evidence already achieved:** standalone top-level golden test 2 frames / **1,808 PASS checks**; 3-frame back-to-back throughput; 4 separate FI smoke tests in CTL, BUF, MEM, DAT; Python parser verified all four in one CSV; Python summary completed.
4. **Exact continuation point:** validate/automate the **Vivado/XSim 4-class replay runner**, preserving all four known-PASS tests; then broaden to a **20-scenario directed matrix (5/class)**. A `run_four_class_smoke.py` draft was prepared with static/dry-run PASS, but **XSim batch execution remains pending**. Do not claim the 400-run randomized pilot or P0 full-campaign data exists.
5. Distinguish **mechanism PASS** (fault applied, observed, classified correctly) from **safe inference**. A correct test can output `PROTOCOL_VIOLATION` or `WRONG_LOGITS_SAME_LABEL`.
6. Do not report **25% harmful** from these intentionally selected four scenarios as an unbiased population vulnerability estimate. Likewise, `CTL=100%` here has N=1.
7. This document **does not include an independently verified Git commit hash**, frozen baseline tag, complete routed utilization report, per-run PPA manifest, or an automated XSim runner PASS. Record these when available rather than inventing them.

## 1. Research scope and phase boundaries

- **Phase 1 / P0:** Verified, bit-exact Q4.4 MNIST CNN RTL. Purpose: stable *fault-free* numerical, protocol, timing, latency, throughput, and PPA reference; not necessarily an optimized production accelerator.
- **Phase 2:** RTL simulation-based *transient single-bit state upset* fault injection (FI) across **CTL / BUF / MEM / DAT**, with deterministic replay, goldens, execution/protocol observability, and statistically defensible denominators. The four-class mechanism is demonstrated at one representative scenario per class; the generic runner and broad population campaign are not yet verified.
- **Phase 3 / protections (planned, NOT implemented):** P1 flow/liveness monitor; P2 SEC-DED for writable feature memories; P3 selectively triplicated control (TMR). Preserve identical workloads/scenarios/methodology for P0 vs protected configurations, then compare harmful rate/detection/correction/masking against PPA/latency/throughput overhead. Protection order and targets remain experimental decisions.

## 2. Locked CNN model, architecture and flow

```text
MNIST test image: 1 x 28 x 28 = 784 signed INT8 Q4.4 pixels
   |
   v
Conv1: Cin=1, Cout=4, kernel=3x3, stride=1, padding=0
    raw: 4 x 26 x 26 = 2704 signed INT32 Q8.8
   -> round-to-nearest-even >>4 -> sat INT8 -> ReLU
    quantized: 4 x 26 x 26 INT8
   -> Pool1: 2x2 max, stride 2: 4 x 13 x 13 = 676 INT8
   -> pool1_reorder: spatial-major -> canonical channel-major
   -> feature_mem1: 169 x 32-bit = 676 INT8
   -> synchronous sequential replay
   -> conv2_buf: capture 676 canonical bytes; 121 valid 3x3x4 windows
   -> Conv2: Cin=4, Cout=8, kernel=3x3, stride=1, padding=0
    raw: 8 x 11 x 11 = 968 signed INT32 Q8.8
   -> round-to-nearest-even >>4 -> sat INT8 -> ReLU
   -> Pool2: 2x2 max, stride 2: 8 x 5 x 5 = 200 INT8
   -> pool2_reorder: spatial-major -> canonical channel-major
   -> feature_mem2: 25 x 64-bit = 200 INT8
   -> synchronous sequential replay
   -> Fully Connected: 200 features -> 10 signed INT32 Q8.8 raw logits
   -> signed ArgMax, strict '>' and lowest class index on tie
   -> o_digit[3:0], o_valid / o_done

Top control FSM: IDLE(000) -> CONV1(001) -> CONV2(010)
               -> FC(011) -> ARGMAX(100) -> DONE(101) -> IDLE(000)
```

**The *actual top-level instance names* used in FI:** `dut.u_top_fsm`, `dut.u_conv1_layer`, `dut.u_feature_mem1`, `dut.u_conv2_layer`, `dut.u_feature_mem2`, `dut.u_fc_rom`, `dut.u_fully_connected`, `dut.u_argmax`. Within Conv1/Conv2, inspect actual submodule hierarchy before injecting; e.g. `dut.u_conv2_layer.u_conv2_buf` and `dut.u_conv1_layer.u_conv1_buf` are source-confirmed.

### 2.1 Numerical contract — DO NOT CHANGE

| Property | Locked rule |
|---|---|
| Input, weight, bias, intermediate feature | signed INT8 Q4.4, scale=16, range [-128,127] |
| INT8 x INT8 product | signed Q8.8 |
| Conv/FC accumulator | signed INT32 Q8.8 |
| Bias alignment | sign-extend INT8 bias, then arithmetic `<<< 4` |
| Conv requantization | round-to-nearest-even of signed INT32, then arithmetic `>> 4` |
| Saturation / activation | clip to signed INT8 range, **then** ReLU |
| Pool1/Pool2 | signed per-channel max; kernel 2x2, stride 2 |
| FC output | 10 raw signed INT32 Q8.8 logits; **no output requantization** |
| ArgMax | signed strict `>`; ties retain first/lowest index |
| Tensor serialization | canonical `[channel][y][x]`, index `c*(H*W)+y*W+x` |
| Bus packing | first/lowest byte into bits `[7:0]`, next `[15:8]`, etc. |
| Cross-channel packing | **NO padding** at boundaries (e.g. 25 bytes/channel in Mem2) |
| Reset | active-low **synchronous** reset on sequential P0 logic |
| Input protocol | one valid pixel accepted on a rising edge; `i_valid` gaps legal; `i_start` accepted when FSM is IDLE |

**Key historical bug to avoid:** `pool1_reorder` uses a canonical temporary `reorder_mem[0:675]` and direct byte drain. The earlier channel-array/combinational `read_byte()` scheme produced an unknown first-frame word. Do not regress to it. Canonical **word 42** crosses ch0/ch1 boundary and was verified as expected `0x121110A8`.

### 2.2 Module/unit verification inherited from earlier progress

The earlier project progress documented these separately tested modules/checkpoints (older intermediate 'NEXT: feature_mem1' notes are obsolete):

| Block/checkpoint | Earlier verified evidence | Current interpretation |
|---|---|---|
| `round_shift_even.v` | positive, negative, half-even boundary cases PASS | foundational arithmetic |
| `sat_int8.v`, `relu_int8.v` | signed saturation / ReLU tests PASS | foundational arithmetic |
| `conv1_buf.v` | 676/676 3x3 windows per frame; two frames PASS | 72-bit window |
| `conv1_calc.v` | 2704/2704 raw accumulators bit-exact PASS | 4 x INT32 outputs, 128-bit |
| `conv1_quant_relu.v` | 2704/2704 values bit-exact PASS | 4 x INT8, 32-bit |
| `pool1_buf.v` | 169/169 2x2 windows, two frames PASS | 128-bit pooling window |
| `pool1.v` | 676 pooled INT8 values bit-exact PASS | 169 spatial words |
| `pool1_reorder.v` | 169 words/frame, two frames PASS; boundary word 42 PASS | canonical bytes/packing |
| Feature Mem1, Conv2, Pool2, Feature Mem2, FC, ArgMax, Top FSM | full-top 2-frame golden test PASS, FI-disabled runs PASS | integrated behavior established; **do not infer every distinct unit TB passed unless its log exists** |

### 2.3 Golden source, dataset identity, HEX contracts

- `python/train_model_python.py`: PyTorch training; its `quantized_forward()` is an **approximate float-based** Q4.4 estimate, **not** the RTL bit-exact reference.
- `python/golden_model.py`: sole numerical source of truth (integer Q8.8 accumulation, round-to-nearest-even, saturation/ReLU, FC raw logits, signed ArgMax).
- `python/export_hex.py`: exports weights, biases, image and all golden checkpoints. Earlier uploaded source uses `SAMPLE_INDEX=3`; campaign label is `IMG0000` (these are **different naming schemes**). Record explicit mapping and hashes for larger campaigns. The test image's golden predicted digit is **0**.

| Data file under `data/` | Lines | Format / role |
|---|---:|---|
| `input.hex` | 784 | INT8 pixel input (2 hex digits/line) |
| `conv1_weight.hex`, `conv1_bias.hex` | 36, 4 | INT8 ROM parameters |
| `conv2_weight.hex`, `conv2_bias.hex` | 288, 8 | INT8 ROM parameters |
| `fc_weight.hex`, `fc_bias.hex` | 2000, 10 | INT8 ROM parameters |
| `golden_conv1.hex` | 2704 | signed INT32 Q8.8 raw accumulators |
| `golden_relu1.hex` | 2704 | INT8 quant/ReLU |
| `golden_pool1.hex` | 676 | INT8 canonical Pool1 |
| `golden_conv2.hex` | 968 | signed INT32 Q8.8 raw accumulators |
| `golden_relu2.hex` | 968 | INT8 quant/ReLU |
| `golden_pool2.hex` | 200 | INT8 canonical Pool2 |
| `golden.hex` | 10 | signed INT32 Q8.8 final logits (8 hex digits/line) |
| `prediction.hex` | 1 | prediction=0 |
| `label.hex` | 1 | dataset label (do not confuse with predicted label) |

**Golden logits for current IMG0000 (raw signed INT32 Q8.8, classes 0..9):**

```text
[2758, -5029, -391, -2184, -1498, -1216, -930, -231, -921, -818]
argmax = 0
```

The files that were previously uploaded can expire in an AI conversation; the Git working tree and permanent `data/` folder, not chat attachments, should be treated as the reproducibility source.

## 3. P0: LOCKED functional, performance and PPA baseline

### 3.1 Full-top bit-exact / protocol evidence

```text
CNN_MNIST_TOP TEST: PASS
Frames tested       : 2
Input pixels total  : 1568
Conv1 words total   : 338   (=169 per frame)
Conv2 words total   : 50    (=25 per frame)
FC output events    : 2
Prediction events   : 2
Expected digit      : 0
Final DUT digit     : 0
Total checks        : 1808
Errors              : 0
```

- Nominal frame continuous valid: **1342 cycles**, **13.420 us** @100 MHz; final post-input latency **557 cycles**, **5.570 us**.
- A separate frame with deliberate input-valid gaps: end-to-end **1409 cycles**, **14.090 us**, exactly **67 cycles longer**; post-input latency remained **557 cycles**. This is evidence that gaps extend acquisition rather than the downstream tail for this measured workload.
- Back-to-back 3-frame throughput regression: **22 checks PASS**, 3/3 digit 0, no overlap between inferences.

| Frame | Accepted start cycle | Done cycle | Latency (cycles) |
|---|---:|---:|---:|
| 0 | 3 | 1345 | 1342 |
| 1 | 1346 | 2688 | 1342 |
| 2 | 2689 | 4031 | 1342 |

| Metric | **P0 locked value** | Derivation/meaning |
|---|---:|---|
| Target clock | **100 MHz** | Vivado 10.000 ns period |
| Clock period | **10.000 ns** | constraint |
| Start-to-done latency | **1342 cycles** | all three back-to-back frames |
| Start-to-done time | **13.420 us** | cycles x 10 ns |
| Start-to-start II | **1343 cycles** | accepted starts 3,1346,2689 |
| Initiation interval time | **13.430 us** | II x 10 ns |
| **Sustained non-overlapped throughput** | **74,460.16 images/s** | 100,000,000 / 1343 |
| Post-input latency | **557 cycles / 5.570 us** | golden nominal input timing |
| Single-frame equivalent throughput | **74,515.65 images/s** | 100 MHz / 1342, **NOT** sustained throughput |

**Interpretation:** II=1343 instead of latency=1342 because next `i_start` is accepted at the next available idle opportunity. The sustained throughput statement is supported by actual accepted-start spacing, not just an estimate from one frame.

### 3.2 Post-placement utilization + routed timing, Vivado 2022.2

**Device:** `xck26-sfvc784-2LV-c` (Kria K26/KR260 flow). The utilization counts below were observed from a **fully-placed report**; timing values are from a **routed report**. Use a *post-route utilization report* if available to make final PPA table report stages exactly consistent. Do not mislabel placed utilization as independently verified post-route utilization.

| PPA metric | **P0 value** | Comments |
|---|---:|---|
| LUT | **2,566** | **2.19%** of 117,120 (placed) |
| FF | **813** | **0.35%** of 234,240 (placed) |
| DSP | **0** | no DSP primitives in this implementation |
| BRAM | **0** | no BRAM primitives |
| URAM | **0** | no URAM primitives |
| CARRY8 | **560** | ~3.83% |
| CLB | **676** | ~4.62% |
| BUFGCE | **1** | one global clock buffer |
| I/O | **11** | placed port usage |
| WNS (setup, routed) | **+0.466 ns** | timing met at 100 MHz |
| TNS (routed) | **0.000 ns** | no negative setup slack |
| Failing setup endpoints | **0** | routed report |
| WHS (hold, routed) | **+0.059 ns** | hold met |
| THS (hold, routed) | **0.000 ns** | hold total |
| Clock domain | **single `sys_clk`** | 1 BUFGCE, no MMCM/PLL in report |
| **Timing status** | **MET @ 100 MHz** | internal constrained timing |

Other previously observed synthesis LUT count: **3,601 LUT**. Synthesis vs placed counts can differ due to mapping/optimization; do not combine them into one stage's table.

**Critical path, routed:** `u_fully_connected/o_logits_reg[43]` -> `u_argmax/o_digit_reg[2]/R`, data-path delay **9.278 ns**, logic **3.332 ns**, routing **5.946 ns** (about 64% routing), **24 logic levels**.

**Important timing qualification:** Vivado reported `TIMING-18` missing input/output delays (3 input ports and 7 output ports lacked delays in the cited report). The positive WNS verifies implemented constrained internal timing at 100 MHz, **not** full board-level I/O timing. Use the same XDC/methodology in P0/P1/P2/P3 comparisons. **Do not claim measured Fmax** (the rough 104.9 MHz arithmetic extrapolation from slack is NOT an implementation-backed Fmax sweep). Also **power has not been measured/reported as a locked metric**.

### 3.3 Freeze/reproducibility gate

- Baseline tag suggested in FI spec: `baseline_bitexact_phase1`, **not confirmed created**; FI branch suggestion: `fi-infrastructure`, **not confirmed**.
- Collect `git rev-parse HEAD` for P0 and current FI, `vivado -version`, device/part, project XDC hash, ROM/input/golden SHA256, report timestamps, and exact testbench files. The current numerical/PPA values are user-provided observed reports/logs; raw `.rpt` files have not been bundled into this handoff.
- Keep raw evidence of the 1808-check test and the 3-frame throughput test; the deduplicated throughput TB has **22 checks**, not 1808.

## 4. Phase 2 — fault model, catalog, outcome definitions

### 4.1 Primary model

```text
fault_type            = SEU_SINGLE_BIT
faults_per_inference  = 1
injection_target      = selected RTL *state* bit
injection_operation   = exactly one XOR bit flip / deterministic state deposit
control_pulse         = one injection event (not permanent force)
corrupted state life  = persists until natural overwrite / state update
same image/weights/RTL/clock/golden methodology per compared scenario
```

**NOT studied in P0 FI core:** gate-level SEE, irradiation, FPGA configuration SRAM faults, permanent stuck-at, multi-bit simultaneous faults, voltage glitching, hardware board FI. `force/release` for a single clock is NOT assumed equivalent to persisted stored upset without a proof of correct lifetime.

### 4.2 Structural target taxonomy

| Class | Meaning | Representative target(s) |
|---|---|---|
| CTL | registered FSM, pointers, counters, launch/done/protocol state | `dut.u_top_fsm.state` |
| BUF | streaming line/window storage and reorder/capture buffers | `dut.u_conv2_layer.u_conv2_buf.feature_mem[0]` |
| MEM | writable intermediate feature storage | `dut.u_feature_mem1.mem[0]`, `dut.u_feature_mem2.mem[...]` |
| DAT | registered arithmetic values, accumulators, result logits | `dut.u_fully_connected.o_logits`, `.accumulator[...]` |

Catalogs: `fi/config/fi_targets.csv` = early 9-entry CTL draft; **`fi/config/fi_targets_candidates_v2.csv` = 80 candidate entries**: **31 CTL, 33 BUF, 6 MEM, 10 DAT**. *These are representative target entries, NOT 80 physical flip-flops, NOT full RTL population, and NOT 80 proven XSim injectable fault sites.* `target_id` names MUST match CSV. The first four smoke target IDs are listed below.

FPGA synthesizer may re-encode FSM or merge identical output registers; avoid equating RTL state bits to physical SEU sites. ROM weights/bias are outside the primary *writable-state* MEM campaign unless a separate fault model is explicitly defined.

### 4.3 Five mutually exclusive final outcomes and precedence

1. `NONCOMPLETION` — inference not completed by frozen timeout (**initial spec recommendation: 3000 cycles from accepted start**).
2. `PROTOCOL_VIOLATION` — completes or signals done, but required valid/done/transaction ordering/counts are wrong.
3. `WRONG_LABEL` — protocol valid and completed, prediction differs from golden.
4. `WRONG_LOGITS_SAME_LABEL` — protocol valid, digit same, logits differ.
5. `FINAL_OUTPUT_MATCH` — protocol valid, final digit and logits match golden; **internal mismatches/read corruption may still exist and must be logged separately**.

**Precedence** is exactly the order above. `first_bad_stage` may be `POOL1`, `POOL2`, `FC`, `ARGMAX`, `PROTOCOL`, `TIMEOUT`, or `NONE`; stage tagging does not replace final outcome. The initial *harmful* indicator is only `NONCOMPLETION + PROTOCOL_VIOLATION + WRONG_LABEL` (wrong logits with same label is distinct, not in this numerator). Detection, correction, and masking are separate concepts.

## 5. VERIFIED Phase 2: four deterministic smoke tests

**All four tests ran on the user's Vivado 2022.2 XSim environment.** In each case a *fault-free control run* PASSed, including **1342-cycle latency**, **676 Pool1 values**, **200 Pool2 values**, **10 FC logits**, and prediction **0**. Each faulted inference logged exactly one injected bit and one `FI_RESULT_V2` line. The 4 scenario logs were then validated by `parse_fi_v2.py` and summarized by `summarize_campaign.py`.

### 5.1 Four regression records (LOCK THESE)

| Class | Scenario / target ID | Actual hierarchy / bit | Event cycles | Final outcome | Mechanism |
|---|---|---|---|---|---|
| **CTL** | `FI_CTL_0005` / `CTL_0001` | `dut.u_top_fsm.state[2]` | start=3; inject=128 (offset=125); premature DONE=128 | `PROTOCOL_VIOLATION`; stage `PROTOCOL` | PASS |
| **MEM** | `FI_MEM1_0001` / `MEM_0001` | `dut.u_feature_mem1.mem[0][0]` | write=796; inject=797 (offset=794); read=966 | `FINAL_OUTPUT_MATCH`; stage `NONE` | PASS |
| **BUF** | `FI_BUF_0001` / `BUF_0030` | `dut.u_conv2_layer.u_conv2_buf.feature_mem[0][0]` | write=967; inject=968 (offset=965); emit=1137; consume=1138 | `FINAL_OUTPUT_MATCH`; stage `NONE` | PASS |
| **DAT** | `FI_DAT_0001` / `DAT_0010` | `dut.u_fully_connected.o_logits[0]` | capture=1342; inject=1343 (offset=1340); ArgMax consume=1343 | `WRONG_LOGITS_SAME_LABEL`; stage `FC` | PASS |

#### CTL evidence

```text
state before = 001 (ST_CONV1)
flip bit[2] = 0 -> 1 -> 101 (ST_DONE)
FIRST DONE at cycle 128, only 124/784 pixels had been driven at that moment
Conv2 / FC / ArgMax not completed before early DONE
OUTCOME=PROTOCOL_VIOLATION
```

Be precise: V2 final `FI_COUNTS` has **784 driven and 169 Pool1 words** because the input driver/Conv1 continued after premature top FSM done. `first_done_pixels=124` is the early-completion evidence. Final counts are not counts *at* the first done event.

#### MEM evidence

```text
write mem1[0] at 796, original=0x00000000
inject bit 0 at 797 -> 0x00000001
read at 966: stored=0x00000001, read=0x00000001
persisted 966-797 = 169 cycles (1.69 us @100MHz)
full inference finishes at cycle 1345
final logits and prediction still match golden
OUTCOME=FINAL_OUTPUT_MATCH
```

This proves retention **until the observed first read**, not proof of retention through every future overwrite; the consumer may mask the effect.

#### BUF evidence

```text
conv2_buf.feature_mem[0] write at 967: 0x00
inject bit 0 at 968 -> 0x01
stored byte and 288-bit Conv2 window byte0 = 0x01 at 1137
Conv2 calculator samples 0x01 with i_valid=1 at 1138
retention from injection to window emission = 169 cycles
final logits and prediction still match golden
OUTCOME=FINAL_OUTPUT_MATCH
```

Interpretation: injection, stored lifetime, window propagation, and datapath consumer were all observed; **masking cause** (e.g. arithmetic/quantization/etc.) has not been isolated. `NONE` is *first bad checkpoint* not absence of internal corruption.

#### DAT evidence

```text
FC captures logits at cycle 1342
class-0 logit: golden 2758 = 0x00000AC6
flip low bit at cycle 1343 -> 2759 = 0x00000AC7
ArgMax consumes faulty logit at 1343, still predicts 0
OUTCOME=WRONG_LOGITS_SAME_LABEL; FIRST_BAD_STAGE=FC
```

Injection and consumption are both numbered cycle 1343; this pass was observed on XSim, but for future generalized DAT injections ensure simulator scheduling prevents an injection/sampling race (verify pre/post edge and nonblocking assignment timing explicitly).

### 5.2 Python parser + summary — ACTUALLY EXECUTED

User's PowerShell output:

```text
python fi/scripts/parse_fi_v2.py --logs-dir fi/results/raw/logs \
  --scenarios fi/scenarios/four_class_smoke.csv \
  --out fi/results/parsed/four_class_smoke_results.csv
Validated 4 verified FI scenarios -> fi\results\parsed\four_class_smoke_results.csv

python fi/scripts/summarize_campaign.py \
  --input fi/results/parsed/four_class_smoke_results.csv \
  --output fi/results/summary/four_class_smoke_summary.txt
Total scenarios: 4
PROTOCOL_VIOLATION             1  (25%)
WRONG_LOGITS_SAME_LABEL        1  (25%)
FINAL_OUTPUT_MATCH             2  (50%)
Harmful outcomes               1/4 = 25%
```

Per-class harmful fractions in **this tiny intentional smoke set**: CTL 1/1; MEM 0/1; BUF 0/1; DAT 0/1. **These are not reliability/vulnerability estimates.** The 5 earlier CTL timing-sweep cases (`pilot_scenarios.csv`, `results.csv`, `campaign_summary.txt`) are a separate early deterministic CTL-only experiment (5/5 protocol violations), **not** the 4-class summary and **not** a randomized pilot.

**Four currently validated scenario CSV rows:**

```csv
scenario_id,sample_id,target_id,target_class,bit_index,injection_offset
FI_CTL_0005,IMG0000,CTL_0001,CTL,2,125
FI_MEM1_0001,IMG0000,MEM_0001,MEM,0,794
FI_BUF_0001,IMG0000,BUF_0030,BUF,0,965
FI_DAT_0001,IMG0000,DAT_0010,DAT,0,1340
```

`FI_RESULT_V2` **26 fields** are parsed by `fi/scripts/parse_fi_v2.py`: `scenario_id,sample_id,target_id,target_class,bit_index,injection_offset,requested_cycle,actual_cycle,fault_events,injection_ok,completion_cycle,first_done_pixels,pixels_driven,pixels_accepted,pool1_words,pool2_words,fc_valid_count,argmax_count,done_count,protocol_ok,pool1_mismatch,pool2_mismatch,logits_mismatch,prediction,first_bad_stage,outcome`.

## 6. Current repository / test artifacts and which are necessary

Root shown below is an **illustrative logical structure** assembled from the user's VS Code tree and uploaded source names. Run `git ls-files`, `Get-ChildItem -Recurse`, or `report_compile_order -fileset sources_1` to identify exact live paths before editing. Several Vivado simulation TBs were stored in a **separate `Simulation.srcs/sim_1/new/` project**, so do not assume they are already committed in `cnn-mnist-q44-rtl`.

```text
cnn-mnist-q44-rtl/
├── PROJECT_PROGRESS.md                      # THIS handoff; place at repo root
├── rtl/                                     # inspect actual source subfolders
│   ├── .../cnn_mnist_top.v, top_fsm.v
│   ├── .../conv1_buf.v, conv1_calc.v, conv1_quant_relu.v
│   ├── .../pool1_buf.v, pool1.v, pool1_reorder.v, conv1_layer.v
│   ├── .../feature_mem1.v
│   ├── .../conv2_buf.v, conv2_calc.v, conv2_quant_relu.v
│   ├── .../pool2_buf.v, pool2.v, pool2_reorder.v, conv2_layer.v
│   ├── .../feature_mem2.v, fully_connected.v, argmax.v
│   └── .../conv1_rom.v, conv2_rom.v, fc_rom.v, common/*.v
├── python/
│   ├── golden_model.py                      # exact fixed-point reference
│   ├── export_hex.py                         # inputs, weights, goldens
│   └── train_model_python.py                 # training / approximate evaluation
├── data/                                    # test input, ROM weights/bias, golden tensors
├── fi/
│   ├── config/
│   │   ├── fi_targets.csv                   # 9 CTL early catalog
│   │   ├── fi_targets_candidates_v2.csv     # 80 entries (31/33/6/10)
│   │   └── campaign_config.json             # inspect contents/version before using
│   ├── docs/                                # store FI_SPEC.md here if project convention
│   ├── scenarios/
│   │   ├── pilot_scenarios.csv              # EARLY 5 CTL cases; NOT randomized 400 pilot
│   │   ├── full_scenarios.csv               # previous header-only placeholder
│   │   ├── dat_smoke.csv                    # one verified DAT record
│   │   └── four_class_smoke.csv             # four locked regression scenarios
│   ├── results/
│   │   ├── raw/results.csv                  # earlier CTL pilot results, separate
│   │   ├── raw/logs/
│   │   │   ├── FI_CTL_0005.log
│   │   │   ├── FI_MEM1_0001.log
│   │   │   ├── FI_BUF_0001.log
│   │   │   └── FI_DAT_0001.log
│   │   ├── parsed/results_normalized.csv    # earlier CTL pilot results
│   │   ├── parsed/four_class_smoke_results.csv
│   │   └── summary/four_class_smoke_summary.txt
│   └── scripts/
│       ├── generate_fi_v2.py               # generates static Verilog TB dispatcher, NOT random scenarios
│       ├── parse_fi_v2.py                  # validated unified parser
│       ├── run_campaign.py                 # runner stage NOT verified end-to-end
│       └── summarize_campaign.py           # verified on 4-class CSV
└── (Vivado simulation project, possibly outside repo)
    ├── tb_cnn_mnist_fi_campaign_v2.v         # CTL generic V2 TB
    ├── tb_cnn_mnist_fi_mem1_smoke.v
    ├── tb_cnn_mnist_fi_buf_smoke.v
    ├── tb_cnn_mnist_fi_dat_smoke.v
    ├── tb_cnn_mnist_top.v                   # deduplicated throughput version
    └── original full-top 1808-check TB      # locate preserved PASS version
```

**Required to reproduce FI four-class regression:** current **exact P0 RTL**, all referenced ROM weight/bias HEX, input and golden HEX, XSim project & constraints, four testbenches above, four scenarios, per-scenario logs, current parser+summarizer, and accurate simulator compile-order/top settings. The four smoke TBs are not a substitute for the original full-top 1808-check testbench.

**File locations / common mistakes:**
- Run `python fi/scripts/...` from **repository root**, not from `fi/results`, or `fi/results/fi/results/...` will be accidentally created.
- On PowerShell, `Set-Content -Encoding ascii` works for the all-ASCII CSV; in Python read CSV with `utf-8-sig` to accept UTF-8 BOM variants. `empty scenarios file` can result from empty/incorrect content or wrong file/header; inspect the actual CSV before changing RTL.
- Avoid double extension names like `parse_fi_v2.py.py`.
- Log files are one scenario each, e.g. `fi/results/raw/logs/FI_DAT_0001.log`, with **one** `FI_RESULT_V2` record and PASS markers. The `.log` pattern may be ignored by `.gitignore`; commit/retain raw evidence appropriately.
- Legacy `tb_cnn_mnist_top.v` upload contained two duplicate `tb_cnn_mnist` modules, later deduplicated; three-frame throughput regression passed afterward. Do not add both versions under one module name.
- Simulator may use absolute Windows hex paths (`D:/Share_Data_ubuntu_window/NKKH/data/...`) and a separate `Simulation` project; these must be resolved on a new machine. Never silently change RTL ROM numerical contents.

## 7. Exact Vivado Tcl and Python replay commands

Run Tcl in Vivado 2022.2's **Tcl Console** with the correct project, compiled RTL and all four TB files in **Simulation Sources**. Each block is a separate simulation (different top):

```tcl
# CTL: FI_CTL_0005
close_sim -force
set_property top tb_cnn_mnist_fi_campaign_v2 [get_filesets sim_1]
set_property -name {xsim.simulate.xsim.more_options} -value {-testplusarg FI_OFFSET=125 -testplusarg SCENARIO_ID=FI_CTL_0005 -testplusarg TARGET_ID=CTL_0001 -testplusarg FI_BIT=2 -testplusarg SAMPLE_ID=IMG0000} -objects [get_filesets sim_1]
launch_simulation
run all
```

```tcl
# MEM: FI_MEM1_0001
close_sim -force
set_property top tb_cnn_mnist_fi_mem1_smoke [get_filesets sim_1]
set_property -name {xsim.simulate.xsim.more_options} -value {-testplusarg MEM1_AUTO=1 -testplusarg SCENARIO_ID=FI_MEM1_0001 -testplusarg TARGET_ID=MEM_0001 -testplusarg FI_BIT=0 -testplusarg SAMPLE_ID=IMG0000} -objects [get_filesets sim_1]
launch_simulation
run all
```

```tcl
# BUF: FI_BUF_0001
close_sim -force
set_property top tb_cnn_mnist_fi_buf_smoke [get_filesets sim_1]
set_property -name {xsim.simulate.xsim.more_options} -value {-testplusarg BUF_AUTO=1 -testplusarg SCENARIO_ID=FI_BUF_0001 -testplusarg TARGET_ID=BUF_0030 -testplusarg FI_BIT=0 -testplusarg SAMPLE_ID=IMG0000} -objects [get_filesets sim_1]
launch_simulation
run all
```

```tcl
# DAT: FI_DAT_0001
close_sim -force
set_property top tb_cnn_mnist_fi_dat_smoke [get_filesets sim_1]
set_property -name {xsim.simulate.xsim.more_options} -value {-testplusarg DAT_AUTO=1 -testplusarg SCENARIO_ID=FI_DAT_0001 -testplusarg TARGET_ID=DAT_0010 -testplusarg FI_BIT=0 -testplusarg SAMPLE_ID=IMG0000} -objects [get_filesets sim_1]
launch_simulation
run all
```

**The *_AUTO=1 testbenches discover event-triggered offsets; the verified offsets were MEM=794, BUF=965, DAT=1340. They are NOT equivalent to assigning these offsets to the older CTL-only smoke TB.**

Run from repo root in **PowerShell** once the four `.log` files exist:

```powershell
python fi/scripts/parse_fi_v2.py `
  --logs-dir fi/results/raw/logs `
  --scenarios fi/scenarios/four_class_smoke.csv `
  --out fi/results/parsed/four_class_smoke_results.csv

python fi/scripts/summarize_campaign.py `
  --input fi/results/parsed/four_class_smoke_results.csv `
  --output fi/results/summary/four_class_smoke_summary.txt
```

Previously verified user console messages: `Validated 4 verified FI scenarios` and summary `Total scenarios: 4`, 1 protocol violation, 1 wrong logits same label, 2 final matches. **This does not prove automated execution of Vivado from Python.**

## 8. NEXT STEP — automate XSim replay; then 20 directed scenarios

### Gate A — 4-class automated runner (NEXT, pending actual Vivado run)

A separate handoff artifact `run_four_class_smoke.py` and `AUTOMATED_RUNNER_README.md` has been prepared (not known to be copied to Git). Its Python syntax check, 2-replay Tcl-generation dry-run and invalid-scenario negative test passed locally. **No Vivado/XSim execution occurred for that script.** Use the exact `.xpr` path and run `--execute` on the user's workstation before changing this gate to PASS.

1. Record actual `.xpr` path, Vivado executable path, `sim_1` name, simulation source list and **P0 repository hash**.
2. Run one known scenario per testbench via **automated** `vivado -mode batch -source <generated_tcl>` (or a documented alternative). Do **not** treat only `subprocess.returncode == 0` as success.
3. For every scenario: capture dedicated XSim stdout/stderr `.log`, require one `FI_RESULT_V2`, `FI_DISABLED_CONTROL_PASS`, `FI_APPLIED`, injection_ok=1/fault_events=1, requested=actual, target/bit/offset match scenario; and class-specific propagation evidence for MEM/BUF/DAT smoke.
4. Parse all 4 results with `parse_fi_v2.py`, summarize; compare **all 26 normalized result fields** to the manually validated reference. Replay twice to check deterministic equivalence.
5. Exercise failures: missing log, duplicate result, wrong target, unexpected bit, missing PASS marker, timeout, no injection, simulator nonzero return code. Runner must **fail closed**.
6. Only after actual XSim batch tests PASS, mark **AUTOMATED_XSIM_RUNNER_PASS**. A local Python dry-run or parser test does not satisfy this gate.

### Gate B — 20-scene deterministic matrix (5 per class)

- Keep the existing four locked scenarios as regression seeds.
- Add **4 distinct valid scenarios/class** selected from different registers, bits, and active phases/conditions (20 total); use catalog + actual elaborated RTL hierarchy.
- For BUF/MEM, record **written/initialized -> bit flipped -> retained -> consumed/read** where relevant. For DAT, prove consumption before overwrite; for CTL, verify FSM/control state and protocol or liveness.
- Distinguish **eligible/exposed** state-bit/time combinations from inactive sites; retain raw denominators and sampling definitions.
- At least one negative-control masked case and a reproducible noncompletion/timeout checker test are desirable, but **do not tune statistical sampling solely to force outcome classes**.

### Gate C — randomized pilot (NOT STARTED)

- Proposed in FI spec: **100 scenarios per class x 4 = 400**; record pseudorandom seed, sample IDs, target-bit/site population, active windows, eligible cycles, selection probabilities and duplicate policy.
- Expand scenario generator beyond current CTL timing sweep. The current `generate_fi_v2.py` is a **Verilog dispatcher generator**, not a randomized FI scenario generator.
- Analyze outcome counts and confidence intervals, per-class rates with explicit denominators, stage of first observable corruption, detection vs masking. Report unknown/invalid injections separately and do not silently drop them.
- Then design *full* P0 campaign size, run, and use results to justify P1/P2/P3 target selection.

## 9. Paper/report interpretation and claims that are **not** justified yet

**Safe claims today:**
- A verified bit-exact RTL baseline exists for the specified image/test workload and its golden intermediate checkpoints.
- The placed design uses 2,566 LUT / 813 FF / 0 DSP / 0 BRAM, and the routed constrained clock reports +0.466 ns WNS at 100 MHz.
- Three back-to-back frames yield II=1343, sustained non-overlapped throughput=74,460.16 images/s.
- Deterministic 1-bit injection mechanisms were demonstrated on four RTL state classes; one case each for CTL/BUF/MEM/DAT; four logs parsed and summarized with a strict Python tool.
- Specific cases yielded premature DONE (CTL), memory/buffer corruption with unchanged final output (MEM/BUF), and changed FC logits with unchanged digit (DAT).

**Do NOT claim:** physical irradiation/SEU cross-section, FPGA configuration-memory resilience, population-wide class vulnerability ranking, 100%/0% vulnerability of CTL/MEM/BUF/DAT from N=1, board-level I/O timing closure, measured Fmax, measured power, completed 400-scene pilot, implemented SECDED/TMR, or automated Vivado runner PASS.

## 10. Current milestone and handoff checklist

| Deliverable | State as of 2026-11-03 |
|---|---|
| Phase 1 numerical / bit-exact contract | LOCKED |
| Top full golden (2 frames; 1808 checks) | PASS (historical log) |
| Back-to-back 3-frame throughput (22 checks) | PASS |
| Placed utilization / routed 100 MHz timing | Baseline numbers RECORDED; save raw `.rpt` if not done |
| FI spec (`FI_SPEC.md`) | DEFINED; old 'current state' section is stale and needs synchronization |
| 80-entry 4-class candidate catalog | SOURCE-VERIFIED candidates; not full population |
| CTL_0001 / `FI_CTL_0005` | XSim smoke PASS |
| MEM_0001 / `FI_MEM1_0001` | XSim write/inject/read lifetime smoke PASS |
| BUF_0030 / `FI_BUF_0001` | XSim storage/window/consumer smoke PASS |
| DAT_0010 / `FI_DAT_0001` | XSim FC-logits/ArgMax consumption smoke PASS |
| 4-class normalized results (`parse_fi_v2.py`) | **4/4 PASS** |
| 4-class summary (`summarize_campaign.py`) | **PASS** |
| **Automated XSim runner** | **Draft `run_four_class_smoke.py` dry-run PASS; actual XSim batch NEXT / NOT VERIFIED** |
| **20 deterministic scenarios (5/class)** | PLANNED, NOT RUN |
| **400 randomized scenarios (100/class)** | PLANNED, NOT RUN |
| Full P0 FI campaign / statistical vulnerability | NOT STARTED |
| P1/P2/P3 protection builds and comparison | NOT STARTED |

### What the next AI should ask for only if truly absent

- The **actual Vivado `.xpr` path**, complete elaborated simulation sources / run TCL, `git rev-parse HEAD`, and P0 `.rpt` reports (to make automation and paper reproducible).
- `tb_cnn_mnist_top` **original 1808-check golden testbench** if needing full regression (not the 22-check throughput TB).
- Any missing HEX / RTL / smoke testbench **on disk**, not necessarily re-uploaded if already present in repo/project. Ask for only the missing exact file; avoid re-requesting everything.
- Actual XSim batch logs once automated runner is attempted.

### Version discipline

Work from repo root. Keep the known-PASS testbenches as regression references; do not overwrite one with a generic unproven version. Preserve scenario IDs and offsets, and use informative Git commits with staged changes inspected via `git diff --cached --stat`. Example commit for this handoff: `docs(progress): lock P0 baseline and four-class FI smoke results`.

---

**Final handoff line:** P0 CNN is frozen; **CTL, BUF, MEM, DAT deterministic 1-bit FI have all PASSed in XSim; the 4 resulting logs have PASSed Python parsing/summary. The exact next engineering gate is an automated four-class Vivado/XSim replay runner, followed by a methodologically valid 20-scenario directed matrix, then the 400-scenario pilot.**

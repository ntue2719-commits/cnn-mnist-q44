# VERILOG CODING AND VERIFICATION ROADMAP v2
## CNN MNIST Q4.4 — Phase-1 Bit-Exact Reference RTL

This roadmap is synchronized with `RTL_SPEC_v6.md` and the updated Python golden model.

Core method:

1. **Bottom-up implementation:** verify small arithmetic, memory, and buffering blocks before wrappers/top-level integration.
2. **Checkpoint verification:** compare every important RTL boundary against Python-generated `.hex` data.
3. **First-mismatch rule:** stop at the earliest mismatch and debug upstream before continuing.
4. **Correctness before PPA:** do not time-multiplex or aggressively optimize the MAC arrays until Phase 1 is bit-exact.

---

## PHASE 0 — GOLDEN DATA AND SIMULATION SETUP

### Goal

Create a trustworthy reference dataset and prove the Python arithmetic contract before RTL coding.

### Tasks

1. Install Python dependencies.
2. Run:

```bash
cd python
python smoke_test_golden.py
```

Expected:

```text
golden_model smoke test: PASS
```

3. Train or provide `model_q44.pth`.
4. Run:

```bash
python export_hex.py
```

5. Verify exact file counts:

| File | Count | Format |
|---|---:|---|
| `input.hex` | 784 | INT8 Q4.4 |
| `golden_conv1.hex` | 2704 | INT32 Q8.8 raw |
| `golden_relu1.hex` | 2704 | INT8 Q4.4 |
| `golden_pool1.hex` | 676 | INT8 Q4.4 |
| `golden_conv2.hex` | 968 | INT32 Q8.8 raw |
| `golden_relu2.hex` | 968 | INT8 Q4.4 |
| `golden_pool2.hex` | 200 | INT8 Q4.4 |
| `golden.hex` | 10 | INT32 Q8.8 logits |
| `prediction.hex` | 1 | digit |

6. Configure Vivado Simulator / XSim / ModelSim file paths.

### Exit criterion

- Python smoke test passes.
- Exporter completes with all line-count checks `OK`.
- Testbench can open `.hex` files.

---

## PHASE 1 — COMMON MATH, MEMORIES, AND ROMS

### 1. `rtl/common/round_shift_even.v`

Implement exact round-to-nearest-even for signed INT32 with a 4-bit right shift.

Test cases must include:

- remainder < 8;
- remainder > 8;
- exact half with even lower result;
- exact half with odd lower result;
- corresponding negative cases;
- values near INT8 saturation boundaries after shifting.

Compare against Python `round_shift_to_even()`.

### 2. `rtl/common/sat_int8.v`

Clamp signed input to:

```text
-128 .. +127
```

Test positive overflow, negative overflow, and exact boundaries.

### 3. `rtl/common/relu_int8.v`

```text
negative -> 0
zero     -> 0
positive -> unchanged
```

### 4. `feature_mem1.v`

Implement:

```text
169 x 32-bit
```

Required tests:

- sequential writes;
- simultaneous `i_start + i_wr_valid` writes first word to address 0;
- no wrap after word 168;
- read setup cycle;
- first valid read one cycle after start;
- exactly 169 valid reads;
- no read-address increment during idle.

### 5. `feature_mem2.v`

Same tests for:

```text
25 x 64-bit
```

### 6. ROMs

Implement:

```text
conv1_rom.v
conv2_rom.v
fc_rom.v
```

Locked Phase-1 behavior:

- `$readmemh()` parameter loading;
- Conv ROM outputs stable weight/bias buses;
- FC ROM lookup is combinational with respect to `i_cycle`;
- no hidden extra ROM latency.

FC ROM unit test must verify cycle 0, a middle cycle, and cycle 24 against `fc_weight.hex` indexing.

### Exit criterion

Every common block and memory/ROM unit test passes independently.

---

## PHASE 2 — CONV1 SUBSYSTEM

### 2.1 `conv1_buf.v`

Input: accepted pixel stream with legal gaps in `i_valid`.

Verify:

- internal position advances only on `i_valid`;
- exactly 676 valid 3x3 windows from 784 accepted pixels;
- first valid window corresponds to input rows 0..2, columns 0..2;
- last valid window corresponds to rows 25..27, columns 25..27;
- window byte packing P0..P8 is exact.

### 2.2 `conv1_calc.v`

Architecture:

```text
4 output channels x 9 products = 36 products/window
```

For each valid spatial position:

```text
acc = (bias << 4) + sum(pixel * weight)
```

Compare raw 4xINT32 output against `golden_conv1.hex`.

**Do not compare this stage against INT8 data.** `golden_conv1.hex` is now raw INT32 Q8.8.

### 2.3 `conv1_quant_relu.v`

For each of four raw accumulators:

```text
round-even -> saturate INT8 -> ReLU
```

Compare against `golden_relu1.hex`.

### 2.4 `pool1_buf.v` + `pool1.v`

Verify:

- 2x2 stride-2 pooling;
- signed maximum;
- exactly 169 pooled spatial positions;
- no extra quantization.

### 2.5 `pool1_reorder.v`

This stage is not a pass-through.

Locked reference implementation:

```text
CAPTURE 169 spatial words
-> store four channels separately
-> DRAIN in channel-major order
-> pack four consecutive canonical bytes per Mem1 word
-> emit exactly 169 words
```

Verification:

1. Collect output bytes from all 169 words.
2. Unpack bytes `[7:0]`, `[15:8]`, `[23:16]`, `[31:24]`.
3. Compare resulting 676-byte stream directly with `golden_pool1.hex`.

Test channel-boundary crossing explicitly.

### 2.6 `conv1_layer.v`

Full layer test:

```text
input.hex -> Conv1 layer -> 169 Mem1 write words
```

Verify:

- all internal checkpoints;
- final serialized byte stream equals `golden_pool1.hex`;
- `o_conv1_done` occurs with the final output word, not one cycle early.

### Exit criterion

Conv1 subsystem produces a byte-for-byte canonical Pool1 stream.

---

## PHASE 3 — CONV2 SUBSYSTEM

### Important architectural note

Mem1 contains canonical channel-major bytes. A 32-bit Mem1 word is **not** equal to four channels at one spatial coordinate.

### 3.1 `conv2_buf.v`

Reference Phase-1 approach:

```text
CAPTURE 169 x 32-bit Mem1 words
-> unpack 676 canonical bytes
-> reconstruct Pool1[4][13][13]
-> generate 3x3x4 windows
```

This is intentionally correctness-first.

Verify:

- exactly 676 input bytes reconstructed;
- tensor mapping `index = c*169 + y*13 + x`;
- exactly 121 valid windows;
- window channel packing:
  - ch0 `[71:0]`
  - ch1 `[143:72]`
  - ch2 `[215:144]`
  - ch3 `[287:216]`.

### 3.2 `conv2_calc.v`

Architecture:

```text
8 output channels x 4 input channels x 9 kernel values
= 288 products/window
```

Compare eight raw INT32 outputs against `golden_conv2.hex`.

### 3.3 `conv2_quant_relu.v`

Compare eight INT8 outputs against `golden_relu2.hex`.

### 3.4 `pool2_buf.v` + `pool2.v`

Verify:

- 11x11 -> 5x5 output;
- final row/column discarded;
- exactly 25 pooled spatial positions;
- signed maximum.

### 3.5 `pool2_reorder.v`

Locked reference:

```text
CAPTURE 25 spatial words x 8 channels
-> DRAIN channel-major 200-byte stream
-> pack 8 bytes/word
-> emit exactly 25 words
```

Critical test:

```text
stream[24] = channel0 last feature
stream[25] = channel1 first feature
```

These must occupy adjacent bytes with no padding.

Compare unpacked output bytes directly against `golden_pool2.hex`.

### 3.6 `conv2_layer.v`

Full test:

```text
golden_pool1.hex / Mem1-format input
-> Conv2 layer
-> 25 Mem2 words
```

Verify all checkpoints and final `o_conv2_done` timing.

### Exit criterion

Mem2 byte stream is exactly `golden_pool2.hex` in canonical order.

---

## PHASE 4 — FULLY CONNECTED AND ARGMAX

### 4.1 `fc_rom.v`

For cycle `k=0..24`, output weights for features:

```text
8*k .. 8*k+7
```

for all ten classes.

### 4.2 `fully_connected.v`

Reference architecture:

```text
80 parallel multipliers/cycle
= 10 classes x 8 features
10 signed INT32 accumulators
25 accepted input cycles
```

Initialization on `i_start_fc`:

```text
acc[class] = bias[class] << 4
```

On each `i_valid`:

```text
for class 0..9:
  acc[class] += sum(lane 0..7 feature[lane] * weight[class][lane])
```

`o_rom_cycle` increments only on accepted valid words.

On valid cycle 24:

- include the final 8-feature products;
- publish final logits;
- pulse `o_valid` and `o_fc_done` only after final accumulation is represented.

Compare all ten signed INT32 logits with `golden.hex`.

### 4.3 `arg_max.v`

Use signed strict `>` comparison.

Tie test example:

```text
logit[2] = 100
logit[5] = 100
expected digit = 2
```

Compare ordinary inference result with `prediction.hex`.

### Exit criterion

FC and ArgMax are bit-exact independently of the full CNN pipeline.

---

## PHASE 5 — TOP FSM AND END-TO-END INTEGRATION

### 5.1 `top_fsm.v`

States:

```text
IDLE -> CONV1 -> CONV2 -> FC -> ARGMAX -> DONE -> IDLE
```

Locked transitions:

#### Start

```text
i_start accepted in IDLE
-> enter CONV1
-> pulse o_start_conv1
```

#### Conv1 to Conv2

On final accepted Mem1 write / `i_conv1_done`:

```text
pulse o_start_rd1
pulse o_start_conv2
```

same cycle.

Mem1 first read data appears one cycle later.

#### Conv2 to FC

On final accepted Mem2 write / `i_conv2_done`:

```text
pulse o_start_rd2
pulse o_start_fc
```

same cycle.

#### FC to ArgMax

After `i_fc_done`, enter ARGMAX and pulse `o_load_digit` at the defined capture cycle.

#### DONE

For one cycle:

```text
o_valid = 1
o_done  = 1
o_digit stable and valid
```

Then return to IDLE.

### 5.2 `cnn_mnist_top.v`

Integrate all wrappers, memories, FC, ArgMax, and FSM.

### 5.3 End-to-end single-image test

1. Reset synchronously.
2. Pulse `i_start`.
3. Stream exactly 784 bytes from `input.hex`.
4. Insert deliberate gaps in `i_valid` to prove accepted-pixel counting is correct.
5. Wait for `o_valid/o_done`.
6. Compare `o_digit` with `prediction.hex`.

### 5.4 Multi-image regression

After one-image bit-exact success, extend Python export/test infrastructure to multiple samples and run a batch regression. Keep per-layer checkpoints for at least a small debug subset.

### Exit criterion

Full RTL inference is bit-exact with Python for all regression samples.

---

## PHASE 6 — SYNTHESIS AND PPA BASELINE

Only after Phase 5 passes.

Target example:

```text
Zynq-7010 / xc7z010clg400-1
```

Record:

- LUT;
- FF;
- DSP;
- BRAM;
- worst slack / Fmax;
- inference cycles;
- estimated latency;
- power estimate where practical.

Do not treat poor Phase-1 area as a functional failure: the design intentionally favors parallel reference arithmetic and explicit reorder storage.

---

## PHASE 7 — OPTIMIZATION ITERATIONS

Create a new architectural revision while keeping Phase-1 golden checkpoints unchanged.

Candidate directions:

1. Reduce Conv2 multiplier count through time multiplexing.
2. Reuse DSPs across channels/kernel taps.
3. Pipeline adder trees.
4. Replace CAPTURE/DRAIN reorder buffers with direct-address writes or different memory layout.
5. Stream Conv2 without full-tensor reconstruction.
6. Map feature storage/weights to BRAM.
7. Add AXI-Lite control and AXI/DMA data movement.
8. Benchmark reference vs optimized architecture.

Every optimized revision must first prove bit-exact equivalence with Phase 1.

---

## FIRST-MISMATCH DEBUG RULE

If a layer fails, stop at the earliest wrong checkpoint:

```text
final digit wrong
 -> golden logits?
 -> Pool2 bytes?
 -> ReLU2?
 -> Conv2 raw?
 -> Pool1 bytes?
 -> ReLU1?
 -> Conv1 raw?
 -> Conv1 window/input/weights?
```

Never debug the whole CNN at once when an earlier checkpoint is already wrong.

---

## PRACTICAL CODING ORDER

```text
[ ] Phase 0 Python smoke test + HEX export

[ ] round_shift_even.v
[ ] sat_int8.v
[ ] relu_int8.v
[ ] tb_common_math

[ ] feature_mem1.v
[ ] feature_mem2.v
[ ] tb_feature_mem1
[ ] tb_feature_mem2

[ ] conv1_rom.v
[ ] conv2_rom.v
[ ] fc_rom.v
[ ] tb_roms

[ ] conv1_buf.v
[ ] tb_conv1_buf
[ ] conv1_calc.v
[ ] tb_conv1_calc           -> golden_conv1.hex
[ ] conv1_quant_relu.v
[ ] tb_conv1_quant_relu     -> golden_relu1.hex
[ ] pool1_buf.v
[ ] pool1.v
[ ] pool1_reorder.v
[ ] tb_pool1_reorder        -> golden_pool1.hex
[ ] conv1_layer.v
[ ] tb_conv1_layer

[ ] conv2_buf.v
[ ] tb_conv2_buf
[ ] conv2_calc.v
[ ] tb_conv2_calc           -> golden_conv2.hex
[ ] conv2_quant_relu.v
[ ] tb_conv2_quant_relu     -> golden_relu2.hex
[ ] pool2_buf.v
[ ] pool2.v
[ ] pool2_reorder.v
[ ] tb_pool2_reorder        -> golden_pool2.hex
[ ] conv2_layer.v
[ ] tb_conv2_layer

[ ] fully_connected.v
[ ] tb_fully_connected      -> golden.hex
[ ] arg_max.v
[ ] tb_arg_max              -> prediction.hex

[ ] top_fsm.v
[ ] cnn_mnist_top.v
[ ] tb_cnn_mnist_top
[ ] end-to-end regression

[ ] synthesis
[ ] utilization/timing report
[ ] optimization branch/revision
```

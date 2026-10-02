# CNN MNIST Q4.4 — Phase 1 RTL Specification

**Status:** Phase 1 reference RTL  
**Priority:** correctness → verification → architecture → PPA  
**Numerical source of truth:** `python/golden_model.py`

This document intentionally stays short. It locks only the contracts required to implement and verify the RTL without ambiguity.

---

## 1. Network

```text
Input 1x28x28
  ↓
Conv1 1→4, 3x3
  ↓
Round-even → Saturate → ReLU
  ↓
MaxPool 2x2
  ↓
Pool1 4x13x13
  ↓
Reorder → Mem1
  ↓
Conv2 4→8, 3x3
  ↓
Round-even → Saturate → ReLU
  ↓
MaxPool 2x2
  ↓
Pool2 8x5x5
  ↓
Reorder → Mem2
  ↓
FC 200→10
  ↓
ArgMax
  ↓
digit[3:0]
```

Tensor shapes:

| Stage | Shape | Format |
|---|---:|---|
| Input | `1x28x28` | INT8 Q4.4 |
| Conv1 raw | `4x26x26` | INT32 Q8.8 |
| ReLU1 | `4x26x26` | INT8 Q4.4 |
| Pool1 | `4x13x13` | INT8 Q4.4 |
| Conv2 raw | `8x11x11` | INT32 Q8.8 |
| ReLU2 | `8x11x11` | INT8 Q4.4 |
| Pool2 | `8x5x5` | INT8 Q4.4 |
| FC | `10` | INT32 Q8.8 |
| Output | `1` | digit `0..9` |

---

## 2. Global Contracts

### 2.1 Clock / Reset

- `i_clk`: system clock.
- `i_rst_n`: **active-low synchronous reset**.
- Sequential template:

```verilog
always @(posedge i_clk) begin
    if (!i_rst_n) begin
        // reset
    end else begin
        // logic
    end
end
```

### 2.2 Naming

- Inputs: `i_*`
- Outputs: `o_*`

### 2.3 Valid

- `i_valid=1`: input data is accepted on the rising edge.
- `o_valid=1`: output data is valid for that transaction.
- Gaps in `i_valid` are legal unless a module-specific contract says otherwise.

### 2.4 Start / Busy / Done

Layer/top modules use:

```text
i_start → one operation
o_busy  → operation active
o_done  → one-cycle completion pulse
```

A new `i_start` is not accepted while `o_busy=1`.

---

## 3. Fixed-Point Contract

| Item | Rule |
|---|---|
| Q4.4 | signed INT8, scale 16 |
| Product | `int8 × int8 → Q8.8` |
| Accumulator | signed INT32 |
| Bias | `(signed bias) <<< 4` |
| Requantization | round-to-nearest-even by 4 bits |
| Saturation | clamp to `[-128,127]` |
| ReLU | `max(x,0)` after requantization |
| FC output | raw INT32 Q8.8; no requantization |

Conv/FC:

```text
acc = (bias << 4) + Σ(input * weight)
```

Round-to-nearest-even:

```text
shifted   = acc >> 4
remainder = acc - (shifted << 4)

if remainder > 8:
    shifted += 1
elif remainder == 8 and shifted[0] == 1:
    shifted += 1
```

All CNN arithmetic must be explicitly signed.

---

## 4. Ordering and Packing

### 4.1 Tensor Order

All multi-channel tensors use:

```text
[channel][row][column]
```

Flatten:

```text
index = c * (H*W) + y * W + x
```

### 4.2 Bus Byte Order

For consecutive INT8 values:

```text
value[0] → bus[7:0]
value[1] → bus[15:8]
...
```

### 4.3 Weight Order

```text
Conv1: [out_channel][in_channel][ky][kx]
Conv2: [out_channel][in_channel][ky][kx]
FC:    [class][feature]
```

### 4.4 Critical Reorder Rule

Conv/Pool datapaths naturally produce:

```text
[c0(y,x), c1(y,x), ...]
```

Feature memories must store:

```text
c0(all spatial), c1(all spatial), ...
```

Therefore:

- `pool1_reorder.v` converts `169 spatial words × 4 channels` into a continuous channel-major stream, then packs 4 consecutive bytes per Mem1 word.
- `pool2_reorder.v` converts `25 spatial words × 8 channels` into a continuous channel-major stream, then packs 8 consecutive bytes per Mem2 word.
- **No padding is inserted at channel boundaries.**

Conv2 must reconstruct Pool1 from the canonical Mem1 byte stream; a Mem1 32-bit word is **not guaranteed to represent four channels at one `(y,x)` coordinate**.

---

# 5. File Map and Interfaces

## 5.1 Common Arithmetic

### `rtl/common/round_shift_even.v`

**Purpose:** signed INT32 Q8.8 → rounded integer Q4.4 scale.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_value` | in | 32 | signed accumulator |
| `o_value` | out | 32 | signed value after round-even `>>4` |

Combinational block. No saturation or ReLU.

### `rtl/common/sat_int8.v`

**Purpose:** clamp signed INT32 to INT8 range.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_value` | in | 32 | signed input |
| `o_value` | out | 8 | signed saturated result |

### `rtl/common/relu_int8.v`

**Purpose:** INT8 ReLU.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_value` | in | 8 | signed INT8 |
| `o_value` | out | 8 | `max(i_value,0)` |

---

## 5.2 Feature Memories

### `rtl/memory/feature_mem1.v`

**Purpose:** store canonical Pool1 stream.

- Depth: `169`
- Width: `32`
- Total: `676` INT8 values

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | sync active-low reset |
| `i_start` | in | 1 | reset write pointer to 0 |
| `i_wr_valid` | in | 1 | write one 32-bit word |
| `i_wr_data` | in | 32 | four consecutive canonical bytes |
| `i_start_rd` | in | 1 | start sequential read |
| `o_rd_valid` | out | 1 | read data valid |
| `o_rd_data` | out | 32 | sequential memory word |

Read timing:

```text
cycle N:   i_start_rd=1, o_rd_valid=0
cycle N+1: o_rd_valid=1, o_rd_data=mem[0]
...
cycle N+169: mem[168]
```

Writes after address `168` are ignored; no wraparound.

### `rtl/memory/feature_mem2.v`

**Purpose:** store canonical Pool2 / FC input stream.

- Depth: `25`
- Width: `64`
- Total: `200` INT8 values

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | sync active-low reset |
| `i_start` | in | 1 | reset write pointer |
| `i_wr_valid` | in | 1 | write one 64-bit word |
| `i_wr_data` | in | 64 | eight consecutive canonical bytes |
| `i_start_rd` | in | 1 | start sequential read |
| `o_rd_valid` | out | 1 | read data valid |
| `o_rd_data` | out | 64 | sequential memory word |

Read timing is identical to Mem1, with 25 valid words.

---

## 5.3 Parameter ROMs

### `rtl/rom/conv1_rom.v`

**Purpose:** expose all Conv1 parameters.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `o_weights` | out | 288 | 36 × INT8 |
| `o_biases` | out | 32 | 4 × INT8 |

Loaded from:

```text
data/conv1_weight.hex
data/conv1_bias.hex
```

### `rtl/rom/conv2_rom.v`

**Purpose:** expose all Conv2 parameters.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `o_weights` | out | 2304 | 288 × INT8 |
| `o_biases` | out | 64 | 8 × INT8 |

### `rtl/rom/fc_rom.v`

**Purpose:** provide FC weights for the current 8-feature group.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_cycle` | in | 5 | feature group `0..24` |
| `o_weights` | out | 640 | `10 classes × 8 weights × 8b` |
| `o_biases` | out | 80 | 10 × INT8 |

**Phase 1 ROM contract:** combinational lookup; no hidden 1-cycle latency.

---

# 6. Conv1

## `rtl/conv1/conv1_buf.v`

**Purpose:** 28×28 pixel stream → valid 3×3 windows.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | clear counters/buffer state |
| `i_valid` | in | 1 | pixel valid |
| `i_pixel` | in | 8 | signed Q4.4 pixel |
| `o_valid` | out | 1 | 3×3 window valid |
| `o_window` | out | 72 | 9 × INT8 |

Window packing:

```text
o_window[7:0]   = P0
...
o_window[71:64] = P8
```

Valid windows: `26×26 = 676`.

## `rtl/conv1/conv1_calc.v`

**Purpose:** four parallel Conv1 output-channel accumulators.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_valid` | in | 1 | window valid |
| `i_window` | in | 72 | 9 pixels |
| `i_weights` | in | 288 | 36 weights |
| `i_biases` | in | 32 | 4 biases |
| `o_valid` | out | 1 | accumulator bus valid |
| `o_acc` | out | 128 | 4 × INT32 |

Per output channel:

```text
acc = bias<<4 + Σ(9 products)
```

Checkpoint: `data/golden_conv1.hex`.

## `rtl/conv1/conv1_quant_relu.v`

**Purpose:** four INT32 accumulators → four Q4.4 ReLU values.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_valid` | in | 1 | accumulator valid |
| `i_acc` | in | 128 | 4 × INT32 |
| `o_valid` | out | 1 | output valid |
| `o_data` | out | 32 | 4 × INT8 |

Per channel:

```text
round-even → saturate → ReLU
```

Checkpoint: `data/golden_relu1.hex`.

## `rtl/conv1/pool1_buf.v`

**Purpose:** create 2×2 pooling windows for four channels.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | clear buffer/counters |
| `i_valid` | in | 1 | 4-channel spatial word valid |
| `i_data` | in | 32 | 4 × INT8 |
| `o_valid` | out | 1 | pool window valid |
| `o_window` | out | 128 | `2×2×4×8b` |

Produces `13×13 = 169` windows.

## `rtl/conv1/pool1.v`

**Purpose:** signed max over each 2×2 window.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_valid` | in | 1 | window valid |
| `i_window` | in | 128 | 2×2×4 channels |
| `o_valid` | out | 1 | output valid |
| `o_data` | out | 32 | 4 maxima |

No rounding or requantization.

## `rtl/conv1/pool1_reorder.v`

**Purpose:** convert spatial-major Pool1 output into canonical channel-major Mem1 words.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | start new tensor |
| `i_valid` | in | 1 | spatial Pool1 word valid |
| `i_data` | in | 32 | `[ch0,ch1,ch2,ch3]` at one `(y,x)` |
| `o_valid` | out | 1 | Mem1 write valid |
| `o_data` | out | 32 | four consecutive channel-major bytes |
| `o_done` | out | 1 | final word emitted |

Reference implementation:

```text
CAPTURE 169 spatial words
    ↓
DRAIN [c][y][x] channel-major
    ↓
pack 4 bytes / word
    ↓
169 Mem1 words
```

## `rtl/conv1/conv1_layer.v`

**Purpose:** Conv1 + quant/ReLU + Pool1 + reorder wrapper.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | start layer |
| `i_valid` | in | 1 | pixel valid |
| `i_pixel` | in | 8 | input pixel |
| `o_busy` | out | 1 | layer active |
| `o_valid` | out | 1 | Mem1 write valid |
| `o_data` | out | 32 | canonical Mem1 word |
| `o_done` | out | 1 | final output word completed |

---

# 7. Conv2

## `rtl/conv2/conv2_buf.v`

**Purpose:** canonical Mem1 stream → Pool1 tensor → 3×3×4 Conv2 windows.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | start capture |
| `i_valid` | in | 1 | Mem1 word valid |
| `i_data` | in | 32 | 4 consecutive canonical bytes |
| `o_valid` | out | 1 | Conv2 window valid |
| `o_window` | out | 288 | `4 channels × 3×3 × 8b` |

Phase 1 reference behavior:

```text
capture 676 canonical bytes
    ↓
reconstruct Pool1[4][13][13]
    ↓
emit 121 windows
```

Packing:

```text
ch0 → [71:0]
ch1 → [143:72]
ch2 → [215:144]
ch3 → [287:216]
```

## `rtl/conv2/conv2_calc.v`

**Purpose:** eight parallel Conv2 output accumulators.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_valid` | in | 1 | window valid |
| `i_window` | in | 288 | 36 × INT8 |
| `i_weights` | in | 2304 | 288 weights |
| `i_biases` | in | 64 | 8 biases |
| `o_valid` | out | 1 | output valid |
| `o_acc` | out | 256 | 8 × INT32 |

Per channel:

```text
acc = bias<<4 + Σ(4×3×3 products)
```

Checkpoint: `data/golden_conv2.hex`.

## `rtl/conv2/conv2_quant_relu.v`

**Purpose:** eight raw accumulators → eight Q4.4 ReLU values.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_valid` | in | 1 | input valid |
| `i_acc` | in | 256 | 8 × INT32 |
| `o_valid` | out | 1 | output valid |
| `o_data` | out | 64 | 8 × INT8 |

Checkpoint: `data/golden_relu2.hex`.

## `rtl/conv2/pool2_buf.v`

**Purpose:** create 2×2 pooling windows for eight channels.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | clear state |
| `i_valid` | in | 1 | 8-channel word valid |
| `i_data` | in | 64 | 8 × INT8 |
| `o_valid` | out | 1 | window valid |
| `o_window` | out | 256 | `2×2×8×8b` |

Produces `5×5 = 25` windows. Final row/column of the 11×11 map are discarded.

## `rtl/conv2/pool2.v`

**Purpose:** signed 2×2 max for eight channels.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_valid` | in | 1 | window valid |
| `i_window` | in | 256 | 2×2×8 channels |
| `o_valid` | out | 1 | output valid |
| `o_data` | out | 64 | 8 maxima |

## `rtl/conv2/pool2_reorder.v`

**Purpose:** convert spatial Pool2 output to canonical channel-major Mem2 words.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | start new tensor |
| `i_valid` | in | 1 | spatial Pool2 word valid |
| `i_data` | in | 64 | 8 channels at one `(y,x)` |
| `o_valid` | out | 1 | Mem2 write valid |
| `o_data` | out | 64 | 8 consecutive canonical bytes |
| `o_done` | out | 1 | final Mem2 word emitted |

Reference:

```text
CAPTURE 25 spatial words
    ↓
DRAIN Pool2[c][y][x]
    ↓
pack 8 bytes / word
    ↓
25 Mem2 words
```

There are 25 values/channel, so words may cross channel boundaries. No padding.

## `rtl/conv2/conv2_layer.v`

**Purpose:** Conv2 + quant/ReLU + Pool2 + reorder wrapper.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | start layer |
| `i_valid` | in | 1 | Mem1 read valid |
| `i_data` | in | 32 | Mem1 word |
| `o_busy` | out | 1 | layer active |
| `o_valid` | out | 1 | Mem2 write valid |
| `o_data` | out | 64 | canonical Mem2 word |
| `o_done` | out | 1 | final output completed |

---

# 8. Fully Connected and ArgMax

## `rtl/fc/fully_connected.v`

**Purpose:** process 200 Pool2 features into 10 raw logits.

Architecture:

```text
25 valid words
× 8 features/word
× 10 classes
= 80 multipliers/cycle
+ 10 INT32 accumulators
```

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | initialize accumulators with `bias<<4` |
| `i_valid` | in | 1 | Mem2 word valid |
| `i_data` | in | 64 | 8 features |
| `i_weights` | in | 640 | 80 weights for current group |
| `i_biases` | in | 80 | 10 biases |
| `o_rom_cycle` | out | 5 | current group `0..24` |
| `o_busy` | out | 1 | FC active |
| `o_valid` | out | 1 | logits valid |
| `o_logits` | out | 320 | 10 × INT32 |
| `o_done` | out | 1 | final accumulation complete |

FC output has **no** round, saturation or ReLU.

Checkpoint: `data/golden.hex`.

## `rtl/fc/argmax.v`

**Purpose:** signed maximum of ten logits; lowest index wins ties.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_load` | in | 1 | register current result |
| `i_logits` | in | 320 | 10 × signed INT32 |
| `o_digit` | out | 4 | predicted digit |

Algorithm:

```text
best = 0
for i=1..9:
    if logit[i] > logit[best]:
        best = i
```

Checkpoint: `data/prediction.hex`.

---

# 9. Control and Top

## `rtl/control/top_fsm.v`

**Purpose:** sequence the complete inference.

States:

```text
IDLE → CONV1 → CONV2 → FC → DONE → IDLE
```

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | clock |
| `i_rst_n` | in | 1 | reset |
| `i_start` | in | 1 | start inference |
| `i_conv1_done` | in | 1 | Conv1 completed Mem1 output |
| `i_conv2_done` | in | 1 | Conv2 completed Mem2 output |
| `i_fc_done` | in | 1 | FC final accumulation done |
| `o_start_conv1` | out | 1 | start Conv1 / clear Mem1 writer |
| `o_start_rd1` | out | 1 | start Mem1 read |
| `o_start_conv2` | out | 1 | start Conv2 |
| `o_start_rd2` | out | 1 | start Mem2 read |
| `o_start_fc` | out | 1 | start FC |
| `o_load_digit` | out | 1 | register argmax |
| `o_busy` | out | 1 | inference active |
| `o_valid` | out | 1 | final digit valid |
| `o_done` | out | 1 | one-cycle completion pulse |

Transitions launch:

```text
Conv1 done → o_start_rd1 + o_start_conv2
Conv2 done → o_start_rd2 + o_start_fc
```

The first memory data appears one cycle after `o_start_rd*`.

## `rtl/top/cnn_mnist_top.v`

**Purpose:** instantiate and connect all Phase 1 blocks.

| Signal | Dir | Width | Meaning |
|---|---|---:|---|
| `i_clk` | in | 1 | system clock |
| `i_rst_n` | in | 1 | sync active-low reset |
| `i_start` | in | 1 | start one image |
| `i_valid` | in | 1 | input pixel valid |
| `i_pixel` | in | 8 | signed Q4.4 input pixel |
| `o_busy` | out | 1 | inference active |
| `o_valid` | out | 1 | result valid |
| `o_done` | out | 1 | inference complete pulse |
| `o_digit` | out | 4 | predicted digit |

A complete input transaction contains exactly `784` accepted pixels.

---

# 10. Golden Files

| File | Count | Format | Used by |
|---|---:|---|---|
| `input.hex` | 784 | INT8 | top / Conv1 TB |
| `conv1_weight.hex` | 36 | INT8 | Conv1 ROM |
| `conv1_bias.hex` | 4 | INT8 | Conv1 ROM |
| `conv2_weight.hex` | 288 | INT8 | Conv2 ROM |
| `conv2_bias.hex` | 8 | INT8 | Conv2 ROM |
| `fc_weight.hex` | 2000 | INT8 | FC ROM |
| `fc_bias.hex` | 10 | INT8 | FC ROM |
| `golden_conv1.hex` | 2704 | INT32 | Conv1 MAC |
| `golden_relu1.hex` | 2704 | INT8 | Conv1 quant/ReLU |
| `golden_pool1.hex` | 676 | INT8 | Pool1 tensor |
| `golden_conv2.hex` | 968 | INT32 | Conv2 MAC |
| `golden_relu2.hex` | 968 | INT8 | Conv2 quant/ReLU |
| `golden_pool2.hex` | 200 | INT8 | Pool2 tensor |
| `golden.hex` | 10 | INT32 | FC logits |
| `prediction.hex` | 1 | digit | ArgMax |
| `label.hex` | 1 | digit | dataset reference |

`.hex` serialization:

```text
INT8  → 2 hex digits, two's complement, one value/line
INT32 → 8 hex digits, two's complement, one value/line
```

---

# 11. Verification Order

```text
smoke_test_golden.py
    ↓
round_shift_even
sat_int8
relu_int8
    ↓
feature_mem1 / feature_mem2
ROMs
    ↓
conv1_buf
conv1_calc
conv1_quant_relu
pool1_buf
pool1
pool1_reorder
conv1_layer
    ↓
conv2_buf
conv2_calc
conv2_quant_relu
pool2_buf
pool2
pool2_reorder
conv2_layer
    ↓
fully_connected
argmax
    ↓
top_fsm
cnn_mnist_top
```

**First-mismatch rule:** stop at the first incorrect checkpoint and debug that block before moving downstream.

---

# 12. Phase 1 Completion Criteria

Phase 1 is complete when:

- arithmetic unit tests pass;
- every golden checkpoint is bit-exact;
- tensor ordering is correct across Mem1/Mem2;
- FC logits match all 10 INT32 values;
- RTL prediction matches `prediction.hex`;
- multi-image regression reports zero RTL-vs-golden mismatches.

Phase 1 does not require optimized DSP/BRAM usage or maximum throughput.

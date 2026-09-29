# CNN MNIST Q4.4 — RTL Phase 1 Specification

## 1. Objective

Phase 1 implements a **bit-exact RTL reference datapath** matching:

```text
golden_model.py

```

The objective is functional correctness and verification.

Phase 1 does **not** optimize:

- DSP utilization
- BRAM utilization
- throughput
- GEMM architecture
- memory arbitration
- AXI
- CPU/PS interface
- multi-image pipeline

Optimization is performed in later phases.

---

# 2. CNN Architecture

```text
Input
28 × 28 × 1
    │
    ▼
CONV1
1 → 4 channels
3 × 3
stride = 1
padding = 0
    │
    ▼
4 × 26 × 26
    │
    ▼
QUANTIZE + ReLU
    │
    ▼
POOL1
2 × 2
stride = 2
    │
    ▼
4 × 13 × 13
    │
    ▼
CONV2
4 → 8 channels
3 × 3
stride = 1
padding = 0
    │
    ▼
8 × 11 × 11
    │
    ▼
QUANTIZE + ReLU
    │
    ▼
POOL2
2 × 2
stride = 2
    │
    ▼
8 × 5 × 5
    │
    ▼
FLATTEN
200 features
    │
    ▼
FC
200 → 10
    │
    ▼
10 × Q8.8 int32 logits
    │
    ▼
ARGMAX
    │
    ▼
digit[3:0]

```

---

# 3. Phase 1 File Structure

Recommended repository structure:

```text
rtl/
├── common/
│   ├── round_shift_even.v
│   ├── sat_int8.v
│   └── relu_int8.v
│
├── conv1/
│   ├── conv1_buf.v
│   ├── conv1_calc.v
│   ├── conv1_quant_relu.v
│   ├── pool1_buf.v
│   ├── pool1.v
│   └── conv1_layer.v
│
├── conv2/
│   ├── conv2_buf.v
│   ├── conv2_calc.v
│   ├── conv2_quant_relu.v
│   ├── pool2_buf.v
│   ├── pool2.v
│   └── conv2_layer.v
│
├── fc/
│   ├── fully_connected.v
│   └── argmax.v
│
└── top/
    └── cnn_mnist_top.v

```

Verification:

```text
tb/
├── tb_round_shift_even.v
├── tb_conv1.v
├── tb_pool1.v
├── tb_conv2.v
├── tb_pool2.v
├── tb_fc.v
└── tb_cnn_mnist_top.v

```

Golden data:

```text
data/
├── input.hex
├── conv1_weight.hex
├── conv1_bias.hex
├── conv2_weight.hex
├── conv2_bias.hex
├── fc_weight.hex
├── fc_bias.hex
│
├── golden_conv1.hex
├── golden_relu1.hex
├── golden_pool1.hex
├── golden_conv2.hex
├── golden_relu2.hex
├── golden_pool2.hex
│
├── golden.hex
├── prediction.hex
└── label.hex

```

---

# 4. Global RTL Interface Convention

All Phase 1 modules use:

```text
clk
rst_n
valid_in
valid_out

```

For layer-level modules additionally:

```text
start
busy
done

```

## 4.1 Clock

```verilog
input clk;

```

All sequential logic is triggered on:

```verilog
posedge clk

```

---

## 4.2 Reset

Active-low synchronous reset:

```verilog
input rst_n;

```

Required behavior:

```text
rst_n = 0
    ↓
internal state cleared
valid_out = 0
done = 0
busy = 0

```

All Phase 1 modules must use the same reset convention.

---

# 5. valid Protocol

## 5.1 valid_in

`valid_in = 1` means:

> Input data is valid and must be sampled at the rising clock edge.

Example:

```text
cycle 0: valid_in = 0
cycle 1: valid_in = 1 → sample data
cycle 2: valid_in = 1 → sample data
cycle 3: valid_in = 0

```

No data transaction occurs when:

```text
valid_in = 0

```

---

## 5.2 valid_out

`valid_out = 1` means:

> Output data is valid during this clock cycle.

Example:

```text
valid_in:
0 1 1 1 1 1 ...

valid_out:
0 0 0 1 1 1 ...

```

Latency between `valid_in` and `valid_out` is module-dependent and must be documented.

---

# 6. start / busy / done

Layer-level modules use:

```verilog
input  start;
output busy;
output done;

```

Protocol:

```text
IDLE
 │
 │ start=1
 ▼
BUSY
 │
 │ processing
 │
 ▼
DONE
 │
 │ done=1
 ▼
IDLE

```

`start` starts exactly **one image transaction**.

A new `start` must not be accepted while:

```text
busy = 1

```

---

# 7. Fixed-Point Specification

## 7.1 Q4.4

Input, weights, biases and feature maps:

```text
signed int8
Q4.4

```

Representation:

```text
real_value = integer / 16

```

Range:

```text
-128 / 16 = -8
127 / 16  = 7.9375

```

---

# 8. Multiplication

Two Q4.4 values:

```text
A × B

```

produce:

```text
Q8.8

```

because:

```text
2^-4 × 2^-4 = 2^-8

```

Therefore:

```verilog
product = A * B;

```

must be treated as a **signed Q8.8 value**.

---

# 9. Bias Alignment

Bias is stored as Q4.4.

Accumulator uses Q8.8.

Therefore:

```text
bias_Q8.8 = bias_Q4.4 << 4

```

Every convolution and FC accumulator starts with:

```text
acc = bias << 4

```

This is mandatory.

---

# 10. Accumulator Width

Phase 1 uses:

```text
signed 32-bit accumulator

```

for:

- Conv1
- Conv2
- FC

Maximum values for this network are far below the signed 32-bit range.

Therefore:

```verilog
reg signed [31:0] acc;

```

is sufficient for Phase 1.

---

# 11. Round-to-Nearest-Even

Convolution output uses:

```text
round_shift_even(acc, 4)

```

Equivalent Python algorithm:

```python
shifted = acc >> 4
remainder = acc - (shifted << 4)

if remainder > 8:
    shifted += 1

elif remainder == 8 and (shifted & 1):
    shifted += 1

```

Important:

```text
NOT truncate
NOT round-half-up

```

It is:

```text
round-half-to-even

```

---

# 12. Saturation

After rounding:

```text
[-128, 127]

```

is enforced.

```text
if value > 127:
    value = 127

if value < -128:
    value = -128

```

Output:

```text
signed int8 Q4.4

```

---

# 13. ReLU

ReLU operates on the quantized Q4.4 result:

```text
ReLU(x) = max(x, 0)

```

Therefore:

```text
negative → 0
positive → unchanged

```

The Phase 1 order is:

```text
MAC
 ↓
round-to-even
 ↓
saturation
 ↓
ReLU
 ↓
Q4.4 output

```

This order must match `golden_model.py`.

---

# 14. CONV1

## 14.1 Input

```text
28 × 28 × 1

```

Input interface:

```verilog
input  [7:0] pixel_in;
input        valid_in;

```

`pixel_in` is interpreted as:

```text
signed int8 Q4.4

```

Input order:

```text
row-major

```

```text
(0,0)
(0,1)
...
(0,27)
(1,0)
...
(27,27)

```

Total:

```text
784 pixels

```

---

# 15. conv1_buf.v

Purpose:

```text
streaming 28×28 input
        ↓
3×3 sliding window

```

Interface:

```verilog
module conv1_buf (
    input        clk,
    input        rst_n,

    input        valid_in,
    input  [7:0] pixel_in,

    output       valid_out,
    output [71:0] window_out
);

```

Window contains:

```text
9 × 8-bit

```

Mapping:

```text
P0 P1 P2
P3 P4 P5
P6 P7 P8

```

where:

```text
P0 = input[y+0][x+0]
P1 = input[y+0][x+1]
P2 = input[y+0][x+2]

P3 = input[y+1][x+0]
P4 = input[y+1][x+1]
P5 = input[y+1][x+2]

P6 = input[y+2][x+0]
P7 = input[y+2][x+1]
P8 = input[y+2][x+2]

```

Window output order must be explicitly fixed as:

```text
window_out = {P8,P7,P6,P5,P4,P3,P2,P1,P0}

```

Therefore:

```text
window_out[7:0]    = P0
window_out[15:8]   = P1
...
window_out[71:64]  = P8

```

Output windows:

```text
26 × 26 = 676

```

---

# 16. conv1_calc.v

Purpose:

```text
3×3 window
    +
4 output channels
    ↓
4 convolution accumulators

```

Interface:

```verilog
module conv1_calc (
    input  [71:0]  window_in,
    input  [287:0] weight_bank,
    input  [31:0]  bias_bank,

    output [127:0] acc_out
);

```

Weight shape:

```text
4 × 1 × 3 × 3

```

Total:

```text
36 × int8

```

Each output channel:

```text
acc[oc] =
    bias[oc] << 4
    + P0*W0
    + P1*W1
    ...
    + P8*W8

```

Output:

```text
4 × signed int32 Q8.8

```

Packing:

```text
acc_out[31:0]     = acc0
acc_out[63:32]    = acc1
acc_out[95:64]    = acc2
acc_out[127:96]   = acc3

```

`conv1_calc` does **not**:

- round
- saturate
- ReLU
- pool

---

# 17. conv1_quant_relu.v

Purpose:

```text
4 × Q8.8 accumulator
        ↓
round
        ↓
saturation
        ↓
ReLU
        ↓
4 × Q4.4

```

Interface:

```verilog
module conv1_quant_relu (
    input        clk,
    input        rst_n,

    input        valid_in,
    input [127:0] acc_in,

    output       valid_out,
    output [31:0] data_out
);

```

Packing:

```text
data_out[7:0]    = channel 0
data_out[15:8]   = channel 1
data_out[23:16]  = channel 2
data_out[31:24]  = channel 3

```

One transaction represents:

```text
one spatial position
+
four output channels

```

Total transactions:

```text
676

```

---

# 18. pool1_buf.v

Purpose:

```text
4-channel stream
        ↓
2×2 spatial buffering

```

Input:

```text
4 × 8-bit

```

Interface:

```verilog
module pool1_buf (
    input         clk,
    input         rst_n,

    input         valid_in,
    input  [31:0] data_in,

    output        valid_out,
    output [127:0] pool_window
);

```

Each pool window contains:

```text
A B
C D

```

for every channel.

The four positions are:

```text
A = (y,   x)
B = (y,   x+1)
C = (y+1, x)
D = (y+1, x+1)

```

Pool stride:

```text
2

```

Output transactions:

```text
13 × 13 = 169

```

---

# 19. pool1.v

Interface:

```verilog
module pool1 (
    input        clk,
    input        rst_n,

    input        valid_in,
    input [127:0] pool_window,

    output       valid_out,
    output [31:0] data_out
);

```

For each channel:

```text
output[c] = max(A[c], B[c], C[c], D[c])

```

No arithmetic rounding occurs here.

Input/output:

```text
input  = 4 × Q4.4
output = 4 × Q4.4

```

Output shape:

```text
4 × 13 × 13

```

---

# 20. conv1_layer.v

This module combines:

```text
conv1_buf
    ↓
conv1_calc
    ↓
conv1_quant_relu
    ↓
pool1_buf
    ↓
pool1

```

Interface:

```verilog
module conv1_layer (
    input        clk,
    input        rst_n,
    input        start,

    input        valid_in,
    input  [7:0] pixel_in,

    output       busy,
    output       valid_out,
    output [31:0] data_out,
    output       done
);

```

Input:

```text
784 pixels

```

Output:

```text
169 transactions

```

Each output transaction:

```text
4 × signed int8 Q4.4

```

Output corresponds to:

```text
4 × 13 × 13

```

Verification target:

```text
golden_pool1.hex

```

---

# 21. CONV2

## 21.1 Input

Conv2 receives:

```text
4 × 13 × 13

```

One transaction:

```text
4 × int8

```

Total input transactions:

```text
169

```

---

# 22. conv2_buf.v

Purpose:

```text
4-channel feature stream
        ↓
3×3 spatial window

```

Interface:

```verilog
module conv2_buf (
    input        clk,
    input        rst_n,

    input        valid_in,
    input [31:0] data_in,

    output       valid_out,
    output [287:0] window_out
);

```

Window contains:

```text
4 channels × 9 pixels
= 36 × 8-bit

```

For every channel:

```text
P0 P1 P2
P3 P4 P5
P6 P7 P8

```

Packing convention:

```text
channel 0 occupies [71:0]
channel 1 occupies [143:72]
channel 2 occupies [215:144]
channel 3 occupies [287:216]

```

Within each channel:

```text
[7:0]   = P0
[15:8]  = P1
...
[71:64] = P8

```

Output windows:

```text
11 × 11 = 121

```

---

# 23. conv2_calc.v

Interface:

```verilog
module conv2_calc (
    input  [287:0] window_in,
    input  [2303:0] weight_bank,
    input  [63:0] bias_bank,

    output [255:0] acc_out
);

```

Weight shape:

```text
8 × 4 × 3 × 3

```

Total weights:

```text
288 × int8

```

Each output channel:

```text
acc[oc] =
    bias[oc] << 4
    + Σ(input[ic][ky][kx] × weight[oc][ic][ky][kx])

```

MAC count per output channel:

```text
4 × 3 × 3 = 36

```

Total parallel multiplications:

```text
8 × 36 = 288

```

Output:

```text
8 × signed int32 Q8.8

```

Packing:

```text
acc_out[31:0]       = acc0
acc_out[63:32]      = acc1
...
acc_out[255:224]    = acc7

```

---

# 24. conv2_quant_relu.v

Interface:

```verilog
module conv2_quant_relu (
    input        clk,
    input        rst_n,

    input        valid_in,
    input [255:0] acc_in,

    output       valid_out,
    output [63:0] data_out
);

```

For each channel:

```text
Q8.8 accumulator
    ↓
round-to-even
    ↓
saturation
    ↓
ReLU
    ↓
Q4.4 int8

```

Packing:

```text
data_out[7:0]     = ch0
data_out[15:8]    = ch1
...
data_out[63:56]   = ch7

```

Total transactions:

```text
121

```

---

# 25. pool2_buf.v

Input:

```text
8 × 11 × 11

```

One transaction:

```text
8 × int8

```

The buffer forms:

```text
2×2

```

windows with stride 2.

Output transactions:

```text
5 × 5 = 25

```

The last row and column of the 11×11 feature map are discarded.

This exactly matches:

```python
H // 2
W // 2

```

in `golden_model.py`.

---

# 26. pool2.v

Interface:

```verilog
module pool2 (
    input        clk,
    input        rst_n,

    input        valid_in,
    input [255:0] pool_window,

    output       valid_out,
    output [63:0] data_out
);

```

Output:

```text
8 × Q4.4

```

Shape:

```text
8 × 5 × 5

```

Total:

```text
200 features

```

Verification target:

```text
golden_pool2.hex

```

---

# 27. conv2_layer.v

Combines:

```text
conv2_buf
    ↓
conv2_calc
    ↓
conv2_quant_relu
    ↓
pool2_buf
    ↓
pool2

```

Interface:

```verilog
module conv2_layer (
    input        clk,
    input        rst_n,
    input        start,

    input        valid_in,
    input [31:0] data_in,

    output       busy,
    output       valid_out,
    output [63:0] data_out,
    output       done
);

```

Input:

```text
169 transactions

```

Output:

```text
25 transactions

```

Each output:

```text
8 × signed int8 Q4.4

```

Verification target:

```text
golden_pool2.hex

```

---

# 28. Flatten

No arithmetic is performed.

Pool2 output:

```text
8 × 5 × 5

```

is flattened in channel-major order:

```text
channel 0:
(0,0) ... (4,4)

channel 1:
(0,0) ... (4,4)

...

channel 7:
(0,0) ... (4,4)

```

Index:

```text
feature_index = c × 25 + y × 5 + x

```

Therefore:

```text
feature[0..24]    = channel 0
feature[25..49]   = channel 1
...
feature[175..199] = channel 7

```

---

# 29. fully_connected.v

## 29.1 Interface

```verilog
module fully_connected (
    input        clk,
    input        rst_n,
    input        start,

    input        valid_in,
    input [63:0] data_in,

    input [15999:0] fc_weights,
    input [79:0]    fc_bias,

    output       busy,
    output       valid_out,
    output [319:0] logits_out,
    output       done
);

```

Input:

```text
25 transactions

```

Each transaction:

```text
8 × Q4.4

```

Total:

```text
25 × 8 = 200 features

```

---

# 30. FC Mathematical Definition

For class `j`:

```text
logit[j] =
    bias[j] << 4
    +
    Σ X[i] × W[j][i]

```

where:

```text
j = 0 ... 9
i = 0 ... 199

```

Input:

```text
X[i] = Q4.4

```

Weight:

```text
W[j][i] = Q4.4

```

Output:

```text
logit[j] = Q8.8 signed int32

```

---

# 31. FC Phase 1 Architecture

Phase 1 uses:

```text
10 parallel accumulators

```

and processes:

```text
8 features / clock

```

Therefore:

```text
25 cycles

```

of feature transactions are required.

Conceptually:

```text
                ┌── MAC → acc0
X[8] ───────────┼── MAC → acc1
                ├── ...
                └── MAC → acc9

```

Each class performs:

```text
8 multiplications / cycle

```

Total parallel multipliers:

```text
10 × 8 = 80

```

This is a **reference architecture**, not the final optimized GEMM architecture.

---

# 32. FC Output

Packing:

```text
logits_out[31:0]      = logit0
logits_out[63:32]     = logit1
...
logits_out[319:288]   = logit9

```

Important:

```text
NO round
NO saturation
NO ReLU
NO requantization

```

FC output remains:

```text
signed int32 Q8.8

```

Verification target:

```text
golden.hex

```

---

# 33. argmax.v

Interface:

```verilog
module argmax (
    input  [319:0] logits_in,
    output [3:0]   digit_out
);

```

Algorithm:

```text
best_index = 0
best_value = logit[0]

for i = 1 ... 9:

    if logit[i] > best_value:
        best_value = logit[i]
        best_index = i

```

Comparison must use:

```text
>

```

not:

```text
>=

```

Therefore ties select the smaller index.

Example:

```text
logit[2] = 100
logit[5] = 100

```

result:

```text
digit = 2

```

---

# 34. cnn_mnist_top.v

Top-level Phase 1 module:

```verilog
module cnn_mnist_top (
    input        clk,
    input        rst_n,
    input        start,

    input        valid_in,
    input  [7:0] pixel_in,

    output       busy,
    output       done,
    output       valid_out,

    output [3:0] digit_out
);

```

Top-level data flow:

```text
pixel_in
   │
   ▼
conv1_layer
   │
   │ 4×Q4.4
   ▼
conv2_layer
   │
   │ 8×Q4.4
   ▼
fully_connected
   │
   │ 10×Q8.8 int32
   ▼
argmax
   │
   ▼
digit_out

```

---

# 35. Top-Level Input Protocol

For one image:

```text
start = 1

```

then:

```text
valid_in = 1
pixel_in = input.hex[0]

```

through:

```text
pixel_in = input.hex[783]

```

Input order:

```text
row-major

```

The top module accepts exactly:

```text
784 valid input transactions

```

---

# 36. Top-Level Output Protocol

When classification is complete:

```text
valid_out = 1
done      = 1
digit_out = predicted digit

```

`digit_out` must match:

```text
prediction.hex

```

After completion:

```text
busy = 0

```

---

# 37. Transaction Summary

| Stage       | Input Transactions | Output Transactions | Output/Transaction |
| ----------- | ------------------ | ------------------- | ------------------ |
| Input       | 784                | 784                 | 1 × int8           |
| Conv1       | 784 pixels         | 676                 | 4 × int32          |
| Quant/ReLU1 | 676                | 676                 | 4 × int8           |
| Pool1       | 676                | 169                 | 4 × int8           |
| Conv2       | 169                | 121                 | 8 × int32          |
| Quant/ReLU2 | 121                | 121                 | 8 × int8           |
| Pool2       | 121                | 25                  | 8 × int8           |
| Flatten     | 25                 | 25                  | 8 × int8           |
| FC          | 25                 | 1                   | 10 × int32         |
| ArgMax      | 1                  | 1                   | 1 × 4-bit          |

---

# 38. Verification Order

Do not debug the complete CNN first.

Verification order:

```text
1. round_shift_even
        ↓
2. sat_int8
        ↓
3. signed MAC
        ↓
4. conv1_buf
        ↓
5. conv1_calc
        ↓
6. conv1_quant_relu
        ↓
7. pool1_buf
        ↓
8. pool1
        ↓
9. conv1_layer
        ↓
10. conv2_buf
        ↓
11. conv2_calc
        ↓
12. conv2_quant_relu
        ↓
13. pool2_buf
        ↓
14. pool2
        ↓
15. conv2_layer
        ↓
16. fully_connected
        ↓
17. argmax
        ↓
18. cnn_mnist_top

```

---

# 39. Golden Comparison Points

## Conv1

Compare:

```text
conv1_quant_relu output

```

against:

```text
golden_relu1.hex

```

Then:

```text
pool1 output

```

against:

```text
golden_pool1.hex

```

---

## Conv2

Compare:

```text
conv2_quant_relu output

```

against:

```text
golden_relu2.hex

```

Then:

```text
pool2 output

```

against:

```text
golden_pool2.hex

```

---

## FC

Compare:

```text
logits_out

```

against:

```text
golden.hex

```

Remember:

```text
golden.hex = 10 int32 logits

```

It is **not** the predicted digit.

---

## ArgMax

Compare:

```text
digit_out

```

against:

```text
prediction.hex

```

---

# 40. Required Arithmetic Test Vectors

`round_shift_even.v` must be tested independently.

At minimum:

```text
acc = 0
acc = 1
acc = 7
acc = 8
acc = 9
acc = 15
acc = 16
acc = 17
acc = 23
acc = 24
acc = 25

```

Negative cases:

```text
acc = -1
acc = -7
acc = -8
acc = -9
acc = -15
acc = -16
acc = -17
acc = -23
acc = -24
acc = -25

```

Special attention must be given to:

```text
remainder = 8

```

because this is the half-to-even boundary.

---

# 41. Signed RTL Requirement

All arithmetic signals must explicitly use signed interpretation.

Example:

```verilog
reg signed [7:0] a;
reg signed [7:0] b;
reg signed [31:0] acc;
reg signed [15:0] product;

```

Do not rely on implicit signed conversion.

For example:

```verilog
product = a * b;

```

must operate as:

```text
signed int8 × signed int8

```

---

# 42. Weight Addressing

Conv1:

```text
index =
    ((oc * 1 + ic) * 3 + ky) * 3 + kx

```

Conv2:

```text
index =
    ((oc * 4 + ic) * 3 + ky) * 3 + kx

```

FC:

```text
index = class * 200 + feature

```

These formulas must be identical between Python and RTL.

---

# 43. Memory Ordering

All `.hex` files use flattened memory order.

## Conv1

```text
[oc][ic][ky][kx]

```

## Conv2

```text
[oc][ic][ky][kx]

```

## FC

```text
[class][feature]

```

## Feature maps

```text
[channel][y][x]

```

## Flatten

```text
channel-major

```

---

# 44. Phase 1 Resource Strategy

Phase 1 intentionally uses a relatively parallel architecture.

Approximate multiplier counts:

```text
Conv1:
4 × 9 = 36 multipliers

Conv2:
8 × 4 × 9 = 288 multipliers

FC:
10 × 8 = 80 multipliers

```

Total theoretical parallel multipliers:

```text
404

```

This is **not considered the final hardware architecture**.

The purpose is:

```text
Python
  ↓
bit-exact RTL
  ↓
correctness
  ↓
measurement
  ↓
optimization

```

---

# 45. Phase 1 Completion Criteria

Phase 1 is considered complete only when:

### Arithmetic

```text
round_shift_even == Python
sat_int8 == Python
signed MAC == Python

```

### Conv1

```text
RTL golden_relu1.hex == Python golden_relu1.hex
RTL golden_pool1.hex == Python golden_pool1.hex

```

### Conv2

```text
RTL golden_relu2.hex == Python golden_relu2.hex
RTL golden_pool2.hex == Python golden_pool2.hex

```

### FC

```text
RTL logits == Python golden.hex

```

### ArgMax

```text
RTL digit == Python prediction.hex

```

### Complete CNN

```text
RTL prediction == Python prediction

```

No mismatch is allowed.

---

# 46. Explicitly Out of Scope for Phase 1

The following are **not** implemented in Phase 1:

```text
FSM optimization
GEMM engine
PE array
AXI-Lite
AXI4
DMA
BRAM optimization
weight reuse optimization
input reuse optimization
DSP optimization
CPU/PS interface
RISC-V interface
arbiter
multi-layer shared MAC engine
multi-image pipeline

```

These belong to later phases.

---

# 47. Phase Roadmap

```text
PHASE 0
Arithmetic primitives
    ↓
PHASE 1
Bit-exact CNN reference datapath
    ↓
PHASE 2
FC → GEMM/GEMV engine
    ↓
PHASE 3
FSM + datapath separation
    ↓
PHASE 4
Memory architecture
    ↓
PHASE 5
Arbiter + accelerator control
    ↓
PHASE 6
PPA optimization

```

Final intended architecture:

```text
                 ┌───────────────────────┐
                 │     CNN Controller    │
                 │         FSM           │
                 └──────────┬────────────┘
                            │
                       control signals
                            │
       ┌────────────────────┴───────────────────┐
       │                                        │
       ▼                                        ▼
┌──────────────┐                        ┌──────────────┐
│ Memory System│                        │   Datapath   │
│              │                        │              │
│ Input MEM    │                        │ Conv / MAC   │
│ Weight MEM   │◄────── Arbiter ──────►│ GEMM / PE    │
│ Feature MEM  │                        │ Pool / ReLU  │
│ Output MEM   │                        │ ArgMax       │
└──────────────┘                        └──────────────┘

```

Phase 1 is therefore the **golden RTL reference implementation** against which all later optimized architectures must be verified.
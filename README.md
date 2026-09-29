# CNN MNIST Q4.4 — RTL Accelerator

Bit-exact SystemVerilog/Verilog reference implementation of a small MNIST CNN using signed INT8 Q4.4 fixed-point arithmetic.

## Goal

Phase 1 is a **golden RTL reference datapath**. Its primary target is functional correctness against `golden_model.py`, not PPA or throughput optimization.

The network is:

```text
28x28x1
  │
  ▼
Conv1: 1 -> 4, 3x3, stride 1
  │
  ▼
ReLU + 2x2 MaxPool
  │
  ▼
4x13x13
  │
  ▼
Conv2: 4 -> 8, 3x3, stride 1
  │
  ▼
ReLU + 2x2 MaxPool
  │
  ▼
8x5x5 = 200 features
  │
  ▼
FC: 200 -> 10
  │
  ▼
ArgMax
  │
  ▼
digit[3:0]
```

## Fixed-point format

- Input / weights / bias / feature maps: signed INT8 Q4.4
- Q4.4 real value: `integer / 16`
- Multiplication: signed Q8.8
- Accumulator: signed INT32 Q8.8
- Bias alignment: `bias << 4`
- Quantization: round-to-nearest-even, then saturation to `[-128, 127]`
- ReLU is applied after quantization
- FC output remains signed INT32 Q8.8

## Repository structure

```text
cnn-mnist-q44/
├── rtl/
│   ├── common/
│   │   ├── round_shift_even.v
│   │   ├── sat_int8.v
│   │   └── relu_int8.v
│   ├── conv1/
│   │   ├── conv1_buf.v
│   │   ├── conv1_calc.v
│   │   ├── conv1_quant_relu.v
│   │   ├── pool1_buf.v
│   │   ├── pool1.v
│   │   └── conv1_layer.v
│   ├── conv2/
│   │   ├── conv2_buf.v
│   │   ├── conv2_calc.v
│   │   ├── conv2_quant_relu.v
│   │   ├── pool2_buf.v
│   │   ├── pool2.v
│   │   └── conv2_layer.v
│   ├── fc/
│   │   ├── fully_connected.v
│   │   └── argmax.v
│   └── top/
│       └── cnn_mnist_top.v
├── tb/
├── data/
├── scripts/
├── docs/
└── README.md
```

## Verification order

Do not debug the complete CNN first.

```text
round_shift_even
      ↓
sat_int8
      ↓
signed MAC
      ↓
Conv1 buffer/calculation/quantization
      ↓
Pool1
      ↓
Conv2 buffer/calculation/quantization
      ↓
Pool2
      ↓
FC
      ↓
ArgMax
      ↓
cnn_mnist_top
```

## Golden comparison points

| Stage | Reference |
|---|---|
| Conv1 quant/ReLU | `data/golden_relu1.hex` |
| Pool1 | `data/golden_pool1.hex` |
| Conv2 quant/ReLU | `data/golden_relu2.hex` |
| Pool2 | `data/golden_pool2.hex` |
| FC logits | `data/golden.hex` |
| ArgMax | `data/prediction.hex` |

## Resource note

The Phase 1 reference architecture is intentionally parallel:

- Conv1: 36 theoretical multipliers
- Conv2: 288 theoretical multipliers
- FC: 80 theoretical multipliers
- Total: about 404 theoretical parallel multipliers

This is **not** the final accelerator architecture. Later phases will introduce GEMM/GEMV, FSM/datapath separation, memory reuse, arbitration, and PPA optimization.

## Roadmap

```text
PHASE 0  Arithmetic primitives
   ↓
PHASE 1  Bit-exact CNN reference datapath
   ↓
PHASE 2  FC → GEMM/GEMV engine
   ↓
PHASE 3  FSM + datapath separation
   ↓
PHASE 4  Memory architecture
   ↓
PHASE 5  Arbiter + accelerator control
   ↓
PHASE 6  PPA optimization
```

## Status

- [ ] Phase 0 — arithmetic primitives
- [ ] Conv1
- [ ] Pool1
- [ ] Conv2
- [ ] Pool2
- [ ] FC
- [ ] ArgMax
- [ ] Top-level CNN
- [ ] Bit-exact verification
- [ ] Multi-image regression

## License

Add the license appropriate for your project before publishing externally.

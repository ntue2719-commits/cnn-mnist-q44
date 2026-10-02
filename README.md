# CNN MNIST Q4.4 — RTL Accelerator

Bit-exact Verilog/SystemVerilog reference implementation of a small MNIST CNN using signed INT8 Q4.4 fixed-point arithmetic.

> **Phase 1 goal:** correctness and verification first.  
> PPA, throughput, MAC reuse, BRAM optimization, AXI and accelerator integration are later phases.

## Architecture

```text
MNIST 28x28x1 Q4.4
        │
        ▼
Conv1: 1 → 4, 3x3, stride 1
        │
        ▼
Round-even → Saturate → ReLU
        │
        ▼
MaxPool 2x2
        │
        ▼
Pool1: 4x13x13
        │
        ▼
Channel-major reorder → Feature Mem1
        │
        ▼
Conv2: 4 → 8, 3x3, stride 1
        │
        ▼
Round-even → Saturate → ReLU
        │
        ▼
MaxPool 2x2
        │
        ▼
Pool2: 8x5x5
        │
        ▼
Channel-major reorder → Feature Mem2
        │
        ▼
FC: 200 → 10
        │
        ▼
ArgMax
        │
        ▼
digit[3:0]
```

## Numerical Contract

| Item | Format / Rule |
|---|---|
| Input / weights / bias / feature maps | signed INT8 Q4.4 |
| Q4.4 scale | `16` |
| Q4.4 real value | `integer / 16` |
| Product | signed Q8.8 |
| Conv / FC accumulator | signed INT32 Q8.8 |
| Bias alignment | `bias << 4` |
| Requantization | round-to-nearest-even, then saturate to `[-128, 127]` |
| Activation | ReLU after requantization |
| FC output | raw signed INT32 Q8.8 logits |
| ArgMax tie rule | strict `>`; lowest index wins |

`python/golden_model.py` is the numerical source of truth.

## Repository Structure

```text
cnn-mnist-q44/
├── rtl/
│   ├── common/
│   │   ├── round_shift_even.v
│   │   ├── sat_int8.v
│   │   └── relu_int8.v
│   ├── memory/
│   │   ├── feature_mem1.v
│   │   └── feature_mem2.v
│   ├── rom/
│   │   ├── conv1_rom.v
│   │   ├── conv2_rom.v
│   │   └── fc_rom.v
│   ├── conv1/
│   │   ├── conv1_buf.v
│   │   ├── conv1_calc.v
│   │   ├── conv1_quant_relu.v
│   │   ├── pool1_buf.v
│   │   ├── pool1.v
│   │   ├── pool1_reorder.v
│   │   └── conv1_layer.v
│   ├── conv2/
│   │   ├── conv2_buf.v
│   │   ├── conv2_calc.v
│   │   ├── conv2_quant_relu.v
│   │   ├── pool2_buf.v
│   │   ├── pool2.v
│   │   ├── pool2_reorder.v
│   │   └── conv2_layer.v
│   ├── fc/
│   │   ├── fully_connected.v
│   │   └── argmax.v
│   ├── control/
│   │   └── top_fsm.v
│   └── top/
│       └── cnn_mnist_top.v
├── python/
│   ├── train_model_python.py
│   ├── golden_model.py
│   ├── export_hex.py
│   └── smoke_test_golden.py
├── tb/
├── data/
├── scripts/
├── docs/
│   ├── RTL_SPEC.md
│   └── VERIFICATION_ROADMAP.md
└── README.md
```

## Python Flow

| File | Purpose |
|---|---|
| `python/train_model_python.py` | Train the MNIST CNN and save the model checkpoint. |
| `python/golden_model.py` | Bit-exact fixed-point arithmetic used as the RTL reference. |
| `python/export_hex.py` | Export parameters, input data and golden checkpoints to `data/`. |
| `python/smoke_test_golden.py` | Sanity-test the golden arithmetic before generating RTL vectors. |

### Generate Verification Data

From the repository root:

```bash
cd python

python -m pip install -r requirements.txt

# Optional but recommended after changing golden_model.py
python smoke_test_golden.py

# Train once if model_q44.pth does not exist
python train_model_python.py

# Generate RTL parameters and golden checkpoints
python export_hex.py
```

Expected checkpoint files include:

```text
data/
├── input.hex
├── conv1_weight.hex
├── conv1_bias.hex
├── conv2_weight.hex
├── conv2_bias.hex
├── fc_weight.hex
├── fc_bias.hex
├── golden_conv1.hex
├── golden_relu1.hex
├── golden_pool1.hex
├── golden_conv2.hex
├── golden_relu2.hex
├── golden_pool2.hex
├── golden.hex
├── prediction.hex
└── label.hex
```

## Golden Checkpoints

| RTL stage | Golden file | Format |
|---|---|---|
| `conv1_calc` | `golden_conv1.hex` | 2704 × INT32 Q8.8 |
| `conv1_quant_relu` | `golden_relu1.hex` | 2704 × INT8 Q4.4 |
| Pool1 logical tensor | `golden_pool1.hex` | 676 × INT8 Q4.4 |
| `conv2_calc` | `golden_conv2.hex` | 968 × INT32 Q8.8 |
| `conv2_quant_relu` | `golden_relu2.hex` | 968 × INT8 Q4.4 |
| Pool2 logical tensor | `golden_pool2.hex` | 200 × INT8 Q4.4 |
| `fully_connected` | `golden.hex` | 10 × INT32 Q8.8 |
| `argmax` | `prediction.hex` | 1 × digit |

## Tensor and Memory Ordering

All multi-channel tensors use:

```text
[channel][row][column]
```

Canonical flatten index:

```text
index = c * (H * W) + y * W + x
```

Feature memories store this **channel-major flattened stream**.

Important: the Conv/Pool datapath naturally produces all channels for one spatial coordinate at the same time. That spatial word is **not** the canonical memory order. `pool1_reorder.v` and `pool2_reorder.v` convert the spatial stream into channel-major order before writing feature memory.

## Verification Strategy

Verify bottom-up and stop at the first mismatch:

```text
golden arithmetic smoke test
        ↓
round_shift_even / sat_int8 / relu_int8
        ↓
feature memories + ROMs
        ↓
Conv1 buffer → MAC → quant/ReLU → pool → reorder
        ↓
Conv2 buffer → MAC → quant/ReLU → pool → reorder
        ↓
FC
        ↓
ArgMax
        ↓
Top-level end-to-end inference
```

Do not debug the full CNN first. Every major stage has a Python checkpoint.

## Phase 1 Resource Strategy

The reference architecture intentionally favors simple, visible parallelism:

| Block | Reference parallel multipliers |
|---|---:|
| Conv1 | 36 |
| Conv2 | 288 |
| FC | 80 |
| **Total** | **404** |

These are reference datapaths, not the final optimized FPGA architecture.

Later versions can introduce MAC reuse, GEMM/GEMV, DSP mapping, BRAM-based storage, pipelining and shared compute engines.

## Status

- [ ] Golden arithmetic smoke test
- [ ] Common arithmetic RTL
- [ ] Feature memories / ROMs
- [ ] Conv1
- [ ] Pool1 + reorder
- [ ] Conv2
- [ ] Pool2 + reorder
- [ ] FC
- [ ] ArgMax
- [ ] Top-level integration
- [ ] Bit-exact single-image verification
- [ ] Multi-image regression
- [ ] Synthesis / PPA report

## Roadmap

```text
Phase 1  Bit-exact reference RTL
   ↓
Phase 2  MAC reuse / GEMM-GEMV refactor
   ↓
Phase 3  Datapath + controller separation
   ↓
Phase 4  BRAM / memory architecture
   ↓
Phase 5  Accelerator control + bus integration
   ↓
Phase 6  PPA optimization and FPGA benchmarking
```

See [`docs/RTL_SPEC.md`](docs/RTL_SPEC.md) for the locked module interfaces and [`docs/VERIFICATION_ROADMAP.md`](docs/VERIFICATION_ROADMAP.md) for the implementation order.

## License

Add a license before public release.

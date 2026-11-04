# V4N — Independent Golden / Trace Witness Audit (CANDIDATE)

**Scope:** Directed `IMG0000` in CNN_PEP V4c (40/40 frozen scenarios). No new Vivado build, XSim, FI campaign, RTL modification, protocol seal, or G6/G7 authorization.

## Why V4N follows V4M

V4M replays 40 SHA256-pinned `FI_RESULT_V2` records and obtains `L0=9`, `L1=20`, `L2=24`. Its four L2-only cases have `pool1_words=169`, `pool1_mismatch=1`, `pool2_words=25`, `pool2_mismatch=0`, `logits_mismatch=0` and `FINAL_OUTPUT_MATCH`. The Stage-2 counts are **original/isolated TB counters**, not an independently acquired output dump. V4N supplies a second evidence path based on **golden byte values and independently parsed value-aware consumer traces**. V4N does not import the V4M analyzer.

### Independent golden baseline

1. Pin SHA256 for `data/golden_relu1.hex` (2704 signed INT8/byte values; 4 x 26 x 26 channel-major) and the frozen IMG0000 `golden_pool1.hex` (676 bytes; 4 x 13 x 13).
2. Recompute all 676 bytes with Python `max(relu1[c,2*y+dy,2*x+dx])` for `dy,dx` in `{0,1}`. The computed output matches all 676/676 golden Pool1 bytes.
3. Pin the 200-byte Pool2 golden array, but do **not** claim an independent Pool2 numerical recomputation because this audit does not independently reconstruct Conv2.

### Four L2-only witnesses and what can actually be claimed

| Frozen scenario | Fault class | Independent witness | Counter-based finding | Important limit |
|---|---|---|---|---|
| `FI_P400_0101` | BUF_0007 A | `pool1_reorder.reorder_mem[337]`: canonical Pool1 byte 337 = `00`; V3b injected `00 -> 01`, traced through `MEM_WRITE`, `MEM_READ`, buffer capture and Conv2 MAC consumption | POOL1 169/169 words, mismatch 1; POOL2 25/25 words, mismatch 0; final match | No direct per-word faulty `dut.conv1_out_data` dump |
| `FI_P400_0102` | BUF_0007 B | Same byte and consumer chain, injection cycle differs (`795` vs `794`) | Same counters; final match | Same limitation |
| `FI_P400_0301` | DAT_0002 A | Conv1 quantized channel-0 byte at `(x=1,y=12)` changes `00 -> 01` and is accepted by Pool1 max input; golden 2x2 window `[0,0,0,0]` independently predicts Pool1 channel-0 byte index **78**, `00 -> 01` | Same counters; final match | Output `01` is **predicted**, not independently read from RTL |
| `FI_P400_0302` | DAT_0002 B | Conv1 quantized channel-0 byte at `(x=5,y=19)` changes `00 -> 01`; golden 2x2 window `[0,0,0,0]` independently predicts Pool1 byte index **119**, `00 -> 01` | Same counters; final match | Same limitation |

The V3b isolated testbenches for BUF_0007 and DAT_0002 have SHA256 values cross-checked against their V3b manifests; their entire POOL1/POOL2 comparison code region is byte-identical to the original frozen FI testbench. Each V3b raw log SHA256 matches both its own V3b manifest and the V4c matrix. The independent audit also checks source and log hashes and a separate parsing implementation.

### Negative evidence: sixteen L2 FALSE scenarios

V4N independently re-reads all 40 SHA256-linked raw `FI_RESULT_V2` records and isolates the **20 final-output-match** outcomes. Among these, four are L2-only; the other sixteen have full TB-counter observation **POOL1=169 words and POOL2=25 words** with zero mismatch counters and zero logits mismatches. These sixteen support *TB-observed stage MATCH*, **not** a 169/25-word standalone replay of faulty hardware output.

### Scientific interpretation and limits

- On the **same 40 directed IMG0000 scenario IDs**, L0 TRUE = 9, L1 TRUE = 20, L2 TRUE = 24; additional L2-only = 4/40 (+10 percentage points). A/B configurations are correlated, selected tuples, not independently sampled random physical SEUs.
- The independent golden pool calculation is exact over **golden** ReLU1, not a reconstruction of all **faulty** RTL activations. For DAT_0002, value-aware traces and independent arithmetic strongly corroborate why a POOL1 difference should be observable, but only the TB actually counted the corresponding faulty POOL1 mismatch.
- A complete numeric byte-by-byte comparison with faulty RTL output **requires a new, independently acquired stage dump/trace with a separate, approved execution plan**. V4N does not fabricate that evidence.
- E3 traces prove the selected accepted consumption paths; `FINAL_OUTPUT_MATCH` is not evidence of no internal effect. The study does not claim an on-chip detector.
- G7 L2 interpretation remains a **candidate**. The prepared protocol SHA256 must not be modified in place; a material change requires versioning and genuine two-person attestation. G6 production remains `NOT QUALIFIED`, Full400 remains `BLOCKED`.

## Windows instructions (read-only)

At the `cnn-mnist-q44-rtl` repository root after installing the additive V4N files:

```powershell
python -m unittest discover -s fi/v4/scripts -p "test_*v4n.py" -v

python fi/v4/scripts/validate_checkpoint_witness_v4n.py --repo-root . --mode check

python fi/v4/scripts/validate_checkpoint_witness_v4n.py `
  --repo-root . --mode analyze `
  --out-dir fi/results/v4n_stage_witness_01

python fi/v4/scripts/audit_checkpoint_witness_v4n.py `
  --repo-root . `
  --analysis-dir fi/results/v4n_stage_witness_01 `
  --out-dir fi/results/v4n_stage_witness_audit_01
```

Stop immediately if a command returns nonzero. Preserve all old/failed outputs; never reuse existing `--out-dir`. Do not patch historical raw logs, frozen sources, golden assets or catalogs. Both `--handoff-zip` and `--repo-root` are supported, but require **one source exactly**, and a historical ZIP must contain the exact preserved source bytes. Unit tests construct negative fixtures under temporary directories, not under the real evidence tree. On Windows the tests run against the repository's existing evidence; on an isolated container a `CNN_PEP_V4N_SOURCE_ZIP` environment variable may be provided.

**Expected markers:**

- `V4N_G7L2_GOLDEN_TRACE_PASS: 676/676 golden Pool1 bytes; 4/4 L2-only witnesses; 16/16 L2 FALSE full TB-counter coverage; L0=9 L1=20 L2=24; 0 XSim; G7 NOT APPROVED; FULL400 BLOCKED`
- `V4N_INDEPENDENT_AUDIT_PASS: 40/40 V4c raw SHA-pinned scenarios; 676/676 golden Pool1 bytes; 4/4 witnesses; 16/16 complete L2 false TB counters; 0 XSim; FULL400 BLOCKED`

**Versioned milestone:** `V4N_HISTORICAL_GOLDEN_TRACE_WITNESSES_LOCAL_PASS__G7_NOT_APPROVED__FULL400_BLOCKED` until Windows tests/replay are confirmed.

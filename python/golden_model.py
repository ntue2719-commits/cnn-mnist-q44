"""Bit-exact fixed-point primitives for the CNN-MNIST Q4.4 RTL project.

This module is the numerical source of truth for RTL verification.

Formats
-------
Input / weight / bias : signed INT8 Q4.4 (scale 16)
Product               : Q8.8
Conv raw accumulator  : signed INT32 Q8.8 checkpoint
Conv post-quant output: signed INT8 Q4.4
FC output             : signed INT32 Q8.8 raw logits

The important split is intentional:
  conv2d_acc_q88() -> raw INT32 accumulator checkpoint
  quant_relu_q44() -> round-to-nearest-even, saturate, ReLU

That split lets RTL testbenches independently verify conv_calc and
quant_relu stages without hiding an error in a combined software function.
"""

from __future__ import annotations

import numpy as np

SCALE = 16
Q_MIN = -128
Q_MAX = 127
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1


def quantize_q44(x):
    """Quantize floating-point value(s) to signed INT8 Q4.4.

    Rule: round-half-to-even(x * 16), clip to [-128, 127].
    NumPy round uses banker's rounding for these values.
    """
    q = np.round(np.asarray(x) * SCALE)
    q = np.clip(q, Q_MIN, Q_MAX)
    return q.astype(np.int8)


def round_shift_to_even(acc: int, bits: int = 4) -> int:
    """Arithmetic right shift with round-to-nearest-even.

    Works for positive and negative integers. For bits=4, this implements
    the Q8.8 -> Q4.4 requantization rule used by the RTL.
    """
    acc = int(acc)
    shifted = acc >> bits
    remainder = acc - (shifted << bits)  # always 0 .. 2**bits-1
    half = 1 << (bits - 1)

    if remainder > half:
        shifted += 1
    elif remainder == half and (shifted & 1):
        shifted += 1

    return shifted


def _check_int32(value: int, context: str = "accumulator") -> None:
    """Fail loudly if a Phase-1 RTL INT32 accumulator would overflow."""
    if value < INT32_MIN or value > INT32_MAX:
        raise OverflowError(
            f"{context}={value} is outside signed INT32 range "
            f"[{INT32_MIN}, {INT32_MAX}]"
        )


def int8_to_hex(x) -> str:
    return f"{int(x) & 0xFF:02X}"


def int32_to_hex(x) -> str:
    return f"{int(x) & 0xFFFFFFFF:08X}"


def write_int8_hex(filename, data) -> None:
    data = np.asarray(data).flatten()
    with open(filename, "w", encoding="ascii") as f:
        for value in data:
            f.write(int8_to_hex(value) + "\n")


def write_int32_hex(filename, data) -> None:
    data = np.asarray(data).flatten()
    with open(filename, "w", encoding="ascii") as f:
        for value in data:
            _check_int32(int(value), f"write_int32_hex({filename})")
            f.write(int32_to_hex(value) + "\n")


def conv2d_acc_q88(input_data, weight, bias):
    """Return RAW convolution accumulators as signed INT32 Q8.8.

    input_data : [Cin, H, W]          int8 Q4.4
    weight     : [Cout, Cin, Kh, Kw]  int8 Q4.4
    bias       : [Cout]               int8 Q4.4

    acc = (bias << 4) + sum(input * weight)

    No rounding, saturation, or ReLU is performed here.
    """
    input_data = np.asarray(input_data, dtype=np.int8)
    weight = np.asarray(weight, dtype=np.int8)
    bias = np.asarray(bias, dtype=np.int8)

    if input_data.ndim != 3 or weight.ndim != 4 or bias.ndim != 1:
        raise ValueError("Invalid Conv2D tensor rank")

    cin, h, w = input_data.shape
    cout, weight_cin, kh, kw = weight.shape
    if weight_cin != cin:
        raise ValueError(f"Conv channel mismatch: input Cin={cin}, weight Cin={weight_cin}")
    if len(bias) != cout:
        raise ValueError("Bias length does not match Cout")

    oh = h - kh + 1
    ow = w - kw + 1
    if oh <= 0 or ow <= 0:
        raise ValueError("Kernel is larger than input")

    output = np.zeros((cout, oh, ow), dtype=np.int32)

    for oc in range(cout):
        for y in range(oh):
            for x in range(ow):
                acc = int(bias[oc]) << 4

                for ic in range(cin):
                    for ky in range(kh):
                        for kx in range(kw):
                            a = int(input_data[ic, y + ky, x + kx])
                            wgt = int(weight[oc, ic, ky, kx])
                            acc += a * wgt

                _check_int32(acc, f"conv oc={oc}, y={y}, x={x}")
                output[oc, y, x] = acc

    return output


def requantize_q88_to_q44(acc_data):
    """RAW INT32 Q8.8 -> signed INT8 Q4.4, without ReLU."""
    acc_data = np.asarray(acc_data)
    output = np.empty(acc_data.shape, dtype=np.int8)

    for idx in np.ndindex(acc_data.shape):
        value = round_shift_to_even(int(acc_data[idx]), 4)
        value = min(Q_MAX, max(Q_MIN, value))
        output[idx] = value

    return output


def relu_q44(x):
    """ReLU for signed INT8 Q4.4."""
    x = np.asarray(x, dtype=np.int8)
    return np.maximum(x, 0).astype(np.int8)


def quant_relu_q44(acc_data):
    """RAW INT32 Q8.8 -> round-even -> saturate INT8 -> ReLU."""
    return relu_q44(requantize_q88_to_q44(acc_data))


def conv2d_q44(input_data, weight, bias):
    """Convenience wrapper returning post-quantization/ReLU Q4.4 output.

    RTL checkpoint generation should still use conv2d_acc_q88() and
    quant_relu_q44() separately so raw and post-quant checkpoints exist.
    """
    return quant_relu_q44(conv2d_acc_q88(input_data, weight, bias))


def maxpool2x2_q44(x):
    """2x2 max pool, stride 2, floor behavior for odd dimensions."""
    x = np.asarray(x, dtype=np.int8)
    if x.ndim != 3:
        raise ValueError("MaxPool input must have shape [C,H,W]")

    channels, h, w = x.shape
    oh = h // 2
    ow = w // 2
    output = np.zeros((channels, oh, ow), dtype=np.int8)

    for c in range(channels):
        for y in range(oh):
            for x_pos in range(ow):
                values = (
                    int(x[c, 2 * y, 2 * x_pos]),
                    int(x[c, 2 * y, 2 * x_pos + 1]),
                    int(x[c, 2 * y + 1, 2 * x_pos]),
                    int(x[c, 2 * y + 1, 2 * x_pos + 1]),
                )
                output[c, y, x_pos] = max(values)

    return output


def fc_q44(input_data, weight, bias):
    """Fully connected layer with raw signed INT32 Q8.8 logits.

    input  : [200]     INT8 Q4.4
    weight : [10,200]  INT8 Q4.4
    bias   : [10]      INT8 Q4.4
    output : [10]      INT32 Q8.8

    No requantization, saturation, or ReLU is applied to logits.
    """
    input_data = np.asarray(input_data, dtype=np.int8).reshape(-1)
    weight = np.asarray(weight, dtype=np.int8)
    bias = np.asarray(bias, dtype=np.int8).reshape(-1)

    if weight.ndim != 2:
        raise ValueError("FC weight must have shape [Cout, features]")
    if weight.shape[1] != input_data.size:
        raise ValueError("FC feature count mismatch")
    if weight.shape[0] != bias.size:
        raise ValueError("FC bias count mismatch")

    output = np.zeros(weight.shape[0], dtype=np.int32)

    for o in range(weight.shape[0]):
        acc = int(bias[o]) << 4
        for i in range(weight.shape[1]):
            acc += int(input_data[i]) * int(weight[o, i])
        _check_int32(acc, f"fc class={o}")
        output[o] = acc

    return output


def argmax_q44(logits) -> int:
    """Strict-`>` equivalent ArgMax: ties keep the smaller index."""
    logits = np.asarray(logits).reshape(-1)
    if logits.size == 0:
        raise ValueError("ArgMax requires at least one logit")
    return int(np.argmax(logits))

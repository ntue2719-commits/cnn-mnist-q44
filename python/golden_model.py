# golden_model.py
#
# Pure fixed-point Q4.4 Golden Model primitives.
#
# This module is the single source of truth for the
# integer arithmetic that RTL must reproduce bit-exactly:
#
#   Input     : Q4.4  (signed int8, scale = 16)
#   Weight    : Q4.4  (signed int8, scale = 16)
#   Product   : Q8.8  (signed, scale = 256)
#   Accumulator: wider Q-format (Python int / int32)
#   After accumulation : ReLU
#   After requantization: Q4.4  (round-to-nearest-even, saturate)
#
# See project spec, sections 6-9, 12, 16-17, 59-61.

import numpy as np


SCALE = 16          # Q4.4 scale (2**4)
Q_MIN = -128
Q_MAX = 127


# ============================================================
# Q4.4 quantization of a real-valued tensor/array
# ============================================================

def quantize_q44(x):
    """
    Convert floating-point value(s) to signed int8 Q4.4.

    Matches the Golden Model rule (spec section 8):

        step   = 1.0 / 16.0
        scaled = round(x / step)      == round(x * 16)
        clipped = saturate(scaled, -128, 127)

    NumPy's round() is round-half-to-even, same as
    torch.round() used during training/eval -- this keeps
    weight/bias/input export consistent with the floating
    point Golden Model.
    """

    q = np.round(x * SCALE)
    q = np.clip(q, Q_MIN, Q_MAX)

    return q.astype(np.int8)


# ============================================================
# Round-to-nearest-even right shift (Q8.8 -> Q4.4)
# ============================================================

def round_shift_to_even(acc, bits=4):
    """
    Divide a Q(m).8 fixed-point accumulator (plain Python int,
    arbitrary precision) by 2**bits, rounding half-to-even,
    to produce the Q(m).4 result.

    This is the RTL-facing equivalent of quantize_q44() applied
    directly to an integer accumulator instead of a float, and
    it is what makes the "round" step in spec sections 9/59-61
    match torch.round()'s banker's-rounding behavior at the
    boundary values called out in section 9
    (0.03125 / 0.09375 / -0.03125 / -0.09375, i.e. exact .5
    ties at this bit position).

    Plain arithmetic shift (`acc >> bits`) TRUNCATES (floors)
    instead of rounding and must not be used here.
    """

    shifted = acc >> bits                     # floor(acc / 2**bits)
    remainder = acc - (shifted << bits)        # in [0, 2**bits)

    half = 1 << (bits - 1)

    if remainder > half:
        shifted += 1
    elif remainder == half and (shifted & 1):
        # tie: round to even
        shifted += 1

    return shifted


# ============================================================
# int8 -> hexadecimal
# ============================================================

def int8_to_hex(x):
    """Convert signed int8 to 2-digit two's-complement hex."""

    value = int(x) & 0xFF
    return f"{value:02X}"


# ============================================================
# int32 -> hexadecimal
# ============================================================

def int32_to_hex(x):
    """Convert signed int32 to 8-digit two's-complement hex."""

    value = int(x) & 0xFFFFFFFF
    return f"{value:08X}"


# ============================================================
# HEX file writers
# ============================================================

def write_int8_hex(filename, data):
    """Write one signed int8 value per line (no '0x' prefix)."""

    data = np.asarray(data).flatten()

    with open(filename, "w") as f:
        for value in data:
            f.write(int8_to_hex(value))
            f.write("\n")


def write_int32_hex(filename, data):
    """Write one signed int32 value per line (no '0x' prefix)."""

    data = np.asarray(data).flatten()

    with open(filename, "w") as f:
        for value in data:
            f.write(int32_to_hex(value))
            f.write("\n")


# ============================================================
# Fixed-point Conv2D  (spec sections 12, 16-17, 59-61)
# ============================================================

def conv2d_q44(input_data, weight, bias):
    """
    Fixed-point 2D convolution, no padding, stride = 1.

    input_data : [Cin, H, W]         int8 Q4.4
    weight     : [Cout, Cin, Kh, Kw] int8 Q4.4
    bias       : [Cout]              int8 Q4.4

    Arithmetic contract (spec section 61):

        product = input * weight        Q4.4 * Q4.4 -> Q8.8
        bias_q88 = bias << 4            Q4.4 -> Q8.8
        acc = sum(product) + bias_q88   wider accumulator
        value = round_shift_to_even(acc, 4)   Q8.8 -> Q4.4
        output = saturate(value, -128, 127)
    """

    Cin, H, W = input_data.shape
    Cout, _, Kh, Kw = weight.shape

    OH = H - Kh + 1
    OW = W - Kw + 1

    output = np.zeros((Cout, OH, OW), dtype=np.int8)

    for oc in range(Cout):
        for y in range(OH):
            for x in range(OW):

                acc = int(bias[oc]) << 4   # Q4.4 -> Q8.8

                for ic in range(Cin):
                    for ky in range(Kh):
                        for kx in range(Kw):

                            a = int(input_data[ic, y + ky, x + kx])
                            w = int(weight[oc, ic, ky, kx])

                            # Q4.4 * Q4.4 -> Q8.8
                            acc += a * w

                # Q8.8 -> Q4.4, round-to-nearest-even
                value = round_shift_to_even(acc, 4)

                # Saturate
                if value > Q_MAX:
                    value = Q_MAX
                elif value < Q_MIN:
                    value = Q_MIN

                output[oc, y, x] = value

    return output


# ============================================================
# ReLU  (spec section 17: applied on the accumulator, before
# requantization -- mathematically identical here to applying
# it after conv2d_q44's own round+saturate step, since both
# quantize_q44/round_shift_to_even and saturation are monotonic
# and map 0 -> 0, so ReLU(quantize(x)) == quantize(ReLU(x)).)
# ============================================================

def relu_q44(x):
    """ReLU for signed int8 Q4.4."""

    return np.maximum(x, 0).astype(np.int8)


# ============================================================
# MaxPool 2x2, stride 2  (spec section 18)
# ============================================================

def maxpool2x2_q44(x):

    C, H, W = x.shape

    OH = H // 2
    OW = W // 2

    output = np.zeros((C, OH, OW), dtype=np.int8)

    for c in range(C):
        for y in range(OH):
            for x_pos in range(OW):

                a = int(x[c, y * 2,     x_pos * 2])
                b = int(x[c, y * 2,     x_pos * 2 + 1])
                c_val = int(x[c, y * 2 + 1, x_pos * 2])
                d = int(x[c, y * 2 + 1, x_pos * 2 + 1])

                output[c, y, x_pos] = max(a, b, c_val, d)

    return output


# ============================================================
# Fully-connected  (spec sections 28-29, 61-63)
# ============================================================

def fc_q44(input_data, weight, bias):
    """
    input  : Q4.4 int8   [200]
    weight : Q4.4 int8   [10, 200]
    bias   : Q4.4 int8   [10]

    Output stays as the raw Q8.8 int32 accumulator
    (NOT saturated / requantized to int8) so that RTL's
    ArgMax can compare at full accumulator precision --
    per spec section 29/63, this is what golden.hex holds.
    """

    input_data = input_data.astype(np.int64)
    weight = weight.astype(np.int64)
    bias = bias.astype(np.int64)

    num_outputs = weight.shape[0]
    output = np.zeros(num_outputs, dtype=np.int64)

    for o in range(num_outputs):

        acc = int(bias[o]) << 4   # Q4.4 -> Q8.8

        for i in range(weight.shape[1]):
            acc += int(input_data[i]) * int(weight[o, i])

        output[o] = acc

    return output.astype(np.int32)


# ============================================================
# ArgMax  (spec section 33: tie -> keep the smaller index)
# ============================================================

def argmax_q44(logits):
    """
    Return predicted digit 0..9.

    np.argmax already keeps the first (smallest-index) maximum
    on ties, matching spec section 33's tie-break rule.
    """

    return int(np.argmax(logits))
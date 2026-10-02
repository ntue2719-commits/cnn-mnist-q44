"""Small dependency-light arithmetic smoke test for golden_model.py."""

import numpy as np
from golden_model import (
    round_shift_to_even,
    conv2d_acc_q88,
    quant_relu_q44,
    maxpool2x2_q44,
    fc_q44,
    argmax_q44,
)

# Half-to-even boundaries, including negative values.
assert round_shift_to_even(8, 4) == 0       # +0.5 -> 0 (even)
assert round_shift_to_even(24, 4) == 2      # +1.5 -> 2 (even)
assert round_shift_to_even(-8, 4) == 0      # -0.5 -> 0 (even)
assert round_shift_to_even(-24, 4) == -2    # -1.5 -> -2 (even)

x = np.arange(9, dtype=np.int8).reshape(1, 3, 3)
w = np.ones((1, 1, 3, 3), dtype=np.int8)
b = np.array([1], dtype=np.int8)
raw = conv2d_acc_q88(x, w, b)
assert raw.shape == (1, 1, 1)
assert int(raw[0, 0, 0]) == (1 << 4) + sum(range(9))

q = quant_relu_q44(raw)
assert q.dtype == np.int8

pool_in = np.array([[[1, 2], [3, 4]]], dtype=np.int8)
assert int(maxpool2x2_q44(pool_in)[0, 0, 0]) == 4

features = np.array([1, 2], dtype=np.int8)
weights = np.array([[1, 1], [1, 1]], dtype=np.int8)
bias = np.array([0, 0], dtype=np.int8)
logits = fc_q44(features, weights, bias)
assert logits.tolist() == [3, 3]
assert argmax_q44(logits) == 0  # tie -> smaller index

print("golden_model smoke test: PASS")

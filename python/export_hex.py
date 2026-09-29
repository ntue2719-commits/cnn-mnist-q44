# export_hex.py
#
# Quantization + serialization (spec file-purpose table, section 73).
#
# Reads the trained model (model_q44.pth), quantizes all
# parameters to signed int8 Q4.4, runs the fixed-point Golden
# Model (golden_model.py) on one MNIST test image, and exports
# every .hex file RTL / testbenches need (spec sections 41-49, 63).

import os
import torch
import numpy as np
from torchvision import datasets, transforms

from golden_model import (
    SCALE,
    quantize_q44,
    write_int8_hex,
    write_int32_hex,
    conv2d_q44,
    relu_q44,
    maxpool2x2_q44,
    fc_q44,
    argmax_q44,
)


# ============================================================
# Configuration
# ============================================================

MODEL_FILE = "../model_q44.pth"
OUTPUT_DIR = "../data"

# Must be identical to the dataset root used by
# train_model_python.py so MNIST isn't downloaded twice.
# (PyTorch caches MNIST under data/MNIST/..., which doesn't
# collide with the data/*.hex files exported below.)
DATA_ROOT = "../data"

INPUT_SIZE = 28


# ============================================================
# Load model
# ============================================================

print("=" * 60)
print("Loading model")
print("=" * 60)

if not os.path.exists(MODEL_FILE):
    raise FileNotFoundError(f"Cannot find {MODEL_FILE}")

checkpoint = torch.load(MODEL_FILE, map_location="cpu")

print("Model loaded:", MODEL_FILE)


# ============================================================
# Extract parameters
# ============================================================

if "model_state_dict" in checkpoint:
    state_dict = checkpoint["model_state_dict"]
else:
    state_dict = checkpoint


def get_tensor(name):
    if name not in state_dict:
        raise KeyError(f"Cannot find parameter: {name}")
    return state_dict[name].detach().cpu().numpy()


conv1_weight_fp = get_tensor("conv1.weight")
conv1_bias_fp = get_tensor("conv1.bias")
conv2_weight_fp = get_tensor("conv2.weight")
conv2_bias_fp = get_tensor("conv2.bias")
fc_weight_fp = get_tensor("fc.weight")
fc_bias_fp = get_tensor("fc.bias")


# ============================================================
# Quantize parameters to Q4.4
# ============================================================

print()
print("=" * 60)
print("Quantizing parameters to Q4.4")
print("=" * 60)

conv1_weight = quantize_q44(conv1_weight_fp)
conv1_bias = quantize_q44(conv1_bias_fp)
conv2_weight = quantize_q44(conv2_weight_fp)
conv2_bias = quantize_q44(conv2_bias_fp)
fc_weight = quantize_q44(fc_weight_fp)
fc_bias = quantize_q44(fc_bias_fp)


# ============================================================
# Create output directory
# ============================================================

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# Export weights and biases
# ============================================================
#
# Export order (spec sections 43-45-46):
#
#   conv1_weight : [4][1][3][3]  -> out_ch, in_ch, ky, kx   (36 values)
#   conv2_weight : [8][4][3][3]  -> out_ch, in_ch, ky, kx   (288 values)
#   fc_weight    : [10][200]     -> out_class, feature      (2000 values)
#   biases       : bias[0..N-1] in class/channel order
#
# numpy .flatten() on a C-contiguous array (PyTorch tensors
# converted with .numpy() are C-contiguous) walks the LAST axis
# fastest and the FIRST axis slowest, which is exactly the
# nested-loop order the spec lists above -- so a plain
# .flatten() already produces the required export order.

print()
print("=" * 60)
print("Writing parameter HEX files")
print("=" * 60)

write_int8_hex(f"{OUTPUT_DIR}/conv1_weight.hex", conv1_weight)
write_int8_hex(f"{OUTPUT_DIR}/conv1_bias.hex", conv1_bias)
write_int8_hex(f"{OUTPUT_DIR}/conv2_weight.hex", conv2_weight)
write_int8_hex(f"{OUTPUT_DIR}/conv2_bias.hex", conv2_bias)
write_int8_hex(f"{OUTPUT_DIR}/fc_weight.hex", fc_weight)
write_int8_hex(f"{OUTPUT_DIR}/fc_bias.hex", fc_bias)


# ============================================================
# Load MNIST sample
# ============================================================

print()
print("=" * 60)
print("Loading MNIST sample")
print("=" * 60)

# IMPORTANT (spec section 11/47):
# The model is TRAINED on Normalize((0.1307,), (0.3081,)) input.
# input.hex must go through the exact same preprocessing before
# Q4.4 quantization -- raw 0..255 / 0..1 pixels must never be fed
# to the RTL directly.
transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize((0.1307,), (0.3081,))
])

dataset = datasets.MNIST(
    root=DATA_ROOT,
    train=False,
    download=True,
    transform=transform
)

image, label = dataset[3]
image_np = image.numpy()[0]

print("Label:", label)
print("Image shape:", image_np.shape)


# ============================================================
# Quantize + export input
# ============================================================

input_q44 = quantize_q44(image_np)
input_q44 = input_q44.reshape(1, INPUT_SIZE, INPUT_SIZE)

write_int8_hex(f"{OUTPUT_DIR}/input.hex", input_q44)


# ============================================================
# Golden Model  (layer order per spec section 5)
# ============================================================

print()
print("=" * 60)
print("Running fixed-point Golden Model")
print("=" * 60)

# ---- Conv1 -> ReLU -> Q4.4 (done inside conv2d_q44) -> MaxPool1
conv1_out = conv2d_q44(input_q44, conv1_weight, conv1_bias)
print("Conv1:", conv1_out.shape)

relu1_out = relu_q44(conv1_out)
pool1_out = maxpool2x2_q44(relu1_out)
print("Pool1:", pool1_out.shape)

# ---- Conv2 -> ReLU -> Q4.4 -> MaxPool2
conv2_out = conv2d_q44(pool1_out, conv2_weight, conv2_bias)
print("Conv2:", conv2_out.shape)

relu2_out = relu_q44(conv2_out)
pool2_out = maxpool2x2_q44(relu2_out)
print("Pool2:", pool2_out.shape)

# ---- Flatten (spec section 32):
#      for channel = 0..7: for row = 0..4: for col = 0..4
#      pool2_out has shape [C, H, W] = [8, 5, 5], C-contiguous,
#      so .flatten() already walks channel-major, row, col.
flatten = pool2_out.flatten()
print("Flatten:", flatten.shape)

# ---- FC: raw Q8.8 int32 accumulator, NOT saturated (spec 29/63)
logits_q88 = fc_q44(flatten, fc_weight, fc_bias)
print("FC logits:", logits_q88)

# ---- ArgMax (tie -> smaller index, spec section 33)
prediction = argmax_q44(logits_q88)

print()
print("Golden prediction:", prediction)
print("Actual label     :", int(label))
print("RESULT:", "PASS" if prediction == int(label) else "FAIL")


# ============================================================
# Export Golden Model outputs (spec sections 48-49, 63)
# ============================================================

write_int32_hex(f"{OUTPUT_DIR}/golden.hex", logits_q88)
write_int8_hex(f"{OUTPUT_DIR}/label.hex", np.array([label], dtype=np.int8))
write_int8_hex(f"{OUTPUT_DIR}/prediction.hex", np.array([prediction], dtype=np.int8))

# Per-layer dumps for hierarchical debugging (spec section 49-50)
write_int8_hex(f"{OUTPUT_DIR}/golden_conv1.hex", conv1_out)
write_int8_hex(f"{OUTPUT_DIR}/golden_relu1.hex", relu1_out)
write_int8_hex(f"{OUTPUT_DIR}/golden_pool1.hex", pool1_out)
write_int8_hex(f"{OUTPUT_DIR}/golden_conv2.hex", conv2_out)
write_int8_hex(f"{OUTPUT_DIR}/golden_relu2.hex", relu2_out)
write_int8_hex(f"{OUTPUT_DIR}/golden_pool2.hex", pool2_out)


# ============================================================
# Summary
# ============================================================

print()
print("=" * 60)
print("EXPORT COMPLETE")
print("=" * 60)

files = [
    "conv1_weight.hex", "conv1_bias.hex",
    "conv2_weight.hex", "conv2_bias.hex",
    "fc_weight.hex", "fc_bias.hex",
    "input.hex", "label.hex", "prediction.hex", "golden.hex",
    "golden_conv1.hex", "golden_relu1.hex", "golden_pool1.hex",
    "golden_conv2.hex", "golden_relu2.hex", "golden_pool2.hex",
]

print()
print("Generated files:")
for filename in files:
    print(f"  {OUTPUT_DIR}/{filename}")

print()
print("Q4.4 scale:", SCALE)
print("Input     :", "28x28x1")
print("Conv1     :", "4x26x26")
print("Pool1     :", "4x13x13")
print("Conv2     :", "8x11x11")
print("Pool2     :", "8x5x5")
print("FC        :", "10 logits")
print("Prediction:", prediction)
print("Label     :", int(label))
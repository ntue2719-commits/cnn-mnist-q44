"""Export model parameters, one MNIST input, and bit-exact RTL checkpoints."""

from __future__ import annotations

import os
import numpy as np
import torch
from torchvision import datasets, transforms

from golden_model import (
    SCALE,
    quantize_q44,
    write_int8_hex,
    write_int32_hex,
    conv2d_acc_q88,
    quant_relu_q44,
    maxpool2x2_q44,
    fc_q44,
    argmax_q44,
)

MODEL_FILE = "../model_q44.pth"
OUTPUT_DIR = "../data"
DATA_ROOT = "../data"
INPUT_SIZE = 28
SAMPLE_INDEX = 3


def get_tensor(state_dict, name):
    if name not in state_dict:
        raise KeyError(f"Cannot find parameter: {name}")
    return state_dict[name].detach().cpu().numpy()


def main():
    print("=" * 64)
    print("CNN MNIST Q4.4 - RTL CHECKPOINT EXPORT")
    print("=" * 64)

    if not os.path.exists(MODEL_FILE):
        raise FileNotFoundError(
            f"Cannot find {MODEL_FILE}. Run train_model_python.py first "
            "or place model_q44.pth in the project root."
        )

    checkpoint = torch.load(MODEL_FILE, map_location="cpu")
    state_dict = checkpoint.get("model_state_dict", checkpoint)

    conv1_weight = quantize_q44(get_tensor(state_dict, "conv1.weight"))
    conv1_bias = quantize_q44(get_tensor(state_dict, "conv1.bias"))
    conv2_weight = quantize_q44(get_tensor(state_dict, "conv2.weight"))
    conv2_bias = quantize_q44(get_tensor(state_dict, "conv2.bias"))
    fc_weight = quantize_q44(get_tensor(state_dict, "fc.weight"))
    fc_bias = quantize_q44(get_tensor(state_dict, "fc.bias"))

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Parameters: canonical C-order is exactly the locked tensor order.
    write_int8_hex(f"{OUTPUT_DIR}/conv1_weight.hex", conv1_weight)
    write_int8_hex(f"{OUTPUT_DIR}/conv1_bias.hex", conv1_bias)
    write_int8_hex(f"{OUTPUT_DIR}/conv2_weight.hex", conv2_weight)
    write_int8_hex(f"{OUTPUT_DIR}/conv2_bias.hex", conv2_bias)
    write_int8_hex(f"{OUTPUT_DIR}/fc_weight.hex", fc_weight)
    write_int8_hex(f"{OUTPUT_DIR}/fc_bias.hex", fc_bias)

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,)),
    ])

    dataset = datasets.MNIST(
        root=DATA_ROOT,
        train=False,
        download=True,
        transform=transform,
    )

    image, label = dataset[SAMPLE_INDEX]
    image_np = image.numpy()[0]
    input_q44 = quantize_q44(image_np).reshape(1, INPUT_SIZE, INPUT_SIZE)
    write_int8_hex(f"{OUTPUT_DIR}/input.hex", input_q44)

    # ------------------------------------------------------------
    # Bit-exact staged inference.
    # IMPORTANT: raw Conv checkpoints are INT32 Q8.8.
    # ------------------------------------------------------------
    conv1_acc = conv2d_acc_q88(input_q44, conv1_weight, conv1_bias)
    relu1_out = quant_relu_q44(conv1_acc)
    pool1_out = maxpool2x2_q44(relu1_out)

    conv2_acc = conv2d_acc_q88(pool1_out, conv2_weight, conv2_bias)
    relu2_out = quant_relu_q44(conv2_acc)
    pool2_out = maxpool2x2_q44(relu2_out)

    # C-contiguous [C,H,W].flatten() -> channel-major [c][y][x].
    flatten = pool2_out.flatten()
    logits_q88 = fc_q44(flatten, fc_weight, fc_bias)
    prediction = argmax_q44(logits_q88)

    # Golden checkpoints.
    write_int32_hex(f"{OUTPUT_DIR}/golden_conv1.hex", conv1_acc)
    write_int8_hex(f"{OUTPUT_DIR}/golden_relu1.hex", relu1_out)
    write_int8_hex(f"{OUTPUT_DIR}/golden_pool1.hex", pool1_out)
    write_int32_hex(f"{OUTPUT_DIR}/golden_conv2.hex", conv2_acc)
    write_int8_hex(f"{OUTPUT_DIR}/golden_relu2.hex", relu2_out)
    write_int8_hex(f"{OUTPUT_DIR}/golden_pool2.hex", pool2_out)
    write_int32_hex(f"{OUTPUT_DIR}/golden.hex", logits_q88)
    write_int8_hex(f"{OUTPUT_DIR}/prediction.hex", np.array([prediction], dtype=np.int8))
    write_int8_hex(f"{OUTPUT_DIR}/label.hex", np.array([label], dtype=np.int8))

    expected_counts = {
        "conv1_weight.hex": 36,
        "conv1_bias.hex": 4,
        "conv2_weight.hex": 288,
        "conv2_bias.hex": 8,
        "fc_weight.hex": 2000,
        "fc_bias.hex": 10,
        "input.hex": 784,
        "golden_conv1.hex": 2704,
        "golden_relu1.hex": 2704,
        "golden_pool1.hex": 676,
        "golden_conv2.hex": 968,
        "golden_relu2.hex": 968,
        "golden_pool2.hex": 200,
        "golden.hex": 10,
        "prediction.hex": 1,
        "label.hex": 1,
    }

    print("\nGenerated checkpoints:")
    for name, count in expected_counts.items():
        path = os.path.join(OUTPUT_DIR, name)
        with open(path, "r", encoding="ascii") as f:
            actual = sum(1 for _ in f)
        status = "OK" if actual == count else "ERROR"
        print(f"  {status:5s} {name:22s} {actual:4d} lines (expected {count})")
        if actual != count:
            raise RuntimeError(f"Unexpected line count in {name}")

    print("\nShapes:")
    print("  input       :", input_q44.shape, "INT8 Q4.4")
    print("  conv1 raw   :", conv1_acc.shape, "INT32 Q8.8")
    print("  relu1       :", relu1_out.shape, "INT8 Q4.4")
    print("  pool1       :", pool1_out.shape, "INT8 Q4.4")
    print("  conv2 raw   :", conv2_acc.shape, "INT32 Q8.8")
    print("  relu2       :", relu2_out.shape, "INT8 Q4.4")
    print("  pool2       :", pool2_out.shape, "INT8 Q4.4")
    print("  fc logits   :", logits_q88.shape, "INT32 Q8.8")
    print("\nQ4.4 scale       :", SCALE)
    print("Sample index      :", SAMPLE_INDEX)
    print("Golden prediction :", prediction)
    print("Dataset label     :", int(label))
    print("Classification    :", "PASS" if prediction == int(label) else "DIFF")


if __name__ == "__main__":
    main()

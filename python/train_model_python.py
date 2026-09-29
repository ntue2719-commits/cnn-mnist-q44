import os
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms
from torch.utils.data import DataLoader


# ============================================================
# CONFIGURATION
# ============================================================

SEED = 42

BATCH_SIZE_TRAIN = 64
BATCH_SIZE_TEST = 1000

EPOCHS = 2
LEARNING_RATE = 0.01

MODEL_FILE = "../model_q44.pth"
DATA_ROOT = "../data"

# Q4.4 configuration
Q_FRAC_BITS = 4
Q_SCALE = 1 << Q_FRAC_BITS       # 16
Q_MIN = -128
Q_MAX = 127


# ============================================================
# REPRODUCIBILITY
# ============================================================

torch.manual_seed(SEED)

if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)


# ============================================================
# Q4.4 QUANTIZATION
# ============================================================

def quantize_to_q44(tensor):
    """
    Quantize a floating-point tensor to signed Q4.4.

    Q4.4:
        Total bits     = 8
        Fraction bits  = 4
        Scale          = 16

    Integer range:
        -128 ... +127

    Real range:
        -8.0 ... +7.9375

    Resolution:
        0.0625
    """

    scaled = torch.round(tensor * Q_SCALE)

    clipped = torch.clamp(
        scaled,
        Q_MIN,
        Q_MAX
    )

    return clipped / Q_SCALE


# ============================================================
# CONVERT FLOATING POINT TO INT8 Q4.4
# ============================================================

def tensor_to_q44_int8(tensor):
    """
    Convert floating-point tensor to integer representation
    used by RTL.

    Example:

        1.0     -> 16
        0.5     -> 8
        -1.0    -> -16
        0.0625  -> 1
    """

    scaled = torch.round(tensor * Q_SCALE)

    scaled = torch.clamp(
        scaled,
        Q_MIN,
        Q_MAX
    )

    return scaled.to(torch.int8)


# ============================================================
# CNN MODEL
# ============================================================

class MNIST_CNN(nn.Module):

    def __init__(self):
        super(MNIST_CNN, self).__init__()

        # ----------------------------------------------------
        # Conv1
        # 1 input channel
        # 4 output channels
        # 3x3 kernel
        # stride = 1
        # padding = 0
        # bias = True
        # ----------------------------------------------------

        self.conv1 = nn.Conv2d(
            in_channels=1,
            out_channels=4,
            kernel_size=3,
            stride=1,
            padding=0,
            bias=True
        )

        # 26x26 -> 13x13

        self.pool1 = nn.MaxPool2d(
            kernel_size=2,
            stride=2
        )

        # ----------------------------------------------------
        # Conv2
        # 4 input channels
        # 8 output channels
        # 3x3 kernel
        # ----------------------------------------------------

        self.conv2 = nn.Conv2d(
            in_channels=4,
            out_channels=8,
            kernel_size=3,
            stride=1,
            padding=0,
            bias=True
        )

        # 11x11 -> 5x5

        self.pool2 = nn.MaxPool2d(
            kernel_size=2,
            stride=2
        )

        # ----------------------------------------------------
        # Fully Connected
        # 5x5x8 = 200
        # 10 classes
        # ----------------------------------------------------

        self.fc = nn.Linear(
            5 * 5 * 8,
            10,
            bias=True
        )

        self.relu = nn.ReLU()

    # --------------------------------------------------------
    # FLOATING-POINT FORWARD
    # --------------------------------------------------------

    def forward(self, x):

        x = self.relu(
            self.conv1(x)
        )

        x = self.pool1(x)

        x = self.relu(
            self.conv2(x)
        )

        x = self.pool2(x)

        x = x.view(
            -1,
            5 * 5 * 8
        )

        x = self.fc(x)

        return x


# ============================================================
# QUANTIZED FORWARD (approximate float-simulated Q4.4 pass)
# ============================================================
#
# NOTE: this is an APPROXIMATE, float-simulated Q4.4 pass used
# only to report a quick "how much does quantization hurt
# accuracy" number during training. It is NOT the bit-exact
# fixed-point pipeline RTL must match -- that pipeline (integer
# Q8.8 accumulation, round-to-nearest-even right-shift, explicit
# saturation) lives in python/golden_model.py and is exercised
# per-sample by python/export_hex.py, which is the actual
# reference used to generate golden.hex / RTL testbench data
# (spec sections 2.2, 59-61).

def quantized_forward(model, x):
    """
    Q4.4 software Golden Model (approximate, see note above).

    This follows the same sequence used by the original
    training/evaluation script:

        Input
          ↓
        Q4.4
          ↓
        Conv1
          ↓
        ReLU
          ↓
        Q4.4
          ↓
        Pool1
          ↓
        Conv2
          ↓
        ReLU
          ↓
        Q4.4
          ↓
        Pool2
          ↓
        FC
          ↓
        logits
    """

    # --------------------------------------------------------
    # Input quantization
    # --------------------------------------------------------

    x = quantize_to_q44(x)

    # --------------------------------------------------------
    # Conv1
    # --------------------------------------------------------

    w1_q = quantize_to_q44(
        model.conv1.weight
    )

    b1_q = quantize_to_q44(
        model.conv1.bias
    )

    x = nn.functional.conv2d(
        x,
        w1_q,
        b1_q,
        stride=1,
        padding=0
    )

    # ReLU

    x = model.relu(x)

    # Q4.4

    x = quantize_to_q44(x)

    # MaxPool

    x = model.pool1(x)

    # --------------------------------------------------------
    # Conv2
    # --------------------------------------------------------

    w2_q = quantize_to_q44(
        model.conv2.weight
    )

    b2_q = quantize_to_q44(
        model.conv2.bias
    )

    x = nn.functional.conv2d(
        x,
        w2_q,
        b2_q,
        stride=1,
        padding=0
    )

    # ReLU

    x = model.relu(x)

    # Q4.4

    x = quantize_to_q44(x)

    # MaxPool

    x = model.pool2(x)

    # --------------------------------------------------------
    # Flatten
    # --------------------------------------------------------

    x = x.view(
        -1,
        5 * 5 * 8
    )

    # --------------------------------------------------------
    # FC
    # --------------------------------------------------------

    wfc_q = quantize_to_q44(
        model.fc.weight
    )

    bfc_q = quantize_to_q44(
        model.fc.bias
    )

    x = nn.functional.linear(
        x,
        wfc_q,
        bfc_q
    )

    return x


# ============================================================
# DATASET
# ============================================================

def load_dataset():

    transform = transforms.Compose([
        transforms.ToTensor(),

        transforms.Normalize(
            (0.1307,),
            (0.3081,)
        )
    ])

    train_dataset = datasets.MNIST(
        root=DATA_ROOT,
        train=True,
        download=True,
        transform=transform
    )

    test_dataset = datasets.MNIST(
        root=DATA_ROOT,
        train=False,
        download=True,
        transform=transform
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE_TRAIN,
        shuffle=True
    )

    test_loader = DataLoader(
        test_dataset,
        batch_size=BATCH_SIZE_TEST,
        shuffle=False
    )

    return train_loader, test_loader


# ============================================================
# TRAINING
# ============================================================

def train_model(
    model,
    train_loader,
    device
):

    criterion = nn.CrossEntropyLoss()

    optimizer = optim.Adam(
        model.parameters(),
        lr=LEARNING_RATE
    )

    print()
    print("========================================")
    print("Training MNIST CNN")
    print("========================================")

    for epoch in range(EPOCHS):

        model.train()

        running_loss = 0.0

        for images, labels in train_loader:

            images = images.to(device)
            labels = labels.to(device)

            optimizer.zero_grad()

            outputs = model(images)

            loss = criterion(
                outputs,
                labels
            )

            loss.backward()

            optimizer.step()

            running_loss += loss.item()

        avg_loss = (
            running_loss /
            len(train_loader)
        )

        print(
            "Epoch {}/{} - Loss: {:.6f}".format(
                epoch + 1,
                EPOCHS,
                avg_loss
            )
        )


# ============================================================
# FLOATING-POINT EVALUATION
# ============================================================

def evaluate_float(
    model,
    test_loader,
    device
):

    model.eval()

    correct = 0
    total = 0

    with torch.no_grad():

        for images, labels in test_loader:

            images = images.to(device)
            labels = labels.to(device)

            outputs = model(images)

            _, predictions = torch.max(
                outputs,
                1
            )

            correct += (
                predictions == labels
            ).sum().item()

            total += labels.size(0)

    accuracy = (
        100.0 * correct / total
    )

    return accuracy


# ============================================================
# Q4.4 EVALUATION (approximate -- see quantized_forward() note)
# ============================================================

def evaluate_q44(
    model,
    test_loader,
    device
):

    model.eval()

    correct = 0
    total = 0

    with torch.no_grad():

        for images, labels in test_loader:

            images = images.to(device)
            labels = labels.to(device)

            outputs = quantized_forward(
                model,
                images
            )

            _, predictions = torch.max(
                outputs,
                1
            )

            correct += (
                predictions == labels
            ).sum().item()

            total += labels.size(0)

    accuracy = (
        100.0 * correct / total
    )

    return accuracy


# ============================================================
# SAVE MODEL
# ============================================================

def save_model(model):

    torch.save(
        model.state_dict(),
        MODEL_FILE
    )

    print()
    print(
        "Model saved to: {}".format(
            MODEL_FILE
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("========================================")
    print("CNN MNIST Q4.4")
    print("========================================")

    device = torch.device(
        "cuda"
        if torch.cuda.is_available()
        else "cpu"
    )

    print(
        "Device:",
        device
    )

    # --------------------------------------------------------
    # Dataset
    # --------------------------------------------------------

    train_loader, test_loader = load_dataset()

    # --------------------------------------------------------
    # Model
    # --------------------------------------------------------

    model = MNIST_CNN().to(device)

    # --------------------------------------------------------
    # Train
    # --------------------------------------------------------

    train_model(
        model,
        train_loader,
        device
    )

    # --------------------------------------------------------
    # Floating-point accuracy
    # --------------------------------------------------------

    float_accuracy = evaluate_float(
        model,
        test_loader,
        device
    )

    # --------------------------------------------------------
    # Q4.4 accuracy (approximate)
    # --------------------------------------------------------

    q44_accuracy = evaluate_q44(
        model,
        test_loader,
        device
    )

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    print()
    print("========================================")
    print("FINAL TEST ACCURACY")
    print("========================================")

    print(
        "Floating-point : {:.2f}%".format(
            float_accuracy
        )
    )

    print(
        "Q4.4 (approx)  : {:.2f}%".format(
            q44_accuracy
        )
    )

    # --------------------------------------------------------
    # Save trained model
    # --------------------------------------------------------

    save_model(model)

    print()
    print("Training complete.")
    print("Next step: run export_hex.py to generate the")
    print("bit-exact fixed-point Golden Model .hex files.")


if __name__ == "__main__":
    main()
"""Train the BiLSTM KPI classifier and save weights to MODEL_PATH.

Builds labelled SEQ_LEN sequences from the per-class sampler in
dataset_generator (separate 4G/5G archetypes), balances mini-batches with a
WeightedRandomSampler, trains a few epochs, and saves the state dict.
"""
import logging
import os
import random

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler

from dataset_generator import ARCHETYPES, CLASS_DIST, sample_kpi
from features import CLASS_IDX, SEQ_LEN, extract_features, normalise
from model import KPIClassifier

log = logging.getLogger("kpi_agent.train")

N_SEQUENCES = int(os.environ.get("TRAIN_SEQUENCES", "6000"))
EPOCHS = int(os.environ.get("TRAIN_EPOCHS", "12"))
BATCH = 64


def _make_sequence(cls: str) -> list[list[float]]:
    """SEQ_LEN temporally-smooth raw feature vectors for one class label."""
    arch = random.choice(ARCHETYPES)
    return [extract_features(sample_kpi(cls, arch)) for _ in range(SEQ_LEN)]


def build_dataset(n: int):
    classes = list(CLASS_DIST.keys())
    weights = [CLASS_DIST[c] for c in classes]
    X, y = [], []
    for _ in range(n):
        cls = random.choices(classes, weights=weights, k=1)[0]
        seq = [normalise(v) for v in _make_sequence(cls)]
        X.append(seq)
        y.append(CLASS_IDX[cls])
    return torch.tensor(X, dtype=torch.float32), torch.tensor(y, dtype=torch.long)


def train(model_path: str, seed: int = 42) -> str:
    random.seed(seed)
    torch.manual_seed(seed)
    logging.basicConfig(level=logging.INFO)

    X, y = build_dataset(N_SEQUENCES)
    # class-balanced sampling
    class_counts = torch.bincount(y, minlength=len(CLASS_IDX)).float()
    sample_w = (1.0 / class_counts)[y]
    sampler = WeightedRandomSampler(sample_w, num_samples=len(y), replacement=True)
    loader = DataLoader(TensorDataset(X, y), batch_size=BATCH, sampler=sampler)

    model = KPIClassifier()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()

    model.train()
    for epoch in range(EPOCHS):
        total, correct, loss_sum = 0, 0, 0.0
        for xb, yb in loader:
            opt.zero_grad()
            out = model(xb)
            loss = loss_fn(out, yb)
            loss.backward()
            opt.step()
            loss_sum += loss.item() * len(yb)
            correct += (out.argmax(1) == yb).sum().item()
            total += len(yb)
        log.info("epoch %2d/%d  loss=%.4f  acc=%.3f",
                 epoch + 1, EPOCHS, loss_sum / total, correct / total)

    torch.save(model.state_dict(), model_path)
    log.info("saved model -> %s", model_path)
    return model_path


if __name__ == "__main__":
    train(os.environ.get("MODEL_PATH", "kpi_model.pt"))

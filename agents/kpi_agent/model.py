"""Bidirectional-LSTM cell-state classifier (KPIClassifier).

Input  : (batch, SEQ_LEN=6, N_FEATURES=9)  — 60 s of per-cell history.
Output : 5 classes — NORMAL / OVERLOAD / UNDERLOAD / SINR_LOW / POWER_WASTE.
"""
import torch
import torch.nn as nn

from features import N_CLASSES, N_FEATURES, SEQ_LEN, normalise


class KPIClassifier(nn.Module):
    def __init__(self, n_features: int = N_FEATURES, hidden: int = 64,
                 n_classes: int = N_CLASSES, dropout: float = 0.25):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=n_features, hidden_size=hidden, num_layers=2,
            batch_first=True, bidirectional=True, dropout=dropout)
        self.head = nn.Sequential(
            nn.Linear(hidden * 2, hidden), nn.ReLU(),
            nn.Dropout(dropout), nn.Linear(hidden, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)            # (B, T, 2*hidden)
        last = out[:, -1, :]            # final timestep, both directions
        return self.head(last)          # (B, n_classes)


@torch.no_grad()
def infer(model: KPIClassifier, window: list[list[float]]) -> tuple[int, float]:
    """Classify one SEQ_LEN window of raw feature vectors.

    Returns (class_index, confidence). `window` is a list of SEQ_LEN raw
    9-feature vectors (oldest first); they are normalised here.
    """
    model.eval()
    norm = [normalise(v) for v in window]
    x = torch.tensor([norm], dtype=torch.float32)   # (1, T, F)
    probs = torch.softmax(model(x), dim=1)[0]
    conf, idx = torch.max(probs, dim=0)
    return int(idx.item()), float(conf.item())


def load_model(path: str) -> KPIClassifier:
    model = KPIClassifier()
    model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    model.eval()
    return model

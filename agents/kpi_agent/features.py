"""Feature definitions and normalisation for the KPI classifier.

The 9-feature vector order is fixed and shared by training (train.py),
inference (kpi_agent.py), and the dataset generator. Normalisation ranges cover
both 4G and 5G hardware so a single model handles all cell types.
"""

# Fixed feature order (subset of cell_kpi fields — see docs/schema.md).
FEATURE_NAMES = [
    "prb_dl_pct", "sinr_db", "connected_ues", "power_w", "packet_loss_pct",
    "dl_throughput_mbps", "cqi", "bler_pct", "latency_ms",
]
N_FEATURES = len(FEATURE_NAMES)
SEQ_LEN = 6

# Per-feature (min, range) for min-max normalisation to [0, 1].
FEATURE_NORM = {
    "prb_dl_pct":          (0.0, 100.0),
    "sinr_db":             (-5.0, 35.0),
    "connected_ues":       (0.0, 900.0),
    "power_w":             (40.0, 290.0),
    "packet_loss_pct":     (0.0, 20.0),
    "dl_throughput_mbps":  (0.0, 3800.0),
    "cqi":                 (1.0, 14.0),
    "bler_pct":            (0.0, 100.0),
    "latency_ms":          (1.0, 99.0),
}

CLASS_NAMES = ["NORMAL", "OVERLOAD", "UNDERLOAD", "SINR_LOW", "POWER_WASTE"]
N_CLASSES = len(CLASS_NAMES)
CLASS_IDX = {n: i for i, n in enumerate(CLASS_NAMES)}


def extract_features(kpi: dict) -> list[float]:
    """Pull the 9 features (raw, unnormalised) from a cell_kpi dict."""
    return [float(kpi.get(f, 0.0) or 0.0) for f in FEATURE_NAMES]


def normalise(vec: list[float]) -> list[float]:
    """Min-max normalise a raw feature vector to [0, 1]."""
    out = []
    for name, x in zip(FEATURE_NAMES, vec):
        lo, rng = FEATURE_NORM[name]
        out.append(min(1.0, max(0.0, (x - lo) / rng)))
    return out

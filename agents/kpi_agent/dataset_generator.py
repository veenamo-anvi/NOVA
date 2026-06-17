"""Synthetic labelled KPI dataset generator.

Produces a CSV with one row per (day, hour, cell) — 70 days x 24 h x 30 cells =
50,400 rows by default — with a realistic class distribution
(70% NORMAL / 15% OVERLOAD / 8% UNDERLOAD / 5% SINR_LOW / 2% POWER_WASTE).

`sample_kpi()` is the shared per-class sampler reused by train.py to synthesise
labelled training sequences.

CLI:  python dataset_generator.py --days 70 --seed 42 --out kpi_dataset.csv
"""
import argparse
import csv
import random

from features import CLASS_NAMES

CLASS_DIST = {"NORMAL": 0.70, "OVERLOAD": 0.15, "UNDERLOAD": 0.08,
              "SINR_LOW": 0.05, "POWER_WASTE": 0.02}

# Representative cell archetypes (5G n78 and 4G B3) used when no cell is given.
ARCHETYPES = [
    {"generation": "5G", "band": "n78", "max_ues": 900, "peak_dl_mbps": 3800,
     "idle_power_w": 120, "tx_power_w": 200},
    {"generation": "4G", "band": "B3", "max_ues": 250, "peak_dl_mbps": 220,
     "idle_power_w": 50, "tx_power_w": 80},
]


def _u(a, b):
    return random.uniform(a, b)


def sample_kpi(cls: str, cell: dict) -> dict:
    """Sample a full cell_kpi field set consistent with a class label."""
    is_5g = cell["generation"] == "5G"
    max_ues = cell["max_ues"]
    peak_dl = cell["peak_dl_mbps"]
    idle_p, tx_p = cell["idle_power_w"], cell["tx_power_w"]

    if cls == "OVERLOAD":
        util, sinr, cqi, bler, loss = _u(0.85, 1.0), _u(5, 14), _u(6, 11), _u(12, 25), _u(1, 8)
        lat = _u(12, 30)
    elif cls == "UNDERLOAD":
        util, sinr, cqi, bler, loss = _u(0.01, 0.20), _u(18, 30), _u(12, 15), _u(5, 10), _u(0, 0.3)
        lat = _u(2, 8)
    elif cls == "SINR_LOW":
        util, sinr, cqi, bler, loss = _u(0.3, 0.8), _u(-5, 5), _u(1, 5), _u(25, 60), _u(3, 15)
        lat = _u(15, 40)
    elif cls == "POWER_WASTE":
        util, sinr, cqi, bler, loss = _u(0.02, 0.15), _u(15, 28), _u(11, 15), _u(6, 12), _u(0, 0.5)
        lat = _u(3, 9)
    else:  # NORMAL
        util, sinr, cqi, bler, loss = _u(0.3, 0.8), _u(12, 28), _u(9, 15), _u(6, 12), _u(0, 1.0)
        lat = _u(3, 12)

    connected = int(max_ues * util)
    prb_dl = min(100.0, util * 98 + _u(-2, 2))
    se = cqi / 15.0
    dl_tput = min(peak_dl, peak_dl * se * (prb_dl / 100.0) * _u(0.95, 1.05))

    # POWER_WASTE: high power despite low load; else power tracks utilisation.
    if cls == "POWER_WASTE":
        power = idle_p + tx_p * _u(0.8, 1.0)
    else:
        power = idle_p + util * tx_p + _u(-4, 4)

    return {
        "connected_ues": connected,
        "prb_dl_pct": round(prb_dl, 1),
        "prb_ul_pct": round(min(100.0, prb_dl * 0.45), 1),
        "sinr_db": round(sinr, 1),
        "rsrp_dbm": round(_u(-110, -80), 1),
        "rsrq_db": round(_u(-15, -4), 1),
        "power_w": round(power, 1),
        "dl_throughput_mbps": round(dl_tput, 2),
        "ul_throughput_mbps": round(dl_tput * 0.14, 2),
        "packet_loss_pct": round(loss, 3),
        "cqi": int(cqi),
        "mcs": int(min(28, cqi * 1.85)),
        "bler_pct": round(bler, 2),
        "latency_ms": round(lat, 2),
        "jitter_ms": round(lat * 0.1, 2),
        "interference_dbm": round(_u(-115, -90), 1),
    }


def _pick_class() -> str:
    r, cum = random.random(), 0.0
    for cls, p in CLASS_DIST.items():
        cum += p
        if r <= cum:
            return cls
    return "NORMAL"


def generate_csv(days: int, out: str, seed: int) -> int:
    random.seed(seed)
    try:
        import topology_loader  # optional: use real topology if present
        cells = topology_loader.cells()
    except Exception:
        cells = None

    rows = 0
    fields = ["timestamp_day", "hour", "cell_id", "du_id", "vendor", "generation",
              "band", "area", "label",
              "connected_ues", "prb_dl_pct", "prb_ul_pct", "sinr_db", "rsrp_dbm",
              "rsrq_db", "power_w", "dl_throughput_mbps", "ul_throughput_mbps",
              "packet_loss_pct", "cqi", "mcs", "bler_pct", "latency_ms",
              "jitter_ms", "interference_dbm"]

    # Synthetic 30-cell set when topology isn't importable.
    if not cells:
        cells = []
        for s in range(10):
            for sec, arch in enumerate(("5G", "5G", "4G")):
                a = ARCHETYPES[0] if arch == "5G" else ARCHETYPES[1]
                cells.append({"cell_id": f"MLS_S{s:02d}_{sec+1:02d}", "du_id": f"DU-MLS-{s%3+1}",
                              "vendor": "Nokia", "area": f"area{s}",
                              **a, "band": a["band"]})

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for day in range(days):
            for hour in range(24):
                for cell in cells:
                    cls = _pick_class()
                    kpi = sample_kpi(cls, cell)
                    w.writerow({"timestamp_day": day, "hour": hour,
                                "cell_id": cell["cell_id"], "du_id": cell["du_id"],
                                "vendor": cell["vendor"], "generation": cell["generation"],
                                "band": cell["band"], "area": cell["area"], "label": cls,
                                **kpi})
                    rows += 1
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=70)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="kpi_dataset.csv")
    args = ap.parse_args()
    n = generate_csv(args.days, args.out, args.seed)
    print(f"wrote {n} rows to {args.out} ({len(CLASS_NAMES)} classes)")


if __name__ == "__main__":
    main()

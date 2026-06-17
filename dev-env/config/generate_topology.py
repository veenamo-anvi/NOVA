#!/usr/bin/env python3
"""Generate the 30-cell Malleswaram topology.json (source of truth).

10 macro sites x 3 sectors = 30 cells, grouped under 3 DUs and 1 CU.
Run from anywhere:  python dev-env/config/generate_topology.py

Sector mix (per spec.md):
  High-traffic sites (RWS, 18C, SNK, SPG, 10C):  5G n78 | 5G n41 | 4G B3
  Residential sites  (BEL, 3MN, MGR, CHD, 6CR):  5G n78 | 4G B40 | 4G B3

Vendor assignment is per-site (all 3 sectors share a vendor): Nokia/Ericsson
x3 sites, Samsung/ZTE x2 sites => 9/9/6/6 cells. The spec's "10 cells per
vendor" does not divide evenly into 30 cells; per-site assignment keeps each
site's hardware self-consistent while staying close to 25% each.

PCIs are assigned uniquely (1..30) which is trivially collision- and
confusion-free; the planning engine performs graph-coloring for generated plans.
"""
import json
import os

# --- Band -> radio parameters (cell-level max_ues per spec capacity calc) ---
# n78:900 x10 + n41:700 x5 + B40:300 x5 + B3:250 x10 = 16,500 cell-level max UEs
BANDS = {
    "n78": {"gen": "5G", "freq_mhz": 3500, "max_ues": 900, "tx_power_w": 200,
            "idle_power_w": 120, "antenna_config": "64T64R"},
    "n41": {"gen": "5G", "freq_mhz": 2500, "max_ues": 700, "tx_power_w": 160,
            "idle_power_w": 120, "antenna_config": "64T64R"},
    "B40": {"gen": "4G", "freq_mhz": 2300, "max_ues": 300, "tx_power_w": 80,
            "idle_power_w": 50, "antenna_config": "4T4R"},
    "B3":  {"gen": "4G", "freq_mhz": 1800, "max_ues": 250, "tx_power_w": 80,
            "idle_power_w": 50, "antenna_config": "4T4R"},
}

# --- Vendor hardware specs (per spec.md vendor table) -----------------------
VENDORS = {
    "Nokia":    {"hw_5g": "AirScale MAA 64T64R", "hw_4g": "AWHFA",
                 "peak_dl_5g": 3800, "peak_dl_4g": 220},
    "Ericsson": {"hw_5g": "AIR 6449",            "hw_4g": "RBS 6402",
                 "peak_dl_5g": 3600, "peak_dl_4g": 200},
    "Samsung":  {"hw_5g": "TM500 64T64R",        "hw_4g": "RRU",
                 "peak_dl_5g": 3400, "peak_dl_4g": 190},
    "ZTE":      {"hw_5g": "AAU 5614",            "hw_4g": "RRU",
                 "peak_dl_5g": 3200, "peak_dl_4g": 180},
}

# --- Sites: code, area, lat, lon, profile, vendor, du --------------------
# High-traffic profile -> [n78, n41, B3]; residential -> [n78, B40, B3]
SITES = [
    # north -> DU-MLS-1
    ("RWS", "Malleswaram Railway Station", 13.0121, 77.5571, "high", "Nokia",    "DU-MLS-1"),
    ("18C", "18th Cross",                  13.0052, 77.5712, "high", "Ericsson", "DU-MLS-1"),
    ("BEL", "BEL Road",                    13.0271, 77.5631, "res",  "Samsung",  "DU-MLS-1"),
    ("SNK", "Sankey Road",                 13.0049, 77.5783, "high", "ZTE",      "DU-MLS-1"),
    # central -> DU-MLS-2
    ("SPG", "Sampige Road",                13.0031, 77.5701, "high", "Nokia",    "DU-MLS-2"),
    ("3MN", "3rd Main",                    12.9981, 77.5732, "res",  "Ericsson", "DU-MLS-2"),
    ("10C", "10th Cross",                  13.0011, 77.5682, "high", "Samsung",  "DU-MLS-2"),
    # south-west -> DU-MLS-3
    ("MGR", "Margosa Road",                12.9961, 77.5722, "res",  "Nokia",    "DU-MLS-3"),
    ("CHD", "Chowdiah Road",               13.0091, 77.5671, "res",  "Ericsson", "DU-MLS-3"),
    ("6CR", "6th Cross",                   12.9991, 77.5661, "res",  "ZTE",      "DU-MLS-3"),
]

SECTOR_BANDS = {
    "high": ["n78", "n41", "B3"],
    "res":  ["n78", "B40", "B3"],
}

# Small per-sector position offsets (deg) so sectors don't fully overlap.
SECTOR_OFFSETS = [(0.0008, 0.0), (-0.0004, 0.0007), (-0.0004, -0.0007)]


def build():
    cus = {
        "CU-MLS": {
            "cu_id": "CU-MLS",
            "area": "Malleswaram",
            "lat": 13.0050,
            "lon": 77.5700,
        }
    }
    dus = {
        "DU-MLS-1": {"du_id": "DU-MLS-1", "cu_id": "CU-MLS", "area": "north",
                     "lat": 13.0123, "lon": 77.5639, "max_cells": 12},
        "DU-MLS-2": {"du_id": "DU-MLS-2", "cu_id": "CU-MLS", "area": "central",
                     "lat": 13.0008, "lon": 77.5705, "max_cells": 12},
        "DU-MLS-3": {"du_id": "DU-MLS-3", "cu_id": "CU-MLS", "area": "south-west",
                     "lat": 13.0014, "lon": 77.5685, "max_cells": 12},
    }

    cells = {}
    pci = 1
    for code, area, lat, lon, profile, vendor, du_id in SITES:
        vspec = VENDORS[vendor]
        for sector_idx, band in enumerate(SECTOR_BANDS[profile], start=1):
            b = BANDS[band]
            gen = b["gen"]
            dlat, dlon = SECTOR_OFFSETS[sector_idx - 1]
            cell_id = f"MLS_{code}_{sector_idx:02d}"
            cells[cell_id] = {
                "cell_id": cell_id,
                "du_id": du_id,
                "cu_id": "CU-MLS",
                "area": area,
                "lat": round(lat + dlat, 6),
                "lon": round(lon + dlon, 6),
                "generation": gen,
                "band": band,
                "freq_mhz": b["freq_mhz"],
                "vendor": vendor,
                "hardware_model": vspec["hw_5g"] if gen == "5G" else vspec["hw_4g"],
                "antenna_config": b["antenna_config"],
                "pci": pci,
                "tx_power_w": b["tx_power_w"],
                "idle_power_w": b["idle_power_w"],
                "peak_dl_mbps": vspec["peak_dl_5g"] if gen == "5G" else vspec["peak_dl_4g"],
                "max_ues": b["max_ues"],
            }
            pci += 1

    return {
        "metadata": {
            "deployment": "Malleswaram, North Bangalore",
            "operator_market_share": 0.40,
            "active_ues_peak": 18400,
            "cell_level_max_ues": sum(c["max_ues"] for c in cells.values()),
            "site_count": len(SITES),
            "cell_count": len(cells),
        },
        "cus": cus,
        "dus": dus,
        "cells": cells,
    }


def main():
    topo = build()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topology.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(topo, f, indent=2)
        f.write("\n")
    m = topo["metadata"]
    print(f"Wrote {out}")
    print(f"  cells={m['cell_count']} sites={m['site_count']} "
          f"cell_level_max_ues={m['cell_level_max_ues']}")
    # quick vendor tally
    tally = {}
    for c in topo["cells"].values():
        tally[c["vendor"]] = tally.get(c["vendor"], 0) + 1
    print(f"  vendor cells: {tally}")


if __name__ == "__main__":
    main()

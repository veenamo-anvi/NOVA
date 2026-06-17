"""Candidate cell sites and Bangalore demand clusters for the planner.

Candidate *sites* (each expands to 3 sectors / cells) are the infrastructure
the planner may build. Demand *clusters* (Tutschku 1998) represent traffic to be
served and are kept separate from candidates. The 10 core Malleswaram sites
reproduce the live deployment; the extra sites give the placer room to optimise.
"""

# Band -> radio parameters (mirrors the topology generator / docs/schema.md).
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

SECTOR_BANDS = {"high": ["n78", "n41", "B3"], "res": ["n78", "B40", "B3"]}
SECTOR_OFFSETS = [(0.0008, 0.0), (-0.0004, 0.0007), (-0.0004, -0.0007)]

# code, area, lat, lon, profile, vendor, install_cost, op_cost, core(bool)
CANDIDATE_SITES = [
    ("RWS", "Malleswaram Railway Station", 13.0121, 77.5571, "high", "Nokia",    120000, 18000, True),
    ("18C", "18th Cross",                  13.0052, 77.5712, "high", "Ericsson", 118000, 17500, True),
    ("BEL", "BEL Road",                    13.0271, 77.5631, "res",  "Samsung",  100000, 15000, True),
    ("SNK", "Sankey Road",                 13.0049, 77.5783, "high", "ZTE",      115000, 17000, True),
    ("SPG", "Sampige Road",                13.0031, 77.5701, "high", "Nokia",    120000, 18000, True),
    ("3MN", "3rd Main",                    12.9981, 77.5732, "res",  "Ericsson", 100000, 15000, True),
    ("10C", "10th Cross",                  13.0011, 77.5682, "high", "Samsung",  117000, 17500, True),
    ("MGR", "Margosa Road",                12.9961, 77.5722, "res",  "Nokia",    100000, 15000, True),
    ("CHD", "Chowdiah Road",               13.0091, 77.5671, "res",  "Ericsson", 100000, 15000, True),
    ("6CR", "6th Cross",                   12.9991, 77.5661, "res",  "ZTE",      100000, 15000, True),
    # Expansion candidates (not in the live 30-cell deployment):
    ("MSR", "Malleswaram 15th Cross",      13.0081, 77.5721, "high", "Samsung",  110000, 16000, False),
    ("RMV", "RMV 2nd Stage",               13.0181, 77.5751, "res",  "Ericsson",  98000, 14500, False),
    ("YPR", "Yeshwantpur",                 13.0281, 77.5401, "high", "ZTE",      112000, 16500, False),
    ("PYA", "Peenya",                      13.0331, 77.5201, "res",  "Nokia",     95000, 14000, False),
]

# 10 Bangalore demand clusters (one per area). demand = per-cluster channel
# requirement (simultaneous channels), kept <= a single site's capacity so the
# paper's unique-assignment constraint (each cluster served by exactly one BS)
# stays feasible.
DEMAND_CLUSTERS = [
    {"id": "DC-RWS", "area": "Railway Station", "lat": 13.0125, "lon": 77.5575, "demand": 1400},
    {"id": "DC-18C", "area": "18th Cross",      "lat": 13.0055, "lon": 77.5715, "demand": 1200},
    {"id": "DC-BEL", "area": "BEL Road",        "lat": 13.0275, "lon": 77.5635, "demand": 800},
    {"id": "DC-SNK", "area": "Sankey",          "lat": 13.0050, "lon": 77.5785, "demand": 1100},
    {"id": "DC-SPG", "area": "Sampige",         "lat": 13.0035, "lon": 77.5705, "demand": 1200},
    {"id": "DC-3MN", "area": "3rd Main",        "lat": 12.9985, "lon": 77.5735, "demand": 700},
    {"id": "DC-10C", "area": "10th Cross",      "lat": 13.0015, "lon": 77.5685, "demand": 1000},
    {"id": "DC-MGR", "area": "Margosa",         "lat": 12.9965, "lon": 77.5725, "demand": 700},
    {"id": "DC-CHD", "area": "Chowdiah",        "lat": 13.0095, "lon": 77.5675, "demand": 800},
    {"id": "DC-6CR", "area": "6th Cross",       "lat": 12.9995, "lon": 77.5665, "demand": 800},
]

# Preset multi-period demand profiles (cluster_id -> demand per period).
# Case A (permanent/expanding): demand grows as the rollout proceeds.
# Case B (temporary/shifting): demand moves between areas across the day.
DEMAND_PERIODS = {
    "permanent": [
        {"DC-RWS": 900, "DC-18C": 800, "DC-SPG": 800, "DC-10C": 700},
        {"DC-RWS": 1200, "DC-18C": 1100, "DC-SPG": 1000, "DC-10C": 900,
         "DC-SNK": 900, "DC-CHD": 600},
        {"DC-RWS": 1400, "DC-18C": 1200, "DC-SPG": 1200, "DC-10C": 1000,
         "DC-SNK": 1100, "DC-CHD": 800, "DC-BEL": 800, "DC-3MN": 700,
         "DC-MGR": 700, "DC-6CR": 800},
    ],
    "temporary": [
        # morning: residential heavy
        {"DC-BEL": 1300, "DC-3MN": 1200, "DC-MGR": 1100, "DC-6CR": 1100},
        # midday: transit / commercial hubs
        {"DC-RWS": 1400, "DC-18C": 1300, "DC-SPG": 1200, "DC-10C": 1100, "DC-SNK": 1100},
        # evening: commute / station
        {"DC-RWS": 1400, "DC-SNK": 1200, "DC-CHD": 1000, "DC-18C": 1000, "DC-BEL": 900},
    ],
}


def build_candidate_cells() -> list[dict]:
    """Expand candidate sites into 3-sector candidate cells with full hardware."""
    cells = []
    for code, area, lat, lon, profile, vendor, install_cost, op_cost, core in CANDIDATE_SITES:
        vspec = VENDORS[vendor]
        for idx, band in enumerate(SECTOR_BANDS[profile], start=1):
            b = BANDS[band]
            gen = b["gen"]
            dlat, dlon = SECTOR_OFFSETS[idx - 1]
            cells.append({
                "cell_id": f"MLS_{code}_{idx:02d}",
                "site": code, "area": area,
                "lat": round(lat + dlat, 6), "lon": round(lon + dlon, 6),
                "generation": gen, "band": band, "freq_mhz": b["freq_mhz"],
                "vendor": vendor,
                "hardware_model": vspec["hw_5g"] if gen == "5G" else vspec["hw_4g"],
                "antenna_config": b["antenna_config"],
                "tx_power_w": b["tx_power_w"], "idle_power_w": b["idle_power_w"],
                "peak_dl_mbps": vspec["peak_dl_5g"] if gen == "5G" else vspec["peak_dl_4g"],
                "max_ues": b["max_ues"],
                "install_cost": install_cost, "op_cost": op_cost, "core_site": core,
            })
    return cells


def site_catalog() -> dict[str, dict]:
    """Per-site summary: location, capacity (sum of sector max_ues), costs."""
    out = {}
    for code, area, lat, lon, profile, vendor, install_cost, op_cost, core in CANDIDATE_SITES:
        cap = sum(BANDS[b]["max_ues"] for b in SECTOR_BANDS[profile])
        out[code] = {"site": code, "area": area, "lat": lat, "lon": lon,
                     "profile": profile, "vendor": vendor, "capacity": cap,
                     "install_cost": install_cost, "op_cost": op_cost, "core_site": core}
    return out

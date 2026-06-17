"""Physics-grounded synthetic KPI generation for a single cell.

Load follows a diurnal curve (evening peak ~19:00) scaled down on weekends.
RSRP is derived from the COST-231-Hata urban-macro path-loss model; the
remaining KPIs are correlated with offered load and link quality the way a real
cell behaves (SINR falls with load, CQI/MCS track SINR, throughput tracks
CQI x PRB, power tracks utilisation, etc.).
"""
import math
import random

WEEKEND_FACTOR = 0.75

# High-traffic sites carry ~full load; residential sites are lighter.
HIGH_TRAFFIC_SITES = {"RWS", "18C", "SNK", "SPG", "10C"}
RESIDENTIAL_WEIGHT = 0.72

# Hourly demand multipliers (0..1), urban profile with morning + evening peaks.
_HOURLY = {
    0: 0.15, 1: 0.10, 2: 0.08, 3: 0.07, 4: 0.08, 5: 0.12,
    6: 0.20, 7: 0.35, 8: 0.55, 9: 0.70, 10: 0.75, 11: 0.78,
    12: 0.72, 13: 0.70, 14: 0.68, 15: 0.70, 16: 0.75, 17: 0.82,
    18: 0.90, 19: 1.00, 20: 0.95, 21: 0.80, 22: 0.55, 23: 0.30,
}

# Per-band PRB count (for RSRP per-resource-element spreading) and base SINR.
_BAND = {
    "n78": {"prb": 273, "base_sinr": 24.0},
    "n41": {"prb": 106, "base_sinr": 20.0},
    "B40": {"prb": 100, "base_sinr": 16.0},
    "B3":  {"prb": 100, "base_sinr": 14.0},
}

_ANTENNA_GAIN = {"64T64R": 24.0, "4T4R": 17.0}


def load_factor(dt) -> float:
    """Smoothly-interpolated diurnal load in [0,1], weekend-scaled."""
    h = dt.hour
    frac = dt.minute / 60.0
    cur = _HOURLY[h]
    nxt = _HOURLY[(h + 1) % 24]
    lf = cur + (nxt - cur) * frac
    if dt.weekday() >= 5:  # Sat/Sun
        lf *= WEEKEND_FACTOR
    return lf


def _site_of(cell_id: str) -> str:
    parts = cell_id.split("_")
    return parts[1] if len(parts) >= 3 else cell_id


def _cost231_hata_pl(freq_mhz: float, d_km: float, hb: float = 25.0,
                     hm: float = 1.5) -> float:
    """COST-231-Hata urban-macro path loss (dB). C=3 for metropolitan."""
    log_f = math.log10(freq_mhz)
    a_hm = (1.1 * log_f - 0.7) * hm - (1.56 * log_f - 0.8)
    return (46.3 + 33.9 * log_f - 13.82 * math.log10(hb) - a_hm
            + (44.9 - 6.55 * math.log10(hb)) * math.log10(max(d_km, 0.01)) + 3.0)


def _rsrp_dbm(cell: dict, util: float) -> float:
    """Representative serving RSRP per resource element.

    Heavier load -> more cell-edge UEs served -> lower average RSRP, modelled by
    pushing the representative distance outward with utilisation.
    """
    band = _BAND.get(cell["band"], _BAND["B3"])
    gain = _ANTENNA_GAIN.get(cell.get("antenna_config", ""), 17.0)
    tx_w = max(cell.get("tx_power_w", 40.0), 1.0)
    eirp_dbm = 10 * math.log10(tx_w * 1000.0) + gain
    d_km = 0.30 + 0.30 * util               # 0.3..0.6 km representative
    pl = _cost231_hata_pl(cell["freq_mhz"], d_km)
    n_re = 12 * band["prb"]                  # spread total power across REs
    rsrp = eirp_dbm - pl - 10 * math.log10(n_re)
    return max(-120.0, min(-70.0, rsrp + random.gauss(0, 1.5)))


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def generate_cell_kpi(cell: dict, lf: float) -> dict:
    """Return the full cell_kpi field set for one emit cycle."""
    band = _BAND.get(cell["band"], _BAND["B3"])
    is_5g = cell.get("generation") == "5G"
    max_ues = max(cell.get("max_ues", 100), 1)

    weight = 1.0 if _site_of(cell["cell_id"]) in HIGH_TRAFFIC_SITES else RESIDENTIAL_WEIGHT
    util = _clamp(lf * weight * random.gauss(1.0, 0.06), 0.0, 1.05)

    connected = int(min(max_ues, round(max_ues * util)))
    prb_dl = _clamp(util * 98 + random.gauss(0, 2), 0, 100)
    prb_ul = _clamp(prb_dl * 0.45 + random.gauss(0, 2), 0, 100)

    sinr = _clamp(band["base_sinr"] - util * 10 + random.gauss(0, 1.5), -5, 30)
    rsrp = _rsrp_dbm(cell, util)
    rsrq = _clamp(-3 - util * 7 + random.gauss(0, 1), -19.5, -3)

    cqi = int(_clamp(round(0.5 * sinr + 3), 1, 15))
    mcs = int(_clamp(round(cqi * 1.85), 0, 28))
    bler = _clamp(10 + max(0.0, 5 - sinr) * 4 + random.gauss(0, 1.5), 0, 100)

    spectral_eff = cqi / 15.0
    dl_tput = cell.get("peak_dl_mbps", 100) * spectral_eff * (prb_dl / 100.0)
    dl_tput = _clamp(dl_tput * random.gauss(1.0, 0.05), 0, cell.get("peak_dl_mbps", 100))
    ul_tput = dl_tput * 0.14

    power = cell.get("idle_power_w", 50) + util * cell.get("tx_power_w", 80) + random.gauss(0, 4)
    packet_loss = _clamp(0.1 + max(0.0, util - 0.85) * 8 + random.gauss(0, 0.05), 0, 100)

    base_lat = 4.0 if is_5g else 9.0
    latency = _clamp(base_lat + util * 6 + random.gauss(0, 0.8), 1, 100)
    jitter = _clamp(latency * 0.10 + random.gauss(0, 0.3), 0, 50)
    interference = _clamp(-115 + util * 20 + random.gauss(0, 2), -120, -80)

    return {
        "connected_ues": connected,
        "dl_throughput_mbps": round(dl_tput, 2),
        "ul_throughput_mbps": round(ul_tput, 2),
        "rsrp_dbm": round(rsrp, 1),
        "rsrq_db": round(rsrq, 1),
        "sinr_db": round(sinr, 1),
        "power_w": round(power, 1),
        "prb_dl_pct": round(prb_dl, 1),
        "prb_ul_pct": round(prb_ul, 1),
        "packet_loss_pct": round(packet_loss, 3),
        "cqi": cqi,
        "mcs": mcs,
        "bler_pct": round(bler, 2),
        "latency_ms": round(latency, 2),
        "jitter_ms": round(jitter, 2),
        "interference_dbm": round(interference, 1),
    }

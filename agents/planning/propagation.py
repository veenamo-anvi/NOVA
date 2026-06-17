"""Propagation models used by the planner.

- COST-231-Hata (urban macro) — coverage-radius / link-budget feasibility.
- COST-231-Walfisch-Ikegami (urban NLOS) — path loss for the MIP link budget
  and SINR feasibility (constraint 8 in Almoghathawi et al. 2024).
"""
import math

ANTENNA_GAIN = {"64T64R": 24.0, "4T4R": 17.0}
UE_NOISE_FIGURE_DB = 7.0
# Channel bandwidth per band (MHz) — sets the thermal-noise floor.
BANDWIDTH_MHZ = {"n78": 100.0, "n41": 40.0, "B40": 20.0, "B3": 20.0}


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cost231_hata_pl(freq_mhz, d_km, hb=25.0, hm=1.5) -> float:
    """COST-231-Hata urban-macro path loss (dB), metropolitan correction C=3."""
    log_f = math.log10(freq_mhz)
    a_hm = (1.1 * log_f - 0.7) * hm - (1.56 * log_f - 0.8)
    return (46.3 + 33.9 * log_f - 13.82 * math.log10(hb) - a_hm
            + (44.9 - 6.55 * math.log10(hb)) * math.log10(max(d_km, 0.01)) + 3.0)


def walfisch_ikegami_nlos_pl(freq_mhz, d_km, hb=30.0, hroof=20.0, hm=1.5,
                             w=25.0, b=40.0, phi_deg=90.0) -> float:
    """COST-231-Walfisch-Ikegami NLOS path loss (dB), metropolitan."""
    d_km = max(d_km, 0.02)
    L0 = 32.4 + 20 * math.log10(d_km) + 20 * math.log10(freq_mhz)

    dhm = hroof - hm
    if 55 <= phi_deg <= 90:
        l_ori = 4.0 - 0.114 * (phi_deg - 55)
    elif 35 <= phi_deg < 55:
        l_ori = 2.5 + 0.075 * (phi_deg - 35)
    else:
        l_ori = -10.0 + 0.354 * phi_deg
    l_rts = -16.9 - 10 * math.log10(w) + 10 * math.log10(freq_mhz) + 20 * math.log10(max(dhm, 0.1)) + l_ori

    dhb = hb - hroof
    if hb > hroof:
        l_bsh = -18 * math.log10(1 + dhb)
        ka = 54.0
        kd = 18.0
    else:
        l_bsh = 0.0
        ka = 54.0 - 0.8 * dhb * (min(d_km, 0.5) / 0.5)
        kd = 18.0 - 15.0 * dhb / max(hroof, 1.0)
    kf = -4.0 + 1.5 * (freq_mhz / 925.0 - 1.0)  # metropolitan
    l_msd = l_bsh + ka + kd * math.log10(d_km) + kf * math.log10(freq_mhz) - 9 * math.log10(b)

    extra = l_rts + l_msd
    return L0 + extra if extra > 0 else L0


def eirp_dbm(tx_power_w, antenna_config) -> float:
    gain = ANTENNA_GAIN.get(antenna_config, 17.0)
    return 10 * math.log10(max(tx_power_w, 0.1) * 1000.0) + gain


def _noise_floor_dbm(band: str) -> float:
    bw_hz = BANDWIDTH_MHZ.get(band, 20.0) * 1e6
    return -174.0 + 10 * math.log10(bw_hz) + UE_NOISE_FIGURE_DB


def sinr_at_distance_db(cell: dict, d_km: float) -> float:
    """Single-link SINR (no inter-cell interference) at distance d.

    Received power and the thermal-noise floor are both referenced to the full
    channel bandwidth, so the ratio is dimensionally consistent.
    """
    pl = walfisch_ikegami_nlos_pl(cell["freq_mhz"], d_km)
    eirp = eirp_dbm(cell["tx_power_w"], cell.get("antenna_config", "4T4R"))
    rx_dbm = eirp - pl
    return rx_dbm - _noise_floor_dbm(cell.get("band", "B3"))


def coverage_radius_km(cell: dict, edge_sinr_db: float = -3.0) -> float:
    """Largest distance where SINR >= edge_sinr_db (binary search)."""
    lo, hi = 0.02, 12.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if sinr_at_distance_db(cell, mid) >= edge_sinr_db:
            lo = mid
        else:
            hi = mid
    return lo

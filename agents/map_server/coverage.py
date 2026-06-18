"""Coverage-radius estimation via the COST-231-Hata urban-macro model (inverted).

Given a cell's band, TX power, generation, and antenna config, compute the
edge-of-coverage radius in metres by inverting the path-loss formula
PL = A + B*log10(d_km)  ->  d_km = 10^((PL_max - A) / B).
"""
import math

ANTENNA_GAIN = {"64T64R": 24.0, "4T4R": 17.0}
RF_EFFICIENCY = {"5G": 0.22, "4G": 0.32}
BANDWIDTH_MHZ = {"n78": 100.0, "n41": 40.0, "B40": 20.0, "B3": 20.0}

HB = 25.0            # base station height (m)
HM = 1.5             # mobile height (m)
C_METRO = 3.0        # dense-urban correction (dB)
UE_NF_DB = 7.0
EDGE_SNR_DB = -3.0
PENETRATION_LOSS_DB = 18.0


def _hata_AB(freq_mhz: float) -> tuple[float, float]:
    log_f = math.log10(freq_mhz)
    a_hm = (1.1 * log_f - 0.7) * HM - (1.56 * log_f - 0.8)
    A = 46.3 + 33.9 * log_f - 13.82 * math.log10(HB) - a_hm + C_METRO
    B = 44.9 - 6.55 * math.log10(HB)
    return A, B


def compute_coverage_radius_m(band: str, tx_power_w: float, generation: str,
                              antenna_config: str, freq_mhz: float | None = None) -> float:
    freq = freq_mhz or {"n78": 3500, "n41": 2500, "B40": 2300, "B3": 1800}.get(band, 1800)
    gain = ANTENNA_GAIN.get(antenna_config, 17.0)
    eff = RF_EFFICIENCY.get(generation, 0.3)
    bw_hz = BANDWIDTH_MHZ.get(band, 20.0) * 1e6

    rf_w = max(tx_power_w, 1.0) * eff
    eirp_dbm = 10 * math.log10(rf_w * 1000.0) + gain
    noise_dbm = -174.0 + 10 * math.log10(bw_hz) + UE_NF_DB
    pl_max = eirp_dbm - (noise_dbm - EDGE_SNR_DB) - PENETRATION_LOSS_DB

    A, B = _hata_AB(freq)
    d_km = 10 ** ((pl_max - A) / B)
    return round(max(50.0, min(d_km * 1000.0, 12000.0)), 1)

"""Unit tests for propagation models and coverage-radius computation."""
import unittest

import _paths  # noqa: F401

import propagation as P
from coverage import compute_coverage_radius_m


class TestPropagation(unittest.TestCase):
    def test_hata_increases_with_distance(self):
        near = P.cost231_hata_pl(3500, 0.3)
        far = P.cost231_hata_pl(3500, 2.0)
        self.assertLess(near, far)

    def test_wi_nlos_increases_with_distance(self):
        self.assertLess(P.walfisch_ikegami_nlos_pl(3500, 0.3),
                        P.walfisch_ikegami_nlos_pl(3500, 2.0))

    def test_sinr_decreases_with_distance(self):
        cell = {"freq_mhz": 3500, "tx_power_w": 200, "antenna_config": "64T64R", "band": "n78"}
        self.assertGreater(P.sinr_at_distance_db(cell, 0.3),
                           P.sinr_at_distance_db(cell, 2.0))

    def test_coverage_radius_reasonable(self):
        cell = {"freq_mhz": 3500, "tx_power_w": 200, "antenna_config": "64T64R", "band": "n78"}
        r = P.coverage_radius_km(cell)
        self.assertTrue(0.3 < r < 6.0, f"radius {r} km out of expected range")


class TestCoverage(unittest.TestCase):
    def test_radius_positive_and_bounded(self):
        r = compute_coverage_radius_m("n78", 200, "5G", "64T64R", 3500)
        self.assertTrue(50 <= r <= 12000)

    def test_lower_freq_reaches_further(self):
        # B3 (1800 MHz) should reach further than n78 (3500 MHz) at equal power.
        r_n78 = compute_coverage_radius_m("n78", 80, "5G", "4T4R", 3500)
        r_b3 = compute_coverage_radius_m("B3", 80, "4G", "4T4R", 1800)
        self.assertGreater(r_b3, r_n78)


if __name__ == "__main__":
    unittest.main()

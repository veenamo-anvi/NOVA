"""Unit tests for KPI features and the per-class dataset sampler."""
import unittest

import _paths  # noqa: F401

import features as F
from dataset_generator import sample_kpi, ARCHETYPES


class TestFeatures(unittest.TestCase):
    def test_vector_length(self):
        kpi = sample_kpi("NORMAL", ARCHETYPES[0])
        self.assertEqual(len(F.extract_features(kpi)), 9)

    def test_normalise_in_range(self):
        kpi = sample_kpi("OVERLOAD", ARCHETYPES[0])
        for x in F.normalise(F.extract_features(kpi)):
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, 1.0)

    def test_five_classes(self):
        self.assertEqual(len(F.CLASS_NAMES), 5)


class TestClassSeparability(unittest.TestCase):
    """Sampled KPIs should match each class's defining condition."""

    def _avg(self, cls, field, n=40):
        return sum(sample_kpi(cls, ARCHETYPES[0])[field] for _ in range(n)) / n

    def test_overload_high_prb(self):
        self.assertGreaterEqual(self._avg("OVERLOAD", "prb_dl_pct"), 85)

    def test_underload_low_prb(self):
        self.assertLessEqual(self._avg("UNDERLOAD", "prb_dl_pct"), 20)

    def test_sinr_low(self):
        self.assertLessEqual(self._avg("SINR_LOW", "sinr_db"), 5)

    def test_power_waste_low_load_high_power(self):
        # defining signal: low PRB utilisation but power well above idle
        self.assertLessEqual(self._avg("POWER_WASTE", "prb_dl_pct"), 20)
        self.assertGreater(self._avg("POWER_WASTE", "power_w"), 200)


if __name__ == "__main__":
    unittest.main()

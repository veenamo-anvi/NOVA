"""Unit tests for the planning algorithms: placement, PCI, slices, MIP."""
import unittest

import _paths  # noqa: F401  (sets sys.path)

from placement import select_cells
from pci_planner import assign_pcis, verify, NEIGHBOR_RADIUS_M
import slice_allocator
import mip_placer
from candidates import DEMAND_CLUSTERS


class TestPlacement(unittest.TestCase):
    def test_reproduces_core_layout(self):
        cells = select_cells(16500)
        sites = {c["site"] for c in cells}
        self.assertEqual(len(cells), 30)
        self.assertEqual(len(sites), 10)

    def test_capacity_target_scales_selection(self):
        small = select_cells(3000)
        big = select_cells(16500)
        self.assertLessEqual(len(small), len(big))
        self.assertGreater(len(small), 0)


class TestPCI(unittest.TestCase):
    def setUp(self):
        self.cells = select_cells(16500)
        assign_pcis(self.cells)

    def test_collision_and_confusion_free(self):
        chk = verify(self.cells)
        self.assertEqual(chk["collisions"], 0)
        self.assertEqual(chk["confusions"], 0)

    def test_all_cells_assigned_valid_pci(self):
        for c in self.cells:
            self.assertGreaterEqual(c["pci"], 1)
            self.assertLess(c["pci"], 1008)


class TestSlices(unittest.TestCase):
    def test_per_cell_prb_sums_to_band_budget(self):
        cells = select_cells(16500)
        alloc = slice_allocator.allocate_slices(cells, {"eMBB": 0.7, "URLLC": 0.2, "mMTC": 0.1})
        for c in cells:
            prb = slice_allocator.PRB_BY_BAND[c["band"]]
            self.assertEqual(sum(alloc["per_cell"][c["cell_id"]].values()), prb)

    def test_fractions_normalised(self):
        cells = select_cells(3000)
        alloc = slice_allocator.allocate_slices(cells, {"eMBB": 7, "URLLC": 2, "mMTC": 1})
        self.assertAlmostEqual(sum(alloc["fractions"].values()), 1.0, places=5)


class TestMIP(unittest.TestCase):
    def test_single_period_feasible_and_covers_demand(self):
        demand = {dc["id"]: dc["demand"] for dc in DEMAND_CLUSTERS}
        res = mip_placer.solve([demand], sinr_min_db=10.0, time_limit_sec=60)
        self.assertIsNotNone(res, "MIP should be feasible for default demand")
        self.assertEqual(res["status"], "Optimal")
        self.assertGreater(len(res["final_sites"]), 0)
        self.assertGreater(res["cost_estimate"]["total"], 0)

    def test_multi_period_build_once(self):
        from candidates import DEMAND_PERIODS
        res = mip_placer.solve(DEMAND_PERIODS["permanent"], sinr_min_db=10.0, time_limit_sec=60)
        self.assertIsNotNone(res)
        # a site is built at most once across periods
        built = [(b["site"]) for b in res["build_schedule"]]
        self.assertEqual(len(built), len(set(built)))


if __name__ == "__main__":
    unittest.main()

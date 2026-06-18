"""Live integration test: orchestrator -> planning -> controller chain.

Skipped automatically when the stack isn't running. Run the stack with
`docker compose --profile full up -d` first.
"""
import json
import unittest
import urllib.error
import urllib.request

CONTROLLER = "http://localhost:8080"
PLANNING = "http://localhost:8081"
ORCH = "http://localhost:8082"
MAP = "http://localhost:8083"


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_text(url, body, timeout=120):
    """For text/plain streaming endpoints like /chat."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def _reachable():
    try:
        _get(CONTROLLER + "/health", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_reachable(), "stack not running on localhost")
class TestIntegration(unittest.TestCase):
    def test_all_services_healthy(self):
        self.assertEqual(_get(CONTROLLER + "/health")["status"], "ok")
        self.assertEqual(_get(PLANNING + "/health")["status"], "ok")
        self.assertEqual(_get(ORCH + "/health")["status"], "ok")
        self.assertEqual(_get(MAP + "/health")["status"], "ok")

    def test_controller_serves_30_cells(self):
        net = _get(CONTROLLER + "/network")
        self.assertEqual(len(net["cells"]), 30)

    def test_cells_have_live_kpis(self):
        net = _get(CONTROLLER + "/network")
        with_kpi = [c for c in net["cells"] if c.get("kpi")]
        self.assertEqual(len(with_kpi), 30)

    def test_plan_generation_and_retrieval(self):
        plan = _post(PLANNING + "/plan", {})
        self.assertIn("plan_id", plan)
        self.assertGreater(plan["selected_cell_count"], 0)
        self.assertEqual(plan["pci_check"]["collisions"], 0)
        again = _get(PLANNING + f"/plan/{plan['plan_id']}")
        self.assertEqual(again["plan_id"], plan["plan_id"])

    def test_orchestrator_tools_listed(self):
        tools = _get(ORCH + "/tools")
        self.assertEqual(len(tools), 13)

    def test_chat_executes_tool(self):
        body = {"message": "what is the network status", "session_id": "itest"}
        resp = _post_text(ORCH + "/chat", body)
        self.assertIn("Network status", resp)

    def test_map_cells_have_coverage(self):
        cells = _get(MAP + "/api/cells")["cells"]
        self.assertEqual(len(cells), 30)
        self.assertTrue(all(c["coverage_radius_m"] > 0 for c in cells))


if __name__ == "__main__":
    unittest.main()

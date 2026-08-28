"""
Unit tests for the GridMind Dashboard API endpoints.
"""

import unittest
from starlette.testclient import TestClient

from gridmind.service import GridMindService
from agent.incident_manager import IncidentCommander
from dashboard.api import create_dashboard_app


class TestDashboardAPI(unittest.TestCase):
    """Tests all REST endpoints for the dashboard."""

    def setUp(self) -> None:
        self.service = GridMindService(data_dir="gridmind_data/curated")
        self.commander = IncidentCommander(self.service)
        self.app = create_dashboard_app(service=self.service, commander=self.commander)
        self.client = TestClient(self.app)

    def test_01_get_system_status(self) -> None:
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "OPERATIONAL")
        self.assertTrue(data["grid_stable"])
        self.assertAlmostEqual(data["frequency_hz"], 50.0000, places=3)

    def test_02_get_grid_state(self) -> None:
        resp = self.client.get("/api/grid-state")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["is_stable"])
        self.assertEqual(len(data["nodes"]), 10)
        self.assertEqual(len(data["lines"]), 8)
        self.assertEqual(len(data["transformers"]), 5)

    def test_03_load_scenario_and_investigate(self) -> None:
        # Load SC01
        load_res = self.client.post("/api/scenario/load", json={"scenario_id": "SC01"})
        self.assertEqual(load_res.status_code, 200)
        load_data = load_res.json()
        self.assertTrue(load_data["success"])
        self.assertEqual(load_data["scenario_id"], "SC01")

        # Run investigation
        inv_res = self.client.post("/api/incident/investigate")
        self.assertEqual(inv_res.status_code, 200)
        inv_data = inv_res.json()
        self.assertEqual(inv_data["state"], "AWAITING_APPROVAL")
        self.assertIsNotNone(inv_data["recommended_plan"])
        self.assertEqual(inv_data["recommended_plan"]["action_type"], "load_restriction")

        # Fetch candidate plans
        plans_res = self.client.get("/api/plans")
        self.assertEqual(plans_res.status_code, 200)
        plans_data = plans_res.json()
        self.assertGreaterEqual(len(plans_data), 3)

        # Approve action
        appr_res = self.client.post("/api/incident/approve", json={})
        self.assertEqual(appr_res.status_code, 200)
        appr_data = appr_res.json()
        self.assertEqual(appr_data["state"], "RESOLVED")
        self.assertTrue(appr_data["verification"]["passed"])

        # Check timeline
        time_res = self.client.get("/api/timeline")
        self.assertEqual(time_res.status_code, 200)
        time_data = time_res.json()
        self.assertGreater(len(time_data), 5)


if __name__ == "__main__":
    unittest.main()

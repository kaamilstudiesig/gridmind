"""
End-to-end unit and integration tests for Scenario SC01-B:
Heatwave commercial demand spike with operational emergency tie-line L08.
"""

import asyncio
import json
import unittest

from gridmind.contract import ActionRequest
from gridmind.engine import GridMindEngine
from gridmind.loader import load_curated_grid
from gridmind.mcp_server import GridMindMCPServer
from gridmind.models import Action, ActionCategory, LineStatus, ViolationType
from gridmind.scenario import run_scenario_sc01_b
from gridmind.service import GridMindService


class TestScenarioSC01B(unittest.TestCase):
    """Verifies complete deterministic lifecycle and candidate actions for SC01-B."""

    def test_sc01_b_full_lifecycle(self) -> None:
        report = run_scenario_sc01_b("gridmind_data/curated")

        # 1. Baseline Verification
        base = report["baseline"]
        self.assertTrue(base["is_stable"])
        self.assertEqual(base["violations_count"], 0)
        self.assertAlmostEqual(base["freq_hz"], 49.9400, places=3)
        self.assertAlmostEqual(base["total_demand_kw"], 1447.56, delta=0.5)
        self.assertAlmostEqual(base["t04_temp_c"], 101.71, delta=0.2)
        self.assertAlmostEqual(base["t02_temp_c"], 90.38, delta=0.2)
        self.assertEqual(base["critical_hospital_service_pct"], 100.0)

        # 2. Incident Verification
        inc = report["incident"]
        self.assertFalse(inc["is_stable"])
        self.assertEqual(len(inc["violations"]), 1)
        self.assertIn("T04", inc["violations"][0])
        self.assertAlmostEqual(inc["freq_hz"], 49.9204, places=3)
        self.assertAlmostEqual(inc["total_demand_kw"], 1509.37, delta=0.5)
        self.assertAlmostEqual(inc["t04_load_pct"], 116.22, delta=0.2)
        self.assertAlmostEqual(inc["t04_temp_c"], 112.65, delta=0.2)
        self.assertAlmostEqual(inc["t02_temp_c"], 99.48, delta=0.2)
        self.assertEqual(inc["critical_hospital_service_pct"], 100.0)
        # In SC01-B, L08 is OPEN (healthy and available), NOT tripped
        self.assertEqual(inc["l08_status"], LineStatus.OPEN.value)

        # 3. Sandbox Evaluations - Multiple Feasible Interventions
        sb = report["sandbox_evaluations"]

        # Option A: Load Restriction (-15% on N08)
        act1 = sb["load_restriction_15pct"]
        self.assertTrue(act1["action_valid"])
        self.assertTrue(act1["is_stable"])
        self.assertEqual(act1["violations_count"], 0)
        self.assertAlmostEqual(act1["t04_temp_c"], 97.55, delta=0.2)

        # Option B: Load Transfer via operational L08 (Feasible in SC01-B!)
        act2 = sb["load_transfer_l08"]
        self.assertTrue(act2["action_valid"])
        self.assertTrue(act2["is_stable"])
        self.assertEqual(act2["violations_count"], 0)
        self.assertAlmostEqual(act2["l08_flow_kw"], 100.0, delta=0.1)
        self.assertAlmostEqual(act2["t04_temp_c"], 95.32, delta=0.2)
        self.assertLess(act2["t04_temp_c"], 110.0)

        # Option C: Isolate T04 (Secondary Overload on T02)
        act3 = sb["isolate_t04"]
        self.assertTrue(act3["action_valid"])
        self.assertFalse(act3["is_stable"])
        self.assertAlmostEqual(act3["t02_load_pct"], 163.09, delta=0.5)
        self.assertGreater(act3["t02_temp_c"], 110.0)
        self.assertAlmostEqual(act3["t02_temp_c"], 178.71, delta=0.5)

        # Option D: Replace/Uprate T04 (500 kVA planning work order)
        act4 = sb["replace_t04_500kva"]
        self.assertTrue(act4["action_valid"])
        self.assertTrue(act4["is_stable"])
        self.assertAlmostEqual(act4["t04_temp_c"], 75.56, delta=0.2)
        self.assertAlmostEqual(act4["t02_temp_c"], 75.56, delta=0.2)

        # 4. Sandbox Isolation Invariance
        self.assertTrue(report["sandbox_isolation_verified"])

        # 5. Live State Execution (Load Transfer)
        exec_res = report["post_execution"]
        self.assertTrue(exec_res["is_stable"])
        self.assertAlmostEqual(exec_res["l08_flow_kw"], 100.0, delta=0.1)
        self.assertAlmostEqual(exec_res["t04_temp_c"], 95.32, delta=0.2)
        self.assertEqual(exec_res["critical_hospital_service_pct"], 100.0)

    def test_sc01_b_service_layer_integration(self) -> None:
        """Tests that GridMindService correctly loads and processes SC01-B."""
        service = GridMindService(data_dir="gridmind_data/curated")
        inc_resp = service.load_scenario("SC01-B")

        self.assertEqual(service.active_scenario_id, "SC01-B")
        self.assertEqual(inc_resp.scenario_id, "SC01-B")
        self.assertFalse(inc_resp.is_stable)
        self.assertIn("T04", inc_resp.overheated_transformers)
        # L08 is healthy, not in tripped lines
        self.assertNotIn("L08", inc_resp.tripped_lines)

        # 1. Evaluate load restriction in sandbox
        eval_restr = service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 15.0},
            )
        )
        self.assertTrue(eval_restr.action_valid)
        self.assertTrue(eval_restr.is_stable)

        # 2. Evaluate load transfer over L08 in sandbox
        eval_xfer = service.evaluate_action(
            ActionRequest(
                action_type="load_transfer",
                parameters={"line_id": "L08", "source": "N08", "destination": "N04", "transfer_mw": 0.100},
            )
        )
        self.assertTrue(eval_xfer.action_valid)
        self.assertTrue(eval_xfer.is_stable)
        self.assertAlmostEqual(eval_xfer.predicted_line_loadings_pct["L08"], 10.0, delta=0.1)
        self.assertAlmostEqual(eval_xfer.critical_load_service_pct["LZ04"], 100.0)

        # 3. Live execution of load transfer
        exec_resp = service.execute_action(
            ActionRequest(
                action_type="load_transfer",
                parameters={"line_id": "L08", "source": "N08", "destination": "N04", "transfer_mw": 0.100},
            )
        )
        self.assertTrue(exec_resp.success)
        self.assertTrue(exec_resp.is_stable)
        self.assertAlmostEqual(exec_resp.line_loadings_pct["L08"], 10.0, delta=0.1)

        # Verify live grid state is stable with critical hospital preserved
        grid_state = service.get_grid_state()
        self.assertTrue(grid_state.is_stable)
        l08 = next(line for line in grid_state.lines if line.line_id == "L08")
        self.assertEqual(l08.status, LineStatus.CLOSED.value)
        self.assertAlmostEqual(l08.flow_kw, 100.0, delta=0.1)


class TestScenarioSC01BMCP(unittest.IsolatedAsyncioTestCase):
    """Tests MCP tool invocation specifically against SC01-B scenario."""

    async def asyncSetUp(self) -> None:
        self.service = GridMindService(data_dir="gridmind_data/curated")
        self.mcp_wrapper = GridMindMCPServer(service=self.service)
        self.server = self.mcp_wrapper.server

    async def _call_tool_json(self, tool_name: str, arguments: dict) -> dict:
        result = await self.server.call_tool(tool_name, arguments)
        self.assertFalse(result.is_error, f"Tool '{tool_name}' returned error: {result}")
        self.assertTrue(len(result.content) > 0)
        return json.loads(result.content[0].text)

    async def test_sc01_b_mcp_evaluation_and_execution(self) -> None:
        # 1. Load SC01-B
        load_res = await self._call_tool_json("load_scenario", {"scenario_id": "SC01-B"})
        self.assertEqual(load_res["scenario_id"], "SC01-B")
        self.assertFalse(load_res["is_stable"])
        self.assertNotIn("L08", load_res["tripped_lines"])

        # 2. Evaluate load_transfer in sandbox over MCP
        eval_res = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_transfer",
                "parameters": {
                    "line_id": "L08",
                    "source": "N08",
                    "destination": "N04",
                    "transfer_mw": 0.100,
                },
            },
        )
        self.assertTrue(eval_res["action_valid"])
        self.assertTrue(eval_res["is_stable"])
        self.assertEqual(eval_res["critical_load_service_pct"]["LZ04"], 100.0)

        # 3. Live execute load_transfer over MCP
        exec_res = await self._call_tool_json(
            "execute_action",
            {
                "action_type": "load_transfer",
                "parameters": {
                    "line_id": "L08",
                    "source": "N08",
                    "destination": "N04",
                    "transfer_mw": 0.100,
                },
            },
        )
        self.assertTrue(exec_res["success"])
        self.assertTrue(exec_res["is_stable"])

        # 4. Verify live grid state
        grid_res = await self._call_tool_json("get_grid_state", {})
        self.assertTrue(grid_res["is_stable"])


if __name__ == "__main__":
    unittest.main()

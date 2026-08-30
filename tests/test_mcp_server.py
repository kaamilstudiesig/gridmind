"""
Unit and integration tests for the GridMind Model Context Protocol (MCP) server.

Tests:
1. All six tools discoverable
2. Correct tool names and descriptions
3. Correct tool annotations (readOnlyHint, destructiveHint, idempotentHint)
4. get_grid_state
5. get_incident_state
6. load_scenario
7. evaluate_action
8. execute_action
9. sandbox immutability through MCP
10. live mutation through execute_action
11. unknown action rejection
12. malformed action parameter rejection
13. invalid transfer rejection (e.g. over tripped L08 in SC01)
14. invalid restriction rejection (e.g. critical load or min_service_pct violation)
15. stdio transport / client round-trip
"""

import asyncio
import json
import sys
import unittest
from typing import Any
from unittest.mock import MagicMock

from gridmind.commander import GridMindCommander
from gridmind.llm import LLMClient
from gridmind.mcp_server import (
    DESTRUCTIVE_ANNOTATIONS,
    IDEMPOTENT_MUTATING_ANNOTATIONS,
    READ_ONLY_ANNOTATIONS,
    GridMindMCPServer,
    create_mcp_server,
)
from gridmind.service import GridMindService


class TestMCPServer(unittest.IsolatedAsyncioTestCase):
    """Tests MCP tool registration, schema validation, isolation, and execution."""

    async def asyncSetUp(self) -> None:
        self.mock_llm = MagicMock(spec=LLMClient)
        self.mock_llm.generate_narrative.side_effect = (
            lambda agent_role, status, candidates, evidence, risks, default_finding, default_recommendation: (
                default_finding,
                default_recommendation,
            )
        )
        self.service = GridMindService(data_dir="gridmind_data/curated")
        self.commander = GridMindCommander(
            service=self.service,
            llm_client=self.mock_llm,
        )
        self.mcp_wrapper = GridMindMCPServer(
            service=self.service,
            commander=self.commander,
        )
        self.server = self.mcp_wrapper.server

    async def _call_tool_json(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Helper to invoke a tool on MCPServer and parse the resulting JSON content."""
        result = await self.server.call_tool(tool_name, arguments)
        self.assertFalse(result.is_error, f"Tool '{tool_name}' returned error: {result}")
        self.assertTrue(len(result.content) > 0)
        return json.loads(result.content[0].text)

    async def test_01_all_six_tools_discoverable(self) -> None:
        """Tests that exactly the six required tools are registered and discoverable."""
        tools = await self.server.list_tools()
        tool_names = [t.name for t in tools]
        expected_tools = [
            "get_grid_state",
            "get_incident_state",
            "evaluate_action",
            "execute_action",
            "get_last_simulation_result",
            "load_scenario",
        ]
        self.assertEqual(len(tools), 6)
        self.assertEqual(sorted(tool_names), sorted(expected_tools))

    async def test_02_correct_tool_names_and_descriptions(self) -> None:
        """Tests that all tools have non-empty, descriptive descriptions."""
        tools = await self.server.list_tools()
        for t in tools:
            self.assertTrue(bool(t.description), f"Tool {t.name} is missing a description")
            self.assertGreater(len(t.description), 15)

    async def test_03_correct_tool_annotations(self) -> None:
        """Tests that tool annotations explicitly communicate read_only and destructive hints."""
        tools = await self.server.list_tools()
        tools_by_name = {t.name: t for t in tools}

        # Read-only tools
        read_only_tool_names = [
            "get_grid_state",
            "get_incident_state",
            "evaluate_action",
            "get_last_simulation_result",
        ]
        for name in read_only_tool_names:
            ann = tools_by_name[name].annotations
            self.assertIsNotNone(ann, f"Missing annotations on {name}")
            self.assertTrue(ann.read_only_hint, f"{name} should have read_only_hint=True")
            self.assertFalse(ann.destructive_hint, f"{name} should have destructive_hint=False")
            self.assertTrue(ann.idempotent_hint, f"{name} should have idempotent_hint=True")

        # Destructive execution tool
        exec_ann = tools_by_name["execute_action"].annotations
        self.assertIsNotNone(exec_ann)
        self.assertFalse(exec_ann.read_only_hint)
        self.assertTrue(exec_ann.destructive_hint)
        self.assertFalse(exec_ann.idempotent_hint)

        # Idempotent mutating tool (load_scenario)
        load_ann = tools_by_name["load_scenario"].annotations
        self.assertIsNotNone(load_ann)
        self.assertFalse(load_ann.read_only_hint)
        self.assertFalse(load_ann.destructive_hint)
        self.assertTrue(load_ann.idempotent_hint)

    async def test_04_get_grid_state_tool(self) -> None:
        """Tests get_grid_state tool returns valid structured grid state."""
        res = await self._call_tool_json("get_grid_state", {})
        self.assertTrue(res["is_stable"])
        self.assertAlmostEqual(res["frequency_hz"], 50.0000, places=3)
        self.assertEqual(len(res["nodes"]), 10)
        self.assertEqual(len(res["lines"]), 8)
        self.assertEqual(len(res["transformers"]), 5)
        self.assertEqual(len(res["load_zones"]), 4)

    async def test_05_get_incident_state_tool(self) -> None:
        """Tests get_incident_state tool after scenario initialization."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        res = await self._call_tool_json("get_incident_state", {})
        self.assertFalse(res["is_stable"])
        self.assertEqual(res["scenario_id"], "SC01")
        self.assertIn("L08", res["tripped_lines"])
        self.assertIn("T04", res["overheated_transformers"])
        self.assertEqual(len(res["unserved_critical_loads"]), 0)

    async def test_06_load_scenario_tool(self) -> None:
        """Tests load_scenario resets state to scenario conditions and returns incident state."""
        res = await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        self.assertEqual(res["scenario_id"], "SC01")
        self.assertFalse(res["is_stable"])
        self.assertIn("L08", res["tripped_lines"])

    async def test_07_evaluate_action_tool(self) -> None:
        """Tests evaluate_action performs valid sandboxed evaluation."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        res = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15.0},
            },
        )
        self.assertTrue(res["action_valid"])
        self.assertTrue(res["is_stable"])
        self.assertEqual(len(res["violations"]), 0)
        self.assertAlmostEqual(
            res["predicted_transformer_temperatures_c"]["T04"], 97.55, delta=0.2
        )

    async def test_08_execute_action_tool(self) -> None:
        """Tests execute_action applies intervention to live state."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        plan = self.mcp_wrapper.commander.plan_incident_response()
        self.mcp_wrapper.commander.authorize_plan(plan.incident_id, approved_by="operator_lead")
        res = await self._call_tool_json(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15.0},
            },
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["is_stable"])
        self.assertIsNone(res["error_message"])
        self.assertAlmostEqual(
            res["transformer_temperatures_c"]["T04"], 97.55, delta=0.2
        )

    async def test_09_critical_safety_sandbox_immutability(self) -> None:
        """
        CRITICAL SAFETY TEST:
        Load SC01, capture complete live GridState, call evaluate_action through MCP,
        capture live GridState again, and assert the live operational state is completely unchanged.
        """
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})

        # 1. Capture live GridState before evaluation
        state_before = await self._call_tool_json("get_grid_state", {})
        self.assertFalse(state_before["is_stable"])
        t04_temp_before = next(
            t["temperature_c"]
            for t in state_before["transformers"]
            if t["transformer_id"] == "T04"
        )
        self.assertAlmostEqual(t04_temp_before, 112.65, delta=0.2)

        # 2. Call evaluate_action through MCP
        eval_res = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15.0},
            },
        )
        self.assertTrue(eval_res["action_valid"])
        self.assertTrue(eval_res["is_stable"])

        # 3. Capture live GridState again and assert complete immutability
        state_after = await self._call_tool_json("get_grid_state", {})
        self.assertFalse(state_after["is_stable"])
        t04_temp_after = next(
            t["temperature_c"]
            for t in state_after["transformers"]
            if t["transformer_id"] == "T04"
        )
        self.assertEqual(t04_temp_before, t04_temp_after)
        self.assertEqual(state_before["active_violations"], state_after["active_violations"])
        self.assertEqual(state_before["total_demand_kw"], state_after["total_demand_kw"])

    async def test_10_live_mutation_after_execution(self) -> None:
        """Tests that execute_action mutates live state permanently."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        plan = self.mcp_wrapper.commander.plan_incident_response()
        self.mcp_wrapper.commander.authorize_plan(plan.incident_id, approved_by="operator_lead")

        # Execute load restriction
        exec_res = await self._call_tool_json(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15.0},
            },
        )
        self.assertTrue(exec_res["success"])
        self.assertTrue(exec_res["is_stable"])

        # Check live state is now stable and mutated
        live_state = await self._call_tool_json("get_grid_state", {})
        self.assertTrue(live_state["is_stable"])
        n08_lz = next(lz for lz in live_state["load_zones"] if lz["load_id"] == "LZ02")
        self.assertEqual(n08_lz["curtailment_pct"], 15.0)

    async def test_11_unknown_action_rejection(self) -> None:
        """Tests that unknown action_type is rejected."""
        res_eval = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "unsupported_magic_action",
                "parameters": {"target": "N08"},
            },
        )
        self.assertFalse(res_eval["action_valid"])
        self.assertIn("Unknown action_type", res_eval["rejection_reason"])

        res_exec = await self._call_tool_json(
            "execute_action",
            {
                "action_type": "unsupported_magic_action",
                "parameters": {"target": "N08"},
            },
        )
        self.assertFalse(res_exec["success"])
        self.assertIn("Unknown action_type", res_exec["error_message"])

    async def test_12_malformed_action_rejection(self) -> None:
        """Tests that missing required parameters are rejected by Pydantic action schema validation."""
        # Missing reduction_pct for load_restriction
        res = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08"},
            },
        )
        self.assertFalse(res["action_valid"])
        self.assertIn("Invalid parameters", res["rejection_reason"])
        self.assertIn("reduction_pct", res["rejection_reason"])

    async def test_13_invalid_transfer_rejection(self) -> None:
        """Tests that transfer over tripped L08 in SC01 is rejected."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        res = await self._call_tool_json(
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
        self.assertFalse(res["action_valid"])
        self.assertIn("tripped", res["rejection_reason"].lower())

    async def test_14_invalid_restriction_rejection(self) -> None:
        """Tests restriction of critical load or violation of min_service_pct is rejected."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})

        # Critical hospital load restriction
        res_crit = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N10", "reduction_pct": 10.0},
            },
        )
        self.assertFalse(res_crit["action_valid"])
        self.assertIn("CRITICAL", res_crit["rejection_reason"])

        # Below min_service_pct restriction on N08
        res_min = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 50.0},
            },
        )
        self.assertFalse(res_min["action_valid"])
        self.assertIn("minimum service requirement", res_min["rejection_reason"])

    async def test_15_get_last_simulation_result(self) -> None:
        """Tests get_last_simulation_result retrieval via MCP."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        res = await self._call_tool_json("get_last_simulation_result", {})
        self.assertFalse(res["is_stable"])
        self.assertAlmostEqual(
            res["predicted_transformer_temperatures_c"]["T04"], 112.65, delta=0.2
        )

    async def test_16_stdio_transport_client_roundtrip(self) -> None:
        """Tests end-to-end client communication over standard I/O transport."""
        from mcp.client.session import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters

        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "gridmind.server_cli"],
            env=None,
        )
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                # List tools over stdio
                tools_resp = await session.list_tools()
                self.assertEqual(len(tools_resp.tools), 6)

                # Call get_grid_state over stdio
                res = await session.call_tool("get_grid_state", {})
                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertTrue(data["is_stable"])
                self.assertAlmostEqual(data["frequency_hz"], 50.0000, places=3)

    async def test_17_load_scenario_unsupported_id_error(self) -> None:
        """Tests that loading an unsupported scenario ID returns structured error without corrupting active state."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})
        res = await self._call_tool_json("load_scenario", {"scenario_id": "NON_EXISTENT_SCENARIO"})
        self.assertFalse(res.get("success", True))
        self.assertIn("Unsupported scenario ID", res.get("error", ""))
        self.assertEqual(res.get("scenario_id"), "SC01")

        # Verify active incident state is still SC01
        inc = await self._call_tool_json("get_incident_state", {})
        self.assertEqual(inc["scenario_id"], "SC01")
        self.assertFalse(inc["is_stable"])

    async def test_19_mcp_rejection_contract_consistency(self) -> None:
        """Tests that all MCP rejection/error responses conform to the consistent structured contract."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})

        # Case A: Unknown action type in evaluate_action
        res_eval_unknown = await self._call_tool_json(
            "evaluate_action",
            {"action_type": "completely_bogus_action", "parameters": {}},
        )
        self.assertFalse(res_eval_unknown["action_valid"])
        self.assertFalse(res_eval_unknown["is_stable"])
        self.assertIn("Allowed actions", res_eval_unknown["rejection_reason"])
        self.assertTrue(len(res_eval_unknown["violations"]) > 0)
        self.assertIn("LZ04", res_eval_unknown["critical_load_service_pct"])

        # Case B: Unknown action type in execute_action
        res_exec_unknown = await self._call_tool_json(
            "execute_action",
            {"action_type": "completely_bogus_action", "parameters": {}},
        )
        self.assertFalse(res_exec_unknown["success"])
        self.assertFalse(res_exec_unknown["is_stable"])
        self.assertIn("Allowed actions", res_exec_unknown["error_message"])
        self.assertTrue(len(res_exec_unknown["violations"]) > 0)
        self.assertIn("LZ04", res_exec_unknown["critical_load_service_pct"])

        # Case C: Domain constraint rejection in execute_action (curtailing unapproved/critical target)
        res_exec_crit = await self._call_tool_json(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N10", "reduction_pct": 20.0},
            },
        )
        self.assertFalse(res_exec_crit["success"])
        self.assertFalse(res_exec_crit["is_stable"])
        self.assertTrue(
            any(k in res_exec_crit["error_message"] for k in ("APPROVAL_REQUIRED", "INVALID_ACTION", "CRITICAL"))
        )
        self.assertIn("LZ04", res_exec_crit["critical_load_service_pct"])

    async def test_20_reduction_pct_numeric_percentage_and_string_rejection(self) -> None:
        """Tests that reduction_pct is explicitly interpreted as a numeric percentage (0-100) and strings are rejected."""
        await self._call_tool_json("load_scenario", {"scenario_id": "SC01"})

        # 1. 15 -> 15% reduction (T04 cools to 97.55°C, stable grid)
        res_15 = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15},
            },
        )
        self.assertTrue(res_15["action_valid"])
        self.assertTrue(res_15["is_stable"])
        self.assertAlmostEqual(res_15["predicted_transformer_temperatures_c"]["T04"], 97.55, delta=0.2)

        # 2. 5 -> 5% reduction (valid reduction, T04 cools to 107.45°C)
        res_5 = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 5},
            },
        )
        self.assertTrue(res_5["action_valid"])
        self.assertTrue(res_5["is_stable"])
        self.assertAlmostEqual(res_5["predicted_transformer_temperatures_c"]["T04"], 107.45, delta=0.2)

        # 3. 0.15 -> 0.15% reduction (NOT 15%, so T04 remains overheated at 112.49°C)
        res_015_float = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 0.15},
            },
        )
        self.assertTrue(res_015_float["action_valid"])
        self.assertFalse(res_015_float["is_stable"])
        self.assertAlmostEqual(res_015_float["predicted_transformer_temperatures_c"]["T04"], 112.49, delta=0.2)

        # 4. "15" string -> REJECTED
        res_15_str = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": "15"},
            },
        )
        self.assertFalse(res_15_str["action_valid"])
        self.assertIn("rejection_reason", res_15_str)
        self.assertIn("numeric", res_15_str["rejection_reason"])

        # 5. "0.15" string -> REJECTED
        res_015_str = await self._call_tool_json(
            "evaluate_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": "0.15"},
            },
        )
        self.assertFalse(res_015_str["action_valid"])
        self.assertIn("rejection_reason", res_015_str)
        self.assertIn("numeric", res_015_str["rejection_reason"])


if __name__ == "__main__":
    unittest.main()

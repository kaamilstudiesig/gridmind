"""
Regression and Integration tests for TrueForge MCP Execution Gate.

Proves:
1. Direct MCP `execute_action` cannot bypass Commander authorization.
2. Stale-state scenario changes (e.g. SC02 planned -> BASE loaded -> SC02 executed)
   are rejected with STALE_STATE and leave BASE grid state completely untouched.
3. No pending approval -> APPROVAL_REQUIRED rejection.
4. Wrong action / parameters -> INVALID_ACTION rejection.
5. Legitimate planned & approved action delegates to Commander, verifies post-state,
   and persists VERIFIED AuditRecord.
6. Atomic claim prevents duplicate concurrent executions.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from typing import Any

from mcp import ClientSession, StdioServerParameters, stdio_client

from unittest.mock import MagicMock
from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecordStatus, GridMindCommander
from gridmind.llm import LLMClient
from gridmind.mcp_server import GridMindMCPServer
from gridmind.service import GridMindService


class TestTrueForgeExecutionGate(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying Commander gating on MCP execute_action."""

    async def asyncSetUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_audit.db")
        self.audit_store = AuditStore(db_path=self.db_path)
        self.mock_llm = MagicMock(spec=LLMClient)
        self.mock_llm.generate_narrative.side_effect = (
            lambda agent_role, status, candidates, evidence, risks, default_finding, default_recommendation: (
                default_finding,
                default_recommendation,
            )
        )
        self.service = GridMindService()
        self.commander = GridMindCommander(
            service=self.service,
            audit_store=self.audit_store,
            llm_client=self.mock_llm,
        )
        self.mcp_wrapper = GridMindMCPServer(
            service=self.service,
            commander=self.commander,
            audit_store=self.audit_store,
        )
        self.server = self.mcp_wrapper.server

    async def asyncTearDown(self) -> None:
        self.tmp_dir.cleanup()

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Directly invokes an MCP tool on the server instance."""
        res = await self.server.call_tool(name, arguments)
        self.assertIsNotNone(res)
        text = res[0].text if isinstance(res, list) else res.content[0].text
        return json.loads(text)

    async def test_01_stale_state_rehearsal_sc02_then_base_rejected(self) -> None:
        """
        REPRODUCES THE LIVE TRUEFORGE REHEARSAL SCENARIO:
        1. Load SC02 (T01 overheated).
        2. Create Commander recommendation (load_restriction on N07, 15%).
        3. Record reaches PENDING_APPROVAL with SC02 state_revision.
        4. Load BASE, changing the live scenario and state_revision.
        5. Attempt to execute the old SC02 recommendation through MCP execute_action.
        6. Execution MUST be rejected with STALE_STATE.
        7. AuditRecord MUST be transitioned to STALE_STATE.
        8. Live BASE grid state MUST remain completely untouched.
        """
        # Step 1: Load SC02
        load_res = await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        self.assertEqual(load_res["scenario_id"], "SC02")
        self.assertFalse(load_res["is_stable"])

        # Step 2 & 3: Commander plans SC02 incident
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertEqual(plan.recommended_action["action_type"], "load_restriction")
        self.assertEqual(plan.recommended_action["parameters"]["target"], "N07")
        sc02_incident_id = plan.incident_id

        # Step 4: Load BASE out-of-band
        base_load = await self._call_tool("load_scenario", {"scenario_id": "BASE"})
        self.assertEqual(base_load["scenario_id"], "BASE")
        self.assertTrue(base_load["is_stable"])

        # Capture BASE baseline metrics
        base_state_before = await self._call_tool("get_grid_state", {})
        self.assertTrue(base_state_before["is_stable"])
        self.assertEqual(len(base_state_before["active_violations"]), 0)
        n07_lz_before = next(lz for lz in base_state_before["load_zones"] if lz["node_id"] == "N07")
        self.assertEqual(n07_lz_before["curtailment_pct"], 0.0)

        # Step 5: Attempt executing the old SC02 recommendation via MCP execute_action
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N07", "reduction_pct": 15.0},
            },
        )

        # Step 6: Assert execution is rejected with STALE_STATE or APPROVAL_REQUIRED
        self.assertFalse(exec_res["success"])
        self.assertIn("STALE_STATE", exec_res["error_message"] or exec_res["summary"])

        # Step 7: Assert AuditRecord was marked STALE_STATE
        saved_rec = self.audit_store.get(sc02_incident_id)
        self.assertEqual(saved_rec["status"], AuditRecordStatus.STALE_STATE.value)

        # Step 8: Assert BASE grid state is completely unmutated
        base_state_after = await self._call_tool("get_grid_state", {})
        self.assertTrue(base_state_after["is_stable"])
        self.assertEqual(len(base_state_after["active_violations"]), 0)
        n07_lz_after = next(lz for lz in base_state_after["load_zones"] if lz["node_id"] == "N07")
        self.assertEqual(n07_lz_after["curtailment_pct"], 0.0)

    async def test_02_direct_mcp_call_without_pending_approval_rejected(self) -> None:
        """Requirement A: Raw MCP execute_action without PENDING_APPROVAL is rejected (no mutation)."""
        await self._call_tool("load_scenario", {"scenario_id": "SC01"})

        # Attempt execute_action directly without planning
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15.0},
            },
        )

        self.assertFalse(exec_res["success"])
        self.assertIn("APPROVAL_REQUIRED", exec_res["error_message"])

        # Grid state remains in original incident state
        grid_state = await self._call_tool("get_grid_state", {})
        self.assertFalse(grid_state["is_stable"])

    async def test_03_pending_approval_but_wrong_action_type_rejected(self) -> None:
        """Requirement B: Raw MCP execute_action with mismatched action_type is rejected (no mutation)."""
        await self._call_tool("load_scenario", {"scenario_id": "SC01"})
        # Commander plans load_restriction on N08
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        # Attempt to execute isolate_transformer instead
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "isolate_transformer",
                "parameters": {"transformer_id": "T04"},
            },
        )

        self.assertFalse(exec_res["success"])
        self.assertIn("INVALID_ACTION", exec_res["error_message"])

    async def test_04_pending_approval_but_wrong_parameters_rejected(self) -> None:
        """Requirement C: Raw MCP execute_action with mismatched parameters is rejected (no mutation)."""
        await self._call_tool("load_scenario", {"scenario_id": "SC01"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        # Attempt to execute with 50.0% reduction instead of planned 15.0%
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 50.0},
            },
        )

        self.assertFalse(exec_res["success"])
        self.assertIn("INVALID_ACTION", exec_res["error_message"])

    async def test_05_valid_planned_action_executes_and_reaches_verified(self) -> None:
        """Requirement E: Matching planned action delegates through Commander, verifies state, and updates AuditRecord."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)
        inc_id = plan.incident_id

        # Execute matching planned action over MCP
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N07", "reduction_pct": 15.0},
            },
        )

        self.assertTrue(exec_res["success"])
        self.assertTrue(exec_res["is_stable"])

        # Audit record updated to VERIFIED
        saved_rec = self.audit_store.get(inc_id)
        self.assertEqual(saved_rec["status"], AuditRecordStatus.VERIFIED.value)
        self.assertTrue(saved_rec["execution"]["executed"])
        self.assertTrue(saved_rec["verification"]["verified"])

        # Grid state is physically recovered
        grid_state = await self._call_tool("get_grid_state", {})
        self.assertTrue(grid_state["is_stable"])
        t01_temp = next(t["temperature_c"] for t in grid_state["transformers"] if t["transformer_id"] == "T01")
        self.assertLess(t01_temp, 110.0)

    async def test_06_second_execution_attempt_rejected_by_atomic_claim(self) -> None:
        """Requirement F: Second execution attempt on the same plan is rejected."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        # First execution succeeds
        exec_1 = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N07", "reduction_pct": 15.0},
            },
        )
        self.assertTrue(exec_1["success"])

        # Second execution attempt is rejected (no longer PENDING_APPROVAL)
        exec_2 = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N07", "reduction_pct": 15.0},
            },
        )
        self.assertFalse(exec_2["success"])
        self.assertIn("APPROVAL_REQUIRED", exec_2["error_message"])

    async def test_07_concurrent_execution_attempts_atomic_single_winner(self) -> None:
        """Concurrency: Two simultaneous execution calls result in exactly one execution."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        call_args = {
            "action_type": "load_restriction",
            "parameters": {"target": "N07", "reduction_pct": 15.0},
        }

        # Dispatch two concurrent tool calls
        results = await asyncio.gather(
            self._call_tool("execute_action", call_args),
            self._call_tool("execute_action", call_args),
            return_exceptions=False,
        )

        successes = [r for r in results if r.get("success") is True]
        failures = [r for r in results if r.get("success") is False]

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)


if __name__ == "__main__":
    unittest.main()

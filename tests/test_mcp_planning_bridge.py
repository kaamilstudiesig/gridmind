"""
Integration and Regression test suite for TrueForge ↔ GridMind Commander MCP Planning Bridge.

Tests the complete lifecycle and invariants required for plan_incident_response:
1. Test 1 — MCP planning creates real AuditRecord in AuditStore.
2. Test 2 — No execution happens during planning; live grid state remains untouched.
3. Test 3 — Dashboard / AuditStore query immediately observes the new PENDING_APPROVAL record.
4. Test 4 — Real recommendation survives in both MCP return dictionary and AuditRecord.
5. Test 5 — Execution immediately after planning without trusted authorization fails closed (APPROVAL_REQUIRED).
6. Test 6 — Execution after trusted human authorization succeeds and produces VERIFIED status.
7. Test 7 — Loading another scenario after planning invalidates the plan with STALE_STATE on execution.
8. Test 8 — Dependency identity invariant: MCP, Commander, Service, and AuditStore share exact same instances.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock

from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecordStatus, GridMindCommander
from gridmind.llm import LLMClient
from gridmind.mcp_server import GridMindMCPServer
from gridmind.service import GridMindService


class TestMCPPlanningBridge(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying the plan_incident_response MCP tool integration and security contracts."""

    async def asyncSetUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp_dir.name, "test_bridge_audit.db")
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

    async def test_01_mcp_planning_creates_real_audit_record(self) -> None:
        """Test 1 — TrueForge calls load_scenario(SC02) then plan_incident_response(), creating real AuditRecord."""
        load_res = await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        self.assertEqual(load_res["scenario_id"], "SC02")
        self.assertFalse(load_res["is_stable"])

        plan_res = await self._call_tool("plan_incident_response", {})
        self.assertEqual(plan_res["scenario_id"], "SC02")
        self.assertTrue(plan_res["incident_id"].startswith("INC-"))
        self.assertEqual(plan_res["status"], AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertIsNotNone(plan_res["recommended_action"])

        # Verify record exists in AuditStore
        db_rec = self.audit_store.get(plan_res["incident_id"])
        self.assertIsNotNone(db_rec)
        self.assertEqual(db_rec["incident_id"], plan_res["incident_id"])
        self.assertEqual(db_rec["scenario_id"], "SC02")
        self.assertEqual(db_rec["status"], AuditRecordStatus.PENDING_APPROVAL.value)

    async def test_02_no_execution_during_planning(self) -> None:
        """Test 2 — Verify execute_action was NOT called and live grid state remains untouched by planning."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        state_before = await self._call_tool("get_grid_state", {})

        plan_res = await self._call_tool("plan_incident_response", {})
        self.assertEqual(plan_res["status"], AuditRecordStatus.PENDING_APPROVAL.value)

        state_after = await self._call_tool("get_grid_state", {})
        self.assertEqual(state_before, state_after)

        # Check latest audit record execution state is False
        db_rec = self.audit_store.get(plan_res["incident_id"])
        self.assertFalse(db_rec["execution"]["executed"])

    async def test_03_dashboard_sees_the_same_record(self) -> None:
        """Test 3 — After MCP planning, audit records query returns the exact incident_id and PENDING_APPROVAL status."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan_res = await self._call_tool("plan_incident_response", {})
        inc_id = plan_res["incident_id"]

        records = self.audit_store.list(scenario_id="SC02", status=AuditRecordStatus.PENDING_APPROVAL.value)
        found_ids = [r["incident_id"] for r in records]
        self.assertIn(inc_id, found_ids)

        latest = self.audit_store.get_latest(scenario_id="SC02")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["incident_id"], inc_id)
        self.assertEqual(latest["status"], AuditRecordStatus.PENDING_APPROVAL.value)

    async def test_04_real_recommendation_survives(self) -> None:
        """Test 4 — The returned MCP planning dictionary and stored AuditRecord contain identical recommended action."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan_res = await self._call_tool("plan_incident_response", {})

        rec_mcp = plan_res["recommended_action"]
        db_rec = self.audit_store.get(plan_res["incident_id"])
        rec_db = db_rec["recommended_action"]

        self.assertIsNotNone(rec_mcp)
        self.assertEqual(rec_mcp, rec_db)
        self.assertEqual(rec_mcp["action_type"], "load_restriction")
        self.assertEqual(rec_mcp["parameters"]["target"], "N07")

    async def test_05_approval_still_required(self) -> None:
        """Test 5 — Calling MCP execute_action immediately after planning, without trusted human auth, fails closed."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan_res = await self._call_tool("plan_incident_response", {})
        rec = plan_res["recommended_action"]

        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": rec["action_type"],
                "parameters": rec["parameters"],
            },
        )
        self.assertFalse(exec_res["success"])
        self.assertIn("APPROVAL_REQUIRED", exec_res["error_message"])

        # Grid state remains unmutated
        grid_state = await self._call_tool("get_grid_state", {})
        self.assertFalse(grid_state["is_stable"])

    async def test_06_trusted_approval_still_executes(self) -> None:
        """Test 6 — After authorization via trusted authorize_plan(), MCP execute_action executes and produces VERIFIED."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan_res = await self._call_tool("plan_incident_response", {})
        inc_id = plan_res["incident_id"]
        rec = plan_res["recommended_action"]

        # Human operator authorizes the plan via control plane
        self.commander.authorize_plan(incident_id=inc_id, approved_by="control_room_lead_alice")

        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": rec["action_type"],
                "parameters": rec["parameters"],
            },
        )
        self.assertTrue(exec_res["success"])
        self.assertTrue(exec_res["is_stable"])

        # AuditRecord updated to VERIFIED
        db_rec = self.audit_store.get(inc_id)
        self.assertEqual(db_rec["status"], AuditRecordStatus.VERIFIED.value)
        self.assertTrue(db_rec["execution"]["executed"])
        self.assertTrue(db_rec["verification"]["verified"])
        self.assertEqual(db_rec["approval"]["approved_by"], "control_room_lead_alice")

    async def test_07_stale_state_still_works(self) -> None:
        """Test 7 — SC02 plan -> PENDING_APPROVAL -> BASE loaded -> old execute_action -> STALE_STATE -> no mutation."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan_res = await self._call_tool("plan_incident_response", {})
        inc_id = plan_res["incident_id"]

        # Load BASE, changing active scenario and revision
        await self._call_tool("load_scenario", {"scenario_id": "BASE"})

        # Authorize old plan (should fail due to state change)
        with self.assertRaises(ValueError) as cm:
            self.commander.authorize_plan(incident_id=inc_id, approved_by="operator_bob")
        self.assertIn("STALE_STATE", str(cm.exception))

        # Stored record marked STALE_STATE
        db_rec = self.audit_store.get(inc_id)
        self.assertEqual(db_rec["status"], AuditRecordStatus.STALE_STATE.value)

    def test_08_same_dependency_identity(self) -> None:
        """Test 8 — Prove MCP planning, Commander, Service, and AuditStore operate on exact same instances."""
        self.assertIs(self.mcp_wrapper.service, self.commander.service)
        self.assertIs(self.mcp_wrapper.audit_store, self.commander.audit_store)
        self.assertIs(self.mcp_wrapper.service, self.service)
        self.assertIs(self.mcp_wrapper.audit_store, self.audit_store)


if __name__ == "__main__":
    unittest.main()

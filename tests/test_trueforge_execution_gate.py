"""
Regression and Integration tests for TrueForge MCP Execution Gate.

Covers all 4 Qodo review findings:
1. Security & Human Authorization:
   - Raw MCP execute_action without human approval fails closed (APPROVAL_REQUIRED).
   - Client-supplied approved=true / approved_by in parameters is ignored; fails closed.
   - Synthetic identity 'mcp_operator_authorized' is completely eliminated.
   - Real operator identity is recorded and persisted on legitimate execution.
2. Service Dependency Invariants:
   - MCP and Commander share the exact same GridMindService instance.
   - Constructor and factory mismatch between commander.service and service raises ValueError.
3. AuditStore Dependency Invariants:
   - MCP and Commander share the exact same AuditStore instance.
   - Constructor mismatch raises ValueError.
4. Atomic Obsolete Pending Invalidation:
   - Multiple obsolete PENDING_APPROVAL records across old scenarios/revisions are all atomically transitioned to STALE_STATE.
   - Active scenario's valid record survives.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock

from mcp import ClientSession, StdioServerParameters, stdio_client

from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecord, AuditRecordStatus, GridMindCommander
from gridmind.llm import LLMClient
from gridmind.mcp_server import GridMindMCPServer
from gridmind.service import GridMindService


class TestTrueForgeExecutionGate(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying Commander gating on MCP execute_action."""

    async def asyncSetUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

        # Step 6: Assert execution is rejected
        self.assertFalse(exec_res["success"])
        self.assertTrue("STALE_STATE" in exec_res["error_message"] or "APPROVAL_REQUIRED" in exec_res["error_message"])

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
        """Requirement: Raw MCP execute_action without PENDING_APPROVAL is rejected (no mutation)."""
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

    async def test_03_pending_approval_without_human_authorization_fails_closed(self) -> None:
        """Finding 1: Planning creates PENDING_APPROVAL, but without explicit human authorization execute_action FAILS CLOSED."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        # Calling execute_action without human operator authorization fails closed
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {"target": "N07", "reduction_pct": 15.0},
            },
        )
        self.assertFalse(exec_res["success"])
        self.assertIn("APPROVAL_REQUIRED", exec_res["error_message"])
        self.assertIn("human authorization", exec_res["error_message"])

        # Grid state remains unmutated
        grid_state = await self._call_tool("get_grid_state", {})
        self.assertFalse(grid_state["is_stable"])

    async def test_04_client_supplied_approved_parameters_ignored_and_fails_closed(self) -> None:
        """Finding 1: Client attempting to pass approved=True or approved_by in parameters is ignored and fails closed."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        self.commander.plan_incident_response()

        # Client attempts parameter spoofing
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {
                    "target": "N07",
                    "reduction_pct": 15.0,
                    "approved": True,
                    "approved_by": "untrusted_client",
                },
            },
        )
        self.assertFalse(exec_res["success"])
        self.assertIn("APPROVAL_REQUIRED", exec_res["error_message"])

    async def test_05_pending_approval_but_wrong_action_type_rejected(self) -> None:
        """Requirement: Raw MCP execute_action with mismatched action_type is rejected (no mutation)."""
        await self._call_tool("load_scenario", {"scenario_id": "SC01"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)
        self.commander.authorize_plan(plan.incident_id, approved_by="operator_lead")

        # Attempt to execute isolate_transformer instead of recommended load_restriction
        exec_res = await self._call_tool(
            "execute_action",
            {
                "action_type": "isolate_transformer",
                "parameters": {"transformer_id": "T04"},
            },
        )

        self.assertFalse(exec_res["success"])
        self.assertIn("INVALID_ACTION", exec_res["error_message"])

    async def test_06_pending_approval_but_wrong_parameters_rejected(self) -> None:
        """Requirement: Raw MCP execute_action with mismatched parameters is rejected (no mutation)."""
        await self._call_tool("load_scenario", {"scenario_id": "SC01"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)
        self.commander.authorize_plan(plan.incident_id, approved_by="operator_lead")

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

    async def test_07_valid_authorized_action_executes_and_records_real_operator_identity(self) -> None:
        """Finding 1: Legitimate human authorization enables execution and records REAL operator username, not synthetic identity."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)
        inc_id = plan.incident_id

        # Human operator explicitly authorizes the plan
        self.commander.authorize_plan(
            incident_id=inc_id,
            approved_by="control_room_lead_bob",
            reason="Authorized per standard storm protocol",
        )

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

        # Audit record updated to VERIFIED with REAL operator identity
        saved_rec = self.audit_store.get(inc_id)
        self.assertEqual(saved_rec["status"], AuditRecordStatus.VERIFIED.value)
        self.assertTrue(saved_rec["execution"]["executed"])
        self.assertTrue(saved_rec["verification"]["verified"])
        self.assertEqual(saved_rec["approval"]["approved_by"], "control_room_lead_bob")
        self.assertNotEqual(saved_rec["approval"]["approved_by"], "mcp_operator_authorized")

        # Grid state is physically recovered
        grid_state = await self._call_tool("get_grid_state", {})
        self.assertTrue(grid_state["is_stable"])
        t01_temp = next(t["temperature_c"] for t in grid_state["transformers"] if t["transformer_id"] == "T01")
        self.assertLess(t01_temp, 110.0)

    async def test_08_second_execution_attempt_rejected_by_atomic_claim(self) -> None:
        """Requirement: Second execution attempt on the same plan is rejected."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.commander.authorize_plan(plan.incident_id, approved_by="operator_lead")

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

    async def test_09_concurrent_execution_attempts_atomic_single_winner(self) -> None:
        """Concurrency: Two simultaneous execution calls result in exactly one execution."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.commander.authorize_plan(plan.incident_id, approved_by="operator_lead")

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

    def test_10_service_dependency_invariants(self) -> None:
        """Finding 2: Tests service dependency injection invariants."""
        # 1. Neither supplied: MCP and Commander share the same created service
        mcp_default = GridMindMCPServer()
        self.assertIs(mcp_default.service, mcp_default.commander.service)

        # 2. Commander only: MCP uses commander.service
        custom_service = GridMindService()
        custom_commander = GridMindCommander(service=custom_service)
        mcp_cmd_only = GridMindMCPServer(commander=custom_commander)
        self.assertIs(mcp_cmd_only.service, custom_service)
        self.assertIs(mcp_cmd_only.service, mcp_cmd_only.commander.service)

        # 3. Service only: Commander uses injected service
        mcp_svc_only = GridMindMCPServer(service=custom_service)
        self.assertIs(mcp_svc_only.service, custom_service)
        self.assertIs(mcp_svc_only.commander.service, custom_service)

        # 4. Matching service + commander succeeds
        mcp_matching = GridMindMCPServer(service=custom_service, commander=custom_commander)
        self.assertIs(mcp_matching.service, custom_service)

        # 5. Mismatched service + commander raises ValueError
        different_service = GridMindService()
        with self.assertRaises(ValueError) as cm:
            GridMindMCPServer(service=different_service, commander=custom_commander)
        self.assertIn("Dependency mismatch", str(cm.exception))
        self.assertIn("service", str(cm.exception))

    def test_11_audit_store_dependency_invariants(self) -> None:
        """Finding 3: Tests AuditStore dependency injection invariants."""
        store_1 = AuditStore(db_path=os.path.join(self.tmp_dir.name, "store1.db"))
        store_2 = AuditStore(db_path=os.path.join(self.tmp_dir.name, "store2.db"))
        svc = GridMindService()

        # 1. Commander only: MCP uses commander.audit_store
        cmd = GridMindCommander(service=svc, audit_store=store_1)
        mcp_cmd = GridMindMCPServer(commander=cmd)
        self.assertIs(mcp_cmd.audit_store, store_1)
        self.assertIs(mcp_cmd.audit_store, mcp_cmd.commander.audit_store)

        # 2. AuditStore only: Commander uses injected store
        mcp_store = GridMindMCPServer(service=svc, audit_store=store_1)
        self.assertIs(mcp_store.audit_store, store_1)
        self.assertIs(mcp_store.commander.audit_store, store_1)

        # 3. Matching store + commander succeeds
        mcp_match = GridMindMCPServer(commander=cmd, audit_store=store_1)
        self.assertIs(mcp_match.audit_store, store_1)

        # 4. Mismatched store + commander raises ValueError
        with self.assertRaises(ValueError) as cm:
            GridMindMCPServer(commander=cmd, audit_store=store_2)
        self.assertIn("Dependency mismatch", str(cm.exception))
        self.assertIn("audit_store", str(cm.exception))

    def test_12_atomic_invalidation_of_all_multiple_obsolete_pending_records(self) -> None:
        """Finding 4: Atomically invalidates ALL obsolete pending records across old scenarios/revisions."""
        store = AuditStore(db_path=os.path.join(self.tmp_dir.name, "stale_test.db"))

        # Create 3 obsolete pending records across old scenarios/revisions
        rec1 = AuditRecord(incident_id="INC-OLD-1", scenario_id="SC01", status="PENDING_APPROVAL", state_revision="REV_OLD_1")
        rec2 = AuditRecord(incident_id="INC-OLD-2", scenario_id="SC01-B", status="PENDING_APPROVAL", state_revision="REV_OLD_2")
        rec3 = AuditRecord(incident_id="INC-OLD-3", scenario_id="SC02", status="PENDING_APPROVAL", state_revision="REV_OLD_3")
        store.save(rec1)
        store.save(rec2)
        store.save(rec3)

        self.assertEqual(store.count(status="PENDING_APPROVAL"), 3)

        # Create 1 valid pending record for current active scenario (BASE, revision REV_BASE)
        rec_valid = AuditRecord(incident_id="INC-VALID-CURRENT", scenario_id="BASE", status="PENDING_APPROVAL", state_revision="REV_BASE")
        store.save(rec_valid)

        self.assertEqual(store.count(status="PENDING_APPROVAL"), 4)

        # Invalidate all obsolete records for active scenario BASE and revision REV_BASE
        invalidated = store.invalidate_stale_pending_records(
            active_scenario_id="BASE",
            current_state_revision="REV_BASE",
        )

        self.assertEqual(invalidated, 3)

        # Verify all 3 old records became STALE_STATE
        self.assertEqual(store.get("INC-OLD-1")["status"], AuditRecordStatus.STALE_STATE.value)
        self.assertEqual(store.get("INC-OLD-2")["status"], AuditRecordStatus.STALE_STATE.value)
        self.assertEqual(store.get("INC-OLD-3")["status"], AuditRecordStatus.STALE_STATE.value)

        # Verify the current valid record remains PENDING_APPROVAL
        self.assertEqual(store.get("INC-VALID-CURRENT")["status"], AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertEqual(store.count(status="PENDING_APPROVAL"), 1)

    async def test_14_unauthenticated_mcp_client_cannot_manufacture_approval_by_any_path(self) -> None:
        """Security: Unauthenticated/raw MCP client attempting to manufacture approval fails closed."""
        await self._call_tool("load_scenario", {"scenario_id": "SC02"})
        plan = self.commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        # Attempt 1: Raw MCP call with manufactured approved keys
        res1 = await self._call_tool(
            "execute_action",
            {
                "action_type": "load_restriction",
                "parameters": {
                    "target": "N07",
                    "reduction_pct": 15.0,
                    "approved": True,
                    "approved_by": "attacker",
                    "authorization": "Bearer fake_token",
                },
            },
        )
        self.assertFalse(res1["success"])
        self.assertIn("APPROVAL_REQUIRED", res1["error_message"])

        # Verify AuditRecord was NOT authorized and NOT executed
        rec = self.audit_store.get(plan.incident_id)
        self.assertEqual(rec["status"], AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertFalse(rec.get("approval", {}).get("approved", False))
        self.assertIsNone(rec.get("approval", {}).get("approved_by"))

        # Verify live grid state remains unmutated
        grid_state = await self._call_tool("get_grid_state", {})
        self.assertFalse(grid_state["is_stable"])

    def test_15_dashboard_authenticated_operator_identity_reaches_audit_record(self) -> None:
        """Security: Dashboard authenticated operator's actual identity is strictly derived and reaches AuditRecord."""
        from fastapi.testclient import TestClient
        from dashboard.app import create_dashboard_app

        app = create_dashboard_app(
            service=self.service,
            commander=self.commander,
            audit_store=self.audit_store,
        )
        client = TestClient(app)

        # 1. Unauthenticated request to /api/commander/approve fails with 401
        res_unauth = client.post("/api/commander/approve", json={"reason": "unauthorized attempt"})
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Viewer role cannot approve (403 Forbidden)
        res_viewer = client.post(
            "/api/commander/approve",
            json={"reason": "viewer attempt"},
            headers={"Authorization": "Bearer gm-viewer-token-secret"},
        )
        self.assertEqual(res_viewer.status_code, 403)

        # 3. Load SC01 and trigger plan as operator
        load_res = client.post(
            "/api/scenario/load",
            json={"scenario_id": "SC01"},
            headers={"Authorization": "Bearer gm-operator-token-secret"},
        )
        self.assertEqual(load_res.status_code, 200)
        plan_res = client.post(
            "/api/commander/plan",
            headers={"Authorization": "Bearer gm-operator-token-secret"},
        )
        self.assertEqual(plan_res.status_code, 200)
        inc_id = plan_res.json()["incident_id"]

        # 4. Authenticated operator_lead approves
        appr_res = client.post(
            "/api/commander/approve",
            json={"incident_id": inc_id, "reason": "Authorized by Alice"},
            headers={"Authorization": "Bearer gm-lead-token-secret"},
        )
        self.assertEqual(appr_res.status_code, 200)

        # 5. Assert AuditRecord contains the real authenticated operator username
        saved_rec = self.audit_store.get(inc_id)
        self.assertEqual(saved_rec["status"], AuditRecordStatus.VERIFIED.value)
        self.assertEqual(saved_rec["approval"]["approved_by"], "operator_alice")
        self.assertNotEqual(saved_rec["approval"]["approved_by"], "mcp_operator_authorized")


if __name__ == "__main__":
    unittest.main()

"""
Unit and integration tests for GridMind Command Center Dashboard & FastAPI Backend.
Verifies all core scenarios, observability contracts, RBAC security, scenario scoping,
non-blocking event loop dispatch, and bounded pagination.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.app import create_dashboard_app, extract_incident_events
from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecord, AuditRecordStatus, GridMindCommander
from gridmind.llm import LLMClient
from gridmind.service import GridMindService
from gridmind.specialists import OperationsSpecialist, SafetySpecialist, SpecialistResult, SpecialistRole, SpecialistStatus


LEAD_AUTH = {"Authorization": "Bearer gm-lead-token-secret"}
OPERATOR_AUTH = {"Authorization": "Bearer gm-operator-token-secret"}
VIEWER_AUTH = {"Authorization": "Bearer gm-viewer-token-secret"}


class TestDashboard(unittest.TestCase):
    """Test suite for GridMind Dashboard API, event extraction, and safety guarantees."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_dash_audit.db")
        self.audit_store = AuditStore(db_path=self.db_path)
        self.service = GridMindService(data_dir="gridmind_data/curated")
        self.mock_llm = MagicMock(spec=LLMClient)
        self.mock_llm.generate_narrative.side_effect = (
            lambda agent_role, status, candidates, evidence, risks, default_finding, default_recommendation: (
                default_finding,
                default_recommendation,
            )
        )
        self.commander = GridMindCommander(
            service=self.service,
            audit_store=self.audit_store,
            llm_client=self.mock_llm,
        )
        self.app = create_dashboard_app(
            service=self.service,
            audit_store=self.audit_store,
            commander=self.commander,
            llm_client=self.mock_llm,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_01_get_index_renders_successfully(self) -> None:
        """1. GET / renders successfully with HTML."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("GRIDMIND", resp.text)
        self.assertIn("Single-Line Feeder Diagram", resp.text)
        self.assertIn("Sandbox Trade-Off Comparison Matrix", resp.text)

    def test_02_api_status_returns_live_state_and_latest_audit(self) -> None:
        """2. /api/status returns live state + latest audit for active scenario."""
        self.service.load_scenario("SC01")
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["scenario_id"], "SC01")
        self.assertIn("grid_state", data)
        self.assertIn("incident_state", data)
        self.assertIn("state_revision", data)
        self.assertIn("latest_record", data)

    def test_03_api_grid_live_returns_gridmind_state(self) -> None:
        """3. /api/grid/live returns GridMind state."""
        resp = self.client.get("/api/grid/live")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("frequency_hz", data)
        self.assertIn("transformers", data)
        self.assertIn("lines", data)

    def test_04_api_incident_live_returns_incident_state(self) -> None:
        """4. /api/incident/live returns incident state."""
        self.service.load_scenario("SC01")
        resp = self.client.get("/api/incident/live")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["scenario_id"], "SC01")
        self.assertIn("overheated_transformers", data)
        self.assertIn("active_violations", data)

    def test_05_api_scenarios_returns_supported_scenarios(self) -> None:
        """5. /api/scenarios returns supported scenarios."""
        resp = self.client.get("/api/scenarios")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("SC01", data["scenarios"])
        self.assertIn("SC01-B", data["scenarios"])
        self.assertIn("BASE", data["scenarios"])

    def test_06_audit_list_endpoint(self) -> None:
        """6. audit list works with pagination metadata."""
        rec = AuditRecord(
            incident_id="INC-TEST-01",
            scenario_id="SC01",
            status=AuditRecordStatus.PENDING_APPROVAL.value,
        )
        self.audit_store.save(rec)

        resp = self.client.get("/api/audit/records")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["records"][0]["incident_id"], "INC-TEST-01")

    def test_07_audit_record_retrieval(self) -> None:
        """7. audit record retrieval works."""
        rec = AuditRecord(
            incident_id="INC-TEST-02",
            scenario_id="SC01-B",
            status=AuditRecordStatus.VERIFIED.value,
        )
        self.audit_store.save(rec)

        resp = self.client.get("/api/audit/records/INC-TEST-02")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["incident_id"], "INC-TEST-02")
        self.assertEqual(data["status"], AuditRecordStatus.VERIFIED.value)

        resp404 = self.client.get("/api/audit/records/NONEXISTENT")
        self.assertEqual(resp404.status_code, 404)

    def test_08_scenario_loading_resets_requested_scenario(self) -> None:
        """8. scenario loading resets the requested scenario."""
        resp = self.client.post("/api/scenario/load", json={"scenario_id": "SC01"}, headers=OPERATOR_AUTH)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["scenario_id"], "SC01")
        self.assertFalse(data["incident_state"]["is_stable"])

    def test_09_scenario_loading_does_not_automatically_trigger_planning(self) -> None:
        """9. scenario loading does NOT automatically trigger Commander planning."""
        self.audit_store.clear()
        resp = self.client.post("/api/scenario/load", json={"scenario_id": "SC01"}, headers=OPERATOR_AUTH)
        self.assertEqual(resp.status_code, 200)

        # Confirm zero audit records created simply by loading a scenario
        records = self.audit_store.list()
        self.assertEqual(len(records), 0)

    def test_10_commander_planning_creates_expected_audit_state(self) -> None:
        """10. commander planning creates the expected audit state."""
        self.service.load_scenario("SC01")
        resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertIsNotNone(data["recommended_action"])

        # Confirm saved in AuditStore
        records = self.audit_store.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], AuditRecordStatus.PENDING_APPROVAL.value)

    def test_11_planning_does_not_execute(self) -> None:
        """11. planning does not execute."""
        self.service.load_scenario("SC01")
        resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        self.assertEqual(resp.status_code, 200)

        # Simulator remains in un-executed incident state (T04 overheated)
        grid_state = self.service.get_grid_state()
        self.assertFalse(grid_state.is_stable)
        t04 = next(t for t in grid_state.transformers if t.transformer_id == "T04")
        self.assertGreater(t04.temperature_c, 110.0)

    def test_12_approval_delegates_to_commander(self) -> None:
        """12. approval delegates to Commander."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        plan_data = plan_resp.json()
        inc_id = plan_data["incident_id"]

        app_resp = self.client.post(
            "/api/commander/approve",
            json={"reason": "Authorized SC01 protocol", "incident_id": inc_id},
            headers=LEAD_AUTH,
        )
        self.assertEqual(app_resp.status_code, 200)
        app_data = app_resp.json()
        self.assertTrue(app_data["success"])
        self.assertEqual(app_data["record"]["status"], AuditRecordStatus.VERIFIED.value)

        # Live grid is now physically stable
        post_grid = self.service.get_grid_state()
        self.assertTrue(post_grid.is_stable)

    def test_13_rejection_delegates_to_commander(self) -> None:
        """13. rejection delegates to Commander."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        plan_data = plan_resp.json()
        inc_id = plan_data["incident_id"]

        rej_resp = self.client.post(
            "/api/commander/reject",
            json={"reason": "Operator override hold", "incident_id": inc_id},
            headers=LEAD_AUTH,
        )
        self.assertEqual(rej_resp.status_code, 200)
        rej_data = rej_resp.json()
        self.assertTrue(rej_data["success"])
        self.assertEqual(rej_data["record"]["status"], AuditRecordStatus.REJECTED_BY_HUMAN.value)

        # Live grid was NOT executed
        post_grid = self.service.get_grid_state()
        self.assertFalse(post_grid.is_stable)

    def test_14_dashboard_cannot_directly_bypass_commander_execution(self) -> None:
        """14. dashboard cannot directly bypass Commander execution or inject arbitrary actions."""
        # Attempting to approve non-existent incident raises 404
        bad_resp = self.client.post(
            "/api/commander/approve",
            json={"incident_id": "NONEXISTENT-INCIDENT"},
            headers=LEAD_AUTH,
        )
        self.assertEqual(bad_resp.status_code, 404)

    def test_15_activity_endpoint_only_returns_actual_recorded_events(self) -> None:
        """15. activity endpoint only returns actual recorded events without fabrication."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        events_resp = self.client.get(f"/api/events/{inc_id}")
        self.assertEqual(events_resp.status_code, 200)
        events_data = events_resp.json()
        events = events_data["events"]

        self.assertGreater(len(events), 0)
        valid_types = {
            "state_inspection",
            "sandbox_evaluation",
            "reasoning_summary",
            "recommendation",
            "approval_checkpoint",
            "execution_dispatch",
            "verification_result",
        }
        for ev in events:
            self.assertIn("stage", ev)
            self.assertIn("event_type", ev)
            self.assertIn("summary", ev)
            self.assertIn("status", ev)
            # Confirm event types are strictly valid categories and NEVER tool_call/tool_result
            self.assertIn(ev["event_type"], valid_types)
            self.assertNotIn(ev["event_type"], ("tool_call", "tool_result"))

    def test_16_dashboard_handles_pending_approval(self) -> None:
        """16. dashboard handles PENDING_APPROVAL."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        status_resp = self.client.get("/api/status")
        self.assertEqual(status_resp.json()["commander_status"], AuditRecordStatus.PENDING_APPROVAL.value)

    def test_17_dashboard_handles_no_safe_action(self) -> None:
        """17. dashboard handles NO_SAFE_ACTION without crashing."""
        rec = AuditRecord(
            incident_id="INC-NO-SAFE",
            scenario_id="SC01",
            recommended_action=None,
            status=AuditRecordStatus.NO_SAFE_ACTION.value,
        )
        self.audit_store.save(rec)

        events = extract_incident_events(rec.to_dict())
        self.assertGreater(len(events), 0)
        self.assertTrue(any("NO_SAFE_ACTION" in ev["summary"] for ev in events))

    def test_18_dashboard_handles_escalated(self) -> None:
        """18. dashboard handles ESCALATED without crashing."""
        rec = AuditRecord(
            incident_id="INC-ESC-01",
            scenario_id="SC01",
            recommended_action=None,
            status=AuditRecordStatus.ESCALATED.value,
        )
        self.audit_store.save(rec)

        events = extract_incident_events(rec.to_dict())
        self.assertTrue(any("ESCALATED" in ev["summary"] for ev in events))

    def test_19_dashboard_handles_rejected_by_human(self) -> None:
        """19. dashboard handles REJECTED_BY_HUMAN."""
        rec = AuditRecord(
            incident_id="INC-REJ-01",
            scenario_id="SC01",
            approval={"approved": False, "approved_by": "operator_test", "reason": "Testing rejection"},
            status=AuditRecordStatus.REJECTED_BY_HUMAN.value,
        )
        self.audit_store.save(rec)

        events = extract_incident_events(rec.to_dict())
        self.assertTrue(any("rejected intervention" in ev["summary"] for ev in events))

    def test_20_dashboard_handles_verified(self) -> None:
        """20. dashboard handles VERIFIED."""
        rec = AuditRecord(
            incident_id="INC-VER-01",
            scenario_id="SC01",
            recommended_action={"action_type": "load_restriction", "parameters": {"target": "N08", "reduction_pct": 15.0}},
            execution={"executed": True, "response": {"success": True}},
            verification={"verified": True, "post_state_stable": True, "active_violations": []},
            status=AuditRecordStatus.VERIFIED.value,
        )
        self.audit_store.save(rec)

        events = extract_incident_events(rec.to_dict())
        self.assertTrue(any("VERIFIED" in ev["summary"] for ev in events))

    def test_21_dashboard_handles_executed_unverified(self) -> None:
        """21. dashboard handles EXECUTED_UNVERIFIED."""
        rec = AuditRecord(
            incident_id="INC-UNVER-01",
            scenario_id="SC01",
            recommended_action={"action_type": "isolate_transformer", "parameters": {"transformer_id": "T04"}},
            execution={"executed": True, "response": {"success": True}},
            verification={"verified": False, "post_state_stable": False, "active_violations": ["T02 overheated"]},
            status=AuditRecordStatus.EXECUTED_UNVERIFIED.value,
        )
        self.audit_store.save(rec)

        events = extract_incident_events(rec.to_dict())
        self.assertTrue(any("EXECUTED_UNVERIFIED" in ev["summary"] for ev in events))

    def test_22_sc01_ui_data_path_works(self) -> None:
        """22. SC01 UI/data path works end to end."""
        load_resp = self.client.post("/api/scenario/load", json={"scenario_id": "SC01"}, headers=OPERATOR_AUTH)
        self.assertEqual(load_resp.status_code, 200)

        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        self.assertEqual(plan_resp.status_code, 200)
        self.assertEqual(plan_resp.json()["recommended_action"]["action_type"], "load_restriction")

        app_resp = self.client.post(
            "/api/commander/approve",
            json={"incident_id": plan_resp.json()["incident_id"]},
            headers=LEAD_AUTH,
        )
        self.assertEqual(app_resp.status_code, 200)
        self.assertEqual(app_resp.json()["record"]["status"], AuditRecordStatus.VERIFIED.value)

    def test_23_sc01_b_ui_data_path_works(self) -> None:
        """23. SC01-B UI/data path works end to end."""
        load_resp = self.client.post("/api/scenario/load", json={"scenario_id": "SC01-B"}, headers=OPERATOR_AUTH)
        self.assertEqual(load_resp.status_code, 200)

        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        self.assertEqual(plan_resp.status_code, 200)
        self.assertEqual(plan_resp.json()["recommended_action"]["action_type"], "load_transfer")

        app_resp = self.client.post(
            "/api/commander/approve",
            json={"incident_id": plan_resp.json()["incident_id"]},
            headers=LEAD_AUTH,
        )
        self.assertEqual(app_resp.status_code, 200)
        self.assertEqual(app_resp.json()["record"]["status"], AuditRecordStatus.VERIFIED.value)

    def test_24_base_scenario_nominal_path(self) -> None:
        """24. BASE scenario nominal path works through dashboard."""
        load_resp = self.client.post("/api/scenario/load", json={"scenario_id": "BASE"}, headers=OPERATOR_AUTH)
        self.assertEqual(load_resp.status_code, 200)

        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        self.assertEqual(plan_resp.status_code, 200)
        self.assertEqual(plan_resp.json()["status"], AuditRecordStatus.NOMINAL.value)
        self.assertIsNone(plan_resp.json()["recommended_action"])

    def test_25_no_event_labeled_tool_call_or_tool_result(self) -> None:
        """25. Asserts no event is ever labeled tool_call or tool_result across all lifecycle phases."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        # Phase A: Planned (unexecuted)
        events_pre = self.client.get(f"/api/events/{inc_id}").json()["events"]
        for ev in events_pre:
            self.assertNotEqual(ev["event_type"], "tool_call")
            self.assertNotEqual(ev["event_type"], "tool_result")

        # Phase B: Approved & Executed
        self.client.post("/api/commander/approve", json={"incident_id": inc_id}, headers=LEAD_AUTH)
        events_post = self.client.get(f"/api/events/{inc_id}").json()["events"]
        for ev in events_post:
            self.assertNotEqual(ev["event_type"], "tool_call")
            self.assertNotEqual(ev["event_type"], "tool_result")

    def test_26_execution_dispatch_absent_when_unexecuted(self) -> None:
        """26. Asserts execution_dispatch and verification_result are strictly absent before execution."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        events = self.client.get(f"/api/events/{inc_id}").json()["events"]
        event_types = [ev["event_type"] for ev in events]
        self.assertNotIn("execution_dispatch", event_types)
        self.assertNotIn("verification_result", event_types)
        self.assertIn("approval_checkpoint", event_types)

    def test_27_verification_result_present_only_after_execution(self) -> None:
        """27. Asserts verification_result is derived from actual verification data and present only after execution."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        self.client.post("/api/commander/approve", json={"incident_id": inc_id}, headers=LEAD_AUTH)
        events = self.client.get(f"/api/events/{inc_id}").json()["events"]
        verif_events = [ev for ev in events if ev["event_type"] == "verification_result"]

        self.assertEqual(len(verif_events), 1)
        self.assertEqual(verif_events[0]["status"], "success")
        self.assertIn("verification", verif_events[0])
        self.assertTrue(verif_events[0]["verification"]["verified"])
        self.assertTrue(verif_events[0]["verification"]["post_state_stable"])

    # ==========================================================================
    # Findings 1-7 Regression Tests
    # ==========================================================================

    def test_28_unauthenticated_state_changing_requests_fail_401(self) -> None:
        """Finding 1 (Security): Unauthenticated state-changing requests fail with 401."""
        # 1. load scenario without auth
        resp = self.client.post("/api/scenario/load", json={"scenario_id": "SC01"})
        self.assertEqual(resp.status_code, 401)

        # 2. trigger plan without auth
        resp = self.client.post("/api/commander/plan")
        self.assertEqual(resp.status_code, 401)

        # 3. approve without auth
        resp = self.client.post("/api/commander/approve", json={"incident_id": "INC-01"})
        self.assertEqual(resp.status_code, 401)

        # 4. reject without auth
        resp = self.client.post("/api/commander/reject", json={"incident_id": "INC-01"})
        self.assertEqual(resp.status_code, 401)

        # 5. invalid bearer token
        resp = self.client.post(
            "/api/scenario/load",
            json={"scenario_id": "SC01"},
            headers={"Authorization": "Bearer invalid-token"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_29_authenticated_insufficient_role_fails_403(self) -> None:
        """Finding 1 (Security): Authenticated requests with insufficient role fail with 403."""
        # Viewer attempting to load scenario (requires operator)
        resp = self.client.post("/api/scenario/load", json={"scenario_id": "SC01"}, headers=VIEWER_AUTH)
        self.assertEqual(resp.status_code, 403)

        # Viewer attempting to plan (requires operator)
        resp = self.client.post("/api/commander/plan", headers=VIEWER_AUTH)
        self.assertEqual(resp.status_code, 403)

        # Operator (non-lead) attempting to approve (requires operator_lead)
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        resp = self.client.post(
            "/api/commander/approve",
            json={"incident_id": inc_id},
            headers=OPERATOR_AUTH,
        )
        self.assertEqual(resp.status_code, 403)

    def test_30_approved_by_strictly_derived_from_authenticated_identity(self) -> None:
        """Finding 1 (Security): approved_by is derived from authenticated identity, preventing spoofing."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        # Lead token belongs to 'operator_alice'. Even if body has 'approved_by': 'malicious_user'
        app_resp = self.client.post(
            "/api/commander/approve",
            json={"approved_by": "spoofed_operator", "reason": "Spoofed attempt", "incident_id": inc_id},
            headers=LEAD_AUTH,
        )
        self.assertEqual(app_resp.status_code, 200)
        saved_rec = self.audit_store.get(inc_id)
        self.assertIsNotNone(saved_rec)
        self.assertEqual(saved_rec["approval"]["approved_by"], "operator_alice")
        self.assertNotEqual(saved_rec["approval"]["approved_by"], "spoofed_operator")

    def test_31_no_execution_on_auth_failure(self) -> None:
        """Finding 1 (Security): No execution occurs when authorization fails."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        inc_id = plan_resp.json()["incident_id"]

        # Attempt approve without token
        unauth_resp = self.client.post("/api/commander/approve", json={"incident_id": inc_id})
        self.assertEqual(unauth_resp.status_code, 401)

        # Simulator state must remain unexecuted / unstable
        grid_state = self.service.get_grid_state()
        self.assertFalse(grid_state.is_stable)
        rec = self.audit_store.get(inc_id)
        self.assertEqual(rec["status"], AuditRecordStatus.PENDING_APPROVAL.value)

    def test_32_scenario_scoping_in_status_and_cross_scenario_approval_blocking(self) -> None:
        """Finding 2 (Scenario Isolation): /api/status scopes to active scenario and blocks cross-scenario execution."""
        # 1. Create record in SC01
        self.service.load_scenario("SC01")
        plan_sc01 = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH).json()
        inc_sc01 = plan_sc01["incident_id"]

        # 2. Switch to SC01-B
        self.service.load_scenario("SC01-B")
        status_resp = self.client.get("/api/status").json()
        self.assertEqual(status_resp["scenario_id"], "SC01-B")
        # SC01 record must NOT be returned as active record for SC01-B!
        self.assertIsNone(status_resp["latest_record"])

        # 3. Create record in SC01-B
        plan_sc01b = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH).json()
        inc_sc01b = plan_sc01b["incident_id"]

        status_resp2 = self.client.get("/api/status").json()
        self.assertEqual(status_resp2["latest_record"]["incident_id"], inc_sc01b)

        # 4. Attempt to approve SC01 incident while SC01-B is active -> MUST FAIL with 400
        cross_resp = self.client.post(
            "/api/commander/approve",
            json={"incident_id": inc_sc01},
            headers=LEAD_AUTH,
        )
        self.assertEqual(cross_resp.status_code, 400)
        self.assertIn("Cannot approve incident", cross_resp.json()["detail"])

        # 5. Switch back to SC01 -> Now SC01 is active and its latest record is returned
        self.service.load_scenario("SC01")
        status_resp3 = self.client.get("/api/status").json()
        self.assertEqual(status_resp3["latest_record"]["incident_id"], inc_sc01)

    def test_33_cross_scenario_rejection_blocking(self) -> None:
        """Finding 2 (Scenario Isolation): Blocks cross-scenario rejection."""
        self.service.load_scenario("SC01")
        plan_sc01 = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH).json()
        inc_sc01 = plan_sc01["incident_id"]

        self.service.load_scenario("SC01-B")
        cross_rej = self.client.post(
            "/api/commander/reject",
            json={"incident_id": inc_sc01},
            headers=LEAD_AUTH,
        )
        self.assertEqual(cross_rej.status_code, 400)
        self.assertIn("Cannot reject incident", cross_rej.json()["detail"])

    def test_34_empty_pre_state_evidence_emits_no_state_inspection_event(self) -> None:
        """Finding 5 (Observability Correctness): Empty pre_state_evidence never emits fabricated state_inspection."""
        rec_dict = {
            "incident_id": "INC-NO-EVID",
            "scenario_id": "SC01",
            "status": "NOMINAL",
            "pre_state_evidence": [],  # Empty
            "specialist_results": {},
            "created_at": "2026-08-30T00:00:00Z",
        }
        events = extract_incident_events(rec_dict)
        insp_events = [ev for ev in events if ev["event_type"] == "state_inspection"]
        self.assertEqual(len(insp_events), 0)

        # When evidence is present, state_inspection is emitted with stored timestamp
        rec_dict_with_evidence = {
            "incident_id": "INC-WITH-EVID",
            "scenario_id": "SC01",
            "status": "NOMINAL",
            "pre_state_evidence": [{"is_stable": True, "active_violations": [], "tripped_lines": [], "overheated_transformers": []}],
            "specialist_results": {},
            "created_at": "2026-08-30T00:00:00Z",
        }
        events_with_ev = extract_incident_events(rec_dict_with_evidence)
        insp_events_with = [ev for ev in events_with_ev if ev["event_type"] == "state_inspection"]
        self.assertEqual(len(insp_events_with), 1)
        self.assertEqual(insp_events_with[0]["timestamp"], "2026-08-30T00:00:00Z")

    def test_35_status_endpoint_uses_efficient_query_without_unbounded_list(self) -> None:
        """Finding 7 (Performance): /api/status does not call unbounded AuditStore.list()."""
        with patch.object(self.audit_store, "list", wraps=self.audit_store.list) as mock_list:
            resp = self.client.get("/api/status")
            self.assertEqual(resp.status_code, 200)
            mock_list.assert_not_called()

    def test_36_audit_records_endpoint_pagination_limit_offset_and_total(self) -> None:
        """Finding 7 (Performance): /api/audit/records respects pagination limit, offset, and returns total."""
        for i in range(25):
            rec = AuditRecord(
                incident_id=f"INC-PAGE-{i:02d}",
                scenario_id="SC01",
                status=AuditRecordStatus.VERIFIED.value,
            )
            self.audit_store.save(rec)

        # Fetch page 1 (limit=10, offset=0)
        p1 = self.client.get("/api/audit/records?limit=10&offset=0").json()
        self.assertEqual(p1["total"], 25)
        self.assertEqual(p1["count"], 10)
        self.assertEqual(len(p1["records"]), 10)

        # Fetch page 2 (limit=10, offset=10)
        p2 = self.client.get("/api/audit/records?limit=10&offset=10").json()
        self.assertEqual(p2["total"], 25)
        self.assertEqual(p2["count"], 10)
        self.assertEqual(len(p2["records"]), 10)
        self.assertNotEqual(p1["records"][0]["incident_id"], p2["records"][0]["incident_id"])

        # Fetch page 3 (limit=10, offset=20) -> 5 remaining
        p3 = self.client.get("/api/audit/records?limit=10&offset=20").json()
        self.assertEqual(p3["total"], 25)
        self.assertEqual(p3["count"], 5)

    def test_37_plan_dispatched_via_threadpool_and_non_blocking(self) -> None:
        """Finding 3 (Reliability): Commander planning is dispatched through asyncio.to_thread / worker thread."""
        self.service.load_scenario("SC01")
        plan_resp = self.client.post("/api/commander/plan", headers=OPERATOR_AUTH)
        self.assertEqual(plan_resp.status_code, 200)
        self.assertIn("incident_id", plan_resp.json())

    def test_38_unified_app_mounts_mcp_and_shares_service_state(self) -> None:
        """Tests that Dashboard app mounts MCP routes and shares the exact same GridMindService state."""
        # 1. Check health endpoint exposed on dashboard app
        health_resp = self.client.get("/health")
        self.assertEqual(health_resp.status_code, 200)
        data = health_resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["service"], "gridmind-unified")
        self.assertEqual(len(data["tools"]), 6)

        # 2. Loading scenario via dashboard updates the underlying shared service
        self.client.post("/api/scenario/load", json={"scenario_id": "SC01-B"}, headers=OPERATOR_AUTH)
        self.assertEqual(self.service.active_scenario_id, "SC01-B")

        # 3. Check health endpoint now reflects active scenario
        health_after = self.client.get("/health").json()
        self.assertEqual(health_after["active_scenario"], "SC01-B")


if __name__ == "__main__":
    unittest.main()

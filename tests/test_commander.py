"""
Unit and integration tests for GridMind Commander and Specialist orchestration layer.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Optional

from gridmind.audit_store import AuditStore
from gridmind.commander import (
    AuditRecord,
    AuditRecordStatus,
    CommanderPlanResult,
    GridMindCommander,
    rank_safe_candidates,
)
from gridmind.contract import ActionRequest, EvaluationResponse, ViolationDTO
from gridmind.models import LineStatus
from gridmind.service import GridMindService
from gridmind.specialists import (
    OperationsSpecialist,
    PlanningSpecialist,
    SafetySpecialist,
    SpecialistResult,
    SpecialistRole,
    SpecialistStatus,
)


class TestGridMindCommander(unittest.TestCase):
    """Tests the GridMind Commander orchestration pipeline and specialist contracts."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_audit.db")
        self.audit_store = AuditStore(db_path=self.db_path)
        self.service = GridMindService(data_dir="gridmind_data/curated")
        self.commander = GridMindCommander(
            service=self.service,
            audit_store=self.audit_store,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_01_operations_returns_explicit_candidates(self) -> None:
        """Tests that Operations returns explicit candidate action dicts and respects MAX_CANDIDATES."""
        self.service.load_scenario("SC01")
        inc_state = self.service.get_incident_state()
        grid_state = self.service.get_grid_state()

        op_spec = OperationsSpecialist()
        res = op_spec.analyze(inc_state, grid_state)

        self.assertEqual(res.agent, SpecialistRole.OPERATIONS.value)
        self.assertEqual(res.status, SpecialistStatus.ACCEPT.value)
        self.assertLessEqual(len(res.candidates), OperationsSpecialist.MAX_CANDIDATES)
        self.assertGreater(len(res.candidates), 0)

        # Candidates must be explicit action mappings with action_type and parameters
        for cand in res.candidates:
            self.assertIn("action_type", cand)
            self.assertIsInstance(cand["action_type"], str)
            self.assertIn("parameters", cand)
            self.assertIsInstance(cand["parameters"], dict)

        # Evidence must contain facts, distinct from candidates
        self.assertGreater(len(res.evidence), 0)

    def test_02_operations_escalate_short_circuit(self) -> None:
        """Tests that Operations returning ESCALATE short-circuits Commander before sandbox evaluation."""
        self.service.load_scenario("SC01")

        class EscalatingOperations(OperationsSpecialist):
            def analyze(self, inc_state: Any, grid_state: Any = None) -> SpecialistResult:
                return SpecialistResult(
                    agent=SpecialistRole.OPERATIONS.value,
                    status=SpecialistStatus.ESCALATE.value,
                    candidates=[],
                    finding="Unmodeled severe multi-feeder cascade detected.",
                    evidence=[{"alarm": "critical"}],
                    risks=["Immediate transmission operator escalation required."],
                    recommendation="Escalate to regional grid authority.",
                )

        commander = GridMindCommander(
            service=self.service,
            audit_store=self.audit_store,
            operations_specialist=EscalatingOperations(),
        )

        plan = commander.plan_incident_response(incident_id="INC-ESC-01")
        self.assertEqual(plan.status, AuditRecordStatus.ESCALATED.value)
        self.assertIsNone(plan.recommended_action)
        self.assertIn("operations", plan.specialist_results)
        self.assertNotIn("safety", plan.specialist_results)
        self.assertNotIn("planning", plan.specialist_results)

        # Verifies persistent record
        saved = self.audit_store.get("INC-ESC-01")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], AuditRecordStatus.ESCALATED.value)
        self.assertIsNone(saved["recommended_action"])

    def test_03_safety_escalate_short_circuit(self) -> None:
        """Tests that Safety returning ESCALATE halts Commander before Planning runs."""
        self.service.load_scenario("SC01")

        class EscalatingSafety(SafetySpecialist):
            def evaluate_candidates(self, candidates: Any, evaluations: Any) -> tuple[SpecialistResult, list[Any]]:
                return (
                    SpecialistResult(
                        agent=SpecialistRole.SAFETY.value,
                        status=SpecialistStatus.ESCALATE.value,
                        candidates=[],
                        finding="Conflicting telemetry safety limits.",
                        evidence=[],
                        risks=["Compounding failure risk."],
                        recommendation="Operator manual dispatch required.",
                    ),
                    [],
                )

        commander = GridMindCommander(
            service=self.service,
            audit_store=self.audit_store,
            safety_specialist=EscalatingSafety(),
        )

        plan = commander.plan_incident_response(incident_id="INC-ESC-02")
        self.assertEqual(plan.status, AuditRecordStatus.ESCALATED.value)
        self.assertIsNone(plan.recommended_action)
        self.assertIn("operations", plan.specialist_results)
        self.assertIn("safety", plan.specialist_results)
        self.assertNotIn("planning", plan.specialist_results)

    def test_04_all_candidates_rejected_runs_planning_and_sets_no_safe_action(self) -> None:
        """Tests that when all candidates are rejected by Safety, Planning still runs and NO_SAFE_ACTION is set."""
        self.service.load_scenario("SC01")

        class RejectAllSafety(SafetySpecialist):
            def evaluate_candidates(self, candidates: Any, evaluations: Any) -> tuple[SpecialistResult, list[Any]]:
                return (
                    SpecialistResult(
                        agent=SpecialistRole.SAFETY.value,
                        status=SpecialistStatus.REJECT.value,
                        candidates=[],
                        finding="All operational candidates violate hard physical constraints.",
                        evidence=[],
                        risks=["No safe immediate switching or curtailment possible."],
                        recommendation="Proceed with planning asset reinforcement.",
                    ),
                    [],
                )

        commander = GridMindCommander(
            service=self.service,
            audit_store=self.audit_store,
            safety_specialist=RejectAllSafety(),
        )

        plan = commander.plan_incident_response(incident_id="INC-REJ-01")
        self.assertEqual(plan.status, AuditRecordStatus.NO_SAFE_ACTION.value)
        self.assertIsNone(plan.recommended_action)
        # Planning MUST still run
        self.assertIn("planning", plan.specialist_results)
        self.assertEqual(plan.specialist_results["planning"].agent, SpecialistRole.PLANNING.value)

        # Record must be well-formed without crashing
        saved = self.audit_store.get("INC-REJ-01")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], AuditRecordStatus.NO_SAFE_ACTION.value)
        self.assertIsNone(saved["recommended_action"])

    def test_05_human_explicit_rejection_sets_rejected_status(self) -> None:
        """Tests that explicit human rejection sets REJECTED_BY_HUMAN and never executes."""
        self.service.load_scenario("SC01-B")
        plan = self.commander.plan_incident_response(incident_id="INC-HUM-REJ")
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        # Operator explicitly rejects
        record = self.commander.approve_and_execute(
            approval={
                "approved": False,
                "approved_by": "operator_bob",
                "reason": "Holding for alternate feeder maintenance window.",
            },
            plan_result=plan,
        )

        self.assertEqual(record.status, AuditRecordStatus.REJECTED_BY_HUMAN.value)
        self.assertFalse(record.execution["executed"])
        self.assertFalse(record.approval["approved"])
        self.assertEqual(record.approval["approved_by"], "operator_bob")

        # Grid state remains unmodified / in incident state
        grid_state = self.service.get_grid_state()
        self.assertFalse(grid_state.is_stable)

    def test_06_deterministic_tie_breaking(self) -> None:
        """Tests that rank_safe_candidates deterministically prefers load_transfer over load_restriction."""
        cand_xfer = {
            "action_type": "load_transfer",
            "parameters": {"line_id": "L08", "source": "N08", "destination": "N04", "transfer_mw": 0.100},
        }
        cand_restr = {
            "action_type": "load_restriction",
            "parameters": {"target": "N08", "reduction_pct": 15.0},
        }

        eval_xfer = EvaluationResponse(
            action_valid=True,
            rejection_reason=None,
            is_stable=True,
            violations=[],
            predicted_frequency_hz=60.0,
            predicted_total_demand_kw=1000.0,
            predicted_line_loadings_pct={"L08": 10.0},
            predicted_transformer_temperatures_c={"T04": 95.32, "T02": 75.56},
            critical_load_service_pct={"LZ04": 100.0},
            summary="Valid transfer",
        )
        eval_restr = EvaluationResponse(
            action_valid=True,
            rejection_reason=None,
            is_stable=True,
            violations=[],
            predicted_frequency_hz=60.0,
            predicted_total_demand_kw=900.0,
            predicted_line_loadings_pct={},
            predicted_transformer_temperatures_c={"T04": 94.75, "T02": 75.56},
            critical_load_service_pct={"LZ04": 100.0},
            summary="Valid restriction",
        )

        # Order 1: [xfer, restr] -> chooses xfer (disruption priority 1)
        res1 = rank_safe_candidates([cand_xfer, cand_restr], [eval_xfer, eval_restr])
        self.assertEqual(res1["action_type"], "load_transfer")

        # Order 2: [restr, xfer] -> still chooses xfer (pure deterministic rule)
        res2 = rank_safe_candidates([cand_restr, cand_xfer], [eval_restr, eval_xfer])
        self.assertEqual(res2["action_type"], "load_transfer")

    def test_07_planning_never_executes(self) -> None:
        """Tests that PlanningSpecialist only generates planning recommendations and never mutates grid state."""
        self.service.load_scenario("SC01")
        plan_spec = PlanningSpecialist()
        inc_state = self.service.get_incident_state()

        res = plan_spec.analyze_long_term(inc_state, [])
        self.assertEqual(res.agent, SpecialistRole.PLANNING.value)
        self.assertEqual(res.status, SpecialistStatus.ACCEPT.value)
        self.assertGreater(len(res.candidates), 0)
        self.assertEqual(res.candidates[0]["action_type"], "transformer_replacement")

        # Grid state must not be modified by planning analysis
        live_inc = self.service.get_incident_state()
        self.assertIn("T04", live_inc.overheated_transformers)

    def test_08_stops_at_approval_boundary(self) -> None:
        """Tests that Commander stops at PENDING_APPROVAL and does not execute without approval."""
        self.service.load_scenario("SC01")
        plan = self.commander.plan_incident_response(incident_id="INC-GATE-01")

        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertIsNotNone(plan.recommended_action)

        # Check live grid state: still in incident state (T04 overheated)
        grid_state = self.service.get_grid_state()
        self.assertFalse(grid_state.is_stable)
        t04 = next(t for t in grid_state.transformers if t.transformer_id == "T04")
        self.assertAlmostEqual(t04.temperature_c, 112.65, delta=0.2)

    def test_09_approved_action_executes_and_verifies(self) -> None:
        """Tests that explicit human approval executes the action and produces a VERIFIED AuditRecord."""
        self.service.load_scenario("SC01")
        plan = self.commander.plan_incident_response(incident_id="INC-EXEC-01")
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)

        # Operator approves
        record = self.commander.approve_and_execute(
            approval={"approved": True, "approved_by": "operator_alice", "reason": "Standard SC01 protocol"},
            plan_result=plan,
        )

        self.assertEqual(record.status, AuditRecordStatus.VERIFIED.value)
        self.assertTrue(record.execution["executed"])
        self.assertTrue(record.verification["verified"])
        self.assertTrue(record.verification["post_state_stable"])
        self.assertEqual(record.verification["active_violations"], [])

        # Live grid is now physically stable
        post_grid = self.service.get_grid_state()
        self.assertTrue(post_grid.is_stable)
        self.assertEqual(len(post_grid.active_violations), 0)

    def test_10_failed_or_unsafe_execution_produces_executed_unverified(self) -> None:
        """Tests that if an execution does not resolve all violations, status is EXECUTED_UNVERIFIED."""
        self.service.load_scenario("SC01")
        plan = self.commander.plan_incident_response(incident_id="INC-UNVER-01")

        # Manually alter recommended_action to isolate_transformer (which overloads remaining unit T02)
        plan.audit_record.recommended_action = {
            "action_type": "isolate_transformer",
            "parameters": {"transformer_id": "T04"},
        }

        record = self.commander.approve_and_execute(
            approval={"approved": True, "approved_by": "test_runner"},
            plan_result=plan,
        )

        self.assertEqual(record.status, AuditRecordStatus.EXECUTED_UNVERIFIED.value)
        self.assertTrue(record.execution["executed"])
        self.assertFalse(record.verification["verified"])
        self.assertFalse(record.verification["post_state_stable"])
        self.assertGreater(len(record.verification["active_violations"]), 0)

    def test_11_candidate_count_greater_than_three_rejected(self) -> None:
        """Tests that Operations proposing more than 3 candidates raises ValueError."""
        class OverflowOperations(OperationsSpecialist):
            def analyze(self, inc_state: Any, grid_state: Any = None) -> SpecialistResult:
                return SpecialistResult(
                    agent=SpecialistRole.OPERATIONS.value,
                    status=SpecialistStatus.ACCEPT.value,
                    candidates=[
                        {"action_type": "load_restriction", "parameters": {"target": "N08", "reduction_pct": 10.0}},
                        {"action_type": "load_restriction", "parameters": {"target": "N08", "reduction_pct": 15.0}},
                        {"action_type": "load_restriction", "parameters": {"target": "N08", "reduction_pct": 20.0}},
                        {"action_type": "isolate_transformer", "parameters": {"transformer_id": "T04"}},
                    ],
                )

        commander = GridMindCommander(
            service=self.service,
            audit_store=self.audit_store,
            operations_specialist=OverflowOperations(),
        )

        with self.assertRaises(ValueError):
            commander.plan_incident_response()

    def test_12_audit_persistence_sqlite(self) -> None:
        """Tests SQLite AuditStore operations: saving, querying by ID, and listing by status."""
        rec1 = AuditRecord(
            incident_id="INC-SQL-01",
            scenario_id="SC01",
            recommended_action={"action_type": "load_restriction", "parameters": {"target": "N08", "reduction_pct": 15.0}},
            status=AuditRecordStatus.PENDING_APPROVAL.value,
        )
        rec2 = AuditRecord(
            incident_id="INC-SQL-02",
            scenario_id="SC01-B",
            recommended_action={"action_type": "load_transfer", "parameters": {"line_id": "L08", "transfer_mw": 0.1}},
            status=AuditRecordStatus.VERIFIED.value,
        )

        self.audit_store.save(rec1)
        self.audit_store.save(rec2)

        fetched1 = self.audit_store.get("INC-SQL-01")
        self.assertIsNotNone(fetched1)
        self.assertEqual(fetched1["scenario_id"], "SC01")
        self.assertEqual(fetched1["status"], AuditRecordStatus.PENDING_APPROVAL.value)

        pending_list = self.audit_store.list(status=AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertEqual(len(pending_list), 1)
        self.assertEqual(pending_list[0]["incident_id"], "INC-SQL-01")

        all_list = self.audit_store.list()
        self.assertEqual(len(all_list), 2)

    def test_13_sc01_and_sc01_b_end_to_end_commander_runs(self) -> None:
        """Tests end-to-end Commander planning, human approval, execution, and verification for SC01 and SC01-B."""
        # 1. Scenario SC01: L08 tripped -> Safety rejects transfer -> selects load_restriction
        self.service.load_scenario("SC01")
        plan_sc01 = self.commander.plan_incident_response(incident_id="INC-SC01-E2E")
        self.assertEqual(plan_sc01.scenario_id, "SC01")
        self.assertEqual(plan_sc01.status, AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertEqual(plan_sc01.recommended_action["action_type"], "load_restriction")
        self.assertEqual(plan_sc01.recommended_action["parameters"]["reduction_pct"], 15.0)

        # Approve and execute SC01
        rec_sc01 = self.commander.approve_and_execute(
            approval={"approved": True, "approved_by": "operator_lead"},
            plan_result=plan_sc01,
        )
        self.assertEqual(rec_sc01.status, AuditRecordStatus.VERIFIED.value)
        self.assertTrue(self.service.get_grid_state().is_stable)

        # 2. Scenario SC01-B: L08 operational -> Safety accepts transfer -> Commander selects load_transfer
        self.service.load_scenario("SC01-B")
        plan_sc01_b = self.commander.plan_incident_response(incident_id="INC-SC01B-E2E")
        self.assertEqual(plan_sc01_b.scenario_id, "SC01-B")
        self.assertEqual(plan_sc01_b.status, AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertEqual(plan_sc01_b.recommended_action["action_type"], "load_transfer")

        # Approve and execute SC01-B
        rec_sc01_b = self.commander.approve_and_execute(
            approval={"approved": True, "approved_by": "operator_lead"},
            plan_result=plan_sc01_b,
        )
        self.assertEqual(rec_sc01_b.status, AuditRecordStatus.VERIFIED.value)
        post_grid_b = self.service.get_grid_state()
        self.assertTrue(post_grid_b.is_stable)
        l08 = next(line for line in post_grid_b.lines if line.line_id == "L08")
        self.assertEqual(l08.status, LineStatus.CLOSED.value)
        self.assertAlmostEqual(l08.flow_kw, 100.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()

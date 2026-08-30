"""
Comprehensive integration and unit tests for Scenario SC02:
Storm-induced residential demand surge on Feeder-A with T01 transformer overload,
secondary constraint safety rejections, live topology resolution, and specialist generalization.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import gridmind.specialists
from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecordStatus, GridMindCommander
from gridmind.contract import (
    ActionRequest,
    EvaluationResponse,
    GridStateResponse,
    IncidentStateResponse,
    LineDTO,
    LoadZoneDTO,
    NodeDTO,
    TransformerDTO,
    ViolationDTO,
)
from gridmind.engine import GridMindEngine
from gridmind.llm import LLMClient
from gridmind.loader import load_curated_grid
from gridmind.models import Action, ActionCategory, IncidentEvent
from gridmind.scenario import run_scenario_sc02
from gridmind.service import GridMindService
from gridmind.specialists import OperationsSpecialist, PlanningSpecialist, SafetySpecialist, SpecialistStatus


class TestScenarioSC02(unittest.TestCase):
    """Test suite for SC02 deterministic execution and multi-specialist orchestration."""

    def setUp(self) -> None:
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

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_01_sc02_full_lifecycle(self) -> None:
        """1. Full deterministic lifecycle runner for SC02."""
        report = run_scenario_sc02()
        self.assertEqual(report["scenario_id"], "SC02")

        # Baseline
        self.assertTrue(report["baseline"]["is_stable"])
        self.assertEqual(report["baseline"]["violations_count"], 0)
        self.assertEqual(report["baseline"]["critical_hospital_service_pct"], 100.0)

        # Incident state
        self.assertFalse(report["incident"]["is_stable"])
        self.assertGreater(len(report["incident"]["violations"]), 0)
        self.assertGreater(report["incident"]["t01_temp_c"], 110.0)
        self.assertEqual(report["incident"]["critical_hospital_service_pct"], 100.0)

        # Sandbox evaluations
        sb = report["sandbox_evaluations"]
        # C00: load restriction on N07
        self.assertTrue(sb["load_restriction_15pct"]["action_valid"])
        self.assertTrue(sb["load_restriction_15pct"]["is_stable"])
        self.assertLess(sb["load_restriction_15pct"]["t01_temp_c"], 110.0)

        # C01: load transfer N07 -> N05
        self.assertFalse(sb["load_transfer_l08"]["action_valid"])
        self.assertIn("T04", sb["load_transfer_l08"]["rejection_reason"])

        # C02: isolate T01
        self.assertTrue(sb["isolate_t01"]["action_valid"])
        self.assertFalse(sb["isolate_t01"]["is_stable"])
        self.assertGreater(sb["isolate_t01"]["t05_temp_c"], 110.0)

        # C03: planning replacement
        self.assertTrue(sb["replace_t01_500kva"]["action_valid"])
        self.assertTrue(sb["replace_t01_500kva"]["is_stable"])

        # Sandbox isolation
        self.assertTrue(report["sandbox_isolation_verified"])

        # Post execution
        self.assertTrue(report["post_execution"]["is_stable"])
        self.assertLess(report["post_execution"]["t01_temp_c"], 110.0)
        self.assertEqual(report["post_execution"]["critical_hospital_service_pct"], 100.0)

    def test_02_sc02_service_layer_and_bidirectional_transitions(self) -> None:
        """2. Tests SC02 loading and clean transitions between SC01, SC01-B, SC02, and BASE."""
        service = GridMindService()

        # Load SC02
        inc_sc02 = service.load_scenario("SC02")
        self.assertEqual(inc_sc02.scenario_id, "SC02")
        self.assertFalse(inc_sc02.is_stable)
        self.assertIn("T01", inc_sc02.overheated_transformers)
        self.assertNotIn("T04", inc_sc02.overheated_transformers)
        self.assertNotIn("L08", inc_sc02.tripped_lines)

        # Switch to SC01
        inc_sc01 = service.load_scenario("SC01")
        self.assertEqual(inc_sc01.scenario_id, "SC01")
        self.assertIn("T04", inc_sc01.overheated_transformers)
        self.assertIn("L08", inc_sc01.tripped_lines)

        # Switch back to SC02
        inc_sc02_again = service.load_scenario("SC02")
        self.assertEqual(inc_sc02_again.scenario_id, "SC02")
        self.assertIn("T01", inc_sc02_again.overheated_transformers)
        self.assertNotIn("L08", inc_sc02_again.tripped_lines)

        # Switch to BASE
        inc_base = service.load_scenario("BASE")
        self.assertEqual(inc_base.scenario_id, "BASE")
        self.assertTrue(inc_base.is_stable)
        self.assertEqual(len(inc_base.active_violations), 0)

    def test_03_sc02_safety_rejects_secondary_t04_overload(self) -> None:
        """3. Safety explicitly rejects load transfer to Feeder-B due to secondary T04 overload."""
        service = GridMindService()
        service.load_scenario("SC02")

        eval_resp = service.evaluate_action(
            ActionRequest(
                action_type="load_transfer",
                parameters={"line_id": "L08", "source": "N07", "destination": "N05", "transfer_mw": 0.100},
            )
        )
        self.assertFalse(eval_resp.action_valid)
        self.assertFalse(eval_resp.is_stable)
        self.assertIn("T04", eval_resp.rejection_reason)

    def test_04_sc02_commander_orchestration_and_execution(self) -> None:
        """4. Full Commander planning, approval, and execution for SC02."""
        service = GridMindService()
        service.load_scenario("SC02")
        commander = GridMindCommander(
            service=service,
            audit_store=self.audit_store,
            llm_client=self.mock_llm,
        )

        # 1. Planning cycle
        plan = commander.plan_incident_response()
        self.assertEqual(plan.status, AuditRecordStatus.PENDING_APPROVAL.value)
        self.assertIsNotNone(plan.recommended_action)
        self.assertEqual(plan.recommended_action["action_type"], "load_restriction")
        self.assertEqual(plan.recommended_action["parameters"]["target"], "N07")

        # 2. Safety results verify only safe action accepted
        safety_res = plan.specialist_results["safety"]
        self.assertEqual(safety_res.status, SpecialistStatus.ACCEPT.value)
        self.assertEqual(len(safety_res.candidates), 1)

        # 3. Approve and execute
        final_rec = commander.approve_and_execute(
            approval={"approved": True, "approved_by": "operator_lead", "reason": "SC02 test authorization"},
            incident_id=plan.incident_id,
        )
        self.assertEqual(final_rec.status, AuditRecordStatus.VERIFIED.value)
        self.assertTrue(final_rec.execution["executed"])
        self.assertTrue(final_rec.verification["verified"])
        self.assertTrue(final_rec.verification["post_state_stable"])

        # Grid is physically stable
        grid_state = service.get_grid_state()
        self.assertTrue(grid_state.is_stable)
        t01_dto = next(t for t in grid_state.transformers if t.transformer_id == "T01")
        self.assertLess(t01_dto.temperature_c, 110.0)

    def test_05_specialist_generalization_from_telemetry_without_scenario_id(self) -> None:
        """5. Regression: Proves specialists deduce targets strictly from telemetry + live GridState."""
        ops = OperationsSpecialist(llm_client=self.mock_llm)
        plan_spec = PlanningSpecialist(llm_client=self.mock_llm)
        service = GridMindService()
        live_grid = service.get_grid_state()

        # Case A: Synthetic telemetry with T01 overheated and dummy scenario ID
        synth_t01 = IncidentStateResponse(
            scenario_id="CUSTOM_SCENARIO_XYZ",
            is_stable=False,
            frequency_hz=50.0,
            ambient_temp_c=25.0,
            demand_multiplier=1.0,
            tripped_lines=[],
            overheated_transformers=["T01"],
            unserved_critical_loads=[],
            active_violations=[
                ViolationDTO(
                    violation_type="TRANSFORMER_OVERHEAT",
                    target_id="T01",
                    actual_value=115.0,
                    limit_value=110.0,
                    description="Transformer T01 temperature 115.00°C exceeds limit",
                )
            ],
        )
        res_a = ops.analyze(synth_t01, grid_state=live_grid)
        # Must target Feeder-A curtailable zone N07
        self.assertEqual(res_a.status, SpecialistStatus.ACCEPT.value)
        self.assertEqual(res_a.candidates[0]["action_type"], "load_restriction")
        self.assertEqual(res_a.candidates[0]["parameters"]["target"], "N07")
        self.assertEqual(res_a.candidates[1]["parameters"]["source"], "N07")
        self.assertEqual(res_a.candidates[1]["parameters"]["destination"], "N05")
        self.assertEqual(res_a.candidates[2]["parameters"]["transformer_id"], "T01")

        plan_a = plan_spec.analyze_long_term(synth_t01, safe_actions=res_a.candidates)
        self.assertEqual(plan_a.candidates[0]["parameters"]["transformer_id"], "T01")

        # Case B: Synthetic telemetry with T04 overheated and dummy scenario ID
        synth_t04 = IncidentStateResponse(
            scenario_id="ANOTHER_DUMMY_SCENARIO",
            is_stable=False,
            frequency_hz=50.0,
            ambient_temp_c=25.0,
            demand_multiplier=1.0,
            tripped_lines=[],
            overheated_transformers=["T04"],
            unserved_critical_loads=[],
            active_violations=[
                ViolationDTO(
                    violation_type="TRANSFORMER_OVERHEAT",
                    target_id="T04",
                    actual_value=118.0,
                    limit_value=110.0,
                    description="Transformer T04 temperature 118.00°C exceeds limit",
                )
            ],
        )
        res_b = ops.analyze(synth_t04, grid_state=live_grid)
        # Must target Feeder-B curtailable zone N08
        self.assertEqual(res_b.status, SpecialistStatus.ACCEPT.value)
        self.assertEqual(res_b.candidates[0]["action_type"], "load_restriction")
        self.assertEqual(res_b.candidates[0]["parameters"]["target"], "N08")
        self.assertEqual(res_b.candidates[1]["parameters"]["source"], "N08")
        self.assertEqual(res_b.candidates[1]["parameters"]["destination"], "N04")
        self.assertEqual(res_b.candidates[2]["parameters"]["transformer_id"], "T04")

        plan_b = plan_spec.analyze_long_term(synth_t04, safe_actions=res_b.candidates)
        self.assertEqual(plan_b.candidates[0]["parameters"]["transformer_id"], "T04")

    def test_06_multi_transformer_compound_incident_escalation(self) -> None:
        """6. Qodo Finding 1: Multi-feeder simultaneous overload (T01 + T04) returns ESCALATE without dropping telemetry."""
        ops = OperationsSpecialist(llm_client=self.mock_llm)
        plan_spec = PlanningSpecialist(llm_client=self.mock_llm)
        service = GridMindService()
        live_grid = service.get_grid_state()

        compound_inc = IncidentStateResponse(
            scenario_id="COMPOUND_SCENARIO",
            is_stable=False,
            frequency_hz=49.8,
            ambient_temp_c=32.0,
            demand_multiplier=1.15,
            tripped_lines=[],
            overheated_transformers=["T01", "T04"],
            unserved_critical_loads=[],
            active_violations=[
                ViolationDTO(violation_type="TRANSFORMER_OVERHEAT", target_id="T01", actual_value=116.0, limit_value=110.0, description="T01 overheat"),
                ViolationDTO(violation_type="TRANSFORMER_OVERHEAT", target_id="T04", actual_value=118.0, limit_value=110.0, description="T04 overheat"),
            ],
        )

        res = ops.analyze(compound_inc, grid_state=live_grid)
        # Multi-feeder compound incident must explicitly ESCALATE rather than silently picking T01
        self.assertEqual(res.status, SpecialistStatus.ESCALATE.value)
        self.assertIn("Compound multi-feeder incident", res.finding)
        self.assertIn("N04", res.finding)
        self.assertIn("N05", res.finding)

        # Planning specialist must generate work orders for BOTH affected transformers
        plan_res = plan_spec.analyze_long_term(compound_inc, safe_actions=[])
        plan_xfmrs = {c["parameters"]["transformer_id"] for c in plan_res.candidates}
        self.assertEqual(plan_xfmrs, {"T01", "T04"})

    def test_07_multi_transformer_same_feeder_co_located_handling(self) -> None:
        """7. Qodo Finding 1: Multiple transformers on same feeder (T01 + T05 on Feeder-A) are fully represented."""
        ops = OperationsSpecialist(llm_client=self.mock_llm)
        service = GridMindService()
        live_grid = service.get_grid_state()

        same_feeder_inc = IncidentStateResponse(
            scenario_id="SAME_FEEDER_MULTI_XFMR",
            is_stable=False,
            frequency_hz=49.8,
            ambient_temp_c=30.0,
            demand_multiplier=1.15,
            tripped_lines=[],
            overheated_transformers=["T01", "T05"],
            unserved_critical_loads=[],
            active_violations=[
                ViolationDTO(violation_type="TRANSFORMER_OVERHEAT", target_id="T01", actual_value=115.0, limit_value=110.0, description="T01 overheat"),
                ViolationDTO(violation_type="TRANSFORMER_OVERHEAT", target_id="T05", actual_value=112.0, limit_value=110.0, description="T05 overheat"),
            ],
        )

        res = ops.analyze(same_feeder_inc, grid_state=live_grid)
        self.assertEqual(res.status, SpecialistStatus.ACCEPT.value)
        self.assertLessEqual(len(res.candidates), OperationsSpecialist.MAX_CANDIDATES)

        # Check candidate identities and parameters
        self.assertEqual(res.candidates[0]["action_type"], "load_restriction")
        self.assertEqual(res.candidates[0]["parameters"]["target"], "N07")
        self.assertEqual(res.candidates[1]["action_type"], "load_transfer")
        self.assertEqual(res.candidates[1]["parameters"]["source"], "N07")

    def test_08_live_topology_relocation_and_tripped_tie_lines(self) -> None:
        """8. Qodo Finding 2: Relocating a transformer in live GridState routes actions according to live topology."""
        ops = OperationsSpecialist(llm_client=self.mock_llm)
        service = GridMindService()
        base_grid = service.get_grid_state()

        # Construct synthetic GridState where T01 is located at N06 (Feeder-C) feeding N09
        relocated_grid = GridStateResponse(
            is_stable=False,
            frequency_hz=50.0,
            total_generation_kw=1000.0,
            total_demand_kw=1000.0,
            ambient_temp_c=25.0,
            demand_multiplier=1.0,
            storm=False,
            nodes=base_grid.nodes,
            lines=base_grid.lines,
            transformers=[
                TransformerDTO("T01", "N06", 500.0, 100.0, 115.0, 0, 5, "normal"),
            ],
            load_zones=base_grid.load_zones,
            active_violations=[],
        )

        inc_t01 = IncidentStateResponse(
            scenario_id="RELOCATED_XFMR_INCIDENT",
            is_stable=False,
            frequency_hz=50.0,
            ambient_temp_c=25.0,
            demand_multiplier=1.0,
            tripped_lines=[],
            overheated_transformers=["T01"],
            unserved_critical_loads=[],
            active_violations=[
                ViolationDTO(violation_type="TRANSFORMER_OVERHEAT", target_id="T01", actual_value=115.0, limit_value=110.0, description="T01 overheat"),
            ],
        )

        res = ops.analyze(inc_t01, grid_state=relocated_grid)
        self.assertEqual(res.status, SpecialistStatus.ACCEPT.value)
        # Must target Feeder-C curtailable load zone N09 derived from live GridState
        self.assertEqual(res.candidates[0]["action_type"], "load_restriction")
        self.assertEqual(res.candidates[0]["parameters"]["target"], "N09")

        # Test tripped tie-line L08 prevents tie-transfer candidate from being proposed
        inc_t04_l08_tripped = IncidentStateResponse(
            scenario_id="L08_TRIPPED_SCENARIO",
            is_stable=False,
            frequency_hz=50.0,
            ambient_temp_c=34.0,
            demand_multiplier=1.15,
            tripped_lines=["L08"],
            overheated_transformers=["T04"],
            unserved_critical_loads=[],
            active_violations=[
                ViolationDTO(violation_type="TRANSFORMER_OVERHEAT", target_id="T04", actual_value=115.0, limit_value=110.0, description="T04 overheat"),
            ],
        )
        res_tripped = ops.analyze(inc_t04_l08_tripped, grid_state=base_grid)
        self.assertEqual(res_tripped.status, SpecialistStatus.ACCEPT.value)
        action_types = [c["action_type"] for c in res_tripped.candidates]
        self.assertNotIn("load_transfer", action_types)

    def test_09_unmapped_topology_escalates_without_unsafe_fallback(self) -> None:
        """9. Qodo Finding 2: Unresolvable/missing topology safely returns ESCALATE rather than defaulting to N05/N08."""
        ops = OperationsSpecialist(llm_client=self.mock_llm)

        inc_t99 = IncidentStateResponse(
            scenario_id="UNKNOWN_ASSET_INCIDENT",
            is_stable=False,
            frequency_hz=50.0,
            ambient_temp_c=25.0,
            demand_multiplier=1.0,
            tripped_lines=[],
            overheated_transformers=["T99"],
            unserved_critical_loads=[],
            active_violations=[
                ViolationDTO(violation_type="TRANSFORMER_OVERHEAT", target_id="T99", actual_value=120.0, limit_value=110.0, description="Unknown T99 overheat"),
            ],
        )

        # 1. With None grid_state -> ESCALATE
        res_none = ops.analyze(inc_t99, grid_state=None)
        self.assertEqual(res_none.status, SpecialistStatus.ESCALATE.value)
        self.assertIn("Topology resolution failed", res_none.finding)

        # 2. With grid_state that does not contain T99 -> ESCALATE
        service = GridMindService()
        base_grid = service.get_grid_state()
        res_missing = ops.analyze(inc_t99, grid_state=base_grid)
        self.assertEqual(res_missing.status, SpecialistStatus.ESCALATE.value)
        self.assertIn("T99", res_missing.finding)

    def test_10_single_authoritative_operations_specialist_class(self) -> None:
        """10. Qodo Finding 3: Static AST inspection verifies exactly ONE OperationsSpecialist class declaration exists."""
        specialists_file = Path(gridmind.specialists.__file__).resolve()
        with open(specialists_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(specialists_file))

        class_names = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual(class_names.count("OperationsSpecialist"), 1)
        self.assertEqual(class_names.count("SafetySpecialist"), 1)
        self.assertEqual(class_names.count("PlanningSpecialist"), 1)


if __name__ == "__main__":
    unittest.main()

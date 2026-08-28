"""
Unit tests for the GridMind Multi-Agent Incident Commander system:
- Grid Analyst
- Simulation Agent
- Safety Agent
- Incident Commander lifecycle, approval gate, verification, and replanning.
"""

import unittest

from gridmind.service import GridMindService
from agent.grid_analyst import GridAnalyst
from agent.incident_manager import IncidentCommander
from agent.models import IncidentState, RiskLevel
from agent.safety_agent import SafetyAgent
from agent.simulation_agent import SimulationAgent


class TestAgents(unittest.TestCase):
    """Verifies all agent operations and multi-agent workflows."""

    def setUp(self) -> None:
        self.service = GridMindService(data_dir="gridmind_data/curated")
        self.commander = IncidentCommander(self.service)

    def test_grid_analyst_on_sc01(self) -> None:
        """Tests that Grid Analyst accurately captures incident telemetry without fabrication."""
        self.service.load_scenario("SC01")
        analyst = GridAnalyst(self.service)
        analysis = analyst.analyze()

        self.assertFalse(analysis.is_stable)
        self.assertGreater(len(analysis.violations), 0)
        self.assertGreater(len(analysis.root_cause_hypotheses), 0)
        self.assertGreater(len(analysis.affected_components), 0)

        # Check specific findings
        t04_affected = any(c["id"] == "T04" and c["status"] == "overheated" for c in analysis.affected_components)
        self.assertTrue(t04_affected)

        l08_affected = any(c["id"] == "L08" and c["status"] in ("tripped", "isolated") for c in analysis.affected_components)
        self.assertTrue(l08_affected)

    def test_simulation_agent_evaluates_at_least_three_strategies(self) -> None:
        """Tests that Simulation Agent generates and evaluates at least 3 distinct strategies."""
        self.service.load_scenario("SC01")
        simulator = SimulationAgent(self.service)
        plans = simulator.generate_and_evaluate_plans()

        self.assertGreaterEqual(len(plans), 3)

        plan_types = {p.action_type for p in plans}
        self.assertIn("load_restriction", plan_types)
        self.assertIn("load_transfer", plan_types)
        self.assertIn("isolate_transformer", plan_types)

        # Confirm scores and authoritatively populated simulation metrics
        for p in plans:
            self.assertIsNotNone(p.score)
            self.assertIn("predicted_frequency_hz", p.to_dict())
            if p.is_valid:
                self.assertGreater(p.predicted_frequency_hz, 0.0)

    def test_safety_agent_veto_and_cascading_detection(self) -> None:
        """Tests that Safety Agent rejects invalid actions and detects cascading transformer overload."""
        self.service.load_scenario("SC01")
        simulator = SimulationAgent(self.service)
        safety = SafetyAgent()

        plans = simulator.generate_and_evaluate_plans()
        safety.review_all(plans)

        # 1. Load transfer over tripped L08 must be rejected
        transfer_plan = next(p for p in plans if p.action_type == "load_transfer")
        self.assertFalse(transfer_plan.safety_approved)
        self.assertEqual(transfer_plan.risk_level, RiskLevel.CRITICAL)

        # 2. Transformer isolation causing secondary T02 overload must be rejected
        isolate_plan = next(p for p in plans if p.action_type == "isolate_transformer")
        self.assertFalse(isolate_plan.safety_approved)
        self.assertTrue(isolate_plan.safety_assessment.cascading_failure_risk)

        # 3. Load restriction should be approved
        restr_plan = next(p for p in plans if p.action_type == "load_restriction" and p.is_stable)
        self.assertTrue(restr_plan.safety_approved)
        self.assertEqual(restr_plan.risk_level, RiskLevel.LOW)

    def test_commander_end_to_end_lifecycle_with_approval(self) -> None:
        """Tests the complete autonomous pipeline with approval gate, execution, and verification."""
        incident = self.commander.start_incident("SC01")
        self.assertEqual(incident.state, IncidentState.DETECTED)

        # Run investigation
        incident = self.commander.investigate()
        self.assertEqual(incident.state, IncidentState.AWAITING_APPROVAL)
        self.assertIsNotNone(incident.recommended_plan)
        self.assertTrue(incident.recommended_plan.is_recommended)
        self.assertEqual(incident.recommended_plan.action_type, "load_restriction")

        # Operator approves recommended plan
        incident = self.commander.approve_action()
        self.assertEqual(incident.state, IncidentState.RESOLVED)
        self.assertIsNotNone(incident.verification)
        self.assertTrue(incident.verification.passed)
        self.assertTrue(incident.verification.is_stable)

        # Confirm audit log completeness
        timeline_types = [e.event_type for e in incident.timeline]
        self.assertIn("INCIDENT_DETECTED", timeline_types)
        self.assertIn("ANALYSIS_COMPLETED", timeline_types)
        self.assertIn("SIMULATION_STARTED", timeline_types)
        self.assertIn("SAFETY_ASSESSMENT", timeline_types)
        self.assertIn("APPROVAL_REQUESTED", timeline_types)
        self.assertIn("HUMAN_APPROVAL_GRANTED", timeline_types)
        self.assertIn("ACTION_EXECUTED", timeline_types)
        self.assertIn("VERIFICATION_PASSED", timeline_types)
        self.assertIn("INCIDENT_RESOLVED", timeline_types)

    def test_commander_rejection_triggers_replanning(self) -> None:
        """Tests that operator rejection triggers replanning without executing action."""
        self.commander.start_incident("SC01")
        self.commander.investigate()
        self.assertEqual(self.commander.current_incident.state, IncidentState.AWAITING_APPROVAL)

        # Operator rejects
        self.commander.reject_action("Test operator override")
        self.assertEqual(self.commander.current_incident.state, IncidentState.AWAITING_APPROVAL)

        timeline_types = [e.event_type for e in self.commander.current_incident.timeline]
        self.assertIn("HUMAN_APPROVAL_REJECTED", timeline_types)
        self.assertIn("REPLANNING_INITIATED", timeline_types)


if __name__ == "__main__":
    unittest.main()

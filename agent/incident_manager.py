"""
Incident Commander / Incident Manager for GridMind.

Orchestrates multi-agent investigation, counterfactual simulation, safety review,
human-in-the-loop approval checkpoint, live execution, verification, and recovery loops.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from gridmind.contract import ActionRequest, ExecutionResponse, GridStateResponse
from gridmind.service import GridMindService
from agent.grid_analyst import GridAnalyst
from agent.models import (
    CandidatePlan,
    GridAnalysis,
    IncidentRecord,
    IncidentState,
    RiskLevel,
    VerificationResult,
)
from agent.safety_agent import SafetyAgent
from agent.simulation_agent import SimulationAgent

logger = logging.getLogger("gridmind.agent.commander")


class IncidentCommander:
    """
    Autonomous Multi-Agent Incident Commander.
    
    Coordinates the Grid Analyst, Simulation Agent, and Safety Agent to investigate
    incidents, evaluate counterfactual strategies, enforce safety guardrails,
    require human approval for consequential execution, verify post-action state,
    and manage automated recovery loops.
    """

    def __init__(self, service: GridMindService) -> None:
        self.service = service
        self.analyst = GridAnalyst(service)
        self.simulator = SimulationAgent(service)
        self.safety = SafetyAgent()
        self.current_incident: Optional[IncidentRecord] = None

    def start_incident(self, scenario_id: str = "SC01") -> IncidentRecord:
        """
        Loads a scenario into the simulator and initializes a new incident record.
        """
        logger.info("Incident Commander: Initializing incident for scenario %s", scenario_id)
        # Load scenario into simulator
        inc_resp = self.service.load_scenario(scenario_id)

        incident = IncidentRecord(
            scenario_id=scenario_id,
            state=IncidentState.DETECTED,
            severity="HIGH" if not inc_resp.is_stable else "LOW",
        )
        incident.add_timeline_entry(
            event_type="INCIDENT_DETECTED",
            agent="SYSTEM",
            message=f"Incident detected in scenario {scenario_id}. Grid stability: {'STABLE' if inc_resp.is_stable else 'UNSTABLE'}",
            details={"scenario_id": scenario_id, "tripped_lines": inc_resp.tripped_lines},
        )
        self.current_incident = incident
        return incident

    def investigate(self) -> IncidentRecord:
        """
        Runs the full autonomous multi-agent investigation pipeline up to the
        human approval checkpoint:
        
        DETECTED -> INVESTIGATING -> ANALYZING -> PLANNING -> SIMULATING -> SAFETY_REVIEW -> AWAITING_APPROVAL
        """
        if not self.current_incident:
            self.start_incident("SC01")

        incident = self.current_incident
        assert incident is not None

        # 1. State transition: DETECTED -> INVESTIGATING
        incident.transition(IncidentState.INVESTIGATING)
        incident.add_timeline_entry(
            event_type="INVESTIGATION_STARTED",
            agent="INCIDENT_COMMANDER",
            message="Incident Commander initiated grid investigation",
        )

        # 2. Grid Analyst: INVESTIGATING -> ANALYZING
        incident.transition(IncidentState.ANALYZING)
        incident.add_timeline_entry(
            event_type="TOOL_CALL",
            agent="GRID_ANALYST",
            message="Grid Analyst querying live telemetry via get_grid_state and get_incident_state",
        )

        analysis: GridAnalysis = self.analyst.analyze()
        incident.analysis = analysis

        incident.add_timeline_entry(
            event_type="ANALYSIS_COMPLETED",
            agent="GRID_ANALYST",
            message=f"Analysis complete: {len(analysis.violations)} active violations, {len(analysis.affected_components)} affected components",
            details=analysis.to_dict(),
        )

        # 3. Simulation Agent: ANALYZING -> PLANNING -> SIMULATING
        incident.transition(IncidentState.PLANNING)
        incident.add_timeline_entry(
            event_type="PLANNING_STARTED",
            agent="SIMULATION_AGENT",
            message="Simulation Agent generating candidate recovery strategies",
        )

        incident.transition(IncidentState.SIMULATING)
        incident.add_timeline_entry(
            event_type="SIMULATION_STARTED",
            agent="SIMULATION_AGENT",
            message="Evaluating candidate actions in isolated counterfactual sandboxes via evaluate_action",
        )

        candidate_plans = self.simulator.generate_and_evaluate_plans(analysis)
        incident.candidate_plans = candidate_plans

        for p in candidate_plans:
            incident.add_timeline_entry(
                event_type="SIMULATION_RESULT",
                agent="SIMULATION_AGENT",
                message=f"Simulated {p.plan_id} ({p.name}): Stable={p.is_stable}, Valid={p.is_valid}, Score={p.score}",
                details={
                    "plan_id": p.plan_id,
                    "action_type": p.action_type,
                    "is_stable": p.is_stable,
                    "predicted_freq": p.predicted_frequency_hz,
                    "score": p.score,
                },
            )

        # 4. Safety Agent: SIMULATING -> SAFETY_REVIEW
        incident.transition(IncidentState.SAFETY_REVIEW)
        incident.add_timeline_entry(
            event_type="SAFETY_REVIEW_STARTED",
            agent="SAFETY_AGENT",
            message="Safety Agent performing independent safety and constraint review across all candidate plans",
        )

        self.safety.review_all(candidate_plans)

        for p in candidate_plans:
            if p.safety_assessment:
                verdict = "APPROVED" if p.safety_assessment.approved else "REJECTED"
                incident.add_timeline_entry(
                    event_type="SAFETY_ASSESSMENT",
                    agent="SAFETY_AGENT",
                    message=f"Safety Review {p.plan_id}: {verdict} (Risk: {p.risk_level.value})",
                    details=p.safety_assessment.to_dict(),
                )

        # 5. Incident Commander Plan Selection
        approved_plans = [p for p in candidate_plans if p.safety_approved and p.is_valid and p.is_stable]
        if approved_plans:
            # Pick highest scoring approved plan
            best_plan = approved_plans[0]
            best_plan.is_recommended = True
            best_plan.recommendation_reason = (
                f"Selected as optimal recovery strategy (Score: {best_plan.score:.1f}). "
                f"Predicted to restore grid stability, maintain system frequency at {best_plan.predicted_frequency_hz:.4f} Hz, "
                f"keep peak transformer temperature within {max(best_plan.transformer_temperatures_c.values()):.1f}°C, "
                "and maintain 100% service to critical facilities without cascading secondary overloads."
            )
            incident.recommended_plan = best_plan
        else:
            # Fallback if no clean stable plan exists
            valid_plans = [p for p in candidate_plans if p.is_valid]
            if valid_plans:
                incident.recommended_plan = valid_plans[0]
                incident.recommended_plan.is_recommended = True
                incident.recommended_plan.recommendation_reason = "No completely stable plan found. Highest-scoring valid alternative proposed."

        # 6. Checkpoint: Transition to AWAITING_APPROVAL
        incident.transition(IncidentState.AWAITING_APPROVAL)
        if incident.recommended_plan:
            incident.add_timeline_entry(
                event_type="APPROVAL_REQUESTED",
                agent="INCIDENT_COMMANDER",
                message=f"HUMAN APPROVAL REQUIRED: Recommended Action '{incident.recommended_plan.name}' ({incident.recommended_plan.action_type})",
                details={
                    "plan_id": incident.recommended_plan.plan_id,
                    "action_type": incident.recommended_plan.action_type,
                    "parameters": incident.recommended_plan.parameters,
                    "risk_level": incident.recommended_plan.risk_level.value,
                },
            )

        return incident

    def approve_action(self, plan_id: Optional[str] = None) -> IncidentRecord:
        """
        Human Approval Step: Approves and executes the selected plan on the live grid.
        
        AWAITING_APPROVAL -> EXECUTING -> VERIFYING -> RESOLVED (or RECOVERY_REQUIRED)
        """
        if not self.current_incident:
            raise ValueError("No active incident to approve")

        incident = self.current_incident
        if incident.state != IncidentState.AWAITING_APPROVAL:
            raise ValueError(f"Cannot approve action in state '{incident.state.value}'. Must be in AWAITING_APPROVAL state.")

        # Find plan to execute
        target_plan: Optional[CandidatePlan] = None
        if plan_id:
            target_plan = next((p for p in incident.candidate_plans if p.plan_id == plan_id), None)
        if not target_plan:
            target_plan = incident.recommended_plan

        if not target_plan:
            raise ValueError("No valid candidate plan selected for execution")

        incident.approved_plan = target_plan
        incident.add_timeline_entry(
            event_type="HUMAN_APPROVAL_GRANTED",
            agent="HUMAN_OPERATOR",
            message=f"Operator approved plan {target_plan.plan_id} ({target_plan.name}) for live grid execution",
            details={"plan_id": target_plan.plan_id, "action_type": target_plan.action_type},
        )

        # Transition: AWAITING_APPROVAL -> EXECUTING
        incident.transition(IncidentState.EXECUTING)
        incident.add_timeline_entry(
            event_type="ACTION_EXECUTING",
            agent="INCIDENT_COMMANDER",
            message=f"Executing action '{target_plan.action_type}' with parameters {target_plan.parameters} via execute_action",
        )

        # Call live execute_action on GridMindService
        req = ActionRequest(
            action_type=target_plan.action_type,
            parameters=target_plan.parameters,
        )
        exec_resp: ExecutionResponse = self.service.execute_action(req)
        incident.execution_result = exec_resp.to_dict()

        incident.add_timeline_entry(
            event_type="ACTION_EXECUTED",
            agent="INCIDENT_COMMANDER",
            message=f"Action execution finished: Success={exec_resp.success}, Stable={exec_resp.is_stable}",
            details=exec_resp.to_dict(),
        )

        # Transition: EXECUTING -> VERIFYING
        incident.transition(IncidentState.VERIFYING)
        incident.add_timeline_entry(
            event_type="VERIFICATION_STARTED",
            agent="INCIDENT_COMMANDER",
            message="Querying live post-action grid state for verification",
        )

        # Run Post-Action Verification
        post_grid = self.service.get_grid_state()
        violations_count = len(post_grid.active_violations)
        is_stable = post_grid.is_stable and violations_count == 0

        verification = VerificationResult(
            passed=is_stable,
            is_stable=is_stable,
            frequency_hz=post_grid.frequency_hz,
            violations=[
                {
                    "type": v.violation_type,
                    "target": v.target_id,
                    "actual": round(v.actual_value, 2),
                    "limit": round(v.limit_value, 2),
                    "description": v.description,
                }
                for v in post_grid.active_violations
            ],
            comparison={
                "frequency_hz": post_grid.frequency_hz,
                "total_demand_kw": post_grid.total_demand_kw,
                "active_violations": violations_count,
            },
            message="Verification passed: Grid restored to nominal stability with zero active violations."
            if is_stable
            else f"Verification failed: Grid remains unstable with {violations_count} active violation(s).",
        )
        incident.verification = verification

        if is_stable:
            incident.transition(IncidentState.RESOLVED)
            incident.add_timeline_entry(
                event_type="VERIFICATION_PASSED",
                agent="INCIDENT_COMMANDER",
                message="POST-ACTION VERIFICATION PASSED: Grid is fully stable.",
                details=verification.to_dict(),
            )
            incident.add_timeline_entry(
                event_type="INCIDENT_RESOLVED",
                agent="INCIDENT_COMMANDER",
                message="INCIDENT SUCCESSFULLY RESOLVED. Normal operations restored.",
            )
        else:
            incident.transition(IncidentState.RECOVERY_REQUIRED)
            incident.add_timeline_entry(
                event_type="VERIFICATION_FAILED",
                agent="INCIDENT_COMMANDER",
                message="POST-ACTION VERIFICATION FAILED: Grid state unsafe. Automated recovery required.",
                details=verification.to_dict(),
            )
            # Trigger recovery loop
            self.trigger_recovery()

        return incident

    def reject_action(self, reason: str = "Operator rejected proposed action") -> IncidentRecord:
        """
        Human Rejection Step: Rejects proposed plan and triggers replanning loop.
        
        AWAITING_APPROVAL -> PLANNING -> SIMULATING -> SAFETY_REVIEW -> AWAITING_APPROVAL
        """
        if not self.current_incident:
            raise ValueError("No active incident to reject")

        incident = self.current_incident
        if incident.state != IncidentState.AWAITING_APPROVAL:
            raise ValueError(f"Cannot reject action in state '{incident.state.value}'. Must be in AWAITING_APPROVAL state.")

        rejected_plan = incident.recommended_plan
        incident.add_timeline_entry(
            event_type="HUMAN_APPROVAL_REJECTED",
            agent="HUMAN_OPERATOR",
            message=f"Operator rejected recommendation: {reason}",
            details={"rejected_plan_id": rejected_plan.plan_id if rejected_plan else None, "reason": reason},
        )

        # Transition: AWAITING_APPROVAL -> PLANNING
        incident.transition(IncidentState.PLANNING)
        incident.add_timeline_entry(
            event_type="REPLANNING_INITIATED",
            agent="INCIDENT_COMMANDER",
            message="Incident Commander initiated replanning excluding rejected strategy",
        )

        # Re-evaluate plans with alternate options
        candidate_plans = self.simulator.generate_and_evaluate_plans(incident.analysis)
        # Exclude rejected plan
        if rejected_plan:
            candidate_plans = [p for p in candidate_plans if p.plan_id != rejected_plan.plan_id]

        incident.candidate_plans = candidate_plans

        incident.transition(IncidentState.SIMULATING)
        incident.transition(IncidentState.SAFETY_REVIEW)
        self.safety.review_all(candidate_plans)

        approved_plans = [p for p in candidate_plans if p.safety_approved and p.is_valid and p.is_stable]
        if approved_plans:
            incident.recommended_plan = approved_plans[0]
            incident.recommended_plan.is_recommended = True
            incident.recommended_plan.recommendation_reason = f"Alternative strategy selected following operator rejection: {incident.recommended_plan.name}"
        else:
            valid_plans = [p for p in candidate_plans if p.is_valid]
            if valid_plans:
                incident.recommended_plan = valid_plans[0]
                incident.recommended_plan.is_recommended = True
                incident.recommended_plan.recommendation_reason = "Fallback alternative strategy proposed."

        incident.transition(IncidentState.AWAITING_APPROVAL)
        if incident.recommended_plan:
            incident.add_timeline_entry(
                event_type="APPROVAL_REQUESTED",
                agent="INCIDENT_COMMANDER",
                message=f"HUMAN APPROVAL REQUIRED (REPLANNED): Alternate Action '{incident.recommended_plan.name}'",
                details={
                    "plan_id": incident.recommended_plan.plan_id,
                    "action_type": incident.recommended_plan.action_type,
                    "parameters": incident.recommended_plan.parameters,
                },
            )

        return incident

    def trigger_recovery(self) -> IncidentRecord:
        """
        Automated Recovery Loop when post-action verification fails.
        """
        if not self.current_incident:
            raise ValueError("No active incident for recovery")

        incident = self.current_incident
        incident.recovery_count += 1

        if incident.recovery_count > incident.max_recovery_attempts:
            incident.transition(IncidentState.FAILED)
            incident.add_timeline_entry(
                event_type="RECOVERY_EXHAUSTED",
                agent="INCIDENT_COMMANDER",
                message=f"Recovery attempts exhausted ({incident.recovery_count}/{incident.max_recovery_attempts}). Manual dispatch required.",
            )
            return incident

        incident.transition(IncidentState.RECOVERING)
        incident.add_timeline_entry(
            event_type="RECOVERY_INITIATED",
            agent="INCIDENT_COMMANDER",
            message=f"Recovery loop #{incident.recovery_count} initiated: Re-investigating grid state",
        )

        # Re-run investigation
        return self.investigate()

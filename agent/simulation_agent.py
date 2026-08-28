"""
Simulation Agent for GridMind.

Generates candidate interventions and runs sandboxed counterfactual evaluations
using the deterministic simulation engine via GridMindService.evaluate_action().
Collects actual simulation metrics and computes transparent scoring.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from gridmind.contract import ActionRequest, EvaluationResponse
from gridmind.service import GridMindService
from agent.models import CandidatePlan, GridAnalysis, RiskLevel

logger = logging.getLogger("gridmind.agent.simulation")


class SimulationAgent:
    """
    Simulation / Planning Agent.
    
    Generates diverse operational and planning strategies for grid incidents,
    runs sandboxed counterfactual simulations for each, and transparently scores
    the outcomes based on stability, constraint satisfaction, and customer impact.
    """

    def __init__(self, service: GridMindService) -> None:
        self.service = service

    def generate_and_evaluate_plans(
        self, analysis: Optional[GridAnalysis] = None
    ) -> list[CandidatePlan]:
        """
        Generates candidate interventions relevant to current grid state and
        evaluates each using counterfactual sandbox simulation.
        """
        logger.info("Simulation Agent: Generating and evaluating candidate strategies")

        # Define candidate interventions
        candidates: list[dict[str, Any]] = [
            {
                "plan_id": "PLAN-A",
                "name": "Targeted Load Curtailment",
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15.0},
                "description": "Curtail commercial load at Node N08 (LZ02) by 15% to relieve thermal stress on transformer T04.",
            },
            {
                "plan_id": "PLAN-B",
                "name": "Inter-Feeder Load Transfer",
                "action_type": "load_transfer",
                "parameters": {"from": "N08", "to": "N04", "line_id": "L08", "mw": 0.100},
                "description": "Transfer 100 kW load from Feeder-B (N08) to Feeder-A (N04) via emergency tie-line L08.",
            },
            {
                "plan_id": "PLAN-C",
                "name": "Transformer Isolation",
                "action_type": "isolate_transformer",
                "parameters": {"transformer_id": "T04"},
                "description": "Isolate high-temperature transformer T04 at Node N05 to prevent hardware failure.",
            },
            {
                "plan_id": "PLAN-D",
                "name": "Capacity Uprate (Planning Order)",
                "action_type": "transformer_replacement",
                "parameters": {"transformer_id": "T04", "additional_kva": 250.0},
                "description": "Issue work order to uprate transformer T04 from 250 kVA to 500 kVA (evaluated as planning assessment).",
            },
        ]

        evaluated_plans: list[CandidatePlan] = []

        for c in candidates:
            plan = CandidatePlan(
                plan_id=c["plan_id"],
                name=c["name"],
                action_type=c["action_type"],
                parameters=c["parameters"],
                description=c["description"],
            )

            # Evaluate candidate in sandbox via GridMindService
            req = ActionRequest(
                action_type=plan.action_type,
                parameters=plan.parameters,
            )
            eval_res: EvaluationResponse = self.service.evaluate_action(req)

            # Record authoritative simulation results
            plan.is_valid = eval_res.action_valid
            plan.is_stable = eval_res.is_stable
            plan.rejection_reason = eval_res.rejection_reason
            plan.predicted_frequency_hz = eval_res.predicted_frequency_hz or 0.0
            plan.predicted_total_demand_kw = eval_res.predicted_total_demand_kw or 0.0
            plan.line_loadings_pct = dict(eval_res.predicted_line_loadings_pct or {})
            plan.transformer_temperatures_c = dict(eval_res.predicted_transformer_temperatures_c or {})
            plan.critical_load_service_pct = dict(eval_res.critical_load_service_pct or {})
            plan.summary = eval_res.summary or ""

            plan.violations = [
                {
                    "type": v.violation_type,
                    "target": v.target_id,
                    "actual": round(v.actual_value, 2),
                    "limit": round(v.limit_value, 2),
                    "description": v.description,
                }
                for v in eval_res.violations
            ]

            # Compute transparent multi-objective score
            self._score_plan(plan)
            evaluated_plans.append(plan)

        # Sort plans by score descending
        evaluated_plans.sort(key=lambda p: p.score, reverse=True)

        return evaluated_plans

    def _score_plan(self, plan: CandidatePlan) -> None:
        """
        Computes a transparent scoring model for a candidate plan.
        
        Formula:
        Score = Stability (+50) + Validity (+20 / -100) + Critical Load Protection (+20 / -100)
              + Frequency In-Bounds (+10) + Thermal Headroom (+0 to +15)
              - Curtailment Penalty (-0 to -15) - Risk Penalty (-0 to -20)
        """
        breakdown: dict[str, float] = {}

        if not plan.is_valid:
            breakdown["validity"] = -100.0
            breakdown["stability"] = -50.0
            plan.score = -150.0
            plan.score_breakdown = breakdown
            plan.risk_level = RiskLevel.CRITICAL
            return

        # 1. Action validity
        breakdown["validity"] = 20.0

        # 2. Stability
        if plan.is_stable:
            breakdown["stability"] = 50.0
        else:
            breakdown["stability"] = -40.0

        # 3. Critical load preservation
        hosp_service = plan.critical_load_service_pct.get("LZ04", 100.0)
        if hosp_service >= 99.9:
            breakdown["critical_load_preservation"] = 20.0
        else:
            breakdown["critical_load_preservation"] = -100.0

        # 4. Frequency compliance
        freq = plan.predicted_frequency_hz
        if 49.5 <= freq <= 50.5:
            breakdown["frequency_compliance"] = 10.0
        else:
            breakdown["frequency_compliance"] = -30.0

        # 5. Thermal headroom on transformers
        max_temp = max(plan.transformer_temperatures_c.values()) if plan.transformer_temperatures_c else 100.0
        if max_temp <= 110.0:
            headroom = max(0.0, 110.0 - max_temp)
            breakdown["thermal_headroom"] = round(min(15.0, headroom * 0.75), 2)
        else:
            overheat_deg = max_temp - 110.0
            breakdown["thermal_headroom"] = round(-20.0 - overheat_deg * 2.0, 2)

        # 6. Customer curtailment penalty
        if plan.action_type == "load_restriction":
            reduc = float(plan.parameters.get("reduction_pct", 0.0))
            breakdown["curtailment_penalty"] = round(- (reduc * 0.4), 2)
        else:
            breakdown["curtailment_penalty"] = 0.0

        # 7. Operational immediacy vs planning delay
        if plan.action_type == "transformer_replacement":
            # Planning action: effective long term, but requires dispatch
            breakdown["operational_readiness"] = -10.0
            plan.risk_level = RiskLevel.LOW
        elif plan.action_type == "isolate_transformer":
            if not plan.is_stable:
                breakdown["operational_readiness"] = -25.0
                plan.risk_level = RiskLevel.HIGH
            else:
                breakdown["operational_readiness"] = 5.0
                plan.risk_level = RiskLevel.MEDIUM
        elif plan.action_type == "load_restriction":
            breakdown["operational_readiness"] = 10.0
            plan.risk_level = RiskLevel.LOW if plan.is_stable else RiskLevel.MEDIUM
        elif plan.action_type == "load_transfer":
            breakdown["operational_readiness"] = 5.0
            plan.risk_level = RiskLevel.MEDIUM

        total_score = sum(breakdown.values())
        plan.score = round(total_score, 2)
        plan.score_breakdown = breakdown

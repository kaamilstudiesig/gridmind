"""
Safety Agent for GridMind.

Performs independent deterministic safety reviews of proposed recovery actions,
checking critical loads, thermal headroom, frequency boundaries, line capacity limits,
and cascading failure potential.
"""

from __future__ import annotations

import logging
from typing import Optional

from agent.models import CandidatePlan, RiskLevel, SafetyAssessment

logger = logging.getLogger("gridmind.agent.safety")


class SafetyAgent:
    """
    Safety Review Agent.
    
    Acts as an independent safety checkpoint before any action can be submitted
    for human approval. Has full veto authority over unsafe or destabilizing plans.
    """

    def review_plan(self, plan: CandidatePlan) -> SafetyAssessment:
        """
        Conducts an independent safety review on a candidate plan.
        """
        logger.info("Safety Agent: Reviewing plan %s (%s)", plan.plan_id, plan.action_type)

        reasons: list[str] = []
        violations_found: list[str] = []
        mitigations: list[str] = []
        critical_loads_affected: int = 0
        cascading_risk: bool = False
        approved: bool = True
        risk_level: RiskLevel = RiskLevel.LOW

        # 1. Check validity
        if not plan.is_valid:
            approved = False
            risk_level = RiskLevel.CRITICAL
            reasons.append(f"Action is physically or operationally invalid: {plan.rejection_reason or 'Rejected by validation'}")
            violations_found.append(f"INVALID_ACTION: {plan.rejection_reason or 'Invalid parameters'}")
            mitigations.append("Do not execute. Check equipment status and tie-line lockout status.")
            
            assessment = SafetyAssessment(
                approved=approved,
                risk_level=risk_level,
                reasons=reasons,
                violations=violations_found,
                mitigations=mitigations,
                critical_loads_affected=0,
                cascading_failure_risk=False,
            )
            plan.safety_approved = approved
            plan.safety_assessment = assessment
            plan.risk_level = risk_level
            return assessment

        # 2. Check Critical Loads
        hosp_service = plan.critical_load_service_pct.get("LZ04", 100.0)
        if hosp_service < 99.9:
            approved = False
            risk_level = RiskLevel.CRITICAL
            critical_loads_affected += 1
            reasons.append(
                f"Critical facility Hospital-A (LZ04 at N10) service reduced to {hosp_service:.1f}% (100% required)"
            )
            violations_found.append("CRITICAL_LOAD_UNSERVED: LZ04 Hospital-A below minimum service threshold")
            mitigations.append("Ensure critical feeder N05/L07 maintains uninterrupted supply.")

        # 3. Check Frequency Stability
        freq = plan.predicted_frequency_hz
        if freq < 49.5 or freq > 50.5:
            approved = False
            risk_level = RiskLevel.CRITICAL
            reasons.append(f"Predicted frequency {freq:.4f} Hz violates grid operating limits [49.50, 50.50] Hz")
            violations_found.append(f"FREQUENCY_OUT_OF_BOUNDS: {freq:.4f} Hz")
            mitigations.append("Balance generation and demand before executing operational changes.")

        # 4. Check Transformer Overheating & Cascading Overloads
        for t_id, temp_c in plan.transformer_temperatures_c.items():
            if temp_c > 110.0:
                approved = False
                reasons.append(
                    f"Transformer {t_id} predicted temperature {temp_c:.1f}°C exceeds emergency thermal ceiling (110.0°C)"
                )
                violations_found.append(f"TRANSFORMER_OVERHEAT: {t_id} at {temp_c:.1f}°C")
                
                # Check if this is a cascading overload from another action
                if plan.action_type == "isolate_transformer" and plan.parameters.get("transformer_id") == "T04" and t_id == "T02":
                    cascading_risk = True
                    risk_level = RiskLevel.CRITICAL
                    reasons.append(
                        "Cascading failure detected: Isolating T04 shifts full Feeder-B demand onto T02, causing T02 to overheat to "
                        f"{temp_c:.1f}°C."
                    )
                    mitigations.append("Do not isolate T04 without prior demand reduction on Feeder-B.")

        # 5. Check Line Loading Limits
        for l_id, load_pct in plan.line_loadings_pct.items():
            if load_pct > 100.0:
                approved = False
                reasons.append(f"Line {l_id} loading {load_pct:.1f}% exceeds rated capacity (100%)")
                violations_found.append(f"LINE_OVERLOAD: {l_id} at {load_pct:.1f}%")
                mitigations.append("Reduce power flow through line or reroute via alternative healthy paths.")

        # 6. Assess Overall Plan Risk & Approvals
        if not plan.is_stable:
            approved = False
            if risk_level != RiskLevel.CRITICAL:
                risk_level = RiskLevel.HIGH

        if approved:
            risk_level = RiskLevel.LOW
            reasons.append("All network operating constraints satisfied.")
            reasons.append(f"Predicted grid frequency stable at {plan.predicted_frequency_hz:.4f} Hz.")
            reasons.append("Critical facilities (Hospital-A) remain 100% supplied.")
            max_t = max(plan.transformer_temperatures_c.values()) if plan.transformer_temperatures_c else 0.0
            reasons.append(f"Peak transformer temperature contained to {max_t:.1f}°C (within 110°C limit).")
            mitigations.append("Monitor Feeder-B telemetry closely following execution.")

        assessment = SafetyAssessment(
            approved=approved,
            risk_level=risk_level,
            reasons=reasons,
            violations=violations_found,
            mitigations=mitigations,
            critical_loads_affected=critical_loads_affected,
            cascading_failure_risk=cascading_risk,
        )

        plan.safety_approved = approved
        plan.safety_assessment = assessment
        plan.risk_level = risk_level
        return assessment

    def review_all(self, plans: list[CandidatePlan]) -> list[CandidatePlan]:
        """Reviews all plans in-place and sets safety assessments."""
        for p in plans:
            self.review_plan(p)
        return plans

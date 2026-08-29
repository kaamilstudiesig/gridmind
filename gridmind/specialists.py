"""
GridMind Specialist Roles: Operations, Safety, and Planning.
Conforms strictly to the Common Specialist Result contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from gridmind.contract import EvaluationResponse, GridStateResponse, IncidentStateResponse


class SpecialistRole(str, Enum):
    OPERATIONS = "operations"
    SAFETY = "safety"
    PLANNING = "planning"


class SpecialistStatus(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


@dataclass
class SpecialistResult:
    """
    Common Specialist Result contract for GridMind specialist agents.
    'candidates' contains candidate action inputs (action_type, parameters).
    'evidence' contains returned telemetry facts/results.
    """
    agent: str
    status: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    finding: str = ""
    evidence: list[Any] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperationsSpecialist:
    """
    Operations Specialist:
    - Inspects active incident state and live grid telemetry.
    - Proposes at most 3 plausible candidate actions mapping directly to MCP actions.
    - Does NOT execute actions.
    - Enforces MAX_CANDIDATES = 3.
    """
    MAX_CANDIDATES: int = 3

    def analyze(
        self,
        incident_state: IncidentStateResponse,
        grid_state: Optional[GridStateResponse] = None,
    ) -> SpecialistResult:
        evidence: list[Any] = []
        evidence.append({
            "scenario_id": incident_state.scenario_id,
            "is_stable": incident_state.is_stable,
            "frequency_hz": incident_state.frequency_hz,
            "ambient_temp_c": incident_state.ambient_temp_c,
            "tripped_lines": list(incident_state.tripped_lines),
            "overheated_transformers": list(incident_state.overheated_transformers),
            "active_violations": [v.description for v in incident_state.active_violations],
        })

        if incident_state.is_stable and not incident_state.active_violations:
            return SpecialistResult(
                agent=SpecialistRole.OPERATIONS.value,
                status=SpecialistStatus.ACCEPT.value,
                candidates=[],
                finding="Grid operating in nominal stable state. Zero violations detected.",
                evidence=evidence,
                risks=[],
                recommendation="Continue normal baseline monitoring.",
            )

        candidates: list[dict[str, Any]] = []
        risks: list[str] = []

        # Identify candidate actions based on active incident telemetry
        if "T04" in incident_state.overheated_transformers or any(
            "T04" in v.description for v in incident_state.active_violations
        ):
            # Candidate 1: Immediate demand curtailment on commercial node N08
            candidates.append({
                "action_type": "load_restriction",
                "parameters": {"target": "N08", "reduction_pct": 15.0},
            })

            # Candidate 2: Power rerouting across emergency tie-line L08 to Feeder-A
            candidates.append({
                "action_type": "load_transfer",
                "parameters": {
                    "line_id": "L08",
                    "source": "N08",
                    "destination": "N04",
                    "transfer_mw": 0.100,
                },
            })

            # Candidate 3: Isolation of overheated unit T04
            candidates.append({
                "action_type": "isolate_transformer",
                "parameters": {"transformer_id": "T04"},
            })

            risks.append("T04 thermal runaway risk exceeding 110.0°C maximum limit under peak heatwave.")
            if "L08" in incident_state.tripped_lines:
                risks.append("Tie-line L08 is locked out / tripped in incident telemetry; load transfer may be unavailable.")

        # Strict rate-limit guardrail
        if len(candidates) > self.MAX_CANDIDATES:
            raise ValueError(
                f"Operations proposed {len(candidates)} candidates, exceeding MAX_CANDIDATES={self.MAX_CANDIDATES}"
            )

        return SpecialistResult(
            agent=SpecialistRole.OPERATIONS.value,
            status=SpecialistStatus.ACCEPT.value,
            candidates=candidates[: self.MAX_CANDIDATES],
            finding=f"Identified {len(candidates)} operational candidates to relieve transformer overheating.",
            evidence=evidence,
            risks=risks,
            recommendation=(
                "Evaluate operational candidate actions through MCP sandbox isolation before human approval."
            ),
        )


class SafetySpecialist:
    """
    Safety Specialist:
    - Evaluates sandbox simulation results for candidate actions.
    - Classifies candidates as ACCEPT, REJECT, or ESCALATE.
    - Enforces hard constraints: critical-load preservation (100%), line loading <= 100%, T <= 110.0°C.
    - Does NOT execute actions.
    """

    def evaluate_candidates(
        self,
        candidates: list[dict[str, Any]],
        evaluations: list[EvaluationResponse],
    ) -> tuple[SpecialistResult, list[dict[str, Any]]]:
        safe_candidates: list[dict[str, Any]] = []
        evidence: list[Any] = []
        risks: list[str] = []

        for candidate, eval_res in zip(candidates, evaluations):
            act_type = candidate.get("action_type", "unknown")
            eval_dict = eval_res.to_dict() if hasattr(eval_res, "to_dict") else dict(eval_res)
            evidence.append({
                "action": candidate,
                "action_valid": eval_res.action_valid,
                "is_stable": eval_res.is_stable,
                "rejection_reason": eval_res.rejection_reason,
                "violations": [v.description for v in eval_res.violations],
                "predicted_temp_t04": eval_res.predicted_transformer_temperatures_c.get("T04"),
                "predicted_temp_t02": eval_res.predicted_transformer_temperatures_c.get("T02"),
                "critical_load_service": eval_res.critical_load_service_pct,
            })

            # Check 1: Simulator rejected action validity (e.g. tripped tie line)
            if not eval_res.action_valid:
                risks.append(
                    f"Candidate '{act_type}' is invalid: {eval_res.rejection_reason or 'Validation failed'}"
                )
                continue

            # Check 2: Critical hospital load preservation invariant
            hosp_service = eval_res.critical_load_service_pct.get("LZ04", 100.0)
            if hosp_service < 100.0:
                risks.append(
                    f"Hard Reject: Candidate '{act_type}' curtails critical hospital load LZ04 to {hosp_service:.1f}% (< 100%)."
                )
                continue

            # Check 3: Grid stability and thermal/line violations
            if not eval_res.is_stable or len(eval_res.violations) > 0:
                viol_descs = ", ".join(v.description for v in eval_res.violations)
                risks.append(
                    f"Reject: Candidate '{act_type}' leaves grid unstable with active violations: {viol_descs}"
                )
                continue

            # Safe candidate
            safe_candidates.append(candidate)

        if not safe_candidates:
            status = SpecialistStatus.REJECT.value
            finding = "All candidate actions were rejected by safety constraints."
            recommendation = "No safe immediate action available. Escalating to planning for long-term remediation."
        else:
            status = SpecialistStatus.ACCEPT.value
            finding = f"Verified {len(safe_candidates)} candidate action(s) satisfy all hard safety constraints."
            recommendation = f"Approve one verified safe candidate: {[c['action_type'] for c in safe_candidates]}."

        res = SpecialistResult(
            agent=SpecialistRole.SAFETY.value,
            status=status,
            candidates=safe_candidates,
            finding=finding,
            evidence=evidence,
            risks=risks,
            recommendation=recommendation,
        )
        return res, safe_candidates


class PlanningSpecialist:
    """
    Planning Specialist:
    - Identifies longer-term asset remediation and reinforcement work orders.
    - Does NOT override immediate safety and does NOT execute actions.
    """

    def analyze_long_term(
        self,
        incident_state: IncidentStateResponse,
        safe_actions: list[dict[str, Any]],
    ) -> SpecialistResult:
        evidence: list[Any] = [{
            "scenario_id": incident_state.scenario_id,
            "overheated_transformers": list(incident_state.overheated_transformers),
            "safe_operational_actions": [a.get("action_type") for a in safe_actions],
        }]

        planning_candidates: list[dict[str, Any]] = []
        if "T04" in incident_state.overheated_transformers or incident_state.scenario_id in ("SC01", "SC01-B"):
            planning_candidates.append({
                "action_type": "transformer_replacement",
                "parameters": {"transformer_id": "T04", "additional_kva": 250.0},
            })

        finding = (
            "Recommended long-term planning work order: uprate/replace T04 (+250 kVA) to provide 500 kVA capacity."
        )
        risks = [
            "Planning work orders require capital equipment procurement and crew scheduling; they do not clear real-time thermal overloads immediately."
        ]
        recommendation = (
            "Queue planning work order for T04 uprate after resolving immediate operational constraints."
        )

        return SpecialistResult(
            agent=SpecialistRole.PLANNING.value,
            status=SpecialistStatus.ACCEPT.value,
            candidates=planning_candidates,
            finding=finding,
            evidence=evidence,
            risks=risks,
            recommendation=recommendation,
        )

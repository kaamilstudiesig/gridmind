"""
GridMind Commander: Central multi-specialist orchestration layer with
human approval gating, deterministic tie-breaking, and durable AuditRecord persistence.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid

from gridmind.audit_store import AuditStore
from gridmind.contract import ActionRequest, EvaluationResponse, GridStateResponse, IncidentStateResponse
from gridmind.service import GridMindService
from gridmind.specialists import (
    OperationsSpecialist,
    PlanningSpecialist,
    SafetySpecialist,
    SpecialistResult,
    SpecialistStatus,
)

logger = logging.getLogger("gridmind.commander")


class AuditRecordStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    VERIFIED = "VERIFIED"
    EXECUTED_UNVERIFIED = "EXECUTED_UNVERIFIED"
    ESCALATED = "ESCALATED"
    NO_SAFE_ACTION = "NO_SAFE_ACTION"
    REJECTED_BY_HUMAN = "REJECTED_BY_HUMAN"


@dataclass
class AuditRecord:
    """
    Frozen AuditRecord schema consumed directly by the GridMind Dashboard and operator UI.
    """
    incident_id: str
    scenario_id: str
    recommended_action: Optional[dict[str, Any]] = None
    approval: dict[str, Any] = field(
        default_factory=lambda: {
            "approved": False,
            "approved_by": None,
            "reason": None,
            "timestamp": None,
        }
    )
    pre_state_evidence: list[Any] = field(default_factory=list)
    specialist_results: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(
        default_factory=lambda: {
            "executed": False,
            "response": None,
        }
    )
    verification: dict[str, Any] = field(
        default_factory=lambda: {
            "verified": False,
            "post_state_stable": False,
            "active_violations": [],
        }
    )
    status: str = AuditRecordStatus.PENDING_APPROVAL.value
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CommanderPlanResult:
    """Encapsulates the output of a commander planning cycle before execution."""
    incident_id: str
    scenario_id: str
    status: str
    recommended_action: Optional[dict[str, Any]]
    specialist_results: dict[str, SpecialistResult]
    audit_record: AuditRecord

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "recommended_action": self.recommended_action,
            "specialist_results": {k: v.to_dict() for k, v in self.specialist_results.items()},
            "audit_record": self.audit_record.to_dict(),
        }


def rank_safe_candidates(
    safe_candidates: list[dict[str, Any]],
    evaluations: list[EvaluationResponse],
) -> Optional[dict[str, Any]]:
    """
    Pure, deterministic tie-breaking function for selecting the primary operational intervention.
    
    Ranking Criteria:
    1. Disruption Minimization Priority:
       - Priority 1: load_transfer / close_tie_line (100% customer power maintained via network rerouting)
       - Priority 2: load_restriction (controlled demand curtailment)
       - Priority 3: isolate_transformer (dropping downstream branch service)
    2. Lowest Max Transformer Temperature:
       - Selects candidate achieving the coolest maximum transformer operating temperature.
    3. Operations Candidate Order:
       - Preserves initial candidate ranking if thermal margins are identical.
    """
    if not safe_candidates:
        return None

    type_priority = {
        "load_transfer": 1,
        "close_tie_line": 1,
        "load_restriction": 2,
        "isolate_transformer": 3,
    }

    eval_by_type = {
        c.get("action_type"): ev for c, ev in zip(safe_candidates, evaluations) if c.get("action_type")
    }

    def candidate_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        idx, cand = item
        atype = cand.get("action_type", "")
        prio = type_priority.get(atype, 99)

        ev = eval_by_type.get(atype)
        max_temp = 999.0
        if ev and ev.predicted_transformer_temperatures_c:
            max_temp = max(ev.predicted_transformer_temperatures_c.values())

        return (prio, max_temp, idx)

    sorted_candidates = sorted(enumerate(safe_candidates), key=candidate_sort_key)
    return sorted_candidates[0][1]


class GridMindCommander:
    """
    Top-level incident response commander.
    Orchestrates Operations, Safety, and Planning specialist roles.
    Enforces the PENDING_APPROVAL boundary and logs all decisions to durable AuditStore.
    """

    def __init__(
        self,
        service: Optional[GridMindService] = None,
        audit_store: Optional[AuditStore] = None,
        operations_specialist: Optional[OperationsSpecialist] = None,
        safety_specialist: Optional[SafetySpecialist] = None,
        planning_specialist: Optional[PlanningSpecialist] = None,
    ) -> None:
        self.service = service or GridMindService()
        self.audit_store = audit_store or AuditStore()
        self.operations = operations_specialist or OperationsSpecialist()
        self.safety = safety_specialist or SafetySpecialist()
        self.planning = planning_specialist or PlanningSpecialist()

    def plan_incident_response(
        self,
        incident_id: Optional[str] = None,
    ) -> CommanderPlanResult:
        """
        Executes Steps 1-8 of the Commander incident evaluation lifecycle:
        1. Inspects active incident state and live grid telemetry.
        2. Invokes Operations specialist.
        3. Evaluates Operations candidates via MCP sandbox isolation (evaluate_action).
        4. Invokes Safety specialist to verify constraint satisfaction.
        5. Handles ESCALATE and NO_SAFE_ACTION conditions.
        6. Invokes Planning specialist for long-term advice.
        7. Synthesizes a deterministic recommended action.
        8. Creates AuditRecord in PENDING_APPROVAL status and STOPS.
        """
        inc_id = incident_id or f"INC-{uuid.uuid4().hex[:8].upper()}"
        inc_state = self.service.get_incident_state()
        grid_state = self.service.get_grid_state()

        pre_evidence: list[Any] = [{
            "scenario_id": inc_state.scenario_id,
            "is_stable": inc_state.is_stable,
            "frequency_hz": inc_state.frequency_hz,
            "ambient_temp_c": inc_state.ambient_temp_c,
            "demand_multiplier": inc_state.demand_multiplier,
            "tripped_lines": list(inc_state.tripped_lines),
            "overheated_transformers": list(inc_state.overheated_transformers),
            "active_violations": [v.description for v in inc_state.active_violations],
        }]

        # Step 2: Operations Specialist
        op_result = self.operations.analyze(inc_state, grid_state)
        specialist_results: dict[str, SpecialistResult] = {"operations": op_result}

        # Short-circuit on Operations ESCALATE
        if op_result.status == SpecialistStatus.ESCALATE.value:
            record = AuditRecord(
                incident_id=inc_id,
                scenario_id=inc_state.scenario_id,
                recommended_action=None,
                pre_state_evidence=pre_evidence,
                specialist_results={"operations": op_result.to_dict()},
                status=AuditRecordStatus.ESCALATED.value,
            )
            self.audit_store.save(record)
            return CommanderPlanResult(
                incident_id=inc_id,
                scenario_id=inc_state.scenario_id,
                status=AuditRecordStatus.ESCALATED.value,
                recommended_action=None,
                specialist_results=specialist_results,
                audit_record=record,
            )

        candidates = op_result.candidates
        if len(candidates) > OperationsSpecialist.MAX_CANDIDATES:
            raise ValueError(
                f"Candidate count {len(candidates)} exceeds MAX_CANDIDATES={OperationsSpecialist.MAX_CANDIDATES}"
            )

        # Step 3: Evaluate Candidates in Sandbox
        evaluations: list[EvaluationResponse] = []
        for cand in candidates:
            req = ActionRequest(
                action_type=cand["action_type"],
                parameters=cand.get("parameters", {}),
            )
            eval_res = self.service.evaluate_action(req)
            evaluations.append(eval_res)

        # Step 4: Safety Specialist
        safety_result, safe_candidates = self.safety.evaluate_candidates(candidates, evaluations)
        specialist_results["safety"] = safety_result

        # Short-circuit on Safety ESCALATE
        if safety_result.status == SpecialistStatus.ESCALATE.value:
            record = AuditRecord(
                incident_id=inc_id,
                scenario_id=inc_state.scenario_id,
                recommended_action=None,
                pre_state_evidence=pre_evidence,
                specialist_results={k: v.to_dict() for k, v in specialist_results.items()},
                status=AuditRecordStatus.ESCALATED.value,
            )
            self.audit_store.save(record)
            return CommanderPlanResult(
                incident_id=inc_id,
                scenario_id=inc_state.scenario_id,
                status=AuditRecordStatus.ESCALATED.value,
                recommended_action=None,
                specialist_results=specialist_results,
                audit_record=record,
            )

        # Step 6: Planning Specialist (Runs even if zero safe operational actions)
        planning_result = self.planning.analyze_long_term(inc_state, safe_candidates)
        specialist_results["planning"] = planning_result

        # Step 5 & 7: Check Safe Candidate Count & Synthesize Recommendation
        if not safe_candidates:
            # NO_SAFE_ACTION: recommended_action is explicitly None
            record = AuditRecord(
                incident_id=inc_id,
                scenario_id=inc_state.scenario_id,
                recommended_action=None,
                pre_state_evidence=pre_evidence,
                specialist_results={k: v.to_dict() for k, v in specialist_results.items()},
                status=AuditRecordStatus.NO_SAFE_ACTION.value,
            )
            self.audit_store.save(record)
            return CommanderPlanResult(
                incident_id=inc_id,
                scenario_id=inc_state.scenario_id,
                status=AuditRecordStatus.NO_SAFE_ACTION.value,
                recommended_action=None,
                specialist_results=specialist_results,
                audit_record=record,
            )

        # Apply deterministic tie-breaking rule
        recommended_action = rank_safe_candidates(safe_candidates, evaluations)

        # Step 8: STOP at Human Approval Gate (PENDING_APPROVAL)
        record = AuditRecord(
            incident_id=inc_id,
            scenario_id=inc_state.scenario_id,
            recommended_action=recommended_action,
            pre_state_evidence=pre_evidence,
            specialist_results={k: v.to_dict() for k, v in specialist_results.items()},
            status=AuditRecordStatus.PENDING_APPROVAL.value,
        )
        self.audit_store.save(record)

        return CommanderPlanResult(
            incident_id=inc_id,
            scenario_id=inc_state.scenario_id,
            status=AuditRecordStatus.PENDING_APPROVAL.value,
            recommended_action=recommended_action,
            specialist_results=specialist_results,
            audit_record=record,
        )

    def approve_and_execute(
        self,
        approval: dict[str, Any],
        plan_result: Optional[CommanderPlanResult] = None,
        incident_id: Optional[str] = None,
    ) -> AuditRecord:
        """
        Processes human approval or rejection:
        - If approval['approved'] is False: records rejection, sets REJECTED_BY_HUMAN, and does NOT execute.
        - If approval['approved'] is True: dispatches execute_action, obtains post-action grid state,
          and marks VERIFIED (if safe) or EXECUTED_UNVERIFIED.
        """
        # Resolve target AuditRecord
        if plan_result:
            record = plan_result.audit_record
        elif incident_id:
            raw_rec = self.audit_store.get(incident_id)
            if not raw_rec:
                raise ValueError(f"No AuditRecord found for incident_id '{incident_id}'")
            record = AuditRecord(**raw_rec)
        else:
            raise ValueError("Must provide either plan_result or incident_id to approve_and_execute")

        now_iso = datetime.now(timezone.utc).isoformat()
        approval_payload = {
            "approved": bool(approval.get("approved", False)),
            "approved_by": approval.get("approved_by"),
            "reason": approval.get("reason"),
            "timestamp": approval.get("timestamp", now_iso),
        }
        record.approval = approval_payload

        # Case A: Explicit Human Rejection
        if not approval_payload["approved"]:
            record.status = AuditRecordStatus.REJECTED_BY_HUMAN.value
            record.execution = {"executed": False, "response": None, "reason": "Operator rejected action."}
            record.verification = {"verified": False, "post_state_stable": False, "active_violations": []}
            self.audit_store.save(record)
            return record

        # Case B: Approved Execution
        rec_action = record.recommended_action
        if not rec_action:
            raise ValueError(
                f"Cannot execute incident '{record.incident_id}': No recommended_action present in record."
            )

        # 1. Dispatch execution via Service
        req = ActionRequest(
            action_type=rec_action["action_type"],
            parameters=rec_action.get("parameters", {}),
        )
        exec_resp = self.service.execute_action(req)
        exec_dict = exec_resp.to_dict()

        # 2. Immediately inspect post-action live grid state
        post_grid = self.service.get_grid_state()
        post_violations = [v.description for v in post_grid.active_violations]

        # 3. Verification Criteria:
        # - Execution succeeded
        # - Grid is physically stable
        # - Zero hard constraint violations
        is_verified = bool(exec_resp.success and post_grid.is_stable and len(post_violations) == 0)

        record.execution = {
            "executed": True,
            "response": exec_dict,
        }
        record.verification = {
            "verified": is_verified,
            "post_state_stable": post_grid.is_stable,
            "post_frequency_hz": post_grid.frequency_hz,
            "active_violations": post_violations,
        }
        record.status = (
            AuditRecordStatus.VERIFIED.value if is_verified else AuditRecordStatus.EXECUTED_UNVERIFIED.value
        )

        self.audit_store.save(record)
        return record

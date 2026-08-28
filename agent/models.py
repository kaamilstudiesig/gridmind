"""
Agent domain models for GridMind incident command system.

Defines the incident lifecycle state machine, audit log entries,
plan comparison models, safety assessments, and structured agent outputs.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# =====================================================================
# Incident Lifecycle State Machine
# =====================================================================

class IncidentState(str, Enum):
    """Explicit incident lifecycle states."""
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    SIMULATING = "SIMULATING"
    SAFETY_REVIEW = "SAFETY_REVIEW"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"


# Valid state transitions
VALID_TRANSITIONS: dict[IncidentState, set[IncidentState]] = {
    IncidentState.DETECTED: {IncidentState.INVESTIGATING},
    IncidentState.INVESTIGATING: {IncidentState.ANALYZING, IncidentState.FAILED},
    IncidentState.ANALYZING: {IncidentState.PLANNING, IncidentState.FAILED},
    IncidentState.PLANNING: {IncidentState.SIMULATING, IncidentState.FAILED},
    IncidentState.SIMULATING: {IncidentState.SAFETY_REVIEW, IncidentState.FAILED},
    IncidentState.SAFETY_REVIEW: {IncidentState.AWAITING_APPROVAL, IncidentState.PLANNING, IncidentState.FAILED},
    IncidentState.AWAITING_APPROVAL: {IncidentState.EXECUTING, IncidentState.PLANNING, IncidentState.FAILED},
    IncidentState.EXECUTING: {IncidentState.VERIFYING, IncidentState.FAILED},
    IncidentState.VERIFYING: {IncidentState.RESOLVED, IncidentState.RECOVERY_REQUIRED, IncidentState.FAILED},
    IncidentState.RESOLVED: set(),
    IncidentState.RECOVERY_REQUIRED: {IncidentState.RECOVERING, IncidentState.FAILED},
    IncidentState.RECOVERING: {IncidentState.INVESTIGATING, IncidentState.FAILED},
    IncidentState.FAILED: set(),
}


# =====================================================================
# Risk Level
# =====================================================================

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# =====================================================================
# Audit Log
# =====================================================================

@dataclass
class AuditEntry:
    """Single entry in the incident audit timeline."""
    timestamp: float
    event_type: str
    agent: str
    message: str
    details: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "agent": self.agent,
            "message": self.message,
            "details": self.details,
        }


# =====================================================================
# Grid Analysis Output
# =====================================================================

@dataclass
class GridAnalysis:
    """Structured output from the Grid Analyst agent."""
    incident_summary: str
    root_cause_hypotheses: list[str]
    affected_components: list[dict[str, Any]]
    violations: list[dict[str, Any]]
    critical_constraints: list[str]
    recommended_investigation: list[str]
    grid_frequency_hz: float = 0.0
    total_demand_kw: float = 0.0
    total_generation_kw: float = 0.0
    is_stable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_summary": self.incident_summary,
            "root_cause_hypotheses": self.root_cause_hypotheses,
            "affected_components": self.affected_components,
            "violations": self.violations,
            "critical_constraints": self.critical_constraints,
            "recommended_investigation": self.recommended_investigation,
            "grid_frequency_hz": self.grid_frequency_hz,
            "total_demand_kw": self.total_demand_kw,
            "total_generation_kw": self.total_generation_kw,
            "is_stable": self.is_stable,
        }


# =====================================================================
# Candidate Plan
# =====================================================================

@dataclass
class CandidatePlan:
    """A single candidate intervention plan with simulation results."""
    plan_id: str
    name: str
    action_type: str
    parameters: dict[str, Any]
    description: str

    # Simulation results (populated after evaluate_action)
    is_valid: bool = False
    is_stable: bool = False
    rejection_reason: Optional[str] = None
    predicted_frequency_hz: float = 0.0
    predicted_total_demand_kw: float = 0.0
    violations: list[dict[str, Any]] = field(default_factory=list)
    line_loadings_pct: dict[str, float] = field(default_factory=dict)
    transformer_temperatures_c: dict[str, float] = field(default_factory=dict)
    critical_load_service_pct: dict[str, float] = field(default_factory=dict)
    summary: str = ""

    # Scoring
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)

    # Safety
    safety_approved: Optional[bool] = None
    safety_assessment: Optional[SafetyAssessment] = None
    risk_level: RiskLevel = RiskLevel.MEDIUM

    # Selection
    is_recommended: bool = False
    recommendation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "name": self.name,
            "action_type": self.action_type,
            "parameters": self.parameters,
            "description": self.description,
            "is_valid": self.is_valid,
            "is_stable": self.is_stable,
            "rejection_reason": self.rejection_reason,
            "predicted_frequency_hz": self.predicted_frequency_hz,
            "predicted_total_demand_kw": self.predicted_total_demand_kw,
            "violations": self.violations,
            "line_loadings_pct": self.line_loadings_pct,
            "transformer_temperatures_c": self.transformer_temperatures_c,
            "critical_load_service_pct": self.critical_load_service_pct,
            "summary": self.summary,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "safety_approved": self.safety_approved,
            "safety_assessment": self.safety_assessment.to_dict() if self.safety_assessment else None,
            "risk_level": self.risk_level.value,
            "is_recommended": self.is_recommended,
            "recommendation_reason": self.recommendation_reason,
        }


# =====================================================================
# Safety Assessment
# =====================================================================

@dataclass
class SafetyAssessment:
    """Structured safety review output from the Safety Agent."""
    approved: bool
    risk_level: RiskLevel
    reasons: list[str]
    violations: list[str]
    mitigations: list[str]
    critical_loads_affected: int = 0
    cascading_failure_risk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "risk_level": self.risk_level.value,
            "reasons": self.reasons,
            "violations": self.violations,
            "mitigations": self.mitigations,
            "critical_loads_affected": self.critical_loads_affected,
            "cascading_failure_risk": self.cascading_failure_risk,
        }


# =====================================================================
# Verification Result
# =====================================================================

@dataclass
class VerificationResult:
    """Result of post-action verification."""
    passed: bool
    is_stable: bool
    frequency_hz: float
    violations: list[dict[str, Any]]
    comparison: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "is_stable": self.is_stable,
            "frequency_hz": self.frequency_hz,
            "violations": self.violations,
            "comparison": self.comparison,
            "message": self.message,
        }


# =====================================================================
# Incident Record
# =====================================================================

@dataclass
class IncidentRecord:
    """Complete record of an incident lifecycle."""
    incident_id: str = field(default_factory=lambda: f"INC-{uuid.uuid4().hex[:8].upper()}")
    scenario_id: str = ""
    state: IncidentState = IncidentState.DETECTED
    severity: str = "HIGH"
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Agent outputs
    analysis: Optional[GridAnalysis] = None
    candidate_plans: list[CandidatePlan] = field(default_factory=list)
    recommended_plan: Optional[CandidatePlan] = None
    approved_plan: Optional[CandidatePlan] = None

    # Execution
    execution_result: Optional[dict[str, Any]] = None
    verification: Optional[VerificationResult] = None

    # Audit trail
    timeline: list[AuditEntry] = field(default_factory=list)

    # Recovery
    recovery_count: int = 0
    max_recovery_attempts: int = 2

    def transition(self, new_state: IncidentState) -> bool:
        """Attempts a state transition. Returns True if valid."""
        if new_state in VALID_TRANSITIONS.get(self.state, set()):
            self.state = new_state
            if new_state in (IncidentState.RESOLVED, IncidentState.FAILED):
                self.end_time = time.time()
            return True
        return False

    def add_timeline_entry(
        self, event_type: str, agent: str, message: str, details: Optional[dict[str, Any]] = None
    ) -> AuditEntry:
        entry = AuditEntry(
            timestamp=time.time(),
            event_type=event_type,
            agent=agent,
            message=message,
            details=details,
        )
        self.timeline.append(entry)
        return entry

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "scenario_id": self.scenario_id,
            "state": self.state.value,
            "severity": self.severity,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "analysis": self.analysis.to_dict() if self.analysis else None,
            "candidate_plans": [p.to_dict() for p in self.candidate_plans],
            "recommended_plan": self.recommended_plan.to_dict() if self.recommended_plan else None,
            "approved_plan": self.approved_plan.to_dict() if self.approved_plan else None,
            "execution_result": self.execution_result,
            "verification": self.verification.to_dict() if self.verification else None,
            "timeline": [e.to_dict() for e in self.timeline],
            "recovery_count": self.recovery_count,
        }

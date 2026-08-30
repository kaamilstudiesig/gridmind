"""
GridMind Specialist Roles: Operations, Safety, and Planning.
Conforms strictly to the Common Specialist Result contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from gridmind.contract import EvaluationResponse, GridStateResponse, IncidentStateResponse
from gridmind.llm import LLMClient


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


def _resolve_transformer_topology(
    grid_state: Optional[GridStateResponse],
    transformer_id: str,
    tripped_lines: set[str],
) -> Optional[dict[str, Any]]:
    """
    Traverses live GridStateResponse DTOs to resolve:
    - transformer node (feeder bus)
    - connected downstream non-critical curtailable load zone node
    - available healthy tie-line route(s)

    Returns None if topology is missing, incomplete, or unresolvable.
    """
    if not grid_state or not grid_state.transformers:
        return None

    # 1. Locate transformer DTO to find its feeder bus
    xfmr = next((t for t in grid_state.transformers if t.transformer_id == transformer_id), None)
    if not xfmr or not xfmr.node_id:
        return None

    feeder_node = xfmr.node_id

    # 2. Locate downstream non-critical load zone connected to feeder_node
    lz_by_node: dict[str, Any] = {lz.node_id: lz for lz in (grid_state.load_zones or [])}

    curtailable_load_node: Optional[str] = None
    for line in (grid_state.lines or []):
        if line.is_tie_line:
            continue
        target_node = None
        if line.from_node == feeder_node:
            target_node = line.to_node
        elif line.to_node == feeder_node:
            target_node = line.from_node

        if target_node and target_node in lz_by_node:
            lz = lz_by_node[target_node]
            if str(lz.priority).lower() != "critical":
                curtailable_load_node = target_node
                break

    # Fallback to feeder_node itself if it contains a non-critical load zone directly
    if not curtailable_load_node and feeder_node in lz_by_node:
        lz = lz_by_node[feeder_node]
        if str(lz.priority).lower() != "critical":
            curtailable_load_node = feeder_node

    if not curtailable_load_node:
        return None

    # 3. Locate available healthy tie-line route(s) from feeder_node
    tie_transfer: Optional[dict[str, Any]] = None
    for line in (grid_state.lines or []):
        if not line.is_tie_line:
            continue
        if line.from_node == feeder_node or line.to_node == feeder_node:
            dest_node = line.to_node if line.from_node == feeder_node else line.from_node
            status_str = str(line.status).lower()
            is_unavailable = (
                status_str in ("tripped", "isolated")
                or line.line_id in tripped_lines
            )
            if not is_unavailable:
                tie_transfer = {
                    "line_id": line.line_id,
                    "source": curtailable_load_node,
                    "destination": dest_node,
                    "transfer_mw": 0.100,
                }
            break

    return {
        "feeder_node": feeder_node,
        "load_node": curtailable_load_node,
        "tie_transfer": tie_transfer,
        "transformer_id": transformer_id,
    }


class OperationsSpecialist:
    """
    Operations Specialist:
    - Inspects active incident state and live grid telemetry.
    - Proposes at most 3 plausible candidate actions mapping directly to MCP actions.
    - Dynamically resolves affected assets, feeder routing, and curtailable load zones from live GridState topology.
    - Deterministically handles multiple overheated transformers or escalates when multi-feeder complexity exceeds budget.
    - Synthesizes findings and recommendations via LLMClient.
    - Does NOT execute actions.
    - Enforces MAX_CANDIDATES = 3.
    """
    MAX_CANDIDATES: int = 3

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client or LLMClient()

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

        # Nominal stable state check
        if incident_state.is_stable and not incident_state.active_violations:
            default_finding = "Grid operating in nominal stable state. Zero violations detected."
            default_rec = "Continue normal baseline monitoring."
            finding, rec = self.llm_client.generate_narrative(
                agent_role=SpecialistRole.OPERATIONS.value,
                status=SpecialistStatus.ACCEPT.value,
                candidates=[],
                evidence=evidence,
                risks=[],
                default_finding=default_finding,
                default_recommendation=default_rec,
            )
            return SpecialistResult(
                agent=SpecialistRole.OPERATIONS.value,
                status=SpecialistStatus.ACCEPT.value,
                candidates=[],
                finding=finding,
                evidence=evidence,
                risks=[],
                recommendation=rec,
            )

        # 1. Collect all overheated transformers from telemetry
        overheated_set: set[str] = set(incident_state.overheated_transformers)
        for v in incident_state.active_violations:
            if v.violation_type == "TRANSFORMER_OVERHEAT" and v.target_id:
                overheated_set.add(v.target_id)
            elif "T01" in v.description:
                overheated_set.add("T01")
            elif "T04" in v.description:
                overheated_set.add("T04")
            elif "T02" in v.description:
                overheated_set.add("T02")
            elif "T05" in v.description:
                overheated_set.add("T05")
            elif "T03" in v.description:
                overheated_set.add("T03")

        if not overheated_set:
            # Violations exist but no overheated transformer identified; ESCALATE for operator safety
            default_finding = "Active grid violations detected without identified transformer overload."
            default_rec = "Escalate unclassified incident to grid operator for evaluation."
            finding, rec = self.llm_client.generate_narrative(
                agent_role=SpecialistRole.OPERATIONS.value,
                status=SpecialistStatus.ESCALATE.value,
                candidates=[],
                evidence=evidence,
                risks=["Unclassified active violations present on network."],
                default_finding=default_finding,
                default_recommendation=default_rec,
            )
            return SpecialistResult(
                agent=SpecialistRole.OPERATIONS.value,
                status=SpecialistStatus.ESCALATE.value,
                candidates=[],
                finding=finding,
                evidence=evidence,
                risks=["Unclassified active violations present on network."],
                recommendation=rec,
            )

        # 2. Resolve topology for each overheated transformer from live grid_state
        tripped_lines = set(incident_state.tripped_lines)
        resolved_topologies: dict[str, dict[str, Any]] = {}
        for xfmr_id in sorted(list(overheated_set)):
            top = _resolve_transformer_topology(grid_state, xfmr_id, tripped_lines)
            if top is None:
                # Cannot derive safe topological path from supplied live grid state; ESCALATE
                finding_esc = f"Topology resolution failed for overheated transformer {xfmr_id}: unable to derive safe feeder/load routing from live grid topology."
                rec_esc = f"Escalate incident to grid dispatcher: missing or unresolvable topology for transformer {xfmr_id}."
                finding, rec = self.llm_client.generate_narrative(
                    agent_role=SpecialistRole.OPERATIONS.value,
                    status=SpecialistStatus.ESCALATE.value,
                    candidates=[],
                    evidence=evidence,
                    risks=[f"Unresolvable topology for {xfmr_id}; automated action formulation blocked."],
                    default_finding=finding_esc,
                    default_recommendation=rec_esc,
                )
                return SpecialistResult(
                    agent=SpecialistRole.OPERATIONS.value,
                    status=SpecialistStatus.ESCALATE.value,
                    candidates=[],
                    finding=finding,
                    evidence=evidence,
                    risks=[f"Unresolvable topology for {xfmr_id}; automated action formulation blocked."],
                    recommendation=rec,
                )
            resolved_topologies[xfmr_id] = top

        # 3. Group affected transformers by feeder bus
        feeders_map: dict[str, list[str]] = {}
        for xfmr_id, top in resolved_topologies.items():
            f_node = top["feeder_node"]
            feeders_map.setdefault(f_node, []).append(xfmr_id)

        # 4. Multi-feeder compound incident handling:
        # If multiple disparate feeders are simultaneously overheated, a single 3-candidate budget
        # cannot safely resolve all multi-feeder overloads without omitting affected assets.
        if len(feeders_map) > 1:
            affected_feeders_list = sorted(list(feeders_map.keys()))
            all_xfmrs_list = sorted(list(overheated_set))
            finding_esc = (
                f"Compound multi-feeder incident detected across {len(feeders_map)} feeders {affected_feeders_list} "
                f"(transformers: {all_xfmrs_list}). Automated candidate budget of {self.MAX_CANDIDATES} cannot safely "
                f"cover simultaneous multi-feeder overloads; escalating to system dispatcher."
            )
            rec_esc = "Escalate compound multi-feeder incident to dispatcher for coordinated multi-branch intervention."
            risks_esc = [
                f"Simultaneous thermal overloads across feeders {affected_feeders_list}; tie-line transfers between overloaded feeders are blocked."
            ]
            finding, rec = self.llm_client.generate_narrative(
                agent_role=SpecialistRole.OPERATIONS.value,
                status=SpecialistStatus.ESCALATE.value,
                candidates=[],
                evidence=evidence,
                risks=risks_esc,
                default_finding=finding_esc,
                default_recommendation=rec_esc,
            )
            return SpecialistResult(
                agent=SpecialistRole.OPERATIONS.value,
                status=SpecialistStatus.ESCALATE.value,
                candidates=[],
                finding=finding,
                evidence=evidence,
                risks=risks_esc,
                recommendation=rec,
            )

        # 5. Single-feeder incident: formulate candidates dynamically from resolved topology
        primary_feeder = next(iter(feeders_map.keys()))
        xfmrs_on_feeder = feeders_map[primary_feeder]
        primary_xfmr = xfmrs_on_feeder[0]
        top = resolved_topologies[primary_xfmr]
        load_target = top["load_node"]
        tie_info = top["tie_transfer"]

        candidates: list[dict[str, Any]] = []
        risks: list[str] = []

        # Candidate 1: Load restriction on the affected feeder's curtailable load zone
        candidates.append({
            "action_type": "load_restriction",
            "parameters": {"target": load_target, "reduction_pct": 15.0},
        })

        # Candidate 2: Power rerouting across emergency tie-line if available and healthy
        if tie_info:
            candidates.append({
                "action_type": "load_transfer",
                "parameters": {
                    "line_id": tie_info["line_id"],
                    "source": tie_info["source"],
                    "destination": tie_info["destination"],
                    "transfer_mw": tie_info["transfer_mw"],
                },
            })

        # Candidate 3: Isolation of overheated unit(s)
        for x_id in xfmrs_on_feeder:
            if len(candidates) < self.MAX_CANDIDATES:
                candidates.append({
                    "action_type": "isolate_transformer",
                    "parameters": {"transformer_id": x_id},
                })

        risks.append(f"Transformer(s) {xfmrs_on_feeder} on feeder {primary_feeder} exceeding 110.0°C maximum thermal limit.")
        if not tie_info:
            risks.append(f"No operational tie-line route available from feeder {primary_feeder}; rerouting unavailable.")

        # Rate-limit guardrail
        if len(candidates) > self.MAX_CANDIDATES:
            raise ValueError(
                f"Operations proposed {len(candidates)} candidates, exceeding MAX_CANDIDATES={self.MAX_CANDIDATES}"
            )

        chosen_candidates = candidates[: self.MAX_CANDIDATES]

        # Assign stable unique candidate_id to each candidate
        for idx, cand in enumerate(chosen_candidates):
            cand["candidate_id"] = f"C{idx:02d}"

        default_finding = f"Identified {len(chosen_candidates)} operational candidates to relieve transformer overheating on feeder {primary_feeder}."
        default_rec = "Evaluate operational candidate actions through MCP sandbox isolation before human approval."

        finding, rec = self.llm_client.generate_narrative(
            agent_role=SpecialistRole.OPERATIONS.value,
            status=SpecialistStatus.ACCEPT.value,
            candidates=chosen_candidates,
            evidence=evidence,
            risks=risks,
            default_finding=default_finding,
            default_recommendation=default_rec,
        )

        return SpecialistResult(
            agent=SpecialistRole.OPERATIONS.value,
            status=SpecialistStatus.ACCEPT.value,
            candidates=chosen_candidates,
            finding=finding,
            evidence=evidence,
            risks=risks,
            recommendation=rec,
        )


class SafetySpecialist:
    """
    Safety Specialist:
    - Evaluates sandbox simulation results for candidate actions.
    - Classifies candidates as ACCEPT, REJECT, or ESCALATE.
    - Enforces hard constraints: critical-load preservation (100%), line loading <= 100%, T <= 110.0°C.
    - Synthesizes findings and recommendations via LLMClient.
    - Does NOT execute actions.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client or LLMClient()

    def evaluate_candidates(
        self,
        candidates: list[dict[str, Any]],
        evaluations_by_id: dict[str, EvaluationResponse],
    ) -> tuple[SpecialistResult, list[dict[str, Any]]]:
        safe_candidates: list[dict[str, Any]] = []
        evidence: list[Any] = []
        risks: list[str] = []

        for candidate in candidates:
            cid = candidate["candidate_id"]
            eval_res = evaluations_by_id[cid]
            act_type = candidate.get("action_type", "unknown")
            evidence.append({
                "action": candidate,
                "action_valid": eval_res.action_valid,
                "is_stable": eval_res.is_stable,
                "rejection_reason": eval_res.rejection_reason,
                "violations": [v.description for v in eval_res.violations],
                "predicted_temp_t01": eval_res.predicted_transformer_temperatures_c.get("T01"),
                "predicted_temp_t04": eval_res.predicted_transformer_temperatures_c.get("T04"),
                "predicted_temp_t02": eval_res.predicted_transformer_temperatures_c.get("T02"),
                "predicted_temp_t05": eval_res.predicted_transformer_temperatures_c.get("T05"),
                "critical_load_service": eval_res.critical_load_service_pct,
            })

            # Check 1: Simulator rejected action validity (e.g. tripped tie line or secondary overload)
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
            default_finding = "All candidate actions were rejected by safety constraints."
            default_rec = "No safe immediate action available. Escalating to planning for long-term remediation."
        else:
            status = SpecialistStatus.ACCEPT.value
            default_finding = f"Verified {len(safe_candidates)} candidate action(s) satisfy all hard safety constraints."
            default_rec = f"Approve one verified safe candidate: {[c['action_type'] for c in safe_candidates]}."

        finding, rec = self.llm_client.generate_narrative(
            agent_role=SpecialistRole.SAFETY.value,
            status=status,
            candidates=safe_candidates,
            evidence=evidence,
            risks=risks,
            default_finding=default_finding,
            default_recommendation=default_rec,
        )

        res = SpecialistResult(
            agent=SpecialistRole.SAFETY.value,
            status=status,
            candidates=safe_candidates,
            finding=finding,
            evidence=evidence,
            risks=risks,
            recommendation=rec,
        )
        return res, safe_candidates


class PlanningSpecialist:
    """
    Planning Specialist:
    - Identifies longer-term asset remediation and reinforcement work orders.
    - Dynamically identifies overheated units from incident telemetry.
    - Synthesizes findings and recommendations via LLMClient.
    - Does NOT override immediate safety and does NOT execute actions.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client or LLMClient()

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

        overheated_set = set(incident_state.overheated_transformers)
        for v in getattr(incident_state, "active_violations", []):
            if getattr(v, "violation_type", None) == "TRANSFORMER_OVERHEAT" and getattr(v, "target_id", None):
                overheated_set.add(v.target_id)
            elif "T01" in getattr(v, "description", ""):
                overheated_set.add("T01")
            elif "T04" in getattr(v, "description", ""):
                overheated_set.add("T04")
            elif "T02" in getattr(v, "description", ""):
                overheated_set.add("T02")
            elif "T05" in getattr(v, "description", ""):
                overheated_set.add("T05")
            elif "T03" in getattr(v, "description", ""):
                overheated_set.add("T03")

        planning_candidates: list[dict[str, Any]] = []
        for xfmr_id in sorted(list(overheated_set)):
            planning_candidates.append({
                "action_type": "transformer_replacement",
                "parameters": {"transformer_id": xfmr_id, "additional_kva": 250.0},
            })

        if overheated_set:
            xfmr_names = ", ".join(sorted(list(overheated_set)))
            default_finding = (
                f"Recommended long-term planning work order: uprate/replace {xfmr_names} (+250 kVA) to provide expanded capacity."
            )
            risks = [
                "Planning work orders require capital equipment procurement and crew scheduling; they do not clear real-time thermal overloads immediately."
            ]
            default_rec = (
                f"Queue planning work order for {xfmr_names} uprate after resolving immediate operational constraints."
            )
        else:
            default_finding = "Zero transformer thermal overloads detected. No immediate replacement work order needed."
            risks = []
            default_rec = "Maintain standard planning inspection intervals."

        finding, rec = self.llm_client.generate_narrative(
            agent_role=SpecialistRole.PLANNING.value,
            status=SpecialistStatus.ACCEPT.value,
            candidates=planning_candidates,
            evidence=evidence,
            risks=risks,
            default_finding=default_finding,
            default_recommendation=default_rec,
        )

        return SpecialistResult(
            agent=SpecialistRole.PLANNING.value,
            status=SpecialistStatus.ACCEPT.value,
            candidates=planning_candidates,
            finding=finding,
            evidence=evidence,
            risks=risks,
            recommendation=rec,
        )

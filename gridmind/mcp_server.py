"""
Model Context Protocol (MCP) server for GridMind.

Exposes seven deterministic grid simulation and planning tools to AI agents and external callers:
1. get_grid_state (read-only)
2. get_incident_state (read-only)
3. evaluate_action (read-only / sandboxed)
4. execute_action (state-changing / Commander authorization gated)
5. get_last_simulation_result (read-only)
6. load_scenario (idempotent / state reset)
7. plan_incident_response (Commander planning workflow bridge / PENDING_APPROVAL audit record creation)

Architecture:
    MCP Server (plan_incident_response / execute_action) -> GridMindCommander -> GridMindService -> GridMindEngine
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator
import mcp.types as types
from mcp.server.mcpserver import MCPServer

from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecord, AuditRecordStatus, GridMindCommander
from gridmind.contract import ActionRequest
from gridmind.service import GridMindService


# =====================================================================
# Action Parameter Schemas
# =====================================================================

class LoadRestrictionParams(BaseModel):
    """
    Schema for load restriction (demand curtailment) action.
    Applies a numeric percentage reduction (0.0 to 100.0) to the target node or load zone.
    For example: 15.0 means 15% reduction (NOT 0.15%).
    Ambiguous string representations (e.g. "15" or "0.15") and non-numeric strings are rejected.
    """
    target: str = Field(
        ...,
        description="Target node ID or load zone ID to restrict (e.g. 'N08' or 'LZ02')",
    )
    reduction_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description=(
            "Percentage reduction to apply to zone demand as a numeric value between 0.0 and 100.0 "
            "(e.g., 15.0 means 15% reduction, NOT 0.15%). String values like '15' or '0.15' are rejected."
        ),
    )

    @field_validator("reduction_pct", mode="before")
    @classmethod
    def validate_reduction_pct_numeric(cls, v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(
                f"'reduction_pct' must be a numeric value between 0.0 and 100.0 (e.g. 15.0 means 15% reduction). "
                f"String or non-numeric values such as {v!r} are rejected."
            )
        val = float(v)
        if val < 0.0 or val > 100.0:
            raise ValueError(
                f"'reduction_pct' must be between 0.0 and 100.0 (e.g. 15.0 means 15% reduction), got {val}"
            )
        return val


class LoadTransferParams(BaseModel):
    """
    Schema for parameterized load transfer action.
    Explicitly requests a parameterized power transfer amount ('transfer_mw') across a tie line ('line_id')
    between supported source and destination endpoints (current supported route: L08 between N08 and N04).
    """
    line_id: str = Field(
        ...,
        description="Tie-line identifier for the transfer route (e.g. 'L08')",
    )
    source: str = Field(
        ...,
        description="Source node ID from which load is transferred (e.g. 'N08' on Feeder-B)",
    )
    destination: str = Field(
        ...,
        description="Destination node ID receiving the transferred load (e.g. 'N04' on Feeder-A)",
    )
    transfer_mw: float = Field(
        ...,
        gt=0.0,
        description="Explicitly requested power transfer amount in MW (e.g. 0.100 for 100 kW)",
    )


class IsolateTransformerParams(BaseModel):
    transformer_id: str = Field(
        ...,
        description="Transformer ID to isolate (e.g. 'T04')",
    )


class TransformerReplacementParams(BaseModel):
    transformer_id: str = Field(
        ...,
        description="Transformer ID to replace or uprate (e.g. 'T04')",
    )
    additional_kva: float = Field(
        default=250.0,
        gt=0.0,
        description="Additional rating capacity in kVA (e.g. 250.0)",
    )


class CloseTieLineParams(BaseModel):
    """
    Schema for non-parameterized tie-line closing action.
    Closes an available tie line (e.g. 'L08'). In the current GridMind model, closing L08
    results in the simulator's modeled default 0.10 MW (100 kW) transfer.
    It is not a parameterized transfer action.
    """
    line_id: str = Field(
        default="L08",
        description="Tie-line identifier to close (e.g. 'L08'). Results in default modeled 0.10 MW transfer.",
    )


ACTION_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "load_restriction": LoadRestrictionParams,
    "load_transfer": LoadTransferParams,
    "isolate_transformer": IsolateTransformerParams,
    "transformer_replacement": TransformerReplacementParams,
    "close_tie_line": CloseTieLineParams,
}


def _normalize_action_parameters(action_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Normalizes field aliases into canonical schema parameter keys."""
    d = dict(parameters)
    if action_type == "load_transfer":
        if "source" not in d and "from" in d:
            d["source"] = d.get("from")
        if "source" not in d and "from_node" in d:
            d["source"] = d.get("from_node")
        if "destination" not in d and "to" in d:
            d["destination"] = d.get("to")
        if "destination" not in d and "to_node" in d:
            d["destination"] = d.get("to_node")
        if "transfer_mw" not in d and "mw" in d:
            d["transfer_mw"] = d.get("mw")
        if "transfer_mw" not in d and "amount_mw" in d:
            d["transfer_mw"] = d.get("amount_mw")
    elif action_type in ("isolate_transformer", "transformer_replacement"):
        if "transformer_id" not in d and "target" in d:
            d["transformer_id"] = d.get("target")
    elif action_type == "load_restriction":
        if "target" not in d and "node_id" in d:
            d["target"] = d.get("node_id")
        if "target" not in d and "load_id" in d:
            d["target"] = d.get("load_id")
        if "reduction_pct" not in d and "reduction" in d:
            d["reduction_pct"] = d.get("reduction")
    return d


def validate_action_payload(
    action_type: str, parameters: Optional[dict[str, Any]]
) -> tuple[bool, dict[str, Any], Optional[str]]:
    """
    Validates an incoming action payload against its specific schema.
    Returns (is_valid, validated_dict, error_message).
    """
    if action_type not in ACTION_SCHEMA_MAP:
        allowed = sorted(ACTION_SCHEMA_MAP.keys())
        return (
            False,
            {},
            f"Unknown action_type '{action_type}'. Allowed actions are: {allowed}",
        )

    if parameters is None or not isinstance(parameters, dict):
        return (
            False,
            {},
            f"Parameters must be a non-null dictionary for action '{action_type}'",
        )

    normalized = _normalize_action_parameters(action_type, parameters)
    schema_cls = ACTION_SCHEMA_MAP[action_type]
    try:
        if hasattr(schema_cls, "model_validate"):
            validated_obj = schema_cls.model_validate(normalized)
            return True, validated_obj.model_dump(), None
        else:
            validated_obj = schema_cls.parse_obj(normalized)
            return True, validated_obj.dict(), None
    except Exception as exc:
        return False, {}, f"Invalid parameters for '{action_type}': {str(exc)}"


# =====================================================================
# Tool Annotations Constants
# =====================================================================

READ_ONLY_ANNOTATIONS = types.ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
)

DESTRUCTIVE_ANNOTATIONS = types.ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
)

IDEMPOTENT_MUTATING_ANNOTATIONS = types.ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
)


# =====================================================================
# GridMind MCP Server Wrapper
# =====================================================================

class GridMindMCPServer:
    """
    Stateful MCP Server managing GridMindService and GridMindCommander instances across all tool calls.
    Enforces that live execution passes through the Commander approval, state-revision revalidation,
    and audit tracking gate.
    """

    def __init__(
        self,
        service: Optional[GridMindService] = None,
        commander: Optional[GridMindCommander] = None,
        audit_store: Optional[AuditStore] = None,
        data_dir: str = "gridmind_data/curated",
    ) -> None:
        # Dependency Invariant 1: AuditStore resolution and validation
        if commander is not None and audit_store is not None:
            if commander.audit_store is not audit_store:
                raise ValueError(
                    "Dependency mismatch: Injected 'commander.audit_store' must be the exact same instance as injected 'audit_store'."
                )
            self.audit_store = audit_store
        elif commander is not None:
            self.audit_store = commander.audit_store
        elif audit_store is not None:
            self.audit_store = audit_store
        else:
            self.audit_store = AuditStore()

        # Dependency Invariant 2: GridMindService resolution and validation
        if commander is not None and service is not None:
            if commander.service is not service:
                raise ValueError(
                    "Dependency mismatch: Injected 'commander.service' must be the exact same instance as injected 'service'."
                )
            self.service = service
        elif commander is not None:
            self.service = commander.service
        elif service is not None:
            self.service = service
        else:
            self.service = GridMindService(data_dir=data_dir)

        # Dependency Invariant 3: Commander resolution
        if commander is not None:
            self.commander = commander
        else:
            self.commander = GridMindCommander(
                service=self.service,
                audit_store=self.audit_store,
            )

        self.server = MCPServer("gridmind-mcp")
        self._register_tools()

    def _register_tools(self) -> None:
        service = self.service
        commander = self.commander

        # Tool 1: get_grid_state
        @self.server.tool(
            name="get_grid_state",
            description="Read-only inspection of the current live grid state including frequency, line flows, line loadings, transformer loadings and temperatures, and active violations.",
            annotations=READ_ONLY_ANNOTATIONS,
        )
        async def get_grid_state() -> dict[str, Any]:
            return service.get_grid_state().to_dict()

        # Tool 2: get_incident_state
        @self.server.tool(
            name="get_incident_state",
            description="Read-only inspection of active incident conditions, tripped lines, overheated transformers, unserved critical loads, and constraint violations.",
            annotations=READ_ONLY_ANNOTATIONS,
        )
        async def get_incident_state() -> dict[str, Any]:
            return service.get_incident_state().to_dict()

        # Tool 3: evaluate_action
        @self.server.tool(
            name="evaluate_action",
            description="Sandbox evaluation of a candidate intervention. Performs non-mutating simulation on an isolated deep clone to predict post-action grid metrics and stability without affecting live state.",
            annotations=READ_ONLY_ANNOTATIONS,
        )
        async def evaluate_action(
            action_type: str = Field(
                ...,
                description=(
                    "Type of action: 'load_restriction' (curtail zone demand), "
                    "'load_transfer' (parameterized transfer with line_id, source, destination, transfer_mw), "
                    "'close_tie_line' (non-parameterized tie line close with line_id and default 0.10 MW transfer), "
                    "'isolate_transformer' (isolate transformer), "
                    "'transformer_replacement' (uprate/replace transformer planning work order)"
                ),
            ),
            parameters: dict[str, Any] = Field(
                ...,
                description="Structured action parameters dictionary matching the action_type schema",
            ),
        ) -> dict[str, Any]:
            is_valid, validated_params, err = validate_action_payload(
                action_type, parameters
            )
            if not is_valid:
                cur_state = service.get_grid_state() if service.state else None
                sim_res = (
                    service.state.latest_result or service.engine.solve(service.state)
                ) if service.state else None

                freq = cur_state.frequency_hz if cur_state else None
                total_kw = cur_state.total_demand_kw if cur_state else None
                line_loadings = (
                    {line.line_id: line.loading_pct for line in cur_state.lines}
                    if cur_state else {}
                )
                trans_temps = (
                    {t.transformer_id: t.temperature_c for t in cur_state.transformers}
                    if cur_state else {}
                )
                crit_service = (
                    dict(sim_res.critical_load_service_pct)
                    if sim_res else {}
                )

                return {
                    "action_valid": False,
                    "rejection_reason": err,
                    "is_stable": False,
                    "violations": [
                        {
                            "violation_type": "INVALID_ACTION",
                            "target_id": action_type,
                            "actual_value": 0.0,
                            "limit_value": 0.0,
                            "description": err or "Invalid action parameters",
                        }
                    ],
                    "predicted_frequency_hz": freq,
                    "predicted_total_demand_kw": total_kw,
                    "predicted_line_loadings_pct": line_loadings,
                    "predicted_transformer_temperatures_c": trans_temps,
                    "critical_load_service_pct": crit_service,
                    "summary": f"Action evaluation rejected: {err}",
                }

            req = ActionRequest(action_type=action_type, parameters=validated_params)
            resp = service.evaluate_action(req)
            return resp.to_dict()

        # Tool 4: execute_action (Commander Authorization Gated)
        @self.server.tool(
            name="execute_action",
            description=(
                "Live execution of an approved action on the active GridState. Requires prior multi-specialist planning, "
                "PENDING_APPROVAL status, trusted human operator authorization, and state revision match. "
                "Mutates live state and returns structured execution response."
            ),
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        async def execute_action(
            action_type: str = Field(
                ...,
                description=(
                    "Type of action: 'load_restriction' (curtail zone demand), "
                    "'load_transfer' (parameterized transfer with line_id, source, destination, transfer_mw), "
                    "'close_tie_line' (non-parameterized tie line close with line_id and default 0.10 MW transfer), "
                    "'isolate_transformer' (isolate transformer), "
                    "'transformer_replacement' (uprate/replace transformer planning work order)"
                ),
            ),
            parameters: dict[str, Any] = Field(
                ...,
                description="Structured action parameters dictionary matching the action_type schema",
            ),
        ) -> dict[str, Any]:
            is_valid, validated_params, err = validate_action_payload(
                action_type, parameters
            )
            cur_state = service.get_grid_state() if service.state else None
            sim_res = (
                service.state.latest_result or service.engine.solve(service.state)
            ) if service.state else None

            freq = cur_state.frequency_hz if cur_state else None
            total_kw = cur_state.total_demand_kw if cur_state else None
            line_loadings = (
                {line.line_id: line.loading_pct for line in cur_state.lines}
                if cur_state else {}
            )
            trans_temps = (
                {t.transformer_id: t.temperature_c for t in cur_state.transformers}
                if cur_state else {}
            )
            crit_service = (
                dict(sim_res.critical_load_service_pct)
                if sim_res else {}
            )
            cur_viols = [
                v.to_dict() for v in (cur_state.active_violations if cur_state else [])
            ]

            if not is_valid:
                return {
                    "success": False,
                    "action_applied": action_type,
                    "is_stable": False,
                    "violations": [
                        {
                            "violation_type": "INVALID_ACTION",
                            "target_id": action_type,
                            "actual_value": 0.0,
                            "limit_value": 0.0,
                            "description": err or "Invalid action parameters",
                        }
                    ],
                    "frequency_hz": freq,
                    "total_demand_kw": total_kw,
                    "line_loadings_pct": line_loadings,
                    "transformer_temperatures_c": trans_temps,
                    "critical_load_service_pct": crit_service,
                    "summary": f"Action execution rejected: {err}",
                    "error_message": err,
                }

            # 1. Invalidate all obsolete pending records across the system
            active_sc = service.active_scenario_id
            current_rev = service.get_state_revision()
            commander.audit_store.invalidate_stale_pending_records(
                active_scenario_id=active_sc,
                current_state_revision=current_rev,
            )

            # 2. Look up eligible PENDING_APPROVAL record for active scenario
            pending_dict = commander.audit_store.get_pending_for_scenario(active_sc)
            if not pending_dict:
                latest_rec = commander.audit_store.get_latest()
                if latest_rec and latest_rec.get("status") == AuditRecordStatus.STALE_STATE.value:
                    stale_sc = latest_rec.get("scenario_id")
                    stale_rev = latest_rec.get("state_revision")
                    err_msg = (
                        f"STALE_STATE: Previous incident plan '{latest_rec.get('incident_id')}' (scenario '{stale_sc}', revision {stale_rev}) "
                        f"is in STALE_STATE because active scenario is now '{active_sc}' (revision {current_rev}). "
                        f"Re-planning and explicit human operator authorization required for active scenario '{active_sc}' before live execution."
                    )
                else:
                    err_msg = (
                        f"APPROVAL_REQUIRED: No incident in PENDING_APPROVAL status for active scenario '{active_sc}'. "
                        f"Commander multi-specialist planning and explicit human operator authorization required before live execution."
                    )
                return {
                    "success": False,
                    "action_applied": action_type,
                    "is_stable": cur_state.is_stable if cur_state else False,
                    "violations": cur_viols,
                    "frequency_hz": freq,
                    "total_demand_kw": total_kw,
                    "line_loadings_pct": line_loadings,
                    "transformer_temperatures_c": trans_temps,
                    "critical_load_service_pct": crit_service,
                    "summary": f"Action execution rejected: {err_msg}",
                    "error_message": err_msg,
                }

            # 3. Validate action_type and parameters match the recommended action
            rec_action = pending_dict.get("recommended_action") or {}
            rec_type = rec_action.get("action_type", "")
            norm_req_params = _normalize_action_parameters(action_type, validated_params)
            norm_rec_params = _normalize_action_parameters(rec_type, rec_action.get("parameters", {}))

            if action_type != rec_type or norm_req_params != norm_rec_params:
                err_msg = (
                    f"INVALID_ACTION: Requested action '{action_type}' ({norm_req_params}) does not match "
                    f"Commander approved recommendation '{rec_type}' ({norm_rec_params})."
                )
                return {
                    "success": False,
                    "action_applied": action_type,
                    "is_stable": cur_state.is_stable if cur_state else False,
                    "violations": cur_viols,
                    "frequency_hz": freq,
                    "total_demand_kw": total_kw,
                    "line_loadings_pct": line_loadings,
                    "transformer_temperatures_c": trans_temps,
                    "critical_load_service_pct": crit_service,
                    "summary": f"Action execution rejected: {err_msg}",
                    "error_message": err_msg,
                }

            # 4. State-revision revalidation
            expected_revision = pending_dict.get("state_revision", "")
            if current_rev != expected_revision:
                stale_rec = AuditRecord(**pending_dict)
                stale_rec.status = AuditRecordStatus.STALE_STATE.value
                commander.audit_store.save(stale_rec)
                err_msg = (
                    f"STALE_STATE: Grid state changed since planning (was {expected_revision}, now {current_rev}). "
                    f"Re-plan required."
                )
                return {
                    "success": False,
                    "action_applied": action_type,
                    "is_stable": cur_state.is_stable if cur_state else False,
                    "violations": cur_viols,
                    "frequency_hz": freq,
                    "total_demand_kw": total_kw,
                    "line_loadings_pct": line_loadings,
                    "transformer_temperatures_c": trans_temps,
                    "critical_load_service_pct": crit_service,
                    "summary": f"Action execution rejected: {err_msg}",
                    "error_message": err_msg,
                }

            # 5. Trusted Human Operator Authorization Verification (Finding 1)
            # Fails closed if no real human operator authorization exists in the AuditRecord context.
            existing_approval = pending_dict.get("approval")
            has_human_approval = (
                isinstance(existing_approval, dict)
                and existing_approval.get("approved") is True
                and bool(existing_approval.get("approved_by"))
                and existing_approval.get("approved_by") != "mcp_operator_authorized"
            )

            if not has_human_approval:
                err_msg = (
                    f"APPROVAL_REQUIRED: Incident '{pending_dict['incident_id']}' is in PENDING_APPROVAL status but has not been "
                    f"authorized by an authenticated human operator. Explicit human authorization via the control center dashboard "
                    f"or authorization workflow is required before live execution."
                )
                return {
                    "success": False,
                    "action_applied": action_type,
                    "is_stable": cur_state.is_stable if cur_state else False,
                    "violations": cur_viols,
                    "frequency_hz": freq,
                    "total_demand_kw": total_kw,
                    "line_loadings_pct": line_loadings,
                    "transformer_temperatures_c": trans_temps,
                    "critical_load_service_pct": crit_service,
                    "summary": f"Action execution rejected: {err_msg}",
                    "error_message": err_msg,
                }

            # 6. Delegate through Commander for atomic claim, execution, verification, and audit persistence
            try:
                audit_rec = commander.approve_and_execute(
                    approval=existing_approval,
                    incident_id=pending_dict["incident_id"],
                )
                exec_data = audit_rec.execution
                resp_dict = exec_data.get("response") or {}
                if not resp_dict:
                    post_state = service.get_grid_state()
                    resp_dict = {
                        "success": audit_rec.status == AuditRecordStatus.VERIFIED.value,
                        "action_applied": action_type,
                        "is_stable": post_state.is_stable,
                        "violations": [v.to_dict() for v in post_state.active_violations],
                        "frequency_hz": post_state.frequency_hz,
                        "total_demand_kw": post_state.total_demand_kw,
                        "line_loadings_pct": {l.line_id: l.loading_pct for l in post_state.lines},
                        "transformer_temperatures_c": {t.transformer_id: t.temperature_c for t in post_state.transformers},
                        "critical_load_service_pct": dict(service.state.latest_result.critical_load_service_pct) if service.state.latest_result else {},
                        "summary": f"Action execution {audit_rec.status}",
                        "error_message": None,
                    }
                return resp_dict
            except ValueError as val_err:
                return {
                    "success": False,
                    "action_applied": action_type,
                    "is_stable": cur_state.is_stable if cur_state else False,
                    "violations": cur_viols,
                    "frequency_hz": freq,
                    "total_demand_kw": total_kw,
                    "line_loadings_pct": line_loadings,
                    "transformer_temperatures_c": trans_temps,
                    "critical_load_service_pct": crit_service,
                    "summary": f"Action execution rejected: {str(val_err)}",
                    "error_message": str(val_err),
                }

        # Tool 5: get_last_simulation_result
        @self.server.tool(
            name="get_last_simulation_result",
            description="Read-only retrieval of the most recent simulation evaluation or execution response.",
            annotations=READ_ONLY_ANNOTATIONS,
        )
        async def get_last_simulation_result() -> dict[str, Any]:
            resp = service.get_last_simulation_result()
            if resp is None:
                return {
                    "result": None,
                    "message": "No simulation evaluations or executions performed yet",
                }
            return resp.to_dict()

        # Tool 6: load_scenario
        @self.server.tool(
            name="load_scenario",
            description="Initializes or resets the simulator to a specific scenario state (e.g. SC01 heatwave incident). Returns the resulting incident state.",
            annotations=IDEMPOTENT_MUTATING_ANNOTATIONS,
        )
        async def load_scenario(
            scenario_id: str = Field(
                default="SC01",
                description="Scenario ID to load (e.g. 'SC01')",
            ),
        ) -> dict[str, Any]:
            try:
                resp = service.load_scenario(scenario_id)
                # Invalidate all obsolete pending records across the system
                commander.audit_store.invalidate_stale_pending_records(
                    active_scenario_id=service.active_scenario_id,
                    current_state_revision=service.get_state_revision(),
                )
                return resp.to_dict()
            except ValueError as err:
                return {
                    "error": str(err),
                    "success": False,
                    "scenario_id": service.active_scenario_id,
                }

        # Tool 7: plan_incident_response (Commander Planning Workflow Bridge)
        @self.server.tool(
            name="plan_incident_response",
            description=(
                "Triggers the multi-specialist Commander planning workflow (Operations, Safety, Planning) "
                "for the active scenario or incident. Synthesizes a deterministic recommended action, creates and "
                "persists an authoritative AuditRecord in PENDING_APPROVAL status in AuditStore, and returns the generated incident_id. "
                "Idempotent: if a live PENDING_APPROVAL plan already exists for the current scenario and grid state, "
                "returns that existing record without creating a duplicate. Safe to call on retry."
            ),
            annotations=IDEMPOTENT_MUTATING_ANNOTATIONS,
        )
        async def plan_incident_response(
            scenario_id: Optional[str] = Field(
                default=None,
                description="Optional scenario ID to plan for (e.g. 'SC02'). If provided and different from active, loads the scenario first.",
            ),
        ) -> dict[str, Any]:
            if scenario_id and scenario_id != service.active_scenario_id:
                try:
                    service.load_scenario(scenario_id)
                except ValueError as load_err:
                    return {
                        "error": str(load_err),
                        "success": False,
                        "scenario_id": service.active_scenario_id,
                        "incident_id": None,
                        "status": "LOAD_ERROR",
                        "recommended_action": None,
                    }
                # Invalidate stale records: this is a fast SQLite UPDATE and
                # does not need to be offloaded to a worker thread.
                commander.audit_store.invalidate_stale_pending_records(
                    active_scenario_id=service.active_scenario_id,
                    current_state_revision=service.get_state_revision(),
                )

            # incident_id is intentionally NOT accepted from the MCP caller.
            # The Commander always generates a fresh UUID-based INC-* identifier
            # or returns an existing idempotent PENDING_APPROVAL record.
            # This prevents callers from injecting synthetic identifiers.
            #
            # Performance: commander.plan_incident_response() is fully synchronous.
            # It calls three specialist LLM HTTP calls (each with up to 12 s timeout
            # × 2 retries), which can block the event loop for up to 72 seconds in
            # the worst case.  We offload the entire planning workflow to the
            # default ThreadPoolExecutor so the event loop remains free to serve
            # other MCP tools and health requests concurrently.
            plan_res = await asyncio.to_thread(commander.plan_incident_response)
            res_dict = plan_res.to_dict()

            if plan_res.status == AuditRecordStatus.PENDING_APPROVAL.value:
                res_dict["message"] = "Human approval required before execution."
            elif plan_res.status == AuditRecordStatus.ESCALATED.value:
                res_dict["message"] = "Incident response escalated to human control center. No action recommended."
            elif plan_res.status == AuditRecordStatus.NO_SAFE_ACTION.value:
                res_dict["message"] = "No safe operational intervention satisfies physical constraints."
            elif plan_res.status == AuditRecordStatus.NOMINAL.value:
                res_dict["message"] = "Grid conditions are nominal with no active violations."
            else:
                res_dict["message"] = f"Planning completed with status: {plan_res.status}"

            return res_dict


def create_mcp_server(
    service: Optional[GridMindService] = None,
    commander: Optional[GridMindCommander] = None,
    audit_store: Optional[AuditStore] = None,
    data_dir: str = "gridmind_data/curated",
) -> MCPServer:
    """Factory creating and returning an MCPServer instance configured with GridMind tools."""
    wrapper = GridMindMCPServer(
        service=service,
        commander=commander,
        audit_store=audit_store,
        data_dir=data_dir,
    )
    return wrapper.server


def main() -> None:
    """CLI entrypoint to start the GridMind MCP server over standard I/O."""
    server = create_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

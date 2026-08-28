"""
Model Context Protocol (MCP) server for GridMind.

Exposes exactly six deterministic grid simulation tools to AI agents and external callers:
1. get_grid_state (read-only)
2. get_incident_state (read-only)
3. evaluate_action (read-only / sandboxed)
4. execute_action (state-changing / live mutation)
5. get_last_simulation_result (read-only)
6. load_scenario (idempotent / state reset)

Architecture:
    MCP Server -> GridMindService -> GridMindEngine
"""

from __future__ import annotations

import sys
from typing import Any, Optional
from pydantic import BaseModel, Field
import mcp.types as types
from mcp.server.mcpserver import MCPServer

from gridmind.contract import ActionRequest
from gridmind.service import GridMindService


# =====================================================================
# Action Parameter Schemas
# =====================================================================

class LoadRestrictionParams(BaseModel):
    target: str = Field(
        ...,
        description="Target node ID or load zone ID to restrict (e.g. 'N08' or 'LZ02')",
    )
    reduction_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Percentage reduction to apply (0.0 to 100.0)",
    )


class LoadTransferParams(BaseModel):
    line_id: str = Field(
        ...,
        description="Tie-line identifier (e.g. 'L08')",
    )
    source: str = Field(
        ...,
        description="Source node ID from which load is transferred (e.g. 'N08')",
    )
    destination: str = Field(
        ...,
        description="Destination node ID receiving the transferred load (e.g. 'N04')",
    )
    transfer_mw: float = Field(
        ...,
        gt=0.0,
        description="Power transfer amount in MW (e.g. 0.100)",
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
    line_id: str = Field(
        default="L08",
        description="Emergency tie-line identifier to close (e.g. 'L08')",
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
    Stateful MCP Server managing a single GridMindService instance across all tool calls.
    """

    def __init__(
        self,
        service: Optional[GridMindService] = None,
        data_dir: str = "gridmind_data/curated",
    ) -> None:
        self.service = service or GridMindService(data_dir=data_dir)
        self.server = MCPServer("gridmind-mcp")
        self._register_tools()

    def _register_tools(self) -> None:
        service = self.service

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
                description="Type of action to evaluate: load_restriction, load_transfer, close_tie_line, isolate_transformer, transformer_replacement",
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

        # Tool 4: execute_action
        @self.server.tool(
            name="execute_action",
            description="Live execution of an approved action on the active GridState. Validates the action and mutates the live operating state. Returns structured execution response.",
            annotations=DESTRUCTIVE_ANNOTATIONS,
        )
        async def execute_action(
            action_type: str = Field(
                ...,
                description="Type of action to execute: load_restriction, load_transfer, close_tie_line, isolate_transformer, transformer_replacement",
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

            req = ActionRequest(action_type=action_type, parameters=validated_params)
            resp = service.execute_action(req)
            return resp.to_dict()

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
                return resp.to_dict()
            except ValueError as err:
                return {
                    "error": str(err),
                    "success": False,
                    "scenario_id": service.active_scenario_id,
                }


def create_mcp_server(
    service: Optional[GridMindService] = None,
    data_dir: str = "gridmind_data/curated",
) -> MCPServer:
    """Factory creating and returning an MCPServer instance configured with GridMind tools."""
    wrapper = GridMindMCPServer(service=service, data_dir=data_dir)
    return wrapper.server


def main() -> None:
    """CLI entrypoint to start the GridMind MCP server over standard I/O."""
    server = create_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

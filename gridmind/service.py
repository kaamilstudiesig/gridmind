"""
Service implementation of the GridMind simulator contract.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from gridmind.contract import (
    ActionRequest,
    EvaluationResponse,
    ExecutionResponse,
    GridStateResponse,
    IncidentStateResponse,
    LineDTO,
    LoadZoneDTO,
    NodeDTO,
    TransformerDTO,
    ViolationDTO,
)
from gridmind.engine import GridMindEngine
from gridmind.loader import load_curated_grid
from gridmind.models import (
    Action,
    ActionCategory,
    GridState,
    IncidentEvent,
    LineStatus,
    LoadPriority,
    SimulationResult,
    TransformerStatus,
    ViolationType,
)


SUPPORTED_SCENARIOS = frozenset({"SC01", "SC01-B", "SC01_B", "BASE"})


class GridMindService:
    """
    Stateful service wrapper providing a clean, typed contract for MCP tools,
    external agent harnesses, and dashboard interfaces to interact with the
    deterministic simulation engine.
    """

    def __init__(
        self,
        data_dir: str = "gridmind_data/curated",
        engine: Optional[GridMindEngine] = None,
    ) -> None:
        self.data_dir = data_dir
        self.engine = engine or GridMindEngine()
        self.state: GridState = load_curated_grid(self.data_dir)
        self.engine.solve(self.state)
        self.active_scenario_id: str = "BASE"
        self.last_simulation_result: Optional[SimulationResult] = self.state.latest_result

    def load_scenario(self, scenario_id: str = "SC01") -> IncidentStateResponse:
        """
        Initializes or resets the simulator to a specific scenario state.
        For SC01: applies heatwave environment, trips L08, and spikes N08.
        For SC01-B: applies heatwave environment and spikes N08 with L08 operational.
        Rejects unsupported scenario IDs before mutating live state.
        """
        clean_id = scenario_id.strip()
        norm_id = clean_id.upper().replace("_", "-")
        if norm_id not in ("SC01", "SC01-B", "BASE") and clean_id not in SUPPORTED_SCENARIOS:
            raise ValueError(
                f"Unsupported scenario ID '{scenario_id}'. Supported scenarios: {sorted(SUPPORTED_SCENARIOS)}"
            )

        canonical_id = {"SC01": "SC01", "SC01-B": "SC01-B", "BASE": "BASE"}.get(norm_id, clean_id)

        self.state = load_curated_grid(self.data_dir)
        self.active_scenario_id = canonical_id

        if canonical_id in ("SC01", "SC01-B"):
            # 1. Heatwave environment
            self.engine.apply_event(
                self.state,
                IncidentEvent(
                    event_type="environment",
                    parameters={"ambient_temp_c": 34.0, "demand_multiplier": 1.15, "storm": False},
                ),
            )
            # 2. Lockout on L08 (SC01 only; SC01-B keeps L08 operational/open)
            if canonical_id == "SC01":
                self.engine.apply_event(
                    self.state,
                    IncidentEvent(
                        event_type="line_failure",
                        parameters={"line_id": "L08"},
                    ),
                )
            # 3. Demand spike on N08 (+12%)
            self.engine.apply_event(
                self.state,
                IncidentEvent(
                    event_type="demand_spike",
                    parameters={"target": "N08", "increase_pct": 12.0},
                ),
            )

        self.engine.solve(self.state)
        self.last_simulation_result = self.state.latest_result
        return self.get_incident_state()

    def get_grid_state(self) -> GridStateResponse:
        """
        Read-only inspection of the current live grid state.
        Never mutates the state.
        """
        res = self.state.latest_result or self.engine.solve(self.state)

        nodes = [
            NodeDTO(
                node_id=n.node_id,
                node_type=n.node_type.value,
                name=n.name,
                voltage_kv=n.voltage_kv,
            )
            for n in self.state.nodes.values()
        ]

        lines = [
            LineDTO(
                line_id=e.line_id,
                from_node=e.from_node,
                to_node=e.to_node,
                capacity_mw=e.capacity_mw,
                flow_kw=res.line_flows_mw.get(e.line_id, 0.0) * 1000.0,
                loading_pct=res.line_loadings_pct.get(e.line_id, 0.0),
                status=e.status.value,
                is_tie_line=e.is_tie_line,
            )
            for e in self.state.edges.values()
        ]

        transformers = [
            TransformerDTO(
                transformer_id=t.transformer_id,
                node_id=t.node_id,
                rating_kva=t.rating_kva,
                load_pct=res.transformer_loadings_pct.get(t.transformer_id, 0.0),
                temperature_c=res.transformer_temperatures_c.get(t.transformer_id, 0.0),
                prior_failures=t.prior_failures,
                age_years=t.age_years,
                status=t.status.value,
            )
            for t in self.state.transformers.values()
        ]

        load_zones = [
            LoadZoneDTO(
                load_id=lz.load_id,
                node_id=lz.node_id,
                type=lz.type,
                base_kw=lz.base_mw * 1000.0,
                current_demand_kw=res.load_demands_mw.get(lz.load_id, 0.0) * 1000.0,
                served_kw=res.load_served_mw.get(lz.load_id, 0.0) * 1000.0,
                curtailment_pct=lz.curtailment_pct,
                priority=lz.priority.value,
            )
            for lz in self.state.load_zones.values()
        ]

        violations = [
            ViolationDTO(
                violation_type=v.violation_type.value,
                target_id=v.target_id,
                actual_value=v.actual_value,
                limit_value=v.limit_value,
                description=v.description,
            )
            for v in res.violations
        ]

        return GridStateResponse(
            is_stable=res.is_stable,
            frequency_hz=res.frequency_hz,
            total_generation_kw=res.total_generation_mw * 1000.0,
            total_demand_kw=res.total_demand_mw * 1000.0,
            ambient_temp_c=self.state.environment.ambient_temp_c,
            demand_multiplier=self.state.environment.demand_multiplier,
            storm=self.state.environment.storm,
            nodes=nodes,
            lines=lines,
            transformers=transformers,
            load_zones=load_zones,
            active_violations=violations,
        )

    def get_incident_state(self) -> IncidentStateResponse:
        """
        Read-only inspection of active incident conditions, tripped components,
        overheated equipment, and constraint violations.
        """
        res = self.state.latest_result or self.engine.solve(self.state)

        tripped_lines = [
            e.line_id
            for e in self.state.edges.values()
            if e.status in (LineStatus.TRIPPED, LineStatus.ISOLATED)
        ]

        overheated_transformers = [
            t.transformer_id
            for t in self.state.transformers.values()
            if res.transformer_temperatures_c.get(t.transformer_id, 0.0)
            > self.state.constraints.transformer_temperature_c_max
        ]

        unserved_critical_loads = [
            lz.load_id
            for lz in self.state.load_zones.values()
            if lz.priority == LoadPriority.CRITICAL
            and res.critical_load_service_pct.get(lz.load_id, 100.0)
            < self.state.constraints.critical_load_min_service_pct
        ]

        violations = [
            ViolationDTO(
                violation_type=v.violation_type.value,
                target_id=v.target_id,
                actual_value=v.actual_value,
                limit_value=v.limit_value,
                description=v.description,
            )
            for v in res.violations
        ]

        return IncidentStateResponse(
            scenario_id=self.active_scenario_id,
            is_stable=res.is_stable,
            active_violations=violations,
            tripped_lines=tripped_lines,
            overheated_transformers=overheated_transformers,
            unserved_critical_loads=unserved_critical_loads,
            frequency_hz=res.frequency_hz,
            ambient_temp_c=self.state.environment.ambient_temp_c,
            demand_multiplier=self.state.environment.demand_multiplier,
        )

    def evaluate_action(self, request: ActionRequest) -> EvaluationResponse:
        """
        Sandbox evaluation of a candidate intervention.
        GUARANTEE: Pure read-only sandbox check. The live state is NEVER mutated.
        """
        action = Action(
            action_type=request.action_type,
            category=ActionCategory.PLANNING
            if request.action_type == "transformer_replacement"
            else ActionCategory.IMMEDIATE_CONTROL,
            parameters=request.parameters,
        )

        res: SimulationResult = self.engine.evaluate_sandbox(self.state, action)
        self.last_simulation_result = res

        violations = [
            ViolationDTO(
                violation_type=v.violation_type.value,
                target_id=v.target_id,
                actual_value=v.actual_value,
                limit_value=v.limit_value,
                description=v.description,
            )
            for v in res.violations
        ]

        return EvaluationResponse(
            action_valid=res.action_valid,
            rejection_reason=res.rejection_reason,
            is_stable=res.is_stable,
            violations=violations,
            predicted_frequency_hz=res.frequency_hz,
            predicted_total_demand_kw=res.total_demand_mw * 1000.0,
            predicted_line_loadings_pct=res.line_loadings_pct,
            predicted_transformer_temperatures_c=res.transformer_temperatures_c,
            critical_load_service_pct=res.critical_load_service_pct,
            summary=res.summary,
        )

    def execute_action(self, request: ActionRequest) -> ExecutionResponse:
        """
        Live execution of an approved action on the active GridState.
        GUARANTEE: Validates action, mutates live state if valid, and recomputes all physical metrics.
        If invalid, rejects execution with structured error.
        """
        action = Action(
            action_type=request.action_type,
            category=ActionCategory.PLANNING
            if request.action_type == "transformer_replacement"
            else ActionCategory.IMMEDIATE_CONTROL,
            parameters=request.parameters,
        )

        is_valid, reason = self.engine.validate_action(self.state, action)
        if not is_valid:
            # Rejection without mutating physical state
            res = self.state.latest_result or self.engine.solve(self.state)
            violations = [
                ViolationDTO(
                    violation_type=v.violation_type.value,
                    target_id=v.target_id,
                    actual_value=v.actual_value,
                    limit_value=v.limit_value,
                    description=v.description,
                )
                for v in res.violations
            ]
            violations.append(
                ViolationDTO(
                    violation_type=ViolationType.INVALID_ACTION.value,
                    target_id=request.action_type,
                    actual_value=0.0,
                    limit_value=0.0,
                    description=f"Action validation rejected: {reason}",
                )
            )
            return ExecutionResponse(
                success=False,
                action_applied=request.action_type,
                is_stable=False,
                violations=violations,
                frequency_hz=res.frequency_hz,
                total_demand_kw=res.total_demand_mw * 1000.0,
                line_loadings_pct=res.line_loadings_pct,
                transformer_temperatures_c=res.transformer_temperatures_c,
                critical_load_service_pct=res.critical_load_service_pct,
                summary=f"Action execution rejected: {reason}",
                error_message=reason,
            )

        # Apply to live state
        self.engine.apply_action(self.state, action)
        res = self.state.latest_result or self.engine.solve(self.state)
        self.last_simulation_result = res

        violations = [
            ViolationDTO(
                violation_type=v.violation_type.value,
                target_id=v.target_id,
                actual_value=v.actual_value,
                limit_value=v.limit_value,
                description=v.description,
            )
            for v in res.violations
        ]

        return ExecutionResponse(
            success=True,
            action_applied=request.action_type,
            is_stable=res.is_stable,
            violations=violations,
            frequency_hz=res.frequency_hz,
            total_demand_kw=res.total_demand_mw * 1000.0,
            line_loadings_pct=res.line_loadings_pct,
            transformer_temperatures_c=res.transformer_temperatures_c,
            critical_load_service_pct=res.critical_load_service_pct,
            summary=res.summary,
            error_message=None,
        )

    def get_last_simulation_result(self) -> Optional[EvaluationResponse]:
        """Returns the most recent simulation evaluation result."""
        res = self.last_simulation_result or self.state.latest_result
        if not res:
            return None

        violations = [
            ViolationDTO(
                violation_type=v.violation_type.value,
                target_id=v.target_id,
                actual_value=v.actual_value,
                limit_value=v.limit_value,
                description=v.description,
            )
            for v in res.violations
        ]

        return EvaluationResponse(
            action_valid=res.action_valid,
            rejection_reason=res.rejection_reason,
            is_stable=res.is_stable,
            violations=violations,
            predicted_frequency_hz=res.frequency_hz,
            predicted_total_demand_kw=res.total_demand_mw * 1000.0,
            predicted_line_loadings_pct=res.line_loadings_pct,
            predicted_transformer_temperatures_c=res.transformer_temperatures_c,
            critical_load_service_pct=res.critical_load_service_pct,
            summary=res.summary,
        )

    def get_state_revision(self) -> str:
        """
        Returns a deterministic hash of the current grid/scenario state.
        Used for state-revision revalidation between planning and execution.
        """
        res = self.state.latest_result or self.engine.solve(self.state)
        state_snapshot = {
            "scenario_id": self.active_scenario_id,
            "frequency_hz": round(res.frequency_hz, 6),
            "is_stable": res.is_stable,
            "transformer_temperatures": {
                k: round(v, 4) for k, v in sorted(res.transformer_temperatures_c.items())
            },
            "line_loadings": {
                k: round(v, 4) for k, v in sorted(res.line_loadings_pct.items())
            },
            "violations": sorted([v.description for v in res.violations]),
        }
        snapshot_bytes = json.dumps(state_snapshot, sort_keys=True, ensure_ascii=True).encode()
        return hashlib.sha256(snapshot_bytes).hexdigest()[:16]

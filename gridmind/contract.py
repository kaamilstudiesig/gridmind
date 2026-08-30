"""
Typed contract definitions and DTO schemas for GridMind external interfaces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class ViolationDTO:
    violation_type: str
    target_id: str
    actual_value: float
    limit_value: float
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionRequest:
    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResponse:
    action_valid: bool
    rejection_reason: Optional[str]
    is_stable: bool
    violations: list[ViolationDTO]
    predicted_frequency_hz: float
    predicted_total_demand_kw: float
    predicted_line_loadings_pct: dict[str, float]
    predicted_transformer_temperatures_c: dict[str, float]
    critical_load_service_pct: dict[str, float]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionResponse:
    success: bool
    action_applied: str
    is_stable: bool
    violations: list[ViolationDTO]
    frequency_hz: float
    total_demand_kw: float
    line_loadings_pct: dict[str, float]
    transformer_temperatures_c: dict[str, float]
    critical_load_service_pct: dict[str, float]
    summary: str
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NodeDTO:
    node_id: str
    node_type: str
    name: str
    voltage_kv: float


@dataclass
class LineDTO:
    line_id: str
    from_node: str
    to_node: str
    capacity_mw: float
    flow_kw: float
    loading_pct: float
    status: str
    is_tie_line: bool


@dataclass
class TransformerDTO:
    transformer_id: str
    node_id: str
    rating_kva: float
    load_pct: float
    temperature_c: float
    prior_failures: int
    age_years: int
    status: str


@dataclass
class LoadZoneDTO:
    load_id: str
    node_id: str
    type: str
    base_kw: float
    current_demand_kw: float
    served_kw: float
    curtailment_pct: float
    priority: str


@dataclass
class GridStateResponse:
    is_stable: bool
    frequency_hz: float
    total_generation_kw: float
    total_demand_kw: float
    ambient_temp_c: float
    demand_multiplier: float
    storm: bool
    nodes: list[NodeDTO]
    lines: list[LineDTO]
    transformers: list[TransformerDTO]
    load_zones: list[LoadZoneDTO]
    active_violations: list[ViolationDTO]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IncidentStateResponse:
    scenario_id: str
    is_stable: bool
    active_violations: list[ViolationDTO]
    tripped_lines: list[str]
    overheated_transformers: list[str]
    unserved_critical_loads: list[str]
    frequency_hz: float
    ambient_temp_c: float
    demand_multiplier: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

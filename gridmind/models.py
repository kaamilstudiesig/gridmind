"""
Domain models and dataclasses for GridMind simulation engine.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class NodeType(str, Enum):
    SUBSTATION = "substation"
    FEEDER = "feeder"
    LOAD_ZONE = "load_zone"


class LineStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    TRIPPED = "tripped"
    ISOLATED = "isolated"


class TransformerStatus(str, Enum):
    NORMAL = "normal"
    WATCH = "watch"
    OVERLOAD_RISK = "overload_risk"
    ISOLATED = "isolated"


class LoadPriority(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ActionCategory(str, Enum):
    IMMEDIATE_CONTROL = "immediate_control"
    PLANNING = "planning"


class ViolationType(str, Enum):
    LINE_OVERLOAD = "LINE_OVERLOAD"
    TRANSFORMER_OVERHEAT = "TRANSFORMER_OVERHEAT"
    FREQUENCY_OUT_OF_BOUNDS = "FREQUENCY_OUT_OF_BOUNDS"
    CRITICAL_LOAD_UNSERVED = "CRITICAL_LOAD_UNSERVED"
    INVALID_ACTION = "INVALID_ACTION"


@dataclass
class GridNode:
    node_id: str
    node_type: NodeType
    name: str
    voltage_kv: float


@dataclass
class GridEdge:
    edge_id: str
    from_node: str
    to_node: str
    line_id: str
    voltage_class: str
    capacity_mw: float
    status: LineStatus = LineStatus.CLOSED
    is_tie_line: bool = False


@dataclass
class Transformer:
    transformer_id: str
    node_id: str
    rating_kva: float
    load_pct: float
    age_years: int
    temperature_c: float
    prior_failures: int
    status: TransformerStatus = TransformerStatus.NORMAL


@dataclass
class LoadZone:
    load_id: str
    node_id: str
    type: str
    base_mw: float
    min_service_pct: float
    peak_service_pct: float
    priority: LoadPriority
    curtailment_pct: float = 0.0
    demand_spike_pct: float = 0.0


@dataclass
class GridEnvironment:
    ambient_temp_c: float = 30.0
    demand_multiplier: float = 1.0
    storm: bool = False


@dataclass
class ConstraintLimits:
    frequency_hz_min: float = 49.5
    frequency_hz_max: float = 50.5
    line_loading_pct_max: float = 100.0
    transformer_temperature_c_max: float = 110.0
    critical_load_min_service_pct: float = 100.0


@dataclass
class ConstraintViolation:
    violation_type: ViolationType
    target_id: str
    actual_value: float
    limit_value: float
    description: str


ALLOWED_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "load_restriction",
        "load_transfer",
        "close_tie_line",
        "isolate_transformer",
        "transformer_replacement",
    }
)


@dataclass
class SimulationResult:
    frequency_hz: float
    total_demand_mw: float
    total_generation_mw: float
    available_generation_mw: float = 1.258750
    generation_demand_imbalance_mw: float = 0.0
    line_flows_mw: dict[str, float] = field(default_factory=dict)
    line_loadings_pct: dict[str, float] = field(default_factory=dict)
    transformer_loadings_pct: dict[str, float] = field(default_factory=dict)
    transformer_temperatures_c: dict[str, float] = field(default_factory=dict)
    load_demands_mw: dict[str, float] = field(default_factory=dict)
    load_served_mw: dict[str, float] = field(default_factory=dict)
    critical_load_service_pct: dict[str, float] = field(default_factory=dict)
    violations: list[ConstraintViolation] = field(default_factory=list)
    is_stable: bool = True
    action_applied: Optional[str] = None
    action_valid: bool = True
    rejection_reason: Optional[str] = None
    summary: str = ""


@dataclass
class Action:
    action_type: str
    category: ActionCategory = ActionCategory.IMMEDIATE_CONTROL
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncidentEvent:
    event_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class GridState:
    nodes: dict[str, GridNode] = field(default_factory=dict)
    edges: dict[str, GridEdge] = field(default_factory=dict)
    transformers: dict[str, Transformer] = field(default_factory=dict)
    load_zones: dict[str, LoadZone] = field(default_factory=dict)
    environment: GridEnvironment = field(default_factory=GridEnvironment)
    constraints: ConstraintLimits = field(default_factory=ConstraintLimits)
    available_generation_mw: float = 1.258750
    p_gen_base: float = 1.258750
    active_transfers: dict[str, Any] = field(default_factory=dict)
    planning_work_orders: list[dict[str, Any]] = field(default_factory=list)
    latest_result: Optional[SimulationResult] = None
    applied_actions: list[Action] = field(default_factory=list)
    applied_events: list[IncidentEvent] = field(default_factory=list)

    def clone(self) -> GridState:
        """Create a full deep copy of the grid state for sandbox isolation."""
        return copy.deepcopy(self)

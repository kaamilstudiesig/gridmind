"""
Data loader for synthetic grid assets and scenario definitions.
"""

import csv
import json
from pathlib import Path
from typing import Any, Union

from gridmind.models import (
    ConstraintLimits,
    GridEdge,
    GridEnvironment,
    GridNode,
    GridState,
    LineStatus,
    LoadPriority,
    LoadZone,
    NodeType,
    Transformer,
    TransformerStatus,
)

# Approved physically-consistent baseline load mapping (in MW)
# calibrated to 1.75 MVA transformer fleet at 0.95 power factor
CALIBRATED_BASE_LOADS_MW: dict[str, float] = {
    "LZ01": 0.315875,  # N07: Residential-A (315.88 kW)
    "LZ02": 0.447875,  # N08: Commercial-A (447.88 kW)
    "LZ04": 0.172000,  # N10: Hospital-A [Critical] (172.00 kW)
    "LZ03": 0.323000,  # N09: Industrial-A [High] (323.00 kW)
}

# Approved distribution tier line capacities (in MW)
DISTRIBUTION_LINE_CAPACITIES_MW: dict[str, float] = {
    "L01": 2.0,
    "L02": 2.5,
    "L03": 2.5,
    "L04": 0.8,
    "L05": 1.0,
    "L06": 1.2,
    "L07": 0.6,
    "L08": 1.0,  # Emergency Tie-Line
}


def load_curated_grid(data_dir: Union[str, Path] = "gridmind_data/curated") -> GridState:
    """
    Loads grid topology, equipment metadata, and baseline conditions from CSV files.
    
    Treats CSV values as initial metadata and populates a fresh GridState.
    """
    path = Path(data_dir)
    state = GridState()

    # 1. Load Nodes
    nodes_file = path / "synthetic_grid_nodes.csv"
    with open(nodes_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            node_id = row["node_id"].strip()
            node_type_str = row["node_type"].strip().lower()
            name = row["name"].strip()
            voltage_kv = float(row["voltage_kv"])

            if node_type_str == "substation":
                ntype = NodeType.SUBSTATION
            elif node_type_str == "feeder":
                ntype = NodeType.FEEDER
            else:
                ntype = NodeType.LOAD_ZONE

            state.nodes[node_id] = GridNode(
                node_id=node_id,
                node_type=ntype,
                name=name,
                voltage_kv=voltage_kv,
            )

    # 2. Load Edges (Lines)
    edges_file = path / "synthetic_grid_edges.csv"
    with open(edges_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            edge_id = row["edge_id"].strip()
            from_node = row["from_node"].strip()
            to_node = row["to_node"].strip()
            line_id = row["line_id"].strip()
            voltage_class = row["voltage_class"].strip()
            capacity_mw = DISTRIBUTION_LINE_CAPACITIES_MW.get(
                line_id, float(row["capacity_mw"])
            )

            is_tie_line = line_id == "L08"
            # L08 is normally OPEN; other distribution lines are CLOSED
            status = LineStatus.OPEN if is_tie_line else LineStatus.CLOSED

            state.edges[edge_id] = GridEdge(
                edge_id=edge_id,
                from_node=from_node,
                to_node=to_node,
                line_id=line_id,
                voltage_class=voltage_class,
                capacity_mw=capacity_mw,
                status=status,
                is_tie_line=is_tie_line,
            )

    # 3. Load Transformers
    transformers_file = path / "synthetic_transformers.csv"
    with open(transformers_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t_id = row["transformer_id"].strip()
            node_id = row["node_id"].strip()
            rating_kva = float(row["rating_kva"])
            load_pct = float(row["load_pct"])  # Initial reference
            age_years = int(row["age_years"])
            temperature_c = float(row["temperature_c"])  # Initial reference
            prior_failures = int(row["prior_failures"])
            status_str = row["status"].strip().lower()

            if status_str == "overload_risk":
                t_status = TransformerStatus.OVERLOAD_RISK
            elif status_str == "watch":
                t_status = TransformerStatus.WATCH
            elif status_str == "isolated":
                t_status = TransformerStatus.ISOLATED
            else:
                t_status = TransformerStatus.NORMAL

            state.transformers[t_id] = Transformer(
                transformer_id=t_id,
                node_id=node_id,
                rating_kva=rating_kva,
                load_pct=load_pct,
                age_years=age_years,
                temperature_c=temperature_c,
                prior_failures=prior_failures,
                status=t_status,
            )

    # 4. Load Load Zones
    load_zones_file = path / "synthetic_load_zones.csv"
    with open(load_zones_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            load_id = row["load_id"].strip()
            node_id = row["node_id"].strip()
            l_type = row["type"].strip()
            base_mw = CALIBRATED_BASE_LOADS_MW.get(load_id, float(row["base_mw"]))
            raw_min = float(row["min_service_pct"])
            min_service_pct = raw_min * 100.0 if raw_min <= 1.0 else raw_min
            raw_peak = float(row["peak_service_pct"])
            peak_service_pct = raw_peak * 100.0 if raw_peak <= 1.0 else raw_peak
            priority_str = row["priority"].strip().lower()

            if priority_str == "critical":
                priority = LoadPriority.CRITICAL
            elif priority_str == "high":
                priority = LoadPriority.HIGH
            else:
                priority = LoadPriority.NORMAL

            state.load_zones[load_id] = LoadZone(
                load_id=load_id,
                node_id=node_id,
                type=l_type,
                base_mw=base_mw,
                min_service_pct=min_service_pct,
                peak_service_pct=peak_service_pct,
                priority=priority,
            )

    # Environment defaults
    state.environment = GridEnvironment(
        ambient_temp_c=30.0,
        demand_multiplier=1.0,
        storm=False,
    )
    base_sum = sum(lz.base_mw for lz in state.load_zones.values())
    state.available_generation_mw = base_sum
    state.p_gen_base = base_sum

    return state


def load_scenario(scenario_path: Union[str, Path]) -> dict[str, Any]:
    """Loads scenario definition JSON."""
    with open(scenario_path, mode="r", encoding="utf-8") as f:
        return json.load(f)

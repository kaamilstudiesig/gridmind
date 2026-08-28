"""
Grid Analyst Agent for GridMind.

Inspects live grid state and incident conditions to produce a structured
analysis grounded entirely in actual simulator data. No fabricated values.
"""

from __future__ import annotations

import logging
from typing import Any

from gridmind.service import GridMindService
from agent.models import GridAnalysis

logger = logging.getLogger("gridmind.agent.grid_analyst")


class GridAnalyst:
    """
    Analyzes the current grid state and incident conditions.
    
    Uses GridMindService read-only methods (get_grid_state, get_incident_state)
    to identify problems, root causes, and constraints. All data comes from the
    actual deterministic simulator.
    """

    def __init__(self, service: GridMindService) -> None:
        self.service = service

    def analyze(self) -> GridAnalysis:
        """
        Perform a comprehensive grid analysis.
        
        Returns a structured GridAnalysis grounded in real simulator data.
        """
        logger.info("Grid Analyst: Starting analysis")

        # Read actual grid state
        grid_state = self.service.get_grid_state()
        incident_state = self.service.get_incident_state()

        # Identify affected components
        affected_components: list[dict[str, Any]] = []

        # Find tripped/failed lines
        tripped_lines = []
        for line in grid_state.lines:
            if line.status in ("tripped", "isolated"):
                tripped_lines.append(line)
                affected_components.append({
                    "type": "line",
                    "id": line.line_id,
                    "status": line.status,
                    "is_tie_line": line.is_tie_line,
                    "from_node": line.from_node,
                    "to_node": line.to_node,
                })

        # Find overloaded lines
        overloaded_lines = []
        for line in grid_state.lines:
            if line.loading_pct > 100.0 and line.status == "closed":
                overloaded_lines.append(line)
                affected_components.append({
                    "type": "line",
                    "id": line.line_id,
                    "status": "overloaded",
                    "loading_pct": round(line.loading_pct, 2),
                    "capacity_mw": line.capacity_mw,
                })

        # Find problematic transformers
        overheated_transformers = []
        high_load_transformers = []
        for t in grid_state.transformers:
            if t.temperature_c > 110.0:
                overheated_transformers.append(t)
                affected_components.append({
                    "type": "transformer",
                    "id": t.transformer_id,
                    "status": "overheated",
                    "temperature_c": round(t.temperature_c, 2),
                    "load_pct": round(t.load_pct, 2),
                    "node_id": t.node_id,
                    "prior_failures": t.prior_failures,
                    "age_years": t.age_years,
                })
            elif t.load_pct > 90.0:
                high_load_transformers.append(t)
                affected_components.append({
                    "type": "transformer",
                    "id": t.transformer_id,
                    "status": "high_load",
                    "temperature_c": round(t.temperature_c, 2),
                    "load_pct": round(t.load_pct, 2),
                    "node_id": t.node_id,
                })

        # Check critical loads
        unserved_critical = []
        for lz in grid_state.load_zones:
            if lz.priority == "critical" and lz.served_kw < lz.current_demand_kw * 0.999:
                unserved_critical.append(lz)
                affected_components.append({
                    "type": "load_zone",
                    "id": lz.load_id,
                    "status": "unserved",
                    "priority": lz.priority,
                    "demand_kw": round(lz.current_demand_kw, 2),
                    "served_kw": round(lz.served_kw, 2),
                })

        # Build violations list
        violations = []
        for v in grid_state.active_violations:
            violations.append({
                "type": v.violation_type,
                "target": v.target_id,
                "actual": round(v.actual_value, 2),
                "limit": round(v.limit_value, 2),
                "description": v.description,
            })

        # Generate root cause hypotheses
        root_causes = []
        if tripped_lines:
            tie_lines_tripped = [l for l in tripped_lines if l.is_tie_line]
            if tie_lines_tripped:
                root_causes.append(
                    f"Emergency tie-line {tie_lines_tripped[0].line_id} is tripped/locked out, "
                    f"eliminating inter-feeder load transfer capability"
                )
            for l in tripped_lines:
                if not l.is_tie_line:
                    root_causes.append(f"Distribution line {l.line_id} failure between {l.from_node} and {l.to_node}")

        if grid_state.demand_multiplier > 1.0:
            root_causes.append(
                f"Elevated demand multiplier ({grid_state.demand_multiplier:.2f}x) indicates "
                f"environmental stress (ambient temp: {grid_state.ambient_temp_c}°C)"
            )

        # Check for demand spikes
        for lz in grid_state.load_zones:
            if lz.current_demand_kw > lz.base_kw * grid_state.demand_multiplier * 1.05:
                spike_pct = ((lz.current_demand_kw / (lz.base_kw * grid_state.demand_multiplier)) - 1.0) * 100.0
                root_causes.append(
                    f"Demand spike on {lz.load_id} ({lz.node_id}): "
                    f"{spike_pct:.1f}% above expected load"
                )

        if overheated_transformers:
            for t in overheated_transformers:
                root_causes.append(
                    f"Transformer {t.transformer_id} at {t.node_id} overheated to {t.temperature_c:.1f}°C "
                    f"(limit: 110°C), load: {t.load_pct:.1f}%, "
                    f"age: {t.age_years}yr, prior failures: {t.prior_failures}"
                )

        # Identify critical constraints
        critical_constraints = [
            "Frequency must remain within 49.5–50.5 Hz",
            "No line loading may exceed 100%",
            "Transformer temperatures must remain below 110°C",
            "Critical loads (Hospital-A/LZ04) must maintain 100% service",
        ]

        # Build recommendations
        recommendations = []
        if overheated_transformers:
            recommendations.append("Evaluate load reduction on affected feeder to lower transformer temperature")
            recommendations.append("Evaluate transformer isolation if temperature is dangerously high")
            recommendations.append("Evaluate transformer replacement/uprating for long-term solution")
        if tripped_lines:
            for l in tripped_lines:
                if not l.is_tie_line:
                    recommendations.append(f"Evaluate alternative routing around failed line {l.line_id}")
        if not [l for l in tripped_lines if l.is_tie_line]:
            recommendations.append("Evaluate emergency load transfer via tie-line if available")

        # Build incident summary
        summary_parts = []
        summary_parts.append(f"Grid Status: {'STABLE' if grid_state.is_stable else 'UNSTABLE'}")
        summary_parts.append(f"Frequency: {grid_state.frequency_hz:.4f} Hz")
        summary_parts.append(f"Total Demand: {grid_state.total_demand_kw:.1f} kW")
        summary_parts.append(f"Scenario: {incident_state.scenario_id}")
        if violations:
            summary_parts.append(f"Active Violations: {len(violations)}")
        if tripped_lines:
            summary_parts.append(f"Tripped Lines: {', '.join(l.line_id for l in tripped_lines)}")
        if overheated_transformers:
            summary_parts.append(
                f"Overheated Transformers: {', '.join(t.transformer_id for t in overheated_transformers)}"
            )

        incident_summary = " | ".join(summary_parts)

        analysis = GridAnalysis(
            incident_summary=incident_summary,
            root_cause_hypotheses=root_causes,
            affected_components=affected_components,
            violations=violations,
            critical_constraints=critical_constraints,
            recommended_investigation=recommendations,
            grid_frequency_hz=grid_state.frequency_hz,
            total_demand_kw=grid_state.total_demand_kw,
            total_generation_kw=grid_state.total_generation_kw,
            is_stable=grid_state.is_stable,
        )

        logger.info(
            "Grid Analyst: Analysis complete — %s, %d violations, %d affected components",
            "STABLE" if grid_state.is_stable else "UNSTABLE",
            len(violations),
            len(affected_components),
        )

        return analysis

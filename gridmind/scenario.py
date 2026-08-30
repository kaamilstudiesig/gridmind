"""
Scenario runner and evaluation for Scenario SC01:
Peak-load feeder/transformer overload under heatwave and emergency tie lockout.
"""

from typing import Any, Union
from pathlib import Path

from gridmind.engine import GridMindEngine
from gridmind.loader import load_curated_grid
from gridmind.models import Action, ActionCategory, GridState, IncidentEvent


def run_scenario_sc01(data_dir: Union[str, Path] = "gridmind_data/curated") -> dict[str, Any]:
    """
    Executes the complete deterministic lifecycle for Scenario SC01:
    1. Base state loading and baseline verification
    2. Incident injection (L08 lockout + N08 commercial spike + heatwave)
    3. Isolated sandbox evaluation of candidate actions
    4. Execution of approved action on live grid state
    """
    engine = GridMindEngine()
    state = load_curated_grid(data_dir)

    # -------------------------------------------------------------
    # Step 1: Establish SC01 Baseline (Heatwave conditions)
    # -------------------------------------------------------------
    engine.apply_event(
        state,
        IncidentEvent(
            event_type="environment",
            parameters={"ambient_temp_c": 34.0, "demand_multiplier": 1.15, "storm": False},
        ),
    )
    baseline_result = engine.solve(state)

    # -------------------------------------------------------------
    # Step 2: Inject Incident Events
    # -------------------------------------------------------------
    # Event 1: Emergency tie-line L08 lockout
    engine.apply_event(
        state,
        IncidentEvent(
            event_type="line_failure",
            parameters={"line_id": "L08"},
        ),
    )
    # Event 2: Commercial demand spike on N08 (+12%)
    engine.apply_event(
        state,
        IncidentEvent(
            event_type="demand_spike",
            parameters={"target": "N08", "increase_pct": 12.0},
        ),
    )
    incident_result = engine.solve(state)

    # -------------------------------------------------------------
    # Step 3: Evaluate Candidate Interventions in Sandbox
    # -------------------------------------------------------------
    action_load_restriction = Action(
        action_type="load_restriction",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"target": "N08", "reduction_pct": 15.0},
    )

    action_load_transfer = Action(
        action_type="load_transfer",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"from": "N08", "to": "N04", "line_id": "L08", "mw": 0.100},
    )

    action_isolate_t04 = Action(
        action_type="isolate_transformer",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"transformer_id": "T04"},
    )

    action_replace_t04 = Action(
        action_type="transformer_replacement",
        category=ActionCategory.PLANNING,
        parameters={"transformer_id": "T04", "additional_kva": 250.0},
    )

    eval_load_restriction = engine.evaluate_sandbox(state, action_load_restriction)
    eval_load_transfer = engine.evaluate_sandbox(state, action_load_transfer)
    eval_isolate_t04 = engine.evaluate_sandbox(state, action_isolate_t04)
    eval_replace_t04 = engine.evaluate_sandbox(state, action_replace_t04)

    # -------------------------------------------------------------
    # Step 4: Execute Approved Operational Action (Load Restriction)
    # -------------------------------------------------------------
    # Verify live state was untouched by sandbox evaluations
    t04_pre_action_temp = state.transformers["T04"].temperature_c

    # Apply approved operational control action to live state
    engine.apply_action(state, action_load_restriction)
    executed_result = state.latest_result

    return {
        "scenario_id": "SC01",
        "baseline": {
            "is_stable": baseline_result.is_stable,
            "violations_count": len(baseline_result.violations),
            "freq_hz": baseline_result.frequency_hz,
            "total_demand_kw": baseline_result.total_demand_mw * 1000.0,
            "t04_load_pct": baseline_result.transformer_loadings_pct.get("T04", 0.0),
            "t04_temp_c": baseline_result.transformer_temperatures_c.get("T04", 0.0),
            "t02_temp_c": baseline_result.transformer_temperatures_c.get("T02", 0.0),
            "critical_hospital_service_pct": baseline_result.critical_load_service_pct.get("LZ04", 100.0),
        },
        "incident": {
            "is_stable": incident_result.is_stable,
            "violations": [v.description for v in incident_result.violations],
            "freq_hz": incident_result.frequency_hz,
            "total_demand_kw": incident_result.total_demand_mw * 1000.0,
            "t04_load_pct": incident_result.transformer_loadings_pct.get("T04", 0.0),
            "t04_temp_c": incident_result.transformer_temperatures_c.get("T04", 0.0),
            "t02_temp_c": incident_result.transformer_temperatures_c.get("T02", 0.0),
            "critical_hospital_service_pct": incident_result.critical_load_service_pct.get("LZ04", 100.0),
        },
        "sandbox_evaluations": {
            "load_restriction_15pct": {
                "action_valid": eval_load_restriction.action_valid,
                "is_stable": eval_load_restriction.is_stable,
                "violations_count": len(eval_load_restriction.violations),
                "t04_temp_c": eval_load_restriction.transformer_temperatures_c.get("T04", 0.0),
                "t02_temp_c": eval_load_restriction.transformer_temperatures_c.get("T02", 0.0),
            },
            "load_transfer_l08": {
                "action_valid": eval_load_transfer.action_valid,
                "is_stable": eval_load_transfer.is_stable,
                "rejection_reason": eval_load_transfer.rejection_reason,
            },
            "isolate_t04": {
                "action_valid": eval_isolate_t04.action_valid,
                "is_stable": eval_isolate_t04.is_stable,
                "t02_load_pct": eval_isolate_t04.transformer_loadings_pct.get("T02", 0.0),
                "t02_temp_c": eval_isolate_t04.transformer_temperatures_c.get("T02", 0.0),
            },
            "replace_t04_500kva": {
                "action_valid": eval_replace_t04.action_valid,
                "is_stable": eval_replace_t04.is_stable,
                "t04_temp_c": eval_replace_t04.transformer_temperatures_c.get("T04", 0.0),
                "t02_temp_c": eval_replace_t04.transformer_temperatures_c.get("T02", 0.0),
            },
        },
        "sandbox_isolation_verified": (t04_pre_action_temp == incident_result.transformer_temperatures_c["T04"]),
        "post_execution": {
            "is_stable": executed_result.is_stable if executed_result else False,
            "t04_temp_c": executed_result.transformer_temperatures_c.get("T04", 0.0) if executed_result else 0.0,
            "critical_hospital_service_pct": executed_result.critical_load_service_pct.get("LZ04", 100.0) if executed_result else 0.0,
        },
    }


def run_scenario_sc01_b(data_dir: Union[str, Path] = "gridmind_data/curated") -> dict[str, Any]:
    """
    Executes the complete deterministic lifecycle for Scenario SC01-B:
    1. Base state loading and baseline verification
    2. Incident injection (N08 commercial spike + heatwave, while L08 remains healthy and available)
    3. Isolated sandbox evaluation of candidate actions (including both load_restriction and load_transfer)
    4. Execution of load_transfer intervention on live grid state
    """
    engine = GridMindEngine()
    state = load_curated_grid(data_dir)

    # Step 1: Establish Baseline (Heatwave conditions)
    engine.apply_event(
        state,
        IncidentEvent(
            event_type="environment",
            parameters={"ambient_temp_c": 34.0, "demand_multiplier": 1.15, "storm": False},
        ),
    )
    baseline_result = engine.solve(state)

    # Step 2: Inject Incident Events (Commercial demand spike on N08; L08 remains OPEN and healthy)
    engine.apply_event(
        state,
        IncidentEvent(
            event_type="demand_spike",
            parameters={"target": "N08", "increase_pct": 12.0},
        ),
    )
    incident_result = engine.solve(state)
    inc_l08_status = state.edges["E08"].status.value

    # Step 3: Evaluate Candidate Interventions in Sandbox
    action_load_restriction = Action(
        action_type="load_restriction",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"target": "N08", "reduction_pct": 15.0},
    )

    action_load_transfer = Action(
        action_type="load_transfer",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"from": "N08", "to": "N04", "line_id": "L08", "mw": 0.100},
    )

    action_isolate_t04 = Action(
        action_type="isolate_transformer",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"transformer_id": "T04"},
    )

    action_replace_t04 = Action(
        action_type="transformer_replacement",
        category=ActionCategory.PLANNING,
        parameters={"transformer_id": "T04", "additional_kva": 250.0},
    )

    eval_load_restriction = engine.evaluate_sandbox(state, action_load_restriction)
    eval_load_transfer = engine.evaluate_sandbox(state, action_load_transfer)
    eval_isolate_t04 = engine.evaluate_sandbox(state, action_isolate_t04)
    eval_replace_t04 = engine.evaluate_sandbox(state, action_replace_t04)

    # Step 4: Execute Approved Operational Action (Load Transfer)
    t04_pre_action_temp = state.transformers["T04"].temperature_c
    engine.apply_action(state, action_load_transfer)
    executed_result = state.latest_result

    return {
        "scenario_id": "SC01-B",
        "baseline": {
            "is_stable": baseline_result.is_stable,
            "violations_count": len(baseline_result.violations),
            "freq_hz": baseline_result.frequency_hz,
            "total_demand_kw": baseline_result.total_demand_mw * 1000.0,
            "t04_load_pct": baseline_result.transformer_loadings_pct.get("T04", 0.0),
            "t04_temp_c": baseline_result.transformer_temperatures_c.get("T04", 0.0),
            "t02_temp_c": baseline_result.transformer_temperatures_c.get("T02", 0.0),
            "critical_hospital_service_pct": baseline_result.critical_load_service_pct.get("LZ04", 100.0),
        },
        "incident": {
            "is_stable": incident_result.is_stable,
            "violations": [v.description for v in incident_result.violations],
            "freq_hz": incident_result.frequency_hz,
            "total_demand_kw": incident_result.total_demand_mw * 1000.0,
            "t04_load_pct": incident_result.transformer_loadings_pct.get("T04", 0.0),
            "t04_temp_c": incident_result.transformer_temperatures_c.get("T04", 0.0),
            "t02_temp_c": incident_result.transformer_temperatures_c.get("T02", 0.0),
            "l08_status": inc_l08_status,
            "critical_hospital_service_pct": incident_result.critical_load_service_pct.get("LZ04", 100.0),
        },
        "sandbox_evaluations": {
            "load_restriction_15pct": {
                "action_valid": eval_load_restriction.action_valid,
                "is_stable": eval_load_restriction.is_stable,
                "violations_count": len(eval_load_restriction.violations),
                "t04_temp_c": eval_load_restriction.transformer_temperatures_c.get("T04", 0.0),
                "t02_temp_c": eval_load_restriction.transformer_temperatures_c.get("T02", 0.0),
            },
            "load_transfer_l08": {
                "action_valid": eval_load_transfer.action_valid,
                "is_stable": eval_load_transfer.is_stable,
                "l08_flow_kw": eval_load_transfer.line_flows_mw.get("L08", 0.0) * 1000.0,
                "t04_temp_c": eval_load_transfer.transformer_temperatures_c.get("T04", 0.0),
                "t02_temp_c": eval_load_transfer.transformer_temperatures_c.get("T02", 0.0),
                "violations_count": len(eval_load_transfer.violations),
            },
            "isolate_t04": {
                "action_valid": eval_isolate_t04.action_valid,
                "is_stable": eval_isolate_t04.is_stable,
                "t02_load_pct": eval_isolate_t04.transformer_loadings_pct.get("T02", 0.0),
                "t02_temp_c": eval_isolate_t04.transformer_temperatures_c.get("T02", 0.0),
            },
            "replace_t04_500kva": {
                "action_valid": eval_replace_t04.action_valid,
                "is_stable": eval_replace_t04.is_stable,
                "t04_temp_c": eval_replace_t04.transformer_temperatures_c.get("T04", 0.0),
                "t02_temp_c": eval_replace_t04.transformer_temperatures_c.get("T02", 0.0),
            },
        },
        "sandbox_isolation_verified": (t04_pre_action_temp == incident_result.transformer_temperatures_c["T04"]),
        "post_execution": {
            "is_stable": executed_result.is_stable if executed_result else False,
            "l08_flow_kw": executed_result.line_flows_mw.get("L08", 0.0) * 1000.0 if executed_result else 0.0,
            "t04_temp_c": executed_result.transformer_temperatures_c.get("T04", 0.0) if executed_result else 0.0,
            "critical_hospital_service_pct": executed_result.critical_load_service_pct.get("LZ04", 100.0) if executed_result else 0.0,
        },
    }


def run_scenario_sc02(data_dir: Union[str, Path] = "gridmind_data/curated") -> dict[str, Any]:
    """
    Executes the complete deterministic lifecycle for Scenario SC02:
    1. Base state loading and storm baseline verification (ambient_temp_c=28, demand_multiplier=1.15, storm=True)
    2. Incident injection (severe residential demand surge on N07 +50%, while tie-line L08 is operational)
    3. Isolated sandbox evaluation of candidate actions (C00: load_restriction N07 15%, C01: load_transfer N07->N05 0.1MW, C02: isolate T01, C03: replace T01)
    4. Execution of approved action (load_restriction on N07) on live grid state
    """
    engine = GridMindEngine()
    state = load_curated_grid(data_dir)

    # Step 1: Establish Storm Baseline
    engine.apply_event(
        state,
        IncidentEvent(
            event_type="environment",
            parameters={"ambient_temp_c": 28.0, "demand_multiplier": 1.15, "storm": True},
        ),
    )
    baseline_result = engine.solve(state)

    # Step 2: Inject Incident Event (N07 residential surge +50%)
    engine.apply_event(
        state,
        IncidentEvent(
            event_type="demand_spike",
            parameters={"target": "N07", "increase_pct": 50.0},
        ),
    )
    incident_result = engine.solve(state)

    # Step 3: Evaluate Candidate Interventions in Sandbox
    action_load_restriction = Action(
        action_type="load_restriction",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"target": "N07", "reduction_pct": 15.0},
    )

    action_load_transfer = Action(
        action_type="load_transfer",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"line_id": "L08", "source": "N07", "destination": "N05", "transfer_mw": 0.100},
    )

    action_isolate_t01 = Action(
        action_type="isolate_transformer",
        category=ActionCategory.IMMEDIATE_CONTROL,
        parameters={"transformer_id": "T01"},
    )

    action_replace_t01 = Action(
        action_type="transformer_replacement",
        category=ActionCategory.PLANNING,
        parameters={"transformer_id": "T01", "additional_kva": 250.0},
    )

    eval_load_restriction = engine.evaluate_sandbox(state, action_load_restriction)
    eval_load_transfer = engine.evaluate_sandbox(state, action_load_transfer)
    eval_isolate_t01 = engine.evaluate_sandbox(state, action_isolate_t01)
    eval_replace_t01 = engine.evaluate_sandbox(state, action_replace_t01)

    # Step 4: Execute Approved Operational Action (Load Restriction on N07)
    t01_pre_action_temp = state.transformers["T01"].temperature_c
    engine.apply_action(state, action_load_restriction)
    executed_result = state.latest_result

    return {
        "scenario_id": "SC02",
        "baseline": {
            "is_stable": baseline_result.is_stable,
            "violations_count": len(baseline_result.violations),
            "freq_hz": baseline_result.frequency_hz,
            "total_demand_kw": baseline_result.total_demand_mw * 1000.0,
            "t01_load_pct": baseline_result.transformer_loadings_pct.get("T01", 0.0),
            "t01_temp_c": baseline_result.transformer_temperatures_c.get("T01", 0.0),
            "t05_temp_c": baseline_result.transformer_temperatures_c.get("T05", 0.0),
            "t04_temp_c": baseline_result.transformer_temperatures_c.get("T04", 0.0),
            "critical_hospital_service_pct": baseline_result.critical_load_service_pct.get("LZ04", 100.0),
        },
        "incident": {
            "is_stable": incident_result.is_stable,
            "violations": [v.description for v in incident_result.violations],
            "freq_hz": incident_result.frequency_hz,
            "total_demand_kw": incident_result.total_demand_mw * 1000.0,
            "t01_load_pct": incident_result.transformer_loadings_pct.get("T01", 0.0),
            "t01_temp_c": incident_result.transformer_temperatures_c.get("T01", 0.0),
            "t05_temp_c": incident_result.transformer_temperatures_c.get("T05", 0.0),
            "t04_temp_c": incident_result.transformer_temperatures_c.get("T04", 0.0),
            "critical_hospital_service_pct": incident_result.critical_load_service_pct.get("LZ04", 100.0),
        },
        "sandbox_evaluations": {
            "load_restriction_15pct": {
                "action_valid": eval_load_restriction.action_valid,
                "is_stable": eval_load_restriction.is_stable,
                "violations_count": len(eval_load_restriction.violations),
                "t01_temp_c": eval_load_restriction.transformer_temperatures_c.get("T01", 0.0),
                "t05_temp_c": eval_load_restriction.transformer_temperatures_c.get("T05", 0.0),
            },
            "load_transfer_l08": {
                "action_valid": eval_load_transfer.action_valid,
                "is_stable": eval_load_transfer.is_stable,
                "rejection_reason": eval_load_transfer.rejection_reason,
            },
            "isolate_t01": {
                "action_valid": eval_isolate_t01.action_valid,
                "is_stable": eval_isolate_t01.is_stable,
                "t05_temp_c": eval_isolate_t01.transformer_temperatures_c.get("T05", 0.0),
            },
            "replace_t01_500kva": {
                "action_valid": eval_replace_t01.action_valid,
                "is_stable": eval_replace_t01.is_stable,
                "t01_temp_c": eval_replace_t01.transformer_temperatures_c.get("T01", 0.0),
            },
        },
        "sandbox_isolation_verified": (t01_pre_action_temp == incident_result.transformer_temperatures_c["T01"]),
        "post_execution": {
            "is_stable": executed_result.is_stable if executed_result else False,
            "t01_temp_c": executed_result.transformer_temperatures_c.get("T01", 0.0) if executed_result else 0.0,
            "critical_hospital_service_pct": executed_result.critical_load_service_pct.get("LZ04", 100.0) if executed_result else 0.0,
        },
    }


if __name__ == "__main__":
    import pprint
    print("=" * 60)
    print("SC01 DETERMINISTIC SCENARIO EXECUTION REPORT")
    print("=" * 60)
    pprint.pprint(run_scenario_sc01(), width=100)
    print("=" * 60)
    print("SC01-B DETERMINISTIC SCENARIO EXECUTION REPORT")
    print("=" * 60)
    pprint.pprint(run_scenario_sc01_b(), width=100)
    print("=" * 60)
    print("SC02 DETERMINISTIC SCENARIO EXECUTION REPORT")
    print("=" * 60)
    pprint.pprint(run_scenario_sc02(), width=100)

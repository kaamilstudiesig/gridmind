"""
Unit tests for deterministic simulation engine, power flow, thermal model, frequency droop, and sandbox isolation.
"""

import unittest

from gridmind.engine import GridMindEngine
from gridmind.loader import load_curated_grid
from gridmind.models import (
    Action,
    ActionCategory,
    IncidentEvent,
    LineStatus,
    TransformerStatus,
    ViolationType,
)


class TestEngine(unittest.TestCase):
    """Tests deterministic engine mechanics and constraints."""

    def setUp(self) -> None:
        self.engine = GridMindEngine()
        self.state = load_curated_grid("gridmind_data/curated")

    def test_nominal_baseline_solution(self) -> None:
        """Tests that nominal baseline produces exact deterministic metrics."""
        result = self.engine.solve(self.state)

        # Baseline stability
        self.assertTrue(result.is_stable)
        self.assertEqual(len(result.violations), 0)

        # Total demand = 1.258750 MW (1258.75 kW)
        self.assertAlmostEqual(result.total_demand_mw, 1.258750, places=5)
        self.assertAlmostEqual(result.frequency_hz, 50.0000, places=4)

        # Feeder and LT line flows
        self.assertAlmostEqual(result.line_flows_mw["L01"], 0.315875, places=5)
        self.assertAlmostEqual(result.line_flows_mw["L02"], 0.619875, places=5)
        self.assertAlmostEqual(result.line_flows_mw["L03"], 0.323000, places=5)
        self.assertAlmostEqual(result.line_flows_mw["L08"], 0.000000, places=5)

        # Transformer loadings
        self.assertAlmostEqual(result.transformer_loadings_pct["T04"], 93.0, places=2)
        self.assertAlmostEqual(result.transformer_loadings_pct["T02"], 84.0, places=2)
        self.assertAlmostEqual(result.transformer_loadings_pct["T01"], 72.0, places=2)
        self.assertAlmostEqual(result.transformer_loadings_pct["T05"], 61.0, places=2)
        self.assertAlmostEqual(result.transformer_loadings_pct["T03"], 68.0, places=2)

        # Transformer temperatures at ambient=30.0C
        # T = 30 + 60 * (0.93^1.8) = 82.65C
        self.assertAlmostEqual(result.transformer_temperatures_c["T04"], 82.65, delta=0.1)
        # T = 30 + 60 * (0.84^1.8) = 73.84C
        self.assertAlmostEqual(result.transformer_temperatures_c["T02"], 73.84, delta=0.1)

        # Critical load 100% served
        self.assertEqual(result.critical_load_service_pct["LZ04"], 100.0)

    def test_deterministic_repeatability(self) -> None:
        """Solving the same state multiple times must yield bitwise-identical values."""
        res1 = self.engine.solve(self.state)
        res2 = self.engine.solve(self.state)

        self.assertEqual(res1.frequency_hz, res2.frequency_hz)
        self.assertEqual(res1.transformer_temperatures_c, res2.transformer_temperatures_c)
        self.assertEqual(res1.line_flows_mw, res2.line_flows_mw)

    def test_frequency_droop_response(self) -> None:
        """Tests frequency calculation under demand increase."""
        # Increase demand multiplier to 1.15 (+15%)
        self.state.environment.demand_multiplier = 1.15
        result = self.engine.solve(self.state)

        # f = 50.00 - 0.40 * 0.15 = 49.9400 Hz
        self.assertAlmostEqual(result.frequency_hz, 49.9400, places=4)
        self.assertTrue(result.is_stable)

        # Extreme demand causing frequency violation
        self.state.environment.demand_multiplier = 3.0  # +200% overload
        result_extreme = self.engine.solve(self.state)
        # f = 50.00 - 0.40 * 2.0 = 49.20 Hz (< 49.50 Hz limit)
        self.assertLess(result_extreme.frequency_hz, 49.50)
        self.assertFalse(result_extreme.is_stable)
        freq_violations = [
            v for v in result_extreme.violations if v.violation_type == ViolationType.FREQUENCY_OUT_OF_BOUNDS
        ]
        self.assertEqual(len(freq_violations), 1)

    def test_transformer_thermal_model_and_overheat_violation(self) -> None:
        """Tests thermal rise formula and limit violation threshold."""
        # Ambient 34C, T04 at 116.22% load
        self.state.environment.ambient_temp_c = 34.0
        self.state.environment.demand_multiplier = 1.15
        self.state.load_zones["LZ02"].demand_spike_pct = 12.0

        result = self.engine.solve(self.state)
        t04_temp = result.transformer_temperatures_c["T04"]

        # Expected T04 temp: 34 + 60 * (1.1622^1.8) = 112.65 C (> 110.0 C limit)
        self.assertAlmostEqual(t04_temp, 112.65, delta=0.2)
        self.assertFalse(result.is_stable)

        overheat_violations = [
            v for v in result.violations if v.violation_type == ViolationType.TRANSFORMER_OVERHEAT
        ]
        self.assertEqual(len(overheat_violations), 1)
        self.assertEqual(overheat_violations[0].target_id, "T04")

    def test_critical_load_curtailment_prevention(self) -> None:
        """Tests that critical load zone LZ04 cannot be restricted without violation."""
        action_invalid = Action(
            action_type="load_restriction",
            category=ActionCategory.IMMEDIATE_CONTROL,
            parameters={"target": "N10", "reduction_pct": 20.0},
        )
        is_valid, reason = self.engine.validate_action(self.state, action_invalid)
        self.assertFalse(is_valid)
        self.assertIn("CRITICAL", reason or "")

        # Trip line L07 supplying Hospital
        for edge in self.state.edges.values():
            if edge.line_id == "L07":
                edge.status = LineStatus.TRIPPED

        result = self.engine.solve(self.state)
        self.assertEqual(result.critical_load_service_pct["LZ04"], 0.0)
        crit_violations = [
            v for v in result.violations if v.violation_type == ViolationType.CRITICAL_LOAD_UNSERVED
        ]
        self.assertEqual(len(crit_violations), 1)

    def test_sandbox_isolation_invariance(self) -> None:
        """Tests that evaluate_sandbox strictly prevents mutation of live state."""
        self.state.environment.ambient_temp_c = 34.0
        self.state.environment.demand_multiplier = 1.15
        self.state.load_zones["LZ02"].demand_spike_pct = 12.0
        self.engine.solve(self.state)

        # Baseline incident T04 temp is ~112.65 C
        initial_t04_temp = self.state.transformers["T04"].temperature_c
        self.assertFalse(self.state.latest_result.is_stable)

        # Evaluate load restriction action in sandbox
        action = Action(
            action_type="load_restriction",
            category=ActionCategory.IMMEDIATE_CONTROL,
            parameters={"target": "N08", "reduction_pct": 15.0},
        )
        sandbox_res = self.engine.evaluate_sandbox(self.state, action)

        # Sandbox evaluated as stable
        self.assertTrue(sandbox_res.is_stable)
        self.assertAlmostEqual(sandbox_res.transformer_temperatures_c["T04"], 97.55, delta=0.2)

        # Live state MUST still be UNSTABLE and un-mutated
        self.assertFalse(self.state.latest_result.is_stable)
        self.assertEqual(self.state.transformers["T04"].temperature_c, initial_t04_temp)
        self.assertEqual(self.state.load_zones["LZ02"].curtailment_pct, 0.0)

    def test_action_execution_mutates_state(self) -> None:
        """Tests that apply_action mutates live state and recovers stability."""
        self.state.environment.ambient_temp_c = 34.0
        self.state.environment.demand_multiplier = 1.15
        self.state.load_zones["LZ02"].demand_spike_pct = 12.0
        self.engine.solve(self.state)
        self.assertFalse(self.state.latest_result.is_stable)

        action = Action(
            action_type="load_restriction",
            category=ActionCategory.IMMEDIATE_CONTROL,
            parameters={"target": "N08", "reduction_pct": 15.0},
        )
        updated_state = self.engine.apply_action(self.state, action)

        # Live state is now updated
        self.assertTrue(updated_state.latest_result.is_stable)
        self.assertEqual(updated_state.load_zones["LZ02"].curtailment_pct, 15.0)
        self.assertAlmostEqual(updated_state.transformers["T04"].temperature_c, 97.55, delta=0.2)

    def test_unknown_action_rejected(self) -> None:
        """Tests that unknown/unsupported action types are rejected."""
        action = Action(
            action_type="unknown_experimental_action",
            parameters={"target": "N08"},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertFalse(is_valid)
        self.assertIn("Unknown action type", reason or "")

        # Sandbox evaluation returns rejection with INVALID_ACTION violation
        res = self.engine.evaluate_sandbox(self.state, action)
        self.assertFalse(res.action_valid)
        self.assertFalse(res.is_stable)
        self.assertTrue(
            any(v.violation_type == ViolationType.INVALID_ACTION for v in res.violations)
        )

    def test_negative_load_restriction_rejected(self) -> None:
        """Tests that negative reduction_pct is rejected."""
        action = Action(
            action_type="load_restriction",
            parameters={"target": "N08", "reduction_pct": -10.0},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertFalse(is_valid)
        self.assertIn("cannot be negative", reason or "")

    def test_excessive_load_restriction_rejected(self) -> None:
        """Tests that reduction_pct > 100% is rejected."""
        action = Action(
            action_type="load_restriction",
            parameters={"target": "N08", "reduction_pct": 115.0},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertFalse(is_valid)
        self.assertIn("cannot exceed 100%", reason or "")

    def test_restriction_below_min_service_pct_rejected(self) -> None:
        """Tests that curtailment violating target LoadZone's min_service_pct is rejected."""
        # N08 (LZ02) has min_service_pct = 60.0%. A 45% reduction gives 55% service (< 60%).
        action = Action(
            action_type="load_restriction",
            parameters={"target": "N08", "reduction_pct": 45.0},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertFalse(is_valid)
        self.assertIn("minimum service requirement", reason or "")

        # A 15% reduction gives 85% service (>= 60%), which is allowed
        action_valid = Action(
            action_type="load_restriction",
            parameters={"target": "N08", "reduction_pct": 15.0},
        )
        is_valid_ok, reason_ok = self.engine.validate_action(self.state, action_valid)
        self.assertTrue(is_valid_ok)
        self.assertIsNone(reason_ok)

    def test_load_transfer_amount_respected(self) -> None:
        """Tests that engine uses requested transfer_mw amount instead of hardcoded 0.100 MW."""
        # Transfer 0.150 MW from N08 to N04 over L08
        action = Action(
            action_type="load_transfer",
            parameters={"from": "N08", "to": "N04", "line_id": "L08", "transfer_mw": 0.150},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertTrue(is_valid)

        res = self.engine.evaluate_sandbox(self.state, action)
        self.assertTrue(res.action_valid)
        self.assertAlmostEqual(res.line_flows_mw["L08"], 0.150, places=5)
        # N04 feeder line L01 carries LZ01 (0.315875) + 0.150 = 0.465875 MW
        self.assertAlmostEqual(res.line_flows_mw["L01"], 0.465875, places=5)
        # N08 line L05 carries LZ02 (0.447875) - 0.150 = 0.297875 MW
        self.assertAlmostEqual(res.line_flows_mw["L05"], 0.297875, places=5)

    def test_transfer_above_line_capacity_rejected(self) -> None:
        """Tests that load_transfer exceeding line capacity (1.0 MW on L08) is rejected."""
        action = Action(
            action_type="load_transfer",
            parameters={"from": "N08", "to": "N04", "line_id": "L08", "transfer_mw": 1.250},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertFalse(is_valid)
        self.assertIn("exceeds line L08 capacity", reason or "")

    def test_transfer_creating_receiving_side_overload_rejected(self) -> None:
        """Tests that load_transfer causing receiving feeder/transformer overload is rejected."""
        # Transferring 0.800 MW onto feeder N04 (within L08 capacity 1.0 MW)
        # causes severe overload and overheat on receiving transformer T01
        action = Action(
            action_type="load_transfer",
            parameters={"from": "N08", "to": "N04", "line_id": "L08", "transfer_mw": 0.800},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertFalse(is_valid)
        self.assertIn("constraint violation", (reason or "").lower())
        self.assertIn("T01", reason or "")

    def test_transfer_feeder_bus_endpoints_respected(self) -> None:
        """Tests transfer from Feeder-B bus N05 to Feeder-A bus N04 relieves L02 and increases L01."""
        action = Action(
            action_type="load_transfer",
            parameters={"from": "N05", "to": "N04", "line_id": "L08", "transfer_mw": 0.100},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertTrue(is_valid)

        res = self.engine.evaluate_sandbox(self.state, action)
        self.assertTrue(res.action_valid)
        self.assertAlmostEqual(res.line_flows_mw["L08"], 0.100, places=5)
        # L04 carries LZ01 (0.315875), L01 carries 0.315875 + 0.100 = 0.415875 MW
        self.assertAlmostEqual(res.line_flows_mw["L01"], 0.415875, places=5)
        # L05 is untouched (0.447875 MW), but incoming feeder line L02 is relieved: (0.447875 + 0.172) - 0.100 = 0.519875 MW
        self.assertAlmostEqual(res.line_flows_mw["L05"], 0.447875, places=5)
        self.assertAlmostEqual(res.line_flows_mw["L02"], 0.519875, places=5)

    def test_transfer_unsupported_endpoints_rejected(self) -> None:
        """Tests that unsupported endpoint combinations for tie-line L08 are rejected."""
        # Endpoint on Feeder-C (N09)
        action_c = Action(
            action_type="load_transfer",
            parameters={"from": "N09", "to": "N04", "line_id": "L08", "transfer_mw": 0.100},
        )
        is_valid_c, reason_c = self.engine.validate_action(self.state, action_c)
        self.assertFalse(is_valid_c)
        self.assertIn("unsupported endpoint combination", reason_c or "")

        # Both endpoints on same feeder (N08 to N05)
        action_same = Action(
            action_type="load_transfer",
            parameters={"from": "N08", "to": "N05", "line_id": "L08", "transfer_mw": 0.100},
        )
        is_valid_same, reason_same = self.engine.validate_action(self.state, action_same)
        self.assertFalse(is_valid_same)
        self.assertIn("same feeder side", reason_same or "")

    def test_transfer_critical_load_zone_rejected(self) -> None:
        """Tests that attempting to transfer critical hospital load N10 (LZ04) is rejected."""
        action = Action(
            action_type="load_transfer",
            parameters={"from": "N10", "to": "N04", "line_id": "L08", "transfer_mw": 0.050},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertFalse(is_valid)
        self.assertIn("critical load zone LZ04", reason or "")

    def test_planning_replacement_does_not_silently_perform_physical_installation(self) -> None:
        """Tests that executing planning action logs a work order without altering live hardware."""
        t04 = self.state.transformers["T04"]
        initial_rating = t04.rating_kva
        initial_age = t04.age_years
        initial_failures = t04.prior_failures

        action = Action(
            action_type="transformer_replacement",
            category=ActionCategory.PLANNING,
            parameters={"transformer_id": "T04", "additional_kva": 250.0},
        )

        # Sandbox planning evaluation assesses hypothetical upgraded outcome
        sb_res = self.engine.evaluate_sandbox(self.state, action)
        self.assertTrue(sb_res.action_valid)

        # Live execution applies planning action
        updated_state = self.engine.apply_action(self.state, action)

        # Live transformer physical hardware MUST remain unchanged
        live_t04 = updated_state.transformers["T04"]
        self.assertEqual(live_t04.rating_kva, initial_rating)
        self.assertEqual(live_t04.age_years, initial_age)
        self.assertEqual(live_t04.prior_failures, initial_failures)

        # Planning work order is logged
        self.assertEqual(len(updated_state.planning_work_orders), 1)
        self.assertEqual(
            updated_state.planning_work_orders[0]["action_type"],
            "transformer_replacement",
        )
        self.assertEqual(
            updated_state.planning_work_orders[0]["transformer_id"], "T04"
        )

    def test_frequency_and_generation_semantics_consistent(self) -> None:
        """Tests that available_generation_mw, total_demand_mw, and frequency droop are internally consistent."""
        self.assertEqual(self.state.available_generation_mw, 1.258750)
        self.assertEqual(self.state.p_gen_base, 1.258750)

        # Baseline: demand == available generation -> 0 imbalance -> 50.0000 Hz
        res_base = self.engine.solve(self.state)
        self.assertEqual(res_base.available_generation_mw, 1.258750)
        self.assertEqual(res_base.total_generation_mw, 1.258750)
        self.assertAlmostEqual(res_base.total_demand_mw, 1.258750, places=5)
        self.assertAlmostEqual(res_base.generation_demand_imbalance_mw, 0.0, places=5)
        self.assertAlmostEqual(res_base.frequency_hz, 50.0000, places=4)

        # Demand multiplier = 1.15 (+15% demand)
        self.state.environment.demand_multiplier = 1.15
        res_spike = self.engine.solve(self.state)
        expected_demand = 1.258750 * 1.15
        expected_imbalance = expected_demand - 1.258750
        expected_freq = 50.0000 - 0.4000 * (expected_imbalance / 1.258750)

        self.assertEqual(res_spike.available_generation_mw, 1.258750)
        self.assertAlmostEqual(res_spike.total_demand_mw, expected_demand, places=5)
        self.assertAlmostEqual(res_spike.generation_demand_imbalance_mw, expected_imbalance, places=5)
        self.assertAlmostEqual(res_spike.frequency_hz, expected_freq, places=4)
        self.assertAlmostEqual(res_spike.frequency_hz, 49.9400, places=4)

    def test_transformer_isolation_recalculates_shared_bank_and_overload(self) -> None:
        """Tests that isolating T04 drops its load to 0 and redistributes N05 demand to T02 creating secondary overload."""
        # Load SC01 incident conditions
        self.engine.apply_event(
            self.state,
            IncidentEvent(
                event_type="environment",
                parameters={"ambient_temp_c": 34.0, "demand_multiplier": 1.15, "storm": False},
            ),
        )
        self.engine.apply_event(
            self.state,
            IncidentEvent(
                event_type="line_failure",
                parameters={"line_id": "L08"},
            ),
        )
        self.engine.apply_event(
            self.state,
            IncidentEvent(
                event_type="demand_spike",
                parameters={"target": "N08", "increase_pct": 12.0},
            ),
        )
        action = Action(
            action_type="isolate_transformer",
            parameters={"transformer_id": "T04"},
        )
        is_valid, reason = self.engine.validate_action(self.state, action)
        self.assertTrue(is_valid)

        # Sandbox evaluation
        res = self.engine.evaluate_sandbox(self.state, action)
        self.assertTrue(res.action_valid)
        self.assertFalse(res.is_stable)

        # T04 is isolated and carries 0% load at ambient temperature (34°C)
        self.assertEqual(res.transformer_loadings_pct["T04"], 0.0)
        self.assertEqual(res.transformer_temperatures_c["T04"], 34.0)

        # T02 absorbs full N05 kVA demand (815.435 kVA / 500 kVA = 163.09%)
        self.assertAlmostEqual(res.transformer_loadings_pct["T02"], 163.0869, places=3)
        self.assertAlmostEqual(res.transformer_temperatures_c["T02"], 178.7124, places=2)

        # Violations include T02 overheat (178.71°C > 110.0°C)
        t02_viols = [v for v in res.violations if v.target_id == "T02" and v.violation_type == ViolationType.TRANSFORMER_OVERHEAT]
        self.assertEqual(len(t02_viols), 1)

        # Hospital LZ04 critical service remains 100% through surviving T02 unit
        self.assertEqual(res.critical_load_service_pct["LZ04"], 100.0)

    def test_complete_bank_isolation_cuts_downstream_service_and_violates_critical_load(self) -> None:
        """Tests that isolating all transformers serving a bus drops downstream served load to 0 and trips critical load."""
        self.state.transformers["T02"].status = TransformerStatus.ISOLATED
        self.state.transformers["T04"].status = TransformerStatus.ISOLATED

        res = self.engine.solve(self.state)
        self.assertFalse(res.is_stable)

        # Both N05 transformers isolated
        self.assertEqual(res.transformer_loadings_pct["T02"], 0.0)
        self.assertEqual(res.transformer_loadings_pct["T04"], 0.0)

        # Downstream critical hospital load LZ04 service drops to 0% and triggers violation
        self.assertEqual(res.critical_load_service_pct["LZ04"], 0.0)
        crit_viols = [v for v in res.violations if v.violation_type == ViolationType.CRITICAL_LOAD_UNSERVED]
        self.assertEqual(len(crit_viols), 1)
        self.assertIn("LZ04", crit_viols[0].description)

    def test_transformer_isolation_sandbox_and_live_semantics(self) -> None:
        """Tests that evaluate_sandbox does not mutate live state while apply_action does."""
        action = Action(
            action_type="isolate_transformer",
            parameters={"transformer_id": "T04"},
        )
        initial_status = self.state.transformers["T04"].status
        # 1. Sandbox evaluation
        sb_res = self.engine.evaluate_sandbox(self.state, action)
        self.assertTrue(sb_res.action_valid)
        self.assertEqual(self.state.transformers["T04"].status, initial_status)
        self.assertNotEqual(self.state.transformers["T04"].status, TransformerStatus.ISOLATED)

        # 2. Live execution
        updated_state = self.engine.apply_action(self.state, action)
        self.assertEqual(updated_state.transformers["T04"].status, TransformerStatus.ISOLATED)

    def test_transformer_replacement_rating_variations(self) -> None:
        """Tests that transformer replacement accurately derives resulting bank capacity across various additional_kva values."""
        # Baseline total N05 kVA = (0.619875 MW / 0.95) * 1000 = 652.5 kVA
        # 1. +100 kVA -> T04 becomes 350 kVA, bank total = 850 kVA
        act_100 = Action(
            action_type="transformer_replacement",
            parameters={"transformer_id": "T04", "additional_kva": 100.0},
        )
        res_100 = self.engine.evaluate_sandbox(self.state, act_100)
        self.assertTrue(res_100.action_valid)
        expected_loading_100 = (652.5 / 850.0) * 100.0  # ~76.76%
        self.assertAlmostEqual(res_100.transformer_loadings_pct["T02"], expected_loading_100, places=3)
        self.assertAlmostEqual(res_100.transformer_loadings_pct["T04"], expected_loading_100, places=3)

        # 2. +250 kVA -> T04 becomes 500 kVA, bank total = 1000 kVA
        act_250 = Action(
            action_type="transformer_replacement",
            parameters={"transformer_id": "T04", "additional_kva": 250.0},
        )
        res_250 = self.engine.evaluate_sandbox(self.state, act_250)
        self.assertTrue(res_250.action_valid)
        expected_loading_250 = (652.5 / 1000.0) * 100.0  # 65.25%
        self.assertAlmostEqual(res_250.transformer_loadings_pct["T02"], expected_loading_250, places=3)
        self.assertAlmostEqual(res_250.transformer_loadings_pct["T04"], expected_loading_250, places=3)

        # 3. +500 kVA -> T04 becomes 750 kVA, bank total = 1250 kVA
        act_500 = Action(
            action_type="transformer_replacement",
            parameters={"transformer_id": "T04", "additional_kva": 500.0},
        )
        res_500 = self.engine.evaluate_sandbox(self.state, act_500)
        self.assertTrue(res_500.action_valid)
        expected_loading_500 = (652.5 / 1250.0) * 100.0  # 52.2%
        self.assertAlmostEqual(res_500.transformer_loadings_pct["T02"], expected_loading_500, places=3)
        self.assertAlmostEqual(res_500.transformer_loadings_pct["T04"], expected_loading_500, places=3)

        # 4. Verify live execution logs work order and leaves physical ratings untouched
        self.assertEqual(self.state.transformers["T04"].rating_kva, 250.0)
        live_state = self.engine.apply_action(self.state, act_250)
        self.assertEqual(live_state.transformers["T04"].rating_kva, 250.0)
        self.assertEqual(len(live_state.planning_work_orders), 1)
        self.assertEqual(live_state.planning_work_orders[0]["additional_kva"], 250.0)

    def test_close_tie_line_validation_and_execution(self) -> None:
        """Tests close_tie_line rules: healthy L08 succeeds, non-tie lines rejected, tripped L08 rejected."""
        # 1. Close healthy open L08
        self.state.edges["E08"].status = LineStatus.OPEN
        act_healthy = Action(
            action_type="close_tie_line",
            parameters={"line_id": "L08", "transfer_mw": 0.100},
        )
        is_val, reason = self.engine.validate_action(self.state, act_healthy)
        self.assertTrue(is_val)

        res_close = self.engine.evaluate_sandbox(self.state, act_healthy)
        self.assertTrue(res_close.action_valid)
        self.assertEqual(res_close.line_flows_mw["L08"], 0.100)

        # 2. Close non-tie line L01 -> reject
        act_l01 = Action(
            action_type="close_tie_line",
            parameters={"line_id": "L01"},
        )
        is_val_l01, reason_l01 = self.engine.validate_action(self.state, act_l01)
        self.assertFalse(is_val_l01)
        self.assertIn("not a tie-line", reason_l01 or "")

        # 3. Close non-tie line L05 -> reject
        act_l05 = Action(
            action_type="close_tie_line",
            parameters={"line_id": "L05"},
        )
        is_val_l05, reason_l05 = self.engine.validate_action(self.state, act_l05)
        self.assertFalse(is_val_l05)
        self.assertIn("not a tie-line", reason_l05 or "")

        # 4. Close tripped L08 -> reject
        self.state.edges["E08"].status = LineStatus.TRIPPED
        act_tripped = Action(
            action_type="close_tie_line",
            parameters={"line_id": "L08"},
        )
        is_val_trip, reason_trip = self.engine.validate_action(self.state, act_tripped)
        self.assertFalse(is_val_trip)
        self.assertIn("tripped/locked out", reason_trip or "")


if __name__ == "__main__":
    unittest.main()

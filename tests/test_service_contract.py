"""
Unit tests for the GridMindService interface contract, DTO schemas, and sandbox isolation.
"""

import unittest

from gridmind.contract import ActionRequest
from gridmind.service import GridMindService


class TestServiceContract(unittest.TestCase):
    """Tests the external service contract for GridMind."""

    def setUp(self) -> None:
        self.service = GridMindService(data_dir="gridmind_data/curated")

    def test_get_grid_state_baseline(self) -> None:
        """Tests that get_grid_state returns complete typed response."""
        state = self.service.get_grid_state()
        self.assertTrue(state.is_stable)
        self.assertAlmostEqual(state.frequency_hz, 50.0000, places=3)
        self.assertEqual(len(state.nodes), 10)
        self.assertEqual(len(state.lines), 8)
        self.assertEqual(len(state.transformers), 5)
        self.assertEqual(len(state.load_zones), 4)
        self.assertEqual(len(state.active_violations), 0)

        # Test dictionary conversion
        state_dict = state.to_dict()
        self.assertIn("nodes", state_dict)
        self.assertIn("lines", state_dict)
        self.assertIn("transformers", state_dict)

    def test_get_incident_state_in_sc01(self) -> None:
        """Tests incident reporting when SC01 is loaded."""
        inc_state = self.service.load_scenario("SC01")
        self.assertFalse(inc_state.is_stable)
        self.assertEqual(inc_state.scenario_id, "SC01")
        self.assertIn("L08", inc_state.tripped_lines)
        self.assertIn("T04", inc_state.overheated_transformers)
        self.assertEqual(len(inc_state.unserved_critical_loads), 0)
        self.assertAlmostEqual(inc_state.frequency_hz, 49.9204, places=3)

    def test_evaluate_action_sandbox_isolation(self) -> None:
        """Tests that evaluate_action predicts outcomes on a sandbox without mutating live state."""
        self.service.load_scenario("SC01")

        # Initial live state is unstable
        live_before = self.service.get_grid_state()
        self.assertFalse(live_before.is_stable)
        t04_temp_before = next(
            t.temperature_c for t in live_before.transformers if t.transformer_id == "T04"
        )
        self.assertAlmostEqual(t04_temp_before, 112.65, delta=0.2)

        # Evaluate load restriction action in sandbox
        eval_resp = self.service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 15.0},
            )
        )

        # Evaluation predicts stability
        self.assertTrue(eval_resp.action_valid)
        self.assertTrue(eval_resp.is_stable)
        self.assertEqual(len(eval_resp.violations), 0)
        self.assertAlmostEqual(eval_resp.predicted_transformer_temperatures_c["T04"], 97.55, delta=0.2)

        # Live state MUST remain unchanged (still unstable, T04 still 112.65°C)
        live_after = self.service.get_grid_state()
        self.assertFalse(live_after.is_stable)
        t04_temp_after = next(
            t.temperature_c for t in live_after.transformers if t.transformer_id == "T04"
        )
        self.assertEqual(t04_temp_before, t04_temp_after)

    def test_evaluate_invalid_action_rejected(self) -> None:
        """Tests evaluation of an invalid action (transferring over tripped L08)."""
        self.service.load_scenario("SC01")

        eval_resp = self.service.evaluate_action(
            ActionRequest(
                action_type="load_transfer",
                parameters={"from": "N08", "to": "N04", "line_id": "L08", "mw": 0.100},
            )
        )
        self.assertFalse(eval_resp.action_valid)
        self.assertFalse(eval_resp.is_stable)
        self.assertIsNotNone(eval_resp.rejection_reason)
        self.assertIn("tripped", eval_resp.rejection_reason.lower())

    def test_execute_action_mutates_live_state(self) -> None:
        """Tests that execute_action applies the action to live state and recovers stability."""
        self.service.load_scenario("SC01")

        exec_resp = self.service.execute_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 15.0},
            )
        )
        self.assertTrue(exec_resp.success)
        self.assertTrue(exec_resp.is_stable)
        self.assertIsNone(exec_resp.error_message)
        self.assertAlmostEqual(exec_resp.transformer_temperatures_c["T04"], 97.55, delta=0.2)

        # Confirm live state is now permanently updated
        live_state = self.service.get_grid_state()
        self.assertTrue(live_state.is_stable)
        n08_lz = next(lz for lz in live_state.load_zones if lz.load_id == "LZ02")
        self.assertEqual(n08_lz.curtailment_pct, 15.0)

    def test_execute_invalid_action_rejected(self) -> None:
        """Tests that executing an invalid action is rejected with error."""
        self.service.load_scenario("SC01")

        # Attempt to restrict critical hospital load
        exec_resp = self.service.execute_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N10", "reduction_pct": 20.0},
            )
        )
        self.assertFalse(exec_resp.success)
        self.assertIsNotNone(exec_resp.error_message)
        self.assertIn("CRITICAL", exec_resp.error_message)

    def test_get_last_simulation_result(self) -> None:
        """Tests get_last_simulation_result retrieval."""
        self.service.load_scenario("SC01")
        last_res = self.service.get_last_simulation_result()
        self.assertIsNotNone(last_res)
        self.assertFalse(last_res.is_stable)
        self.assertAlmostEqual(last_res.predicted_transformer_temperatures_c["T04"], 112.65, delta=0.2)

    def test_service_rejects_unknown_action_type(self) -> None:
        """Tests that GridMindService rejects requests with unknown action types."""
        req = ActionRequest(
            action_type="unrecognized_action",
            parameters={"target": "N08"},
        )
        eval_resp = self.service.evaluate_action(req)
        self.assertFalse(eval_resp.action_valid)
        self.assertFalse(eval_resp.is_stable)
        self.assertIn("Unknown action type", eval_resp.rejection_reason or "")

        exec_resp = self.service.execute_action(req)
        self.assertFalse(exec_resp.success)
        self.assertIn("Unknown action type", exec_resp.error_message or "")

    def test_get_last_simulation_result_tracks_sandbox_and_execution(self) -> None:
        """Tests that get_last_simulation_result tracks sandbox evaluations without mutating live state."""
        self.service.load_scenario("SC01")
        # 1. Initially SC01 incident result is present
        inc_res = self.service.get_last_simulation_result()
        self.assertIsNotNone(inc_res)
        self.assertFalse(inc_res.is_stable)
        self.assertAlmostEqual(inc_res.predicted_transformer_temperatures_c["T04"], 112.65, delta=0.2)

        # 2. Evaluate candidate action in sandbox
        eval_resp = self.service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 15.0},
            )
        )
        self.assertTrue(eval_resp.is_stable)

        # 3. get_last_simulation_result returns the sandbox evaluation result
        last_res = self.service.get_last_simulation_result()
        self.assertIsNotNone(last_res)
        self.assertTrue(last_res.is_stable)
        self.assertAlmostEqual(last_res.predicted_transformer_temperatures_c["T04"], 97.55, delta=0.2)

        # 4. Live state remains unchanged (still unstable, 112.65°C)
        live_state = self.service.get_grid_state()
        self.assertFalse(live_state.is_stable)
        t04 = next(t for t in live_state.transformers if t.transformer_id == "T04")
        self.assertAlmostEqual(t04.temperature_c, 112.65, delta=0.2)

        # 5. Execute action updates live state and latest simulation result
        self.service.execute_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 15.0},
            )
        )
        exec_last_res = self.service.get_last_simulation_result()
        self.assertIsNotNone(exec_last_res)
        self.assertTrue(exec_last_res.is_stable)
        self.assertAlmostEqual(exec_last_res.predicted_transformer_temperatures_c["T04"], 97.55, delta=0.2)

    def test_load_scenario_rejects_unknown_id_without_state_mutation(self) -> None:
        """Tests that loading an unsupported scenario ID raises ValueError and leaves existing state unchanged."""
        self.service.load_scenario("SC01")
        self.assertEqual(self.service.active_scenario_id, "SC01")
        live_before = self.service.get_grid_state()
        self.assertFalse(live_before.is_stable)

        # Attempt to load unknown scenario
        with self.assertRaises(ValueError) as ctx:
            self.service.load_scenario("UNKNOWN_SCENARIO_99")
        self.assertIn("Unsupported scenario ID", str(ctx.exception))

        # Scenario ID and live state MUST remain completely unchanged
        self.assertEqual(self.service.active_scenario_id, "SC01")
        live_after = self.service.get_grid_state()
        self.assertFalse(live_after.is_stable)
        t04_before = next(t.temperature_c for t in live_before.transformers if t.transformer_id == "T04")
        t04_after = next(t.temperature_c for t in live_after.transformers if t.transformer_id == "T04")
        self.assertEqual(t04_before, t04_after)

    def test_close_tie_line_vs_load_transfer_semantics(self) -> None:
        """Explicitly tests the distinction between non-parameterized close_tie_line and parameterized load_transfer."""
        self.service.load_scenario("SC01-B")

        # 1. Non-parameterized close_tie_line: takes only line_id, yields default 0.100 MW flow
        eval_close = self.service.evaluate_action(
            ActionRequest(
                action_type="close_tie_line",
                parameters={"line_id": "L08"},
            )
        )
        self.assertTrue(eval_close.action_valid)
        self.assertTrue(eval_close.is_stable)
        self.assertAlmostEqual(eval_close.predicted_line_loadings_pct["L08"], 10.0, delta=0.1)

        # 2. Parameterized load_transfer: takes line_id, source, destination, transfer_mw
        eval_transfer_50kw = self.service.evaluate_action(
            ActionRequest(
                action_type="load_transfer",
                parameters={
                    "line_id": "L08",
                    "source": "N08",
                    "destination": "N04",
                    "transfer_mw": 0.050,
                },
            )
        )
        self.assertTrue(eval_transfer_50kw.action_valid)
        self.assertAlmostEqual(eval_transfer_50kw.predicted_line_loadings_pct["L08"], 5.0, delta=0.1)

        eval_transfer_100kw = self.service.evaluate_action(
            ActionRequest(
                action_type="load_transfer",
                parameters={
                    "line_id": "L08",
                    "source": "N08",
                    "destination": "N04",
                    "transfer_mw": 0.100,
                },
            )
        )
        self.assertTrue(eval_transfer_100kw.action_valid)
        self.assertAlmostEqual(eval_transfer_100kw.predicted_line_loadings_pct["L08"], 10.0, delta=0.1)

    def test_reduction_pct_numeric_percentage_semantics(self) -> None:
        """Tests that load_restriction interprets reduction_pct as numeric percentage and rejects strings/booleans."""
        self.service.load_scenario("SC01")

        # 1. 15 -> 15% reduction
        eval_15 = self.service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 15},
            )
        )
        self.assertTrue(eval_15.action_valid)
        self.assertTrue(eval_15.is_stable)
        self.assertAlmostEqual(eval_15.predicted_transformer_temperatures_c["T04"], 97.55, delta=0.2)

        # 2. 5 -> 5% reduction
        eval_5 = self.service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 5},
            )
        )
        self.assertTrue(eval_5.action_valid)
        self.assertTrue(eval_5.is_stable)
        self.assertAlmostEqual(eval_5.predicted_transformer_temperatures_c["T04"], 107.45, delta=0.2)

        # 3. 0.15 -> 0.15% reduction (NOT 15%, so T04 remains overheated at 112.49°C)
        eval_015 = self.service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": 0.15},
            )
        )
        self.assertTrue(eval_015.action_valid)
        self.assertFalse(eval_015.is_stable)
        self.assertAlmostEqual(eval_015.predicted_transformer_temperatures_c["T04"], 112.49, delta=0.2)

        # 4. "15" string -> rejected
        eval_15_str = self.service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": "15"},
            )
        )
        self.assertFalse(eval_15_str.action_valid)

        # 5. "0.15" string -> rejected
        eval_015_str = self.service.evaluate_action(
            ActionRequest(
                action_type="load_restriction",
                parameters={"target": "N08", "reduction_pct": "0.15"},
            )
        )
        self.assertFalse(eval_015_str.action_valid)


if __name__ == "__main__":
    unittest.main()

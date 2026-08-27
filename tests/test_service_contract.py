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

    def test_service_rejects_min_service_pct_violation(self) -> None:
        """Tests that service rejects load restrictions violating min_service_pct."""
        req = ActionRequest(
            action_type="load_restriction",
            parameters={"target": "N08", "reduction_pct": 50.0},
        )
        eval_resp = self.service.evaluate_action(req)
        self.assertFalse(eval_resp.action_valid)
        self.assertIn("minimum service requirement", eval_resp.rejection_reason or "")


if __name__ == "__main__":
    unittest.main()

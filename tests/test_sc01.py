"""
End-to-end unit tests for Scenario SC01 lifecycle and candidate action evaluations.
"""

import unittest

from gridmind.engine import GridMindEngine
from gridmind.loader import load_curated_grid
from gridmind.models import Action, ActionCategory, IncidentEvent, ViolationType
from gridmind.scenario import run_scenario_sc01


class TestScenarioSC01(unittest.TestCase):
    """Verifies complete deterministic lifecycle of SC01."""

    def test_sc01_full_lifecycle(self) -> None:
        report = run_scenario_sc01("gridmind_data/curated")

        # 1. Baseline Verification
        base = report["baseline"]
        self.assertTrue(base["is_stable"])
        self.assertEqual(base["violations_count"], 0)
        self.assertAlmostEqual(base["freq_hz"], 49.9400, places=3)
        self.assertAlmostEqual(base["total_demand_kw"], 1447.56, delta=0.5)
        self.assertAlmostEqual(base["t04_temp_c"], 101.71, delta=0.2)
        self.assertAlmostEqual(base["t02_temp_c"], 90.38, delta=0.2)
        self.assertEqual(base["critical_hospital_service_pct"], 100.0)

        # 2. Incident Verification
        inc = report["incident"]
        self.assertFalse(inc["is_stable"])
        self.assertEqual(len(inc["violations"]), 1)
        self.assertIn("T04", inc["violations"][0])
        self.assertAlmostEqual(inc["freq_hz"], 49.9204, places=3)
        self.assertAlmostEqual(inc["total_demand_kw"], 1509.37, delta=0.5)
        self.assertAlmostEqual(inc["t04_load_pct"], 116.22, delta=0.2)
        self.assertAlmostEqual(inc["t04_temp_c"], 112.65, delta=0.2)
        self.assertAlmostEqual(inc["t02_temp_c"], 99.48, delta=0.2)
        self.assertEqual(inc["critical_hospital_service_pct"], 100.0)

        # 3. Sandbox Evaluations
        sb = report["sandbox_evaluations"]

        # Action 1: Load Restriction (-15%)
        act1 = sb["load_restriction_15pct"]
        self.assertTrue(act1["action_valid"])
        self.assertTrue(act1["is_stable"])
        self.assertEqual(act1["violations_count"], 0)
        self.assertAlmostEqual(act1["t04_temp_c"], 97.55, delta=0.2)

        # Action 2: Load Transfer via tripped L08 (Must be rejected)
        act2 = sb["load_transfer_l08"]
        self.assertFalse(act2["action_valid"])
        self.assertFalse(act2["is_stable"])
        self.assertIn("tripped", act2["rejection_reason"].lower())

        # Action 3: Isolate T04 (Secondary Overload on T02)
        act3 = sb["isolate_t04"]
        self.assertTrue(act3["action_valid"])
        self.assertFalse(act3["is_stable"])
        self.assertAlmostEqual(act3["t02_load_pct"], 163.09, delta=0.5)
        self.assertGreater(act3["t02_temp_c"], 110.0)
        self.assertAlmostEqual(act3["t02_temp_c"], 178.71, delta=0.5)

        # Action 4: Replace/Uprate T04 (500 kVA)
        act4 = sb["replace_t04_500kva"]
        self.assertTrue(act4["action_valid"])
        self.assertTrue(act4["is_stable"])
        self.assertAlmostEqual(act4["t04_temp_c"], 75.56, delta=0.2)
        self.assertAlmostEqual(act4["t02_temp_c"], 75.56, delta=0.2)

        # 4. Sandbox Isolation Invariance
        self.assertTrue(report["sandbox_isolation_verified"])

        # 5. Live State Execution
        exec_res = report["post_execution"]
        self.assertTrue(exec_res["is_stable"])
        self.assertAlmostEqual(exec_res["t04_temp_c"], 97.55, delta=0.2)
        self.assertEqual(exec_res["critical_hospital_service_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()

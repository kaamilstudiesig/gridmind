"""
Unit tests for data ingestion and loader module.
"""

import unittest
from pathlib import Path

from gridmind.loader import load_curated_grid, load_scenario
from gridmind.models import LineStatus, LoadPriority, NodeType, TransformerStatus


class TestLoader(unittest.TestCase):
    """Tests loading of curated CSV and JSON dataset."""

    def setUp(self) -> None:
        self.state = load_curated_grid("gridmind_data/curated")

    def test_nodes_loaded(self) -> None:
        self.assertEqual(len(self.state.nodes), 10)
        # Check substations
        self.assertIn("N01", self.state.nodes)
        self.assertEqual(self.state.nodes["N01"].node_type, NodeType.SUBSTATION)
        self.assertEqual(self.state.nodes["N01"].voltage_kv, 66.0)
        # Check feeders
        self.assertIn("N04", self.state.nodes)
        self.assertEqual(self.state.nodes["N04"].node_type, NodeType.FEEDER)
        self.assertEqual(self.state.nodes["N04"].voltage_kv, 11.0)
        # Check load zones
        self.assertIn("N08", self.state.nodes)
        self.assertEqual(self.state.nodes["N08"].node_type, NodeType.LOAD_ZONE)

    def test_edges_and_tie_line_loaded(self) -> None:
        self.assertEqual(len(self.state.edges), 8)
        # L08 emergency tie-line checks
        l08 = next((e for e in self.state.edges.values() if e.line_id == "L08"), None)
        self.assertIsNotNone(l08)
        self.assertTrue(l08.is_tie_line)
        self.assertEqual(l08.status, LineStatus.OPEN)

        # Standard lines must be CLOSED (healthy)
        l01 = next((e for e in self.state.edges.values() if e.line_id == "L01"), None)
        self.assertIsNotNone(l01)
        self.assertEqual(l01.status, LineStatus.CLOSED)
        self.assertFalse(l01.is_tie_line)

    def test_transformers_loaded(self) -> None:
        self.assertEqual(len(self.state.transformers), 5)
        t02 = self.state.transformers["T02"]
        self.assertEqual(t02.node_id, "N05")
        self.assertEqual(t02.rating_kva, 500.0)

        t04 = self.state.transformers["T04"]
        self.assertEqual(t04.node_id, "N05")
        self.assertEqual(t04.rating_kva, 250.0)
        self.assertEqual(t04.status, TransformerStatus.OVERLOAD_RISK)

    def test_load_zones_and_power_scale(self) -> None:
        self.assertEqual(len(self.state.load_zones), 4)
        lz04 = self.state.load_zones["LZ04"]
        self.assertEqual(lz04.node_id, "N10")
        self.assertEqual(lz04.priority, LoadPriority.CRITICAL)

        # Check total base demand sum
        total_base = sum(lz.base_mw for lz in self.state.load_zones.values())
        self.assertAlmostEqual(total_base, 1.258750, places=5)
        self.assertAlmostEqual(self.state.p_gen_base, 1.258750, places=5)

    def test_scenario_json_loading(self) -> None:
        scenario = load_scenario("gridmind_data/curated/seed_scenario_SC01.json")
        self.assertEqual(scenario["scenario_id"], "SC01")
        self.assertIn("environment", scenario)
        self.assertIn("events", scenario)
        self.assertIn("hard_constraints", scenario)
        self.assertIn("action_space", scenario)


if __name__ == "__main__":
    unittest.main()

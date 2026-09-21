"""C0 drainage and benchmark-verification tests."""

from pathlib import Path
import unittest

from src.constructability import _drainage_metrics, load_constructability_config, run_c0_verification


ROOT = Path(__file__).resolve().parents[1]


def drainage(cells, found=True):
    return {"path_found": found, "path_cells": cells}


class ConstructabilityTests(unittest.TestCase):
    def test_c0_config_and_gravity_metrics(self) -> None:
        config = load_constructability_config(ROOT / "config" / "constructability.json")
        self.assertEqual(config["constraint_set_id"], "C0")
        passing = _drainage_metrics(drainage([[0, 0, 2], [1, 0, 2], [2, 0, 1]]), 0.1, 0.01)
        self.assertTrue(passing["drainage_gravity_pass"])
        self.assertAlmostEqual(passing["horizontal_run_m"], 0.1)
        self.assertAlmostEqual(passing["actual_net_drop_m"], 0.1)
        level = _drainage_metrics(drainage([[0, 0, 1], [1, 0, 1]]), 0.1, 0.01)
        self.assertFalse(level["minimum_slope_pass"])
        uphill = _drainage_metrics(drainage([[0, 0, 2], [1, 0, 1], [2, 0, 2]]), 0.1, 0.01)
        self.assertFalse(uphill["monotonic_flow_pass"])
        vertical = _drainage_metrics(drainage([[0, 0, 2], [0, 0, 1]]), 0.1, 0.01)
        self.assertEqual(vertical["horizontal_run_m"], 0.0)
        self.assertIsNone(vertical["effective_average_slope_ratio"])
        self.assertTrue(vertical["drainage_gravity_pass"])

    def test_both_baselines_verify_under_c0(self) -> None:
        result = run_c0_verification(ROOT / "experiments" / "scenarios", ROOT / "experiments" / "demands", ROOT / "config" / "mep_systems.json", ROOT / "config" / "constructability.json")
        self.assertEqual(set(result["methods"]), {"B0", "B1"})
        self.assertEqual(len(result["methods"]["B0"]["cases"]), 12)
        self.assertEqual(sum(case["successful_connection_count"] for case in result["methods"]["B0"]["cases"]), 292)
        self.assertEqual(sum(case["successful_connection_count"] for case in result["methods"]["B1"]["cases"]), 144)

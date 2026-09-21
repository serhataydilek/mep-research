"""Focused tests for deterministic Phase 2A benchmark scenarios and metrics."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.benchmark import calculate_metrics, load_scenario_results, write_scenario_metrics
from src.config import load_building_config, shaft_clear_bounds, shaft_outer_bounds
from src.ifc_model import create_ifc_model


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIRECTORY = ROOT / "experiments" / "scenarios"
SCENARIO_FILES = ("s01_low.json", "s02_medium.json", "s03_high.json", "s04_extreme.json")


class BenchmarkScenarioTests(unittest.TestCase):
    def test_all_four_scenario_files_exist_and_manifest_references_them(self) -> None:
        manifest = json.loads((SCENARIOS_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(tuple(entry["config_file"] for entry in manifest["scenarios"]), SCENARIO_FILES)
        self.assertTrue(all((SCENARIOS_DIRECTORY / filename).is_file() for filename in SCENARIO_FILES))

    def test_every_scenario_validates_and_generates_the_expected_ifc_shell(self) -> None:
        for filename in SCENARIO_FILES:
            with self.subTest(filename=filename):
                config = load_building_config(SCENARIOS_DIRECTORY / filename)
                model = create_ifc_model(config)
                metrics = calculate_metrics(config)

                self.assertEqual(model.schema, "IFC4")
                self.assertEqual(len(model.by_type("IfcSlab")), config["floors"])
                self.assertEqual(len(model.by_type("IfcWall")), 8 * config["floors"])
                self.assertEqual(
                    len(model.by_type("IfcColumn")),
                    metrics["column_count_per_floor"] * config["floors"],
                )
                self.assertEqual(len(model.by_type("IfcSpace")), config["floors"])
                self.assertEqual(len(model.by_type("IfcOpeningElement")), config["floors"])

    def test_metrics_are_deterministic_and_match_geometry_helpers(self) -> None:
        for filename in SCENARIO_FILES:
            with self.subTest(filename=filename):
                config = load_building_config(SCENARIOS_DIRECTORY / filename)
                first_metrics = calculate_metrics(config)
                second_metrics = calculate_metrics(config)
                clear_bounds = shaft_clear_bounds(
                    config["width_m"],
                    config["length_m"],
                    config["shaft"]["width_m"],
                    config["shaft"]["length_m"],
                )
                outer_min_x, outer_max_x, outer_min_y, outer_max_y = shaft_outer_bounds(
                    clear_bounds, config["wall_thickness_m"]
                )

                self.assertEqual(first_metrics, second_metrics)
                self.assertEqual(first_metrics["gross_floor_area_m2"], config["width_m"] * config["length_m"])
                self.assertEqual(
                    first_metrics["interior_floor_area_m2"],
                    (config["width_m"] - 2 * config["wall_thickness_m"])
                    * (config["length_m"] - 2 * config["wall_thickness_m"]),
                )
                self.assertEqual(
                    first_metrics["shaft_outer_area_m2"],
                    (outer_max_x - outer_min_x) * (outer_max_y - outer_min_y),
                )
                self.assertGreater(first_metrics["geometric_obstruction_ratio"], 0)
                self.assertLess(first_metrics["geometric_obstruction_ratio"], 1)
                self.assertGreater(first_metrics["plenum_height_m"], 0)
                self.assertGreater(first_metrics["routing_volume_proxy_m3"], 0)

    def test_manifest_results_are_ordered_and_output_is_byte_identical(self) -> None:
        results = load_scenario_results(SCENARIOS_DIRECTORY)
        ratios = [result["geometric_obstruction_ratio"] for result in results]

        self.assertEqual([result["scenario_id"] for result in results], ["s01", "s02", "s03", "s04"])
        self.assertEqual(ratios, sorted(ratios))
        self.assertEqual(len(set(ratios)), 4)
        with TemporaryDirectory() as directory:
            first_output = Path(directory) / "first.json"
            second_output = Path(directory) / "second.json"
            write_scenario_metrics(SCENARIOS_DIRECTORY, first_output)
            write_scenario_metrics(SCENARIOS_DIRECTORY, second_output)
            self.assertEqual(first_output.read_bytes(), second_output.read_bytes())

    def test_default_building_configuration_remains_valid(self) -> None:
        config = load_building_config(ROOT / "config" / "building.json")

        self.assertEqual(config["floors"], 1)


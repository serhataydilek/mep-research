"""Focused tests for deterministic pre-routing MEP demand benchmark cases."""

from __future__ import annotations

import json
from math import isfinite, pi
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.config import load_building_config, shaft_clear_bounds
from src.demand import (
    SYSTEM_ORDER,
    _point_is_valid_terminal_candidate,
    generate_benchmark_cases,
    load_demand_profiles,
    load_service_definitions,
    write_routing_demands,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIRECTORY = ROOT / "experiments" / "scenarios"
DEMANDS_DIRECTORY = ROOT / "experiments" / "demands"
SYSTEMS_PATH = ROOT / "config" / "mep_systems.json"
DEMAND_FILES = ("d01_low.json", "d02_medium.json", "d03_high.json")


class DemandBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = generate_benchmark_cases(SCENARIOS_DIRECTORY, DEMANDS_DIRECTORY, SYSTEMS_PATH)
        cls.case_by_id = {case["case_id"]: case for case in cls.cases}
        cls.config_by_scenario = {
            scenario_id: load_building_config(SCENARIOS_DIRECTORY / filename)
            for scenario_id, filename in (
                ("s01", "s01_low.json"),
                ("s02", "s02_medium.json"),
                ("s03", "s03_high.json"),
                ("s04", "s04_extreme.json"),
            )
        }

    def test_demand_files_and_manifest_references_exist(self) -> None:
        manifest = json.loads((DEMANDS_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(tuple(entry["config_file"] for entry in manifest["profiles"]), DEMAND_FILES)
        self.assertTrue(all((DEMANDS_DIRECTORY / filename).is_file() for filename in DEMAND_FILES))

    def test_system_definitions_and_profile_counts_are_exact(self) -> None:
        systems = load_service_definitions(SYSTEMS_PATH)
        profiles = load_demand_profiles(DEMANDS_DIRECTORY)

        self.assertEqual(tuple(systems), SYSTEM_ORDER)
        self.assertEqual(
            systems,
            {
                "hvac": {"envelope_type": "rectangular", "width_m": 0.4, "height_m": 0.25, "flow_direction": "source_to_terminal"},
                "drainage": {"envelope_type": "circular", "diameter_m": 0.1, "flow_direction": "terminal_to_source"},
                "water": {"envelope_type": "circular", "diameter_m": 0.05, "flow_direction": "source_to_terminal"},
                "fire": {"envelope_type": "circular", "diameter_m": 0.04, "flow_direction": "source_to_terminal"},
                "electrical": {"envelope_type": "rectangular", "width_m": 0.3, "height_m": 0.1, "flow_direction": "source_to_terminal"},
            },
        )
        self.assertEqual(
            [sum(profile["systems"][system]["terminal_count"] for system in SYSTEM_ORDER) for profile in profiles],
            [13, 24, 36],
        )
        self.assertEqual(
            [
                {system: profile["systems"][system]["terminal_count"] for system in SYSTEM_ORDER}
                for profile in profiles
            ],
            [
                {"hvac": 3, "drainage": 2, "water": 2, "fire": 3, "electrical": 3},
                {"hvac": 5, "drainage": 4, "water": 4, "fire": 6, "electrical": 5},
                {"hvac": 8, "drainage": 6, "water": 6, "fire": 8, "electrical": 8},
            ],
        )

    def test_cross_product_case_count_ids_and_determinism(self) -> None:
        second_cases = generate_benchmark_cases(SCENARIOS_DIRECTORY, DEMANDS_DIRECTORY, SYSTEMS_PATH)

        self.assertEqual(len(self.cases), 12)
        self.assertEqual(
            [case["case_id"] for case in self.cases],
            [f"s0{scenario}-d0{demand}" for scenario in range(1, 5) for demand in range(1, 4)],
        )
        self.assertEqual(self.cases, second_cases)

    def test_cli_output_is_byte_identical(self) -> None:
        with TemporaryDirectory() as directory:
            first_output = Path(directory) / "first.json"
            second_output = Path(directory) / "second.json"
            write_routing_demands(SCENARIOS_DIRECTORY, DEMANDS_DIRECTORY, SYSTEMS_PATH, first_output)
            write_routing_demands(SCENARIOS_DIRECTORY, DEMANDS_DIRECTORY, SYSTEMS_PATH, second_output)
            self.assertEqual(first_output.read_bytes(), second_output.read_bytes())

    def test_source_and_terminal_anchors_are_unique_and_geometrically_valid(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                config = self.config_by_scenario[case["scenario_id"]]
                clear_bounds = shaft_clear_bounds(
                    config["width_m"],
                    config["length_m"],
                    config["shaft"]["width_m"],
                    config["shaft"]["length_m"],
                )
                routing_z_min = config["slab_thickness_m"] + config["ceiling_height_m"]
                routing_z_max = config["floor_to_floor_m"]
                source_coordinates = set()
                terminal_coordinates = set()
                self.assertEqual(len(case["source_anchors"]), 5)
                for anchor in case["source_anchors"]:
                    coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
                    self.assertTrue(all(isfinite(value) for value in coordinates))
                    self.assertTrue(clear_bounds[0] < anchor["x_m"] < clear_bounds[1])
                    self.assertTrue(clear_bounds[2] < anchor["y_m"] < clear_bounds[3])
                    self.assertTrue(routing_z_min < anchor["z_m"] < routing_z_max)
                    source_coordinates.add(coordinates)
                for anchor in case["terminal_anchors"]:
                    coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
                    self.assertTrue(all(isfinite(value) for value in coordinates))
                    self.assertTrue(_point_is_valid_terminal_candidate(config, anchor["x_m"], anchor["y_m"]))
                    self.assertTrue(routing_z_min < anchor["z_m"] < routing_z_max)
                    terminal_coordinates.add(coordinates)
                self.assertEqual(len(source_coordinates), 5)
                self.assertEqual(len(terminal_coordinates), len(case["terminal_anchors"]))

    def test_connections_are_complete_and_follow_service_direction(self) -> None:
        systems = load_service_definitions(SYSTEMS_PATH)
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                anchors = {anchor["anchor_id"] for anchor in [*case["source_anchors"], *case["terminal_anchors"]]}
                requests = case["connection_requests"]
                self.assertEqual(len(requests), len(case["terminal_anchors"]))
                self.assertEqual(len({request["connection_id"] for request in requests}), len(requests))
                for request in requests:
                    self.assertIn(request["start_anchor"], anchors)
                    self.assertIn(request["end_anchor"], anchors)
                    source_id = f"source/{request['system']}"
                    if systems[request["system"]]["flow_direction"] == "terminal_to_source":
                        self.assertEqual(request["end_anchor"], source_id)
                        self.assertTrue(request["start_anchor"].startswith("terminal/"))
                    else:
                        self.assertEqual(request["start_anchor"], source_id)
                        self.assertTrue(request["end_anchor"].startswith("terminal/"))

    def test_profiles_are_nested_by_system_with_correct_metrics(self) -> None:
        for scenario_id in self.config_by_scenario:
            low_case = self.case_by_id[f"{scenario_id}-d01"]
            medium_case = self.case_by_id[f"{scenario_id}-d02"]
            high_case = self.case_by_id[f"{scenario_id}-d03"]
            for system in SYSTEM_ORDER:
                low_ids = {
                    anchor["anchor_id"] for anchor in low_case["terminal_anchors"] if anchor["system"] == system
                }
                medium_ids = {
                    anchor["anchor_id"] for anchor in medium_case["terminal_anchors"] if anchor["system"] == system
                }
                high_ids = {
                    anchor["anchor_id"] for anchor in high_case["terminal_anchors"] if anchor["system"] == system
                }
                self.assertTrue(low_ids <= medium_ids <= high_ids)

        low_metrics = self.case_by_id["s01-d01"]["demand_metrics"]
        interior_area = self.case_by_id["s01-d01"]["building_metrics"]["interior_floor_area_m2"]
        self.assertAlmostEqual(low_metrics["terminal_density_per_100m2"], 13 / interior_area * 100)
        self.assertAlmostEqual(low_metrics["connection_density_per_100m2"], 13 / interior_area * 100)
        expected_cross_section = 3 * 0.4 * 0.25 + 2 * pi * 0.1**2 / 4 + 2 * pi * 0.05**2 / 4 + 3 * pi * 0.04**2 / 4 + 3 * 0.3 * 0.1
        self.assertAlmostEqual(low_metrics["aggregate_nominal_cross_section_m2"], expected_cross_section)

    def test_default_building_configuration_remains_valid(self) -> None:
        config = load_building_config(ROOT / "config" / "building.json")

        self.assertEqual(config["floors"], 1)

"""Focused tests for deterministic pre-routing MEP demand benchmark cases."""

from __future__ import annotations

import copy
import json
from math import isfinite, pi
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.benchmark import calculate_metrics
from src.config import load_building_config, shaft_clear_bounds, shaft_outer_bounds
from src.demand import (
    SYSTEM_ORDER,
    _point_is_valid_service_candidate,
    generate_benchmark_cases,
    load_demand_profiles,
    load_service_definitions,
    preflight_service_feasibility,
    service_routing_margins,
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
        cls.systems = load_service_definitions(SYSTEMS_PATH)
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
        systems = self.systems
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

    def test_service_margins_and_plenum_preflight_are_service_aware(self) -> None:
        hvac = service_routing_margins(self.systems["hvac"], 0.05)
        drainage = service_routing_margins(self.systems["drainage"], 0.05)

        self.assertEqual(hvac, {
            "planar_half_extent_m": 0.2,
            "vertical_half_extent_m": 0.125,
            "required_planar_margin_m": 0.25,
            "required_vertical_margin_m": 0.175,
        })
        self.assertEqual(drainage["planar_half_extent_m"], 0.05)
        self.assertEqual(drainage["vertical_half_extent_m"], 0.05)
        self.assertEqual(
            {
                system: service_routing_margins(definition, 0.05)
                for system, definition in self.systems.items()
            },
            {
                "hvac": {"planar_half_extent_m": 0.2, "vertical_half_extent_m": 0.125, "required_planar_margin_m": 0.25, "required_vertical_margin_m": 0.175},
                "drainage": {"planar_half_extent_m": 0.05, "vertical_half_extent_m": 0.05, "required_planar_margin_m": 0.1, "required_vertical_margin_m": 0.1},
                "water": {"planar_half_extent_m": 0.025, "vertical_half_extent_m": 0.025, "required_planar_margin_m": 0.07500000000000001, "required_vertical_margin_m": 0.07500000000000001},
                "fire": {"planar_half_extent_m": 0.02, "vertical_half_extent_m": 0.02, "required_planar_margin_m": 0.07, "required_vertical_margin_m": 0.07},
                "electrical": {"planar_half_extent_m": 0.15, "vertical_half_extent_m": 0.05, "required_planar_margin_m": 0.2, "required_vertical_margin_m": 0.1},
            },
        )
        for config in self.config_by_scenario.values():
            self.assertTrue(preflight_service_feasibility(config, self.systems)["all_service_envelopes_fit_plenum"])

        too_small = copy.deepcopy(self.config_by_scenario["s01"])
        too_small["ceiling_height_m"] = 4.0
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            preflight_service_feasibility(too_small, self.systems)

    def test_s04_has_half_metre_plenum_and_preserves_required_ordering(self) -> None:
        s04_metrics = calculate_metrics(self.config_by_scenario["s04"])
        s03_metrics = calculate_metrics(self.config_by_scenario["s03"])
        all_ratios = [calculate_metrics(config)["geometric_obstruction_ratio"] for config in self.config_by_scenario.values()]

        self.assertEqual(s04_metrics["plenum_height_m"], 0.5)
        self.assertEqual(s04_metrics["geometric_obstruction_ratio"], max(all_ratios))
        self.assertLess(s04_metrics["routing_volume_proxy_m3"], s03_metrics["routing_volume_proxy_m3"])

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

    def test_source_egress_and_terminal_anchors_are_unique_and_geometrically_valid(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                config = self.config_by_scenario[case["scenario_id"]]
                clear_bounds = shaft_clear_bounds(
                    config["width_m"],
                    config["length_m"],
                    config["shaft"]["width_m"],
                    config["shaft"]["length_m"],
                )
                source_coordinates = set()
                egress_coordinates = set()
                terminal_coordinates = set()
                self.assertEqual(len(case["source_anchors"]), 5)
                self.assertEqual(len(case["egress_anchors"]), 5)
                self.assertEqual(len(case["fixed_breakouts"]), 5)
                for anchor in case["source_anchors"]:
                    coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
                    self.assertTrue(all(isfinite(value) for value in coordinates))
                    self.assertTrue(clear_bounds[0] < anchor["x_m"] < clear_bounds[1])
                    self.assertTrue(clear_bounds[2] < anchor["y_m"] < clear_bounds[3])
                    margin = service_routing_margins(self.systems[anchor["system"]], config["routing"]["clearance_m"])
                    self.assertGreaterEqual(anchor["x_m"], clear_bounds[0] + margin["required_planar_margin_m"])
                    self.assertLessEqual(anchor["x_m"], clear_bounds[1] - margin["required_planar_margin_m"])
                    self.assertGreaterEqual(anchor["y_m"], clear_bounds[2] + margin["required_planar_margin_m"])
                    self.assertLessEqual(anchor["y_m"], clear_bounds[3] - margin["required_planar_margin_m"])
                    self.assertGreaterEqual(anchor["z_m"] - margin["required_vertical_margin_m"], config["slab_thickness_m"] + config["ceiling_height_m"])
                    self.assertLessEqual(anchor["z_m"] + margin["required_vertical_margin_m"], config["floor_to_floor_m"])
                    source_coordinates.add(coordinates)
                outer_bounds = shaft_outer_bounds(clear_bounds, config["wall_thickness_m"])
                for anchor in case["egress_anchors"]:
                    coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
                    self.assertTrue(all(isfinite(value) for value in coordinates))
                    self.assertFalse(outer_bounds[0] <= anchor["x_m"] <= outer_bounds[1] and outer_bounds[2] <= anchor["y_m"] <= outer_bounds[3])
                    self.assertTrue(_point_is_valid_service_candidate(config, self.systems[anchor["system"]], anchor["x_m"], anchor["y_m"]))
                    margin = service_routing_margins(self.systems[anchor["system"]], config["routing"]["clearance_m"])
                    self.assertGreaterEqual(anchor["z_m"] - margin["required_vertical_margin_m"], config["slab_thickness_m"] + config["ceiling_height_m"])
                    self.assertLessEqual(anchor["z_m"] + margin["required_vertical_margin_m"], config["floor_to_floor_m"])
                    egress_coordinates.add(coordinates)
                for anchor in case["terminal_anchors"]:
                    coordinates = (anchor["x_m"], anchor["y_m"], anchor["z_m"])
                    self.assertTrue(all(isfinite(value) for value in coordinates))
                    self.assertTrue(_point_is_valid_service_candidate(config, self.systems[anchor["system"]], anchor["x_m"], anchor["y_m"]))
                    margin = service_routing_margins(self.systems[anchor["system"]], config["routing"]["clearance_m"])
                    self.assertGreaterEqual(anchor["z_m"] - margin["required_vertical_margin_m"], config["slab_thickness_m"] + config["ceiling_height_m"])
                    self.assertLessEqual(anchor["z_m"] + margin["required_vertical_margin_m"], config["floor_to_floor_m"])
                    terminal_coordinates.add(coordinates)
                self.assertEqual(len(source_coordinates), 5)
                self.assertEqual(len(egress_coordinates), 5)
                self.assertEqual(len(terminal_coordinates), len(case["terminal_anchors"]))
                self.assertTrue(all(case["preflight"].values()))
                anchors = {anchor["anchor_id"] for anchor in [*case["source_anchors"], *case["egress_anchors"]]}
                for breakout in case["fixed_breakouts"]:
                    self.assertIn(breakout["source_anchor"], anchors)
                    self.assertIn(breakout["egress_anchor"], anchors)
                    self.assertEqual(breakout["source_anchor"], f"source/{breakout['system']}")
                    self.assertEqual(breakout["egress_anchor"], f"egress/{breakout['system']}")

    def test_connections_are_complete_and_follow_service_direction(self) -> None:
        systems = self.systems
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                anchors = {anchor["anchor_id"] for anchor in [*case["source_anchors"], *case["egress_anchors"], *case["terminal_anchors"]]}
                requests = case["connection_requests"]
                self.assertEqual(len(requests), len(case["terminal_anchors"]))
                self.assertEqual(len({request["connection_id"] for request in requests}), len(requests))
                for request in requests:
                    self.assertIn(request["start_anchor"], anchors)
                    self.assertIn(request["end_anchor"], anchors)
                    egress_id = f"egress/{request['system']}"
                    self.assertFalse(request["start_anchor"].startswith("source/"))
                    self.assertFalse(request["end_anchor"].startswith("source/"))
                    if systems[request["system"]]["flow_direction"] == "terminal_to_source":
                        self.assertEqual(request["end_anchor"], egress_id)
                        self.assertTrue(request["start_anchor"].startswith("terminal/"))
                    else:
                        self.assertEqual(request["start_anchor"], egress_id)
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

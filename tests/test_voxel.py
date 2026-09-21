"""Focused tests for IFC-derived service-aware voxel occupancy."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.config import load_building_config
from src.demand import generate_benchmark_cases, load_service_definitions, service_routing_margins
from src.voxel import (
    BLOCKED_FIXED,
    FREE,
    RESERVED_SHAFT,
    build_occupancy_grid,
    build_voxel_summary,
    extract_ifc_obstacles,
    grid_spec_from_config,
    write_voxel_summary,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS_PATH = ROOT / "config" / "mep_systems.json"


class VoxelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.systems = load_service_definitions(SYSTEMS_PATH)
        cls.configs = {
            scenario_id: load_building_config(SCENARIOS / filename)
            for scenario_id, filename in (("s01", "s01_low.json"), ("s02", "s02_medium.json"), ("s03", "s03_high.json"), ("s04", "s04_extreme.json"))
        }
        cls.summary = build_voxel_summary(SCENARIOS, DEMANDS, SYSTEMS_PATH)
        cls.summaries = {(item["scenario_id"], item["system"]): item for item in cls.summary["grid_summaries"]}
        cls.cases = {
            case["case_id"]: case
            for case in generate_benchmark_cases(SCENARIOS, DEMANDS, SYSTEMS_PATH)
        }

    def test_all_scenarios_build_exact_voxel_specs_and_round_trip_centers(self) -> None:
        for scenario_id, config in self.configs.items():
            with self.subTest(scenario_id=scenario_id):
                spec = grid_spec_from_config(config)
                self.assertEqual(spec.nx * spec.voxel_size_m, config["width_m"])
                self.assertEqual(spec.ny * spec.voxel_size_m, config["length_m"])
                self.assertAlmostEqual(
                    spec.nz * spec.voxel_size_m,
                    config["floor_to_floor_m"] - config["slab_thickness_m"] - config["ceiling_height_m"],
                )
                self.assertEqual(spec.world_to_containing_cell(*spec.cell_center(1, 1, 1)), (1, 1, 1))
                with self.assertRaises(ValueError):
                    spec.world_to_containing_cell(-0.01, 0.0, spec.origin_z)

    def test_ifc_geometry_extracts_walls_columns_and_shaft_space(self) -> None:
        obstacles, shaft_bounds = extract_ifc_obstacles(self.configs["s01"])

        self.assertEqual(sum(kind == "perimeter_wall" for kind, _ in obstacles), 4)
        self.assertEqual(sum(kind == "shaft_wall" for kind, _ in obstacles), 4)
        self.assertEqual(sum(kind == "structural_column" for kind, _ in obstacles), 2)
        self.assertEqual(shaft_bounds[:4], (14.0, 16.0, 9.0, 11.0))

    def test_known_fixed_reserved_and_free_locations_have_expected_state(self) -> None:
        grid = build_occupancy_grid("s01", self.configs["s01"], "water", self.systems["water"])
        self.assertEqual(grid.state_at(*grid.spec.world_to_containing_cell(0.1, 0.1, 3.05)), BLOCKED_FIXED)
        self.assertEqual(grid.state_at(*grid.spec.world_to_containing_cell(10.0, 10.0, 3.05)), BLOCKED_FIXED)
        self.assertEqual(grid.state_at(*grid.spec.world_to_containing_cell(15.0, 10.0, 3.05)), RESERVED_SHAFT)
        self.assertEqual(grid.state_at(*grid.spec.world_to_containing_cell(3.0, 3.0, 3.05)), FREE)

    def test_twenty_service_grids_are_service_aware_and_have_free_cells(self) -> None:
        self.assertEqual(len(self.summary["grid_summaries"]), 20)
        for item in self.summary["grid_summaries"]:
            self.assertGreater(item["free_cell_count"], 0)
        for scenario_id in self.configs:
            hvac = self.summaries[(scenario_id, "hvac")]
            electrical = self.summaries[(scenario_id, "electrical")]
            drainage = self.summaries[(scenario_id, "drainage")]
            water = self.summaries[(scenario_id, "water")]
            fire = self.summaries[(scenario_id, "fire")]
            self.assertLessEqual(hvac["free_cell_count"], electrical["free_cell_count"])
            self.assertLessEqual(electrical["free_cell_count"], drainage["free_cell_count"])
            self.assertLessEqual(drainage["free_cell_count"], water["free_cell_count"])
            self.assertLessEqual(water["free_cell_count"], fire["free_cell_count"])
        self.assertGreaterEqual(self.summaries[("s04", "hvac")]["usable_z_layer_count"], 1)

    def test_endpoint_mappings_use_free_cells_and_no_sources(self) -> None:
        cases = {mapping["case_id"] for mapping in self.summary["endpoint_mappings"]}
        self.assertEqual(cases, {f"s0{scenario}-d0{demand}" for scenario in range(1, 5) for demand in range(1, 4)})
        grids = {}
        for mapping in self.summary["endpoint_mappings"]:
            key = (mapping["case_id"].split("-")[0], mapping["system"])
            if key not in grids:
                grids[key] = build_occupancy_grid(key[0], self.configs[key[0]], key[1], self.systems[key[1]])
            grid = grids[key]
            anchors = {
                anchor["anchor_id"]
                : anchor
                for anchor in [
                    *self.cases[mapping["case_id"]]["egress_anchors"],
                    *self.cases[mapping["case_id"]]["terminal_anchors"],
                ]
            }
            margins = service_routing_margins(
                self.systems[mapping["system"]],
                float(self.configs[key[0]]["routing"]["clearance_m"]),
            )
            for endpoint in (mapping["start"], mapping["end"]):
                self.assertFalse(endpoint["anchor_id"].startswith("source/"))
                self.assertEqual(grid.state_at(*endpoint["grid_index"]), FREE)
                self.assertLessEqual(endpoint["snap_distance_m"], 2 * grid.spec.voxel_size_m)
                z = endpoint["voxel_center"][2]
                self.assertGreaterEqual(z - margins["required_vertical_margin_m"], grid.spec.origin_z)
                self.assertLessEqual(
                    z + margins["required_vertical_margin_m"],
                    grid.spec.origin_z + grid.spec.nz * grid.spec.voxel_size_m,
                )
                if endpoint["anchor_id"].startswith("egress/"):
                    x, y, _ = endpoint["voxel_center"]
                    min_x, max_x, min_y, max_y, _, _ = grid.shaft_bounds
                    side = anchors[endpoint["anchor_id"]]["wall_side"]
                    self.assertTrue({
                        "north": y > max_y,
                        "south": y < min_y,
                        "east": x > max_x,
                        "west": x < min_x,
                    }[side])

    def test_summary_output_is_byte_identical(self) -> None:
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            write_voxel_summary(SCENARIOS, DEMANDS, SYSTEMS_PATH, first)
            write_voxel_summary(SCENARIOS, DEMANDS, SYSTEMS_PATH, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

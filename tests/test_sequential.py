"""Tests for the B1 fixed-priority sequential routing baseline."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.demand import load_service_definitions
from src.routing import astar_3d, run_b0_benchmark
from src.sequential import (
    B1_SYSTEM_PRIORITY,
    _route_record,
    block_prior_routes,
    clone_grid,
    run_b1_benchmark,
    write_b1_routes,
)
from src.voxel import (
    BLOCKED_FIXED,
    BLOCKED_PRIOR_ROUTE,
    BLOCKED_VERTICAL_BOUNDARY,
    FREE,
    RESERVED_SHAFT,
    GridSpec,
    OccupancyGrid,
    build_occupancy_grids,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"


def grid(nx: int = 5, ny: int = 3) -> OccupancyGrid:
    spec = GridSpec(1.0, 0.0, 0.0, 0.0, nx, ny, 1)
    return OccupancyGrid("synthetic", "water", spec, bytearray(spec.total_cell_count), (0, 0, 0, 0, 0, 0), {})


def endpoint(index: tuple[int, int, int]) -> dict:
    return {"anchor_id": "terminal/water/0", "original_world": [0, 0, 0], "grid_index": list(index), "voxel_center": [0, 0, 0], "snap_distance_m": 0.0}


class SequentialSyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.definitions = load_service_definitions(SYSTEMS)
        cls.prior = {"system": "hvac", "path_cells": [[1, 0, 0], [2, 0, 0], [3, 0, 0]]}

    def test_priority_and_pair_specific_dynamic_blocking(self) -> None:
        self.assertEqual(B1_SYSTEM_PRIORITY, ("hvac", "drainage", "water", "fire", "electrical"))
        fine_spec = GridSpec(0.1, 0.0, 0.0, 0.0, 50, 30, 10)
        water_grid = OccupancyGrid("synthetic", "water", fine_spec, bytearray(fine_spec.total_cell_count), (0, 0, 0, 0, 0, 0), {})
        electrical_grid = clone_grid(water_grid)
        prior = {"system": "hvac", "path_cells": [[10, 10, 5], [20, 10, 5], [30, 10, 5]]}
        water_count = block_prior_routes(water_grid, [prior], self.definitions["water"], self.definitions)
        electrical_count = block_prior_routes(electrical_grid, [prior], self.definitions["electrical"], self.definitions)
        self.assertGreater(water_count, 0)
        self.assertNotEqual(water_count, electrical_count)
        self.assertEqual(water_grid.state_at(0, 29, 0), FREE)
        self.assertEqual(water_grid.state_at(20, 10, 5), BLOCKED_PRIOR_ROUTE)

    def test_dynamic_blocking_preserves_nonfree_base_states_and_base_grid(self) -> None:
        base = grid()
        base.cells[base.spec.linear_index(1, 0, 0)] = BLOCKED_FIXED
        base.cells[base.spec.linear_index(2, 0, 0)] = RESERVED_SHAFT
        base.cells[base.spec.linear_index(3, 0, 0)] = BLOCKED_VERTICAL_BOUNDARY
        before = bytes(base.cells)
        derived = clone_grid(base)
        block_prior_routes(derived, [self.prior], self.definitions["water"], self.definitions)
        self.assertEqual(bytes(base.cells), before)
        self.assertEqual(derived.state_at(1, 0, 0), BLOCKED_FIXED)
        self.assertEqual(derived.state_at(2, 0, 0), RESERVED_SHAFT)
        self.assertEqual(derived.state_at(3, 0, 0), BLOCKED_VERTICAL_BOUNDARY)

    def test_prior_route_detour_no_path_and_endpoint_failure(self) -> None:
        base = grid(5, 3)
        direct = astar_3d(base, (0, 0, 0), (4, 0, 0))
        derived = clone_grid(base)
        block_prior_routes(derived, [self.prior], self.definitions["water"], self.definitions)
        detour = astar_3d(derived, (0, 0, 0), (4, 0, 0))
        self.assertEqual(direct.step_count, 4)
        self.assertGreater(detour.step_count, direct.step_count)
        unreachable_base = grid(5, 1)
        unreachable = clone_grid(unreachable_base)
        block_prior_routes(unreachable, [self.prior], self.definitions["water"], self.definitions)
        self.assertFalse(astar_3d(unreachable, (0, 0, 0), (4, 0, 0)).path_found)
        no_path_record = _route_record(
            {"case_id": "synthetic", "scenario_id": "synthetic", "demand_profile_id": "d01"},
            {"connection_id": "water/0", "system": "water"}, 3,
            endpoint((0, 0, 0)), endpoint((4, 0, 0)), unreachable,
        )
        self.assertEqual(no_path_record["failure_reason"], "no_path_with_fixed_priority")
        endpoint_blocked = clone_grid(base)
        block_prior_routes(endpoint_blocked, [{"system": "hvac", "path_cells": [[0, 0, 0]]}], self.definitions["water"], self.definitions)
        self.assertEqual(endpoint_blocked.state_at(0, 0, 0), BLOCKED_PRIOR_ROUTE)
        endpoint_record = _route_record(
            {"case_id": "synthetic", "scenario_id": "synthetic", "demand_profile_id": "d01"},
            {"connection_id": "water/0", "system": "water"}, 3,
            endpoint((0, 0, 0)), endpoint((4, 0, 0)), endpoint_blocked,
        )
        self.assertEqual(endpoint_record["failure_reason"], "endpoint_blocked_by_prior_route")

    def test_same_system_routes_are_independent_and_overlap(self) -> None:
        base = grid()
        first = astar_3d(clone_grid(base), (0, 0, 0), (4, 0, 0))
        second = astar_3d(clone_grid(base), (0, 0, 0), (4, 0, 0))
        self.assertEqual(first.path_cells, second.path_cells)
        self.assertEqual(bytes(base.cells), bytes(grid().cells))


class SequentialIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.grids = build_occupancy_grids(SCENARIOS, SYSTEMS)
        cls.before = {key: bytes(value.cells) for key, value in cls.grids.items()}
        cls.b1 = run_b1_benchmark(SCENARIOS, DEMANDS, SYSTEMS, grids=cls.grids)
        cls.b0 = run_b0_benchmark(SCENARIOS, DEMANDS, SYSTEMS, grids=cls.grids)

    def test_all_requests_priority_order_and_base_immutability(self) -> None:
        routes = [route for case in self.b1["cases"] for route in case["routes"]]
        self.assertEqual(len(self.b1["cases"]), 12)
        self.assertEqual(len(routes), 292)
        self.assertEqual(len({(route["case_id"], route["connection_id"]) for route in routes}), 292)
        for case in self.b1["cases"]:
            self.assertEqual([summary["system"] for summary in case["system_routing_summaries"]], list(B1_SYSTEM_PRIORITY))
            self.assertEqual([summary["priority_rank"] for summary in case["system_routing_summaries"]], [1, 2, 3, 4, 5])
            for route in case["routes"]:
                if route["path_found"]:
                    self.assertIsNone(route["failure_reason"])
                else:
                    self.assertIn(route["failure_reason"], {"endpoint_blocked_by_prior_route", "no_path_with_fixed_priority"})
        self.assertEqual(self.before, {key: bytes(value.cells) for key, value in self.grids.items()})

    def test_hvac_matches_b0_and_is_nested_profile_invariant(self) -> None:
        b0_routes = {(route["case_id"], route["connection_id"]): route for case in self.b0["cases"] for route in case["routes"]}
        nested = {}
        for case in self.b1["cases"]:
            for route in case["routes"]:
                if route["system"] != "hvac":
                    continue
                baseline = b0_routes[(route["case_id"], route["connection_id"])]
                for key in ("path_cells", "grid_route_length_m", "bend_count", "vertical_travel_m"):
                    self.assertEqual(route[key], baseline[key])
                nested_key = (route["scenario_id"], route["connection_id"])
                self.assertEqual(nested.setdefault(nested_key, route["path_cells"]), route["path_cells"])

    def test_deterministic_serialization_and_global_totals(self) -> None:
        self.assertEqual(self.b1, run_b1_benchmark(SCENARIOS, DEMANDS, SYSTEMS))
        summary = self.b1["global_summary"]
        self.assertEqual(summary["total_route_instances"], 292)
        self.assertEqual(summary["successful_route_instances"] + summary["failed_route_instances"], 292)
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            write_b1_routes(SCENARIOS, DEMANDS, SYSTEMS, first)
            write_b1_routes(SCENARIOS, DEMANDS, SYSTEMS, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

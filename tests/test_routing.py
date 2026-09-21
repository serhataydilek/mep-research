"""Tests for the independent deterministic B0 voxel A* baseline."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.routing import (
    astar_3d,
    manhattan_heuristic,
    route_metrics,
    run_b0_benchmark,
    write_b0_routes,
)
from src.voxel import (
    BLOCKED_FIXED,
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


def synthetic_grid(nx: int, ny: int = 1, nz: int = 1, blocked: tuple[tuple[int, int, int], ...] = ()) -> OccupancyGrid:
    spec = GridSpec(1.0, 0.0, 0.0, 0.0, nx, ny, nz)
    cells = bytearray(spec.total_cell_count)
    for index in blocked:
        cells[spec.linear_index(*index)] = BLOCKED_FIXED
    return OccupancyGrid("synthetic", "test", spec, cells, (0, 0, 0, 0, 0, 0), {})


class AStarTests(unittest.TestCase):
    def test_start_equals_goal_is_a_zero_step_route(self) -> None:
        result = astar_3d(synthetic_grid(3), (1, 0, 0), (1, 0, 0))
        self.assertTrue(result.path_found)
        self.assertEqual(result.path_cells, ((1, 0, 0),))
        self.assertEqual(result.step_count, 0)

    def test_straight_route_uses_manhattan_minimum(self) -> None:
        result = astar_3d(synthetic_grid(5), (0, 0, 0), (4, 0, 0))
        self.assertEqual(result.step_count, 4)
        self.assertEqual(route_metrics(result, 1.0)["grid_route_length_m"], 4.0)

    def test_route_detours_around_block_and_disconnected_is_explicit(self) -> None:
        detour = astar_3d(synthetic_grid(3, 2, blocked=((1, 0, 0),)), (0, 0, 0), (2, 0, 0))
        self.assertTrue(detour.path_found)
        self.assertEqual(detour.step_count, 4)
        disconnected = astar_3d(synthetic_grid(3, blocked=((1, 0, 0),)), (0, 0, 0), (2, 0, 0))
        self.assertFalse(disconnected.path_found)
        self.assertEqual(disconnected.path_cells, ())
        self.assertIsNone(disconnected.step_count)

    def test_path_invariants_heuristic_and_repeatability(self) -> None:
        grid = synthetic_grid(3, 3, 2, blocked=((1, 1, 0),))
        first = astar_3d(grid, (0, 0, 0), (2, 2, 1))
        second = astar_3d(grid, (0, 0, 0), (2, 2, 1))
        self.assertEqual(first, second)
        self.assertEqual(manhattan_heuristic((0, 0, 0), (2, 2, 1)), 5)
        self.assertEqual(first.path_cells[0], (0, 0, 0))
        self.assertEqual(first.path_cells[-1], (2, 2, 1))
        self.assertEqual(first.step_count, len(first.path_cells) - 1)
        for current, following in zip(first.path_cells, first.path_cells[1:]):
            self.assertEqual(manhattan_heuristic(current, following), 1)
            self.assertEqual(grid.state_at(*following), FREE)

    def test_bends_include_vertical_direction_changes(self) -> None:
        straight = astar_3d(synthetic_grid(3), (0, 0, 0), (2, 0, 0))
        self.assertEqual(route_metrics(straight, 1.0)["bend_count"], 0)
        turn = astar_3d(synthetic_grid(2, 2), (0, 0, 0), (1, 1, 0))
        self.assertEqual(route_metrics(turn, 1.0)["bend_count"], 1)
        vertical_turn = astar_3d(synthetic_grid(2, 1, 2), (0, 0, 0), (1, 0, 1))
        metrics = route_metrics(vertical_turn, 1.0)
        self.assertEqual(metrics["bend_count"], 1)
        self.assertEqual(metrics["vertical_step_count"], 1)

    def test_symmetric_paths_use_repeatable_tie_breaking(self) -> None:
        grid = synthetic_grid(3, 3, blocked=((1, 1, 0),))
        paths = [astar_3d(grid, (0, 1, 0), (2, 1, 0)).path_cells for _ in range(3)]
        self.assertEqual(paths[0], paths[1])
        self.assertEqual(paths[1], paths[2])

    def test_invalid_nonfree_endpoints_raise(self) -> None:
        grid = synthetic_grid(2, blocked=((0, 0, 0),))
        with self.assertRaises(ValueError):
            astar_3d(grid, (0, 0, 0), (1, 0, 0))


class B0IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.grids = build_occupancy_grids(SCENARIOS, SYSTEMS)
        cls.before = {key: bytes(grid.cells) for key, grid in cls.grids.items()}
        cls.result = run_b0_benchmark(SCENARIOS, DEMANDS, SYSTEMS, grids=cls.grids)
        cls.reference_without_cache = run_b0_benchmark(
            SCENARIOS, DEMANDS, SYSTEMS, use_cache=False, grids=cls.grids
        )

    def test_all_cases_routes_and_grid_safety(self) -> None:
        self.assertEqual(len(self.result["cases"]), 12)
        routes = [route for case in self.result["cases"] for route in case["routes"]]
        self.assertEqual(len(routes), 292)
        for route in routes:
            self.assertNotIn("source/", route["start"]["anchor_id"])
            self.assertNotIn("source/", route["end"]["anchor_id"])
            grid = self.grids[(route["scenario_id"], route["system"])]
            self.assertEqual(route["start"]["grid_index"], route["path_cells"][0])
            self.assertEqual(route["end"]["grid_index"], route["path_cells"][-1])
            for index in route["path_cells"]:
                self.assertEqual(grid.state_at(*index), FREE)
            self.assertNotIn(RESERVED_SHAFT, (grid.state_at(*index) for index in route["path_cells"]))
            self.assertNotIn(BLOCKED_FIXED, (grid.state_at(*index) for index in route["path_cells"]))
            self.assertNotIn(BLOCKED_VERTICAL_BOUNDARY, (grid.state_at(*index) for index in route["path_cells"]))

    def test_independence_invariance_cache_and_aggregates(self) -> None:
        stats = self.result["search_statistics"]
        self.assertLess(stats["unique_searches_executed"], 292)
        self.assertEqual(stats["cache_reuse_count"], 292 - stats["unique_searches_executed"])
        by_connection = {}
        for case in self.result["cases"]:
            for route in case["routes"]:
                by_connection.setdefault((route["scenario_id"], route["connection_id"]), []).append(route)
            successful = [route for route in case["routes"] if route["path_found"]]
            self.assertEqual(case["total_grid_route_length_m"], sum(route["grid_route_length_m"] for route in successful))
            self.assertEqual(case["total_bend_count"], sum(route["bend_count"] for route in successful))
            self.assertEqual(case["all_connections_routed"], case["successful_route_count"] == case["connection_count"])
            for route in case["routes"]:
                self.assertEqual(route["endpoint_snap_distance_total_m"], route["start"]["snap_distance_m"] + route["end"]["snap_distance_m"])
                self.assertGreaterEqual(route["expanded_node_count"], 0)
        for routes in by_connection.values():
            first = routes[0]
            for route in routes[1:]:
                self.assertEqual(route["path_cells"], first["path_cells"])
                self.assertEqual(route["step_count"], first["step_count"])
                self.assertEqual(route["bend_count"], first["bend_count"])
        self.assertEqual(self.before, {key: bytes(grid.cells) for key, grid in self.grids.items()})
        self.assertEqual(self.result["cases"], self.reference_without_cache["cases"])

    def test_output_is_byte_identical_and_cache_does_not_change_small_reference(self) -> None:
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            write_b0_routes(SCENARIOS, DEMANDS, SYSTEMS, first)
            write_b0_routes(SCENARIOS, DEMANDS, SYSTEMS, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
        grid = synthetic_grid(3, 2)
        before = bytes(grid.cells)
        first = astar_3d(grid, (0, 0, 0), (2, 1, 0))
        second = astar_3d(grid, (0, 0, 0), (2, 1, 0))
        self.assertEqual(first, second)  # B0 permits the same cells to overlap.
        self.assertEqual(before, bytes(grid.cells))

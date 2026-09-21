"""B0 independent deterministic 3D A* routing on Phase 3A voxel grids.

The search uses six neighbours in this fixed expansion order: +X, -X, +Y,
-Y, +Z, -Z.  Heap entries sort by ``(f, h, g, k, j, i, linear_index)``;
therefore symmetric equal-cost alternatives have a repeatable, documented
choice.  Expanded nodes are non-stale heap entries popped by A*, including
the goal when it is popped.

B0 deliberately treats every connection as independent.  It never writes to
occupancy and has no awareness of any previously emitted route.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import heapq
import json
from pathlib import Path
from typing import Any

from .demand import generate_benchmark_cases
from .voxel import FREE, GridSpec, OccupancyGrid, build_occupancy_grids, snap_anchor_to_free_cell


METHOD = {"id": "B0", "name": "Independent 3D A*"}
NEIGHBOUR_OFFSETS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


@dataclass(frozen=True)
class AStarResult:
    path_found: bool
    path_cells: tuple[tuple[int, int, int], ...]
    step_count: int | None
    expanded_node_count: int


def manhattan_heuristic(start: tuple[int, int, int], goal: tuple[int, int, int]) -> int:
    """Return the admissible six-neighbour distance estimate in voxel steps."""
    return sum(abs(start[axis] - goal[axis]) for axis in range(3))


def _validate_free_index(grid: OccupancyGrid, index: tuple[int, int, int], label: str) -> int:
    linear = grid.spec.linear_index(*index)
    if grid.cells[linear] != FREE:
        raise ValueError(f"A* {label} must be a FREE voxel: {index}")
    return linear


def astar_3d_with_offsets(
    grid: OccupancyGrid, start_index: tuple[int, int, int], goal_index: tuple[int, int, int],
    neighbour_offsets: tuple[tuple[int, int, int], ...],
) -> AStarResult:
    """Find a deterministic shortest raw voxel path, or explicit no-route result.

    Costs are integers measured in voxel moves.  Parent and g-score maps use
    compact linear grid indices; no explicit graph is built.
    """
    start_linear = _validate_free_index(grid, start_index, "start")
    goal_linear = _validate_free_index(grid, goal_index, "goal")
    if start_linear == goal_linear:
        return AStarResult(True, (start_index,), 0, 1)

    spec = grid.spec
    g_scores = {start_linear: 0}
    parents: dict[int, int] = {}
    start_h = manhattan_heuristic(start_index, goal_index)
    heap: list[tuple[int, int, int, int, int, int, int]] = [
        (start_h, start_h, 0, start_index[2], start_index[1], start_index[0], start_linear)
    ]
    expanded = 0

    while heap:
        _, _, g_steps, k, j, i, current = heapq.heappop(heap)
        if g_scores.get(current) != g_steps:
            continue
        expanded += 1
        if current == goal_linear:
            reverse_path = [current]
            while current != start_linear:
                current = parents[current]
                reverse_path.append(current)
            return AStarResult(
                True,
                tuple(spec.index_from_linear(linear) for linear in reversed(reverse_path)),
                g_steps,
                expanded,
            )

        for di, dj, dk in neighbour_offsets:
            next_i, next_j, next_k = i + di, j + dj, k + dk
            if not (0 <= next_i < spec.nx and 0 <= next_j < spec.ny and 0 <= next_k < spec.nz):
                continue
            neighbour = spec.linear_index(next_i, next_j, next_k)
            if grid.cells[neighbour] != FREE:
                continue
            next_g = g_steps + 1
            if next_g >= g_scores.get(neighbour, float("inf")):
                continue
            g_scores[neighbour] = next_g
            parents[neighbour] = current
            h_steps = manhattan_heuristic((next_i, next_j, next_k), goal_index)
            heapq.heappush(
                heap,
                (next_g + h_steps, h_steps, next_g, next_k, next_j, next_i, neighbour),
            )
    return AStarResult(False, (), None, expanded)


def astar_3d(grid: OccupancyGrid, start_index: tuple[int, int, int], goal_index: tuple[int, int, int]) -> AStarResult:
    """Existing B0/B1 six-neighbour deterministic A* wrapper."""
    return astar_3d_with_offsets(grid, start_index, goal_index, NEIGHBOUR_OFFSETS)


def _direction(first: tuple[int, int, int], second: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(second[axis] - first[axis] for axis in range(3))


def route_metrics(result: AStarResult, voxel_size_m: float) -> dict[str, int | float | None]:
    """Calculate raw-path metrics without adding endpoint snapping distance."""
    if not result.path_found:
        return {
            "step_count": None, "grid_route_length_m": None,
            "horizontal_step_count": None, "vertical_step_count": None,
            "vertical_travel_m": None, "bend_count": None,
            "expanded_node_count": result.expanded_node_count,
        }
    path = result.path_cells
    directions = [_direction(path[index], path[index + 1]) for index in range(len(path) - 1)]
    step_count = len(directions)
    vertical_steps = sum(direction[2] != 0 for direction in directions)
    return {
        "step_count": step_count,
        "grid_route_length_m": step_count * voxel_size_m,
        "horizontal_step_count": step_count - vertical_steps,
        "vertical_step_count": vertical_steps,
        "vertical_travel_m": vertical_steps * voxel_size_m,
        "bend_count": sum(
            directions[index] != directions[index - 1] for index in range(1, len(directions))
        ),
        "expanded_node_count": result.expanded_node_count,
    }


def _route_record(
    case: dict[str, Any], request: dict[str, Any], start: dict[str, Any], end: dict[str, Any],
    result: AStarResult, grid: OccupancyGrid,
) -> dict[str, Any]:
    metrics = route_metrics(result, grid.spec.voxel_size_m)
    return {
        "case_id": case["case_id"],
        "scenario_id": case["scenario_id"],
        "demand_profile_id": case["demand_profile_id"],
        "connection_id": request["connection_id"],
        "system": request["system"],
        "path_found": result.path_found,
        "start": start,
        "end": end,
        **metrics,
        "endpoint_snap_distance_total_m": start["snap_distance_m"] + end["snap_distance_m"],
        "path_cells": [list(index) for index in result.path_cells],
    }


def _case_summary(case: dict[str, Any], routes: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [route for route in routes if route["path_found"]]
    failed = len(routes) - len(successful)
    return {
        "case_id": case["case_id"],
        "scenario_id": case["scenario_id"],
        "demand_profile_id": case["demand_profile_id"],
        "connection_count": len(routes),
        "successful_route_count": len(successful),
        "failed_route_count": failed,
        "connection_success_rate": len(successful) / len(routes) if routes else 0.0,
        "all_connections_routed": len(successful) == len(routes),
        "total_grid_route_length_m": sum(route["grid_route_length_m"] for route in successful),
        "total_bend_count": sum(route["bend_count"] for route in successful),
        "total_vertical_travel_m": sum(route["vertical_travel_m"] for route in successful),
        "total_expanded_nodes": sum(route["expanded_node_count"] for route in routes),
        "maximum_single_route_expanded_nodes": max(
            (route["expanded_node_count"] for route in routes), default=0
        ),
        "routes": routes,
    }


def run_b0_benchmark(
    scenarios_directory: Path,
    demands_directory: Path,
    systems_path: Path,
    *,
    use_cache: bool = True,
    grids: dict[tuple[str, str], OccupancyGrid] | None = None,
) -> dict[str, Any]:
    """Route every benchmark connection independently and serialize stable results.

    ``connection_success_rate`` only measures individual fixed-obstacle-free
    reachability.  It is not a MEP layout-feasibility or clash-free claim.
    """
    grids = grids if grids is not None else build_occupancy_grids(scenarios_directory, systems_path)
    cache: dict[tuple[str, str, tuple[int, int, int], tuple[int, int, int]], AStarResult] = {}
    cases_output = []
    route_instances = 0
    cache_reuse_count = 0
    for case in generate_benchmark_cases(scenarios_directory, demands_directory, systems_path):
        anchors = {
            anchor["anchor_id"]: anchor
            for anchor in [*case["egress_anchors"], *case["terminal_anchors"]]
        }
        routes = []
        for request in case["connection_requests"]:
            route_instances += 1
            grid = grids[(case["scenario_id"], request["system"])]
            start = snap_anchor_to_free_cell(grid, anchors[request["start_anchor"]])
            end = snap_anchor_to_free_cell(grid, anchors[request["end_anchor"]])
            start_index = tuple(start["grid_index"])
            end_index = tuple(end["grid_index"])
            key = (case["scenario_id"], request["system"], start_index, end_index)
            if use_cache and key in cache:
                result = cache[key]
                cache_reuse_count += 1
            else:
                result = astar_3d(grid, start_index, end_index)
                if use_cache:
                    cache[key] = result
            routes.append(_route_record(case, request, start, end, result, grid))
        cases_output.append(_case_summary(case, routes))
    unique_searches = len(cache) if use_cache else route_instances
    successful = sum(case["successful_route_count"] for case in cases_output)
    failed = route_instances - successful
    return {
        "method": METHOD,
        "search_statistics": {
            "total_route_instances": route_instances,
            "unique_searches_executed": unique_searches,
            "cache_reuse_count": cache_reuse_count,
            "successful_route_instances": successful,
            "failed_route_instances": failed,
            "maximum_single_route_expanded_nodes": max(
                (case["maximum_single_route_expanded_nodes"] for case in cases_output), default=0
            ),
        },
        "cases": cases_output,
    }


def write_b0_routes(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path, output_path: Path
) -> dict[str, Any]:
    result = run_b0_benchmark(scenarios_directory, demands_directory, systems_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run B0 independent deterministic 3D A*.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = write_b0_routes(args.scenarios, args.demands, args.systems, args.output)
    print("case     connections successful failed success% length(m) bends vertical(m) expanded")
    for case in result["cases"]:
        print(
            f"{case['case_id']:<8} {case['connection_count']:11} {case['successful_route_count']:10} "
            f"{case['failed_route_count']:6} {case['connection_success_rate'] * 100:8.2f} "
            f"{case['total_grid_route_length_m']:9.2f} {case['total_bend_count']:5} "
            f"{case['total_vertical_travel_m']:11.2f} {case['total_expanded_nodes']:8}"
        )
    stats = result["search_statistics"]
    print("global total-routes unique-searches cache-reuse successful failed max-expanded")
    print(
        f"global {stats['total_route_instances']:12} {stats['unique_searches_executed']:15} "
        f"{stats['cache_reuse_count']:11} {stats['successful_route_instances']:10} "
        f"{stats['failed_route_instances']:6} {stats['maximum_single_route_expanded_nodes']:12}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

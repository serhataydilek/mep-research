"""B1 fixed-priority sequential routing on immutable Phase 3A grids.

This controlled baseline uses the predefined physical-priority order HVAC,
gravity drainage, water, fire, electrical.  It is not a claim of universal
construction priority: water merely precedes fire deterministically here.
Successful systems reserve pair-specific envelope-plus-clearance space only
for lower-priority systems; sibling routes remain independent.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from .demand import generate_benchmark_cases, load_service_definitions
from .route_geometry import compress_path_cells, nominal_half_extents, swept_segment_aabb
from .routing import astar_3d, route_metrics
from .voxel import (
    BLOCKED_PRIOR_ROUTE,
    FREE,
    OccupancyGrid,
    aabb_index_ranges,
    build_occupancy_grids,
    snap_anchor_to_free_cell,
)


B1_SYSTEM_PRIORITY = ("hvac", "drainage", "water", "fire", "electrical")
CLEARANCE_M = 0.05


def clone_grid(grid: OccupancyGrid) -> OccupancyGrid:
    """Create a B1-only occupancy copy; Phase 3A grids are never mutated."""
    return OccupancyGrid(
        grid.scenario_id, grid.system, grid.spec, bytearray(grid.cells),
        grid.shaft_bounds, dict(grid.obstacle_counts),
    )


def block_prior_routes(
    derived_grid: OccupancyGrid,
    prior_routes: list[dict[str, Any]],
    target_definition: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
) -> int:
    """Mark FREE cells excluded by successful higher-priority routes only.

    For every prior segment, the current service's half extent plus the full
    0.05 m pairwise gap is added to the prior nominal envelope.  This is the
    pair-specific reservation, with no additional voxel inflation.
    """
    target_planar, target_vertical = nominal_half_extents(target_definition)
    for route in prior_routes:
        prior_planar, prior_vertical = nominal_half_extents(definitions[route["system"]])
        for segment in compress_path_cells(route["path_cells"], derived_grid.spec):
            exclusion = swept_segment_aabb(
                segment,
                prior_planar + target_planar + CLEARANCE_M,
                prior_vertical + target_vertical + CLEARANCE_M,
            )
            xs, ys, zs = aabb_index_ranges(derived_grid.spec, exclusion)
            for k in zs:
                for j in ys:
                    base = derived_grid.spec.linear_index(0, j, k)
                    for i in xs:
                        index = base + i
                        if derived_grid.cells[index] == FREE:
                            derived_grid.cells[index] = BLOCKED_PRIOR_ROUTE
    return derived_grid.cells.count(BLOCKED_PRIOR_ROUTE)


def _route_record(
    case: dict[str, Any], request: dict[str, Any], priority_rank: int,
    start: dict[str, Any], end: dict[str, Any], derived_grid: OccupancyGrid,
) -> dict[str, Any]:
    start_index = tuple(start["grid_index"])
    end_index = tuple(end["grid_index"])
    if derived_grid.state_at(*start_index) == BLOCKED_PRIOR_ROUTE or derived_grid.state_at(*end_index) == BLOCKED_PRIOR_ROUTE:
        result = None
        failure_reason = "endpoint_blocked_by_prior_route"
    else:
        result = astar_3d(derived_grid, start_index, end_index)
        failure_reason = None if result.path_found else "no_path_with_fixed_priority"
    metrics = route_metrics(result, derived_grid.spec.voxel_size_m) if result else {
        "step_count": None, "grid_route_length_m": None,
        "horizontal_step_count": None, "vertical_step_count": None,
        "vertical_travel_m": None, "bend_count": None, "expanded_node_count": 0,
    }
    return {
        "case_id": case["case_id"], "scenario_id": case["scenario_id"],
        "demand_profile_id": case["demand_profile_id"], "connection_id": request["connection_id"],
        "system": request["system"], "priority_rank": priority_rank,
        "path_found": failure_reason is None,
        "failure_reason": failure_reason,
        "start": start, "end": end, **metrics,
        "endpoint_snap_distance_total_m": start["snap_distance_m"] + end["snap_distance_m"],
        "path_cells": [list(cell) for cell in result.path_cells] if result else [],
    }


def _case_summary(case: dict[str, Any], routes: list[dict[str, Any]], systems: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [route for route in routes if route["path_found"]]
    return {
        "case_id": case["case_id"], "scenario_id": case["scenario_id"],
        "demand_profile_id": case["demand_profile_id"],
        "connection_count": len(routes), "successful_route_count": len(successful),
        "failed_route_count": len(routes) - len(successful),
        "connection_success_rate": len(successful) / len(routes) if routes else 0.0,
        "all_connections_routed": len(successful) == len(routes),
        "total_grid_route_length_m": sum(route["grid_route_length_m"] for route in successful),
        "total_bend_count": sum(route["bend_count"] for route in successful),
        "total_vertical_travel_m": sum(route["vertical_travel_m"] for route in successful),
        "total_expanded_nodes": sum(route["expanded_node_count"] for route in routes),
        "maximum_single_route_expanded_nodes": max((route["expanded_node_count"] for route in routes), default=0),
        "system_routing_summaries": systems,
        "routes": routes,
    }


def run_b1_benchmark(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path,
    *, grids: dict[tuple[str, str], OccupancyGrid] | None = None,
) -> dict[str, Any]:
    """Run B1's fixed system sequence without repair, rerouting, or path changes."""
    definitions = load_service_definitions(systems_path)
    base_grids = grids if grids is not None else build_occupancy_grids(scenarios_directory, systems_path)
    before = {key: bytes(grid.cells) for key, grid in base_grids.items()}
    cases_output = []
    for case in generate_benchmark_cases(scenarios_directory, demands_directory, systems_path):
        anchors = {anchor["anchor_id"]: anchor for anchor in [*case["egress_anchors"], *case["terminal_anchors"]]}
        requests_by_system = {system: [] for system in B1_SYSTEM_PRIORITY}
        for request in case["connection_requests"]:
            requests_by_system[request["system"]].append(request)
        completed_prior_routes = []
        routes = []
        system_summaries = []
        for priority_rank, system in enumerate(B1_SYSTEM_PRIORITY, start=1):
            base_grid = base_grids[(case["scenario_id"], system)]
            derived_grid = clone_grid(base_grid)
            dynamic_count = block_prior_routes(derived_grid, completed_prior_routes, definitions[system], definitions)
            requests = sorted(requests_by_system[system], key=lambda item: (item["terminal_index"], item["connection_id"]))
            system_routes = []
            for request in requests:
                # Phase 3A defines endpoints; B1 may reject, but never re-snap, them.
                start = snap_anchor_to_free_cell(base_grid, anchors[request["start_anchor"]])
                end = snap_anchor_to_free_cell(base_grid, anchors[request["end_anchor"]])
                system_routes.append(_route_record(case, request, priority_rank, start, end, derived_grid))
            successful = [route for route in system_routes if route["path_found"]]
            system_summaries.append({
                "system": system, "priority_rank": priority_rank, "request_count": len(system_routes),
                "successful_route_count": len(successful), "failed_route_count": len(system_routes) - len(successful),
                "prior_successful_route_count": len(completed_prior_routes),
                "dynamic_blocked_cell_count": dynamic_count,
                "free_cell_count_after_dynamic_blocking": derived_grid.cells.count(FREE),
            })
            routes.extend(system_routes)
            completed_prior_routes.extend(successful)
        cases_output.append(_case_summary(case, routes, system_summaries))
    if before != {key: bytes(grid.cells) for key, grid in base_grids.items()}:
        raise RuntimeError("B1 must not mutate Phase 3A base occupancy grids.")
    return {
        "method": {"id": "B1", "name": "Fixed-Priority Sequential 3D A*"},
        "priority": list(B1_SYSTEM_PRIORITY),
        "global_summary": _global_summary(cases_output),
        "cases": cases_output,
    }


def _global_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    routes = [route for case in cases for route in case["routes"]]
    successful = [route for route in routes if route["path_found"]]
    by_system = []
    for system in B1_SYSTEM_PRIORITY:
        matching = [route for route in routes if route["system"] == system]
        successes = [route for route in matching if route["path_found"]]
        failures = Counter(route["failure_reason"] for route in matching if not route["path_found"])
        by_system.append({
            "system": system, "attempted": len(matching), "successful": len(successes),
            "failed": len(matching) - len(successes),
            "success_rate": len(successes) / len(matching) if matching else 0.0,
            "failure_reasons": dict(sorted(failures.items())),
        })
    failures = Counter(route["failure_reason"] for route in routes if not route["path_found"])
    dynamic_by_priority = []
    for system in B1_SYSTEM_PRIORITY:
        rows = [summary for case in cases for summary in case["system_routing_summaries"] if summary["system"] == system]
        dynamic_by_priority.append({
            "system": system, "priority_rank": B1_SYSTEM_PRIORITY.index(system) + 1,
            "total_dynamic_blocked_cell_count": sum(row["dynamic_blocked_cell_count"] for row in rows),
        })
    return {
        "total_route_instances": len(routes), "successful_route_instances": len(successful),
        "failed_route_instances": len(routes) - len(successful),
        "connection_success_rate": len(successful) / len(routes) if routes else 0.0,
        "all_connections_routed_case_count": sum(case["all_connections_routed"] for case in cases),
        "total_grid_route_length_m": sum(route["grid_route_length_m"] for route in successful),
        "total_bend_count": sum(route["bend_count"] for route in successful),
        "total_vertical_travel_m": sum(route["vertical_travel_m"] for route in successful),
        "total_expanded_nodes": sum(route["expanded_node_count"] for route in routes),
        "failed_route_counts_by_system": by_system,
        "failed_route_counts_by_scenario": _failure_groups(cases, "scenario_id"),
        "failed_route_counts_by_demand_profile": _failure_groups(cases, "demand_profile_id"),
        "failed_route_counts_by_reason": dict(sorted(failures.items())),
        "dynamic_blocked_cells_by_priority": dynamic_by_priority,
    }


def _failure_groups(cases: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows = []
    for value in sorted({case[key] for case in cases}):
        matching = [route for case in cases if case[key] == value for route in case["routes"]]
        rows.append({key: value, "failed_route_count": sum(not route["path_found"] for route in matching)})
    return rows


def write_b1_routes(scenarios_directory: Path, demands_directory: Path, systems_path: Path, output_path: Path) -> dict[str, Any]:
    result = run_b1_benchmark(scenarios_directory, demands_directory, systems_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run B1 fixed-priority sequential 3D A*.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = write_b1_routes(args.scenarios, args.demands, args.systems, args.output)
    print("case     connections successful failed success% length(m) bends vertical(m) expanded")
    for case in result["cases"]:
        print(f"{case['case_id']:<8} {case['connection_count']:11} {case['successful_route_count']:10} {case['failed_route_count']:6} {case['connection_success_rate'] * 100:8.2f} {case['total_grid_route_length_m']:9.2f} {case['total_bend_count']:5} {case['total_vertical_travel_m']:11.2f} {case['total_expanded_nodes']:8}")
    print("system      attempted successful failed success% failure-reasons")
    for row in result["global_summary"]["failed_route_counts_by_system"]:
        print(f"{row['system']:<11} {row['attempted']:9} {row['successful']:10} {row['failed']:6} {row['success_rate'] * 100:8.2f} {row['failure_reasons']}")
    summary = result["global_summary"]
    print("global", {key: summary[key] for key in ("all_connections_routed_case_count", "total_route_instances", "successful_route_instances", "failed_route_instances")})
    print("dynamic-blocked-cells", summary["dynamic_blocked_cells_by_priority"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

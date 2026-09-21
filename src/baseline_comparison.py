"""Failure-aware deterministic B0 versus B1 baseline comparison.

Conflict geometry is evaluated only among successful routes, so every reported
conflict rate is accompanied by the routing-success denominator.  No winner or
layout-feasibility claim is calculated here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .clash import evaluate_route_result_conflicts
from .demand import SYSTEM_ORDER, load_service_definitions
from .routing import run_b0_benchmark
from .sequential import B1_SYSTEM_PRIORITY, run_b1_benchmark
from .voxel import build_occupancy_grids


def _route_key(route: dict[str, Any]) -> tuple[str, str]:
    return route["system"], route["connection_id"]


def _successful_keys(case: dict[str, Any]) -> set[tuple[str, str]]:
    return {_route_key(route) for route in case["routes"] if route["path_found"]}


def _subset_result(routing_result: dict[str, Any], keys_by_case: dict[str, set[tuple[str, str]]]) -> dict[str, Any]:
    """Make a geometry-only compatible result limited to selected successful connections."""
    return {
        "cases": [
            {
                "case_id": case["case_id"], "scenario_id": case["scenario_id"],
                "demand_profile_id": case["demand_profile_id"],
                "connection_count": len(keys_by_case[case["case_id"]]),
                "routes": [
                    route for route in case["routes"] if _route_key(route) in keys_by_case[case["case_id"]]
                ],
            }
            for case in routing_result["cases"]
        ]
    }


def _quality(routes: list[dict[str, Any]]) -> dict[str, float | int]:
    successful = [route for route in routes if route["path_found"]]
    count = len(successful)
    total_length = sum(route["grid_route_length_m"] for route in successful)
    total_bends = sum(route["bend_count"] for route in successful)
    total_vertical = sum(route["vertical_travel_m"] for route in successful)
    return {
        "total_grid_route_length_m": total_length,
        "mean_grid_route_length_m": total_length / count if count else 0.0,
        "total_bend_count": total_bends,
        "mean_bend_count": total_bends / count if count else 0.0,
        "total_vertical_travel_m": total_vertical,
        "mean_vertical_travel_m": total_vertical / count if count else 0.0,
        "total_expanded_nodes": sum(route["expanded_node_count"] for route in successful),
    }


def _method_metrics(routing: dict[str, Any], conflicts: dict[str, Any]) -> dict[str, Any]:
    routes = [route for case in routing["cases"] for route in case["routes"]]
    successful = [route for route in routes if route["path_found"]]
    conflict_global = conflicts["global_metrics"]
    conflict_cases = {case["case_id"]: case for case in conflicts["cases"]}
    routing_complete = [case for case in routing["cases"] if case["all_connections_routed"]]
    return {
        "routing": {
            "total_route_requests": len(routes), "successful_routes": len(successful),
            "failed_routes": len(routes) - len(successful),
            "connection_success_rate": len(successful) / len(routes) if routes else 0.0,
            "all_connections_routed_case_count": len(routing_complete),
        },
        "conflict_evaluation": {
            "evaluated_inter_system_route_pairs": conflict_global["total_inter_system_route_pairs_evaluated"],
            "hard_conflicting_route_pairs": conflict_global["total_hard_conflicting_route_pairs"],
            "clearance_violating_route_pairs": conflict_global["total_clearance_violating_route_pairs"],
            "clearance_only_route_pairs": conflict_global["total_clearance_only_route_pairs"],
            "hard_conflict_rate_among_successful_pairs": conflict_global["hard_conflict_rate"],
            "clearance_violation_rate_among_successful_pairs": conflict_global["clearance_violation_rate"],
            "hard_conflict_free_case_count_among_routed_geometry": conflict_global["hard_conflict_free_case_count"],
            "clearance_compliant_case_count_among_routed_geometry": conflict_global["clearance_compliant_case_count"],
        },
        "route_quality_successful_only": _quality(routes),
        "completion": {
            "routing_complete_case_count": len(routing_complete),
            "routing_complete_and_hard_conflict_free_case_count": sum(
                conflict_cases[case["case_id"]]["all_inter_system_hard_conflict_free"] for case in routing_complete
            ),
            "routing_complete_and_clearance_compliant_case_count": sum(
                conflict_cases[case["case_id"]]["all_inter_system_clearance_compliant"] for case in routing_complete
            ),
        },
    }


def _case_method_metrics(routing_case: dict[str, Any], conflict_case: dict[str, Any]) -> dict[str, Any]:
    successful = [route for route in routing_case["routes"] if route["path_found"]]
    return {
        "successful": len(successful), "failed": len(routing_case["routes"]) - len(successful),
        "connection_success_rate": len(successful) / len(routing_case["routes"]) if routing_case["routes"] else 0.0,
        "hard_conflicts": conflict_case["hard_conflicting_route_pair_count"],
        "clearance_violations": conflict_case["clearance_violating_route_pair_count"],
        "evaluated_pairs": conflict_case["route_pair_count"],
        "hard_conflict_rate": conflict_case["hard_conflict_rate"],
        "clearance_violation_rate": conflict_case["clearance_violation_rate"],
        "total_route_length": sum(route["grid_route_length_m"] for route in successful),
        "bends": sum(route["bend_count"] for route in successful),
        "vertical_travel": sum(route["vertical_travel_m"] for route in successful),
    }


def _common_quality(routes: list[dict[str, Any]]) -> dict[str, float | int]:
    return _quality(routes)


def compare_baselines(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path
) -> dict[str, Any]:
    """Run both existing baselines and compare their successful-route geometry fairly."""
    grids = build_occupancy_grids(scenarios_directory, systems_path)
    before = {key: bytes(grid.cells) for key, grid in grids.items()}
    definitions = load_service_definitions(systems_path)
    b0 = run_b0_benchmark(scenarios_directory, demands_directory, systems_path, grids=grids)
    b1 = run_b1_benchmark(scenarios_directory, demands_directory, systems_path, grids=grids)
    if before != {key: bytes(grid.cells) for key, grid in grids.items()}:
        raise RuntimeError("Baseline comparison must not mutate Phase 3A occupancy.")
    b0_conflicts = evaluate_route_result_conflicts(
        b0, grids, definitions, "B0-3C", "B0 Inter-System Conflict Evaluation",
        "b0_search_statistics", b0["search_statistics"],
    )
    b1_conflicts = evaluate_route_result_conflicts(
        b1, grids, definitions, "B1-4B", "B1 Inter-System Conflict Evaluation",
        "b1_global_summary", b1["global_summary"], include_requested_counts=True,
    )
    b0_cases = {case["case_id"]: case for case in b0["cases"]}
    b1_cases = {case["case_id"]: case for case in b1["cases"]}
    b0_conflict_cases = {case["case_id"]: case for case in b0_conflicts["cases"]}
    b1_conflict_cases = {case["case_id"]: case for case in b1_conflicts["cases"]}
    common_keys = {
        case_id: _successful_keys(b0_case) & _successful_keys(b1_cases[case_id])
        for case_id, b0_case in b0_cases.items()
    }
    b0_common = evaluate_route_result_conflicts(
        _subset_result(b0, common_keys), grids, definitions, "B0-common", "B0 Common-Success Subset",
        "subset", {}, include_requested_counts=True,
    )
    b1_common = evaluate_route_result_conflicts(
        _subset_result(b1, common_keys), grids, definitions, "B1-common", "B1 Common-Success Subset",
        "subset", {}, include_requested_counts=True,
    )
    b0_common_cases = {case["case_id"]: case for case in b0_common["cases"]}
    b1_common_cases = {case["case_id"]: case for case in b1_common["cases"]}
    case_comparisons = []
    for case_id in sorted(b0_cases):
        common = common_keys[case_id]
        b0_common_routes = [route for route in b0_cases[case_id]["routes"] if _route_key(route) in common]
        b1_common_routes = [route for route in b1_cases[case_id]["routes"] if _route_key(route) in common]
        b0_metrics = _case_method_metrics(b0_cases[case_id], b0_conflict_cases[case_id])
        b1_metrics = _case_method_metrics(b1_cases[case_id], b1_conflict_cases[case_id])
        case_comparisons.append({
            "case_id": case_id, "scenario_id": b0_cases[case_id]["scenario_id"],
            "demand_profile_id": b0_cases[case_id]["demand_profile_id"],
            "B0": b0_metrics, "B1": b1_metrics,
            "delta": {
                "successful_routes": b1_metrics["successful"] - b0_metrics["successful"],
                "failed_routes": b1_metrics["failed"] - b0_metrics["failed"],
                "total_route_length_successful_only": b1_metrics["total_route_length"] - b0_metrics["total_route_length"],
                "bends_successful_only": b1_metrics["bends"] - b0_metrics["bends"],
            },
            "common_success": {
                "common_successful_connection_count": len(common),
                "common_inter_system_pair_count": b0_common_cases[case_id]["route_pair_count"],
                "B1_common_inter_system_pair_count": b1_common_cases[case_id]["route_pair_count"],
                "B0_common_hard_conflicts": b0_common_cases[case_id]["hard_conflicting_route_pair_count"],
                "B1_common_hard_conflicts": b1_common_cases[case_id]["hard_conflicting_route_pair_count"],
                "B0_common_clearance_violations": b0_common_cases[case_id]["clearance_violating_route_pair_count"],
                "B1_common_clearance_violations": b1_common_cases[case_id]["clearance_violating_route_pair_count"],
                "B0_common_hard_conflict_rate": b0_common_cases[case_id]["hard_conflict_rate"],
                "B1_common_hard_conflict_rate": b1_common_cases[case_id]["hard_conflict_rate"],
                "B0_quality": _common_quality(b0_common_routes),
                "B1_quality": _common_quality(b1_common_routes),
            },
        })
    return {
        "methods": {"B0": _method_metrics(b0, b0_conflicts), "B1": _method_metrics(b1, b1_conflicts)},
        "global_comparison": {
            "routing_denominator_total_requested_connections": sum(len(case["routes"]) for case in b0["cases"]),
            "conflict_denominator_note": "Conflict rates use only unordered different-system pairs among successful route geometry.",
        },
        "per_system": _per_system(b0, b1, common_keys),
        "priority_impact": _priority_impact(b1),
        "failure_reasons": b1["global_summary"],
        "cases": case_comparisons,
        "common_success_global": _common_global(b0_common, b1_common, b0, b1, common_keys),
    }


def _per_system(b0: dict[str, Any], b1: dict[str, Any], common: dict[str, set[tuple[str, str]]]) -> list[dict[str, Any]]:
    rows = []
    for system in SYSTEM_ORDER:
        b0_routes = [route for case in b0["cases"] for route in case["routes"] if route["system"] == system]
        b1_routes = [route for case in b1["cases"] for route in case["routes"] if route["system"] == system]
        b0_common = [route for case in b0["cases"] for route in case["routes"] if route["system"] == system and _route_key(route) in common[case["case_id"]]]
        b1_common = [route for case in b1["cases"] for route in case["routes"] if route["system"] == system and _route_key(route) in common[case["case_id"]]]
        b0_quality, b1_quality = _quality(b0_common), _quality(b1_common)
        count = len(b0_common)
        rows.append({
            "system": system, "attempted_B0": len(b0_routes), "successful_B0": sum(route["path_found"] for route in b0_routes),
            "successful_B1": sum(route["path_found"] for route in b1_routes),
            "B0_success_rate": sum(route["path_found"] for route in b0_routes) / len(b0_routes),
            "B1_success_rate": sum(route["path_found"] for route in b1_routes) / len(b1_routes),
            "B1_failure_count": sum(not route["path_found"] for route in b1_routes),
            "common_success_count": count,
            "mean_B0_route_length": b0_quality["mean_grid_route_length_m"],
            "mean_B1_route_length": b1_quality["mean_grid_route_length_m"],
            "mean_route_length_delta": b1_quality["mean_grid_route_length_m"] - b0_quality["mean_grid_route_length_m"],
            "mean_B0_bends": b0_quality["mean_bend_count"], "mean_B1_bends": b1_quality["mean_bend_count"],
        })
    return rows


def _priority_impact(b1: dict[str, Any]) -> list[dict[str, Any]]:
    summary = b1["global_summary"]
    dynamic = {row["system"]: row["total_dynamic_blocked_cell_count"] for row in summary["dynamic_blocked_cells_by_priority"]}
    by_system = {row["system"]: row for row in summary["failed_route_counts_by_system"]}
    return [{
        "priority_rank": index, "system": system, "attempted": by_system[system]["attempted"],
        "successful": by_system[system]["successful"], "failed": by_system[system]["failed"],
        "success_rate": by_system[system]["success_rate"], "dynamic_blocked_cells": dynamic[system],
    } for index, system in enumerate(B1_SYSTEM_PRIORITY, start=1)]


def _common_global(b0_common: dict[str, Any], b1_common: dict[str, Any], b0: dict[str, Any], b1: dict[str, Any], common: dict[str, set[tuple[str, str]]]) -> dict[str, Any]:
    b0_routes = [route for case in b0["cases"] for route in case["routes"] if _route_key(route) in common[case["case_id"]]]
    b1_routes = [route for case in b1["cases"] for route in case["routes"] if _route_key(route) in common[case["case_id"]]]
    b0_quality, b1_quality = _quality(b0_routes), _quality(b1_routes)
    return {
        "B0": {"route_count": len(b0_routes), "inter_system_pair_count": b0_common["global_metrics"]["total_inter_system_route_pairs_evaluated"], "hard_conflicts": b0_common["global_metrics"]["total_hard_conflicting_route_pairs"], "clearance_violations": b0_common["global_metrics"]["total_clearance_violating_route_pairs"], "hard_conflict_rate": b0_common["global_metrics"]["hard_conflict_rate"], **b0_quality},
        "B1": {"route_count": len(b1_routes), "inter_system_pair_count": b1_common["global_metrics"]["total_inter_system_route_pairs_evaluated"], "hard_conflicts": b1_common["global_metrics"]["total_hard_conflicting_route_pairs"], "clearance_violations": b1_common["global_metrics"]["total_clearance_violating_route_pairs"], "hard_conflict_rate": b1_common["global_metrics"]["hard_conflict_rate"], **b1_quality},
        "delta": {"route_length": b1_quality["total_grid_route_length_m"] - b0_quality["total_grid_route_length_m"], "bends": b1_quality["total_bend_count"] - b0_quality["total_bend_count"], "vertical_travel": b1_quality["total_vertical_travel_m"] - b0_quality["total_vertical_travel_m"]},
    }


def write_baseline_comparison(scenarios_directory: Path, demands_directory: Path, systems_path: Path, output_path: Path) -> dict[str, Any]:
    result = compare_baselines(scenarios_directory, demands_directory, systems_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare B0 independent and B1 fixed-priority routing baselines.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = write_baseline_comparison(args.scenarios, args.demands, args.systems, args.output)
    print("method requests successful success% pairs hard hard% clearance clearance% complete clearance-complete")
    for name in ("B0", "B1"):
        metric = result["methods"][name]
        routing, conflict, completion = metric["routing"], metric["conflict_evaluation"], metric["completion"]
        print(f"{name:<6} {routing['total_route_requests']:8} {routing['successful_routes']:10} {routing['connection_success_rate'] * 100:8.2f} {conflict['evaluated_inter_system_route_pairs']:5} {conflict['hard_conflicting_route_pairs']:4} {conflict['hard_conflict_rate_among_successful_pairs'] * 100:5.1f} {conflict['clearance_violating_route_pairs']:9} {conflict['clearance_violation_rate_among_successful_pairs'] * 100:10.1f} {completion['routing_complete_case_count']:8} {completion['routing_complete_and_clearance_compliant_case_count']:18}")
    print("common method routes pairs hard hard% clearance length bends vertical")
    for name in ("B0", "B1"):
        row = result["common_success_global"][name]
        print(f"common {name:<2} {row['route_count']:6} {row['inter_system_pair_count']:5} {row['hard_conflicts']:4} {row['hard_conflict_rate'] * 100:5.1f} {row['clearance_violations']:9} {row['total_grid_route_length_m']:6.1f} {row['total_bend_count']:5} {row['total_vertical_travel_m']:8.1f}")
    print("system attempted B0-ok B1-ok B1-failed")
    for row in result["per_system"]:
        print(f"{row['system']:<11} {row['attempted_B0']:9} {row['successful_B0']:5} {row['successful_B1']:5} {row['B1_failure_count']:9}")
    print("case B0-ok B1-ok B0-hard B1-hard common")
    for case in result["cases"]:
        print(f"{case['case_id']:<8} {case['B0']['successful']:5} {case['B1']['successful']:5} {case['B0']['hard_conflicts']:7} {case['B1']['hard_conflicts']:7} {case['common_success']['common_successful_connection_count']:6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

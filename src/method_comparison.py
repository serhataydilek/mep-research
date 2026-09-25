"""Deterministic, failure-aware comparison of the existing routing methods.

This module only orchestrates existing benchmark implementations and shared
evaluators.  It does not alter route geometry, scenario inputs, or method
semantics, and deliberately produces no ranking or composite score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .baseline_comparison import _quality, _route_key, _subset_result, _successful_keys
from .clash import evaluate_route_result_conflicts
from .constructability import _method_summary, load_constructability_config, verify_route_result
from .coordinator import run_pcore_multi_round_benchmark, run_pcore_seed_benchmark
from .demand import generate_benchmark_cases, load_service_definitions
from .routing import run_b0_benchmark
from .selective_repair import run_b2_benchmark
from .sequential import run_b1_benchmark
from .voxel import build_occupancy_grids


METHOD_ORDER = ("B0", "B1", "B2", "P-CORE-SEED", "P-CORE-6C2B")


def _grid_snapshot(grids: dict[tuple[str, str], Any]) -> dict[tuple[str, str], bytes]:
    return {key: bytes(grid.cells) for key, grid in grids.items()}


def _input_paths(scenarios: Path, demands: Path, systems: Path, constraints: Path, coordinator: Path) -> list[Path]:
    return sorted({*scenarios.rglob("*.json"), *demands.rglob("*.json"), systems, constraints, coordinator}, key=lambda path: str(path))


def _input_snapshot(paths: list[Path]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _request_keys(case: dict[str, Any]) -> set[tuple[str, str]]:
    return {(request["system"], request["connection_id"]) for request in case["connection_requests"]}


def _case_routes(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["case_id"]: case for case in result["cases"]}


def _with_expected_requests(result: dict[str, Any], expected: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Supply identities to the generic C0 verifier without changing routes."""
    return {**result, "cases": [{**case, "connection_requests": expected[case["case_id"]]} for case in result["cases"]]}


def _conflicts(result: dict[str, Any], grids: dict[tuple[str, str], Any], definitions: dict[str, Any], name: str) -> dict[str, Any]:
    return evaluate_route_result_conflicts(result, grids, definitions, name, f"{name} Comparison Evaluation", "comparison", {}, include_requested_counts=True)


def _routing_metrics(result: dict[str, Any]) -> dict[str, Any]:
    routes = [route for case in result["cases"] for route in case["routes"]]
    successful = [route for route in routes if route["path_found"]]
    return {
        "total_requested_connections": len(routes),
        "successful_routes": len(successful),
        "failed_routes": len(routes) - len(successful),
        "connection_success_rate": len(successful) / len(routes) if routes else 0.0,
        "fully_routed_case_count": sum(all(route["path_found"] for route in case["routes"]) for case in result["cases"]),
    }


def _conflict_metrics(conflicts: dict[str, Any]) -> dict[str, Any]:
    metrics = conflicts["global_metrics"]
    return {
        "evaluated_inter_system_successful_route_pairs": metrics["total_inter_system_route_pairs_evaluated"],
        "hard_conflicting_route_pairs": metrics["total_hard_conflicting_route_pairs"],
        "clearance_violating_route_pairs": metrics["total_clearance_violating_route_pairs"],
        "clearance_only_pairs": metrics["total_clearance_only_route_pairs"],
        "hard_conflict_rate": metrics["hard_conflict_rate"],
        "clearance_violation_rate": metrics["clearance_violation_rate"],
    }


def _c0_metrics(verification: dict[str, Any]) -> dict[str, Any]:
    summary = _method_summary(verification)
    return {
        "routing_complete_case_count": summary["routing_complete_case_count"],
        "hard_conflict_free_case_count": summary["hard_conflict_free_case_count"],
        "clearance_compliant_case_count": summary["clearance_compliant_case_count"],
        "drainage_gravity_compliant_case_count": summary["drainage_gravity_compliant_case_count"],
        "benchmark_hard_feasible_C0_case_count": summary["benchmark_hard_feasible_case_count"],
    }


def _method_metrics(result: dict[str, Any], conflicts: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "routing": _routing_metrics(result),
        "authoritative_conflict_evaluation": _conflict_metrics(conflicts),
        "route_geometry_quality_successful_routes_only": _quality([route for case in result["cases"] for route in case["routes"]]),
        "constructability_C0": _c0_metrics(verification),
    }


def _common_success_metrics(
    results: dict[str, dict[str, Any]], grids: dict[tuple[str, str], Any], definitions: dict[str, Any]
) -> dict[str, Any]:
    cases_by_method = {name: _case_routes(result) for name, result in results.items()}
    case_ids = sorted(cases_by_method["B0"])
    keys_by_case = {
        case_id: set.intersection(*(_successful_keys(cases_by_method[name][case_id]) for name in METHOD_ORDER))
        for case_id in case_ids
    }
    method_rows = {}
    for name in METHOD_ORDER:
        subset = _subset_result(results[name], keys_by_case)
        conflicts = _conflicts(subset, grids, definitions, f"{name}-COMMON-SUCCESS")
        routes = [route for case in subset["cases"] for route in case["routes"]]
        method_rows[name] = {
            "common_successful_connection_count": len(routes),
            "route_geometry_quality": _quality(routes),
            "authoritative_conflict_evaluation": _conflict_metrics(conflicts),
        }
    return {
        "denominator_explanation": "For each case, this subset is the intersection of route identities successful in every compared method. Route geometry and conflict metrics above use only that shared population; conflict rates use successful inter-system route pairs within it.",
        "per_case_common_successful_connection_counts": [
            {"case_id": case_id, "common_successful_connection_count": len(keys_by_case[case_id])}
            for case_id in case_ids
        ],
        "methods": method_rows,
    }


def _pcore_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    summary = result["global_summary"]
    return {
        "attempted_rounds": sum(item["summary"]["rounds_attempted"] for item in result["coordination_results"]),
        "accepted_rounds": summary["accepted_rounds"],
        "rolled_back_rounds": summary["rolled_back_rounds"],
        "stop_reasons": summary["stop_reason_counts"],
        "initial_conflict_objective": summary["initial_global_conflict_objective"],
        "final_conflict_objective": summary["final_global_conflict_objective"],
        "selected_repair_candidate_count": summary["selected_repair_candidates"],
        "successful_reroute_count": summary["successful_reroutes"],
        "route_length_delta_m": summary["route_length_delta_m"],
        "bend_count_delta": summary["bend_count_delta"],
        "vertical_travel_delta_m": summary["vertical_travel_delta_m"],
    }


def compare_methods(
    scenarios: Path, demands: Path, systems: Path, constraints: Path, coordinator: Path, *, include_analysis_source: bool = False,
) -> dict[str, Any]:
    """Run all existing methods and evaluate them with common deterministic rules."""
    input_paths = _input_paths(scenarios, demands, systems, constraints, coordinator)
    inputs_before = _input_snapshot(input_paths)
    grids = build_occupancy_grids(scenarios, systems)
    grids_before = _grid_snapshot(grids)
    definitions = load_service_definitions(systems)
    c0_constraints = load_constructability_config(constraints)
    benchmark_cases = generate_benchmark_cases(scenarios, demands, systems)
    expected = {case["case_id"]: case["connection_requests"] for case in benchmark_cases}

    results = {
        "B0": run_b0_benchmark(scenarios, demands, systems, grids=grids),
        "B1": run_b1_benchmark(scenarios, demands, systems, grids=grids),
        "B2": run_b2_benchmark(scenarios, demands, systems, constraints),
        "P-CORE-SEED": run_pcore_seed_benchmark(scenarios, demands, systems, constraints, coordinator),
        "P-CORE-6C2B": run_pcore_multi_round_benchmark(scenarios, demands, systems, constraints, coordinator),
    }
    if grids_before != _grid_snapshot(grids):
        raise RuntimeError("Method comparison must not mutate immutable base occupancy grids.")
    if inputs_before != _input_snapshot(input_paths):
        raise RuntimeError("Method comparison must not mutate scenario, demand, or config inputs.")
    if len(benchmark_cases) != 12:
        raise RuntimeError("Method comparison requires the established 12 benchmark cases.")
    expected_case_ids = sorted(expected)
    for name in METHOD_ORDER:
        cases = _case_routes(results[name])
        if sorted(cases) != expected_case_ids:
            raise RuntimeError(f"{name} does not preserve the benchmark case population.")
        for case_id, case in cases.items():
            actual = {_route_key(route) for route in case["routes"]}
            if actual != _request_keys({"connection_requests": expected[case_id]}):
                raise RuntimeError(f"{name} does not preserve requested connections for {case_id}.")

    conflicts = {name: _conflicts(results[name], grids, definitions, name) for name in METHOD_ORDER}
    verifications = {
        name: verify_route_result(_with_expected_requests(results[name], expected), grids, c0_constraints, definitions)
        for name in METHOD_ORDER
    }
    comparison = {
        "comparison": {
            "phase": "6C3A",
            "method_order": list(METHOD_ORDER),
            "case_count": len(benchmark_cases),
            "requested_connection_population_is_consistent": True,
            "no_winner_or_composite_ranking_calculated": True,
            "raw_metric_denominator_explanation": "Routing rates use all requested connections. Conflict rates use only unordered inter-system pairs among successful routes. Geometry totals and means use successful routes only.",
        },
        "methods": {name: {"method": results[name]["method"], "metrics": _method_metrics(results[name], conflicts[name], verifications[name])} for name in METHOD_ORDER},
        "all_method_common_success_subset": _common_success_metrics(results, grids, definitions),
        "pcore_coordination_diagnostics": _pcore_diagnostics(results["P-CORE-6C2B"]),
        "integrity_checks": {"base_occupancy_grids_unchanged": True, "scenario_demand_and_config_inputs_unchanged": True},
    }
    if include_analysis_source:
        comparison["_analysis_source"] = {
            "benchmark_cases": benchmark_cases, "results": results, "conflicts": conflicts,
            "grids": grids, "definitions": definitions,
        }
    return comparison


def write_method_comparison(scenarios: Path, demands: Path, systems: Path, constraints: Path, coordinator: Path, output: Path) -> dict[str, Any]:
    result = compare_methods(scenarios, demands, systems, constraints, coordinator)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare existing MEP routing methods without ranking them.")
    for name in ("scenarios", "demands", "systems", "constraints", "coordinator", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    result = write_method_comparison(args.scenarios, args.demands, args.systems, args.constraints, args.coordinator, args.output)
    for name in METHOD_ORDER:
        row = result["methods"][name]["metrics"]
        print(name, row["routing"], row["authoritative_conflict_evaluation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

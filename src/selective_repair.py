"""B2 conflict-aware, single-pass selective repair of immutable B0 routes.

B2 uses Phase 6A's current-edge cover, applies ordinary A* once to each
selected route against currently frozen different-system geometry, and retains
the original route whenever that attempt fails.  It is not iterative and does
not apply gravity or constructability-aware routing policy.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .clash import evaluate_route_result_conflicts
from .conflict_diagnosis import diagnose_route_conflicts
from .constructability import _method_summary, load_constructability_config, verify_route_result
from .demand import SYSTEM_ORDER, load_service_definitions
from .routing import astar_3d, route_metrics, run_b0_benchmark
from .sequential import block_prior_routes, clone_grid
from .voxel import FREE, build_occupancy_grids


def _key(route: dict[str, Any]) -> tuple[str, str]:
    return route["system"], route["connection_id"]


def _total(routes: list[dict[str, Any]], field: str) -> int | float:
    return sum(route[field] for route in routes if route["path_found"])


def _repair_metadata(route: dict[str, Any], *, status: str, succeeded: bool | None, reason: str | None,
                     changed: bool, expanded: int) -> dict[str, Any]:
    result = deepcopy(route)
    result.update({"was_selected_for_repair": status != "UNCHANGED_NON_CANDIDATE", "repair_status": status,
                   "repair_attempt_succeeded": succeeded, "repair_failure_reason": reason,
                   "repair_path_changed": changed, "repair_attempt_expanded_nodes": expanded})
    return result


def _initial_conflicts(routing: dict[str, Any], grids: dict[tuple[str, str], Any], definitions: dict[str, Any]) -> dict[str, Any]:
    return evaluate_route_result_conflicts(routing, grids, definitions, "B0-3C", "B0 Inter-System Conflict Evaluation", "b0_search_statistics", routing["search_statistics"])


def selective_repair_pass(routing_result: dict[str, Any], diagnosis_result: dict[str, Any],
                          grids: dict[tuple[str, str], Any], definitions: dict[str, Any]) -> dict[str, Any]:
    """Perform exactly one Phase 6A-ordered pass without mutating any input."""
    before = {key: bytes(grid.cells) for key, grid in grids.items()}
    diagnosis_cases = {case["case_id"]: case for case in diagnosis_result["cases"]}
    output_cases, summaries = [], []
    for initial_case in routing_result["cases"]:
        diagnosis_case = diagnosis_cases[initial_case["case_id"]]
        selected_records = sorted(diagnosis_case["selected_repair_candidates"], key=lambda row: (row["component_id"], row["selection_rank_in_component"]))
        selected = {(row["system"], row["connection_id"]) for row in selected_records}
        original = {_key(route): route for route in initial_case["routes"]}
        final = {key: _repair_metadata(route, status="UNCHANGED_NON_CANDIDATE", succeeded=None, reason=None, changed=False, expanded=0)
                 for key, route in original.items() if key not in selected}
        frozen = [final[key] for key in sorted(final)]
        attempts = []
        for candidate in selected_records:
            key = candidate["system"], candidate["connection_id"]
            original_route = original[key]
            base = grids[(initial_case["scenario_id"], original_route["system"])]
            derived = clone_grid(base)
            blockers = [route for route in frozen if route["system"] != original_route["system"]]
            block_prior_routes(derived, blockers, definitions[original_route["system"]], definitions)
            start_index, end_index = tuple(original_route["start"]["grid_index"]), tuple(original_route["end"]["grid_index"])
            if derived.state_at(*start_index) != FREE or derived.state_at(*end_index) != FREE:
                attempted, failure = None, "endpoint_blocked_by_frozen_route"
                expanded = 0
            else:
                attempted = astar_3d(derived, start_index, end_index)
                failure = None if attempted.path_found else "no_path_with_frozen_layout"
                expanded = attempted.expanded_node_count
            if failure is None:
                repaired = deepcopy(original_route)
                repaired.update(route_metrics(attempted, derived.spec.voxel_size_m))
                repaired["path_cells"] = [list(cell) for cell in attempted.path_cells]
                changed = repaired["path_cells"] != original_route["path_cells"]
                route = _repair_metadata(repaired, status="REPAIRED", succeeded=True, reason=None, changed=changed, expanded=expanded)
            else:
                route = _repair_metadata(original_route, status="RETAINED_ORIGINAL_AFTER_FAILED_REPAIR", succeeded=False,
                                         reason=failure, changed=False, expanded=expanded)
            final[key] = route
            frozen.append(route)
            attempts.append(route)
        routes = [final[_key(route)] for route in initial_case["routes"]]
        output_cases.append({"case_id": initial_case["case_id"], "scenario_id": initial_case["scenario_id"],
                             "demand_profile_id": initial_case["demand_profile_id"], "connection_count": len(routes),
                             "successful_route_count": sum(route["path_found"] for route in routes),
                             "failed_route_count": sum(not route["path_found"] for route in routes),
                             "connection_success_rate": sum(route["path_found"] for route in routes) / len(routes) if routes else 0.0,
                             "routes": routes})
        summaries.append({"case_id": initial_case["case_id"], "selected_repair_candidate_count": len(selected_records),
                          "repair_attempt_count": len(attempts), "successful_repair_attempt_count": sum(route["repair_attempt_succeeded"] is True for route in attempts),
                          "failed_repair_attempt_count": sum(route["repair_attempt_succeeded"] is False for route in attempts),
                          "repaired_path_changed_count": sum(route["repair_path_changed"] for route in attempts),
                          "retained_original_after_failed_repair_count": sum(route["repair_status"] == "RETAINED_ORIGINAL_AFTER_FAILED_REPAIR" for route in attempts),
                          "initial_total_route_length_m": _total(initial_case["routes"], "grid_route_length_m"),
                          "final_total_route_length_m": _total(routes, "grid_route_length_m"),
                          "initial_total_bend_count": _total(initial_case["routes"], "bend_count"), "final_total_bend_count": _total(routes, "bend_count"),
                          "initial_total_vertical_travel_m": _total(initial_case["routes"], "vertical_travel_m"), "final_total_vertical_travel_m": _total(routes, "vertical_travel_m")})
    if before != {key: bytes(grid.cells) for key, grid in grids.items()}:
        raise RuntimeError("B2 must not mutate Phase 3A base occupancy grids.")
    return {"method": {"id": "B2", "name": "Conflict-Aware Single-Pass Selective Repair"}, "cases": output_cases,
            "repair_summaries": summaries}


def _merge_summaries(b2: dict[str, Any], initial_conflicts: dict[str, Any], final_conflicts: dict[str, Any]) -> None:
    initial = {case["case_id"]: case for case in initial_conflicts["cases"]}
    final = {case["case_id"]: case for case in final_conflicts["cases"]}
    for summary in b2["repair_summaries"]:
        before, after = initial[summary["case_id"]], final[summary["case_id"]]
        summary.update({"initial_successful_routes": before["successful_route_count"], "final_successful_routes": after["successful_route_count"],
                        "initial_hard_edges": before["hard_conflicting_route_pair_count"], "final_hard_edges": after["hard_conflicting_route_pair_count"],
                        "initial_clearance_only_edges": before["clearance_only_route_pair_count"], "final_clearance_only_edges": after["clearance_only_route_pair_count"],
                        "initial_total_violation_edges": len(before["conflict_details"]), "final_total_violation_edges": len(after["conflict_details"]),
                        "hard_edge_delta": after["hard_conflicting_route_pair_count"] - before["hard_conflicting_route_pair_count"],
                        "total_violation_edge_delta": len(after["conflict_details"]) - len(before["conflict_details"]),
                        "route_length_delta_m": summary["final_total_route_length_m"] - summary["initial_total_route_length_m"],
                        "bend_delta": summary["final_total_bend_count"] - summary["initial_total_bend_count"],
                        "vertical_travel_delta": summary["final_total_vertical_travel_m"] - summary["initial_total_vertical_travel_m"]})


def _global(b2: dict[str, Any], initial: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    routes = [route for case in b2["cases"] for route in case["routes"]]
    attempts = [route for route in routes if route["was_selected_for_repair"]]
    initial_metrics, final_metrics = initial["global_metrics"], final["global_metrics"]
    return {"total_route_requests": len(routes), "successful_routes": sum(route["path_found"] for route in routes),
            "connection_success_rate": sum(route["path_found"] for route in routes) / len(routes),
            "initial_hard_edges": initial_metrics["total_hard_conflicting_route_pairs"], "final_hard_edges": final_metrics["total_hard_conflicting_route_pairs"],
            "initial_clearance_only_edges": initial_metrics["total_clearance_only_route_pairs"], "final_clearance_only_edges": final_metrics["total_clearance_only_route_pairs"],
            "initial_total_violation_edges": initial_metrics["total_hard_conflicting_route_pairs"] + initial_metrics["total_clearance_only_route_pairs"],
            "final_total_violation_edges": final_metrics["total_hard_conflicting_route_pairs"] + final_metrics["total_clearance_only_route_pairs"],
            "selected_repair_candidate_instances": len(attempts), "repair_attempts": len(attempts),
            "successful_repair_attempts": sum(route["repair_attempt_succeeded"] is True for route in attempts),
            "failed_repair_attempts": sum(route["repair_attempt_succeeded"] is False for route in attempts),
            "retained_original_after_failed_repair": sum(route["repair_status"] == "RETAINED_ORIGINAL_AFTER_FAILED_REPAIR" for route in attempts),
            "total_route_length_before_m": sum(summary["initial_total_route_length_m"] for summary in b2["repair_summaries"]),
            "total_route_length_after_m": sum(summary["final_total_route_length_m"] for summary in b2["repair_summaries"]),
            "total_bends_before": sum(summary["initial_total_bend_count"] for summary in b2["repair_summaries"]),
            "total_bends_after": sum(summary["final_total_bend_count"] for summary in b2["repair_summaries"]),
            "total_vertical_travel_before_m": sum(summary["initial_total_vertical_travel_m"] for summary in b2["repair_summaries"]),
            "total_vertical_travel_after_m": sum(summary["final_total_vertical_travel_m"] for summary in b2["repair_summaries"]),
            "failed_repairs_by_reason": dict(sorted(Counter(route["repair_failure_reason"] for route in attempts if route["repair_failure_reason"]).items()))}


def _by_system(b2: dict[str, Any]) -> list[dict[str, Any]]:
    routes = [route for case in b2["cases"] for route in case["routes"]]
    return [{"system": system, "selected_candidates": sum(route["system"] == system and route["was_selected_for_repair"] for route in routes),
             "successful_repairs": sum(route["system"] == system and route["repair_attempt_succeeded"] is True for route in routes),
             "failed_repairs": sum(route["system"] == system and route["repair_attempt_succeeded"] is False for route in routes),
             "changed_paths": sum(route["system"] == system and route["repair_path_changed"] for route in routes)} for system in SYSTEM_ORDER]


def run_b2_benchmark(scenarios: Path, demands: Path, systems_path: Path, constraints_path: Path) -> dict[str, Any]:
    """Run B0 -> diagnose -> one B2 pass -> evaluate -> final diagnose -> C0; stop."""
    grids = build_occupancy_grids(scenarios, systems_path)
    definitions = load_service_definitions(systems_path)
    b0 = run_b0_benchmark(scenarios, demands, systems_path, grids=grids)
    initial_conflicts = _initial_conflicts(b0, grids, definitions)
    initial_diagnosis = diagnose_route_conflicts(b0, initial_conflicts)
    b2 = selective_repair_pass(b0, initial_diagnosis, grids, definitions)
    final_conflicts = evaluate_route_result_conflicts(b2, grids, definitions, "B2-6B", "B2 Inter-System Conflict Evaluation", "b2_repair_summary", {})
    _merge_summaries(b2, initial_conflicts, final_conflicts)
    verification = verify_route_result(b2, grids, load_constructability_config(constraints_path), definitions)
    b2.update({"global_summary": _global(b2, initial_conflicts, final_conflicts), "repair_results_by_system": _by_system(b2),
               "final_conflict_metrics": final_conflicts["global_metrics"], "final_diagnosis": diagnose_route_conflicts(b2, final_conflicts)["global_metrics"],
               "c0_summary": _method_summary(verification)})
    return b2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run B2 conflict-aware single-pass selective repair.")
    parser.add_argument("--scenarios", required=True, type=Path); parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path); parser.add_argument("--constraints", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path); args = parser.parse_args()
    result = run_b2_benchmark(args.scenarios, args.demands, args.systems, args.constraints)
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("case routes candidates repair-ok repair-failed hard-before hard-after clearance-only-before clearance-only-after length-before length-after")
    for summary in result["repair_summaries"]:
        print(f"{summary['case_id']} {summary['final_successful_routes']} {summary['selected_repair_candidate_count']} {summary['successful_repair_attempt_count']} {summary['failed_repair_attempt_count']} {summary['initial_hard_edges']} {summary['final_hard_edges']} {summary['initial_clearance_only_edges']} {summary['final_clearance_only_edges']} {summary['initial_total_route_length_m']:.1f} {summary['final_total_route_length_m']:.1f}")
    print("system selected repair-ok repair-failed changed-paths")
    for row in result["repair_results_by_system"]: print(row["system"], row["selected_candidates"], row["successful_repairs"], row["failed_repairs"], row["changed_paths"])
    print("global", result["global_summary"]); print("C0", result["c0_summary"])
    return 0


if __name__ == "__main__": raise SystemExit(main())

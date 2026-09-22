"""Deterministic conflict diagnosis and repair-candidate selection.

Phase 6A diagnoses immutable routed geometry using Phase 3C's existing
conflict records.  Its per-component greedy edge cover is deliberately not an
exact minimum vertex cover and is not an engineering-priority policy.  The
stable system order is used only as a final tie-break.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .clash import evaluate_route_result_conflicts
from .demand import SYSTEM_ORDER, load_service_definitions
from .routing import run_b0_benchmark
from .voxel import build_occupancy_grids


SYSTEM_RANK = {system: index for index, system in enumerate(SYSTEM_ORDER)}


def _key(route: dict[str, Any] | tuple[str, str]) -> tuple[int, str]:
    system, connection_id = (route["system"], route["connection_id"]) if isinstance(route, dict) else route
    return SYSTEM_RANK[system], connection_id


def _ref(key: tuple[str, str]) -> dict[str, str]:
    return {"system": key[0], "connection_id": key[1]}


def _route_metadata(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "system": route["system"], "connection_id": route["connection_id"],
        "path_found": route["path_found"], "grid_route_length_m": route["grid_route_length_m"],
        "bend_count": route["bend_count"], "vertical_travel_m": route["vertical_travel_m"],
    }


def _edge_records(conflict_case: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    for record in conflict_case["conflict_details"]:
        hard = record["hard_envelope_conflict"]
        clearance = record["clearance_violation"]
        if not (hard or clearance) or record["route_a_system"] == record["route_b_system"]:
            continue
        first = (record["route_a_system"], record["route_a_connection_id"])
        second = (record["route_b_system"], record["route_b_connection_id"])
        route_a, route_b = sorted((first, second), key=_key)
        edges.append({
            "route_a": _ref(route_a), "route_b": _ref(route_b), "system_pair": record["system_pair"],
            "severity": "HARD" if hard else "CLEARANCE_ONLY",
            "hard_segment_intersection_count": record["hard_segment_intersection_count"],
            "clearance_segment_intersection_count": record["clearance_segment_intersection_count"],
            "shared_centerline_cell_count": record["shared_centerline_cell_count"],
        })
    return sorted(edges, key=lambda edge: (_key(edge["route_a"]), _key(edge["route_b"])))


def _edge_keys(edge: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    return ((edge["route_a"]["system"], edge["route_a"]["connection_id"]),
            (edge["route_b"]["system"], edge["route_b"]["connection_id"]))


def _burdens(routes: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, int]]:
    result = {key: {"hard_conflict_partner_count": 0, "clearance_only_partner_count": 0,
                    "total_violating_partner_count": 0, "hard_segment_intersection_burden": 0,
                    "clearance_segment_intersection_burden": 0,
                    "shared_centerline_conflict_partner_count": 0} for key in routes}
    partners = {key: {"hard": set(), "clearance": set(), "all": set(), "shared": set()} for key in routes}
    for edge in edges:
        first, second = _edge_keys(edge)
        for key, other in ((first, second), (second, first)):
            partners[key]["all"].add(other)
            if edge["severity"] == "HARD":
                partners[key]["hard"].add(other)
            else:
                partners[key]["clearance"].add(other)
            if edge["shared_centerline_cell_count"]:
                partners[key]["shared"].add(other)
            result[key]["hard_segment_intersection_burden"] += edge["hard_segment_intersection_count"]
            result[key]["clearance_segment_intersection_burden"] += edge["clearance_segment_intersection_count"]
    for key, values in partners.items():
        result[key]["hard_conflict_partner_count"] = len(values["hard"])
        result[key]["clearance_only_partner_count"] = len(values["clearance"])
        result[key]["total_violating_partner_count"] = len(values["all"])
        result[key]["shared_centerline_conflict_partner_count"] = len(values["shared"])
    return result


def _components(edges: list[dict[str, Any]]) -> list[tuple[list[tuple[str, str]], list[dict[str, Any]]]]:
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for edge in edges:
        first, second = _edge_keys(edge)
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    remaining = set(adjacency)
    components = []
    while remaining:
        start = min(remaining, key=_key)
        stack, nodes = [start], set()
        while stack:
            node = stack.pop()
            if node in nodes:
                continue
            nodes.add(node)
            stack.extend(sorted(adjacency[node] - nodes, key=_key, reverse=True))
        remaining -= nodes
        node_list = sorted(nodes, key=_key)
        component_edges = [edge for edge in edges if set(_edge_keys(edge)).issubset(nodes)]
        components.append((node_list, component_edges))
    return sorted(components, key=lambda item: _key(item[0][0]))


def _selection(nodes: list[tuple[str, str]], edges: list[dict[str, Any]], burdens: dict[tuple[str, str], dict[str, int]]) -> list[dict[str, Any]]:
    unresolved = list(edges)
    selected = []
    while unresolved:
        rankings = []
        for node in nodes:
            covered = [edge for edge in unresolved if node in _edge_keys(edge)]
            if not covered:
                continue
            hard = [edge for edge in covered if edge["severity"] == "HARD"]
            rankings.append((node, covered, hard, (
                -len(hard), -len(covered),
                -sum(edge["hard_segment_intersection_count"] for edge in covered),
                -sum(edge["clearance_segment_intersection_count"] for edge in covered), _key(node),
            )))
        node, covered, hard, _ = min(rankings, key=lambda item: item[3])
        unresolved = [edge for edge in unresolved if edge not in covered]
        selected.append({
            "selection_rank_in_component": len(selected) + 1, **_ref(node),
            "hard_edges_covered_at_selection": len(hard),
            "total_edges_covered_at_selection": len(covered),
            "hard_segment_intersections_covered_at_selection": sum(edge["hard_segment_intersection_count"] for edge in covered),
            "clearance_segment_intersections_covered_at_selection": sum(edge["clearance_segment_intersection_count"] for edge in covered),
            "remaining_unresolved_edges_after_selection": len(unresolved),
            "route_burden_before_selection": dict(burdens[node]),
        })
    return selected


def _diagnose_case(routing_case: dict[str, Any], conflict_case: dict[str, Any]) -> dict[str, Any]:
    routes = {(route["system"], route["connection_id"]): _route_metadata(route)
              for route in routing_case["routes"] if route["path_found"]}
    edges = _edge_records(conflict_case)
    burdens = _burdens(routes, edges)
    diagnostics = [{**routes[key], **burdens[key]} for key in sorted(routes, key=_key)]
    components = []
    candidates = []
    for index, (nodes, component_edges) in enumerate(_components(edges), 1):
        selected = _selection(nodes, component_edges, burdens)
        component_id = f"component_{index:03d}"
        for candidate in selected:
            candidate["component_id"] = component_id
        candidates.extend(selected)
        components.append({
            "component_id": component_id, "route_count": len(nodes), "edge_count": len(component_edges),
            "hard_edge_count": sum(edge["severity"] == "HARD" for edge in component_edges),
            "clearance_only_edge_count": sum(edge["severity"] == "CLEARANCE_ONLY" for edge in component_edges),
            "systems_present": [system for system in SYSTEM_ORDER if any(node[0] == system for node in nodes)],
            "routes": [_ref(node) for node in nodes], "edges": component_edges,
        })
    conflicting = sum(burden["total_violating_partner_count"] > 0 for burden in burdens.values())
    ordered_burden = sorted(diagnostics, key=lambda route: (
        -route["hard_conflict_partner_count"], -route["total_violating_partner_count"],
        -route["hard_segment_intersection_burden"], -route["clearance_segment_intersection_burden"], _key(route)))
    return {
        "case_id": routing_case["case_id"], "scenario_id": routing_case["scenario_id"],
        "demand_profile_id": routing_case["demand_profile_id"],
        "successful_route_count": len(routes), "conflicting_route_count": conflicting,
        "conflict_free_route_count": len(routes) - conflicting, "violation_edge_count": len(edges),
        "hard_edge_count": sum(edge["severity"] == "HARD" for edge in edges),
        "clearance_only_edge_count": sum(edge["severity"] == "CLEARANCE_ONLY" for edge in edges),
        "conflict_component_count": len(components),
        "largest_component_route_count": max((component["route_count"] for component in components), default=0),
        "largest_component_edge_count": max((component["edge_count"] for component in components), default=0),
        "selected_repair_candidate_count": len(candidates),
        "selected_repair_candidate_fraction_of_successful_routes": len(candidates) / len(routes) if routes else 0.0,
        "current_violation_edge_cover_complete": all(
            any(_edge_keys(edge)[0] == (candidate["system"], candidate["connection_id"]) or
                _edge_keys(edge)[1] == (candidate["system"], candidate["connection_id"]) for candidate in candidates)
            for edge in edges),
        "route_diagnostics": diagnostics, "highest_burden_routes": ordered_burden,
        "component_diagnostics": components, "selected_repair_candidates": candidates,
    }


def _group(cases: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [{key: value, "selected_repair_candidate_instances": sum(
        case["selected_repair_candidate_count"] for case in cases if case[key] == value)}
        for value in sorted({case[key] for case in cases})]


def diagnose_route_conflicts(routing_result: dict[str, Any], conflict_result: dict[str, Any]) -> dict[str, Any]:
    """Diagnose compatible immutable routing and evaluator results without mutation."""
    conflict_cases = {case["case_id"]: case for case in conflict_result["cases"]}
    cases = [_diagnose_case(case, conflict_cases[case["case_id"]]) for case in routing_result["cases"]]
    systems = []
    for system in SYSTEM_ORDER:
        routes = [route for case in cases for route in case["route_diagnostics"] if route["system"] == system]
        selected = [candidate for case in cases for candidate in case["selected_repair_candidates"] if candidate["system"] == system]
        systems.append({"system": system, "route_instances": len(routes),
            "conflicting_route_instances": sum(route["total_violating_partner_count"] > 0 for route in routes),
            "selected_repair_candidate_instances": len(selected),
            "hard_partner_burden_total": sum(route["hard_conflict_partner_count"] for route in routes),
            "clearance_only_partner_burden_total": sum(route["clearance_only_partner_count"] for route in routes)})
    return {"method": {"id": "6A", "name": "Conflict Diagnosis and Repair Candidate Selection",
             "selection": "Per-component deterministic greedy current-violation edge cover; not exact minimum vertex cover.",
             "system_order_note": "SYSTEM_ORDER is only the final deterministic tie-break, not an engineering priority."},
        "cases": cases, "grouped_selected_candidate_counts": {"system": systems, "scenario": _group(cases, "scenario_id"), "demand_profile": _group(cases, "demand_profile_id")},
        "global_metrics": {"total_route_instances": sum(case["successful_route_count"] for case in cases),
            "total_conflicting_route_instances": sum(case["conflicting_route_count"] for case in cases),
            "total_conflict_free_route_instances": sum(case["conflict_free_route_count"] for case in cases),
            "total_violation_edges": sum(case["violation_edge_count"] for case in cases),
            "total_hard_edges": sum(case["hard_edge_count"] for case in cases),
            "total_clearance_only_edges": sum(case["clearance_only_edge_count"] for case in cases),
            "total_conflict_components": sum(case["conflict_component_count"] for case in cases),
            "total_selected_repair_candidate_instances": sum(case["selected_repair_candidate_count"] for case in cases),
            "mean_selected_candidates_per_case": sum(case["selected_repair_candidate_count"] for case in cases) / len(cases) if cases else 0.0,
            "mean_conflicting_routes_per_case": sum(case["conflicting_route_count"] for case in cases) / len(cases) if cases else 0.0}}


def diagnose_b0_conflicts(scenarios_directory: Path, demands_directory: Path, systems_path: Path) -> dict[str, Any]:
    """Build B0 and its shared-evaluator records, then diagnose without modifying either."""
    grids = build_occupancy_grids(scenarios_directory, systems_path)
    b0 = run_b0_benchmark(scenarios_directory, demands_directory, systems_path, grids=grids)
    conflicts = evaluate_route_result_conflicts(b0, grids, load_service_definitions(systems_path), "B0-3C", "B0 Inter-System Conflict Evaluation", "b0_search_statistics", b0["search_statistics"])
    return diagnose_route_conflicts(b0, conflicts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose immutable B0 conflict records and select repair candidates.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = diagnose_b0_conflicts(args.scenarios, args.demands, args.systems)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("case routes conflicting-routes hard-edges clearance-only components repair-candidates edge-cover")
    for case in result["cases"]:
        print(f"{case['case_id']} {case['successful_route_count']} {case['conflicting_route_count']} {case['hard_edge_count']} {case['clearance_only_edge_count']} {case['conflict_component_count']} {case['selected_repair_candidate_count']} {case['current_violation_edge_cover_complete']}")
    print("system routes conflicting selected hard-burden clearance-only-burden")
    for row in result["grouped_selected_candidate_counts"]["system"]:
        print(f"{row['system']} {row['route_instances']} {row['conflicting_route_instances']} {row['selected_repair_candidate_instances']} {row['hard_partner_burden_total']} {row['clearance_only_partner_burden_total']}")
    print("global", result["global_metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

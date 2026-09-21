"""Deterministic Phase 3C evaluation of jointly placed, independent B0 paths.

This is a benchmark proxy: maximal collinear B0 centerlines are swept into
nominal service AABBs, then compared pairwise.  It neither changes a route nor
claims exact fabrication clash detection or layout feasibility.
"""

from __future__ import annotations

import argparse
from itertools import combinations
import json
from math import floor
from pathlib import Path
from typing import Any

from .demand import SYSTEM_ORDER, load_service_definitions
from .route_geometry import Segment, compress_path_cells, nominal_half_extents, swept_segment_aabb
from .routing import run_b0_benchmark
from .sequential import run_b1_benchmark
from .voxel import GridSpec, build_occupancy_grids


EPSILON = 1e-9
CLEARANCE_M = 0.05
HALF_CLEARANCE_M = CLEARANCE_M / 2
SYSTEM_RANK = {system: index for index, system in enumerate(SYSTEM_ORDER)}
SYSTEM_PAIR_ORDER = tuple(
    f"{first}|{second}" for index, first in enumerate(SYSTEM_ORDER) for second in SYSTEM_ORDER[index + 1 :]
)




def positive_aabb_overlap(
    first: tuple[float, float, float, float, float, float],
    second: tuple[float, float, float, float, float, float],
) -> bool:
    """Return true only for positive overlap on all axes; touching is excluded."""
    return all(
        min(first[axis + 1], second[axis + 1]) - max(first[axis], second[axis]) > EPSILON
        for axis in (0, 2, 4)
    )


def _aabb_bins(aabb: tuple[float, float, float, float, float, float], size: float = 1.0):
    """Yield deterministic broad-phase spatial bins touched by an AABB."""
    ranges = [range(floor(aabb[axis] / size), floor(aabb[axis + 1] / size) + 1) for axis in (0, 2, 4)]
    for x in ranges[0]:
        for y in ranges[1]:
            for z in ranges[2]:
                yield x, y, z


def _system_pair(first: str, second: str) -> str:
    return "|".join(sorted((first, second), key=SYSTEM_RANK.__getitem__))


def _route_key(route: dict[str, Any]) -> tuple[int, str]:
    return SYSTEM_RANK[route["system"]], route["connection_id"]


def _evaluate_route_pair(
    case_id: str,
    route_a: dict[str, Any],
    route_b: dict[str, Any],
    spec: GridSpec,
    definitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    segments_a = compress_path_cells(route_a["path_cells"], spec)
    segments_b = compress_path_cells(route_b["path_cells"], spec)
    planar_a, vertical_a = nominal_half_extents(definitions[route_a["system"]])
    planar_b, vertical_b = nominal_half_extents(definitions[route_b["system"]])
    hard_aabbs_a = [swept_segment_aabb(segment, planar_a, vertical_a) for segment in segments_a]
    hard_aabbs_b = [swept_segment_aabb(segment, planar_b, vertical_b) for segment in segments_b]
    clearance_aabbs_a = [
        swept_segment_aabb(segment, planar_a, vertical_a, HALF_CLEARANCE_M) for segment in segments_a
    ]
    clearance_aabbs_b = [
        swept_segment_aabb(segment, planar_b, vertical_b, HALF_CLEARANCE_M) for segment in segments_b
    ]
    broad_phase: dict[tuple[int, int, int], list[int]] = {}
    for index, aabb in enumerate(clearance_aabbs_b):
        for cell in _aabb_bins(aabb):
            broad_phase.setdefault(cell, []).append(index)
    hard_count = 0
    clearance_count = 0
    for index_a, clearance_aabb_a in enumerate(clearance_aabbs_a):
        candidate_indices = {
            index_b for cell in _aabb_bins(clearance_aabb_a) for index_b in broad_phase.get(cell, [])
        }
        for index_b in sorted(candidate_indices):
            if positive_aabb_overlap(hard_aabbs_a[index_a], hard_aabbs_b[index_b]):
                hard_count += 1
            if positive_aabb_overlap(clearance_aabb_a, clearance_aabbs_b[index_b]):
                clearance_count += 1
    hard = hard_count > 0
    clearance = clearance_count > 0
    return {
        "case_id": case_id,
        "route_a_connection_id": route_a["connection_id"],
        "route_a_system": route_a["system"],
        "route_b_connection_id": route_b["connection_id"],
        "route_b_system": route_b["system"],
        "system_pair": _system_pair(route_a["system"], route_b["system"]),
        "hard_envelope_conflict": hard,
        "clearance_violation": clearance,
        "clearance_only_violation": clearance and not hard,
        "hard_segment_intersection_count": hard_count,
        "clearance_segment_intersection_count": clearance_count,
        "shared_centerline_cell_count": len(
            {tuple(cell) for cell in route_a["path_cells"]}
            & {tuple(cell) for cell in route_b["path_cells"]}
        ),
    }


def _pair_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(records)
    hard = sum(record["hard_envelope_conflict"] for record in records)
    clearance = sum(record["clearance_violation"] for record in records)
    clearance_only = sum(record["clearance_only_violation"] for record in records)
    return {
        "route_pair_count": count,
        "hard_conflicting_route_pair_count": hard,
        "clearance_violating_route_pair_count": clearance,
        "clearance_only_route_pair_count": clearance_only,
        "hard_conflict_rate": hard / count if count else 0.0,
        "clearance_violation_rate": clearance / count if count else 0.0,
    }


def evaluate_route_result_conflicts(
    routing_result: dict[str, Any], grids: dict[tuple[str, str], Any],
    definitions: dict[str, dict[str, Any]], evaluation_id: str, evaluation_name: str,
    summary_key: str, summary_value: dict[str, Any], *, include_requested_counts: bool = False,
) -> dict[str, Any]:
    """Evaluate compatible successful-route geometry with the single Phase 3C rule set."""
    evaluated_cases = []
    for case in routing_result["cases"]:
        successful = sorted((route for route in case["routes"] if route["path_found"]), key=_route_key)
        route_burdens = {
            (route["system"], route["connection_id"]): {"hard": set(), "clearance": set()}
            for route in successful
        }
        inter_records = []
        same_system_pair_count = 0
        same_system_overlapping_pair_count = 0
        same_system_shared_centerline_cell_count = 0
        spec = grids[(case["scenario_id"], SYSTEM_ORDER[0])].spec
        for route_a, route_b in combinations(successful, 2):
            if route_a["system"] == route_b["system"]:
                same_system_pair_count += 1
                shared = len({tuple(cell) for cell in route_a["path_cells"]} & {tuple(cell) for cell in route_b["path_cells"]})
                same_system_shared_centerline_cell_count += shared
                same_system_overlapping_pair_count += shared > 0
                continue
            record = _evaluate_route_pair(case["case_id"], route_a, route_b, spec, definitions)
            inter_records.append(record)
            if record["hard_envelope_conflict"]:
                route_burdens[(route_a["system"], route_a["connection_id"])]["hard"].add((route_b["system"], route_b["connection_id"]))
                route_burdens[(route_b["system"], route_b["connection_id"])]["hard"].add((route_a["system"], route_a["connection_id"]))
            if record["clearance_violation"]:
                route_burdens[(route_a["system"], route_a["connection_id"])]["clearance"].add((route_b["system"], route_b["connection_id"]))
                route_burdens[(route_b["system"], route_b["connection_id"])]["clearance"].add((route_a["system"], route_a["connection_id"]))
        summaries_by_pair = []
        for system_pair in SYSTEM_PAIR_ORDER:
            summary = _pair_summary([record for record in inter_records if record["system_pair"] == system_pair])
            summaries_by_pair.append({"system_pair": system_pair, **summary})
        routes = []
        for route in case["routes"]:
            enriched = dict(route)
            burden = route_burdens.get((route["system"], route["connection_id"]))
            if burden is not None:
                enriched["inter_system_hard_conflict_partner_count"] = len(burden["hard"])
                enriched["inter_system_clearance_violation_partner_count"] = len(burden["clearance"])
            routes.append(enriched)
        primary = _pair_summary(inter_records)
        hard_segments = sum(record["hard_segment_intersection_count"] for record in inter_records)
        clearance_segments = sum(record["clearance_segment_intersection_count"] for record in inter_records)
        details = [record for record in inter_records if record["hard_envelope_conflict"] or record["clearance_violation"]]
        evaluated_case = {
            "case_id": case["case_id"], "scenario_id": case["scenario_id"], "demand_profile_id": case["demand_profile_id"],
            "successful_route_count": len(successful),
            **primary,
            "inter_system_route_pair_count": primary["route_pair_count"],
            "hard_segment_intersection_count": hard_segments,
            "clearance_segment_intersection_count": clearance_segments,
            "inter_system_shared_centerline_pair_count": sum(record["shared_centerline_cell_count"] > 0 for record in inter_records),
            "all_inter_system_hard_conflict_free": primary["hard_conflicting_route_pair_count"] == 0,
            "all_inter_system_clearance_compliant": primary["clearance_violating_route_pair_count"] == 0,
            "same_system_route_pair_count": same_system_pair_count,
            "same_system_overlapping_pair_count": same_system_overlapping_pair_count,
            "same_system_shared_centerline_cell_count": same_system_shared_centerline_cell_count,
            "system_pair_summaries": summaries_by_pair,
            "conflict_details": details,
            "routes": routes,
        }
        if include_requested_counts:
            evaluated_case["requested_route_count"] = case["connection_count"]
            evaluated_case["failed_route_count"] = case["connection_count"] - len(successful)
        evaluated_cases.append(evaluated_case)
    return {
        "method": {"id": evaluation_id, "name": evaluation_name},
        summary_key: summary_value,
        "cases": evaluated_cases,
        "grouped_metrics": {
            "scenario": _group_metrics(evaluated_cases, "scenario_id"),
            "demand_profile": _group_metrics(evaluated_cases, "demand_profile_id"),
        },
        "system_pair_metrics": _system_pair_metrics(evaluated_cases),
        "global_metrics": _global_metrics(evaluated_cases),
    }


def evaluate_b0_conflicts(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path
) -> dict[str, Any]:
    """Jointly measure, but never coordinate, all independent B0 route instances."""
    grids = build_occupancy_grids(scenarios_directory, systems_path)
    before = {key: bytes(grid.cells) for key, grid in grids.items()}
    b0 = run_b0_benchmark(scenarios_directory, demands_directory, systems_path, grids=grids)
    if before != {key: bytes(grid.cells) for key, grid in grids.items()}:
        raise RuntimeError("B0 conflict evaluation must not mutate Phase 3A occupancy.")
    return evaluate_route_result_conflicts(
        b0, grids, load_service_definitions(systems_path), "B0-3C",
        "B0 Inter-System Conflict Evaluation", "b0_search_statistics", b0["search_statistics"],
    )


def evaluate_b1_conflicts(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path
) -> dict[str, Any]:
    """Evaluate only successful B1 geometry while retaining requested/failure counts."""
    grids = build_occupancy_grids(scenarios_directory, systems_path)
    before = {key: bytes(grid.cells) for key, grid in grids.items()}
    b1 = run_b1_benchmark(scenarios_directory, demands_directory, systems_path, grids=grids)
    if before != {key: bytes(grid.cells) for key, grid in grids.items()}:
        raise RuntimeError("B1 conflict evaluation must not mutate Phase 3A occupancy.")
    return evaluate_route_result_conflicts(
        b1, grids, load_service_definitions(systems_path), "B1-4B",
        "B1 Inter-System Conflict Evaluation", "b1_global_summary", b1["global_summary"],
        include_requested_counts=True,
    )


def _group_metrics(cases: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped = []
    for value in sorted({case[key] for case in cases}):
        matching = [case for case in cases if case[key] == value]
        pairs = sum(case["route_pair_count"] for case in matching)
        hard = sum(case["hard_conflicting_route_pair_count"] for case in matching)
        clearance = sum(case["clearance_violating_route_pair_count"] for case in matching)
        grouped.append({
            key: value, "route_pair_count": pairs, "hard_conflicting_route_pair_count": hard,
            "clearance_violating_route_pair_count": clearance,
            "hard_conflict_rate": hard / pairs if pairs else 0.0,
            "clearance_violation_rate": clearance / pairs if pairs else 0.0,
        })
    return grouped


def _global_metrics(cases: list[dict[str, Any]]) -> dict[str, int | float]:
    pairs = sum(case["route_pair_count"] for case in cases)
    hard = sum(case["hard_conflicting_route_pair_count"] for case in cases)
    clearance = sum(case["clearance_violating_route_pair_count"] for case in cases)
    return {
        "total_route_instances": sum(len(case["routes"]) for case in cases),
        "total_inter_system_route_pairs_evaluated": pairs,
        "total_hard_conflicting_route_pairs": hard,
        "total_clearance_violating_route_pairs": clearance,
        "total_clearance_only_route_pairs": sum(case["clearance_only_route_pair_count"] for case in cases),
        "hard_conflict_rate": hard / pairs if pairs else 0.0,
        "clearance_violation_rate": clearance / pairs if pairs else 0.0,
        "hard_conflict_free_case_count": sum(case["all_inter_system_hard_conflict_free"] for case in cases),
        "clearance_compliant_case_count": sum(case["all_inter_system_clearance_compliant"] for case in cases),
    }


def _system_pair_metrics(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate the stable ten inter-system pairs across all benchmark cases."""
    rows = []
    for pair in SYSTEM_PAIR_ORDER:
        summaries = [
            summary for case in cases for summary in case["system_pair_summaries"]
            if summary["system_pair"] == pair
        ]
        count = sum(summary["route_pair_count"] for summary in summaries)
        hard = sum(summary["hard_conflicting_route_pair_count"] for summary in summaries)
        clearance = sum(summary["clearance_violating_route_pair_count"] for summary in summaries)
        clearance_only = sum(summary["clearance_only_route_pair_count"] for summary in summaries)
        rows.append({
            "system_pair": pair, "route_pair_count": count,
            "hard_conflicting_route_pair_count": hard,
            "clearance_violating_route_pair_count": clearance,
            "clearance_only_route_pair_count": clearance_only,
            "hard_conflict_rate": hard / count if count else 0.0,
            "clearance_violation_rate": clearance / count if count else 0.0,
        })
    return rows


def write_b0_conflicts(
    scenarios_directory: Path, demands_directory: Path, systems_path: Path, output_path: Path
) -> dict[str, Any]:
    result = evaluate_b0_conflicts(scenarios_directory, demands_directory, systems_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate independent B0 inter-system benchmark conflicts.")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--demands", required=True, type=Path)
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = write_b0_conflicts(args.scenarios, args.demands, args.systems, args.output)
    print("case     routes pairs hard hard% clearance clearance% clear-only same-overlap")
    for case in result["cases"]:
        print(f"{case['case_id']:<8} {case['successful_route_count']:6} {case['route_pair_count']:5} {case['hard_conflicting_route_pair_count']:4} {case['hard_conflict_rate'] * 100:5.1f} {case['clearance_violating_route_pair_count']:9} {case['clearance_violation_rate'] * 100:10.1f} {case['clearance_only_route_pair_count']:10} {case['same_system_overlapping_pair_count']:12}")
    print("system-pair pairs hard clearance clear-only hard% clearance%")
    for row in result["system_pair_metrics"]:
        print(f"{row['system_pair']:<20} {row['route_pair_count']:5} {row['hard_conflicting_route_pair_count']:4} {row['clearance_violating_route_pair_count']:9} {row['clearance_only_route_pair_count']:10} {row['hard_conflict_rate'] * 100:5.1f} {row['clearance_violation_rate'] * 100:10.1f}")
    for label, rows in result["grouped_metrics"].items():
        print(f"by-{label} value pairs hard clearance hard% clearance%")
        for row in rows:
            value = row.get("scenario_id", row.get("demand_profile_id"))
            print(f"{value:<18} {row['route_pair_count']:5} {row['hard_conflicting_route_pair_count']:4} {row['clearance_violating_route_pair_count']:9} {row['hard_conflict_rate'] * 100:5.1f} {row['clearance_violation_rate'] * 100:10.1f}")
    print("global", result["global_metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

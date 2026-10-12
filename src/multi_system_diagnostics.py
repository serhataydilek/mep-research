"""Phase 9D.3 grid-level conflict diagnostics for one multi-system routing result.

Every check reads the actual routed grid cells.  Envelope checks are the
Phase 3C swept-AABB proxy, not continuous solid geometry.  The output never
certifies a clash-free layout: an absent or unresolved check is reported as
such and never counted as zero conflict.
"""
from __future__ import annotations
from itertools import combinations
import json
from typing import Any
from .clash import positive_aabb_overlap
from .clearance_policy import ClearancePolicy, SameSystemClearanceStatus, legacy_obstacle_margin_m
from .multi_system_routing import ConnectionRouteStatus, MultiSystemRoutingResult, PhysicalEdgeRecord
from .physical_edge_routing import PhysicalEdgeRouteStatus
from .route_geometry import compress_path_cells, swept_segment_aabb
from .spatial_evidence import CELL_STATE_NAMES, OccupancyRole, SpatialEvidence
from .voxel import FREE

TOLERANCE_M = 1e-9


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


def _point(p) -> list[float]:
    return [_round(v) for v in p]


def _box(a, b=None) -> dict[str, float]:
    box = a if b is None else tuple(f(a[i], b[i]) for i in range(6) for f in [(max, min)[i % 2]])
    return {"min_x_m": _round(box[0]), "max_x_m": _round(box[1]), "min_y_m": _round(box[2]), "max_y_m": _round(box[3]), "min_z_m": _round(box[4]), "max_z_m": _round(box[5])}


def _spec_key(spec) -> tuple:
    return (spec.origin_x, spec.origin_y, spec.origin_z, spec.nx, spec.ny, spec.nz, spec.voxel_size_m)


def _evaluated_edges(result: MultiSystemRoutingResult) -> tuple[list[PhysicalEdgeRecord], list[str]]:
    """Edges carrying only fully routed connections; partially routed networks are excluded, not dropped silently."""
    status = {x.connection_id: x.status for x in result.connections}
    kept, excluded = [], []
    for edge in result.edges:
        ok = edge.route.status is PhysicalEdgeRouteStatus.ROUTED and all(status[c] is ConnectionRouteStatus.ROUTED for c in edge.connection_ids)
        (kept if ok else excluded).append(edge if ok else edge.physical_edge_id)
    return kept, excluded


def _segment_boxes(edge: PhysicalEdgeRecord, spec, policy: ClearancePolicy, extra: float = 0.0):
    envelope = policy.envelope_for(edge.system)
    return [swept_segment_aabb(s, envelope.planar_half_extent_m, envelope.vertical_half_extent_m, extra) for s in compress_path_cells(list(edge.route.grid_cells), spec)]


def _node_cells(edge: PhysicalEdgeRecord) -> dict[str, tuple[int, int, int]]:
    cells = edge.route.grid_cells
    return {edge.start_node_key: cells[0], edge.end_node_key: cells[-1]}


def _inter_system(edges, spec, policy) -> dict[str, Any]:
    records = []; evaluated = 0
    for a, b in combinations(edges, 2):
        if a.system == b.system: continue
        evaluated += 1
        shared = sorted(set(a.route.grid_cells) & set(b.route.grid_cells))
        half = policy.inter_system_clearance(a.system, b.system).value_m / 2
        hard_a, hard_b = _segment_boxes(a, spec, policy), _segment_boxes(b, spec, policy)
        clear_a, clear_b = _segment_boxes(a, spec, policy, half), _segment_boxes(b, spec, policy, half)
        hard_regions = [_box(x, y) for x in hard_a for y in hard_b if positive_aabb_overlap(x, y)]
        clearance = sum(positive_aabb_overlap(x, y) for x in clear_a for y in clear_b)
        if not (shared or hard_regions or clearance): continue
        records.append({"edge_a": a.physical_edge_id, "edge_b": b.physical_edge_id, "system_a": a.system, "system_b": b.system,
                        "shared_cell_count": len(shared), "shared_cells_world_m": [_point(spec.cell_center(*c)) for c in shared],
                        "hard_segment_pair_count": len(hard_regions), "hard_overlap_regions": hard_regions,
                        "clearance_segment_pair_count": clearance,
                        "severity": "SHARED_CELL" if shared else "HARD_ENVELOPE" if hard_regions else "CLEARANCE_ONLY"})
    return {"status": "EVALUATED", "method": "exact shared centerline cells plus Phase 3C swept-AABB envelope proxy",
            "evaluated_pair_count": evaluated, "conflicting_pair_count": len(records),
            "shared_cell_pair_count": sum(r["shared_cell_count"] > 0 for r in records),
            "shared_cell_total": sum(r["shared_cell_count"] for r in records),
            "hard_envelope_pair_count": sum(r["hard_segment_pair_count"] > 0 for r in records),
            "clearance_violation_pair_count": sum(r["clearance_segment_pair_count"] > 0 for r in records),
            "clearance_only_pair_count": sum(r["severity"] == "CLEARANCE_ONLY" for r in records), "records": records}


def _same_system(edges, spec, policy) -> dict[str, Any]:
    records = []; statuses = {}
    for a, b in combinations(edges, 2):
        if a.system != b.system: continue
        rule = policy.same_system_clearance(a.system); statuses[a.system] = rule.status.value
        na, nb = _node_cells(a), _node_cells(b); common = sorted(set(na) & set(nb))
        allowed = {na[k] for k in common} | {nb[k] for k in common}
        shared = set(a.route.grid_cells) & set(b.route.grid_cells); unexplained = sorted(shared - allowed)
        if unexplained: verdict = "GRID_OVERLAP_COLLISION_SEMANTICS_UNRESOLVED"
        elif rule.status is SameSystemClearanceStatus.UNDEFINED: verdict = "NO_UNEXPLAINED_SHARED_CELL_CLEARANCE_UNDEFINED"
        else: verdict = "DEFINED_SAME_SYSTEM_RULE_NOT_EVALUATED"
        records.append({"edge_a": a.physical_edge_id, "edge_b": b.physical_edge_id, "system": a.system,
                        "relation": "SHARED_TOPOLOGY_NODE" if common else "INDEPENDENT_RUNS", "shared_node_keys": common,
                        "shared_node_cell_count": len(shared & allowed), "unexplained_shared_cell_count": len(unexplained),
                        "unexplained_shared_cells_world_m": [_point(spec.cell_center(*c)) for c in unexplained],
                        "clearance_rule_status": rule.status.value, "verdict": verdict})
    return {"status": "EVALUATED_CLEARANCE_UNRESOLVED" if any(v == SameSystemClearanceStatus.UNDEFINED.value for v in statuses.values()) else "EVALUATED",
            "clearance_rule_status_by_system": dict(sorted(statuses.items())), "pair_count": len(records),
            "independent_run_pair_count": sum(r["relation"] == "INDEPENDENT_RUNS" for r in records),
            "shared_node_pair_count": sum(r["relation"] == "SHARED_TOPOLOGY_NODE" for r in records),
            "unexplained_overlap_pair_count": sum(r["unexplained_shared_cell_count"] > 0 for r in records),
            "unexplained_overlap_cell_total": sum(r["unexplained_shared_cell_count"] for r in records),
            "unresolved_clearance_pair_count": sum(r["clearance_rule_status"] != SameSystemClearanceStatus.DEFINED.value for r in records),
            "records": records}


def _ownership(result: MultiSystemRoutingResult) -> dict[str, Any]:
    violations = []; by_id = {}
    for edge in result.edges:
        if edge.physical_edge_id in by_id: violations.append({"code": "DUPLICATE_PHYSICAL_EDGE", "subject_id": edge.physical_edge_id})
        by_id[edge.physical_edge_id] = edge
    connection = {x.connection_id: x for x in result.connections}
    for edge in result.edges:
        if not (edge.owner_system == edge.system == (edge.route.system or edge.system)):
            violations.append({"code": "OWNER_SYSTEM_MISMATCH", "subject_id": edge.physical_edge_id})
        if not edge.connection_ids: violations.append({"code": "EDGE_WITHOUT_CONNECTION", "subject_id": edge.physical_edge_id})
        for cid in edge.connection_ids:
            if connection[cid].system != edge.system: violations.append({"code": "CROSS_SYSTEM_EDGE_SHARING", "subject_id": f"{edge.physical_edge_id}|{cid}"})
            if edge.physical_edge_id not in connection[cid].physical_edge_ids: violations.append({"code": "EDGE_CONNECTION_BACKREFERENCE_MISSING", "subject_id": f"{edge.physical_edge_id}|{cid}"})
        if edge.role == "MAIN" and len(edge.connection_ids) < 2: violations.append({"code": "MAIN_NOT_SHARED", "subject_id": edge.physical_edge_id})
        if edge.role == "MAIN" and edge.route.status is PhysicalEdgeRouteStatus.ROUTED and len(edge.route.grid_cells) < 2:
            violations.append({"code": "DEGENERATE_ZERO_LENGTH_MAIN", "subject_id": edge.physical_edge_id})
    for record in result.connections:
        if record.status is ConnectionRouteStatus.ROUTED:
            for eid in record.physical_edge_ids:
                if eid not in by_id: violations.append({"code": "ROUTED_CONNECTION_EDGE_MISSING", "subject_id": f"{record.connection_id}|{eid}"})
    mains = [{"physical_edge_id": e.physical_edge_id, "system": e.system, "connection_ids": list(e.connection_ids), "route_status": e.route.status.value,
              "routed_instance_count": sum(x.physical_edge_id == e.physical_edge_id for x in result.edges)} for e in result.edges if e.role == "MAIN"]
    return {"status": "PASS" if not violations else "FAIL", "violation_count": len(violations), "violations": violations, "shared_main_edges": mains}


def _obstacles(edges, evidence: SpatialEvidence, spec, policy) -> dict[str, Any]:
    blocked = [x for x in evidence.elements if x.occupancy_role is OccupancyRole.BLOCKED_FIXED]
    grid_hits, centerline_hits, envelope_hits, shaft_hits = [], [], [], []
    for edge in edges:
        grid = evidence.grid_for(edge.system)
        for cell in edge.route.grid_cells:
            state = grid.cells[grid.spec.linear_index(*cell)]
            if state != FREE: grid_hits.append({"physical_edge_id": edge.physical_edge_id, "cell": list(cell), "state": CELL_STATE_NAMES.get(state, str(state))})
        for point in edge.route.world_centerline_m:
            for element in blocked:
                b = element.world_bounds
                if b.min_x + TOLERANCE_M < point[0] < b.max_x - TOLERANCE_M and b.min_y + TOLERANCE_M < point[1] < b.max_y - TOLERANCE_M and b.min_z + TOLERANCE_M < point[2] < b.max_z - TOLERANCE_M:
                    centerline_hits.append({"physical_edge_id": edge.physical_edge_id, "global_id": element.global_id, "ifc_class": element.ifc_class, "world_m": _point(point)})
            for shaft in evidence.shafts:
                if shaft.world_bounds.contains_point(*point, tolerance=0.0): shaft_hits.append({"physical_edge_id": edge.physical_edge_id, "shaft_global_id": shaft.global_id, "world_m": _point(point)})
        for box in _segment_boxes(edge, spec, policy):
            for element in blocked:
                if positive_aabb_overlap(box, element.world_bounds.as_tuple()):
                    envelope_hits.append({"physical_edge_id": edge.physical_edge_id, "global_id": element.global_id, "ifc_class": element.ifc_class, "overlap_region": _box(box, element.world_bounds.as_tuple())})
    return {"status": "EVALUATED", "blocked_element_count": len(blocked),
            "non_free_grid_cell_count": len(grid_hits), "non_free_grid_cells": grid_hits,
            "centerline_inside_obstacle_count": len(centerline_hits), "centerline_inside_obstacle": centerline_hits,
            "nominal_envelope_obstacle_overlap_count": len(envelope_hits), "nominal_envelope_obstacle_overlaps": envelope_hits,
            "centerline_inside_shaft_count": len(shaft_hits), "centerline_inside_shaft": shaft_hits,
            "note": "Envelope check uses nominal service half-extents without clearance against raw element AABBs."}


def _policy(result: MultiSystemRoutingResult, edges, evidence: SpatialEvidence, policy: ClearancePolicy) -> dict[str, Any]:
    violations = []
    if (result.policy_id, result.policy_digest_sha256) != (policy.policy_id, policy.digest_sha256): violations.append({"code": "RESULT_POLICY_MISMATCH", "subject_id": None})
    systems = [s for s in result.systems if evidence.grid_for(s) is not None]
    margins = []
    for system in systems:
        grid = evidence.grid_for(system)
        if grid.clearance_policy_digest_sha256 != policy.digest_sha256: violations.append({"code": "GRID_POLICY_DIGEST_MISMATCH", "subject_id": system})
        planar, vertical = legacy_obstacle_margin_m(policy, system, "PERIMETER_WALL")
        consistent = abs(planar - grid.required_planar_margin_m) <= TOLERANCE_M and abs(vertical - grid.required_vertical_margin_m) <= TOLERANCE_M
        if not consistent: violations.append({"code": "GRID_MARGIN_POLICY_MISMATCH", "subject_id": system})
        margins.append({"system": system, "policy_planar_margin_m": _round(planar), "grid_planar_margin_m": _round(grid.required_planar_margin_m),
                        "policy_vertical_margin_m": _round(vertical), "grid_vertical_margin_m": _round(grid.required_vertical_margin_m), "consistent": consistent})
    for edge in result.edges:
        if (edge.route.policy_id, edge.route.policy_digest_sha256) != (policy.policy_id, policy.digest_sha256): violations.append({"code": "EDGE_POLICY_MISMATCH", "subject_id": edge.physical_edge_id})
        if evidence.grid_for(edge.system) is None or edge.route.grid_cells_sha256 != evidence.grid_for(edge.system).cells_sha256: violations.append({"code": "EDGE_GRID_DIGEST_MISMATCH", "subject_id": edge.physical_edge_id})
    specs = {_spec_key(evidence.grid_for(s).spec) for s in {e.system for e in edges}}
    if len(specs) > 1: violations.append({"code": "GRID_SPEC_MISMATCH_ACROSS_SYSTEMS", "subject_id": None})
    pairs = [{"system_a": a, "system_b": b, **policy.inter_system_clearance(a, b).to_canonical_dict()} for a, b in combinations(systems, 2)]
    same = [{"system": s, **policy.same_system_clearance(s).to_canonical_dict()} for s in systems]
    return {"status": "PASS" if not violations else "FAIL", "violation_count": len(violations), "violations": violations, "policy_id": policy.policy_id,
            "policy_digest_sha256": policy.digest_sha256, "grid_specs_identical_across_systems": len(specs) <= 1, "per_system_margins": margins,
            "inter_system_rules": pairs, "same_system_rules": same}


def _unresolved(result: MultiSystemRoutingResult, edges, excluded, same) -> list[dict[str, Any]]:
    items = [{"code": "CONTINUOUS_GEOMETRY_CLASH_CERTIFICATION_UNAVAILABLE", "subject_id": None},
             {"code": "ENVELOPE_CHECK_IS_AABB_PROXY", "subject_id": None},
             {"code": "NO_INTER_SYSTEM_RESERVATION_APPLIED", "subject_id": None}]
    items += [{"code": "SAME_SYSTEM_CLEARANCE_UNDEFINED", "subject_id": s} for s, v in same["clearance_rule_status_by_system"].items() if v == SameSystemClearanceStatus.UNDEFINED.value]
    items += [{"code": "JUNCTION_FITTING_UNEVALUATED", "subject_id": e.end_node_key} for e in edges if e.role == "MAIN"]
    items += [{"code": "SHARED_ENDPOINT_CELL_CONNECTION_UNEVALUATED", "subject_id": f"{r['edge_a']}|{r['edge_b']}"} for r in same["records"]
              if any(k.startswith("node/authoritative/") for k in r["shared_node_keys"])]
    items += [{"code": "ENDPOINT_SNAPPED_CONNECTOR_ENGAGEMENT_UNVALIDATED", "subject_id": s.endpoint_id} for s in result.endpoint_snaps]
    items += [{"code": "DRAINAGE_GRAVITY_SLOPE_UNVALIDATED", "subject_id": c.connection_id} for c in result.connections if "DRAINAGE_GRAVITY_SLOPE_UNVALIDATED" in c.unvalidated_requirements]
    items += [{"code": "CONNECTION_NOT_ROUTED_NOT_EVALUATED", "subject_id": c.connection_id} for c in result.connections if c.status is not ConnectionRouteStatus.ROUTED]
    items += [{"code": "PARTIAL_NETWORK_EDGE_EXCLUDED", "subject_id": x} for x in excluded]
    return items


def diagnose_multi_system_conflicts(result: MultiSystemRoutingResult, evidence: SpatialEvidence, policy: ClearancePolicy) -> dict[str, Any]:
    """Run every independent check on the actual routed cells; never infer zero conflict from a missing check."""
    if not isinstance(result, MultiSystemRoutingResult) or not isinstance(evidence, SpatialEvidence) or not isinstance(policy, ClearancePolicy):
        raise ValueError('Diagnostics require a MultiSystemRoutingResult, SpatialEvidence and ClearancePolicy.')
    edges, excluded = _evaluated_edges(result)
    consistency = _policy(result, edges, evidence, policy)
    ownership = _ownership(result)
    if not edges:
        empty = {"status": "NOT_EVALUATED", "reason": "NO_FULLY_ROUTED_EDGES"}
        inter, same, obstacles = dict(empty), {**empty, "clearance_rule_status_by_system": {}, "records": []}, dict(empty)
    elif not consistency["grid_specs_identical_across_systems"]:
        spec = evidence.grid_for(edges[0].system).spec
        inter = {"status": "NOT_EVALUATED", "reason": "GRID_SPEC_MISMATCH_ACROSS_SYSTEMS"}
        same, obstacles = _same_system(edges, spec, policy), _obstacles(edges, evidence, spec, policy)
    else:
        spec = evidence.grid_for(edges[0].system).spec
        inter, same, obstacles = _inter_system(edges, spec, policy), _same_system(edges, spec, policy), _obstacles(edges, evidence, spec, policy)
    unresolved = _unresolved(result, edges, excluded, same)
    evaluated = all(x["status"] != "NOT_EVALUATED" for x in (inter, same, obstacles))
    detected = (inter.get("conflicting_pair_count", 0) + same.get("unexplained_overlap_pair_count", 0) + obstacles.get("non_free_grid_cell_count", 0)
                + obstacles.get("centerline_inside_obstacle_count", 0) + obstacles.get("nominal_envelope_obstacle_overlap_count", 0))
    inconsistent = ownership["violation_count"] + consistency["violation_count"]
    verdict = ("INCONSISTENT_INPUTS" if inconsistent else "CONFLICTS_DETECTED" if detected else "NOT_FULLY_EVALUATED" if not evaluated
               else "NO_GRID_CONFLICT_DETECTED_SEMANTICS_UNRESOLVED")
    return {"scenario_id": result.scenario_id, "endpoint_cell_mode": result.endpoint_cell_mode.value,
            "evaluated_physical_edge_ids": [e.physical_edge_id for e in edges], "excluded_physical_edge_ids": excluded,
            "checks": {"inter_system_overlap": inter, "same_system_independent_run_overlap": same, "shared_edge_ownership": ownership,
                       "obstacle_intersection": obstacles, "clearance_policy_consistency": consistency},
            "unresolved_collision_semantics": unresolved,
            "summary": {"grid_level_verdict": verdict, "all_checks_evaluated": evaluated,
                        "inter_system_conflicting_pair_count": inter.get("conflicting_pair_count"),
                        "inter_system_shared_cell_pair_count": inter.get("shared_cell_pair_count"),
                        "inter_system_hard_envelope_pair_count": inter.get("hard_envelope_pair_count"),
                        "inter_system_clearance_violation_pair_count": inter.get("clearance_violation_pair_count"),
                        "same_system_unexplained_overlap_pair_count": same.get("unexplained_overlap_pair_count"),
                        "same_system_unresolved_clearance_pair_count": same.get("unresolved_clearance_pair_count"),
                        "obstacle_non_free_cell_count": obstacles.get("non_free_grid_cell_count"),
                        "obstacle_envelope_overlap_count": obstacles.get("nominal_envelope_obstacle_overlap_count"),
                        "ownership_violation_count": ownership["violation_count"], "policy_violation_count": consistency["violation_count"],
                        "unresolved_item_count": len(unresolved)},
            "geometric_clash_certified": False, "collision_free_claimed": False,
            "disclaimer": "Grid-level diagnostics on voxel centerlines and AABB envelope proxies; not continuous-geometry clash certification."}


def diagnostics_to_json(diagnostics: dict[str, Any]) -> str:
    return json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))

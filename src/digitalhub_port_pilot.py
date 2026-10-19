"""Phase 11A.2 port-to-port pilot.

The frozen ``astar_3d`` router is called only after a terminal has been qualified
against source meshes. Bounding-box overlap alone does not free or reject a cell.
"""

from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path
from typing import Any

import ifcopenshell
import numpy as np

from .clash import positive_aabb_overlap
from .endpoint_grid import assemble_physical_polyline, polyline_metrics
from .endpoint_qualification import decide_terminal, execution_status_flags
from .external_ifc_probe import (
    bounds_tuple,
    placement_origin_m,
    port_host_map,
    project_length_scale,
    world_settings,
    world_vertices_metres,
    write_json,
    explicit_port_connections,
)
from .external_ifc_routing import (
    DISCIPLINE_ORDER,
    _public_interface,
    _relations_for_pair,
    _working_set,
    bounds_intersect,
    broad_phase_world_polyline,
    build_occupancy,
    discover_discipline_files,
    inflate_bounds,
    load_role_geometry,
    mark_obstacle_aabbs,
    prepare_candidate,
    resolve_physical_terminal,
    select_flow_subpaths,
    system_and_storey_maps,
)
from .ifc_mesh_section import cells_touching_mesh, mesh_facts, surface_distance_m, triangles_from_mesh, vertical_section
from .route_geometry import Segment, swept_segment_aabb
from .routing import astar_3d, route_metrics
from .voxel import BLOCKED_FIXED, FREE, aabb_index_ranges


MAX_RECORDED_CANDIDATES = 10
MAX_EXTENDED_CANDIDATES = 20
MAX_ASTAR_ATTEMPTS = 6
PREFERRED_MIN_M = 2.0
PREFERRED_MAX_M = 8.0
MESH_REFINED_CLASSES = {"IfcCovering", "IfcBuildingElementProxy"}
MAX_REFINED_CELLS = 250_000
CLEAN_VALIDATION = {"ZERO_BROAD_PHASE_CANDIDATES", "MESH_SEPARATED_BROAD_PHASE_CANDIDATES"}


def pilot_sort_key(preview: dict[str, Any]) -> tuple:
    """Deterministic preference: clear of slab AABBs, then a 2–8 m separation."""
    distance = float(preview["straight_line_m"])
    inside_slab = bool(preview.get("port_inside_slab_aabb"))
    band = PREFERRED_MIN_M <= distance <= PREFERRED_MAX_M
    return (
        0 if not inside_slab else 1,
        0 if band else 1,
        0 if preview.get("same_system") else 1,
        0 if preview.get("same_storey") else 1,
        abs(distance - 5.0),
        tuple(preview["replaced_host_global_ids"]),
        tuple(preview["boundary_host_global_ids"]),
        preview.get("discipline") or "",
    )


def execute_port_pilot(
    data_dir: Path,
    output_dir: Path,
    *,
    threads: int = 4,
    original_request_path: Path | None = None,
) -> dict[str, Any]:
    """Qualify a deterministic shortlist and search the first physically usable request."""
    files = discover_discipline_files(data_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    original_ids = _original_replaced_ids(original_request_path or output_dir / "routing_request.json")
    models = {role: ifcopenshell.open(str(path)) for role, path in files.items()}
    previews = _previews(models, files)
    cache = _geometry_cache(files, output_dir / "world_aabb_cache.json", threads)
    slab_bounds = _slab_bounds(cache.get("architecture"))
    for preview in previews:
        start = preview["preview_start_m"]
        goal = preview["preview_goal_m"]
        preview["port_inside_slab_aabb"] = _inside_any(start, slab_bounds) or _inside_any(goal, slab_bounds)
        preview["approximate_obstacle_count"] = _corridor_obstacle_count(
            cache, start, goal, set(preview["replaced_host_global_ids"]),
        )
    ranked = sorted(previews, key=pilot_sort_key)
    ordered = _evaluation_order(ranked, original_ids)
    mesh_cache: dict[tuple[str, str], tuple[list[float], list[int]] | None] = {}
    records = []
    selected = None
    attempted = []
    seen = set()
    for preview in ordered:
        if len(records) >= MAX_RECORDED_CANDIDATES and selected is not None:
            break
        if len(records) >= MAX_EXTENDED_CANDIDATES:
            break
        identity = tuple(preview["replaced_host_global_ids"])
        if identity in seen:
            continue
        seen.add(identity)
        record = _qualify_preview(preview, models, files, cache, mesh_cache, threads)
        if record.get("qualified") and selected is None and len(attempted) < MAX_ASTAR_ATTEMPTS:
            request = _request_document(record)
            write_json(output_dir / "first_valid_routing_request.json", request)
            outcome = _run_search(record, files, cache, models, mesh_cache)
            outcome["request"] = request
            attempted.append(outcome)
            record["routing_attempt"] = _public_attempt(outcome)
            if outcome["flags"]["path_found"] and outcome["validation"]["label"] in CLEAN_VALIDATION:
                selected = outcome
        records.append(record)
    if selected is None and attempted:
        selected = next((item for item in attempted if item["flags"]["path_found"]), attempted[-1])
        if selected.get("execution") is None:
            selected = None
    _fill_shortlist_if_needed(records, ordered, models, files, cache, mesh_cache, threads)
    write_json(output_dir / "candidate_routing_requests.json", {
        "selection_rule": (
            "Original H_RL 68 is always diagnosed. Other candidates are ranked by slab-AABB avoidance, "
            "then straight-line length in [2 m, 8 m], shared system, shared storey, distance to 5 m, and GlobalId."
        ),
        "candidate_count": len(records),
        "candidates": [_public_candidate(item) for item in records],
    })
    write_json(output_dir / "endpoint_accessibility_report.json", {
        "original_h_rl_68": _original_report(records, original_ids),
        "candidates": [_public_candidate(item) for item in records],
        "aabb_is_not_solid_penetration": True,
    })
    if selected is not None and selected.get("execution") is not None:
        write_json(output_dir / "first_valid_routing_request.json", selected["request"])
        write_json(output_dir / "first_valid_routing_execution.json", selected["execution"])
    summary = _summary(records, selected, original_ids)
    write_json(output_dir / "port_pilot_summary.json", summary)
    return summary


def _evaluation_order(ranked: list[dict[str, Any]], original_ids: tuple[str, ...] | None) -> list[dict[str, Any]]:
    original = []
    others = []
    for preview in ranked:
        if original_ids is not None and tuple(preview["replaced_host_global_ids"]) == original_ids:
            preview["shortlist_reason"] = "ORIGINAL_H_RL_68_REQUEST"
            original.append(preview)
        else:
            preview["shortlist_reason"] = "DETERMINISTIC_SHORTLIST"
            others.append(preview)
    return original + others


def _fill_shortlist_if_needed(records, ordered, models, files, cache, mesh_cache, threads) -> None:
    seen = {tuple(item["replaced_host_global_ids"]) for item in records}
    for preview in ordered:
        if len(records) >= MAX_RECORDED_CANDIDATES:
            return
        identity = tuple(preview["replaced_host_global_ids"])
        if identity in seen:
            continue
        records.append(_qualify_preview(preview, models, files, cache, mesh_cache, threads))
        seen.add(identity)


def _previews(models: dict[str, Any], files: dict[str, Path]) -> list[dict[str, Any]]:
    previews = []
    for discipline in DISCIPLINE_ORDER:
        model = models[discipline]
        scale = project_length_scale(model)
        connections = explicit_port_connections(model, port_host_map(model))
        flow_ids = {item.GlobalId for item in model.by_type("IfcFlowSegment")}
        flow_ids.update(item.GlobalId for item in model.by_type("IfcFlowFitting"))
        systems, storeys, names = system_and_storey_maps(model)
        for candidate in select_flow_subpaths(_adjacency(connections), flow_ids):
            start = _host_port_world(model, connections, candidate["boundary_host_global_ids"][0], candidate["replaced_host_global_ids"][0], scale)
            goal = _host_port_world(model, connections, candidate["boundary_host_global_ids"][1], candidate["replaced_host_global_ids"][-1], scale)
            if start is None or goal is None:
                continue
            delta = [goal[axis] - start[axis] for axis in range(3)]
            distance = float(np.linalg.norm(delta))
            if distance < 0.5:
                continue
            replaced = candidate["replaced_host_global_ids"]
            system_sets = [systems.get(global_id, set()) for global_id in replaced]
            storey_sets = [storeys.get(global_id, set()) for global_id in replaced]
            shared_systems = set.intersection(*system_sets) if system_sets and all(system_sets) else set()
            shared_storeys = set.intersection(*storey_sets) if storey_sets and all(storey_sets) else set()
            previews.append({
                "discipline": discipline,
                "source_file": files[discipline].name,
                "unit_scale_m": scale,
                "candidate": candidate,
                "replaced_host_global_ids": list(replaced),
                "boundary_host_global_ids": list(candidate["boundary_host_global_ids"]),
                "preview_start_m": start,
                "preview_goal_m": goal,
                "straight_line_m": distance,
                "z_delta_m": delta[2],
                "same_system": bool(shared_systems),
                "same_storey": bool(shared_storeys),
                "system_global_ids": sorted(shared_systems),
                "system_names": [names.get(global_id) for global_id in sorted(shared_systems)],
                "storeys": sorted(shared_storeys),
                "connections": connections,
                "systems": systems,
                "storey_map": storeys,
                "system_names_map": names,
            })
    return previews


def _qualify_preview(
    preview, models, files, cache, mesh_cache, threads,
    *,
    inflation_m: float | None = None,
    mesh_contact_m: float = 1e-3,
    solid_contact_m: float = 1e-3,
    voxel_size_m: float = 0.1,
) -> dict[str, Any]:
    model = models[preview["discipline"]]
    try:
        prepared = prepare_candidate(
            model,
            preview["candidate"],
            preview["connections"],
            preview["unit_scale_m"],
            preview["systems"],
            preview["storey_map"],
            preview["system_names_map"],
        )
    except Exception as exc:
        return _failed_preview(preview, f"PREPARE_FAILED: {type(exc).__name__}: {exc}")
    if prepared is None:
        return _failed_preview(preview, "PREPARE_RETURNED_NO_GEOMETRICALLY_RESOLVED_INTERFACES")
    prepared["discipline"] = preview["discipline"]
    prepared["unit_scale_m"] = preview["unit_scale_m"]
    prepared["source_file"] = preview["source_file"]
    body = float(prepared["body_half_extent_m"])
    inflation = body if inflation_m is None else float(inflation_m)
    try:
        occupancy = build_occupancy(
            files, prepared, threads=threads, geometry_cache=cache,
            voxel_size_m=voxel_size_m, inflation_m=inflation,
        )
    except ValueError as exc:
        return _failed_preview(preview, f"ROI_LIMITS: {exc}")
    refinement = refine_loose_aabbs(
        occupancy, models, mesh_cache, inflation, mesh_contact_m=mesh_contact_m,
    )
    terminals = []
    qualified = True
    for role, interface in (("start", prepared["start_interface"]), ("end", prepared["end_interface"])):
        terminal = resolve_physical_terminal(
            occupancy["grid"],
            tuple(interface["world_m"]),
            occupancy["obstacles"],
            inflation,
            role=role,
        )
        terminal["immutable_interface_m"] = list(interface["world_m"])
        terminal["coordinate_classification"] = interface["coordinate_classification"]
        decision = _decide_from_terminal(
            terminal,
            interface,
            occupancy,
            prepared,
            models,
            cache,
            mesh_cache,
            solid_contact_m=solid_contact_m,
        )
        terminal["qualification"] = decision
        if decision.get("release_cell") and decision.get("qualified") and terminal.get("containing_index"):
            index = tuple(terminal["containing_index"])
            occupancy["grid"].cells[occupancy["grid"].spec.linear_index(*index)] = FREE
            terminal["cell_state_after_local_rule"] = "FREE"
        else:
            terminal["cell_state_after_local_rule"] = terminal.get("cell_state")
        if not decision.get("qualified"):
            qualified = False
        terminals.append(terminal)
    record = _candidate_record(preview, prepared, occupancy, terminals, qualified)
    record["occupancy_refinement"] = refinement
    record["occupancy_policy"] = {
        "voxel_size_m": voxel_size_m,
        "aabb_inflation_m": inflation,
        "covering_mesh_contact_m": mesh_contact_m,
        "endpoint_solid_contact_m": solid_contact_m,
        "body_half_extent_m": body,
        "additional_clearance_m": inflation - body,
    }
    record["_prepared"] = prepared
    record["_occupancy"] = occupancy
    record["_terminals"] = terminals
    return record


def refine_loose_aabbs(
    occupancy, models, mesh_cache, inflation: float, *, mesh_contact_m: float = 1e-3,
) -> list[dict[str, Any]]:
    """Replace covering and proxy AABB marks with cells the exported mesh actually occupies.

    Walls, slabs, and MEP keep their AABB marks. A covering whose shell misses a
    cell centre no longer blocks that cell.
    """
    grid = occupancy["grid"]
    spec = grid.spec
    grid.cells[:] = bytes(len(grid.cells))
    plain = []
    refined = []
    for obstacle in occupancy["obstacles"]:
        if obstacle.get("ifc_class") in MESH_REFINED_CLASSES:
            refined.append(obstacle)
        else:
            plain.append(obstacle)
    mark_obstacle_aabbs(grid, plain, inflation)
    stats = []
    for obstacle in refined:
        ranges = aabb_index_ranges(spec, inflate_bounds(tuple(obstacle["bounds_m"]), inflation))
        cell_count = len(ranges[0]) * len(ranges[1]) * len(ranges[2])
        arrays = _mesh_arrays(models, obstacle["file_role"], obstacle["global_id"], mesh_cache)
        if arrays is None or cell_count == 0 or cell_count > MAX_REFINED_CELLS:
            mark_obstacle_aabbs(grid, [obstacle], inflation)
            stats.append({
                "global_id": obstacle["global_id"],
                "ifc_class": obstacle.get("ifc_class"),
                "method": "AABB_FALLBACK",
                "aabb_cell_count": cell_count,
            })
            continue
        triangles = triangles_from_mesh(*arrays)
        xs = np.arange(ranges[0].start, ranges[0].stop)
        ys = np.arange(ranges[1].start, ranges[1].stop)
        zs = np.arange(ranges[2].start, ranges[2].stop)
        ii, jj, kk = np.meshgrid(xs, ys, zs, indexing="ij")
        centers = np.column_stack((
            spec.origin_x + (ii.ravel() + 0.5) * spec.voxel_size_m,
            spec.origin_y + (jj.ravel() + 0.5) * spec.voxel_size_m,
            spec.origin_z + (kk.ravel() + 0.5) * spec.voxel_size_m,
        ))
        mask = cells_touching_mesh(centers, triangles, contact_m=mesh_contact_m)
        chosen = np.column_stack((ii.ravel(), jj.ravel(), kk.ravel()))[mask]
        if len(chosen):
            linear = ((chosen[:, 2] * spec.ny) + chosen[:, 1]) * spec.nx + chosen[:, 0]
            linear = np.unique(linear.astype(np.int64))
            occupied = np.frombuffer(grid.cells, dtype=np.uint8)
            newly = occupied[linear] != BLOCKED_FIXED
            occupied[linear] = BLOCKED_FIXED
            marked = int(newly.sum())
        else:
            marked = 0
        stats.append({
            "global_id": obstacle["global_id"],
            "ifc_class": obstacle.get("ifc_class"),
            "method": "MESH_ENCLOSURE_OR_CONTACT",
            "mesh_contact_m": mesh_contact_m,
            "aabb_inflation_m": inflation,
            "aabb_cell_count": int(cell_count),
            "mesh_occupied_cell_count": int(mask.sum()),
            "newly_marked_cell_count": marked,
        })
    return stats


def _decide_from_terminal(
    terminal, interface, occupancy, prepared, models, cache, mesh_cache, *, solid_contact_m: float = 1e-3,
) -> dict[str, Any]:
    boundary_id = interface["retained_host_global_id"]
    blockers = list(terminal.get("blocking_obstacles") or [])
    seen = {item["global_id"] for item in blockers}
    for hit in terminal.get("port_point_obstacle_hits") or []:
        if hit["global_id"] not in seen:
            blockers.append(hit)
            seen.add(hit["global_id"])
    facts = {}
    openings = {}
    point = tuple(interface["world_m"])
    center = terminal.get("voxel_center_m") or point
    for blocker in blockers:
        role = blocker["file_role"]
        facts[blocker["global_id"]] = _facts_for(models, role, blocker["global_id"], point, center, mesh_cache)
        openings[blocker["global_id"]] = _openings_for_host(cache.get(role, {}).get("voids", {}), blocker["global_id"])
        for opening in openings[blocker["global_id"]][:4]:
            opening_id = opening.get("opening_global_id")
            if not opening_id:
                continue
            opening_mesh = _facts_for(models, role, opening_id, point, center, mesh_cache)
            opening["opening_mesh_encloses_port"] = bool(opening_mesh.get("port_enclosed")) if opening_mesh.get("resolved") else None
    return decide_terminal(
        point_m=point,
        cell_index=terminal.get("containing_index"),
        cell_center_m=terminal.get("voxel_center_m"),
        cell_blocked=terminal.get("cell_state") == "BLOCKED",
        outside_grid=terminal.get("status") == "ENDPOINT_OUTSIDE_GRID",
        boundary_host_global_id=boundary_id,
        blockers=blockers,
        mesh_facts_by_id=facts,
        openings_by_host=openings,
        solid_contact_m=solid_contact_m,
    )


def _run_search(record, files, cache, models, mesh_cache, *, required_separation_m: float | None = None) -> dict[str, Any]:
    prepared = record["_prepared"]
    occupancy = record["_occupancy"]
    grid = occupancy["grid"]
    start = tuple(record["_terminals"][0]["containing_index"])
    goal = tuple(record["_terminals"][1]["containing_index"])
    if grid.cells[grid.spec.linear_index(*start)] != FREE or grid.cells[grid.spec.linear_index(*goal)] != FREE:
        flags = execution_status_flags(cli_completed=True, astar_invoked=False, path_found=False, validation_label="NOT_RUN_ENDPOINT_INVALID")
        return {"flags": flags, "validation": {"label": "NOT_RUN_ENDPOINT_INVALID", "confirmed_collision_result": False}, "record": record, "execution": None}
    tracemalloc.start()
    started = time.perf_counter()
    result = astar_3d(grid, start, goal)
    runtime = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    physical = None
    physical_metrics = None
    validation = {"label": "NOT_RUN_NO_VOXEL_PATH", "confirmed_collision_result": False, "confirmed_separation": False}
    if result.path_found:
        centers = [list(grid.spec.cell_center(*cell)) for cell in result.path_cells]
        physical = assemble_physical_polyline(
            tuple(prepared["start_interface"]["world_m"]),
            centers,
            tuple(prepared["end_interface"]["world_m"]),
        )
        physical_metrics = polyline_metrics(physical["polyline_m"])
        validation = _validate_physical_route(
            physical["polyline_m"],
            occupancy,
            prepared,
            record["_terminals"],
            models,
            mesh_cache,
            required_separation_m=required_separation_m,
        )
    flags = execution_status_flags(
        cli_completed=True,
        astar_invoked=True,
        path_found=bool(result.path_found),
        validation_label=validation["label"],
    )
    execution = {
        "routing_command_completed": True,
        "astar_invoked": True,
        "router": "src.routing.astar_3d",
        "search_status": "COMPLETED_PATH_FOUND" if result.path_found else "COMPLETED_NO_PATH",
        "path_found": result.path_found,
        "exact_port_to_port_connection": bool(physical and physical["exact_port_to_port"]),
        "runtime_seconds": round(runtime, 6),
        "python_tracemalloc_peak_bytes": peak,
        "process_memory": _working_set(),
        "expanded_node_count": result.expanded_node_count,
        "grid_search_metrics": route_metrics(result, grid.spec.voxel_size_m),
        "physical_route_metrics": physical_metrics,
        "physical_route_length_m": None if physical_metrics is None else physical_metrics["length_m"],
        "physical_vertical_travel_m": None if physical_metrics is None else physical_metrics["vertical_travel_m"],
        "start_grid_index": list(start),
        "goal_grid_index": list(goal),
        "immutable_start_m": prepared["start_interface"]["world_m"],
        "immutable_goal_m": prepared["end_interface"]["world_m"],
        "world_polyline_m": None if physical is None else physical["polyline_m"],
        "start_stub_m": None if physical is None else physical["start_stub_m"],
        "end_stub_m": None if physical is None else physical["end_stub_m"],
        "path_cells": [list(cell) for cell in result.path_cells],
        "allowances": [item["qualification"].get("allowance") for item in record["_terminals"] if item["qualification"].get("allowance")],
        "collision_validation": validation,
        "replaced_host_global_ids": prepared["candidate"]["replaced_host_global_ids"],
        "boundary_host_global_ids": prepared["candidate"]["boundary_host_global_ids"],
        "discipline": prepared["discipline"],
        "system_names": prepared["system_names"],
        "storeys": prepared["storeys"],
        "status_flags": flags,
        "physical_infeasibility_claimed": False,
        "physical_clash_freedom_claimed": validation["label"] == "MESH_SEPARATED_BROAD_PHASE_CANDIDATES",
    }
    return {"flags": flags, "validation": validation, "record": record, "execution": execution}


def _validate_physical_route(
    points, occupancy, prepared, terminals, models, mesh_cache, *, required_separation_m: float | None = None,
) -> dict[str, Any]:
    body = float(prepared["body_half_extent_m"])
    required = body if required_separation_m is None else float(required_separation_m)
    replaced = set(prepared["candidate"]["replaced_host_global_ids"])
    broad = broad_phase_world_polyline(points, occupancy["obstacles"], required, replaced)
    allowances = [item["qualification"]["allowance"] for item in terminals if item["qualification"].get("allowance")]
    if broad["hit_count"] == 0:
        return {
            **broad,
            "label": "ZERO_BROAD_PHASE_CANDIDATES",
            "confirmed_collision_result": False,
            "confirmed_separation": False,
            "note": "No swept-AABB candidates. This does not prove mesh clearance against every retained solid.",
            "required_separation_m": required,
            "body_half_extent_m": body,
            "measurement": "SWEPT_AABB_BROAD_PHASE",
        }
    confirmed = []
    clearance = []
    separated = []
    unresolved = []
    allowance_ignored = []
    hit_ids = {(item["global_id"], item["file_role"]) for item in broad["hit_sample"]}
    # broad_phase_world_polyline keeps only 20 samples. Re-scan ids from the obstacle list
    # when the sample is truncated by using the same overlap test here for every obstacle.
    if broad["hit_count"] > len(broad["hit_sample"]):
        hit_ids = set()
        for obstacle in occupancy["obstacles"]:
            if obstacle["global_id"] in replaced:
                continue
            for start, end in zip(points, points[1:]):
                if all(abs(float(start[axis]) - float(end[axis])) <= 1e-12 for axis in range(3)):
                    continue
                swept = swept_segment_aabb(
                    Segment(tuple(float(value) for value in start), tuple(float(value) for value in end)),
                    required,
                    required,
                )
                if _aabb_overlap(swept, tuple(obstacle["bounds_m"])):
                    hit_ids.add((obstacle["global_id"], obstacle["file_role"]))
                    break
    for global_id, role in sorted(hit_ids):
        facts_mesh = _mesh_arrays(models, role, global_id, mesh_cache)
        if facts_mesh is None:
            unresolved.append(global_id)
            continue
        vertices, faces = facts_mesh
        triangles = triangles_from_mesh(vertices, faces)
        min_distance = None
        enclosed = False
        for start, end in zip(points, points[1:]):
            if _segment_inside_allowance(start, end, global_id, allowances, occupancy["grid"].spec):
                allowance_ignored.append(global_id)
                continue
            for sample in _segment_samples(start, end):
                section = vertical_section(sample, triangles)
                if not section.get("resolved", False):
                    unresolved.append(global_id)
                    enclosed = False
                    break
                if section["enclosed"]:
                    enclosed = True
                distance = surface_distance_m(sample, triangles)
                if distance is not None and (min_distance is None or distance < min_distance):
                    min_distance = distance
            if global_id in unresolved:
                break
        if global_id in unresolved:
            continue
        record = {
            "global_id": global_id,
            "file_role": role,
            "min_centerline_distance_m": min_distance,
            "body_half_extent_m": body,
            "required_separation_m": required,
            "measurement": "MESH_SURFACE_DISTANCE_OF_CENTERLINE_SAMPLES",
        }
        if enclosed:
            confirmed.append(record)
        elif min_distance is not None and min_distance + 1e-6 < required:
            clearance.append(record)
        else:
            separated.append(record)
    if confirmed:
        label = "CONFIRMED_MESH_INTERSECTION"
    elif clearance:
        label = "CLEARANCE_VIOLATION"
    elif unresolved:
        label = "UNRESOLVED_GEOMETRY"
    else:
        label = "MESH_SEPARATED_BROAD_PHASE_CANDIDATES"
    return {
        "method": "SWEPT_AABB_THEN_LOCAL_MESH",
        "label": label,
        "confirmed_collision_result": label == "CONFIRMED_MESH_INTERSECTION",
        "confirmed_separation": label == "MESH_SEPARATED_BROAD_PHASE_CANDIDATES",
        "broad_phase_hit_count": broad["hit_count"],
        "confirmed_mesh_intersections": confirmed[:20],
        "clearance_violations": clearance[:20],
        "mesh_separated_samples": separated[:20],
        "unresolved_global_ids": sorted(set(unresolved))[:20],
        "allowance_segments_not_counted_against_boundary_host": sorted(set(allowance_ignored)),
        "excluded_replaced_host_global_ids": sorted(replaced),
        "body_half_extent_m": body,
        "required_separation_m": required,
        "additional_clearance_m": required - body,
        "note": (
            "Mesh tests cover swept-AABB candidates only. Separation from those candidates is not clearance "
            "from every untested solid. Zero broad-phase hits would not be reported as confirmed separation."
        ),
    }


def _candidate_record(preview, prepared, occupancy, terminals, qualified) -> dict[str, Any]:
    start = prepared["start_interface"]
    end = prepared["end_interface"]
    reasons = [item["qualification"]["status"] for item in terminals]
    return {
        "discipline": preview["discipline"],
        "source_file": preview["source_file"],
        "system_names": prepared["system_names"],
        "system_global_ids": prepared["system_global_ids"],
        "storeys": prepared["storeys"],
        "shortlist_reason": preview.get("shortlist_reason"),
        "replaced_host_global_ids": prepared["candidate"]["replaced_host_global_ids"],
        "boundary_host_global_ids": prepared["candidate"]["boundary_host_global_ids"],
        "topology_path_global_ids": (
            prepared["candidate"]["boundary_host_global_ids"][:1]
            + prepared["candidate"]["replaced_host_global_ids"]
            + prepared["candidate"]["boundary_host_global_ids"][1:]
        ),
        "start_interface": _public_interface(start),
        "end_interface": _public_interface(end),
        "straight_line_m": preview["straight_line_m"],
        "z_delta_m": preview["z_delta_m"],
        "approximate_obstacle_count": preview.get("approximate_obstacle_count"),
        "roi_obstacle_count": len(occupancy["obstacles"]),
        "endpoint_qualification": [item["qualification"] for item in terminals],
        "qualified": qualified,
        "rejection_reason": None if qualified else reasons,
        "grid_origin_m": [
            occupancy["grid"].spec.origin_x,
            occupancy["grid"].spec.origin_y,
            occupancy["grid"].spec.origin_z,
        ],
        "containing_indices": [item.get("containing_index") for item in terminals],
    }


def _failed_preview(preview, reason: str) -> dict[str, Any]:
    return {
        "discipline": preview["discipline"],
        "source_file": preview["source_file"],
        "system_names": preview.get("system_names"),
        "system_global_ids": preview.get("system_global_ids"),
        "storeys": preview.get("storeys"),
        "shortlist_reason": preview.get("shortlist_reason"),
        "replaced_host_global_ids": preview["replaced_host_global_ids"],
        "boundary_host_global_ids": preview["boundary_host_global_ids"],
        "topology_path_global_ids": (
            preview["boundary_host_global_ids"][:1]
            + preview["replaced_host_global_ids"]
            + preview["boundary_host_global_ids"][1:]
        ),
        "preview_start_m": preview["preview_start_m"],
        "preview_goal_m": preview["preview_goal_m"],
        "straight_line_m": preview["straight_line_m"],
        "z_delta_m": preview["z_delta_m"],
        "approximate_obstacle_count": preview.get("approximate_obstacle_count"),
        "qualified": False,
        "rejection_reason": reason,
        "endpoint_qualification": [],
    }


def _request_document(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_kind": "EXISTING_SUBPATH_REPLACEMENT",
        "not_a_native_source_to_terminal_design_request": True,
        "endpoint_policy": "IMMUTABLE_PORT_PLUS_LOCAL_EVIDENCE",
        "frozen_before_search": True,
        "discipline": record["discipline"],
        "source_file": record["source_file"],
        "system_names": record["system_names"],
        "system_global_ids": record["system_global_ids"],
        "storeys": record["storeys"],
        "replaced_host_global_ids": record["replaced_host_global_ids"],
        "boundary_host_global_ids": record["boundary_host_global_ids"],
        "topology_path_global_ids": record["topology_path_global_ids"],
        "start_interface": record["start_interface"],
        "end_interface": record["end_interface"],
        "allowances": [item.get("allowance") for item in record["endpoint_qualification"] if item.get("allowance")],
        "endpoint_qualification_status": [item.get("status") for item in record["endpoint_qualification"]],
    }


def _public_candidate(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _public_attempt(outcome: dict[str, Any]) -> dict[str, Any]:
    execution = outcome.get("execution") or {}
    return {
        "astar_invoked": outcome["flags"]["astar_invoked"],
        "path_found": outcome["flags"]["path_found"],
        "search_status": execution.get("search_status"),
        "geometric_validation_status": outcome["validation"]["label"],
        "exact_port_to_port_connection": execution.get("exact_port_to_port_connection"),
        "physical_route_length_m": execution.get("physical_route_length_m"),
        "physical_vertical_travel_m": execution.get("physical_vertical_travel_m"),
    }


def _original_report(records: list[dict[str, Any]], original_ids: tuple[str, ...] | None) -> dict[str, Any] | None:
    for record in records:
        if original_ids is not None and tuple(record["replaced_host_global_ids"]) == original_ids:
            return _public_candidate(record)
    return None


def _summary(records, selected, original_ids) -> dict[str, Any]:
    original = _original_report(records, original_ids)
    execution = None if selected is None else selected.get("execution")
    return {
        "candidates_evaluated": len(records),
        "original_h_rl_68_status": None if original is None else original.get("rejection_reason") or original.get("endpoint_qualification"),
        "selected_request": None if execution is None else {
            "discipline": execution.get("discipline"),
            "system_names": execution.get("system_names"),
            "storeys": execution.get("storeys"),
            "replaced_host_global_ids": execution.get("replaced_host_global_ids"),
            "boundary_host_global_ids": execution.get("boundary_host_global_ids"),
        },
        "start_and_goal_physically_qualified": execution is not None,
        "astar_invoked": bool(execution and execution.get("astar_invoked")),
        "search_status": None if execution is None else execution.get("search_status"),
        "exact_port_to_port_connection": bool(execution and execution.get("exact_port_to_port_connection")),
        "physical_route_length_m": None if execution is None else execution.get("physical_route_length_m"),
        "physical_vertical_travel_m": None if execution is None else execution.get("physical_vertical_travel_m"),
        "geometric_validation_status": None if execution is None else execution["collision_validation"]["label"],
        "qualified_candidate_count": sum(1 for item in records if item.get("qualified")),
    }


def _geometry_cache(files: dict[str, Path], cache_path: Path, threads: int) -> dict[str, dict[str, Any]]:
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return payload["roles"]
    roles = {}
    for role, path in files.items():
        roles[role] = load_role_geometry(path, threads=threads)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"roles": roles}, default=_json_default), encoding="utf-8")
    return roles


def _json_default(value):
    if isinstance(value, (bytes, bytearray)):
        return list(value)
    return float(value) if hasattr(value, "item") else str(value)


def _slab_bounds(architecture: dict[str, Any] | None) -> list[tuple[float, float, float, float, float, float]]:
    if not architecture:
        return []
    bounds = []
    for element in architecture["collected"]["elements"].values():
        if element.get("ifc_class") == "IfcSlab":
            bounds.append(bounds_tuple(element["bounds_m"]))
    return bounds


def _inside_any(point: list[float], boxes: list[tuple[float, float, float, float, float, float]]) -> bool:
    return any(
        box[0] <= point[0] <= box[1] and box[2] <= point[1] <= box[3] and box[4] <= point[2] <= box[5]
        for box in boxes
    )


def _corridor_obstacle_count(cache, start, goal, replaced: set[str]) -> int:
    low = [min(start[axis], goal[axis]) - 0.5 for axis in range(3)]
    high = [max(start[axis], goal[axis]) + 0.5 for axis in range(3)]
    roi = (low[0], high[0], low[1], high[1], low[2], high[2])
    count = 0
    for role, payload in cache.items():
        for global_id, element in payload["collected"]["elements"].items():
            if global_id in replaced:
                continue
            if bounds_intersect(bounds_tuple(element["bounds_m"]), roi):
                count += 1
    return count


def _host_port_world(model, connections, retained_id: str, replaced_id: str, scale: float) -> list[float] | None:
    relations = _relations_for_pair(connections, retained_id, replaced_id)
    if not relations:
        return None
    relation = relations[0]
    if relation["relating_host_global_id"] == retained_id:
        port_id = relation["relating_port_global_id"]
    elif relation["related_host_global_id"] == retained_id:
        port_id = relation["related_port_global_id"]
    else:
        return None
    if not port_id:
        return None
    port = model.by_guid(port_id)
    world = None if port is None else placement_origin_m(port, scale)
    if world is None:
        return None
    return [float(value) for value in world]


def _adjacency(connections: list[dict[str, Any]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for item in connections:
        left = item["relating_host_global_id"]
        right = item["related_host_global_id"]
        if left and right and left != right:
            adjacency.setdefault(left, set()).add(right)
            adjacency.setdefault(right, set()).add(left)
    return adjacency


def _facts_for(models, role: str, global_id: str, point, center, mesh_cache) -> dict[str, Any]:
    arrays = _mesh_arrays(models, role, global_id, mesh_cache)
    if arrays is None:
        return {"resolved": False, "reason": "MESH_UNAVAILABLE"}
    vertices, faces = arrays
    return mesh_facts(point, center, triangles_from_mesh(vertices, faces))


def _mesh_arrays(models, role: str, global_id: str, mesh_cache):
    key = (role, global_id)
    if key in mesh_cache:
        return mesh_cache[key]
    model = models.get(role)
    entity = None if model is None else model.by_guid(global_id)
    if entity is None or getattr(entity, "Representation", None) is None:
        mesh_cache[key] = None
        return None
    try:
        shape = ifcopenshell.geom.create_shape(world_settings(), entity)
        vertices = list(world_vertices_metres(shape.geometry.verts))
        faces = [int(value) for value in shape.geometry.faces]
    except Exception:
        mesh_cache[key] = None
        return None
    mesh_cache[key] = (vertices, faces)
    return mesh_cache[key]


def _openings_for_host(voids: dict[str, Any], host_id: str) -> list[dict[str, Any]]:
    found = []
    for opening_id, links in voids.items():
        for link in links:
            if link.get("host_global_id") == host_id:
                found.append({
                    "opening_global_id": opening_id,
                    "relation_global_id": link.get("relation_global_id"),
                    "host_global_id": host_id,
                })
    return found


def _segment_samples(start, end, count: int = 5) -> list[list[float]]:
    start_v = np.asarray(start, dtype=float)
    end_v = np.asarray(end, dtype=float)
    return [list(start_v + (end_v - start_v) * (index / (count - 1))) for index in range(count)]


def _segment_inside_allowance(start, end, global_id: str, allowances: list[dict[str, Any]], spec) -> bool:
    for allowance in allowances:
        if allowance.get("host_global_id") != global_id or not allowance.get("cell_index"):
            continue
        index = allowance["cell_index"]
        low = [getattr(spec, f"origin_{axis}") + index[axis_index] * spec.voxel_size_m for axis_index, axis in enumerate("xyz")]
        high = [value + spec.voxel_size_m for value in low]
        if _point_in_box(start, low, high) and _point_in_box(end, low, high):
            return True
    return False


def _point_in_box(point, low, high) -> bool:
    return all(low[axis] - 1e-8 <= float(point[axis]) <= high[axis] + 1e-8 for axis in range(3))


def _aabb_overlap(first, second) -> bool:
    return positive_aabb_overlap(first, second)


def _original_replaced_ids(path: Path) -> tuple[str, ...] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    replaced = payload.get("replaced_host_global_ids")
    if not replaced:
        return None
    return tuple(replaced)

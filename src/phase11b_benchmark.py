"""Phase 11B: five real-IFC routes with clearance-aware obstacle inflation.

The frozen ``astar_3d`` search is not modified. Occupancy inflation, mesh contact,
and validation thresholds live in this adapter.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

import ifcopenshell

from .digitalhub_port_pilot import (
    _corridor_obstacle_count,
    _geometry_cache,
    _inside_any,
    _mesh_arrays,
    _previews,
    _qualify_preview,
    _run_search,
    _segment_samples,
    _slab_bounds,
)
from .endpoint_qualification import execution_status_flags
from .external_ifc_probe import OPENING_POLICY, write_json
from .external_ifc_routing import (
    MAX_GRID_CELLS,
    _working_set,
    discover_discipline_files,
    estimate_cell_count,
)
from .ifc_mesh_section import surface_distance_m, triangles_from_mesh, vertical_section
from .phase11b_clearance import (
    BASELINE_MODE,
    BOUNDARY_HOST_CONNECTION_CONTACT,
    CLEARANCE_AWARE_MODE,
    CLEARANCE_POLICY,
    CLEARANCE_VIOLATION,
    CONFIRMED_GEOMETRIC_COLLISION,
    COVERING_CONTACT_BASELINE_M,
    FINER_GRID_MODE,
    FINER_VOXEL_SIZE_M,
    GEOMETRIC_VALIDATION_INCOMPLETE,
    PHYSICALLY_ACCEPTABLE,
    SEARCH_PATH_FOUND,
    SENSITIVITY_ADDITIONAL_CLEARANCE_M,
    VOXEL_SIZE_M,
    classify_proximity,
    geometric_state,
    phase11b_rank,
    point_in_allowance_cell,
    same_point,
    search_state,
    shared_roi_group,
)
from .route_geometry import Segment, swept_segment_aabb
from .routing import astar_3d, route_metrics
from .voxel import BLOCKED_FIXED, FREE, aabb_index_ranges


QUOTAS = (("ventilation", 1, "V"), ("heating", 2, "H"), ("sanitary", 2, "S"))
MAX_EXAMINE_PER_DISCIPLINE = 24
SELECTION_BUDGET_S = 1800.0
FROZEN_FILES = (
    "first_valid_routing_request.json",
    "first_valid_routing_execution.json",
    "port_pilot_summary.json",
    "candidate_routing_requests.json",
)
CSV_FIELDS = (
    "request_id", "discipline", "system_names", "storeys", "mode", "additional_clearance_m",
    "voxel_size_m", "search_state", "geometric_state", "path_found", "astar_invoked",
    "exact_port_to_port", "physical_route_length_m", "physical_vertical_travel_m",
    "physical_bend_count", "expanded_node_count", "runtime_seconds",
    "python_tracemalloc_peak_bytes", "process_peak_working_set",
)


def execute_phase11b(data_dir: Path, output_dir: Path, *, threads: int = 4) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frozen = _freeze_baseline(output_dir)
    files = discover_discipline_files(data_dir)
    models = {role: ifcopenshell.open(str(path)) for role, path in files.items()}
    mesh_cache: dict[tuple[str, str], Any] = {}
    evidence = _diagnose_frozen(output_dir, models, mesh_cache)
    previews = _previews(models, files)
    cache = _geometry_cache(files, output_dir / "world_aabb_cache.json", threads)
    slabs = _slab_bounds(cache.get("architecture"))
    for preview in previews:
        start = preview["preview_start_m"]
        goal = preview["preview_goal_m"]
        preview["port_inside_slab_aabb"] = _inside_any(start, slabs) or _inside_any(goal, slabs)
        preview["approximate_obstacle_count"] = _corridor_obstacle_count(
            cache, start, goal, set(preview["replaced_host_global_ids"]),
        )
    manifest = _read_json(output_dir / "dataset_manifest.json")
    skip = _replaced_ids(output_dir / "routing_request.json")
    prefer = _replaced_ids(output_dir / "historical_phase11a2" / "first_valid_routing_request.json")
    requests, examined, cases = _select_and_execute(
        previews, models, files, cache, mesh_cache, threads, manifest, skip, prefer, evidence,
    )
    multi = _coordinate(requests, cases, models, files, cache, mesh_cache, threads)
    payload = {
        "dataset_id": "rwth-digitalhub-v1",
        "frozen_phase11a2": frozen,
        "clearance_policy": CLEARANCE_POLICY,
        "opening_policy": OPENING_POLICY,
        "obstacle_policy": (
            "Exclude only the exact replaced IFC hosts. Retain architecture, structure carried in the "
            "architecture model, and unrelated MEP. Openings are inventoried and are not subtracted. "
            "If the exported host mesh still encloses a point, the opening does not clear it."
        ),
        "h_rl_68_revived": False,
        "skipped_replaced_host_global_ids": list(skip or ()),
        "requests": requests,
        "examined_not_qualified": examined,
        "cases": cases,
        "multi_system": multi,
        "counts": _counts(requests, cases, multi),
    }
    evidence["requests"] = [_evidence_request(item) for item in requests]
    evidence["case_validations"] = [_evidence_case(item) for item in cases]
    evidence["multi_system_status"] = multi.get("status")
    _write_outputs(output_dir, payload, evidence)
    return {"mandatory": payload["counts"], "outputs": _output_paths(output_dir)}


def _select_and_execute(previews, models, files, cache, mesh_cache, threads, manifest, skip, prefer, evidence):
    requests = []
    examined = []
    cases = []
    started = time.perf_counter()
    for discipline, quota, prefix in QUOTAS:
        ranked = phase11b_rank(
            previews,
            discipline,
            skip_replaced=() if skip is None else (skip,),
            prefer_replaced=prefer if discipline == "ventilation" else None,
        )
        found = 0
        seen = 0
        if time.perf_counter() - started > SELECTION_BUDGET_S:
            examined.append({
                "discipline": discipline,
                "shortfall": True,
                "qualified": 0,
                "target": quota,
                "examined": 0,
                "ranked_available": len(ranked),
                "reason": "Selection budget elapsed before this discipline was examined.",
            })
            continue
        print(f"phase11b select {discipline}: {len(ranked)} ranked previews", flush=True)
        for preview in ranked:
            if found >= quota or seen >= MAX_EXAMINE_PER_DISCIPLINE:
                break
            if time.perf_counter() - started > SELECTION_BUDGET_S and found > 0:
                break
            seen += 1
            print(
                f"  qualify {discipline} {seen} system={preview.get('system_names')} "
                f"length={preview.get('straight_line_m'):.2f}",
                flush=True,
            )
            record = _qualify_preview(preview, models, files, cache, mesh_cache, threads)
            if not record.get("qualified"):
                examined.append(_short_reject(preview, record))
                statuses = examined[-1]["endpoint_status"]
                print(f"    rejected {statuses or examined[-1]['rejection_reason']}", flush=True)
                _release(record)
                continue
            found += 1
            request_id = f"DH11B-{prefix}{found}"
            request = _freeze_request(request_id, record, manifest)
            request["_preview"] = preview
            request["containing_indices"] = _indices(record)
            _attach_body(evidence, request)
            baseline = _execute_case(
                request_id, record, models, files, cache, mesh_cache,
                mode=BASELINE_MODE, additional_clearance_m=0.0, voxel_size_m=VOXEL_SIZE_M,
                required_separation_m=float(record["_prepared"]["body_half_extent_m"]),
            )
            request_cases = [baseline]
            _release(record)
            if baseline["geometric_state"] in {CLEARANCE_VIOLATION, CONFIRMED_GEOMETRIC_COLLISION}:
                request_cases.extend(_clearance_reruns(
                    preview, request, models, files, cache, mesh_cache, threads,
                ))
            if discipline == "ventilation" and found == 1:
                finer = _finer_grid_case(preview, request, models, files, cache, mesh_cache, threads)
                if finer is not None:
                    request_cases.append(finer)
            request["cases_run"] = [item["mode"] + f"@{item['additional_clearance_m']}" for item in request_cases]
            requests.append(request)
            cases.extend(request_cases)
            print(
                f"  {request_id} baseline {baseline['search_state']} / {baseline['geometric_state']}",
                flush=True,
            )
        if found < quota:
            examined.append({
                "discipline": discipline,
                "shortfall": True,
                "qualified": found,
                "target": quota,
                "examined": seen,
                "ranked_available": len(ranked),
                "reason": "Not enough physically qualified requests in the examined deterministic inventory.",
            })
    return requests, examined, cases


def _clearance_reruns(preview, request, models, files, cache, mesh_cache, threads) -> list[dict[str, Any]]:
    body = float(request["body_half_extent_m"])
    cases = []
    for additional in SENSITIVITY_ADDITIONAL_CLEARANCE_M:
        margin = body + additional
        print(f"  clearance-aware {request['request_id']} additional={additional}", flush=True)
        record = _qualify_preview(
            preview, models, files, cache, mesh_cache, threads,
            inflation_m=margin, mesh_contact_m=margin, solid_contact_m=margin,
        )
        _assert_endpoints(request, record)
        if not record.get("qualified"):
            flags = execution_status_flags(
                cli_completed=True, astar_invoked=False, path_found=False, validation_label="NOT_RUN_ENDPOINT_INVALID",
            )
            outcome = {"flags": flags, "validation": {"label": "NOT_RUN_ENDPOINT_INVALID"}, "execution": None}
            cases.append(_compact_case(
                request, record, outcome, CLEARANCE_AWARE_MODE, additional, VOXEL_SIZE_M, margin,
            ))
            _release(record)
            continue
        cases.append(_execute_case(
            request["request_id"], record, models, files, cache, mesh_cache,
            mode=CLEARANCE_AWARE_MODE, additional_clearance_m=additional, voxel_size_m=VOXEL_SIZE_M,
            required_separation_m=margin,
        ))
        _release(record)
    return cases


def _finer_grid_case(preview, request, models, files, cache, mesh_cache, threads) -> dict[str, Any] | None:
    body = float(request["body_half_extent_m"])
    estimated = estimate_cell_count(tuple(request["span_bounds_m"]), request["roi_margin_m"], FINER_VOXEL_SIZE_M)
    if estimated > MAX_GRID_CELLS:
        return {
            **_empty_case(request, FINER_GRID_MODE, 0.0, FINER_VOXEL_SIZE_M),
            "search_state": "NOT_RUN",
            "note": (
                f"Finer-grid sensitivity not run. Estimated {estimated} cells exceed the adapter limit "
                f"of {MAX_GRID_CELLS}. A* was not changed."
            ),
        }
    print(f"  finer grid {request['request_id']} estimated_cells={estimated}", flush=True)
    try:
        record = _qualify_preview(
            preview, models, files, cache, mesh_cache, threads,
            inflation_m=body, mesh_contact_m=body, solid_contact_m=body, voxel_size_m=FINER_VOXEL_SIZE_M,
        )
    except Exception as exc:
        return {
            **_empty_case(request, FINER_GRID_MODE, 0.0, FINER_VOXEL_SIZE_M),
            "search_state": "NOT_RUN",
            "note": f"Finer-grid adapter refused the ROI: {type(exc).__name__}: {exc}",
        }
    _assert_endpoints(request, record, compare_indices=False)
    if not record.get("qualified"):
        flags = execution_status_flags(
            cli_completed=True, astar_invoked=False, path_found=False, validation_label="NOT_RUN_ENDPOINT_INVALID",
        )
        outcome = {"flags": flags, "validation": {"label": "NOT_RUN_ENDPOINT_INVALID"}, "execution": None}
        case = _compact_case(request, record, outcome, FINER_GRID_MODE, 0.0, FINER_VOXEL_SIZE_M, body)
    else:
        case = _execute_case(
            request["request_id"], record, models, files, cache, mesh_cache,
            mode=FINER_GRID_MODE, additional_clearance_m=0.0, voxel_size_m=FINER_VOXEL_SIZE_M,
            required_separation_m=body,
        )
    case["note"] = (
        "Localized 0.05 m sensitivity using the same frozen A*. "
        "It does not replace the 0.1 m result. Covering cells above the adapter mesh budget fall back to AABBs."
    )
    case["estimated_cells"] = estimated
    _release(record)
    return case


def _execute_case(request_id, record, models, files, cache, mesh_cache, *, mode, additional_clearance_m, voxel_size_m, required_separation_m):
    outcome = _run_search(
        record, files, cache, models, mesh_cache, required_separation_m=required_separation_m,
    )
    request = {"request_id": request_id, **_identity(record)}
    return _compact_case(request, record, outcome, mode, additional_clearance_m, voxel_size_m, required_separation_m)


def _compact_case(request, record, outcome, mode, additional, voxel, required) -> dict[str, Any]:
    execution = outcome.get("execution") or {}
    validation = outcome.get("validation") or {}
    flags = outcome["flags"]
    terminals = record.get("_terminals") or []
    statuses = [item.get("qualification", {}).get("status") for item in terminals]
    path_found = bool(flags.get("path_found"))
    metrics = execution.get("physical_route_metrics") or {}
    memory = execution.get("process_memory") or {}
    refinement = record.get("occupancy_refinement") or []
    fallback = sum(1 for item in refinement if item.get("method") == "AABB_FALLBACK")
    state = geometric_state(validation.get("label"), path_found)
    return {
        "request_id": request["request_id"],
        "discipline": request.get("discipline") or record.get("discipline"),
        "system_names": request.get("system_names") or record.get("system_names"),
        "storeys": request.get("storeys") or record.get("storeys"),
        "mode": mode,
        "additional_clearance_m": additional,
        "required_separation_m": required,
        "voxel_size_m": voxel,
        "search_state": search_state(
            astar_invoked=bool(flags.get("astar_invoked")),
            path_found=path_found,
            endpoint_statuses=[status or "" for status in statuses],
        ),
        "geometric_state": state,
        "validation_label": validation.get("label"),
        "astar_invoked": bool(flags.get("astar_invoked")),
        "path_found": path_found,
        "exact_port_to_port": execution.get("exact_port_to_port_connection"),
        "physical_route_length_m": execution.get("physical_route_length_m"),
        "physical_vertical_travel_m": execution.get("physical_vertical_travel_m"),
        "physical_bend_count": metrics.get("bend_count"),
        "expanded_node_count": execution.get("expanded_node_count"),
        "runtime_seconds": execution.get("runtime_seconds"),
        "python_tracemalloc_peak_bytes": execution.get("python_tracemalloc_peak_bytes"),
        "process_memory": memory,
        "immutable_start_m": _world(record, "start") or request.get("start_m"),
        "immutable_goal_m": _world(record, "end") or request.get("goal_m"),
        "start_grid_index": execution.get("start_grid_index") or _index(terminals, 0),
        "goal_grid_index": execution.get("goal_grid_index") or _index(terminals, 1),
        "endpoint_status": statuses,
        "allowances": execution.get("allowances") or [
            item.get("qualification", {}).get("allowance") for item in terminals if item.get("qualification", {}).get("allowance")
        ],
        "collision_validation": {
            key: validation.get(key) for key in (
                "label", "method", "broad_phase_hit_count", "confirmed_mesh_intersections",
                "clearance_violations", "mesh_separated_samples", "unresolved_global_ids",
                "allowance_segments_not_counted_against_boundary_host", "required_separation_m",
                "body_half_extent_m", "additional_clearance_m", "confirmed_collision_result",
                "confirmed_separation", "note",
            )
        },
        "world_polyline_m": execution.get("world_polyline_m"),
        "occupancy_policy": record.get("occupancy_policy"),
        "body_half_extent_m": (record.get("occupancy_policy") or {}).get("body_half_extent_m") or request.get("body_half_extent_m"),
        "mesh_refinement_fallback_count": fallback,
        "mesh_refined_obstacle_count": sum(1 for item in refinement if item.get("method") == "MESH_ENCLOSURE_OR_CONTACT"),
        "physically_acceptable_under_declared_policy": state == PHYSICALLY_ACCEPTABLE,
        "path_found_is_not_sufficient": path_found and state != PHYSICALLY_ACCEPTABLE,
    }


def _empty_case(request, mode, additional, voxel) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "discipline": request["discipline"],
        "system_names": request["system_names"],
        "storeys": request["storeys"],
        "mode": mode,
        "additional_clearance_m": additional,
        "voxel_size_m": voxel,
        "search_state": "NOT_RUN",
        "geometric_state": None,
        "path_found": False,
        "astar_invoked": False,
        "exact_port_to_port": None,
        "physical_route_length_m": None,
        "physical_vertical_travel_m": None,
        "physical_bend_count": None,
        "expanded_node_count": None,
        "runtime_seconds": None,
        "python_tracemalloc_peak_bytes": None,
        "process_memory": _working_set(),
    }


def _coordinate(requests, cases, models, files, cache, mesh_cache, threads) -> dict[str, Any]:
    group = shared_roi_group(requests)
    base = {
        "b0": "Independent frozen astar_3d on each real-IFC occupancy. Those results are the per-request baseline cases.",
        "b1_b2_pcore_support_real_ifc_requests": False,
        "b1_b2_pcore_reason": (
            "run_b1_benchmark, B2, and P-CORE consume synthetic scenario anchors and service grids. "
            "They re-snap anchors and do not accept these IFC endpoints or obstacle fields."
        ),
    }
    if group is None:
        return {
            **base,
            "status": "MULTI_SYSTEM_COMPARISON_NOT_YET_VALID",
            "executed": False,
            "reason": "Fewer than three physically qualified requests share a ROI of at least 0.5 m on every axis.",
            "request_rois": [_roi_summary(item) for item in requests],
        }
    selected = [item for item in requests if item["request_id"] in group]
    selected.sort(key=lambda item: item["request_id"])
    print(f"phase11b coordination {group}", flush=True)
    sequential = _sequential_batch(selected, models, files, cache, mesh_cache, threads)
    independent = [
        item for item in cases
        if item["request_id"] in group and item["mode"] == BASELINE_MODE
    ]
    return {
        **base,
        "status": "EXECUTED_INDEPENDENT_B0_AND_SEQUENTIAL_PRIOR_ROUTE_BLOCKING",
        "executed": True,
        "request_ids": [item["request_id"] for item in selected],
        "order": [item["request_id"] for item in selected],
        "clearance_policy": "Baseline occupancy. Prior-route cells are blocked by the sum of the two body half-extents.",
        "independent_conflicts": _pairwise(independent, 0.0),
        "sequential": sequential,
    }


def _sequential_batch(requests, models, files, cache, mesh_cache, threads) -> dict[str, Any]:
    prior = []
    results = []
    for request in requests:
        preview = request.get("_preview")
        if preview is None:
            results.append({"request_id": request["request_id"], "search_state": "NOT_RUN", "astar_invoked": False, "note": "Preview was not retained."})
            continue
        record = _qualify_preview(preview, models, files, cache, mesh_cache, threads)
        _assert_endpoints(request, record)
        if not record.get("qualified"):
            results.append({"request_id": request["request_id"], "search_state": "ENDPOINT_BLOCKED", "astar_invoked": False})
            _release(record)
            continue
        grid = record["_occupancy"]["grid"]
        blocked = 0
        body = float(request["body_half_extent_m"])
        for earlier in prior:
            if earlier.get("world_polyline_m"):
                blocked += _block_polyline(grid, earlier["world_polyline_m"], body + float(earlier["body_half_extent_m"]))
        start = tuple(record["_terminals"][0]["containing_index"])
        goal = tuple(record["_terminals"][1]["containing_index"])
        if grid.cells[grid.spec.linear_index(*start)] != FREE or grid.cells[grid.spec.linear_index(*goal)] != FREE:
            results.append({
                "request_id": request["request_id"],
                "search_state": "ENDPOINT_BLOCKED",
                "astar_invoked": False,
                "prior_route_cells_blocked": blocked,
                "note": "A prior route occupies the immutable endpoint cell. The endpoint was not moved.",
            })
            _release(record)
            continue
        started = time.perf_counter()
        result = astar_3d(grid, start, goal)
        runtime = time.perf_counter() - started
        entry = {
            "request_id": request["request_id"],
            "search_state": SEARCH_PATH_FOUND if result.path_found else "SEARCH_NO_PATH",
            "astar_invoked": True,
            "path_found": result.path_found,
            "expanded_node_count": result.expanded_node_count,
            "runtime_seconds": round(runtime, 6),
            "prior_route_cells_blocked": blocked,
            "grid_metrics": route_metrics(result, grid.spec.voxel_size_m),
            "body_half_extent_m": body,
        }
        if result.path_found:
            from .endpoint_grid import assemble_physical_polyline
            physical = assemble_physical_polyline(
                tuple(request["start_m"]),
                [list(grid.spec.cell_center(*cell)) for cell in result.path_cells],
                tuple(request["goal_m"]),
            )
            entry["world_polyline_m"] = physical["polyline_m"]
            entry["exact_port_to_port"] = physical["exact_port_to_port"]
        results.append(entry)
        if entry.get("world_polyline_m"):
            prior.append(entry)
        _release(record)
    return {
        "method": "SEQUENTIAL_ASTAR_PRIOR_ROUTE_RESERVATION",
        "not_the_frozen_b1_runner": True,
        "routes": [{key: value for key, value in item.items() if key != "world_polyline_m"} | {"has_polyline": bool(item.get("world_polyline_m"))} for item in results],
        "conflicts": _pairwise(results, 0.0),
    }


def _pairwise(routes: list[dict[str, Any]], extra_m: float) -> list[dict[str, Any]]:
    usable = []
    for item in routes:
        body = item.get("body_half_extent_m")
        if body is None and item.get("additional_clearance_m") == 0.0:
            body = item.get("required_separation_m")
        if item.get("world_polyline_m") and body is not None:
            usable.append({**item, "body_half_extent_m": float(body)})
    conflicts = []
    for index, left in enumerate(usable):
        for right in usable[index + 1:]:
            required = left["body_half_extent_m"] + right["body_half_extent_m"] + extra_m
            conflicts.append({
                "left": left["request_id"],
                "right": right["request_id"],
                "required_separation_m": required,
                **_route_pair(
                    left["world_polyline_m"], right["world_polyline_m"],
                    left["body_half_extent_m"], right["body_half_extent_m"], extra_m,
                ),
            })
    return conflicts


def _route_pair(left_points, right_points, left_half: float, right_half: float, extra_m: float) -> dict[str, Any]:
    required = left_half + right_half + extra_m
    overlapped = False
    for start, end in _segments(left_points):
        left_box = swept_segment_aabb(Segment(start, end), left_half + extra_m, left_half + extra_m)
        for other_start, other_end in _segments(right_points):
            right_box = swept_segment_aabb(Segment(other_start, other_end), right_half, right_half)
            if _boxes_overlap(left_box, right_box):
                overlapped = True
                break
        if overlapped:
            break
    if not overlapped:
        return {
            "geometric_state": "SEPARATED_UNDER_DECLARED_POLICY",
            "measurement": "SWEPT_AABB_ENVELOPES",
            "broad_phase_candidate": False,
            "required_separation_m": required,
            "note": "The declared body envelopes do not overlap. This is not a triangle-mesh test of an installed route solid.",
        }
    distance = _min_sample_distance(left_points, right_points)
    if distance is not None and distance + 1e-6 < required:
        state = CLEARANCE_VIOLATION
    else:
        state = "BROAD_PHASE_CANDIDATE"
    return {
        "geometric_state": state,
        "min_centerline_sample_distance_m": distance,
        "required_separation_m": required,
        "measurement": "SWEPT_AABB_THEN_CENTERLINE_SAMPLES",
        "broad_phase_candidate": True,
        "note": "Centreline samples are not a full swept-volume intersection. An AABB overlap without a sample violation stays a broad-phase candidate.",
    }


def _block_polyline(grid, points, half: float) -> int:
    marked = 0
    spec = grid.spec
    for start, end in _segments(points):
        swept = swept_segment_aabb(Segment(start, end), half, half)
        ranges = aabb_index_ranges(spec, swept)
        for k in ranges[2]:
            for j in ranges[1]:
                base = spec.linear_index(0, j, k)
                for i in ranges[0]:
                    index = base + i
                    if grid.cells[index] != BLOCKED_FIXED:
                        grid.cells[index] = BLOCKED_FIXED
                        marked += 1
    return marked


def _segments(points):
    for start, end in zip(points, points[1:]):
        start_t = tuple(float(value) for value in start)
        end_t = tuple(float(value) for value in end)
        if all(abs(start_t[axis] - end_t[axis]) <= 1e-12 for axis in range(3)):
            continue
        yield start_t, end_t


def _boxes_overlap(left, right) -> bool:
    return not (
        left[1] < right[0] or left[0] > right[1]
        or left[3] < right[2] or left[2] > right[3]
        or left[5] < right[4] or left[4] > right[5]
    )


def _min_sample_distance(left_points, right_points) -> float | None:
    best = None
    left_samples = [sample for start, end in _segments(left_points) for sample in _segment_samples(start, end)]
    right_samples = [sample for start, end in _segments(right_points) for sample in _segment_samples(start, end)]
    for left in left_samples:
        for right in right_samples:
            distance = sum((float(left[axis]) - float(right[axis])) ** 2 for axis in range(3)) ** 0.5
            if best is None or distance < best:
                best = distance
    return best


def _diagnose_frozen(output_dir: Path, models, mesh_cache) -> dict[str, Any]:
    path = output_dir / "historical_phase11a2" / "first_valid_routing_execution.json"
    request_path = output_dir / "historical_phase11a2" / "first_valid_routing_request.json"
    execution = _read_json(path)
    request = _read_json(request_path)
    validation = execution["collision_validation"]
    offenders = []
    for item in validation.get("clearance_violations") or []:
        offenders.append(_offender_detail(execution, item, models, mesh_cache))
    separated = []
    for item in validation.get("mesh_separated_samples") or []:
        separated.append({
            "global_id": item["global_id"],
            "file_role": item["file_role"],
            "min_centerline_distance_m": item.get("min_centerline_distance_m"),
            "measurement": "MESH_SURFACE_DISTANCE",
            "state": "SEPARATED_UNDER_DECLARED_POLICY",
            "note": "This broad-phase candidate was mesh-tested. It does not certify elements that produced no AABB hit.",
        })
    body = None
    if validation.get("clearance_violations"):
        body = validation["clearance_violations"][0].get("body_half_extent_m")
    return {
        "artifact": str(path),
        "artifact_sha256": _sha256(path),
        "dataset": "DigitalHub V1",
        "discipline": execution.get("discipline"),
        "system_names": request.get("system_names"),
        "storeys": request.get("storeys"),
        "endpoint_evidence_class": [
            request["start_interface"]["coordinate_classification"],
            request["end_interface"]["coordinate_classification"],
        ],
        "endpoint_class_is_not_native_port_placement": True,
        "replaced_host_global_ids": request.get("replaced_host_global_ids"),
        "start_m": execution.get("immutable_start_m"),
        "goal_m": execution.get("immutable_goal_m"),
        "physical_route_length_m": execution.get("physical_route_length_m"),
        "physical_vertical_travel_m": execution.get("physical_vertical_travel_m"),
        "physical_bend_count": (execution.get("physical_route_metrics") or {}).get("bend_count"),
        "expanded_node_count": execution.get("expanded_node_count"),
        "runtime_seconds": execution.get("runtime_seconds"),
        "search_state": SEARCH_PATH_FOUND,
        "geometric_state": CLEARANCE_VIOLATION,
        "policy_that_produced_clearance_violation": {
            "label": validation.get("label"),
            "method": validation.get("method"),
            "rule": "A centreline sample whose mesh surface distance is below the measured body half-extent is a clearance violation.",
            "additional_clearance_m": 0.0,
            "required_separation_m": body,
            "body_half_extent_m": body,
            "benchmark_v1_50mm_was_not_this_rule": True,
            "confirmed_intersection_required": "Vertical section hits both below and above the sample.",
            "non_intersection_is_not_sufficient_clearance": True,
        },
        "broad_phase_hit_count": validation.get("broad_phase_hit_count"),
        "broad_phase_meaning": "BROAD_PHASE_CANDIDATE only. Mesh distance decides the violation.",
        "offenders": offenders,
        "mesh_separated_broad_phase_candidates": separated,
        "allowance_hosts_not_dropped": [
            item.get("host_global_id") for item in execution.get("allowances") or []
        ],
    }


def _offender_detail(execution, item, models, mesh_cache) -> dict[str, Any]:
    allowances = execution.get("allowances") or []
    host_ids = {allowance.get("host_global_id") for allowance in allowances}
    is_boundary = item["global_id"] in host_ids
    arrays = _mesh_arrays(models, item["file_role"], item["global_id"], mesh_cache)
    inside = []
    outside = []
    enclosed_outside = False
    if arrays is not None:
        triangles = triangles_from_mesh(*arrays)
        for start, end in _segments(execution["world_polyline_m"]):
            for sample in _segment_samples(start, end):
                section = vertical_section(sample, triangles)
                distance = surface_distance_m(sample, triangles)
                enclosed = bool(section.get("enclosed"))
                in_cell = any(
                    point_in_allowance_cell(sample, allowance)
                    for allowance in allowances if allowance.get("host_global_id") == item["global_id"]
                )
                if enclosed and not in_cell:
                    enclosed_outside = True
                bucket = inside if in_cell else outside
                bucket.append(distance)
    required = item.get("body_half_extent_m")
    def _min(values):
        finite = [value for value in values if value is not None]
        return None if not finite else min(finite)
    inside_min = _min(inside)
    outside_min = _min(outside)
    return {
        "global_id": item["global_id"],
        "file_role": item["file_role"],
        "reported_min_centerline_distance_m": item.get("min_centerline_distance_m"),
        "body_half_extent_m": required,
        "required_separation_m": required,
        "additional_clearance_m": 0.0,
        "measurement": "MESH_SURFACE_DISTANCE",
        "not_an_aabb_distance": True,
        "broad_phase_only_selected_the_candidate": True,
        "is_retained_boundary_host": is_boundary,
        "samples_inside_boundary_allowance_cell": len(inside),
        "samples_outside_boundary_allowance_cell": len(outside),
        "min_distance_inside_allowance_cell_m": inside_min,
        "min_distance_outside_allowance_cell_m": outside_min,
        "inside_allowance_state": None if inside_min is None else BOUNDARY_HOST_CONNECTION_CONTACT,
        "outside_allowance_state": None if outside_min is None else classify_proximity(
            distance_m=outside_min,
            required_separation_m=float(required),
            enclosed=enclosed_outside,
            obstacle_is_boundary_host=is_boundary,
            sample_inside_boundary_allowance_cell=False,
            measurement="MESH_SURFACE_DISTANCE",
        ),
        "any_recomputed_sample_enclosed_outside_allowance": enclosed_outside,
        "mesh_recompute_available": arrays is not None,
        "non_intersection_is_not_sufficient_clearance": True,
        "interpretation": (
            "Contact inside the boundary-host allowance cell is the physical interface. "
            "A shorter separation outside that cell is a route-body violation against the retained host."
            if is_boundary else
            "This element is not a boundary host. Proximity below the body half-extent is a clearance violation along the route."
        ),
    }


def _freeze_request(request_id: str, record: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    prepared = record["_prepared"]
    occupancy = record["_occupancy"]
    start = prepared["start_interface"]
    end = prepared["end_interface"]
    return {
        "request_id": request_id,
        "dataset_id": manifest.get("dataset_id", "rwth-digitalhub-v1"),
        "ifc_files": [
            {"file_name": item["file_name"], "role": item["role"], "sha256": item["sha256"], "size_bytes": item["size_bytes"]}
            for item in manifest.get("files", [])
        ],
        "discipline": record["discipline"],
        "source_file": record["source_file"],
        "system_names": record["system_names"],
        "system_global_ids": record["system_global_ids"],
        "storeys": record["storeys"],
        "endpoint_evidence_class": [start["coordinate_classification"], end["coordinate_classification"]],
        "relationship_constrained_geometric_is_not_native_port_placement": True,
        "start_m": list(start["world_m"]),
        "goal_m": list(end["world_m"]),
        "start_interface": start,
        "end_interface": end,
        "replaced_host_global_ids": list(prepared["candidate"]["replaced_host_global_ids"]),
        "boundary_host_global_ids": list(prepared["candidate"]["boundary_host_global_ids"]),
        "bodies": prepared["bodies"],
        "body_half_extent_m": prepared["body_half_extent_m"],
        "body_half_extent_rule": "Measured IFC profile. Rectangular profiles use max(width, height) / 2 without applying the duct orientation.",
        "span_bounds_m": list(prepared["span_bounds_m"]),
        "roi_margin_m": prepared["roi_margin_m"],
        "roi_bounds_m": list(occupancy["roi_bounds_m"]),
        "voxel_size_m": VOXEL_SIZE_M,
        "opening_policy": occupancy["opening_policy"],
        "obstacle_policy": "REPLACED_HOSTS_ONLY_EXCLUDED",
        "clearance_policy": CLEARANCE_POLICY,
        "straight_line_m": record.get("straight_line_m"),
        "approximate_obstacle_count": record.get("approximate_obstacle_count"),
        "_preview_identity": {
            "discipline": record["discipline"],
            "replaced_host_global_ids": list(prepared["candidate"]["replaced_host_global_ids"]),
        },
    }


def _attach_body(evidence: dict[str, Any], request: dict[str, Any]) -> None:
    frozen_ids = evidence.get("replaced_host_global_ids") or []
    if frozenset(frozen_ids) != frozenset(request["replaced_host_global_ids"]):
        return
    evidence["route_body"] = {
        "body_half_extent_m": request["body_half_extent_m"],
        "bodies": request["bodies"],
        "cross_section_rule": request["body_half_extent_rule"],
    }


def _assert_endpoints(request: dict[str, Any], record: dict[str, Any], *, compare_indices: bool = True) -> None:
    if "_prepared" not in record:
        return
    prepared = record["_prepared"]
    if not same_point(prepared["start_interface"]["world_m"], request["start_m"]):
        raise RuntimeError(f"{request['request_id']} start endpoint moved.")
    if not same_point(prepared["end_interface"]["world_m"], request["goal_m"]):
        raise RuntimeError(f"{request['request_id']} goal endpoint moved.")
    if not compare_indices:
        return
    current = _indices(record)
    stored = request.get("containing_indices")
    if stored is None:
        request["containing_indices"] = current
        return
    if current != stored:
        raise RuntimeError(f"{request['request_id']} containing cell changed without a voxel-size change.")


def _indices(record: dict[str, Any]) -> list[list[int] | None]:
    indices = []
    for item in record.get("_terminals") or []:
        index = item.get("containing_index")
        indices.append(None if index is None else [int(value) for value in index])
    return indices


def _freeze_baseline(output_dir: Path) -> dict[str, Any]:
    dest = output_dir / "historical_phase11a2"
    dest.mkdir(parents=True, exist_ok=True)
    label_path = dest / "LABEL.json"
    if label_path.exists():
        return _read_json(label_path)
    copied = []
    for name in FROZEN_FILES:
        source = output_dir / name
        if not source.exists():
            continue
        shutil.copy2(source, dest / name)
        copied.append({"file": name, "sha256": _sha256(source)})
    payload = {
        "label": "Frozen Phase 11A.2 ventilation route and validation. Later Phase 11B reruns do not replace these files.",
        "files": copied,
    }
    label_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _write_outputs(output_dir: Path, payload: dict[str, Any], evidence: dict[str, Any]) -> None:
    public_requests = []
    for item in payload["requests"]:
        public = {key: value for key, value in item.items() if not key.startswith("_")}
        public_requests.append(public)
        item.pop("_preview", None)
    request_doc = {
        "selection_rule": (
            "Per discipline, skip the rejected H_RL 68 identity, then rank by straight-line length in [2 m, 8 m], "
            "shared system and storey, surrounding obstacle count, slab-AABB avoidance, distance to 5 m, and GlobalId. "
            "The frozen ZUL_R 2 ventilation identity is preferred first. Endpoints are never relocated."
        ),
        "target": {"heating": 2, "sanitary": 2, "ventilation": 1},
        "requests": public_requests,
        "examined_not_qualified": payload["examined_not_qualified"],
    }
    results = {key: value for key, value in payload.items() if key != "requests"}
    results["requests"] = public_requests
    write_json(output_dir / "phase11b_requests.json", _ready(request_doc))
    write_json(output_dir / "phase11b_results.json", _ready(results))
    write_json(output_dir / "phase11b_clearance_evidence.json", _ready(evidence))
    _write_csv(output_dir / "phase11b_results.csv", payload["cases"])
    report = render_report(results, evidence)
    (output_dir.parent.parent / "docs" / "PHASE_11B_REAL_IFC_CLEARANCE_BENCHMARK.md").write_text(report, encoding="utf-8")


def _output_paths(output_dir: Path) -> dict[str, str]:
    root = output_dir.parent.parent
    return {
        "requests": str(output_dir / "phase11b_requests.json"),
        "results": str(output_dir / "phase11b_results.json"),
        "csv": str(output_dir / "phase11b_results.csv"),
        "evidence": str(output_dir / "phase11b_clearance_evidence.json"),
        "report": str(root / "docs" / "PHASE_11B_REAL_IFC_CLEARANCE_BENCHMARK.md"),
    }


def _write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for case in cases:
            memory = case.get("process_memory") or {}
            row = {field: case.get(field) for field in CSV_FIELDS}
            row["system_names"] = "|".join(case.get("system_names") or [])
            row["storeys"] = "|".join(case.get("storeys") or [])
            row["process_peak_working_set"] = memory.get("peak_working_set_bytes")
            writer.writerow(row)


def render_report(results: dict[str, Any], evidence: dict[str, Any]) -> str:
    counts = results["counts"]
    lines = [
        "# Phase 11B: Real-IFC clearance benchmark",
        "",
        "Five DigitalHub V1 replacement requests were executed with the frozen orthogonal 3D A*. "
        "A path returned by A* is a search result. It is a physically acceptable route only when the "
        "reconstructed body, including connector stubs, meets the declared separation from retained IFC meshes.",
        "",
        "## Frozen Phase 11A.2 baseline",
        "",
        f"- System: {', '.join(evidence.get('system_names') or [])} on {', '.join(evidence.get('storeys') or [])}.",
        f"- Endpoint class: {', '.join(evidence.get('endpoint_evidence_class') or [])}. This is not an exact native port placement.",
        f"- Start `{evidence.get('start_m')}` m, goal `{evidence.get('goal_m')}` m.",
        f"- Search `{evidence.get('search_state')}`, geometric result `{evidence.get('geometric_state')}`.",
        f"- Physical length {evidence.get('physical_route_length_m')} m, vertical travel {evidence.get('physical_vertical_travel_m')} m, bends {evidence.get('physical_bend_count')}.",
        f"- A* expansions {evidence.get('expanded_node_count')}, runtime {evidence.get('runtime_seconds')} s.",
        "",
        "The violation was not the synthetic benchmark's 50 mm inter-system rule. "
        "The validator compared mesh surface distance of centreline samples with the measured body half-extent. "
        "Additional clearance was 0. A sample that is not enclosed by the mesh can still violate that body envelope.",
        "",
        "## Clearance evidence",
        "",
        "| GlobalId | Role | Boundary host | Mesh distance | Required separation | Inside allowance | Outside allowance |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in evidence.get("offenders") or []:
        lines.append(
            f"| `{item['global_id']}` | {item['file_role']} | {item['is_retained_boundary_host']} | "
            f"{_num(item.get('reported_min_centerline_distance_m'))} | {_num(item.get('required_separation_m'))} | "
            f"{item.get('inside_allowance_state') or ''} | {item.get('outside_allowance_state') or ''} |"
        )
    body = (evidence.get("route_body") or {}).get("bodies") or []
    lines.extend(["", "Route-body profiles:", ""])
    if not body:
        lines.append("Profiles are recorded on the executed ventilation request.")
    for item in body:
        lines.append(f"- `{item.get('global_id')}` {item.get('ifc_class')} source `{item.get('source')}` half-extent {_num(item.get('half_extent_m'))} m.")
    lines.extend([
        "",
        "No DigitalHub building-code clearance is defined in this project. "
        "Sensitivity cases use additional margins of 0, 25, 50, and 100 mm and are not a code-compliance claim. "
        "The 0.05 m `BENCHMARK_V1` constant remains an inter-system synthetic value. Same-system clearance is undefined.",
        "",
        "A 0.1 m grid can miss narrow geometry. Occupancy looks at cell centres. "
        "The mesh check of the full polyline is what supports or refuses a physical-acceptability claim. "
        "Zero swept-AABB hits stay `GEOMETRIC_VALIDATION_INCOMPLETE`.",
        "",
        "## Original and clearance-aware results",
        "",
        "| Request | Discipline | System | Original search | Original geometry | Aware 0 mm | Aware 25 mm | Aware 50 mm | Aware 100 mm |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    by_request: dict[str, list[dict[str, Any]]] = {}
    for case in results.get("cases") or []:
        by_request.setdefault(case["request_id"], []).append(case)
    for request in results.get("requests") or []:
        rows = by_request.get(request["request_id"], [])
        original = next((item for item in rows if item["mode"] == BASELINE_MODE), None)
        aware = {
            item["additional_clearance_m"]: item
            for item in rows if item["mode"] == CLEARANCE_AWARE_MODE
        }
        lines.append(
            "| {id} | {discipline} | {system} | {search} | {geom} | {a0} | {a25} | {a50} | {a100} |".format(
                id=request["request_id"],
                discipline=request["discipline"],
                system=", ".join(request.get("system_names") or []),
                search=_pair(original, "search_state"),
                geom=_pair(original, "geometric_state"),
                a0=_aware(aware.get(0.0)),
                a25=_aware(aware.get(0.025)),
                a50=_aware(aware.get(0.05)),
                a100=_aware(aware.get(0.1)),
            )
        )
    lines.extend(["", "## Request notes", ""])
    for request in results.get("requests") or []:
        rows = by_request.get(request["request_id"], [])
        original = next((item for item in rows if item["mode"] == BASELINE_MODE), {})
        lines.append(
            f"- `{request['request_id']}` {request['discipline']} {', '.join(request.get('system_names') or [])} "
            f"/ {', '.join(request.get('storeys') or [])}. "
            f"Class `{', '.join(request.get('endpoint_evidence_class') or [])}`. "
            f"Body half-extent {_num(request.get('body_half_extent_m'))} m. "
            f"Length {_num(original.get('physical_route_length_m'))} m, "
            f"vertical {_num(original.get('physical_vertical_travel_m'))} m, "
            f"bends {original.get('physical_bend_count')}, "
            f"expansions {original.get('expanded_node_count')}, "
            f"runtime {_num(original.get('runtime_seconds'))} s."
        )
        finer = next((item for item in rows if item["mode"] == FINER_GRID_MODE), None)
        if finer is not None:
            lines.append(
                f"  0.05 m sensitivity: {finer.get('search_state')} / {finer.get('geometric_state') or 'NONE'}. "
                f"{finer.get('note') or ''}"
            )
    shortfalls = [item for item in results.get("examined_not_qualified") or [] if item.get("shortfall")]
    if shortfalls:
        lines.extend(["", "## Qualification shortfalls", ""])
        for item in shortfalls:
            lines.append(
                f"- {item['discipline']}: qualified {item['qualified']} of {item['target']} after examining {item['examined']}."
            )
    multi = results.get("multi_system") or {}
    lines.extend([
        "",
        "## Multi-system coordination",
        "",
        f"Status: `{multi.get('status')}`.",
        "",
        multi.get("reason") or multi.get("clearance_policy") or "",
        "",
        "Independent searches do not prove that the routes can coexist. "
        "B1, B2, and P-CORE were not run because those implementations require the synthetic benchmark anchors.",
        "",
        "## Counts",
        "",
        f"- `REAL_IFC_REQUESTS_QUALIFIED:` {counts['REAL_IFC_REQUESTS_QUALIFIED']}",
        f"- `REAL_IFC_REQUESTS_EXECUTED:` {counts['REAL_IFC_REQUESTS_EXECUTED']}",
        f"- `ASTAR_PATHS_FOUND:` {counts['ASTAR_PATHS_FOUND']}",
        f"- `PHYSICALLY_ACCEPTABLE_ROUTES:` {counts['PHYSICALLY_ACCEPTABLE_ROUTES']}",
        f"- `CLEARANCE_VIOLATIONS:` {counts['CLEARANCE_VIOLATIONS']}",
        f"- `CONFIRMED_GEOMETRIC_COLLISIONS:` {counts['CONFIRMED_GEOMETRIC_COLLISIONS']}",
        f"- `VALIDATION_INCOMPLETE:` {counts['VALIDATION_INCOMPLETE']}",
        f"- `MULTI_SYSTEM_COORDINATION_EXECUTED:` {counts['MULTI_SYSTEM_COORDINATION_EXECUTED']}",
        "",
        "`TESTS_PASSED` is filled after the automated suite.",
        "",
        "Replaced hosts stay excluded. Boundary-host contact is limited to the interface cell. "
        "The frozen 292-request synthetic benchmark was not changed.",
        "",
    ])
    return "\n".join(lines) + "\n"


def _counts(requests, cases, multi) -> dict[str, Any]:
    return {
        "REAL_IFC_REQUESTS_QUALIFIED": len(requests),
        "REAL_IFC_REQUESTS_EXECUTED": len(requests),
        "ASTAR_PATHS_FOUND": sum(1 for case in cases if case.get("path_found")),
        "PHYSICALLY_ACCEPTABLE_ROUTES": sum(1 for case in cases if case.get("geometric_state") == PHYSICALLY_ACCEPTABLE),
        "CLEARANCE_VIOLATIONS": sum(1 for case in cases if case.get("geometric_state") == CLEARANCE_VIOLATION),
        "CONFIRMED_GEOMETRIC_COLLISIONS": sum(1 for case in cases if case.get("geometric_state") == CONFIRMED_GEOMETRIC_COLLISION),
        "VALIDATION_INCOMPLETE": sum(1 for case in cases if case.get("geometric_state") == GEOMETRIC_VALIDATION_INCOMPLETE),
        "MULTI_SYSTEM_COORDINATION_EXECUTED": "YES" if multi.get("executed") else "NO",
    }


def _pair(case: dict[str, Any] | None, key: str) -> str:
    if not case:
        return "NOT_RUN"
    value = case.get(key)
    return "NONE" if value is None else str(value)


def _aware(case: dict[str, Any] | None) -> str:
    if case is None:
        return "NOT_RUN"
    return f"{case.get('search_state')} / {case.get('geometric_state') or 'NONE'}"


def _num(value) -> str:
    if value is None:
        return "NONE"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _evidence_request(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "discipline": request["discipline"],
        "system_names": request["system_names"],
        "storeys": request["storeys"],
        "endpoint_evidence_class": request["endpoint_evidence_class"],
        "start_m": request["start_m"],
        "goal_m": request["goal_m"],
        "replaced_host_global_ids": request["replaced_host_global_ids"],
        "boundary_host_global_ids": request["boundary_host_global_ids"],
        "body_half_extent_m": request["body_half_extent_m"],
        "bodies": request["bodies"],
        "roi_bounds_m": request["roi_bounds_m"],
        "voxel_size_m": request["voxel_size_m"],
    }


def _evidence_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": case["request_id"],
        "mode": case["mode"],
        "additional_clearance_m": case.get("additional_clearance_m"),
        "voxel_size_m": case.get("voxel_size_m"),
        "search_state": case.get("search_state"),
        "geometric_state": case.get("geometric_state"),
        "validation": case.get("collision_validation"),
        "mesh_refinement_fallback_count": case.get("mesh_refinement_fallback_count"),
    }


def _short_reject(preview, record) -> dict[str, Any]:
    qualifications = record.get("endpoint_qualification") or []
    return {
        "discipline": preview["discipline"],
        "system_names": preview.get("system_names"),
        "storeys": preview.get("storeys"),
        "straight_line_m": preview.get("straight_line_m"),
        "replaced_host_global_ids": preview["replaced_host_global_ids"],
        "boundary_host_global_ids": preview["boundary_host_global_ids"],
        "qualified": False,
        "rejection_reason": record.get("rejection_reason"),
        "endpoint_status": [item.get("status") for item in qualifications],
        "obstruction_global_ids": [item.get("obstruction_global_id") for item in qualifications if item.get("obstruction_global_id")],
    }


def _identity(record) -> dict[str, Any]:
    return {
        "discipline": record.get("discipline"),
        "system_names": record.get("system_names"),
        "storeys": record.get("storeys"),
    }


def _world(record, role: str) -> list[float] | None:
    prepared = record.get("_prepared") or {}
    interface = prepared.get("start_interface" if role == "start" else "end_interface")
    if not interface:
        return None
    return list(interface["world_m"])


def _index(terminals, position: int):
    if len(terminals) <= position:
        return None
    return terminals[position].get("containing_index")


def _roi_summary(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "discipline": request["discipline"],
        "system_names": request["system_names"],
        "roi_bounds_m": request.get("roi_bounds_m"),
    }


def _release(record: dict[str, Any]) -> None:
    record.pop("_occupancy", None)
    prepared = record.get("_prepared")
    if isinstance(prepared, dict):
        prepared.pop("meshes", None)


def _replaced_ids(path: Path) -> tuple[str, ...] | None:
    if not path.exists():
        return None
    payload = _read_json(path)
    values = payload.get("replaced_host_global_ids")
    return None if not values else tuple(values)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ready(value):
    if isinstance(value, dict):
        return {str(key): _ready(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_ready(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    return str(value)

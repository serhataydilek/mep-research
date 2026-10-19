"""Independent Phase 10R-B two-storey grid benchmark adapter.

This module deliberately does not enter the frozen 12-case input path.  It
uses the unchanged deterministic A* and gravity primitives over a separately
versioned, explicit world-coordinate fixture.  Its validator is a grid and
swept-AABB proxy check, never physical or engineering certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .clash import positive_aabb_overlap
from .constructability import drainage_gravity_metrics
from .demand import SYSTEM_ORDER, load_service_definitions, service_routing_margins
from .gravity_routing import gravity_route
from .route_geometry import compress_path_cells, swept_segment_aabb
from .routing import astar_3d, route_metrics
from .voxel import BLOCKED_FIXED, FREE, GridSpec, OccupancyGrid, aabb_index_ranges, snap_anchor_to_free_cell


ROOT = Path(__file__).resolve().parents[1]
FORMAT = "PHASE10R_B_MULTISTOREY_V1"
FIXTURE_FILES = {"10rb-a-known-feasible": "fixture_a_known_feasible.json", "10rb-b-constrained": "fixture_b_constrained.json"}
COLORS = {"hvac": "#ff9f1c", "drainage": "#b08968", "water": "#00b4d8", "fire": "#d62828", "electrical": "#9b5de5"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _round(value: float) -> float:
    value = round(float(value), 9)
    return 0.0 if value == -0.0 else value


def _box(bounds: list[float] | tuple[float, ...]) -> dict[str, float]:
    return {key: _round(value) for key, value in zip(("min_x_m", "max_x_m", "min_y_m", "max_y_m", "min_z_m", "max_z_m"), bounds)}


def _inside(point: tuple[float, float, float], bounds: list[float] | tuple[float, ...]) -> bool:
    return all(bounds[axis * 2] <= point[axis] <= bounds[axis * 2 + 1] for axis in range(3))


def load_fixture(path: Path) -> dict[str, Any]:
    """Load a fixture, resolving the explicit local inheritance used by B."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("fixture_id"), str):
        raise ValueError("Phase 10R-B fixture schema is invalid.")
    if "inherits" in data:
        parent = load_fixture(path.parent / data["inherits"])
        merged = deepcopy(parent)
        merged.update({key: value for key, value in data.items() if key not in {"inherits", "elements_append"}})
        merged["elements"] = parent["elements"] + data.get("elements_append", [])
        data = merged
    _validate_fixture(data)
    return data


def _fixture_hashes(path: Path) -> dict[str, str]:
    """Hash every explicit fixture source participating in an inherited case."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}
    if raw.get("inherits"):
        hashes.update(_fixture_hashes(path.parent / raw["inherits"]))
    return dict(sorted(hashes.items()))


def _validate_fixture(data: dict[str, Any]) -> None:
    grid = data.get("grid", {})
    required = ("voxel_size_m", "width_m", "length_m", "height_m", "clearance_m")
    if any(not isinstance(grid.get(key), (int, float)) or grid[key] <= 0 for key in required):
        raise ValueError("Multistorey grid dimensions must be positive numbers.")
    size = float(grid["voxel_size_m"])
    if any(abs(float(grid[key]) / size - round(float(grid[key]) / size)) > 1e-9 for key in ("width_m", "length_m", "height_m")):
        raise ValueError("Multistorey dimensions must be voxel multiples.")
    if len(data.get("storeys", [])) != 2 or not data.get("shaft") or not data.get("elements") or not data.get("spaces"):
        raise ValueError("Fixture requires two storeys, one shaft, spaces, and architectural geometry.")
    ids = [item["id"] for item in data.get("endpoints", [])]
    if len(ids) != 10 or len(set(ids)) != len(ids):
        raise ValueError("Fixture requires ten uniquely identified endpoints.")
    connections = data.get("connections", [])
    if {item.get("system") for item in connections} != set(SYSTEM_ORDER) or len(connections) != 5:
        raise ValueError("Fixture requires exactly one connection for every existing system.")
    endpoint_ids = set(ids)
    if any(item.get("start") not in endpoint_ids or item.get("end") not in endpoint_ids for item in connections):
        raise ValueError("Connection references an unknown endpoint.")


def _grid_spec(data: dict[str, Any]) -> GridSpec:
    grid = data["grid"]; size = float(grid["voxel_size_m"])
    return GridSpec(size, 0.0, 0.0, 0.0, round(grid["width_m"] / size), round(grid["length_m"] / size), round(grid["height_m"] / size))


def _mark(grid: OccupancyGrid, bounds: list[float] | tuple[float, ...]) -> None:
    for k in aabb_index_ranges(grid.spec, tuple(bounds))[2]:
        for j in aabb_index_ranges(grid.spec, tuple(bounds))[1]:
            for i in aabb_index_ranges(grid.spec, tuple(bounds))[0]:
                grid.cells[grid.spec.linear_index(i, j, k)] = BLOCKED_FIXED


def build_multistorey_grid(data: dict[str, Any], system: str, definition: dict[str, Any]) -> OccupancyGrid:
    """Build an allowed-zone grid with a continuous, explicitly opened shaft."""
    spec = _grid_spec(data)
    grid = OccupancyGrid(data["fixture_id"], system, spec, bytearray([BLOCKED_FIXED]) * spec.total_cell_count, tuple(data["shaft"]["bounds"]), {})
    allowed = [item["service_zone"] for item in data["storeys"]] + [data["shaft"]["bounds"]]
    for k in range(spec.nz):
        for j in range(spec.ny):
            for i in range(spec.nx):
                if any(_inside(spec.cell_center(i, j, k), bounds) for bounds in allowed):
                    grid.cells[spec.linear_index(i, j, k)] = FREE
    margin = service_routing_margins(definition, float(data["grid"]["clearance_m"]))
    for element in data["elements"]:
        box = list(element["bounds"])
        inflated = (box[0] - margin["required_planar_margin_m"], box[1] + margin["required_planar_margin_m"], box[2] - margin["required_planar_margin_m"], box[3] + margin["required_planar_margin_m"], box[4] - margin["required_vertical_margin_m"], box[5] + margin["required_vertical_margin_m"])
        _mark(grid, inflated)
    return grid


def _route(data: dict[str, Any], definitions: dict[str, dict[str, Any]], connection: dict[str, Any]) -> dict[str, Any]:
    system = connection["system"]; endpoints = {item["id"]: item for item in data["endpoints"]}; grid = build_multistorey_grid(data, system, definitions[system])
    start, end = endpoints[connection["start"]], endpoints[connection["end"]]
    if system == "drainage":
        chosen, reason = gravity_route(grid, {"anchor_id": start["id"], "x_m": start["world_m"][0], "y_m": start["world_m"][1], "z_m": start["world_m"][2]}, {"anchor_id": end["id"], "x_m": end["world_m"][0], "y_m": end["world_m"][1], "z_m": end["world_m"][2]}, .01)
        if chosen is None:
            return {"physical_edge_id": connection["id"], "system": system, "status": "FAILED", "reason": reason, "world_polyline_m": [], "grid_cells": [], "grid_cell_count": 0, "connection_ids": [connection["id"]], "role": "DIRECT", "clearance_envelopes": []}
        _, _, _, _, _, _, snapped_start, snapped_end, result, gravity, metrics = chosen
    else:
        snapped_start = snap_anchor_to_free_cell(grid, {"anchor_id": start["id"], "x_m": start["world_m"][0], "y_m": start["world_m"][1], "z_m": start["world_m"][2]})
        snapped_end = snap_anchor_to_free_cell(grid, {"anchor_id": end["id"], "x_m": end["world_m"][0], "y_m": end["world_m"][1], "z_m": end["world_m"][2]})
        result = astar_3d(grid, tuple(snapped_start["grid_index"]), tuple(snapped_end["grid_index"])); metrics = route_metrics(result, grid.spec.voxel_size_m); gravity = None
        if not result.path_found:
            return {"physical_edge_id": connection["id"], "system": system, "status": "FAILED", "reason": "SEARCH_NOT_FOUND", "world_polyline_m": [], "grid_cells": [], "grid_cell_count": 0, "connection_ids": [connection["id"]], "role": "DIRECT", "clearance_envelopes": []}
    cells = [list(cell) for cell in result.path_cells]; polyline = [[_round(value) for value in point] for point in [grid.spec.cell_center(*cells[0]), *[grid.spec.cell_center(*cell) for cell in cells[1:]]]]
    margin = service_routing_margins(definitions[system], float(data["grid"]["clearance_m"])); envelopes = [_box(swept_segment_aabb(segment, margin["planar_half_extent_m"], margin["vertical_half_extent_m"], .025)) for segment in compress_path_cells(cells, grid.spec)]
    return {"physical_edge_id": connection["id"], "system": system, "status": "ROUTED", "reason": None, "role": "DIRECT", "connection_ids": [connection["id"]], "grid_cells": cells, "grid_cell_count": len(cells), "world_polyline_m": polyline, "grid_route_length_m": metrics["grid_route_length_m"], "vertical_travel_m": metrics["vertical_travel_m"], "expanded_node_count": metrics["expanded_node_count"], "start": snapped_start, "end": snapped_end, "gravity": gravity, "clearance_envelopes": envelopes}


def _validate(data: dict[str, Any], edges: list[dict[str, Any]], definitions: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use existing cell continuity, gravity metric, and swept-AABB overlap semantics."""
    failures, conflicts = [], []
    for edge in edges:
        if edge["status"] != "ROUTED": failures.append({"edge_id": edge["physical_edge_id"], "classification": "SEARCH_FAILURE", "reason": edge["reason"]}); continue
        cells = edge["grid_cells"]
        if not all(sum(abs(a - b) for a, b in zip(one, two)) == 1 for one, two in zip(cells, cells[1:])):
            failures.append({"edge_id": edge["physical_edge_id"], "classification": "VALIDATION_REJECTION", "reason": "PATH_DISCONTINUITY"})
        if edge["system"] == "drainage":
            gravity = drainage_gravity_metrics({"path_found": True, "path_cells": cells}, float(data["grid"]["voxel_size_m"]), .01)
            if not gravity["drainage_gravity_pass"]: failures.append({"edge_id": edge["physical_edge_id"], "classification": "VALIDATION_REJECTION", "reason": "DRAINAGE_GRAVITY", "gravity": gravity})
    routed = [edge for edge in edges if edge["status"] == "ROUTED"]
    for index, first in enumerate(routed):
        for second in routed[index + 1:]:
            if first["system"] == second["system"]: continue
            hard = [a for a in first["clearance_envelopes"] for b in second["clearance_envelopes"] if positive_aabb_overlap(tuple(a.values()), tuple(b.values()))]
            shared = sorted(set(map(tuple, first["grid_cells"])) & set(map(tuple, second["grid_cells"])))
            if hard or shared:
                conflicts.append({"kind": "INTER_SYSTEM", "severity": "SHARED_CELL" if shared else "HARD_ENVELOPE", "physical_edge_ids": [first["physical_edge_id"], second["physical_edge_id"]], "systems": [first["system"], second["system"]], "shared_cell_count": len(shared), "shared_cells_world_m": [], "hard_segment_pair_count": len(hard), "clearance_segment_pair_count": len(hard), "hard_overlap_regions_shown": hard[:10], "hard_overlap_regions_total": len(hard)})
    return failures, conflicts


def run_fixture(path: Path, systems_path: Path = ROOT / "config/mep_systems.json") -> dict[str, Any]:
    data = load_fixture(path); definitions = load_service_definitions(systems_path); edges = [_route(data, definitions, item) for item in data["connections"]]; failures, conflicts = _validate(data, edges, definitions)
    endpoint_ids = {item["id"]: item for item in data["endpoints"]}; connections = [{"connection_id": item["id"], "system": item["system"], "status": next(edge["status"] for edge in edges if edge["physical_edge_id"] == item["id"]), "physical_edge_ids": [item["id"]], "strategy": "B0_OR_GRAVITY", "reason": next(edge["reason"] for edge in edges if edge["physical_edge_id"] == item["id"]), "unvalidated_requirements": ["CONTINUOUS_SOLID_CERTIFICATION_UNAVAILABLE"]} for item in data["connections"]]
    status = "VALIDATED_GRID_AABB_PROXY" if not failures and not conflicts else "CONSTRAINED_CONFLICTS_DETECTED" if conflicts else "VALIDATION_REJECTED"
    z_occupancy = {edge["physical_edge_id"]: {str(cell[2]): sum(item[2] == cell[2] for item in edge["grid_cells"]) for cell in {tuple(value) for value in edge["grid_cells"]}} for edge in edges if edge["status"] == "ROUTED"}
    return {"schema_version": 3, "kind": "MULTISTOREY_MEP_DEBUG", "debug_export_version": FORMAT, "fixture_id": data["fixture_id"], "description": data["description"], "units": "m", "coordinate_frame": "synthetic/world/metres/phase10rb", "geometric_clash_certified": False, "collision_free_claimed": False, "connector_engagement_validated": False, "certification_warning": "Grid-cell and swept-AABB proxy validation only; not physical feasibility, infeasibility, or engineering certification.", "systems": list(SYSTEM_ORDER), "obstacles": [{"global_id": item["id"], "ifc_class": item["kind"], "bounds": _box(item["bounds"])} for item in data["elements"]], "openings": [{**item, "bounds": _box(item["bounds"])} for item in data["openings"]], "spaces": [{**item, "bounds": _box(item["bounds"])} for item in data["spaces"]], "shaft": {"shaft_id": data["shaft"]["shaft_id"], "bounds": _box(data["shaft"]["bounds"])}, "storeys": data["storeys"], "endpoints": [{"endpoint_id": item["id"], "system": item["system"], "world_m": item["world_m"]} for item in endpoint_ids.values()], "junctions": [], "edges": edges, "connections": connections, "conflicts": conflicts, "validation": {"status": status, "failures": failures, "hard_conflict_count": len(conflicts), "clearance_violation_count": len(conflicts), "not_physical_certification": True}, "z_occupancy": z_occupancy, "coordinate_mapping": {"kind": "VOXEL_CELL_CENTER_TO_WORLD", "origin_m": [0, 0, 0], "voxel_size_m": data["grid"]["voxel_size_m"], "dimensions_cells": [_grid_spec(data).nx, _grid_spec(data).ny, _grid_spec(data).nz]}, "source_identity": {"fixture_sha256": _fixture_hashes(path), "systems_sha256": hashlib.sha256(systems_path.read_bytes()).hexdigest()}}


def write_fixture(path: Path, output: Path, systems_path: Path = ROOT / "config/mep_systems.json") -> dict[str, Any]:
    result = run_fixture(path, systems_path); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(_canonical(result), encoding="utf-8"); return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the independent Phase 10R-B multistorey fixtures.")
    parser.add_argument("--fixture", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--systems", type=Path, default=ROOT / "config/mep_systems.json")
    args = parser.parse_args(); result = write_fixture(args.fixture, args.output, args.systems)
    print(f"{result['fixture_id']}: {sum(edge['status']=='ROUTED' for edge in result['edges'])}/{len(result['edges'])} routed; {result['validation']['status']}; hard proxy conflicts {result['validation']['hard_conflict_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

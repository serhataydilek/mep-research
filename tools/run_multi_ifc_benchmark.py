"""Run the read-only multi-model real-IFC routing benchmark.

This adapter deliberately keeps external IFCs immutable.  It reuses the
project's deterministic six-neighbour A* implementation, but derives an
occupancy grid directly from tessellated real structural IFC geometry instead
of pretending that the project's synthetic shaft convention exists in a
third-party model.  It is a research runner, not an IFC installation writer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import tracemalloc
import sys
from collections import deque
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.unit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.routing import astar_3d, route_metrics
from src.voxel import FREE, GridSpec, OccupancyGrid, aabb_index_ranges


EXTERNAL = ROOT / "validation" / "ifc_samples" / "external"
OUTPUT = ROOT / "out" / "multi_ifc_benchmark"
VOXEL_M = 1.0
MAX_CELLS = 3_000_000
STRUCTURAL_CLASSES = ("IfcWall", "IfcColumn", "IfcBeam", "IfcSlab")
GEOMETRY_MODE = "REAL_IFC_GEOMETRY_SYNTHETIC_ENDPOINTS"
NATIVE_MODE = "REAL_IFC_NATIVE_CONNECTIVITY_EXPERIMENTAL"

MODELS = (
    ("digital_hub", EXTERNAL / "medium/digital_hub/arc.ifc", EXTERNAL / "medium/digital_hub/ventilation.ifc"),
    ("duplex", EXTERNAL / "medium/duplex/arc.ifc", EXTERNAL / "medium/duplex/mep.ifc"),
    ("wbdg_office", EXTERNAL / "medium/wbdg_office/arc.ifc", EXTERNAL / "medium/wbdg_office/mep.ifc"),
)


def source_record(path: Path) -> dict[str, Any]:
    registry = json.loads((EXTERNAL / "download_manifest.json").read_text(encoding="utf-8"))
    relative = path.resolve().relative_to(EXTERNAL.resolve()).as_posix()
    return next((item for item in registry.get("files", []) if item.get("path") == relative),
                {"source_url": None, "discipline": "UNREGISTERED_LOCAL_CANDIDATE"})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bounds(vertices: tuple[float, ...], scale: float) -> tuple[float, float, float, float, float, float]:
    xs, ys, zs = vertices[::3], vertices[1::3], vertices[2::3]
    return (min(xs) * scale, max(xs) * scale, min(ys) * scale, max(ys) * scale, min(zs) * scale, max(zs) * scale)


def geometry(path: Path) -> tuple[dict[str, Any], list[tuple[tuple[float, ...], tuple[int, ...], tuple[float, float, float, float, float, float]]], list[tuple[float, float, float, float, float, float]]]:
    """Extract world-coordinate structural meshes and AABBs from one IFC."""
    model = ifcopenshell.open(str(path))
    scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    meshes: list[tuple[tuple[float, ...], tuple[int, ...], tuple[float, float, float, float, float, float]]] = []
    boxes: list[tuple[float, float, float, float, float, float]] = []
    class_counts: dict[str, int] = {}
    failures = 0
    for class_name in STRUCTURAL_CLASSES:
        items = sorted(model.by_type(class_name), key=lambda item: item.GlobalId)
        class_counts[class_name] = len(items)
        for item in items:
            if not item.Representation:
                continue
            try:
                shape = ifcopenshell.geom.create_shape(settings, item)
                verts = tuple(float(value) * scale for value in shape.geometry.verts)
                faces = tuple(int(value) for value in shape.geometry.faces)
                if not verts or not faces:
                    raise ValueError("empty tessellation")
                box = bounds(tuple(shape.geometry.verts), scale)
                meshes.append((verts, faces, box))
                boxes.append(box)
            except Exception:
                failures += 1
    return ({"schema": str(model.schema), "metre_scale": scale, "class_counts": class_counts,
             "mesh_count": len(meshes), "geometry_extraction_failures": failures}, meshes, boxes)


def make_grid(model_id: str, boxes: list[tuple[float, float, float, float, float, float]]) -> tuple[OccupancyGrid | None, dict[str, Any]]:
    if not boxes:
        return None, {"status": "BLOCKED", "reason": "No usable wall/column/beam/slab geometry."}
    low = [min(box[i] for box in boxes) for i in (0, 2, 4)]
    high = [max(box[i] for box in boxes) for i in (1, 3, 5)]
    # One metre padding permits endpoints inside the observed building envelope,
    # not outside a geometry-less infinite domain.
    origin = tuple(math.floor((value - VOXEL_M) / VOXEL_M) * VOXEL_M for value in low)
    dims = tuple(max(1, math.ceil((high[i] + VOXEL_M - origin[i]) / VOXEL_M)) for i in range(3))
    total = math.prod(dims)
    summary = {"voxel_resolution_m": VOXEL_M, "grid_origin_m": list(origin),
               "grid_dimensions": {"nx": dims[0], "ny": dims[1], "nz": dims[2]}, "total_voxel_count": total}
    if total > MAX_CELLS:
        return None, {**summary, "status": "BLOCKED", "reason": f"Grid needs {total} cells; safety limit is {MAX_CELLS}."}
    spec = GridSpec(VOXEL_M, origin[0], origin[1], origin[2], dims[0], dims[1], dims[2])
    grid = OccupancyGrid(model_id, "hvac", spec, bytearray(total), (0, 0, 0, 0, 0, 0), {"real_structural_aabbs": len(boxes)})
    for box in boxes:
        for k in aabb_index_ranges(spec, box)[2]:
            for j in aabb_index_ranges(spec, box)[1]:
                for i in aabb_index_ranges(spec, box)[0]:
                    grid.cells[spec.linear_index(i, j, k)] = 1
    return grid, {**summary, "status": "READY", "free_cell_count": grid.cells.count(FREE), "blocked_cell_count": grid.cells.count(1),
                  "obstacle_semantics": "Actual tessellated structural IFC products rasterized as conservative world AABBs."}


def component_endpoints(grid: OccupancyGrid) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]] | None:
    """Choose deterministic synthetic source, junction, and two terminals from one free component."""
    spec = grid.spec
    free = [index for index, state in enumerate(grid.cells) if state == FREE]
    if len(free) < 4:
        return None
    start = spec.index_from_linear(min(free))
    seen = {start}
    queue = deque([start])
    while queue:
        i, j, k = queue.popleft()
        for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            candidate = (i + di, j + dj, k + dk)
            try:
                if candidate not in seen and grid.state_at(*candidate) == FREE:
                    seen.add(candidate); queue.append(candidate)
            except ValueError:
                pass
    if len(seen) < 4:
        return None
    ordered = sorted(seen)
    source = ordered[0]
    terminal_a = max(ordered, key=lambda item: (item[0], item[1], item[2]))
    terminal_b = max(ordered, key=lambda item: (item[1], item[0], item[2]))
    junction = min(ordered, key=lambda item: (sum((item[n] - (source[n] + terminal_a[n] + terminal_b[n]) / 3) ** 2 for n in range(3)), item))
    if len({source, junction, terminal_a, terminal_b}) < 4:
        choices = [item for item in ordered if item not in {source, terminal_a, terminal_b}]
        junction = choices[len(choices) // 2]
    return source, junction, terminal_a, terminal_b


def segment_triangle_intersects(p0: tuple[float, float, float], p1: tuple[float, float, float], a: tuple[float, float, float], b: tuple[float, float, float], c: tuple[float, float, float]) -> bool:
    # Moller-Trumbore, bounded to a segment.  Surface contact is a collision.
    direction = tuple(p1[i] - p0[i] for i in range(3)); edge1 = tuple(b[i] - a[i] for i in range(3)); edge2 = tuple(c[i] - a[i] for i in range(3))
    cross = (direction[1]*edge2[2]-direction[2]*edge2[1], direction[2]*edge2[0]-direction[0]*edge2[2], direction[0]*edge2[1]-direction[1]*edge2[0])
    determinant = sum(edge1[i] * cross[i] for i in range(3))
    if abs(determinant) < 1e-10: return False
    inverse = 1.0 / determinant; delta = tuple(p0[i] - a[i] for i in range(3))
    u = inverse * sum(delta[i] * cross[i] for i in range(3))
    if not 0 <= u <= 1: return False
    q = (delta[1]*edge1[2]-delta[2]*edge1[1], delta[2]*edge1[0]-delta[0]*edge1[2], delta[0]*edge1[1]-delta[1]*edge1[0])
    v = inverse * sum(direction[i] * q[i] for i in range(3))
    if v < 0 or u + v > 1: return False
    t = inverse * sum(edge2[i] * q[i] for i in range(3))
    return 0 <= t <= 1


def exact_centerline_collisions(path: tuple[tuple[int, int, int], ...], grid: OccupancyGrid, meshes: list[tuple[tuple[float, ...], tuple[int, ...], tuple[float, float, float, float, float, float]]]) -> int:
    points = [grid.spec.cell_center(*cell) for cell in path]
    hits = 0
    for first, second in zip(points, points[1:]):
        found = False
        seg_box = (min(first[0], second[0]), max(first[0], second[0]), min(first[1], second[1]), max(first[1], second[1]), min(first[2], second[2]), max(first[2], second[2]))
        for verts, faces, mesh_box in meshes:
            if (seg_box[1] < mesh_box[0] or seg_box[0] > mesh_box[1] or seg_box[3] < mesh_box[2] or seg_box[2] > mesh_box[3] or seg_box[5] < mesh_box[4] or seg_box[4] > mesh_box[5]):
                continue
            for index in range(0, len(faces), 3):
                triangle = tuple(tuple(verts[3 * faces[index + offset] + axis] for axis in range(3)) for offset in range(3))
                if segment_triangle_intersects(first, second, *triangle):
                    hits += 1; found = True; break
            if found: break
    return hits


def run_geometry_model(model_id: str, architecture: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    before = sha256(architecture); started = time.perf_counter(); tracemalloc.start()
    geometry_started = time.perf_counter()
    meta, meshes, boxes = geometry(architecture)
    geometry_seconds = time.perf_counter() - geometry_started
    grid_started = time.perf_counter()
    grid, grid_info = make_grid(model_id, boxes)
    grid_seconds = time.perf_counter() - grid_started
    routing_started = time.perf_counter()
    result: dict[str, Any] = {"model_id": model_id, "category": GEOMETRY_MODE, "architecture_ifc": file_record(architecture),
                              "geometry": meta, "grid": grid_info, "endpoint_provenance": "EXPLICIT_SYNTHETIC_EXPERIMENTAL"}
    overlay: dict[str, Any] = {"schema_version": 1, "kind": "REAL_IFC_ROUTING_OVERLAY", "model_id": model_id, "category": GEOMETRY_MODE,
                               "units": "METRE", "original_building_geometry": {"ifc_path": str(architecture), "sha256": before, "rendering": "Load this immutable IFC as the base geometry; route data is an overlay only."},
                               "validation_status": "EXPERIMENTAL_NOT_ENGINEERING_APPROVED", "obstacle_aabbs_m": [list(item) for item in boxes]}
    if grid is None:
        result.update({"status": "BLOCKED", "connections": [], "failure_reasons": [grid_info["reason"]]})
    else:
        endpoints = component_endpoints(grid)
        if endpoints is None:
            result.update({"status": "BLOCKED", "connections": [], "failure_reasons": ["No four endpoints in one free connected component."]})
        else:
            source, junction, first_terminal, second_terminal = endpoints
            edge_defs = (("MAIN", source, junction), ("BRANCH_1", junction, first_terminal), ("BRANCH_2", junction, second_terminal))
            connections = []
            for edge_id, begin, end in edge_defs:
                route = astar_3d(grid, begin, end); metrics = route_metrics(route, grid.spec.voxel_size_m)
                collisions = exact_centerline_collisions(route.path_cells, grid, meshes) if route.path_found else None
                connections.append({"edge_id": edge_id, "status": "COMPLETED" if route.path_found else "FAILED", "start_cell": list(begin), "end_cell": list(end),
                                    "a_star_expansions": route.expanded_node_count, "route_length_m": metrics["grid_route_length_m"], "centerline_mesh_collision_count": collisions,
                                    "physical_duct_body_validation": {"status": "INCOMPLETE_NO_PASS", "reason": "Experimental section dimensions are available, but exact fitting and connector geometry is unavailable; no PASS is permitted."},
                                    "clearance_validation": {"status": "PARTIAL", "reason": "Conservative AABB obstacle raster was used; exact clearance solids are not available."},
                                    "path_world_m": [list(grid.spec.cell_center(*cell)) for cell in route.path_cells]})
            completed = sum(item["status"] == "COMPLETED" for item in connections)
            result.update({"status": "COMPLETED" if completed == len(connections) else "PARTIAL", "routing_algorithm": "Existing src.routing.astar_3d; shared synthetic MAIN plus two BRANCH edges",
                           "experimental_duct_parameters": {"shape": "rectangular", "width_m": 0.4, "height_m": 0.25, "engineering_status": "EXPERIMENTAL_ONLY"},
                           "endpoints": {"source": list(grid.spec.cell_center(*source)), "junction": list(grid.spec.cell_center(*junction)), "terminals": [list(grid.spec.cell_center(*first_terminal)), list(grid.spec.cell_center(*second_terminal))]},
                           "connections": connections, "completed_connection_count": completed, "failed_connection_count": len(connections) - completed,
                           "routing_success_rate": completed / len(connections), "failure_reasons": []})
            overlay.update({"endpoints": result["endpoints"], "routes": connections, "unresolved_segments": [item["edge_id"] for item in connections if item["status"] != "COMPLETED"]})
    current, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    result["runtime"] = {"total_seconds": round(time.perf_counter() - started, 6), "peak_python_memory_bytes": peak,
                         "stage_seconds": {"geometry_extraction": round(geometry_seconds, 6), "grid_construction": round(grid_seconds, 6), "routing_and_validation": round(time.perf_counter() - routing_started, 6)}}
    result["input_unchanged"] = sha256(architecture) == before
    return result, overlay


def file_record(path: Path) -> dict[str, Any]:
    model = ifcopenshell.open(str(path))
    try:
        georeferenced = bool(model.by_type("IfcMapConversion") or model.by_type("IfcProjectedCRS"))
    except RuntimeError:
        georeferenced = False
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size, "schema": str(model.schema),
            "metre_scale": float(ifcopenshell.util.unit.calculate_unit_scale(model)), "coordinate_system": "IfcOpenShell world coordinates; no recentering applied",
            "georeferencing_entities_present": georeferenced,
            "provenance": source_record(path)}


def native_connectivity(model_id: str, architecture: Path, mep: Path) -> dict[str, Any]:
    """Inventory real port relations without fabricating cross-file registration."""
    data = {"model_id": model_id, "category": NATIVE_MODE, "mep_ifc": file_record(mep), "architecture_ifc": file_record(architecture)}
    model = ifcopenshell.open(str(mep)); ports = list(model.by_type("IfcDistributionPort")); links = list(model.by_type("IfcRelConnectsPorts"))
    terminals = list(model.by_type("IfcFlowTerminal")); sources = list(model.by_type("IfcEnergyConversionDevice")) + list(model.by_type("IfcFlowController"))
    data["native_evidence"] = {"distribution_port_count": len(ports), "port_connection_relationship_count": len(links), "flow_terminal_count": len(terminals), "candidate_source_equipment_count": len(sources),
                               "relationship_basis": "IfcRelConnectsPorts entities only; no inferred missing relations."}
    if not ports or not links or not terminals or not sources:
        data.update({"status": "BLOCKED", "reason": "Insufficient native IFC port/source/terminal evidence for a verified source-to-terminal problem."})
    else:
        data.update({"status": "BLOCKED", "reason": "Native ports exist, but architecture-to-MEP coordinate registration and an explicit source-to-terminal system traversal across separate IFC files are not verified. No native route was fabricated.",
                     "native_connections_requested": 0, "native_connections_completed": 0})
    return data


def markdown(manifest: dict[str, Any], results: dict[str, Any]) -> str:
    lines = ["# Multi-IFC Routing Benchmark", "", "Experimental research only. These outputs are not engineering-approved IFC installations or construction designs.", "",
             "## Category Matrix", "", "| Model | Geometry routing | Native connectivity | Physical validation | Notes |", "|---|---|---|---|---|"]
    for item in results["models"]:
        geometry_result, native = item["geometry_routing"], item["native_connectivity"]
        physical = "INCOMPLETE_NO_PASS" if geometry_result.get("connections") else "NOT_APPLICABLE"
        lines.append(f"| {item['model_id']} | {geometry_result['status']} | {native['status']} | {physical} | {native.get('reason', 'geometry run executed')} |")
    lines.extend(["", "## Interpretation", "", f"Three independent real architectural IFCs were executed as `{GEOMETRY_MODE}`. All synthetic endpoints and duct parameters are explicitly marked experimental. Native connectivity routing completed for 0 models because separate architecture/MEP file registration and verified source-to-terminal paths were not established; no ports or relationships were invented.", "",
                  "Each completed geometry route has an exact mesh-surface centerline check. Duct-body/fitting validation is deliberately `INCOMPLETE_NO_PASS`: no output is a physical clearance or construction approval.", "", "## Reproducibility", "", "Run `python tools/run_multi_ifc_benchmark.py`. The JSON manifest records immutable input hashes, units, schemas, and coordinate handling. The runner only writes under `out/multi_ifc_benchmark/`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real IFC geometry and native-connectivity benchmark categories.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(); output = args.output.resolve(); overlays = output / "overlays"; overlays.mkdir(parents=True, exist_ok=True)
    inputs = []
    for model_id, architecture, mep in MODELS:
        if not architecture.is_file() or not mep.is_file(): raise FileNotFoundError(f"Missing benchmark input for {model_id}")
        inputs.append({"model_id": model_id, "architecture": file_record(architecture), "mep": file_record(mep), "provenance": "local pre-downloaded public IFC candidate; original file remains immutable"})
    inventory = [{"path": str(path), **file_record(path),
                  "selected_for_geometry_routing": any(path == architecture for _, architecture, _ in MODELS),
                  "selected_for_native_connectivity_inspection": any(path == mep for _, _, mep in MODELS)}
                 for path in sorted(EXTERNAL.rglob("*.ifc"))]
    manifest = {"format_version": 1, "benchmark": "MULTI_MODEL_REAL_IFC_ROUTING", "categories": [GEOMETRY_MODE, NATIVE_MODE], "inputs": inputs,
                "available_ifc_inventory": inventory,
                "experimental_parameters": {"voxel_resolution_m": VOXEL_M, "maximum_grid_cells": MAX_CELLS, "duct_section_m": [0.4, 0.25]},
                "engineering_boundary": "Research execution only; do not represent overlays as approved IFC installations."}
    records = []
    for model_id, architecture, mep in MODELS:
        geometry_result, overlay = run_geometry_model(model_id, architecture)
        native = native_connectivity(model_id, architecture, mep)
        (overlays / f"{model_id}_routing_overlay.json").write_text(json.dumps(overlay, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        records.append({"model_id": model_id, "geometry_routing": geometry_result, "native_connectivity": native, "overlay": str(overlays / f"{model_id}_routing_overlay.json")})
    results = {"format_version": 1, "benchmark": "MULTI_MODEL_REAL_IFC_ROUTING", "models": records,
               "category_separation": "Do not aggregate GEOMETRY_SYNTHETIC_ENDPOINTS and NATIVE_CONNECTIVITY results into one success rate."}
    (output / "multi_ifc_benchmark_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "multi_ifc_routing_results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "multi_ifc_benchmark_comparison.md").write_text(markdown(manifest, results), encoding="utf-8")
    print(f"Wrote benchmark outputs to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

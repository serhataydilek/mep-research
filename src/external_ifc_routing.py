"""One deterministic real-IFC subpath reroute executed by the existing 3D A* router.

The search calls ``src.routing.astar_3d`` unchanged. Occupancy is an inflated
AABB broad phase on a 0.1 m grid. A completed search is not a physical
clash-free or infeasibility result.
"""

from __future__ import annotations

from collections import defaultdict
from math import ceil, floor, sqrt
import json
import shutil
import time
import tracemalloc
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .clash import positive_aabb_overlap
from .endpoint_grid import (
    ENDPOINT_CELL_BLOCKED,
    ENDPOINT_OK,
    ENDPOINT_OUTSIDE_GRID,
    assemble_physical_polyline,
    locate_containing_cell,
    polyline_metrics,
    trace_relocated_terminals,
)
from .external_ifc_probe import (
    CONTROLLED_GEOMETRY_ENDPOINT,
    NATIVE_PORT_PLACEMENT,
    OPENING_POLICY,
    RELATIONSHIP_CONSTRAINED_GEOMETRIC,
    bounds_tuple,
    collect_world_aabbs,
    default_thread_count,
    explicit_port_connections,
    placement_origin_m,
    scale_placement_translation,
    port_host_map,
    project_length_scale,
    world_settings,
    world_vertices_metres,
    write_json,
    local_origin_is_identity,
)
from .route_geometry import Segment, compress_path_cells, swept_segment_aabb
from .routing import astar_3d, route_metrics
from .voxel import BLOCKED_FIXED, FREE, GridSpec, OccupancyGrid, aabb_index_ranges

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement


VOXEL_SIZE_M = 0.1
MAX_GRID_CELLS = 800_000
ROI_MARGINS_M = (1.5, 1.0, 0.6)
MIN_INTERFACE_SEPARATION_M = 0.3
NATIVE_MESH_TOLERANCE_M = 0.05
NATIVE_AGREEMENT_M = 0.02
GEOMETRIC_GAP_LIMIT_M = 0.25
TARGET_ELEMENT_COUNT = 6
MIN_ELEMENT_COUNT = 3
MAX_ELEMENT_COUNT = 12
DISCIPLINE_ORDER = ("heating", "ventilation", "sanitary")
ROLE_TOKENS = {"ARC": "architecture", "LFT": "ventilation", "HZG": "heating", "SAN": "sanitary"}
CONTROLLED_ENDPOINT_RULE = (
    "Vertex of the replaced-host mesh closest to the centroid of the retained-host mesh."
)


def discover_discipline_files(data_dir: Path) -> dict[str, Path]:
    """Map downloaded IFC filenames to disciplines by tokens present in the name."""
    found: dict[str, Path] = {}
    for path in sorted(data_dir.glob("*.ifc")):
        if "WITH_SB" in path.stem.upper():
            continue
        parts = set(path.stem.upper().replace("-", "_").split("_"))
        for token, role in ROLE_TOKENS.items():
            if token in parts:
                found.setdefault(role, path)
    return found


def select_flow_subpaths(
    adjacency: dict[str, set[str]],
    flow_ids: set[str],
    *,
    min_count: int = MIN_ELEMENT_COUNT,
    max_count: int = MAX_ELEMENT_COUNT,
    window: int = TARGET_ELEMENT_COUNT,
) -> list[dict[str, list[str]]]:
    """Return explicit linear flow runs with two retained boundary hosts.

    Interior hosts are degree-2 flow segments or fittings. Longer runs contribute
    one deterministic window of ``window`` elements starting at the first boundary.
    """
    degree = {node: len(neighbours) for node, neighbours in adjacency.items()}

    def interior(node: str) -> bool:
        return node in flow_ids and degree.get(node, 0) == 2

    seen: set[tuple[str, str]] = set()
    raw: list[tuple[str, list[str], str]] = []
    for start in sorted(node for node in adjacency if not interior(node)):
        for nxt in sorted(adjacency[start]):
            if not interior(nxt) or (start, nxt) in seen:
                continue
            path = [start, nxt]
            seen.add((start, nxt))
            seen.add((nxt, start))
            while interior(path[-1]):
                options = sorted(node for node in adjacency[path[-1]] if node != path[-2])
                if len(options) != 1:
                    break
                path.append(options[0])
                seen.add((path[-2], path[-1]))
                seen.add((path[-1], path[-2]))
            if interior(path[-1]) or len(path) < 3:
                continue
            replaced = path[1:-1]
            if len(replaced) < min_count:
                continue
            if len(replaced) <= max_count:
                raw.append((path[0], replaced, path[-1]))
            else:
                size = min(max_count, max(min_count, window))
                raw.append((path[0], replaced[:size], replaced[size]))
    raw.sort(key=lambda item: (abs(len(item[1]) - window), len(item[1]), tuple(item[1]), item[0], item[2]))
    return [
        {"boundary_host_global_ids": [left, right], "replaced_host_global_ids": list(replaced)}
        for left, replaced, right in raw
    ]


def classify_interface_point(
    *,
    port_world: tuple[float, float, float] | None,
    local_identity: bool,
    mesh_distance_m: float | None,
    ports_agree: bool,
    geometric_midpoint: tuple[float, float, float] | None,
    geometric_gap_m: float | None,
    controlled_point: tuple[float, float, float] | None,
    native_tolerance_m: float = NATIVE_MESH_TOLERANCE_M,
    geometric_gap_limit_m: float = GEOMETRIC_GAP_LIMIT_M,
) -> tuple[str, list[float], dict[str, Any]]:
    """Classify an interface coordinate. Topology evidence is separate from this label."""
    native_ok = (
        port_world is not None
        and not local_identity
        and ports_agree
        and mesh_distance_m is not None
        and mesh_distance_m <= native_tolerance_m
    )
    if native_ok:
        return NATIVE_PORT_PLACEMENT, [float(value) for value in port_world], {
            "mesh_distance_m": mesh_distance_m,
            "ports_agree": True,
        }
    geometric_ok = (
        geometric_midpoint is not None
        and geometric_gap_m is not None
        and geometric_gap_m <= geometric_gap_limit_m
    )
    if geometric_ok:
        return RELATIONSHIP_CONSTRAINED_GEOMETRIC, [float(value) for value in geometric_midpoint], {
            "host_mesh_gap_m": geometric_gap_m,
        }
    if controlled_point is None:
        raise ValueError("A controlled geometry endpoint requires a point on replaced-host geometry.")
    return CONTROLLED_GEOMETRY_ENDPOINT, [float(value) for value in controlled_point], {
        "rule": CONTROLLED_ENDPOINT_RULE,
    }


def measure_body_half_extent_m(entity: Any, unit_scale: float) -> dict[str, Any]:
    """Read profile dimensions in project units and scale them to metres once."""
    profiles: list[dict[str, Any]] = []

    def walk(item: Any, seen: set[int]) -> None:
        if item is None or not hasattr(item, "id") or item.id() in seen:
            return
        seen.add(item.id())
        if item.is_a("IfcCircleProfileDef"):
            radius = float(item.Radius) * unit_scale
            profiles.append({"shape": "CIRCULAR", "radius_m": radius, "half_extent_m": radius})
        elif item.is_a("IfcRectangleProfileDef"):
            width = float(item.XDim) * unit_scale
            height = float(item.YDim) * unit_scale
            profiles.append({
                "shape": "RECTANGULAR",
                "width_m": width,
                "height_m": height,
                "half_extent_m": max(width, height) / 2,
                "half_extent_rule": "MAX_SIDE_HALF_ORIENTATION_NOT_APPLIED",
            })
        elif item.is_a("IfcExtrudedAreaSolid"):
            walk(item.SweptArea, seen)
        elif item.is_a("IfcBooleanResult") or item.is_a("IfcBooleanClippingResult"):
            walk(item.FirstOperand, seen)
        elif item.is_a("IfcMappedItem"):
            source = item.MappingSource
            if source is not None:
                walk(source.MappedRepresentation, seen)
        elif item.is_a("IfcProductDefinitionShape"):
            for representation in item.Representations or []:
                walk(representation, seen)
        elif item.is_a("IfcShapeRepresentation"):
            for child in item.Items or []:
                walk(child, seen)

    if getattr(entity, "Representation", None) is not None:
        walk(entity.Representation, set())
    if not profiles:
        return {"source": "NO_PROFILE", "half_extent_m": None, "profiles": []}
    chosen = max(profiles, key=lambda item: item["half_extent_m"])
    return {"source": "IFC_PROFILE_TIMES_LENGTH_SCALE_ONCE", "half_extent_m": chosen["half_extent_m"], "profiles": profiles}


def host_adjacency(connections: list[dict[str, Any]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for item in connections:
        left = item["relating_host_global_id"]
        right = item["related_host_global_id"]
        if left and right and left != right:
            adjacency[left].add(right)
            adjacency[right].add(left)
    return adjacency


def rank_subpaths(
    subpaths: list[dict[str, list[str]]],
    systems: dict[str, set[str]],
    storeys: dict[str, set[str]],
) -> list[dict[str, list[str]]]:
    def key(item: dict[str, list[str]]) -> tuple[Any, ...]:
        replaced = item["replaced_host_global_ids"]
        system_sets = [systems.get(global_id, set()) for global_id in replaced]
        storey_sets = [storeys.get(global_id, set()) for global_id in replaced]
        same_system = bool(system_sets) and all(system_sets) and bool(set.intersection(*system_sets))
        same_storey = bool(storey_sets) and all(storey_sets) and bool(set.intersection(*storey_sets))
        return (
            0 if same_system else 1,
            0 if same_storey else 1,
            abs(len(replaced) - TARGET_ELEMENT_COUNT),
            len(replaced),
            tuple(replaced),
            tuple(item["boundary_host_global_ids"]),
        )

    return sorted(subpaths, key=key)


def system_and_storey_maps(model: ifcopenshell.file) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, str]]:
    systems: dict[str, set[str]] = defaultdict(set)
    system_names: dict[str, str] = {}
    for relationship in model.by_type("IfcRelAssignsToGroup"):
        group = relationship.RelatingGroup
        if group is None or not group.is_a("IfcSystem"):
            continue
        system_names[group.GlobalId] = group.Name
        for related in relationship.RelatedObjects or []:
            if getattr(related, "GlobalId", None):
                systems[related.GlobalId].add(group.GlobalId)
    storeys: dict[str, set[str]] = defaultdict(set)
    for relationship in model.by_type("IfcRelContainedInSpatialStructure"):
        structure = relationship.RelatingStructure
        if structure is None or not structure.is_a("IfcBuildingStorey"):
            continue
        label = structure.Name or structure.GlobalId
        for related in relationship.RelatedElements or []:
            if getattr(related, "GlobalId", None):
                storeys[related.GlobalId].add(label)
    return systems, storeys, system_names


def tessellate_hosts(model: ifcopenshell.file, global_ids: Iterable[str]) -> tuple[dict[str, list[float]], list[dict[str, str]]]:
    settings = world_settings()
    meshes: dict[str, list[float]] = {}
    failures = []
    for global_id in global_ids:
        entity = model.by_guid(global_id)
        try:
            if entity is None or getattr(entity, "Representation", None) is None:
                raise ValueError("missing representation")
            shape = ifcopenshell.geom.create_shape(settings, entity)
            vertices = world_vertices_metres(shape.geometry.verts)
            if len(vertices) < 9:
                raise ValueError("empty mesh")
            meshes[global_id] = vertices
        except Exception as exc:
            failures.append({"global_id": global_id, "reason": f"{type(exc).__name__}: {exc}"})
    return meshes, failures


def _points(vertices: list[float], cap: int = 2000) -> np.ndarray:
    points = np.asarray(vertices, dtype=float).reshape(-1, 3)
    if len(points) > cap:
        stride = int(ceil(len(points) / cap))
        points = points[::stride]
    return points


def closest_vertex_pair(first: list[float], second: list[float]) -> tuple[float, tuple[float, float, float]]:
    left = _points(first)
    right = _points(second)
    best_distance = None
    best_midpoint = None
    for start in range(0, len(left), 250):
        block = left[start:start + 250]
        distances = np.linalg.norm(block[:, None, :] - right[None, :, :], axis=2)
        flat = int(distances.argmin())
        row, column = divmod(flat, distances.shape[1])
        distance = float(distances[row, column])
        if best_distance is None or distance < best_distance:
            best_distance = distance
            midpoint = (block[row] + right[column]) / 2
            best_midpoint = (float(midpoint[0]), float(midpoint[1]), float(midpoint[2]))
    if best_distance is None or best_midpoint is None:
        raise ValueError("Host meshes have no vertices.")
    return best_distance, best_midpoint


def point_mesh_distance(point: tuple[float, float, float], vertices: list[float]) -> float:
    points = np.asarray(vertices, dtype=float).reshape(-1, 3)
    return float(np.linalg.norm(points - np.asarray(point, dtype=float), axis=1).min())


def centroid(vertices: list[float]) -> tuple[float, float, float]:
    count = len(vertices) // 3
    return tuple(sum(vertices[axis::3]) / count for axis in range(3))


def controlled_vertex(replaced_vertices: list[float], retained_vertices: list[float]) -> tuple[float, float, float]:
    target = np.asarray(centroid(retained_vertices), dtype=float)
    points = np.asarray(replaced_vertices, dtype=float).reshape(-1, 3)
    index = int(np.linalg.norm(points - target, axis=1).argmin())
    point = points[index]
    return float(point[0]), float(point[1]), float(point[2])


def mesh_bounds(vertices: list[float]) -> tuple[float, float, float, float, float, float]:
    xs, ys, zs = vertices[0::3], vertices[1::3], vertices[2::3]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def union_bounds(boxes: Iterable[tuple[float, float, float, float, float, float]]) -> tuple[float, float, float, float, float, float]:
    items = list(boxes)
    return (
        min(item[0] for item in items), max(item[1] for item in items),
        min(item[2] for item in items), max(item[3] for item in items),
        min(item[4] for item in items), max(item[5] for item in items),
    )


def estimate_cell_count(bounds: tuple[float, float, float, float, float, float], margin_m: float, voxel_size_m: float = VOXEL_SIZE_M) -> int:
    counts = []
    for axis in range(3):
        extent = bounds[axis * 2 + 1] - bounds[axis * 2] + 2 * margin_m
        counts.append(max(1, ceil(extent / voxel_size_m - 1e-9)))
    return counts[0] * counts[1] * counts[2]


def align_roi(
    bounds: tuple[float, float, float, float, float, float], margin_m: float, voxel_size_m: float = VOXEL_SIZE_M,
) -> tuple[float, float, float, float, float, float]:
    expanded = (
        bounds[0] - margin_m, bounds[1] + margin_m,
        bounds[2] - margin_m, bounds[3] + margin_m,
        bounds[4] - margin_m, bounds[5] + margin_m,
    )
    aligned = []
    for axis in range(3):
        low = floor(expanded[axis * 2] / voxel_size_m + 1e-9) * voxel_size_m
        high = ceil(expanded[axis * 2 + 1] / voxel_size_m - 1e-9) * voxel_size_m
        if high <= low:
            high = low + voxel_size_m
        aligned.extend((low, high))
    return tuple(aligned)


def _relations_for_pair(connections: list[dict[str, Any]], left: str, right: str) -> list[dict[str, Any]]:
    pair = frozenset((left, right))
    selected = []
    for item in connections:
        hosts = frozenset(host for host in (item["relating_host_global_id"], item["related_host_global_id"]) if host)
        if hosts == pair:
            selected.append(item)
    return sorted(selected, key=lambda item: item["relation_global_id"] or "")


def _port_record(model: ifcopenshell.file, global_id: str | None, unit_scale: float, host_vertices: list[float] | None) -> dict[str, Any] | None:
    if not global_id:
        return None
    port = model.by_guid(global_id)
    if port is None:
        return None
    world = placement_origin_m(port, unit_scale)
    distance = None if world is None or host_vertices is None else point_mesh_distance(world, host_vertices)
    return {
        "global_id": global_id,
        "local_origin_identity": local_origin_is_identity(port),
        "world_m": None if world is None else [float(value) for value in world],
        "mesh_distance_m": distance,
    }


def resolve_interface(
    model: ifcopenshell.file,
    connections: list[dict[str, Any]],
    retained_gid: str,
    replaced_gid: str,
    meshes: dict[str, list[float]],
    unit_scale: float,
) -> dict[str, Any]:
    relations = _relations_for_pair(connections, retained_gid, replaced_gid)
    if not relations:
        raise ValueError(f"No explicit IfcRelConnectsPorts relation joins {retained_gid} and {replaced_gid}.")
    retained_ports = []
    replaced_ports = []
    for relation in relations:
        mapping = {
            relation["relating_host_global_id"]: relation["relating_port_global_id"],
            relation["related_host_global_id"]: relation["related_port_global_id"],
        }
        retained_ports.append(_port_record(model, mapping.get(retained_gid), unit_scale, meshes.get(retained_gid)))
        replaced_ports.append(_port_record(model, mapping.get(replaced_gid), unit_scale, meshes.get(replaced_gid)))
    retained_port = next((item for item in retained_ports if item and item["world_m"] is not None), None)
    replaced_port = next((item for item in replaced_ports if item and item["world_m"] is not None), None)
    gap, midpoint = closest_vertex_pair(meshes[retained_gid], meshes[replaced_gid])
    controlled = controlled_vertex(meshes[replaced_gid], meshes[retained_gid])
    def qualifies(port: dict[str, Any] | None) -> bool:
        if not port or not port.get("world_m") or port.get("local_origin_identity"):
            return False
        distance = port.get("mesh_distance_m")
        return distance is not None and distance <= NATIVE_MESH_TOLERANCE_M

    worlds = [tuple(item["world_m"]) for item in (retained_port, replaced_port) if item and item.get("world_m")]
    ports_agree = len(worlds) <= 1 or _distance(worlds[0], worlds[1]) <= NATIVE_AGREEMENT_M
    qualified = [item for item in (retained_port, replaced_port) if qualifies(item)]
    chosen = qualified[0] if qualified and ports_agree else None
    classification, point, evidence = classify_interface_point(
        port_world=None if chosen is None or chosen["world_m"] is None else tuple(chosen["world_m"]),
        local_identity=True if chosen is None else bool(chosen["local_origin_identity"]),
        mesh_distance_m=None if chosen is None else chosen["mesh_distance_m"],
        ports_agree=ports_agree,
        geometric_midpoint=midpoint,
        geometric_gap_m=gap,
        controlled_point=controlled,
    )
    return {
        "coordinate_classification": classification,
        "world_m": [round(value, 6) for value in point],
        "retained_host_global_id": retained_gid,
        "replaced_host_global_id": replaced_gid,
        "relation_global_ids": [item["relation_global_id"] for item in relations],
        "retained_port": retained_port,
        "replaced_port": replaced_port,
        "ports_agree_within_m": NATIVE_AGREEMENT_M,
        "ports_agree": ports_agree,
        "geometric_gap_m": round(gap, 6),
        "classification_evidence": evidence,
        "topology_evidence": "EXPLICIT_IFC_REL_CONNECTS_PORTS",
        "not_a_native_source_to_terminal_design_request": True,
    }


def _distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return sqrt(sum((first[axis] - second[axis]) ** 2 for axis in range(3)))


def offset_into_replacement(
    interface: tuple[float, float, float],
    replaced_centroid_m: tuple[float, float, float],
    retained_centroid_m: tuple[float, float, float],
    offset_m: float,
) -> tuple[float, float, float]:
    direction = [replaced_centroid_m[axis] - interface[axis] for axis in range(3)]
    norm = sqrt(sum(value * value for value in direction))
    if norm < 1e-8:
        direction = [replaced_centroid_m[axis] - retained_centroid_m[axis] for axis in range(3)]
        norm = sqrt(sum(value * value for value in direction))
    if norm < 1e-8:
        direction = [1.0, 0.0, 0.0]
        norm = 1.0
    return tuple(interface[axis] + direction[axis] / norm * offset_m for axis in range(3))


def aabb_half_extent(vertices: list[float]) -> float:
    bounds = mesh_bounds(vertices)
    edges = [bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]]
    positive = [edge for edge in edges if edge > 1e-9]
    if not positive:
        return 0.0
    return min(positive) / 2


def prepare_candidate(
    model: ifcopenshell.file,
    candidate: dict[str, list[str]],
    connections: list[dict[str, Any]],
    unit_scale: float,
    systems: dict[str, set[str]],
    storeys: dict[str, set[str]],
    system_names: dict[str, str],
) -> dict[str, Any] | None:
    ordered = candidate["boundary_host_global_ids"][0:1] + candidate["replaced_host_global_ids"] + candidate["boundary_host_global_ids"][1:2]
    meshes, failures = tessellate_hosts(model, ordered)
    if failures or any(global_id not in meshes for global_id in ordered):
        return None
    try:
        start = resolve_interface(
            model, connections, candidate["boundary_host_global_ids"][0], candidate["replaced_host_global_ids"][0], meshes, unit_scale,
        )
        end = resolve_interface(
            model, connections, candidate["boundary_host_global_ids"][1], candidate["replaced_host_global_ids"][-1], meshes, unit_scale,
        )
    except ValueError:
        return None
    if _distance(tuple(start["world_m"]), tuple(end["world_m"])) < MIN_INTERFACE_SEPARATION_M:
        return None
    bodies = []
    for global_id in candidate["replaced_host_global_ids"]:
        entity = model.by_guid(global_id)
        measured = measure_body_half_extent_m(entity, unit_scale)
        if measured["half_extent_m"] is None:
            measured = {
                "source": "AABB_MIN_EDGE_HALF",
                "half_extent_m": aabb_half_extent(meshes[global_id]),
                "profiles": [],
            }
        bodies.append({"global_id": global_id, "ifc_class": entity.is_a(), "name": entity.Name, **measured})
    half_extent = max(item["half_extent_m"] for item in bodies)
    boxes = [mesh_bounds(meshes[global_id]) for global_id in ordered]
    span = union_bounds(boxes)
    margin = next((item for item in ROI_MARGINS_M if estimate_cell_count(span, item) <= MAX_GRID_CELLS), None)
    if margin is None:
        return None
    shared_systems = set.intersection(*(systems.get(global_id, set()) for global_id in candidate["replaced_host_global_ids"]))
    shared_storeys = set.intersection(*(storeys.get(global_id, set()) for global_id in candidate["replaced_host_global_ids"]))
    return {
        "candidate": candidate,
        "meshes": meshes,
        "start_interface": start,
        "end_interface": end,
        "bodies": bodies,
        "body_half_extent_m": half_extent,
        "span_bounds_m": span,
        "roi_margin_m": margin,
        "estimated_cells": estimate_cell_count(span, margin),
        "system_global_ids": sorted(shared_systems),
        "system_names": [system_names.get(global_id) for global_id in sorted(shared_systems)],
        "storeys": sorted(shared_storeys),
    }


def _roi_from_preparation(
    prepared: dict[str, Any], voxel_size_m: float = VOXEL_SIZE_M,
) -> tuple[float, float, float, float, float, float]:
    return align_roi(tuple(prepared["span_bounds_m"]), prepared["roi_margin_m"], voxel_size_m)


def grid_shape(roi: tuple[float, float, float, float, float, float], voxel_size_m: float = VOXEL_SIZE_M) -> tuple[int, int, int]:
    counts = []
    for axis in range(3):
        count = round((roi[axis * 2 + 1] - roi[axis * 2]) / voxel_size_m)
        if count < 1 or abs((roi[axis * 2 + 1] - roi[axis * 2]) - count * voxel_size_m) > 1e-6:
            raise ValueError("Aligned ROI is not an integer number of voxels.")
        counts.append(count)
    return counts[0], counts[1], counts[2]


def bounds_intersect(
    bounds: tuple[float, float, float, float, float, float],
    roi: tuple[float, float, float, float, float, float],
) -> bool:
    return not (
        bounds[1] < roi[0] or bounds[0] > roi[1]
        or bounds[3] < roi[2] or bounds[2] > roi[3]
        or bounds[5] < roi[4] or bounds[4] > roi[5]
    )


def obstacle_decision(ifc_class: str, file_role: str, global_id: str, replaced_ids: set[str]) -> str:
    if global_id in replaced_ids:
        return "REPLACED_HOST_EXCLUDED"
    if ifc_class == "IfcOpeningElement":
        return "OPENING_INVENTORIED_NOT_SUBTRACTED"
    if ifc_class in {"IfcSpace", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcAnnotation", "IfcGrid", "IfcDistributionPort"}:
        return "NOT_AN_OBSTACLE"
    if file_role == "architecture":
        return "ARCHITECTURAL_OBSTACLE"
    mep_markers = ("Pipe", "Duct", "Flow", "Valve", "Heater", "Pump", "Tank", "Sensor", "Damper", "Terminal", "Controller", "Fitting", "Segment", "Unitary", "Filter", "Fan", "Coil")
    if any(marker.lower() in ifc_class.lower() for marker in mep_markers) or ifc_class.startswith("IfcDistribution"):
        return "RETAINED_MEP_OBSTACLE"
    return "NOT_AN_OBSTACLE"


def inflate_bounds(
    bounds: tuple[float, float, float, float, float, float], margin_m: float,
) -> tuple[float, float, float, float, float, float]:
    return (
        bounds[0] - margin_m, bounds[1] + margin_m,
        bounds[2] - margin_m, bounds[3] + margin_m,
        bounds[4] - margin_m, bounds[5] + margin_m,
    )


def mark_obstacle_aabbs(
    grid: OccupancyGrid,
    obstacles: list[dict[str, Any]],
    inflation_m: float,
) -> int:
    marked = 0
    for obstacle in obstacles:
        before = marked
        ranges = aabb_index_ranges(grid.spec, inflate_bounds(tuple(obstacle["bounds_m"]), inflation_m))
        for k in ranges[2]:
            for j in ranges[1]:
                base = grid.spec.linear_index(0, j, k)
                for i in ranges[0]:
                    index = base + i
                    if grid.cells[index] != BLOCKED_FIXED:
                        grid.cells[index] = BLOCKED_FIXED
                        marked += 1
        obstacle["marked_cell_count"] = marked - before
    return marked


def nearest_free_cell(grid: OccupancyGrid, point: tuple[float, float, float]) -> dict[str, Any] | None:
    spec = grid.spec

    def clamp(value: float, origin: float, count: int) -> int:
        index = floor((value - origin) / spec.voxel_size_m)
        return min(max(index, 0), count - 1)

    center_index = (
        clamp(point[0], spec.origin_x, spec.nx),
        clamp(point[1], spec.origin_y, spec.ny),
        clamp(point[2], spec.origin_z, spec.nz),
    )
    limit = max(spec.nx, spec.ny, spec.nz)
    for radius in range(limit + 1):
        shell = []
        for dk in range(-radius, radius + 1):
            for dj in range(-radius, radius + 1):
                for di in range(-radius, radius + 1):
                    if abs(di) + abs(dj) + abs(dk) != radius:
                        continue
                    index = (center_index[0] + di, center_index[1] + dj, center_index[2] + dk)
                    if not (0 <= index[0] < spec.nx and 0 <= index[1] < spec.ny and 0 <= index[2] < spec.nz):
                        continue
                    if grid.cells[spec.linear_index(*index)] != FREE:
                        continue
                    center = spec.cell_center(*index)
                    distance_sq = sum((center[axis] - point[axis]) ** 2 for axis in range(3))
                    shell.append((distance_sq, index, center))
        if shell:
            distance_sq, index, center = min(shell)
            return {
                "grid_index": list(index),
                "voxel_center_m": [round(value, 6) for value in center],
                "snap_distance_m": round(sqrt(distance_sq), 6),
                "exceeds_existing_two_voxel_tolerance": sqrt(distance_sq) > 2 * spec.voxel_size_m + 1e-9,
            }
    return None


def diagnose_no_path(grid: OccupancyGrid, start: tuple[int, int, int], goal: tuple[int, int, int], snap_distances: list[float]) -> dict[str, Any]:
    spec = grid.spec
    seen = {spec.linear_index(*start)}
    stack = [start]
    touches_boundary = False
    while stack:
        current = stack.pop()
        if current[0] in (0, spec.nx - 1) or current[1] in (0, spec.ny - 1) or current[2] in (0, spec.nz - 1):
            touches_boundary = True
        for offset in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            nxt = (current[0] + offset[0], current[1] + offset[1], current[2] + offset[2])
            if not (0 <= nxt[0] < spec.nx and 0 <= nxt[1] < spec.ny and 0 <= nxt[2] < spec.nz):
                continue
            linear = spec.linear_index(*nxt)
            if linear in seen or grid.cells[linear] != FREE:
                continue
            seen.add(linear)
            stack.append(nxt)
    free_cells = grid.cells.count(FREE)
    factors = []
    if any(distance > 2 * spec.voxel_size_m + 1e-9 for distance in snap_distances):
        factors.append("ENDPOINT_PLACEMENT")
    if touches_boundary and spec.linear_index(*goal) not in seen:
        factors.append("ROI_LIMITS")
    if spec.linear_index(*goal) not in seen:
        factors.append("OCCUPANCY_APPROXIMATION")
    if not factors:
        factors.append("OCCUPANCY_APPROXIMATION")
    return {
        "physical_infeasibility_claimed": False,
        "search_exhaustion": False,
        "iteration_cap": None,
        "reachable_component_exhausted_by_completed_search": True,
        "reachable_free_cells": len(seen),
        "free_cell_count": free_cells,
        "goal_in_start_component": spec.linear_index(*goal) in seen,
        "reachable_component_touches_roi_boundary": touches_boundary,
        "contributing_factors": factors,
        "primary_factor": factors[0],
        "statement": "The finite orthogonal voxel graph does not connect the routing terminals. This is not a physical-infeasibility claim.",
    }


def broad_phase_validation(
    grid: OccupancyGrid,
    path_cells: tuple[tuple[int, int, int], ...],
    obstacles: list[dict[str, Any]],
    body_half_extent_m: float,
    excluded_global_ids: set[str],
) -> dict[str, Any]:
    if not path_cells:
        return {
            "method": "BROAD_PHASE_SWEPT_AABB",
            "label": "BROAD_PHASE_ONLY",
            "confirmed_collision_result": False,
            "checked": False,
        }
    segments = compress_path_cells([list(cell) for cell in path_cells], grid.spec)
    hits = []
    for segment in segments:
        swept = swept_segment_aabb(segment, body_half_extent_m, body_half_extent_m)
        for obstacle in obstacles:
            if obstacle["global_id"] in excluded_global_ids:
                continue
            if positive_aabb_overlap(swept, tuple(obstacle["bounds_m"])):
                hits.append({
                    "global_id": obstacle["global_id"],
                    "ifc_class": obstacle["ifc_class"],
                    "file_role": obstacle["file_role"],
                })
    unique = {(item["global_id"], item["file_role"]) for item in hits}
    return _broad_phase_report(hits, unique, body_half_extent_m, excluded_global_ids)


def _working_set() -> dict[str, int] | None:
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32")
        query = psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        query.restype = wintypes.BOOL
        if not query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return {"working_set_bytes": int(counters.WorkingSetSize), "peak_working_set_bytes": int(counters.PeakWorkingSetSize)}
    except Exception:
        return None


def load_role_geometry(path: Path, *, threads: int) -> dict[str, Any]:
    model = ifcopenshell.open(str(path))
    collected = collect_world_aabbs(model, threads=threads)
    voids: dict[str, list[dict[str, str | None]]] = {}
    for opening in model.by_type("IfcOpeningElement"):
        links = []
        for relationship in getattr(opening, "VoidsElements", []) or []:
            host = relationship.RelatingBuildingElement
            links.append({
                "relation_global_id": relationship.GlobalId,
                "host_global_id": None if host is None else host.GlobalId,
            })
        if links:
            voids[opening.GlobalId] = links
    return {"collected": collected, "voids": voids}


def build_occupancy(
    files: dict[str, Path],
    prepared: dict[str, Any],
    *,
    threads: int,
    geometry_cache: dict[str, dict[str, Any]] | None = None,
    voxel_size_m: float = VOXEL_SIZE_M,
    inflation_m: float | None = None,
) -> dict[str, Any]:
    replaced = set(prepared["candidate"]["replaced_host_global_ids"])
    roi = _roi_from_preparation(prepared, voxel_size_m)
    nx, ny, nz = grid_shape(roi, voxel_size_m)
    cell_count = nx * ny * nz
    if cell_count > MAX_GRID_CELLS:
        raise ValueError(f"ROI grid has {cell_count} cells, above the {MAX_GRID_CELLS} limit.")
    spec = GridSpec(voxel_size_m, roi[0], roi[2], roi[4], nx, ny, nz)
    grid = OccupancyGrid(
        "digitalhub-v1-first-reroute",
        prepared.get("discipline", "mep"),
        spec,
        bytearray(cell_count),
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        {},
    )
    obstacles = []
    openings = []
    geometry_failures = []
    considered = 0
    cache = geometry_cache if geometry_cache is not None else {}
    for role, path in files.items():
        if role not in cache:
            cache[role] = load_role_geometry(path, threads=threads)
        collected = cache[role]["collected"]
        voids = cache[role]["voids"]
        geometry_failures.append({
            "file_role": role,
            "geometry_failure_count": collected["geometry_failure_count"],
            "geometry_failure_sample": collected["geometry_failure_sample"],
        })
        for global_id, element in collected["elements"].items():
            bounds = bounds_tuple(element["bounds_m"])
            if not bounds_intersect(bounds, roi):
                continue
            considered += 1
            decision = obstacle_decision(element["ifc_class"], role, global_id, replaced)
            record = {
                "global_id": global_id,
                "ifc_class": element["ifc_class"],
                "name": element["name"],
                "file_role": role,
                "decision": decision,
                "bounds_m": list(bounds),
            }
            if decision == "OPENING_INVENTORIED_NOT_SUBTRACTED":
                openings.append({**record, "void_relationships": voids.get(global_id, [])})
            elif decision in {"ARCHITECTURAL_OBSTACLE", "RETAINED_MEP_OBSTACLE"}:
                obstacles.append(record)
    obstacles.sort(key=lambda item: (item["file_role"], item["global_id"]))
    inflation = float(prepared["body_half_extent_m"] if inflation_m is None else inflation_m)
    marked = mark_obstacle_aabbs(grid, obstacles, inflation)
    counts: dict[str, int] = defaultdict(int)
    for obstacle in obstacles:
        counts[f"{obstacle['file_role']}:{obstacle['ifc_class']}"] += 1
    return {
        "grid": grid,
        "roi_bounds_m": list(roi),
        "obstacles": obstacles,
        "openings": openings,
        "marked_cell_count": marked,
        "class_counts": dict(sorted(counts.items())),
        "geometry_failures": geometry_failures,
        "elements_intersecting_roi": considered,
        "opening_policy": OPENING_POLICY,
        "replaced_host_global_ids_excluded": sorted(replaced),
        "inflation_m": inflation,
        "voxel_size_m": voxel_size_m,
    }


def _broad_phase_report(
    hits: list[dict[str, Any]],
    unique: set[tuple[str, str]],
    body_half_extent_m: float,
    excluded_global_ids: set[str],
) -> dict[str, Any]:
    hit_count = len(unique)
    label = "ZERO_BROAD_PHASE_CANDIDATES" if hit_count == 0 else "BROAD_PHASE_AABB_HITS"
    return {
        "method": "BROAD_PHASE_SWEPT_AABB",
        "label": label,
        "confirmed_collision_result": False,
        "checked": True,
        "body_half_extent_m": body_half_extent_m,
        "additional_clearance_m": 0.0,
        "hit_count": hit_count,
        "hit_sample": hits[:20],
        "excluded_obstacle_global_ids": sorted(excluded_global_ids),
        "note": (
            "ZERO_BROAD_PHASE_CANDIDATES means the swept route AABBs overlapped no tested obstacle AABB. "
            "It does not prove exact mesh-level clearance. A nonzero hit count is also an AABB broad phase, not a confirmed mesh collision."
            if hit_count == 0
            else "Swept route AABBs overlap tested obstacle AABBs. This is a broad-phase hit, not a confirmed mesh collision."
        ),
    }


def _point_inside_bounds(
    point: tuple[float, float, float], bounds: tuple[float, float, float, float, float, float],
) -> bool:
    return (
        bounds[0] <= point[0] <= bounds[1]
        and bounds[2] <= point[1] <= bounds[3]
        and bounds[4] <= point[2] <= bounds[5]
    )


def obstacles_covering_index(
    grid: OccupancyGrid,
    obstacles: list[dict[str, Any]],
    index: tuple[int, int, int],
    inflation_m: float,
) -> list[dict[str, Any]]:
    """Obstacles whose inflated AABB marks this cell under the occupancy rule."""
    hits = []
    for obstacle in obstacles:
        ranges = aabb_index_ranges(grid.spec, inflate_bounds(tuple(obstacle["bounds_m"]), inflation_m))
        if index[0] in ranges[0] and index[1] in ranges[1] and index[2] in ranges[2]:
            hits.append({
                "global_id": obstacle["global_id"],
                "ifc_class": obstacle["ifc_class"],
                "file_role": obstacle["file_role"],
                "decision": obstacle["decision"],
            })
    return hits


def obstacles_containing_point(
    obstacles: list[dict[str, Any]], point: tuple[float, float, float], inflation_m: float,
) -> list[dict[str, str]]:
    hits = []
    for obstacle in obstacles:
        raw = tuple(obstacle["bounds_m"])
        inflated = inflate_bounds(raw, inflation_m)
        inside_raw = _point_inside_bounds(point, raw)
        inside_inflated = _point_inside_bounds(point, inflated)
        if inside_raw or inside_inflated:
            hits.append({
                "global_id": obstacle["global_id"],
                "ifc_class": obstacle["ifc_class"],
                "file_role": obstacle["file_role"],
                "decision": obstacle["decision"],
                "point_inside_uninflated_aabb": inside_raw,
                "point_inside_inflated_aabb": inside_inflated,
            })
    return hits


def resolve_physical_terminal(
    grid: OccupancyGrid,
    point: tuple[float, float, float],
    obstacles: list[dict[str, Any]],
    inflation_m: float,
    *,
    role: str,
) -> dict[str, Any]:
    """Map one immutable port to its containing cell. Never clamp or search for a free neighbour."""
    spec = grid.spec
    located = locate_containing_cell(
        (spec.origin_x, spec.origin_y, spec.origin_z),
        spec.voxel_size_m,
        (spec.nx, spec.ny, spec.nz),
        point,
    )
    located["role"] = role
    located["port_point_obstacle_hits"] = obstacles_containing_point(obstacles, point, inflation_m)
    if located["status"] == ENDPOINT_OUTSIDE_GRID:
        located["blocking_obstacles"] = []
        located["voxel_center_m"] = None
        return located
    index = tuple(located["containing_index"])
    center = spec.cell_center(*index)
    located["voxel_center_m"] = [float(value) for value in center]
    located["blocking_obstacles"] = obstacles_covering_index(grid, obstacles, index, inflation_m)
    free = grid.cells[spec.linear_index(*index)] == FREE
    located["cell_state"] = "FREE" if free else "BLOCKED"
    if not free:
        located["status"] = ENDPOINT_CELL_BLOCKED
    return located


def port_placement_trace(model: Any, global_id: str | None, unit_scale: float) -> dict[str, Any] | None:
    """Record the nested placement chain and the single metre conversion."""
    if not global_id:
        return None
    port = model.by_guid(global_id)
    if port is None:
        return None
    chain = []
    placement = getattr(port, "ObjectPlacement", None)
    while placement is not None and placement.is_a("IfcLocalPlacement"):
        local = None
        if placement.RelativePlacement is not None and placement.RelativePlacement.Location is not None:
            local = [float(value) for value in placement.RelativePlacement.Location.Coordinates]
        matrix = ifcopenshell.util.placement.get_local_placement(placement)
        composed = scale_placement_translation((matrix[0][3], matrix[1][3], matrix[2][3]), unit_scale)
        chain.append({
            "placement_global_id": getattr(placement, "GlobalId", None),
            "local_translation_project_units": local,
            "local_translation_m": None if local is None else [value * unit_scale for value in local],
            "composed_world_m": [float(value) for value in composed],
        })
        placement = getattr(placement, "PlacementRelTo", None)
    world = placement_origin_m(port, unit_scale)
    return {
        "port_global_id": global_id,
        "length_unit_scale_m": unit_scale,
        "scale_applications": 1,
        "nested_placement_chain_child_to_root": chain,
        "composed_world_m": None if world is None else [float(value) for value in world],
        "rejected_double_scaled_world_m": None if world is None else [float(value) * unit_scale for value in world],
        "world_mesh_policy": "USE_WORLD_COORDS vertices are already metres and are not multiplied by the unit scale.",
    }


def broad_phase_world_polyline(
    points: list[list[float]],
    obstacles: list[dict[str, Any]],
    body_half_extent_m: float,
    excluded_global_ids: set[str],
) -> dict[str, Any]:
    """Validate voxel-centre segments and explicit terminal stubs with the same swept AABB."""
    hits = []
    for start, end in zip(points, points[1:]):
        if all(abs(float(start[axis]) - float(end[axis])) <= 1e-12 for axis in range(3)):
            continue
        swept = swept_segment_aabb(
            Segment(tuple(float(value) for value in start), tuple(float(value) for value in end)),
            body_half_extent_m,
            body_half_extent_m,
        )
        for obstacle in obstacles:
            if obstacle["global_id"] in excluded_global_ids:
                continue
            if positive_aabb_overlap(swept, tuple(obstacle["bounds_m"])):
                hits.append({
                    "global_id": obstacle["global_id"],
                    "ifc_class": obstacle["ifc_class"],
                    "file_role": obstacle["file_role"],
                })
    unique = {(item["global_id"], item["file_role"]) for item in hits}
    return _broad_phase_report(hits, unique, body_half_extent_m, excluded_global_ids)


def preserve_historical_phase11a(output_dir: Path) -> None:
    """Keep the Phase 11A proxy route before a semantically different execution overwrites it."""
    historical = output_dir / "historical_phase11a"
    if (historical / "routing_execution.json").exists():
        return
    current = output_dir / "routing_execution.json"
    if not current.exists():
        return
    payload = json.loads(current.read_text(encoding="utf-8"))
    if payload.get("endpoint_policy") == "CONTAINING_CELL_NO_RELOCATION":
        return
    historical.mkdir(parents=True, exist_ok=True)
    for name in ("routing_request.json", "occupancy_manifest.json", "routing_execution.json"):
        source = output_dir / name
        if source.exists():
            shutil.copy2(source, historical / name)
    write_json(historical / "LABEL.json", {
        "label": "PHASE_11A_HISTORICAL_PROXY_ROUTE",
        "reason": (
            "Preserved before Phase 11A.1 stopped offsetting ports and moving them to a nearby free cell. "
            "The polyline in routing_execution.json connects voxel centres, not the original IFC ports."
        ),
    })


def historical_relocation_trace(output_dir: Path) -> dict[str, Any] | None:
    historical = output_dir / "historical_phase11a"
    execution_path = historical / "routing_execution.json"
    request_path = historical / "routing_request.json"
    manifest_path = historical / "occupancy_manifest.json"
    if not execution_path.exists() or not request_path.exists() or not manifest_path.exists():
        return None
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    origin = manifest["grid"]["origin_m"]
    voxel = manifest["voxel_size_m"]
    trace = trace_relocated_terminals(
        origin,
        voxel,
        request["start_interface"]["world_m"],
        request["end_interface"]["world_m"],
        execution["start_offset_target_m"],
        execution["end_offset_target_m"],
        execution["start_terminal"]["grid_index"],
        execution["end_terminal"]["grid_index"],
    )
    trace["historical_artifact"] = "experiments/digitalhub_v1/historical_phase11a"
    trace["historical_search_status"] = execution.get("search_status")
    trace["historical_grid_metrics"] = execution.get("metrics")
    trace["historical_polyline_z_values_m"] = sorted({point[2] for point in execution.get("world_polyline_m", [])})
    trace["historical_offset_m"] = request.get("routing_terminal_offset_m")
    start_z = trace["start_containing_index"][2]
    end_z = trace["end_containing_index"][2]
    used_start_z = trace["used_start_index"][2]
    used_end_z = trace["used_end_index"][2]
    counts = (manifest["grid"]["nx"], manifest["grid"]["ny"], manifest["grid"]["nz"])
    start_index = trace["start_containing_index"]
    end_index = trace["end_containing_index"]
    indices_inside = all(
        0 <= start_index[axis] < counts[axis] and 0 <= end_index[axis] < counts[axis] for axis in range(3)
    )
    trace["demonstrated_cause"] = {
        "name": "OFFSET_INTO_REPLACEMENT_THEN_NEAREST_FREE_CELL",
        "ports_share_a_z_cell": start_z == end_z,
        "offset_proxy_moved_start_z_cell": trace["start_proxy_containing_index"][2] != start_z,
        "nearest_free_moved_goal_z_cell": trace["end_relocation"]["z_index_changed"],
        "nearest_free_moved_start_cell": trace["proxy_start_relocation"]["status"] == "TERMINAL_RELOCATED",
        "search_nodes_share_a_z_cell": used_start_z == used_end_z,
        "polyline_dropped_port_elevations": len(trace["historical_polyline_z_values_m"]) == 1,
        "ruled_out": {
            "wrong_port_z_already_equal": start_z == end_z,
            "origin_subtraction_collapsed_z": start_z == end_z,
            "integer_floor_collapsed_z": start_z == end_z,
            "containing_indices_inside_grid": indices_inside,
            "grid_clamping_was_required_for_the_raw_ports": not indices_inside,
            "start_normalized_z": (request["start_interface"]["world_m"][2] - origin[2]) / voxel,
            "end_normalized_z": (request["end_interface"]["world_m"][2] - origin[2]) / voxel,
            "grid_counts": list(counts),
        },
    }
    return trace


def execute_experiment(data_dir: Path, output_dir: Path, *, threads: int | None = None, max_candidates: int = 6) -> dict[str, Any]:
    """Select one real subpath and execute ``astar_3d``. Artifacts are written even on failure."""
    thread_count = default_thread_count() if threads is None else threads
    files = discover_discipline_files(data_dir)
    missing = [role for role in ("architecture", *DISCIPLINE_ORDER) if role not in files]
    if missing:
        raise FileNotFoundError(f"Missing discipline IFC files in {data_dir}: {missing}")
    output_dir.mkdir(parents=True, exist_ok=True)
    preserve_historical_phase11a(output_dir)
    attempts = []
    executed = None
    geometry_cache: dict[str, dict[str, Any]] = {}
    for discipline in DISCIPLINE_ORDER:
        if executed is not None:
            break
        model = ifcopenshell.open(str(files[discipline]))
        unit_scale = project_length_scale(model)
        connections = explicit_port_connections(model, port_host_map(model))
        flow_ids = {item.GlobalId for item in model.by_type("IfcFlowSegment")}
        flow_ids.update(item.GlobalId for item in model.by_type("IfcFlowFitting"))
        systems, storeys, system_names = system_and_storey_maps(model)
        ranked = rank_subpaths(select_flow_subpaths(host_adjacency(connections), flow_ids), systems, storeys)
        resolved = 0
        for candidate in ranked:
            if resolved >= max_candidates or executed is not None:
                break
            prepared = prepare_candidate(model, candidate, connections, unit_scale, systems, storeys, system_names)
            if prepared is None:
                continue
            resolved += 1
            prepared["discipline"] = discipline
            prepared["unit_scale_m"] = unit_scale
            prepared["source_file"] = files[discipline].name
            try:
                occupancy = build_occupancy(files, prepared, threads=thread_count, geometry_cache=geometry_cache)
            except ValueError as exc:
                attempts.append({"discipline": discipline, "status": "ROI_LIMITS", "detail": str(exc), "replaced_host_global_ids": candidate["replaced_host_global_ids"]})
                continue
            grid = occupancy["grid"]
            terminals = []
            for role, interface in (
                ("start", prepared["start_interface"]),
                ("end", prepared["end_interface"]),
            ):
                terminal = resolve_physical_terminal(
                    grid,
                    tuple(interface["world_m"]),
                    occupancy["obstacles"],
                    prepared["body_half_extent_m"],
                    role=role,
                )
                terminal["immutable_interface_m"] = list(interface["world_m"])
                terminal["coordinate_classification"] = interface["coordinate_classification"]
                terminal["placement"] = port_placement_trace(
                    model, (interface.get("retained_port") or {}).get("global_id"), unit_scale,
                )
                terminal["paired_port_placement"] = port_placement_trace(
                    model, (interface.get("replaced_port") or {}).get("global_id"), unit_scale,
                )
                terminals.append(terminal)
            invalid = [item for item in terminals if item["status"] != ENDPOINT_OK]
            if invalid:
                executed = {
                    "prepared": prepared,
                    "occupancy": occupancy,
                    "terminals": terminals,
                    "endpoint_failure": True,
                    "result": None,
                    "runtime_seconds": None,
                    "peak_traced_bytes": None,
                    "grid_metrics": None,
                    "physical": None,
                    "physical_metrics": None,
                    "diagnosis": None,
                    "validation": {
                        "method": "BROAD_PHASE_SWEPT_AABB",
                        "label": "NOT_RUN_ENDPOINT_INVALID",
                        "confirmed_collision_result": False,
                        "checked": False,
                        "note": "No route was searched because a terminal containing cell is outside the grid or blocked. Zero swept-AABB candidates is not applicable.",
                    },
                    "process_memory": _working_set(),
                }
                break
            start_index = tuple(terminals[0]["containing_index"])
            goal_index = tuple(terminals[1]["containing_index"])
            tracemalloc.start()
            started = time.perf_counter()
            result = astar_3d(grid, start_index, goal_index)
            runtime = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            metrics = route_metrics(result, grid.spec.voxel_size_m)
            diagnosis = None if result.path_found else diagnose_no_path(grid, start_index, goal_index, [])
            physical = None
            physical_metrics = None
            validation = {
                "method": "BROAD_PHASE_SWEPT_AABB",
                "label": "NOT_RUN_NO_VOXEL_PATH",
                "confirmed_collision_result": False,
                "checked": False,
            }
            if result.path_found:
                centers = [list(grid.spec.cell_center(*cell)) for cell in result.path_cells]
                physical = assemble_physical_polyline(
                    tuple(prepared["start_interface"]["world_m"]),
                    centers,
                    tuple(prepared["end_interface"]["world_m"]),
                )
                physical_metrics = polyline_metrics(physical["polyline_m"])
                validation = broad_phase_world_polyline(
                    physical["polyline_m"],
                    occupancy["obstacles"],
                    prepared["body_half_extent_m"],
                    set(prepared["candidate"]["replaced_host_global_ids"]),
                )
            executed = {
                "prepared": prepared,
                "occupancy": occupancy,
                "terminals": terminals,
                "endpoint_failure": False,
                "result": result,
                "runtime_seconds": runtime,
                "peak_traced_bytes": peak,
                "grid_metrics": metrics,
                "physical": physical,
                "physical_metrics": physical_metrics,
                "diagnosis": diagnosis,
                "validation": validation,
                "process_memory": _working_set(),
            }
        if executed is None and resolved == 0:
            attempts.append({
                "discipline": discipline,
                "status": "NO_GEOMETRICALLY_RESOLVED_SUBPATH",
                "candidates_considered": min(len(ranked), max_candidates),
            })
    if executed is None:
        payload = {
            "routing_executed": False,
            "physical_infeasibility_claimed": False,
            "attempts": attempts,
            "blocker": "No candidate produced two distinct free routing terminals inside the cell limit.",
        }
        write_json(output_dir / "routing_execution.json", payload)
        return payload
    return _write_execution(output_dir, files, executed, attempts)


def _public_interface(interface: dict[str, Any]) -> dict[str, Any]:
    retained_port = interface.get("retained_port") or {}
    replaced_port = interface.get("replaced_port") or {}
    return {
        "coordinate_classification": interface["coordinate_classification"],
        "world_m": interface["world_m"],
        "retained_host_global_id": interface["retained_host_global_id"],
        "replaced_host_global_id": interface["replaced_host_global_id"],
        "relation_global_ids": interface["relation_global_ids"],
        "retained_port_global_id": retained_port.get("global_id"),
        "replaced_port_global_id": replaced_port.get("global_id"),
        "ports_agree": interface["ports_agree"],
        "geometric_gap_m": interface["geometric_gap_m"],
        "classification_evidence": interface["classification_evidence"],
        "topology_evidence": interface["topology_evidence"],
        "not_a_native_source_to_terminal_design_request": True,
    }


def _public_terminal(terminal: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": terminal["role"],
        "status": terminal["status"],
        "immutable_interface_m": terminal.get("immutable_interface_m"),
        "coordinate_classification": terminal.get("coordinate_classification"),
        "normalized_coordinates": terminal.get("normalized_coordinates"),
        "containing_index": terminal.get("containing_index"),
        "voxel_center_m": terminal.get("voxel_center_m"),
        "cell_state": terminal.get("cell_state"),
        "clamped": terminal.get("clamped"),
        "projected": terminal.get("projected"),
        "relocated": terminal.get("relocated"),
        "outside_axes": terminal.get("outside_axes"),
        "blocking_obstacles": terminal.get("blocking_obstacles"),
        "port_point_obstacle_hits": terminal.get("port_point_obstacle_hits"),
        "placement": terminal.get("placement"),
        "paired_port_placement": terminal.get("paired_port_placement"),
    }


def _request_and_manifest(files: dict[str, Path], executed: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = executed["prepared"]
    occupancy = executed["occupancy"]
    grid = occupancy["grid"]
    candidate = prepared["candidate"]
    request = {
        "request_kind": "EXISTING_SUBPATH_REPLACEMENT",
        "not_a_native_source_to_terminal_design_request": True,
        "endpoint_policy": "CONTAINING_CELL_NO_RELOCATION",
        "discipline": prepared["discipline"],
        "source_file": prepared["source_file"],
        "system_names": prepared["system_names"],
        "system_global_ids": prepared["system_global_ids"],
        "storeys": prepared["storeys"],
        "replaced_host_global_ids": candidate["replaced_host_global_ids"],
        "boundary_host_global_ids": candidate["boundary_host_global_ids"],
        "start_interface": _public_interface(prepared["start_interface"]),
        "end_interface": _public_interface(prepared["end_interface"]),
        "route_body": {
            "elements": [
                {key: value for key, value in body.items() if key != "profiles"} | {"profile_count": len(body["profiles"])}
                for body in prepared["bodies"]
            ],
            "half_extent_m": prepared["body_half_extent_m"],
            "additional_clearance_m": 0.0,
            "clearance_assumption": "NO_ADDITIONAL_CLEARANCE_BEYOND_MEASURED_BODY_HALF_EXTENT",
            "voxel_quantization_note": (
                "The existing routing grid is 0.1 m. A measured body smaller than one voxel cannot be represented exactly; "
                "obstacle inflation uses the measured half-extent even when it is below the voxel size."
            ),
        },
        "routing_terminal_offset_m": None,
        "routing_terminal_offset_meaning": (
            "Phase 11A.1 does not move an interface toward the replacement volume. "
            "The search node is the containing cell of the immutable interface coordinate."
        ),
        "immutable_interface_coordinates": True,
        "selection_rule": "Same IfcSystem, then same storey, then element count nearest 6, then GlobalId order. Interior hosts are degree-2 IfcFlowSegment or IfcFlowFitting elements.",
    }
    manifest = {
        "voxel_size_m": VOXEL_SIZE_M,
        "router": "src.routing.astar_3d",
        "neighbourhood": ["+X", "-X", "+Y", "-Y", "+Z", "-Z"],
        "roi_bounds_m": occupancy["roi_bounds_m"],
        "roi_margin_m": prepared["roi_margin_m"],
        "grid": {
            "nx": grid.spec.nx, "ny": grid.spec.ny, "nz": grid.spec.nz,
            "origin_m": [grid.spec.origin_x, grid.spec.origin_y, grid.spec.origin_z],
            "cell_count": grid.spec.total_cell_count,
            "free_cell_count": grid.cells.count(FREE),
            "blocked_cell_count": grid.cells.count(BLOCKED_FIXED),
        },
        "obstacle_inflation_m": prepared["body_half_extent_m"],
        "opening_policy": occupancy["opening_policy"],
        "openings_in_roi": [
            {key: value for key, value in opening.items() if key != "bounds_m"} | {"bounds_m": [round(value, 6) for value in opening["bounds_m"]]}
            for opening in occupancy["openings"]
        ],
        "obstacle_class_counts": occupancy["class_counts"],
        "obstacle_count": len(occupancy["obstacles"]),
        "obstacles": [
            {
                "global_id": item["global_id"],
                "ifc_class": item["ifc_class"],
                "file_role": item["file_role"],
                "decision": item["decision"],
                "bounds_m": [round(value, 6) for value in item["bounds_m"]],
                "marked_cell_count": item.get("marked_cell_count", 0),
            }
            for item in occupancy["obstacles"]
        ],
        "replaced_hosts_excluded": occupancy["replaced_host_global_ids_excluded"],
        "endpoint_exclusion_scope": "ONLY_REPLACED_HOSTS",
        "geometry_failures_are_not_treated_as_blocked": True,
        "geometry_failures": occupancy["geometry_failures"],
        "synthetic_building_obstacles_used": False,
        "source_files": {role: path.name for role, path in sorted(files.items())},
    }
    return request, manifest


def _write_diagnostics(output_dir: Path, executed: dict[str, Any], execution: dict[str, Any]) -> None:
    prepared = executed["prepared"]
    start = prepared["start_interface"]["world_m"]
    end = prepared["end_interface"]["world_m"]
    terminals = [_public_terminal(item) for item in executed["terminals"]]
    physical = executed.get("physical")
    diagnostics = {
        "phase": "11A.1",
        "endpoint_policy": "CONTAINING_CELL_NO_RELOCATION",
        "immutable_start_port_m": start,
        "immutable_end_port_m": end,
        "original_port_z_delta_m": float(end[2]) - float(start[2]),
        "length_unit_scale_m": prepared.get("unit_scale_m"),
        "grid_origin_m": execution["grid_origin_m"],
        "voxel_size_m": VOXEL_SIZE_M,
        "terminals": terminals,
        "historical_phase11a": historical_relocation_trace(output_dir),
        "corrected_decision": {
            "astar_invoked": execution["astar_invoked"],
            "endpoint_validity_status": execution["endpoint_validity_status"],
            "search_status": execution["search_status"],
            "start_grid_index": execution.get("start_grid_index"),
            "goal_grid_index": execution.get("goal_grid_index"),
            "start_grid_z_index": execution.get("start_grid_z_index"),
            "goal_grid_z_index": execution.get("goal_grid_z_index"),
            "exact_port_to_port_connection": execution["exact_port_to_port_connection"],
            "grid_search_metrics": execution.get("grid_search_metrics"),
            "physical_route_metrics": execution.get("physical_route_metrics"),
            "physical_route_vertical_travel_m": execution.get("physical_route_vertical_travel_m"),
            "collision_validation": execution.get("collision_validation"),
        },
        "physical_polyline_m": None if physical is None else physical["polyline_m"],
        "terminal_stubs_m": None if physical is None else {
            "start": physical["start_stub_m"],
            "end": physical["end_stub_m"],
        },
    }
    write_json(output_dir / "endpoint_grid_diagnostics.json", diagnostics)


def _write_execution(output_dir: Path, files: dict[str, Path], executed: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    prepared = executed["prepared"]
    result = executed["result"]
    candidate = prepared["candidate"]
    request, manifest = _request_and_manifest(files, executed)
    terminals = [_public_terminal(item) for item in executed["terminals"]]
    start_index = executed["terminals"][0].get("containing_index")
    goal_index = executed["terminals"][1].get("containing_index")
    statuses = [item["status"] for item in executed["terminals"]]
    if executed["endpoint_failure"]:
        validity = next(status for status in statuses if status != ENDPOINT_OK)
        execution = {
            "routing_executed": False,
            "astar_invoked": False,
            "endpoint_policy": "CONTAINING_CELL_NO_RELOCATION",
            "endpoint_validity_status": validity,
            "router": "src.routing.astar_3d",
            "voxel_size_m": VOXEL_SIZE_M,
            "search_completed": False,
            "search_status": "NOT_RUN_ENDPOINT_INVALID",
            "path_found": False,
            "exact_port_to_port_connection": False,
            "physical_infeasibility_claimed": False,
            "physical_clash_freedom_claimed": False,
            "runtime_seconds": None,
            "python_tracemalloc_peak_bytes": None,
            "process_memory": executed["process_memory"],
            "expanded_node_count": None,
            "grid_search_metrics": None,
            "physical_route_metrics": None,
            "physical_route_vertical_travel_m": None,
            "original_port_z_delta_m": request["end_interface"]["world_m"][2] - request["start_interface"]["world_m"][2],
            "grid_origin_m": manifest["grid"]["origin_m"],
            "start_grid_index": start_index,
            "goal_grid_index": goal_index,
            "start_grid_z_index": None if start_index is None else start_index[2],
            "goal_grid_z_index": None if goal_index is None else goal_index[2],
            "terminals": terminals,
            "world_polyline_m": None,
            "path_cells": None,
            "collision_validation": executed["validation"],
            "no_path_diagnosis": None,
            "prior_attempts": attempts,
            "discipline": prepared["discipline"],
            "replaced_host_global_ids": candidate["replaced_host_global_ids"],
            "statement": (
                "The immutable IFC interface coordinates do not both lie in free containing cells. "
                "The search was not started and the terminals were not moved."
            ),
        }
    else:
        physical = executed["physical"]
        physical_metrics = executed["physical_metrics"]
        exact = bool(physical and physical["exact_port_to_port"])
        execution = {
            "routing_executed": True,
            "astar_invoked": True,
            "endpoint_policy": "CONTAINING_CELL_NO_RELOCATION",
            "endpoint_validity_status": ENDPOINT_OK,
            "router": "src.routing.astar_3d",
            "voxel_size_m": VOXEL_SIZE_M,
            "search_completed": True,
            "search_status": "COMPLETED_PATH_FOUND" if result.path_found else "COMPLETED_NO_PATH",
            "path_found": result.path_found,
            "exact_port_to_port_connection": exact,
            "physical_infeasibility_claimed": False,
            "physical_clash_freedom_claimed": False,
            "runtime_seconds": round(executed["runtime_seconds"], 6),
            "python_tracemalloc_peak_bytes": executed["peak_traced_bytes"],
            "process_memory": executed["process_memory"],
            "expanded_node_count": result.expanded_node_count,
            "grid_search_metrics": executed["grid_metrics"],
            "physical_route_metrics": physical_metrics,
            "physical_route_vertical_travel_m": None if physical_metrics is None else physical_metrics["vertical_travel_m"],
            "original_port_z_delta_m": request["end_interface"]["world_m"][2] - request["start_interface"]["world_m"][2],
            "grid_origin_m": manifest["grid"]["origin_m"],
            "start_grid_index": start_index,
            "goal_grid_index": goal_index,
            "start_grid_z_index": start_index[2],
            "goal_grid_z_index": goal_index[2],
            "terminals": terminals,
            "start_stub_m": None if physical is None else physical["start_stub_m"],
            "end_stub_m": None if physical is None else physical["end_stub_m"],
            "world_polyline_m": None if physical is None else [[float(value) for value in point] for point in physical["polyline_m"]],
            "path_cells": [list(cell) for cell in result.path_cells],
            "collision_validation": executed["validation"],
            "no_path_diagnosis": executed["diagnosis"],
            "prior_attempts": attempts,
            "discipline": prepared["discipline"],
            "replaced_host_global_ids": candidate["replaced_host_global_ids"],
        }
    write_json(output_dir / "routing_request.json", request)
    write_json(output_dir / "occupancy_manifest.json", manifest)
    write_json(output_dir / "routing_execution.json", execution)
    _write_diagnostics(output_dir, executed, execution)
    return execution

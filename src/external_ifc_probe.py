"""Read-only probe for external IFC discipline files.

Placement translations from ``get_local_placement`` are in project length
units and are multiplied by the project length scale once. IfcOpenShell world
tessellation (``USE_WORLD_COORDS``) is already in SI metres and is never
multiplied by that scale. Bounding-box overlap is recorded as overlap only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.unit


PROBE_TYPES = (
    "IfcProduct",
    "IfcWall",
    "IfcColumn",
    "IfcBeam",
    "IfcSlab",
    "IfcOpeningElement",
    "IfcDoor",
    "IfcWindow",
    "IfcSpace",
    "IfcDistributionPort",
    "IfcRelConnectsPorts",
    "IfcRelNests",
    "IfcRelVoidsElement",
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcFlowTerminal",
    "IfcFlowController",
    "IfcSystem",
    "IfcBuildingStorey",
)
NATIVE_PORT_PLACEMENT = "NATIVE_PORT_PLACEMENT"
RELATIONSHIP_CONSTRAINED_GEOMETRIC = "RELATIONSHIP_CONSTRAINED_GEOMETRIC"
CONTROLLED_GEOMETRY_ENDPOINT = "CONTROLLED_GEOMETRY_ENDPOINT"
OPENING_POLICY = "CONSERVATIVE_HOST_AABB_RETAINS_VOID_SPACE"
WORLD_MESH_POLICY = "IFCOPENSHELL_USE_WORLD_COORDS_SI_METRES_UNSCALED"
PLACEMENT_POLICY = "GET_LOCAL_PLACEMENT_TRANSLATION_TIMES_PROJECT_LENGTH_SCALE_ONCE"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scale_placement_translation(translation: Iterable[float], unit_scale: float) -> tuple[float, float, float]:
    """Convert one placement translation from project length units into metres."""
    values = tuple(float(value) for value in translation)
    if len(values) != 3:
        raise ValueError("Placement translation must contain three coordinates.")
    scale = float(unit_scale)
    return values[0] * scale, values[1] * scale, values[2] * scale


def world_vertices_metres(vertices: Iterable[float]) -> list[float]:
    """Return kernel world vertices unchanged.

    ``ifcopenshell.geom.create_shape`` with world coordinates already returns
    SI metres. Multiplying by the project length scale shrinks millimetre
    models a second time and is intentionally not done here.
    """
    return [float(value) for value in vertices]


def read_step_header(path: Path, limit: int = 12000) -> dict[str, Any]:
    text = path.read_bytes()[:limit].decode("latin1", errors="replace")
    description = _header_argument(text, "FILE_DESCRIPTION")
    schema = _header_argument(text, "FILE_SCHEMA")
    return {
        "step_physical_file": text.lstrip().startswith("ISO-10303-21;"),
        "file_description": description,
        "file_schema_header": schema,
        "view_definition": description,
    }


def _header_argument(text: str, keyword: str) -> str | None:
    token = f"{keyword}("
    start = text.find(token)
    if start < 0:
        return None
    end = text.find(");", start)
    if end < 0:
        return None
    return text[start:end + 2].replace("\r", "").replace("\n", " ")


def project_length_scale(model: ifcopenshell.file) -> float:
    scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    if not scale > 0:
        raise ValueError("IFC project length scale must be positive.")
    return scale


def length_units(model: ifcopenshell.file) -> list[dict[str, Any]]:
    records = []
    for unit in model.by_type("IfcSIUnit"):
        if str(unit.UnitType) != "LENGTHUNIT":
            continue
        records.append({"ifc_class": unit.is_a(), "prefix": None if unit.Prefix is None else str(unit.Prefix), "name": str(unit.Name)})
    for unit in model.by_type("IfcConversionBasedUnit"):
        if str(unit.UnitType) != "LENGTHUNIT":
            continue
        records.append({"ifc_class": unit.is_a(), "name": unit.Name})
    return records


def placement_origin_m(entity: Any, unit_scale: float) -> tuple[float, float, float] | None:
    """Compose the nested IfcLocalPlacement chain and scale the translation once."""
    placement = getattr(entity, "ObjectPlacement", None)
    if placement is None:
        return None
    matrix = ifcopenshell.util.placement.get_local_placement(placement)
    return scale_placement_translation((matrix[0][3], matrix[1][3], matrix[2][3]), unit_scale)


def local_origin_is_identity(entity: Any, tolerance: float = 1e-3) -> bool:
    placement = getattr(entity, "ObjectPlacement", None)
    if placement is None or not placement.is_a("IfcLocalPlacement") or placement.RelativePlacement is None:
        return True
    coordinates = [float(value) for value in placement.RelativePlacement.Location.Coordinates]
    return all(abs(value) <= tolerance for value in coordinates)


def _count(model: ifcopenshell.file, entity_type: str) -> int:
    try:
        return len(model.by_type(entity_type))
    except RuntimeError:
        return 0


def storey_records(model: ifcopenshell.file, unit_scale: float) -> list[dict[str, Any]]:
    records = []
    for storey in sorted(model.by_type("IfcBuildingStorey"), key=lambda item: item.GlobalId or ""):
        elevation = None if storey.Elevation is None else float(storey.Elevation) * unit_scale
        records.append({
            "global_id": storey.GlobalId,
            "name": storey.Name,
            "elevation_project_units": None if storey.Elevation is None else float(storey.Elevation),
            "elevation_m": elevation,
            "placement_origin_m": _rounded(placement_origin_m(storey, unit_scale)),
        })
    return records


def element_container_names(entity: Any) -> list[str]:
    names = []
    for relationship in getattr(entity, "ContainedInStructure", []) or []:
        structure = relationship.RelatingStructure
        if structure is not None:
            names.append(structure.Name or structure.GlobalId)
    return sorted(set(names))


def port_host_map(model: ifcopenshell.file) -> dict[int, Any]:
    """Map distribution-port entity ids to their explicit host elements."""
    hosts: dict[int, Any] = {}
    for relationship in model.by_type("IfcRelNests"):
        parent = relationship.RelatingObject
        for child in relationship.RelatedObjects or []:
            if child is not None and child.is_a("IfcDistributionPort"):
                hosts[child.id()] = parent
    try:
        port_elements = model.by_type("IfcRelConnectsPortToElement")
    except RuntimeError:
        port_elements = []
    for relationship in port_elements:
        port = relationship.RelatingPort
        element = relationship.RelatedElement
        if port is not None and element is not None:
            hosts.setdefault(port.id(), element)
    return hosts


def explicit_port_connections(model: ifcopenshell.file, hosts: dict[int, Any]) -> list[dict[str, Any]]:
    connections = []
    for relationship in model.by_type("IfcRelConnectsPorts"):
        relating = relationship.RelatingPort
        related = relationship.RelatedPort
        relating_host = None if relating is None else hosts.get(relating.id())
        related_host = None if related is None else hosts.get(related.id())
        connections.append({
            "relation_global_id": relationship.GlobalId,
            "relating_port_global_id": None if relating is None else relating.GlobalId,
            "related_port_global_id": None if related is None else related.GlobalId,
            "relating_host_global_id": None if relating_host is None else relating_host.GlobalId,
            "related_host_global_id": None if related_host is None else related_host.GlobalId,
            "host_association": "EXPLICIT_NEST_OR_PORT_TO_ELEMENT" if relating_host is not None and related_host is not None else "HOST_UNRESOLVED",
        })
    connections.sort(key=lambda item: item["relation_global_id"] or "")
    return connections


def system_records(model: ifcopenshell.file) -> list[dict[str, Any]]:
    records = []
    for system in sorted(model.by_type("IfcSystem"), key=lambda item: item.GlobalId or ""):
        members = []
        for relationship in getattr(system, "IsGroupedBy", []) or []:
            for related in relationship.RelatedObjects or []:
                if related is not None and getattr(related, "GlobalId", None):
                    members.append(related.GlobalId)
        records.append({
            "global_id": system.GlobalId,
            "name": system.Name,
            "ifc_class": system.is_a(),
            "member_count": len(members),
        })
    return records


def opening_relationship_records(model: ifcopenshell.file) -> dict[str, Any]:
    voids = []
    for relationship in sorted(model.by_type("IfcRelVoidsElement"), key=lambda item: item.GlobalId or ""):
        host = relationship.RelatingBuildingElement
        opening = relationship.RelatedOpeningElement
        voids.append({
            "relation_global_id": relationship.GlobalId,
            "host_global_id": None if host is None else host.GlobalId,
            "opening_global_id": None if opening is None else opening.GlobalId,
        })
    return {
        "opening_count": _count(model, "IfcOpeningElement"),
        "void_relationship_count": len(voids),
        "void_relationships_sample": voids[:20],
        "opening_policy": OPENING_POLICY,
        "opening_policy_note": (
            "Openings and void relationships are inventoried. Host occupancy keeps the host AABB. "
            "Opening solids are not subtracted, because subtracting an opening AABB can mark solid material as free."
        ),
    }


def port_placement_summary(model: ifcopenshell.file, unit_scale: float, sample_limit: int = 5) -> dict[str, Any]:
    identity = offset = missing = 0
    samples = []
    for port in sorted(model.by_type("IfcDistributionPort"), key=lambda item: item.GlobalId or ""):
        if port.ObjectPlacement is None:
            missing += 1
            continue
        if local_origin_is_identity(port):
            identity += 1
        else:
            offset += 1
        if len(samples) < sample_limit:
            samples.append({
                "global_id": port.GlobalId,
                "local_origin_project_units": _local_origin(port),
                "world_origin_m": _rounded(placement_origin_m(port, unit_scale)),
                "local_origin_identity": local_origin_is_identity(port),
            })
    return {
        "port_count": identity + offset + missing,
        "identity_local_origin_count": identity,
        "nonzero_local_origin_count": offset,
        "missing_placement_count": missing,
        "samples": samples,
        "native_placement_rule": (
            "A port coordinate is NATIVE_PORT_PLACEMENT only when its composed world point uses a non-identity "
            "local origin and lies near the host mesh. An identity local origin is not treated as a connection point."
        ),
    }


def _local_origin(entity: Any) -> list[float] | None:
    placement = getattr(entity, "ObjectPlacement", None)
    if placement is None or not placement.is_a("IfcLocalPlacement") or placement.RelativePlacement is None:
        return None
    return [float(value) for value in placement.RelativePlacement.Location.Coordinates]


def world_settings() -> Any:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    return settings


def _aabb_from_vertices(vertices: list[float]) -> dict[str, float] | None:
    if len(vertices) < 3:
        return None
    xs, ys, zs = vertices[0::3], vertices[1::3], vertices[2::3]
    return {
        "min_x_m": min(xs), "max_x_m": max(xs),
        "min_y_m": min(ys), "max_y_m": max(ys),
        "min_z_m": min(zs), "max_z_m": max(zs),
    }


def bounds_tuple(bounds: dict[str, float]) -> tuple[float, float, float, float, float, float]:
    return (
        bounds["min_x_m"], bounds["max_x_m"], bounds["min_y_m"], bounds["max_y_m"], bounds["min_z_m"], bounds["max_z_m"],
    )


def collect_world_aabbs(model: ifcopenshell.file, *, threads: int = 4) -> dict[str, Any]:
    """Tessellate products to world-metre AABBs without rescaling kernel vertices."""
    products = [item for item in model.by_type("IfcProduct") if getattr(item, "Representation", None)]
    products.sort(key=lambda item: item.GlobalId or "")
    found: dict[str, dict[str, Any]] = {}
    failures = []
    iterator = ifcopenshell.geom.iterator(
        world_settings(), model, max(1, threads), include=products,
    )
    if iterator.initialize():
        while True:
            shape = iterator.get()
            vertices = world_vertices_metres(shape.geometry.verts)
            bounds = _aabb_from_vertices(vertices)
            if bounds is None:
                failures.append({"global_id": shape.guid, "reason": "empty world mesh"})
            else:
                element = model.by_guid(shape.guid)
                found[shape.guid] = {
                    "global_id": shape.guid,
                    "ifc_class": None if element is None else element.is_a(),
                    "name": None if element is None else element.Name,
                    "bounds_m": {key: round(value, 6) for key, value in bounds.items()},
                    "vertex_count": len(vertices) // 3,
                }
            if not iterator.next():
                break
    for product in products:
        if product.GlobalId not in found and not any(item["global_id"] == product.GlobalId for item in failures):
            failures.append({"global_id": product.GlobalId, "ifc_class": product.is_a(), "reason": "iterator omitted element"})
    combined = _combine_bounds(item["bounds_m"] for item in found.values())
    return {
        "product_with_representation_count": len(products),
        "tessellated_count": len(found),
        "geometry_failure_count": len(failures),
        "geometry_failure_sample": failures[:40],
        "world_bounds_m": combined,
        "elements": found,
        "coordinate_policy": WORLD_MESH_POLICY,
    }


def placement_mesh_consistency(
    model: ifcopenshell.file, unit_scale: float, elements: dict[str, dict[str, Any]], sample_size: int = 8,
) -> dict[str, Any]:
    """Check that one placement-scale application agrees with unscaled world meshes."""
    samples = []
    inconsistent = 0
    for global_id in sorted(elements)[:sample_size]:
        entity = model.by_guid(global_id)
        origin = None if entity is None else placement_origin_m(entity, unit_scale)
        bounds = elements[global_id]["bounds_m"]
        contains_scaled = origin is not None and _point_in_expanded_bounds(origin, bounds, 1.0)
        unscaled = None if origin is None else tuple(value / unit_scale for value in origin)
        contains_unscaled = unscaled is not None and _point_in_expanded_bounds(unscaled, bounds, 1.0)
        if origin is not None and not contains_scaled:
            inconsistent += 1
        samples.append({
            "global_id": global_id,
            "placement_origin_m": _rounded(origin),
            "mesh_contains_scaled_origin": contains_scaled,
            "mesh_contains_unscaled_project_origin": contains_unscaled,
        })
    return {
        "sample_count": len(samples),
        "scaled_origin_outside_mesh_count": inconsistent,
        "double_project_scale_applied": False,
        "samples": samples,
        "interpretation": (
            "Agreement between the once-scaled placement origin and the world mesh supports the metre frame. "
            "It does not by itself verify federation with another discipline file."
        ),
    }


def probe_model(path: Path, *, tessellate: bool = True, threads: int = 4) -> dict[str, Any]:
    model = ifcopenshell.open(str(path))
    unit_scale = project_length_scale(model)
    hosts = port_host_map(model)
    connections = explicit_port_connections(model, hosts)
    resolved = sum(item["host_association"] == "EXPLICIT_NEST_OR_PORT_TO_ELEMENT" for item in connections)
    projects = model.by_type("IfcProject")
    sites = model.by_type("IfcSite")
    buildings = model.by_type("IfcBuilding")
    geometry = collect_world_aabbs(model, threads=threads) if tessellate else None
    elements = {} if geometry is None else geometry["elements"]
    record = {
        "file_name": path.name,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "header": read_step_header(path),
        "schema": model.schema,
        "length_unit_scale_m": unit_scale,
        "length_units": length_units(model),
        "project_global_id": None if not projects else projects[0].GlobalId,
        "site_placement_origin_m": None if not sites else _rounded(placement_origin_m(sites[0], unit_scale)),
        "building_placement_origin_m": None if not buildings else _rounded(placement_origin_m(buildings[0], unit_scale)),
        "storeys": storey_records(model, unit_scale),
        "counts": {name: _count(model, name) for name in PROBE_TYPES},
        "ports": port_placement_summary(model, unit_scale),
        "explicit_port_connections": {
            "count": len(connections),
            "both_hosts_resolved_count": resolved,
            "host_unresolved_count": len(connections) - resolved,
            "sample": connections[:10],
        },
        "systems": system_records(model),
        "openings": opening_relationship_records(model),
        "placement_policy": PLACEMENT_POLICY,
        "world_mesh_policy": WORLD_MESH_POLICY,
    }
    if geometry is not None:
        geometry_public = {key: value for key, value in geometry.items() if key != "elements"}
        record["geometry"] = geometry_public
        record["placement_mesh_consistency"] = placement_mesh_consistency(model, unit_scale, elements)
    return record


def federation_assessment(files: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compare discipline frames. Bounds overlap is necessary context, not verification."""
    roles = sorted(files)
    overlaps = []
    for index, left in enumerate(roles):
        for right in roles[index + 1:]:
            left_bounds = (files[left].get("geometry") or {}).get("world_bounds_m")
            right_bounds = (files[right].get("geometry") or {}).get("world_bounds_m")
            overlaps.append({
                "left": left,
                "right": right,
                "bounds_overlap": left_bounds is not None and right_bounds is not None and _bounds_overlap(left_bounds, right_bounds),
            })
    storey_names: dict[str, dict[str, float | None]] = {}
    for role, record in files.items():
        storey_names[role] = {item["name"]: item["elevation_m"] for item in record["storeys"]}
    shared_names = set.intersection(*(set(names) for names in storey_names.values())) if storey_names else set()
    elevation_matches = []
    for name in sorted(shared_names):
        values = {role: storey_names[role][name] for role in roles}
        numeric = [value for value in values.values() if value is not None]
        elevation_matches.append({
            "storey_name": name,
            "elevation_m_by_file": values,
            "max_abs_difference_m": None if len(numeric) < 2 else round(max(numeric) - min(numeric), 6),
        })
    origins = {role: record.get("site_placement_origin_m") for role, record in files.items()}
    any_overlap = any(item["bounds_overlap"] for item in overlaps)
    status = "INSUFFICIENT_GEOMETRY" if not any(item["bounds_overlap"] is True or item["bounds_overlap"] is False for item in overlaps) else (
        "PLACEMENT_AND_BOUNDS_COMPARED_NOT_GEOMETRICALLY_VERIFIED" if any_overlap else "NO_WORLD_BOUNDS_OVERLAP"
    )
    return {
        "status": status,
        "verified_federation": False,
        "bounds_overlap_establishes_verified_federation": False,
        "pairwise_bounds_overlap": overlaps,
        "site_placement_origin_m": origins,
        "shared_storey_elevations": elevation_matches,
        "note": "Matching origins, storey elevations, or bounding-box overlap do not verify element-level federation.",
    }


def _combine_bounds(bounds_list: Iterable[dict[str, float]]) -> dict[str, list[float]] | None:
    items = list(bounds_list)
    if not items:
        return None
    return {
        "min_m": [
            min(item["min_x_m"] for item in items),
            min(item["min_y_m"] for item in items),
            min(item["min_z_m"] for item in items),
        ],
        "max_m": [
            max(item["max_x_m"] for item in items),
            max(item["max_y_m"] for item in items),
            max(item["max_z_m"] for item in items),
        ],
    }


def _bounds_overlap(left: dict[str, list[float]], right: dict[str, list[float]]) -> bool:
    return all(left["min_m"][axis] <= right["max_m"][axis] and right["min_m"][axis] <= left["max_m"][axis] for axis in range(3))


def _point_in_expanded_bounds(point: tuple[float, float, float], bounds: dict[str, float], margin: float) -> bool:
    return (
        bounds["min_x_m"] - margin <= point[0] <= bounds["max_x_m"] + margin
        and bounds["min_y_m"] - margin <= point[1] <= bounds["max_y_m"] + margin
        and bounds["min_z_m"] - margin <= point[2] <= bounds["max_z_m"] + margin
    )


def _rounded(point: tuple[float, float, float] | None, digits: int = 6) -> list[float] | None:
    if point is None:
        return None
    return [round(float(value), digits) for value in point]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_thread_count() -> int:
    return max(1, min(4, os.cpu_count() or 1))

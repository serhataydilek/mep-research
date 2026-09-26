"""Deterministic, read-only sanity inspection for supplied IFC files.

This module describes compatibility with the completed research prototype.  It
does not alter benchmark inputs, routing semantics, or IFC source files.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from math import ceil, isfinite
from pathlib import Path
from typing import Any, Iterable

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.unit


STATUS_VALUES = ("PASS", "PARTIAL", "FAIL", "NOT_APPLICABLE")
INVENTORY_CLASSES = (
    "IfcSlab", "IfcWall", "IfcColumn", "IfcBeam", "IfcRoof", "IfcDoor",
    "IfcWindow", "IfcSpace", "IfcOpeningElement", "IfcBuildingElementProxy",
    "IfcPipeSegment", "IfcPipeFitting", "IfcDuctSegment", "IfcDuctFitting",
    "IfcCableCarrierSegment", "IfcFlowSegment", "IfcFlowFitting",
    "IfcDistributionElement",
)
ARCHITECTURAL_CLASSES = (
    "IfcSlab", "IfcWall", "IfcColumn", "IfcBeam", "IfcRoof", "IfcDoor",
    "IfcWindow", "IfcSpace", "IfcOpeningElement", "IfcBuildingElementProxy",
)
MEP_CLASSES = (
    "IfcPipeSegment", "IfcPipeFitting", "IfcDuctSegment", "IfcDuctFitting",
    "IfcCableCarrierSegment", "IfcFlowSegment", "IfcFlowFitting",
    "IfcDistributionElement",
)
DIRECT_OBSTACLE_CLASSES = ("IfcWall", "IfcColumn")
IGNORED_POLICY_CLASSES = ("IfcSlab", "IfcDoor", "IfcWindow", "IfcOpeningElement")


@dataclass(frozen=True)
class ValidationOptions:
    voxel_size_m: float = 0.1
    max_voxel_cells: int = 25_000_000
    large_coordinate_m: float = 100_000.0


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


def _status(status: str, reason: str | None = None) -> dict[str, str]:
    if status not in STATUS_VALUES:
        raise ValueError(f"Unknown validation status: {status}")
    result = {"status": status}
    if status in ("PARTIAL", "FAIL"):
        result["reason"] = reason or "No reason supplied."
    return result


def _by_type(model: ifcopenshell.file, name: str) -> list[Any]:
    try:
        return list(model.by_type(name))
    except RuntimeError:
        return []


def _unit_details(model: ifcopenshell.file) -> dict[str, Any]:
    scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    assignments = _by_type(model, "IfcUnitAssignment")
    length_units = []
    for assignment in assignments:
        for unit in assignment.Units or ():
            if getattr(unit, "UnitType", None) != "LENGTHUNIT":
                continue
            if unit.is_a("IfcSIUnit"):
                prefix = getattr(unit, "Prefix", None)
                name = str(getattr(unit, "Name", "METRE"))
                label = f"{prefix}_{name}" if prefix else name
            else:
                label = getattr(unit, "Name", None) or unit.is_a()
            length_units.append(str(label))
    return {
        "length_unit": length_units[0] if length_units else None,
        "length_units_declared": sorted(set(length_units)),
        "conversion_factor_to_meters": _round(scale),
        "source": "IfcProject.UnitsInContext via ifcopenshell.util.unit",
    }


def _shape_bounds(element: Any, settings: Any) -> tuple[float, float, float, float, float, float]:
    shape = ifcopenshell.geom.create_shape(settings, element)
    vertices = shape.geometry.verts
    if not vertices:
        raise ValueError("empty mesh")
    coordinates = [float(value) for value in vertices]
    if not all(isfinite(value) for value in coordinates):
        raise ValueError("invalid non-finite bounds")
    bounds = (
        min(coordinates[::3]), max(coordinates[::3]),
        min(coordinates[1::3]), max(coordinates[1::3]),
        min(coordinates[2::3]), max(coordinates[2::3]),
    )
    if bounds[0] > bounds[1] or bounds[2] > bounds[3] or bounds[4] > bounds[5]:
        raise ValueError("invalid bounds")
    return tuple(_round(value) for value in bounds)


def _geometry(model: ifcopenshell.file) -> tuple[dict[str, Any], dict[int, tuple[float, ...]]]:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    entities: dict[int, Any] = {}
    for class_name in ARCHITECTURAL_CLASSES:
        for entity in _by_type(model, class_name):
            entities[entity.id()] = entity
    bounds_by_id: dict[int, tuple[float, ...]] = {}
    failures: Counter[str] = Counter()
    without_representation = 0
    empty_meshes = 0
    invalid_bounds = 0
    class_results: dict[str, Counter[str]] = {}
    for entity in sorted(entities.values(), key=lambda item: (item.is_a(), item.id())):
        class_counter = class_results.setdefault(entity.is_a(), Counter())
        class_counter["considered"] += 1
        if not getattr(entity, "Representation", None):
            without_representation += 1
            class_counter["without_representation"] += 1
            continue
        try:
            bounds_by_id[entity.id()] = _shape_bounds(entity, settings)
            class_counter["usable_geometry"] += 1
        except Exception as exc:  # IFC kernels expose several exception types
            reason = f"{type(exc).__name__}: {str(exc).strip() or 'no message'}"
            failures[reason] += 1
            class_counter["geometry_extraction_failures"] += 1
            if "empty mesh" in reason:
                empty_meshes += 1
            if "invalid" in reason:
                invalid_bounds += 1
    aggregate = None
    if bounds_by_id:
        values = list(bounds_by_id.values())
        aggregate = {
            "min_x_m": _round(min(item[0] for item in values)),
            "min_y_m": _round(min(item[2] for item in values)),
            "min_z_m": _round(min(item[4] for item in values)),
            "max_x_m": _round(max(item[1] for item in values)),
            "max_y_m": _round(max(item[3] for item in values)),
            "max_z_m": _round(max(item[5] for item in values)),
        }
        aggregate["extent_x_m"] = _round(aggregate["max_x_m"] - aggregate["min_x_m"])
        aggregate["extent_y_m"] = _round(aggregate["max_y_m"] - aggregate["min_y_m"])
        aggregate["extent_z_m"] = _round(aggregate["max_z_m"] - aggregate["min_z_m"])
    considered = len(entities)
    usable = len(bounds_by_id)
    summary = {
        "coordinate_basis": "world coordinates emitted by IfcOpenShell geometry",
        "total_entities_considered": considered,
        "entities_with_usable_geometry": usable,
        "entities_without_representation": without_representation,
        "geometry_extraction_failures": sum(failures.values()),
        "empty_meshes": empty_meshes,
        "invalid_bounds": invalid_bounds,
        "unsupported_entity_types": [],
        "exceptions_by_reason": dict(sorted(failures.items())),
        "by_entity_class": {
            name: dict(sorted(counts.items())) for name, counts in sorted(class_results.items())
        },
        "aggregate_bounds_m": aggregate,
    }
    return summary, bounds_by_id


def _storey_for(entity: Any) -> Any | None:
    for relationship in getattr(entity, "ContainedInStructure", ()) or ():
        structure = getattr(relationship, "RelatingStructure", None)
        if structure and structure.is_a("IfcBuildingStorey"):
            return structure
    for relationship in getattr(entity, "Decomposes", ()) or ():
        parent = getattr(relationship, "RelatingObject", None)
        if parent and parent.is_a("IfcBuildingStorey"):
            return parent
    return None


def _spatial(model: ifcopenshell.file, bounds_by_id: dict[int, tuple[float, ...]], options: ValidationOptions) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    local_placements = _by_type(model, "IfcLocalPlacement")
    nested = sum(getattr(item, "PlacementRelTo", None) is not None for item in local_placements)
    storeys = sorted(
        _by_type(model, "IfcBuildingStorey"),
        key=lambda item: ((float(item.Elevation) if item.Elevation is not None else float("inf")), item.id()),
    )
    rows = []
    elevations = []
    for storey in storeys:
        elevation = float(storey.Elevation) if storey.Elevation is not None else None
        if elevation is not None:
            elevations.append(elevation)
        contained = []
        class_counts: Counter[str] = Counter()
        for class_name in ARCHITECTURAL_CLASSES:
            for entity in _by_type(model, class_name):
                if _storey_for(entity) == storey:
                    contained.append(entity)
                    class_counts[entity.is_a()] += 1
        entity_bounds = [bounds_by_id[item.id()] for item in contained if item.id() in bounds_by_id]
        z_range = None
        if entity_bounds:
            z_range = {
                "min_z_m": _round(min(item[4] for item in entity_bounds)),
                "max_z_m": _round(max(item[5] for item in entity_bounds)),
            }
        rows.append({
            "id": storey.id(), "name": storey.Name, "elevation_declared": elevation,
            "entity_counts": dict(sorted(class_counts.items())), "geometry_z_range_m": z_range,
        })
    all_values = [value for bounds in bounds_by_id.values() for value in bounds]
    large = any(abs(value) >= options.large_coordinate_m for value in all_values)
    sensible = True
    sensible_reason = None
    if elevations != sorted(elevations) or any(not isfinite(value) for value in elevations):
        sensible = False
        sensible_reason = "Declared storey elevations are non-finite or not monotonically ordered."
    aggregate = None
    if bounds_by_id:
        aggregate = (
            max(item[1] for item in bounds_by_id.values()) - min(item[0] for item in bounds_by_id.values()),
            max(item[3] for item in bounds_by_id.values()) - min(item[2] for item in bounds_by_id.values()),
            max(item[5] for item in bounds_by_id.values()) - min(item[4] for item in bounds_by_id.values()),
        )
    coherent = bool(aggregate and all(isfinite(value) and value > 0 for value in aggregate))
    spatial = {
        "local_placements_present": bool(local_placements),
        "local_placement_count": len(local_placements),
        "nested_local_placement_count": nested,
        "large_coordinate_threshold_m": options.large_coordinate_m,
        "large_coordinate_offsets_observed": large,
        "georeferencing_entities": {
            "IfcMapConversion": len(_by_type(model, "IfcMapConversion")),
            "IfcProjectedCRS": len(_by_type(model, "IfcProjectedCRS")),
        },
        "storey_elevations_sensible": sensible,
        "storey_elevation_reason": sensible_reason,
        "geometry_spatially_coherent": coherent,
        "recentered": False,
    }
    return spatial, rows


def _obstacle_compatibility(model: ifcopenshell.file, bounds_by_id: dict[int, tuple[float, ...]]) -> dict[str, Any]:
    counts: dict[str, Counter[str]] = {}
    for class_name in ARCHITECTURAL_CLASSES:
        for entity in _by_type(model, class_name):
            actual = entity.is_a()
            row = counts.setdefault(actual, Counter())
            if entity.id() not in bounds_by_id:
                row["failed_geometry_extraction"] += 1
            elif entity.is_a(DIRECT_OBSTACLE_CLASSES[0]) or entity.is_a(DIRECT_OBSTACLE_CLASSES[1]):
                row["directly_supported"] += 1
            elif entity.is_a("IfcSpace") and entity.Name == "Service Shaft 0":
                row["directly_supported_as_synthetic_shaft_reservation"] += 1
            elif entity.is_a("IfcSpace"):
                row["ignored_by_current_policy"] += 1
            elif any(entity.is_a(name) for name in IGNORED_POLICY_CLASSES):
                row["ignored_by_current_policy"] += 1
            else:
                row["supported_through_generic_geometry_extraction"] += 1
    totals: Counter[str] = Counter()
    for row in counts.values():
        totals.update(row)
    return {
        "current_policy": "IfcWall and IfcColumn world-coordinate AABBs only; named shaft-space reservation is synthetic-specific.",
        "aabb_limitation": "AABBs preserve current benchmark semantics but over-approximate rotated or curved geometry.",
        "totals": dict(sorted(totals.items())),
        "by_entity_class": {name: dict(sorted(row.items())) for name, row in sorted(counts.items())},
    }


def _voxelize(model: ifcopenshell.file, bounds_by_id: dict[int, tuple[float, ...]], options: ValidationOptions) -> dict[str, Any]:
    obstacle_ids = {
        entity.id() for class_name in DIRECT_OBSTACLE_CLASSES for entity in _by_type(model, class_name)
    }
    obstacles = [bounds for entity_id, bounds in bounds_by_id.items() if entity_id in obstacle_ids]
    shaft_spaces = [
        bounds_by_id[space.id()] for space in _by_type(model, "IfcSpace")
        if space.Name == "Service Shaft 0" and space.id() in bounds_by_id
    ]
    base = {
        "voxel_resolution_m": options.voxel_size_m,
        "max_voxel_cells": options.max_voxel_cells,
        "obstacle_semantics": "validation-only raster of current-policy IfcWall/IfcColumn AABBs and the synthetic named shaft reservation when present",
        "coordinate_transform": "grid origin is the minimum obstacle bound; original world bounds are retained",
    }
    if not obstacles:
        return {**base, "processing_success": False, "failure_reason": "No usable current-policy obstacle geometry.", "operationally_reasonable": False}
    mins = (min(item[0] for item in obstacles), min(item[2] for item in obstacles), min(item[4] for item in obstacles))
    maxs = (max(item[1] for item in obstacles), max(item[3] for item in obstacles), max(item[5] for item in obstacles))
    extents = tuple(maxs[index] - mins[index] for index in range(3))
    dimensions = tuple(max(1, ceil(value / options.voxel_size_m)) for value in extents)
    total = dimensions[0] * dimensions[1] * dimensions[2]
    world_bounds = {
        "min_x_m": _round(mins[0]), "min_y_m": _round(mins[1]), "min_z_m": _round(mins[2]),
        "max_x_m": _round(maxs[0]), "max_y_m": _round(maxs[1]), "max_z_m": _round(maxs[2]),
    }
    result = {**base, "world_bounds_m": world_bounds, "grid_origin_m": [_round(value) for value in mins],
              "grid_dimensions": {"nx": dimensions[0], "ny": dimensions[1], "nz": dimensions[2]},
              "total_voxel_count": total}
    if total > options.max_voxel_cells:
        return {**result, "processing_success": False,
                "failure_reason": f"Dense grid would require {total} cells, exceeding safety limit {options.max_voxel_cells}.",
                "operationally_reasonable": False}
    cells = bytearray(total)
    nx, ny, nz = dimensions
    for bounds in obstacles:
        starts = [max(0, int((bounds[index * 2] - mins[index]) // options.voxel_size_m)) for index in range(3)]
        ends = [min(dimensions[index] - 1, ceil((bounds[index * 2 + 1] - mins[index]) / options.voxel_size_m) - 1) for index in range(3)]
        for k in range(starts[2], ends[2] + 1):
            for j in range(starts[1], ends[1] + 1):
                offset = (k * ny + j) * nx
                for i in range(starts[0], ends[0] + 1):
                    cells[offset + i] = 1
    for bounds in shaft_spaces:
        starts = [max(0, int((bounds[index * 2] - mins[index]) // options.voxel_size_m)) for index in range(3)]
        ends = [min(dimensions[index] - 1, ceil((bounds[index * 2 + 1] - mins[index]) / options.voxel_size_m) - 1) for index in range(3)]
        for k in range(starts[2], ends[2] + 1):
            for j in range(starts[1], ends[1] + 1):
                offset = (k * ny + j) * nx
                for i in range(starts[0], ends[0] + 1):
                    if cells[offset + i] == 0:
                        cells[offset + i] = 2
    fixed = cells.count(1)
    reserved = cells.count(2)
    occupied = fixed + reserved
    return {**result, "processing_success": True, "failure_reason": None,
            "fixed_obstacle_voxel_count": fixed, "reserved_shaft_voxel_count": reserved,
            "occupied_voxel_count": occupied, "occupied_ratio": _round(occupied / total),
            "operationally_reasonable": True}


def _mep(model: ifcopenshell.file) -> dict[str, Any]:
    inventory = {name: len(_by_type(model, name)) for name in MEP_CLASSES}
    unique: dict[int, Any] = {}
    for name in MEP_CLASSES:
        for entity in _by_type(model, name):
            unique[entity.id()] = entity
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    usable = 0
    failures = 0
    for entity in sorted(unique.values(), key=lambda item: item.id()):
        if not getattr(entity, "Representation", None):
            continue
        try:
            _shape_bounds(entity, settings)
            usable += 1
        except Exception:
            failures += 1
    classified = 0
    for entity in unique.values():
        if getattr(entity, "PredefinedType", None) not in (None, "NOTDEFINED"):
            classified += 1
        elif getattr(entity, "HasAssignments", None):
            classified += 1
    return {
        "counts_are_inheritance_aware_and_overlap": True,
        "entity_counts": inventory,
        "unique_distribution_entities": len(unique),
        "entities_with_usable_geometry": usable,
        "geometry_extraction_failures": failures,
        "entities_with_system_or_type_classification_metadata": classified,
        "directly_consumable_by_current_router": False,
        "router_note": "The router consumes separate benchmark demand endpoints and voxel paths, not existing IFC distribution elements.",
    }


def inspect_ifc(path: Path, *, sample_id: str | None = None, sample_kind: str = "EXTERNAL_IFC", options: ValidationOptions | None = None) -> dict[str, Any]:
    """Inspect one IFC without mutating it; malformed files return a FAIL report."""
    options = options or ValidationOptions()
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"IFC file does not exist: {path}")
    metadata = {"sample_id": sample_id or path.stem, "original_filename": path.name,
                "path": str(path), "sample_kind": sample_kind}
    try:
        if b"ISO-10303-21" not in path.read_bytes()[:1024].upper():
            raise ValueError("missing ISO-10303-21 STEP header")
        model = ifcopenshell.open(str(path))
    except Exception as exc:
        reason = f"{type(exc).__name__}: {str(exc).strip() or 'unable to parse IFC'}"
        return {
            "metadata": metadata, "schema": None,
            "pipeline_stages": {name: _status("FAIL", reason) if name == "file_parsing" else _status("NOT_APPLICABLE")
                                for name in ("file_parsing", "schema_recognition", "unit_interpretation", "spatial_hierarchy", "architectural_geometry_extraction", "obstacle_extraction", "storey_mapping", "voxelization", "MEP_entity_inspection", "ready_for_current_research_router_input")},
            "limitations_and_warnings": [reason], "router_readiness": {"router_ready": False, "reasons": [reason]},
        }
    schema = str(model.schema)
    hierarchy = {"project_count": len(_by_type(model, "IfcProject")), "site_count": len(_by_type(model, "IfcSite")),
                 "building_count": len(_by_type(model, "IfcBuilding")), "building_storey_count": len(_by_type(model, "IfcBuildingStorey"))}
    units = _unit_details(model)
    inventory = {name: len(_by_type(model, name)) for name in INVENTORY_CLASSES}
    geometry, bounds_by_id = _geometry(model)
    spatial, storeys = _spatial(model, bounds_by_id, options)
    obstacles = _obstacle_compatibility(model, bounds_by_id)
    voxel = _voxelize(model, bounds_by_id, options)
    mep = _mep(model)
    warnings = [
        "Entity inventory uses inheritance-aware IfcOpenShell by_type counts; superclass and subclass counts overlap.",
        "Current research obstacle extraction is limited to wall/column AABBs and a synthetic name-based shaft convention.",
    ]
    if sample_kind == "CONTROL_GENERATED_IFC":
        warnings.append("This generated control is regression evidence only, not external IFC evidence.")
    if spatial["large_coordinate_offsets_observed"]:
        warnings.append("Large world-coordinate offsets were observed; no arbitrary recentering was applied.")
    router_reasons = []
    if not bounds_by_id:
        router_reasons.append("No usable architectural geometry was extracted.")
    if not voxel.get("processing_success"):
        router_reasons.append("A safe validation voxel grid could not be produced.")
    if not storeys:
        router_reasons.append("No identifiable IfcBuildingStorey scope exists.")
    router_reasons.append("No compatible source/target routing endpoints are supplied by this inspection; demands are not invented.")
    stages = {
        "file_parsing": _status("PASS"),
        "schema_recognition": _status("PASS" if schema.upper().startswith(("IFC2X3", "IFC4")) else "PARTIAL", f"Schema {schema} is parsed but is not an explicitly expected IFC2X3/IFC4 family."),
        "unit_interpretation": _status("PASS" if units["length_unit"] and units["conversion_factor_to_meters"] > 0 else "FAIL", "A valid project length unit and metre conversion were not available."),
        "spatial_hierarchy": _status("PASS" if hierarchy["project_count"] and hierarchy["building_count"] and hierarchy["building_storey_count"] else "PARTIAL", "Project/building/storey hierarchy is incomplete."),
        "architectural_geometry_extraction": _status("PASS" if geometry["total_entities_considered"] and geometry["entities_with_usable_geometry"] == geometry["total_entities_considered"] else ("PARTIAL" if geometry["entities_with_usable_geometry"] else "FAIL"), "Some or all relevant architectural entities lack usable geometry."),
        "obstacle_extraction": _status("PASS" if obstacles["totals"].get("directly_supported", 0) else "PARTIAL", "No usable IfcWall/IfcColumn geometry matches current obstacle policy."),
        "storey_mapping": _status("PASS" if storeys and any(row["entity_counts"] for row in storeys) else "PARTIAL", "Storeys or contained architectural elements could not be mapped."),
        "voxelization": _status("PASS" if voxel.get("processing_success") else "FAIL", voxel.get("failure_reason")),
        "MEP_entity_inspection": _status("PASS" if mep["unique_distribution_entities"] else "NOT_APPLICABLE"),
        "ready_for_current_research_router_input": _status("FAIL", "; ".join(router_reasons)),
    }
    return {
        "metadata": metadata, "schema": schema, "ifc_hierarchy": hierarchy, "units": units,
        "entity_inventory": {"counting_mode": "inheritance-aware; counts can overlap", "counts": inventory},
        "geometry_summary": geometry, "spatial_summary": spatial, "storeys": storeys,
        "obstacle_compatibility": obstacles, "voxelization_summary": voxel, "mep_content": mep,
        "pipeline_stages": stages,
        "router_readiness": {"router_ready": False, "minimum_conditions": ["meter-normalized bounds", "usable obstacle geometry", "valid routing volume", "reasonable voxel dimensions", "identifiable storey scope", "deterministic source/target coordinate mapping"], "reasons": router_reasons},
        "limitations_and_warnings": warnings,
    }


def build_validation_report(paths: Iterable[Path], *, control_paths: Iterable[Path] = (), options: ValidationOptions | None = None) -> dict[str, Any]:
    controls = {Path(path).resolve() for path in control_paths}
    samples = [inspect_ifc(Path(path), sample_kind="CONTROL_GENERATED_IFC" if Path(path).resolve() in controls else "EXTERNAL_IFC", options=options) for path in paths]
    return {"report_type": "REAL_IFC_SANITY_VALIDATION", "format_version": 1,
            "research_checkpoint": "5b8130d59486f4849af6856a62dff1c3276ead69",
            "benchmark_statement": "External compatibility findings do not alter the completed Phase 8 benchmark or its metrics.",
            "samples": samples}


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def markdown_report(report: dict[str, Any]) -> str:
    samples = report["samples"]
    lines = ["# Real IFC Sanity Validation", "", "## Scope", "", report["benchmark_statement"], "", "## Samples", ""]
    if not samples:
        lines.append("No IFC samples were supplied.")
    for sample in samples:
        meta = sample["metadata"]
        lines.extend([f"### {meta['sample_id']}", "", f"- Kind: `{meta['sample_kind']}`", f"- File: `{meta['original_filename']}`", f"- Schema: `{sample.get('schema') or 'unavailable'}`", ""])
    sections = (
        ("Schema and Units", lambda s: f"{s.get('schema') or 'unavailable'}; {s.get('units', {}).get('length_unit') or 'unit unavailable'}; metre factor {s.get('units', {}).get('conversion_factor_to_meters', 'unavailable')}"),
        ("Spatial Structure", lambda s: json.dumps(s.get("ifc_hierarchy", {}), sort_keys=True)),
        ("Architectural Geometry", lambda s: f"usable {s.get('geometry_summary', {}).get('entities_with_usable_geometry', 0)}/{s.get('geometry_summary', {}).get('total_entities_considered', 0)}; failures {s.get('geometry_summary', {}).get('geometry_extraction_failures', 0)}"),
        ("Obstacle Compatibility", lambda s: json.dumps(s.get("obstacle_compatibility", {}).get("totals", {}), sort_keys=True)),
        ("Voxelization", lambda s: f"success={str(s.get('voxelization_summary', {}).get('processing_success', False)).lower()}; dimensions={json.dumps(s.get('voxelization_summary', {}).get('grid_dimensions'), sort_keys=True)}"),
        ("Existing MEP Content", lambda s: json.dumps(s.get("mep_content", {}).get("entity_counts", {}), sort_keys=True)),
        ("Router Readiness", lambda s: f"router_ready={str(s.get('router_readiness', {}).get('router_ready', False)).lower()}; reasons={'; '.join(s.get('router_readiness', {}).get('reasons', []))}"),
    )
    for heading, render in sections:
        lines.extend([f"## {heading}", ""])
        for sample in samples:
            lines.append(f"- `{sample['metadata']['sample_id']}`: {render(sample)}")
        lines.append("")
    lines.extend(["## Unsupported / Partial Features", ""])
    for sample in samples:
        for name, status in sample.get("pipeline_stages", {}).items():
            if status["status"] in ("PARTIAL", "FAIL"):
                lines.append(f"- `{sample['metadata']['sample_id']}` / `{name}`: `{status['status']}` — {status['reason']}")
    lines.extend(["", "## Implications for the Research Prototype", "", "The controlled benchmark remains valid within its stated synthetic assumptions. These results describe only external-file compatibility and do not broaden the research claims.", "", "## Next Engineering Steps", "", "Address sample-specific partial or failed stages before attempting product-oriented routing. Existing MEP geometry is inspected only; it is not rerouted or overwritten.", ""])
    return "\n".join(lines)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown_report(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect external IFC compatibility without changing research semantics.")
    parser.add_argument("--ifc", required=True, type=Path, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--control-generated", type=Path, action="append", default=[])
    parser.add_argument("--voxel-size-m", type=float, default=0.1)
    parser.add_argument("--max-voxel-cells", type=int, default=25_000_000)
    args = parser.parse_args()
    report = build_validation_report(args.ifc, control_paths=args.control_generated,
                                     options=ValidationOptions(args.voxel_size_m, args.max_voxel_cells))
    write_json(report, args.output)
    if args.markdown:
        write_markdown(report, args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

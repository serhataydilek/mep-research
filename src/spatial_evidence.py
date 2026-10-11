"""Immutable spatial evidence derived from existing IFC building geometry.

Phase 9C.0 records what the source model and the existing Phase 3A occupancy
grids actually establish.  It does not route, place junctions, measure
clearance between services, or certify collision freedom.  Element geometry is
reduced to world-coordinate AABBs (the existing ``src.voxel`` convention) and
each element records whether that AABB is exact for its tessellated volume.
Anything the current policy cannot represent produces an explicit diagnostic
instead of being treated as free space.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from math import isfinite, prod
from typing import Any, Mapping

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.unit
import numpy as np

from .demand import SYSTEM_ORDER, service_routing_margins
from .endpoints import EndpointProvenance
from .ifc_model import create_ifc_model
from .topology import TopologyEdgeRole
from .topology_demand import inspect_topology_demand_coverage
from .topology_planner import TopologyCandidate
from .voxel import (
    BLOCKED_FIXED,
    BLOCKED_PRIOR_ROUTE,
    BLOCKED_VERTICAL_BOUNDARY,
    FREE,
    RESERVED_SHAFT,
    GridSpec,
    OccupancyGrid,
    build_occupancy_grid,
    grid_spec_from_config,
    snap_anchor_to_free_cell,
)


EVIDENCE_FORMAT_VERSION = 1
SYNTHETIC_SHAFT_SPACE_NAME = "Service Shaft 0"
DEFAULT_MAX_GRID_CELLS = 25_000_000
GEOMETRY_TOLERANCE_M = 1e-6
AUTHORITATIVE_POSITION_PROVENANCE = frozenset({
    EndpointProvenance.BENCHMARK_SYNTHETIC,
    EndpointProvenance.IFC_DISTRIBUTION_PORT,
    EndpointProvenance.EXPORT_CONNECTOR,
    EndpointProvenance.MANUAL,
})
CELL_STATE_NAMES = {
    FREE: "FREE",
    BLOCKED_FIXED: "BLOCKED_FIXED",
    RESERVED_SHAFT: "RESERVED_SHAFT",
    BLOCKED_VERTICAL_BOUNDARY: "BLOCKED_VERTICAL_BOUNDARY",
    BLOCKED_PRIOR_ROUTE: "BLOCKED_PRIOR_ROUTE",
}
UNSUPPORTED_CAPABILITIES = (
    "BEAM_DUCT_PIPE_AND_EXISTING_MEP_OBSTACLE_POLICY",
    "CORRIDOR_AND_ROOM_USAGE_SEMANTICS",
    "EXACT_COLLISION_CERTIFICATION",
    "EXACT_ROTATED_OR_CURVED_OBSTACLE_REPRESENTATION",
    "MEP_PENETRATION_APPROVAL",
    "MULTI_STOREY_OCCUPANCY",
    "PHYSICAL_JUNCTION_PLACEMENT",
    "ROUTE_GEOMETRY_LENGTH_AND_CLASH_EVALUATION",
    "SYSTEM_PAIR_CLEARANCE_POLICY",
)


class SpatialEvidenceStatus(str, Enum):
    """Evidence assessment outcome; never a route-feasibility verdict."""

    EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED_GEOMETRY = "UNSUPPORTED_GEOMETRY"
    INVALID_SPATIAL_INPUT = "INVALID_SPATIAL_INPUT"


class DiagnosticSeverity(str, Enum):
    LIMITATION = "LIMITATION"
    INSUFFICIENT = "INSUFFICIENT"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"


_STATUS_BY_SEVERITY = {
    DiagnosticSeverity.LIMITATION: SpatialEvidenceStatus.EVIDENCE_AVAILABLE,
    DiagnosticSeverity.INSUFFICIENT: SpatialEvidenceStatus.INSUFFICIENT_EVIDENCE,
    DiagnosticSeverity.UNSUPPORTED: SpatialEvidenceStatus.UNSUPPORTED_GEOMETRY,
    DiagnosticSeverity.INVALID: SpatialEvidenceStatus.INVALID_SPATIAL_INPUT,
}
_STATUS_PRECEDENCE = (
    SpatialEvidenceStatus.INVALID_SPATIAL_INPUT,
    SpatialEvidenceStatus.UNSUPPORTED_GEOMETRY,
    SpatialEvidenceStatus.INSUFFICIENT_EVIDENCE,
    SpatialEvidenceStatus.EVIDENCE_AVAILABLE,
)


class ElementClassification(str, Enum):
    PERIMETER_WALL = "PERIMETER_WALL"
    SHAFT_WALL = "SHAFT_WALL"
    STRUCTURAL_COLUMN = "STRUCTURAL_COLUMN"
    SLAB = "SLAB"
    SHAFT_SPACE = "SHAFT_SPACE"
    SPACE = "SPACE"
    UNCLASSIFIED = "UNCLASSIFIED"


class OccupancyRole(str, Enum):
    BLOCKED_FIXED = "BLOCKED_FIXED"
    RESERVED_SHAFT = "RESERVED_SHAFT"
    NOT_IN_OCCUPANCY_POLICY = "NOT_IN_OCCUPANCY_POLICY"


class AabbFidelity(str, Enum):
    """Whether the world AABB equals the tessellated solid (by volume)."""

    EXACT = "EXACT"
    OVER_APPROXIMATION = "OVER_APPROXIMATION"


class EndpointGridCompatibility(str, Enum):
    CONTAINING_CELL_FREE = "CONTAINING_CELL_FREE"
    SNAPPABLE_WITHIN_TOLERANCE = "SNAPPABLE_WITHIN_TOLERANCE"
    NOT_SNAPPABLE = "NOT_SNAPPABLE"
    OUTSIDE_GRID_DOMAIN = "OUTSIDE_GRID_DOMAIN"
    NO_GRID_FOR_SYSTEM = "NO_GRID_FOR_SYSTEM"
    NOT_EVALUATED_FRAME_MISMATCH = "NOT_EVALUATED_FRAME_MISMATCH"


_VOXEL_OBSTACLE_KIND = {
    ElementClassification.PERIMETER_WALL: "perimeter_wall",
    ElementClassification.SHAFT_WALL: "shaft_wall",
    ElementClassification.STRUCTURAL_COLUMN: "structural_column",
}
_NON_OBSTACLE_CLASSIFICATIONS = frozenset({ElementClassification.SHAFT_SPACE, ElementClassification.SPACE})


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == 0 else rounded


def _require_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")


def _require_tuple_of(value: Any, kind: type, label: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, kind) for item in value):
        raise ValueError(f"{label} must be an immutable tuple of {kind.__name__} values.")


@dataclass(frozen=True, slots=True)
class Bounds3:
    """World-space axis-aligned bounds in metres, in voxel tuple order."""

    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    def __post_init__(self) -> None:
        values = self.as_tuple()
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) for value in values):
            raise ValueError("Bounds3 values must be finite numbers.")
        if self.min_x > self.max_x or self.min_y > self.max_y or self.min_z > self.max_z:
            raise ValueError("Bounds3 minimum values must not exceed maximum values.")

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (self.min_x, self.max_x, self.min_y, self.max_y, self.min_z, self.max_z)

    def contains_point(self, x: float, y: float, z: float, tolerance: float = GEOMETRY_TOLERANCE_M) -> bool:
        return (
            self.min_x - tolerance <= x <= self.max_x + tolerance
            and self.min_y - tolerance <= y <= self.max_y + tolerance
            and self.min_z - tolerance <= z <= self.max_z + tolerance
        )

    def contains_bounds(self, other: "Bounds3", tolerance: float = GEOMETRY_TOLERANCE_M) -> bool:
        return self.contains_point(other.min_x, other.min_y, other.min_z, tolerance) and self.contains_point(
            other.max_x, other.max_y, other.max_z, tolerance
        )

    def overlaps_interior(self, other: "Bounds3", tolerance: float = GEOMETRY_TOLERANCE_M) -> bool:
        """Positive-volume overlap; touching faces do not count."""
        first, second = self.as_tuple(), other.as_tuple()
        return all(min(first[a + 1], second[a + 1]) - max(first[a], second[a]) > tolerance for a in (0, 2, 4))

    def to_canonical_dict(self) -> dict[str, float]:
        return {
            "max_x_m": _round(self.max_x), "max_y_m": _round(self.max_y), "max_z_m": _round(self.max_z),
            "min_x_m": _round(self.min_x), "min_y_m": _round(self.min_y), "min_z_m": _round(self.min_z),
        }


@dataclass(frozen=True, slots=True)
class SpatialDiagnostic:
    code: str
    severity: DiagnosticSeverity
    message: str
    subject_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.code, "Spatial diagnostic code")
        _require_text(self.message, "Spatial diagnostic message")
        if not isinstance(self.severity, DiagnosticSeverity):
            raise ValueError("Spatial diagnostic severity must be a DiagnosticSeverity.")
        if self.subject_id is not None:
            _require_text(self.subject_id, "Spatial diagnostic subject_id")

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.code, self.subject_id or "", self.severity.value, self.message)

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "severity": self.severity.value, "subject_id": self.subject_id}


@dataclass(frozen=True, slots=True)
class SpatialEvidenceProvenance:
    source_kind: str
    source_model_id: str
    scenario_id: str
    ifc_schema: str
    project_global_id: str | None
    config_sha256: str
    extractor: str
    ifcopenshell_version: str

    def __post_init__(self) -> None:
        for name in ("source_kind", "source_model_id", "scenario_id", "ifc_schema", "config_sha256", "extractor", "ifcopenshell_version"):
            _require_text(getattr(self, name), f"Spatial provenance {name}")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in sorted(self.__slots__)}


@dataclass(frozen=True, slots=True)
class CoordinateReference:
    """The single frame all evidence coordinates are expressed in."""

    frame_id: str
    length_unit: str
    source_length_unit_scale_to_metre: float
    coordinate_basis: str
    georeferencing_entities_present: bool

    def __post_init__(self) -> None:
        _require_text(self.frame_id, "Coordinate frame_id")
        if self.length_unit != "METRE":
            raise ValueError("Spatial evidence coordinates are normalized to METRE.")
        if not isfinite(self.source_length_unit_scale_to_metre) or self.source_length_unit_scale_to_metre <= 0:
            raise ValueError("Source length unit scale must be finite and positive.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "coordinate_basis": self.coordinate_basis,
            "frame_id": self.frame_id,
            "georeferencing_entities_present": self.georeferencing_entities_present,
            "length_unit": self.length_unit,
            "source_length_unit_scale_to_metre": _round(self.source_length_unit_scale_to_metre),
        }


@dataclass(frozen=True, slots=True)
class ElementEvidence:
    """One IFC product with tessellated geometry, keyed by its GlobalId."""

    global_id: str
    ifc_class: str
    name: str | None
    classification: ElementClassification
    occupancy_role: OccupancyRole
    world_bounds: Bounds3
    aabb_fidelity: AabbFidelity
    placement_rotation_free: bool
    intersects_grid_domain: bool

    def __post_init__(self) -> None:
        _require_text(self.global_id, "Element global_id")
        _require_text(self.ifc_class, "Element ifc_class")
        if not isinstance(self.classification, ElementClassification) or not isinstance(self.occupancy_role, OccupancyRole):
            raise ValueError("Element classification and occupancy role must use spatial evidence enums.")
        if not isinstance(self.world_bounds, Bounds3) or not isinstance(self.aabb_fidelity, AabbFidelity):
            raise ValueError("Element bounds and fidelity must use spatial evidence types.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "aabb_fidelity": self.aabb_fidelity.value,
            "classification": self.classification.value,
            "global_id": self.global_id,
            "ifc_class": self.ifc_class,
            "intersects_grid_domain": self.intersects_grid_domain,
            "name": self.name,
            "occupancy_role": self.occupancy_role.value,
            "placement_rotation_free": self.placement_rotation_free,
            "world_bounds": self.world_bounds.to_canonical_dict(),
        }


@dataclass(frozen=True, slots=True)
class ShaftRegionEvidence:
    global_id: str
    name: str | None
    world_bounds: Bounds3
    identification_basis: str
    reserved_in_occupancy: bool

    def __post_init__(self) -> None:
        _require_text(self.global_id, "Shaft global_id")
        _require_text(self.identification_basis, "Shaft identification_basis")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "global_id": self.global_id,
            "identification_basis": self.identification_basis,
            "name": self.name,
            "reserved_in_occupancy": self.reserved_in_occupancy,
            "world_bounds": self.world_bounds.to_canonical_dict(),
        }


@dataclass(frozen=True, slots=True)
class OpeningEvidence:
    """An existing IFC opening.  It is never an approved MEP penetration."""

    global_id: str
    name: str | None
    host_global_id: str | None
    host_ifc_class: str | None
    world_bounds: Bounds3
    has_fillings: bool
    intersects_grid_domain: bool
    approved_mep_penetration: bool = False

    def __post_init__(self) -> None:
        _require_text(self.global_id, "Opening global_id")
        if self.approved_mep_penetration:
            raise ValueError("Existing IFC openings cannot be recorded as approved MEP penetrations.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "approved_mep_penetration": False,
            "global_id": self.global_id,
            "has_fillings": self.has_fillings,
            "host_global_id": self.host_global_id,
            "host_ifc_class": self.host_ifc_class,
            "intersects_grid_domain": self.intersects_grid_domain,
            "name": self.name,
            "world_bounds": self.world_bounds.to_canonical_dict(),
        }


@dataclass(frozen=True, slots=True)
class OccupancyGridReference:
    """Immutable snapshot of one existing service-aware Phase 3A grid."""

    scenario_id: str
    system: str
    spec: GridSpec
    clearance_m: float
    required_planar_margin_m: float
    required_vertical_margin_m: float
    shaft_bounds: Bounds3
    cells_sha256: str
    cells: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _require_text(self.scenario_id, "Grid scenario_id")
        _require_text(self.system, "Grid system")
        if not isinstance(self.spec, GridSpec) or not isinstance(self.cells, bytes):
            raise ValueError("Grid reference requires a GridSpec and immutable bytes cells.")
        if len(self.cells) != self.spec.total_cell_count:
            raise ValueError("Grid reference cell count does not match its GridSpec.")
        if hashlib.sha256(self.cells).hexdigest() != self.cells_sha256:
            raise ValueError("Grid reference cells do not match their recorded digest.")
        if sum(self.state_counts().values()) != self.spec.total_cell_count:
            raise ValueError("Grid reference cells contain states outside the Phase 3A cell vocabulary.")

    @property
    def domain_bounds(self) -> Bounds3:
        spec = self.spec
        size = spec.voxel_size_m
        return Bounds3(
            spec.origin_x, spec.origin_x + spec.nx * size,
            spec.origin_y, spec.origin_y + spec.ny * size,
            spec.origin_z, spec.origin_z + spec.nz * size,
        )

    def state_counts(self) -> dict[str, int]:
        return {name: self.cells.count(state) for state, name in sorted(CELL_STATE_NAMES.items())}

    def to_canonical_dict(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "cells_sha256": self.cells_sha256,
            "clearance_m": _round(self.clearance_m),
            "grid_dimensions": {"nx": spec.nx, "ny": spec.ny, "nz": spec.nz},
            "grid_origin_m": [_round(spec.origin_x), _round(spec.origin_y), _round(spec.origin_z)],
            "rasterization": "voxel centre inside service-inflated AABB (src.voxel Phase 3A convention)",
            "required_planar_margin_m": _round(self.required_planar_margin_m),
            "required_vertical_margin_m": _round(self.required_vertical_margin_m),
            "scenario_id": self.scenario_id,
            "shaft_bounds": self.shaft_bounds.to_canonical_dict(),
            "state_counts": self.state_counts(),
            "system": self.system,
            "total_cell_count": spec.total_cell_count,
            "voxel_size_m": _round(spec.voxel_size_m),
        }


def _worst_status(statuses: list[SpatialEvidenceStatus]) -> SpatialEvidenceStatus:
    for status in _STATUS_PRECEDENCE:
        if status in statuses:
            return status
    return SpatialEvidenceStatus.EVIDENCE_AVAILABLE


@dataclass(frozen=True, slots=True)
class SpatialEvidence:
    provenance: SpatialEvidenceProvenance
    coordinate_reference: CoordinateReference
    model_bounds: Bounds3 | None
    grid_domain: Bounds3 | None
    elements: tuple[ElementEvidence, ...]
    shafts: tuple[ShaftRegionEvidence, ...]
    openings: tuple[OpeningEvidence, ...]
    grids: tuple[OccupancyGridReference, ...]
    diagnostics: tuple[SpatialDiagnostic, ...]
    unsupported_capabilities: tuple[str, ...] = UNSUPPORTED_CAPABILITIES

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, SpatialEvidenceProvenance) or not isinstance(self.coordinate_reference, CoordinateReference):
            raise ValueError("Spatial evidence requires typed provenance and coordinate reference.")
        _require_tuple_of(self.elements, ElementEvidence, "Spatial evidence elements")
        _require_tuple_of(self.shafts, ShaftRegionEvidence, "Spatial evidence shafts")
        _require_tuple_of(self.openings, OpeningEvidence, "Spatial evidence openings")
        _require_tuple_of(self.grids, OccupancyGridReference, "Spatial evidence grids")
        _require_tuple_of(self.diagnostics, SpatialDiagnostic, "Spatial evidence diagnostics")
        _require_tuple_of(self.unsupported_capabilities, str, "Spatial evidence unsupported_capabilities")
        for label, items in (("elements", self.elements), ("shafts", self.shafts), ("openings", self.openings)):
            ids = tuple(item.global_id for item in items)
            if ids != tuple(sorted(set(ids))):
                raise ValueError(f"Spatial evidence {label} must be unique and ordered by GlobalId.")
        if tuple(grid.system for grid in self.grids) != tuple(system for system in SYSTEM_ORDER if system in {g.system for g in self.grids}):
            raise ValueError("Spatial evidence grids must be unique and in stable system order.")
        for grid in self.grids:
            if grid.scenario_id != self.provenance.scenario_id:
                raise ValueError(f"Grid {grid.system} belongs to scenario {grid.scenario_id}, not {self.provenance.scenario_id}.")
            if self.grid_domain is None or not (
                grid.domain_bounds.contains_bounds(self.grid_domain) and self.grid_domain.contains_bounds(grid.domain_bounds)
            ):
                raise ValueError(f"Grid {grid.system} domain does not match the evidence grid domain.")
        if self.diagnostics != tuple(sorted(self.diagnostics, key=SpatialDiagnostic.sort_key)):
            raise ValueError("Spatial evidence diagnostics must have deterministic ordering.")

    @property
    def status(self) -> SpatialEvidenceStatus:
        return _worst_status([_STATUS_BY_SEVERITY[item.severity] for item in self.diagnostics])

    def grid_for(self, system: str) -> OccupancyGridReference | None:
        return next((grid for grid in self.grids if grid.system == system), None)

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "coordinate_reference": self.coordinate_reference.to_canonical_dict(),
            "diagnostics": [item.to_canonical_dict() for item in self.diagnostics],
            "elements": [item.to_canonical_dict() for item in self.elements],
            "format_version": EVIDENCE_FORMAT_VERSION,
            "grid_domain": None if self.grid_domain is None else self.grid_domain.to_canonical_dict(),
            "grids": [item.to_canonical_dict() for item in self.grids],
            "model_bounds": None if self.model_bounds is None else self.model_bounds.to_canonical_dict(),
            "openings": [item.to_canonical_dict() for item in self.openings],
            "provenance": self.provenance.to_canonical_dict(),
            "shafts": [item.to_canonical_dict() for item in self.shafts],
            "status": self.status.value,
            "unsupported_capabilities": list(self.unsupported_capabilities),
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"))


def config_sha256(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def synthetic_frame_id(scenario_id: str, config: Mapping[str, Any]) -> str:
    """Frame shared by a generated synthetic IFC and benchmark anchors from the same config."""
    return f"ifc-world/synthetic/{scenario_id}/{config_sha256(config)[:16]}"


def _world_settings() -> Any:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    return settings


def _mesh_bounds_and_volume(shape: Any) -> tuple[Bounds3, float, np.ndarray]:
    vertices = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
    faces = np.asarray(shape.geometry.faces, dtype=int).reshape(-1, 3)
    if vertices.size == 0 or faces.size == 0:
        raise ValueError("empty tessellation")
    if not np.all(np.isfinite(vertices)):
        raise ValueError("non-finite tessellation")
    a, b, c = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    volume = abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum())) / 6.0
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    return Bounds3(float(low[0]), float(high[0]), float(low[1]), float(high[1]), float(low[2]), float(high[2])), volume, vertices


def _classify(entity: Any) -> tuple[ElementClassification, OccupancyRole]:
    name = entity.Name or ""
    if entity.is_a("IfcWall"):
        if name.startswith("Shaft"):
            return ElementClassification.SHAFT_WALL, OccupancyRole.BLOCKED_FIXED
        return ElementClassification.PERIMETER_WALL, OccupancyRole.BLOCKED_FIXED
    if entity.is_a("IfcColumn"):
        return ElementClassification.STRUCTURAL_COLUMN, OccupancyRole.BLOCKED_FIXED
    if entity.is_a("IfcSlab"):
        return ElementClassification.SLAB, OccupancyRole.NOT_IN_OCCUPANCY_POLICY
    if entity.is_a("IfcSpace"):
        if name == SYNTHETIC_SHAFT_SPACE_NAME:
            return ElementClassification.SHAFT_SPACE, OccupancyRole.RESERVED_SHAFT
        if name.startswith("Service Shaft "):
            return ElementClassification.SHAFT_SPACE, OccupancyRole.NOT_IN_OCCUPANCY_POLICY
        return ElementClassification.SPACE, OccupancyRole.NOT_IN_OCCUPANCY_POLICY
    return ElementClassification.UNCLASSIFIED, OccupancyRole.NOT_IN_OCCUPANCY_POLICY


class _Collector:
    def __init__(self) -> None:
        self.items: list[SpatialDiagnostic] = []

    def add(self, code: str, severity: DiagnosticSeverity, message: str, subject_id: str | None = None) -> None:
        self.items.append(SpatialDiagnostic(code, severity, message, subject_id))

    def ordered(self) -> tuple[SpatialDiagnostic, ...]:
        return tuple(sorted(set(self.items), key=SpatialDiagnostic.sort_key))


def _tessellate(
    entity: Any, world: Any, local: Any, unit_scale: float, diagnostics: _Collector
) -> tuple[Bounds3, AabbFidelity, bool] | None:
    """Return world AABB, AABB fidelity and rotation-free flag, or None with a diagnostic.

    Tessellation is in metres, while ``get_local_placement`` translations are
    in project length units, so the translation is scaled before comparison.
    """
    subject = entity.GlobalId
    try:
        world_bounds, volume, _ = _mesh_bounds_and_volume(ifcopenshell.geom.create_shape(world, entity))
        _, _, local_vertices = _mesh_bounds_and_volume(ifcopenshell.geom.create_shape(local, entity))
        matrix = np.asarray(ifcopenshell.util.placement.get_local_placement(entity.ObjectPlacement), dtype=float)
        matrix[:3, 3] *= unit_scale
    except Exception as exc:  # IfcOpenShell raises several kernel-specific exception types
        diagnostics.add(
            "GEOMETRY_EXTRACTION_FAILED", DiagnosticSeverity.UNSUPPORTED,
            f"{entity.is_a()} geometry could not be tessellated ({type(exc).__name__}); its space is unknown, not free.",
            subject,
        )
        return None
    transformed = local_vertices @ matrix[:3, :3].T + matrix[:3, 3]
    low, high = transformed.min(axis=0), transformed.max(axis=0)
    expected = (low[0], high[0], low[1], high[1], low[2], high[2])
    if any(abs(a - b) > GEOMETRY_TOLERANCE_M for a, b in zip(expected, world_bounds.as_tuple())):
        diagnostics.add(
            "PLACEMENT_TRANSFORM_MISMATCH", DiagnosticSeverity.INVALID,
            "Local tessellation transformed by the resolved IfcLocalPlacement chain does not match world tessellation.",
            subject,
        )
    extents = (world_bounds.max_x - world_bounds.min_x, world_bounds.max_y - world_bounds.min_y, world_bounds.max_z - world_bounds.min_z)
    box_volume = prod(extents)
    if box_volume <= GEOMETRY_TOLERANCE_M ** 3:
        diagnostics.add("DEGENERATE_GEOMETRY", DiagnosticSeverity.UNSUPPORTED, "Element tessellation has no positive volume.", subject)
        return None
    exact = abs(box_volume - volume) <= 1e-6 * box_volume
    rotation_free = bool(np.allclose(matrix[:3, :3], np.eye(3), atol=1e-9))
    return world_bounds, AabbFidelity.EXACT if exact else AabbFidelity.OVER_APPROXIMATION, rotation_free


def extract_spatial_evidence(
    model: ifcopenshell.file,
    config: Mapping[str, Any],
    systems: Mapping[str, Mapping[str, Any]],
    *,
    scenario_id: str,
    source_kind: str = "SUPPLIED_IFC",
    frame_id: str | None = None,
    max_grid_cells: int = DEFAULT_MAX_GRID_CELLS,
) -> SpatialEvidence:
    """Derive read-only spatial evidence from an IFC model and its building config.

    Occupancy grids are produced by the existing ``src.voxel.build_occupancy_grid``
    from the identity-preserving obstacle AABBs extracted here; the model is
    not modified.
    """
    _require_text(scenario_id, "Spatial evidence scenario_id")
    diagnostics = _Collector()
    digest = config_sha256(config)
    frame = frame_id or f"ifc-world/{source_kind.lower()}/{scenario_id}/{digest[:16]}"
    projects = list(model.by_type("IfcProject"))
    scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    if not isfinite(scale) or scale <= 0:
        diagnostics.add("LENGTH_UNIT_UNRESOLVED", DiagnosticSeverity.INVALID, "Project length unit scale could not be resolved.")
        scale = 1.0
    elif abs(scale - 1.0) > 1e-12:
        diagnostics.add(
            "NON_METRE_LENGTH_UNIT_UNVALIDATED", DiagnosticSeverity.UNSUPPORTED,
            f"Project length unit scale {scale} is not 1.0; the config-metre occupancy pipeline is only validated for metre models.",
        )
    georeferenced = bool(list(model.by_type("IfcMapConversion")) or (model.schema != "IFC2X3" and list(model.by_type("IfcProjectedCRS"))))
    if georeferenced:
        diagnostics.add("GEOREFERENCING_NOT_APPLIED", DiagnosticSeverity.LIMITATION, "Evidence uses IFC world coordinates; map conversion is not applied.")
    diagnostics.add(
        "AABB_BROAD_PHASE_ONLY", DiagnosticSeverity.LIMITATION,
        "Obstacles are world AABBs suitable for broad-phase occupancy only; no exact collision certification is made.",
    )

    grid_spec: GridSpec | None = None
    grid_domain: Bounds3 | None = None
    try:
        grid_spec = grid_spec_from_config(dict(config))
        size = grid_spec.voxel_size_m
        grid_domain = Bounds3(
            grid_spec.origin_x, grid_spec.origin_x + grid_spec.nx * size,
            grid_spec.origin_y, grid_spec.origin_y + grid_spec.ny * size,
            grid_spec.origin_z, grid_spec.origin_z + grid_spec.nz * size,
        )
        diagnostics.add(
            "GRID_VERTICAL_DOMAIN_FROM_CONFIG", DiagnosticSeverity.LIMITATION,
            "The occupancy domain is the storey-0 plenum derived from config ceiling height; no IFC ceiling element bounds it.",
        )
    except (KeyError, TypeError, ValueError) as exc:
        diagnostics.add("GRID_SPEC_INVALID", DiagnosticSeverity.INVALID, f"Occupancy grid specification is invalid: {exc}")

    world, local = _world_settings(), ifcopenshell.geom.settings()
    elements: list[ElementEvidence] = []
    products = sorted(
        (item for item in model.by_type("IfcProduct") if item.Representation is not None and not item.is_a("IfcOpeningElement")),
        key=lambda item: item.GlobalId,
    )
    for entity in products:
        result = _tessellate(entity, world, local, scale, diagnostics)
        if result is None:
            continue
        bounds, fidelity, rotation_free = result
        classification, role = _classify(entity)
        in_domain = grid_domain is not None and bounds.overlaps_interior(grid_domain)
        elements.append(ElementEvidence(
            entity.GlobalId, entity.is_a(), entity.Name, classification, role, bounds, fidelity, rotation_free, in_domain,
        ))
        if role is not OccupancyRole.NOT_IN_OCCUPANCY_POLICY and fidelity is AabbFidelity.OVER_APPROXIMATION:
            diagnostics.add(
                "AABB_OVER_APPROXIMATION", DiagnosticSeverity.LIMITATION,
                "Occupancy uses this element's AABB, which blocks more space than its tessellated solid.",
                entity.GlobalId,
            )
        if not rotation_free:
            diagnostics.add(
                "ROTATED_PLACEMENT", DiagnosticSeverity.LIMITATION,
                "Element placement is rotated; axis-aligned occupancy is an approximation.", entity.GlobalId,
            )
        if in_domain and role is OccupancyRole.NOT_IN_OCCUPANCY_POLICY and classification not in _NON_OBSTACLE_CLASSIFICATIONS:
            diagnostics.add(
                "UNMODELLED_GEOMETRY_IN_GRID_DOMAIN", DiagnosticSeverity.UNSUPPORTED,
                f"{entity.is_a()} geometry intersects the occupancy domain but is not in the obstacle policy; its space is not treated as free.",
                entity.GlobalId,
            )

    openings = []
    for opening in sorted(model.by_type("IfcOpeningElement"), key=lambda item: item.GlobalId):
        if opening.Representation is None:
            diagnostics.add("OPENING_WITHOUT_GEOMETRY", DiagnosticSeverity.LIMITATION, "Opening has no representation; it is not recorded.", opening.GlobalId)
            continue
        result = _tessellate(opening, world, local, scale, diagnostics)
        if result is None:
            continue
        hosts = [rel.RelatingBuildingElement for rel in opening.VoidsElements or ()]
        if len(hosts) != 1:
            diagnostics.add("OPENING_HOST_UNRESOLVED", DiagnosticSeverity.LIMITATION, "Opening does not void exactly one host element.", opening.GlobalId)
        host = hosts[0] if len(hosts) == 1 else None
        openings.append(OpeningEvidence(
            opening.GlobalId, opening.Name,
            None if host is None else host.GlobalId, None if host is None else host.is_a(),
            result[0], bool(opening.HasFillings),
            grid_domain is not None and result[0].overlaps_interior(grid_domain),
        ))

    shafts = tuple(
        ShaftRegionEvidence(
            item.global_id, item.name, item.world_bounds,
            f"synthetic IfcSpace name convention '{item.name}'",
            item.occupancy_role is OccupancyRole.RESERVED_SHAFT,
        )
        for item in elements if item.classification is ElementClassification.SHAFT_SPACE
    )
    reserved = [item for item in shafts if item.reserved_in_occupancy]
    obstacle_elements = [item for item in elements if item.classification in _VOXEL_OBSTACLE_KIND]
    solids = [item.world_bounds for item in elements if item.classification not in _NON_OBSTACLE_CLASSIFICATIONS]
    model_bounds = None
    if solids:
        model_bounds = Bounds3(
            min(b.min_x for b in solids), max(b.max_x for b in solids), min(b.min_y for b in solids),
            max(b.max_y for b in solids), min(b.min_z for b in solids), max(b.max_z for b in solids),
        )
    else:
        diagnostics.add("NO_SOLID_GEOMETRY", DiagnosticSeverity.INVALID, "No solid building geometry could be extracted.")
    if not obstacle_elements:
        diagnostics.add("NO_POLICY_OBSTACLES", DiagnosticSeverity.INSUFFICIENT, "No IfcWall/IfcColumn obstacle geometry is available.")
    if len(reserved) != 1:
        diagnostics.add(
            "SHAFT_RESERVATION_UNAVAILABLE", DiagnosticSeverity.INSUFFICIENT,
            f"Exactly one '{SYNTHETIC_SHAFT_SPACE_NAME}' IfcSpace is required by the existing occupancy policy; found {len(reserved)}.",
        )
    if grid_domain is not None and model_bounds is not None and not model_bounds.contains_bounds(grid_domain):
        diagnostics.add(
            "GRID_DOMAIN_EXCEEDS_MODEL_BOUNDS", DiagnosticSeverity.INSUFFICIENT,
            "Part of the occupancy domain lies outside all extracted IFC geometry; free cells there have no model evidence.",
        )

    grids: list[OccupancyGridReference] = []
    if grid_spec is not None and grid_spec.total_cell_count > max_grid_cells:
        diagnostics.add(
            "GRID_CELL_LIMIT_EXCEEDED", DiagnosticSeverity.UNSUPPORTED,
            f"Grid requires {grid_spec.total_cell_count} cells per system, above the {max_grid_cells} safety limit.",
        )
    elif grid_spec is not None and len(reserved) == 1 and obstacle_elements:
        obstacles = [(_VOXEL_OBSTACLE_KIND[item.classification], item.world_bounds.as_tuple()) for item in obstacle_elements]
        shaft_bounds = reserved[0].world_bounds
        clearance = float(config["routing"]["clearance_m"])
        for system in SYSTEM_ORDER:
            if system not in systems:
                diagnostics.add("SYSTEM_DEFINITION_MISSING", DiagnosticSeverity.INSUFFICIENT, f"No service definition for {system}.", system)
                continue
            grid = build_occupancy_grid(scenario_id, dict(config), system, dict(systems[system]), obstacles, shaft_bounds.as_tuple())
            margins = service_routing_margins(dict(systems[system]), clearance)
            cells = bytes(grid.cells)
            grids.append(OccupancyGridReference(
                scenario_id, system, grid.spec, clearance,
                margins["required_planar_margin_m"], margins["required_vertical_margin_m"],
                shaft_bounds, hashlib.sha256(cells).hexdigest(), cells,
            ))

    provenance = SpatialEvidenceProvenance(
        source_kind=source_kind,
        source_model_id=frame.split("/", 1)[1],
        scenario_id=scenario_id,
        ifc_schema=str(model.schema),
        project_global_id=projects[0].GlobalId if len(projects) == 1 else None,
        config_sha256=digest,
        extractor="src.spatial_evidence.extract_spatial_evidence",
        ifcopenshell_version=str(ifcopenshell.version),
    )
    coordinate_reference = CoordinateReference(
        frame_id=frame,
        length_unit="METRE",
        source_length_unit_scale_to_metre=scale,
        coordinate_basis="IfcOpenShell world-coordinate tessellation (IfcLocalPlacement chain resolved)",
        georeferencing_entities_present=georeferenced,
    )
    return SpatialEvidence(
        provenance, coordinate_reference, model_bounds, grid_domain, tuple(elements), shafts, tuple(openings),
        tuple(grids), diagnostics.ordered(),
    )


def build_synthetic_spatial_evidence(
    scenario_id: str, config: Mapping[str, Any], systems: Mapping[str, Mapping[str, Any]]
) -> SpatialEvidence:
    """Evidence for the deterministic generated IFC of one existing building config."""
    return extract_spatial_evidence(
        create_ifc_model(dict(config)), config, systems, scenario_id=scenario_id,
        source_kind="GENERATED_SYNTHETIC_IFC", frame_id=synthetic_frame_id(scenario_id, config),
    )


@dataclass(frozen=True, slots=True)
class EndpointSpatialEvidence:
    endpoint_id: str
    system: str
    provenance: str
    position_m: tuple[float, float, float]
    position_authoritative: bool
    within_grid_domain: bool | None
    containing_cell: tuple[int, int, int] | None
    containing_cell_state: str | None
    grid_compatibility: EndpointGridCompatibility
    snap_cell: tuple[int, int, int] | None = None
    snap_distance_m: float | None = None

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "containing_cell": None if self.containing_cell is None else list(self.containing_cell),
            "containing_cell_state": self.containing_cell_state,
            "endpoint_id": self.endpoint_id,
            "grid_compatibility": self.grid_compatibility.value,
            "position_authoritative": self.position_authoritative,
            "position_m": [_round(value) for value in self.position_m],
            "provenance": self.provenance,
            "snap_cell": None if self.snap_cell is None else list(self.snap_cell),
            "snap_distance_m": None if self.snap_distance_m is None else _round(self.snap_distance_m),
            "system": self.system,
            "within_grid_domain": self.within_grid_domain,
        }


@dataclass(frozen=True, slots=True)
class UnresolvedPhysicalRequirement:
    """Geometry a later phase must produce before feasibility can be judged."""

    kind: str
    subject_id: str
    dependency: str

    def to_canonical_dict(self) -> dict[str, str]:
        return {"dependency": self.dependency, "kind": self.kind, "subject_id": self.subject_id}


@dataclass(frozen=True, slots=True)
class CandidateSpatialAssessment:
    """Read-only evidence assessment for one abstract topology candidate.

    ``status`` reports whether spatial evidence exists to evaluate the
    candidate later.  It is not a route-feasibility, clearance or clash result.
    """

    candidate_id: str
    candidate_kind: str
    evidence_frame_id: str
    endpoint_frame_id: str
    source_model_id: str
    status: SpatialEvidenceStatus
    source_model_usable: bool
    endpoints: tuple[EndpointSpatialEvidence, ...]
    unresolved_physical_requirements: tuple[UnresolvedPhysicalRequirement, ...]
    diagnostics: tuple[SpatialDiagnostic, ...]
    physical_feasibility_evaluated: bool = False
    collision_free_claimed: bool = False

    def __post_init__(self) -> None:
        if self.physical_feasibility_evaluated or self.collision_free_claimed:
            raise ValueError("Spatial evidence assessment cannot claim physical feasibility or collision freedom.")
        if not isinstance(self.status, SpatialEvidenceStatus):
            raise ValueError("Assessment status must be a SpatialEvidenceStatus.")
        _require_tuple_of(self.endpoints, EndpointSpatialEvidence, "Assessment endpoints")
        _require_tuple_of(self.unresolved_physical_requirements, UnresolvedPhysicalRequirement, "Assessment requirements")
        _require_tuple_of(self.diagnostics, SpatialDiagnostic, "Assessment diagnostics")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "collision_free_claimed": False,
            "diagnostics": [item.to_canonical_dict() for item in self.diagnostics],
            "endpoint_frame_id": self.endpoint_frame_id,
            "endpoints": [item.to_canonical_dict() for item in self.endpoints],
            "evidence_frame_id": self.evidence_frame_id,
            "physical_feasibility_evaluated": False,
            "source_model_id": self.source_model_id,
            "source_model_usable": self.source_model_usable,
            "status": self.status.value,
            "unresolved_physical_requirements": [item.to_canonical_dict() for item in self.unresolved_physical_requirements],
        }


def read_only_occupancy_view(grid: OccupancyGridReference) -> OccupancyGrid:
    """Legacy ``OccupancyGrid`` over the immutable snapshot, without copying cells.

    Only read-only legacy helpers (``state_at``, ``snap_anchor_to_free_cell``)
    may consume this view; writes fail because the cells are ``bytes``.
    """
    return OccupancyGrid(grid.scenario_id, grid.system, grid.spec, grid.cells, grid.shaft_bounds.as_tuple(), {})  # type: ignore[arg-type]


def _free_cell_near(grid: OccupancyGridReference, cell: tuple[int, int, int], reach: int = 3) -> bool:
    """Any FREE cell within ``reach`` indices; bounds the existing outward snap search.

    A FREE centre within the two-voxel snap tolerance is never more than three
    indices from the containing cell, so ``False`` means not snappable.
    """
    spec = grid.spec
    for k in range(max(0, cell[2] - reach), min(spec.nz, cell[2] + reach + 1)):
        for j in range(max(0, cell[1] - reach), min(spec.ny, cell[1] + reach + 1)):
            for i in range(max(0, cell[0] - reach), min(spec.nx, cell[0] + reach + 1)):
                if grid.cells[spec.linear_index(i, j, k)] == FREE:
                    return True
    return False


def _endpoint_evidence(
    endpoint: Any, evidence: SpatialEvidence, frame_matches: bool, diagnostics: _Collector,
    snapshots: dict[str, OccupancyGrid],
) -> EndpointSpatialEvidence:
    position = (endpoint.position.x, endpoint.position.y, endpoint.position.z)
    authoritative = endpoint.provenance in AUTHORITATIVE_POSITION_PROVENANCE
    base = dict(
        endpoint_id=endpoint.endpoint_id, system=endpoint.system.value, provenance=endpoint.provenance.value,
        position_m=position, position_authoritative=authoritative,
    )
    if not authoritative:
        diagnostics.add(
            "ENDPOINT_POSITION_NOT_AUTHORITATIVE", DiagnosticSeverity.INSUFFICIENT,
            f"Endpoint provenance {endpoint.provenance.value} does not establish an authoritative position.", endpoint.endpoint_id,
        )
    if not frame_matches:
        return EndpointSpatialEvidence(**base, within_grid_domain=None, containing_cell=None, containing_cell_state=None,
                                       grid_compatibility=EndpointGridCompatibility.NOT_EVALUATED_FRAME_MISMATCH)
    grid = evidence.grid_for(endpoint.system.value)
    if grid is None:
        diagnostics.add("NO_OCCUPANCY_GRID_FOR_SYSTEM", DiagnosticSeverity.INSUFFICIENT,
                        f"No occupancy grid is available for {endpoint.system.value}.", endpoint.endpoint_id)
        within = None if evidence.grid_domain is None else evidence.grid_domain.contains_point(*position, tolerance=0.0)
        return EndpointSpatialEvidence(**base, within_grid_domain=within, containing_cell=None, containing_cell_state=None,
                                       grid_compatibility=EndpointGridCompatibility.NO_GRID_FOR_SYSTEM)
    try:
        cell = grid.spec.world_to_containing_cell(*position)
    except ValueError:
        diagnostics.add("ENDPOINT_OUTSIDE_SUPPORTED_BOUNDS", DiagnosticSeverity.INSUFFICIENT,
                        "Endpoint lies outside the occupancy domain; no spatial evidence covers it.", endpoint.endpoint_id)
        return EndpointSpatialEvidence(**base, within_grid_domain=False, containing_cell=None, containing_cell_state=None,
                                       grid_compatibility=EndpointGridCompatibility.OUTSIDE_GRID_DOMAIN)
    state = grid.cells[grid.spec.linear_index(*cell)]
    if endpoint.system.value not in snapshots:
        snapshots[endpoint.system.value] = read_only_occupancy_view(grid)
    try:
        if not _free_cell_near(grid, cell):
            raise ValueError("no FREE cell near endpoint")
        snapped = snap_anchor_to_free_cell(
            snapshots[endpoint.system.value],
            {"anchor_id": endpoint.endpoint_id, "x_m": position[0], "y_m": position[1], "z_m": position[2]},
        )
    except ValueError:
        diagnostics.add(
            "ENDPOINT_NOT_GRID_COMPATIBLE", DiagnosticSeverity.INSUFFICIENT,
            f"Endpoint cell is {CELL_STATE_NAMES.get(state, str(state))} and no FREE cell lies within the two-voxel snap tolerance.",
            endpoint.endpoint_id,
        )
        return EndpointSpatialEvidence(**base, within_grid_domain=True, containing_cell=cell,
                                       containing_cell_state=CELL_STATE_NAMES.get(state, str(state)),
                                       grid_compatibility=EndpointGridCompatibility.NOT_SNAPPABLE)
    compatibility = (EndpointGridCompatibility.CONTAINING_CELL_FREE if state == FREE
                     else EndpointGridCompatibility.SNAPPABLE_WITHIN_TOLERANCE)
    return EndpointSpatialEvidence(
        **base, within_grid_domain=True, containing_cell=cell, containing_cell_state=CELL_STATE_NAMES.get(state, str(state)),
        grid_compatibility=compatibility, snap_cell=tuple(snapped["grid_index"]), snap_distance_m=float(snapped["snap_distance_m"]),
    )


def assess_topology_candidate(
    candidate: TopologyCandidate, evidence: SpatialEvidence, *, endpoint_frame_id: str
) -> CandidateSpatialAssessment:
    """Report which spatial evidence exists for a candidate; never evaluate routes.

    ``endpoint_frame_id`` is the caller's declaration of the frame its
    endpoint positions use.  A mismatch with the evidence frame is invalid
    input; positions are not reinterpreted.
    """
    if not isinstance(candidate, TopologyCandidate):
        raise ValueError("Spatial assessment requires a TopologyCandidate.")
    if not isinstance(evidence, SpatialEvidence):
        raise ValueError("Spatial assessment requires SpatialEvidence.")
    _require_text(endpoint_frame_id, "endpoint_frame_id")
    demand = candidate.demand
    if inspect_topology_demand_coverage(demand.problem, demand.assignments).coverage_status != "COMPLETE":
        raise ValueError("Spatial assessment requires complete authoritative demand coverage.")
    diagnostics = _Collector()
    for item in evidence.diagnostics:
        diagnostics.add(f"EVIDENCE_{item.code}", item.severity, item.message, item.subject_id)
    frame_matches = endpoint_frame_id == evidence.coordinate_reference.frame_id
    if not frame_matches:
        diagnostics.add(
            "COORDINATE_FRAME_MISMATCH", DiagnosticSeverity.INVALID,
            f"Endpoint frame {endpoint_frame_id} differs from evidence frame {evidence.coordinate_reference.frame_id}.",
        )
    endpoint_by_id = {item.endpoint_id: item for item in demand.problem.endpoints}
    bound_ids = sorted(node.endpoint_id for node in demand.topology.nodes if node.endpoint_id is not None)
    snapshots: dict[str, OccupancyGrid] = {}
    endpoints = tuple(
        _endpoint_evidence(endpoint_by_id[item], evidence, frame_matches, diagnostics, snapshots) for item in bound_ids
    )

    requirements = []
    for node in demand.topology.nodes:
        if node.endpoint_id is None:
            requirements.append(UnresolvedPhysicalRequirement(
                "JUNCTION_POSITION_UNRESOLVED", node.node_id, "physical junction placement search (Phase 9C)"))
    for edge in demand.topology.edges:
        requirements.append(UnresolvedPhysicalRequirement(
            "EDGE_ROUTE_GEOMETRY_UNRESOLVED", edge.edge_id, "constrained routing search (Phase 9D)"))
        if edge.role is TopologyEdgeRole.MAIN and any(node.endpoint_id is None for node in demand.topology.nodes
                                             if node.node_id in (edge.start_node_id, edge.end_node_id)):
            requirements.append(UnresolvedPhysicalRequirement(
                "SHARED_TRUNK_FIT_UNEVALUATED", edge.edge_id, "junction placement and trunk sizing (Phase 9C/9D)"))
    requirements.sort(key=lambda item: (item.kind, item.subject_id))
    ordered = diagnostics.ordered()
    return CandidateSpatialAssessment(
        candidate_id=candidate.candidate_id,
        candidate_kind=candidate.kind.value,
        evidence_frame_id=evidence.coordinate_reference.frame_id,
        endpoint_frame_id=endpoint_frame_id,
        source_model_id=evidence.provenance.source_model_id,
        status=_worst_status([_STATUS_BY_SEVERITY[item.severity] for item in ordered]),
        source_model_usable=evidence.status is SpatialEvidenceStatus.EVIDENCE_AVAILABLE,
        endpoints=endpoints,
        unresolved_physical_requirements=tuple(requirements),
        diagnostics=ordered,
    )

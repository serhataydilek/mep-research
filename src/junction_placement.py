"""Bounded, evidence-derived candidate cells for logical SHARED_MAIN junctions.

Phase 9C.1 proposes where a virtual junction *could* later be evaluated.  A
proposal is a FREE cell of the junction's own system occupancy grid inside a
bounded region derived from authoritative endpoint positions.  FREE means only
what the Phase 3A grid already means: the service's nominal straight-run
envelope plus the scenario routing clearance, centred on that cell, does not
reach a fixed-obstacle AABB or the plenum boundary.  It does not establish
that a fitting fits, that the main or branches can be routed, or that anything
is collision free.  The topology itself is never modified, authoritative
endpoint coordinates are never snapped, and a bounded search that finds no
usable cell is reported unresolved, never infeasible.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import json
from math import isfinite
from typing import Any

from .endpoints import Endpoint, System
from .spatial_evidence import (
    AUTHORITATIVE_POSITION_PROVENANCE,
    CELL_STATE_NAMES,
    Bounds3,
    DiagnosticSeverity,
    OccupancyGridReference,
    SpatialDiagnostic,
    SpatialEvidence,
    SpatialEvidenceStatus,
    UnresolvedPhysicalRequirement,
)
from .topology import TopologyEdge, TopologyEdgeRole, TopologyNodeKind
from .topology_planner import TopologyCandidate
from .voxel import FREE


PLACEMENT_FORMAT_VERSION = 1
USABLE_CELL_STATE = "FREE"
USABLE_CELL_SEMANTICS = (
    "Phase 3A FREE centreline cell of the junction system's own grid: nominal straight-run service envelope "
    "plus routing clearance clears fixed-obstacle AABBs and the plenum boundary. RESERVED_SHAFT and all "
    "BLOCKED_* states are not usable, matching the existing FREE-only routers."
)
GENERATION_REASON = "FREE_CELL_IN_BOUNDED_ENDPOINT_REGION"
SEED_BASIS = "component-wise lower median of authoritative endpoint containing cells (minimises summed L1 index distance)"
ORDERING_BASIS = "L1 index distance from seed, then (k, j, i); an exploration order, not a route or fitting cost"
MAX_EXPLORED_CELLS_CAP = 25_000_000


class JunctionPlacementStatus(str, Enum):
    """Outcome of candidate generation only; never ROUTED or INFEASIBLE."""

    PROPOSALS_GENERATED = "PROPOSALS_GENERATED"
    UNRESOLVED = "UNRESOLVED"


class UnresolvedPlacementReason(str, Enum):
    UNSUPPORTED_JUNCTION_STRUCTURE = "UNSUPPORTED_JUNCTION_STRUCTURE"
    SPATIAL_EVIDENCE_NOT_USABLE = "SPATIAL_EVIDENCE_NOT_USABLE"
    COORDINATE_FRAME_MISMATCH = "COORDINATE_FRAME_MISMATCH"
    NO_COMPATIBLE_GRID = "NO_COMPATIBLE_GRID"
    ENDPOINT_POSITION_NOT_AUTHORITATIVE = "ENDPOINT_POSITION_NOT_AUTHORITATIVE"
    ENDPOINT_OUTSIDE_GRID = "ENDPOINT_OUTSIDE_GRID"
    NO_USABLE_CELL_IN_BOUNDED_REGION = "NO_USABLE_CELL_IN_BOUNDED_REGION"


_REASON_PRECEDENCE = tuple(UnresolvedPlacementReason)


class SearchTermination(str, Enum):
    NOT_SEARCHED = "NOT_SEARCHED"
    REGION_EXHAUSTED = "REGION_EXHAUSTED"
    CANDIDATE_LIMIT_REACHED = "CANDIDATE_LIMIT_REACHED"
    EXPLORATION_LIMIT_REACHED = "EXPLORATION_LIMIT_REACHED"


class Establishment(str, Enum):
    ESTABLISHED = "ESTABLISHED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_EVALUATED = "NOT_EVALUATED"


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == 0 else rounded


def _require_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")


def _require_tuple_of(value: Any, kind: type, label: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, kind) for item in value):
        raise ValueError(f"{label} must be an immutable tuple of {kind.__name__} values.")


def _require_cell(value: Any, label: str) -> None:
    if not (isinstance(value, tuple) and len(value) == 3
            and all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in value)):
        raise ValueError(f"{label} must be a tuple of three non-negative grid indices.")


def _require_count(value: Any, label: str, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer of at least {minimum}.")


@dataclass(frozen=True, slots=True)
class JunctionSearchLimits:
    """Explicit bounds on one junction's candidate search."""

    max_candidates_per_junction: int = 8
    max_explored_cells: int = 50_000
    region_margin_cells: int = 2

    def __post_init__(self) -> None:
        _require_count(self.max_candidates_per_junction, "max_candidates_per_junction", 1)
        _require_count(self.max_explored_cells, "max_explored_cells", 1)
        _require_count(self.region_margin_cells, "region_margin_cells", 0)
        if self.max_explored_cells > MAX_EXPLORED_CELLS_CAP:
            raise ValueError(f"max_explored_cells must not exceed {MAX_EXPLORED_CELLS_CAP}.")

    def to_canonical_dict(self) -> dict[str, int]:
        return {
            "max_candidates_per_junction": self.max_candidates_per_junction,
            "max_explored_cells": self.max_explored_cells,
            "region_margin_cells": self.region_margin_cells,
        }


@dataclass(frozen=True, slots=True)
class ValidationLedger:
    """What this milestone establishes; the last three are structurally unevaluated."""

    structural_topology: Establishment
    spatial_evidence: Establishment
    candidate_placement: Establishment
    physical_fitting_feasibility: Establishment = Establishment.NOT_EVALUATED
    reachable_route_geometry: Establishment = Establishment.NOT_EVALUATED
    collision_free_certification: Establishment = Establishment.NOT_EVALUATED

    def __post_init__(self) -> None:
        for name in ("structural_topology", "spatial_evidence", "candidate_placement"):
            if getattr(self, name) not in (Establishment.ESTABLISHED, Establishment.NOT_ESTABLISHED):
                raise ValueError(f"Ledger {name} must be ESTABLISHED or NOT_ESTABLISHED.")
        for name in ("physical_fitting_feasibility", "reachable_route_geometry", "collision_free_certification"):
            if getattr(self, name) is not Establishment.NOT_EVALUATED:
                raise ValueError(f"Junction placement cannot evaluate {name}.")

    def to_canonical_dict(self) -> dict[str, str]:
        return {name: getattr(self, name).value for name in sorted(self.__slots__)}


@dataclass(frozen=True, slots=True)
class GridEvidenceReference:
    """Identifies the exact grid snapshot a proposal was read from."""

    evidence_frame_id: str
    source_model_id: str
    scenario_id: str
    system: str
    cells_sha256: str
    voxel_size_m: float
    clearance_m: float
    required_planar_margin_m: float
    required_vertical_margin_m: float
    clearance_policy_digest_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("evidence_frame_id", "source_model_id", "scenario_id", "system", "cells_sha256"):
            _require_text(getattr(self, name), f"Grid evidence {name}")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "cells_sha256": self.cells_sha256,
            "clearance_policy_digest_sha256": self.clearance_policy_digest_sha256,
            "clearance_m": _round(self.clearance_m),
            "evidence_frame_id": self.evidence_frame_id,
            "required_planar_margin_m": _round(self.required_planar_margin_m),
            "required_vertical_margin_m": _round(self.required_vertical_margin_m),
            "scenario_id": self.scenario_id,
            "source_model_id": self.source_model_id,
            "system": self.system,
            "voxel_size_m": _round(self.voxel_size_m),
        }


@dataclass(frozen=True, slots=True)
class EndpointAnchor:
    """An endpoint as read; its position is never modified.

    ``SOURCE`` is the group's shared connection start endpoint (the planner's
    grouping key), whatever its flow direction; benchmark groups start at
    egress anchors whose flow direction is UNKNOWN.
    """

    endpoint_id: str
    role: str
    flow_direction: str
    provenance: str
    position_authoritative: bool
    position_m: tuple[float, float, float]
    containing_cell: tuple[int, int, int] | None
    containing_cell_state: str | None

    def __post_init__(self) -> None:
        _require_text(self.endpoint_id, "Anchor endpoint_id")
        if self.role not in ("SOURCE", "TERMINAL"):
            raise ValueError("Anchor role must be SOURCE or TERMINAL.")
        if self.containing_cell is not None:
            _require_cell(self.containing_cell, "Anchor containing_cell")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "containing_cell": None if self.containing_cell is None else list(self.containing_cell),
            "containing_cell_state": self.containing_cell_state,
            "endpoint_id": self.endpoint_id,
            "flow_direction": self.flow_direction,
            "position_authoritative": self.position_authoritative,
            "position_m": [_round(value) for value in self.position_m],
            "provenance": self.provenance,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class SearchRegion:
    """Inclusive index box searched for one junction, with its world extent."""

    min_cell: tuple[int, int, int]
    max_cell: tuple[int, int, int]
    world_bounds: Bounds3
    seed_cell: tuple[int, int, int]
    cell_count: int
    margin_cells: int
    basis: str = "authoritative endpoint containing-cell box expanded by margin_cells, clipped to the grid domain"

    def __post_init__(self) -> None:
        for name in ("min_cell", "max_cell", "seed_cell"):
            _require_cell(getattr(self, name), f"Search region {name}")
        if any(self.min_cell[a] > self.max_cell[a] or not self.min_cell[a] <= self.seed_cell[a] <= self.max_cell[a]
               for a in range(3)):
            raise ValueError("Search region seed must lie inside a non-empty index box.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "cell_count": self.cell_count,
            "margin_cells": self.margin_cells,
            "max_cell": list(self.max_cell),
            "min_cell": list(self.min_cell),
            "seed_basis": SEED_BASIS,
            "seed_cell": list(self.seed_cell),
            "world_bounds": self.world_bounds.to_canonical_dict(),
        }


@dataclass(frozen=True, slots=True)
class JunctionPlacementProposal:
    """One grid-derived candidate location; not a validated physical position."""

    candidate_id: str
    junction_node_id: str
    system: str
    rank: int
    grid_index: tuple[int, int, int]
    linear_index: int
    world_position_m: tuple[float, float, float]
    cell_state: str
    seed_l1_distance: int
    evidence: GridEvidenceReference
    unresolved_physical_requirements: tuple[UnresolvedPhysicalRequirement, ...]
    generation_reason: str = GENERATION_REASON
    physical_position_validated: bool = False
    fitting_feasibility_evaluated: bool = False
    route_reachability_evaluated: bool = False
    collision_free_claimed: bool = False

    def __post_init__(self) -> None:
        if (self.physical_position_validated or self.fitting_feasibility_evaluated
                or self.route_reachability_evaluated or self.collision_free_claimed):
            raise ValueError("Junction placement proposals cannot claim physical validity, routing or collision freedom.")
        _require_text(self.candidate_id, "Proposal candidate_id")
        _require_text(self.junction_node_id, "Proposal junction_node_id")
        _require_cell(self.grid_index, "Proposal grid_index")
        if self.cell_state != USABLE_CELL_STATE:
            raise ValueError("Junction placement proposals may only use FREE cells.")
        if not isinstance(self.evidence, GridEvidenceReference) or self.evidence.system != self.system:
            raise ValueError("Proposal evidence must reference the junction system's own grid.")
        if len(self.world_position_m) != 3 or not all(isfinite(value) for value in self.world_position_m):
            raise ValueError("Proposal world position must be three finite coordinates.")
        _require_tuple_of(self.unresolved_physical_requirements, UnresolvedPhysicalRequirement, "Proposal requirements")
        if not self.unresolved_physical_requirements:
            raise ValueError("Proposals must state their unresolved physical requirements.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "cell_state": self.cell_state,
            "collision_free_claimed": False,
            "evidence": self.evidence.to_canonical_dict(),
            "fitting_feasibility_evaluated": False,
            "generation_reason": self.generation_reason,
            "grid_index": list(self.grid_index),
            "junction_node_id": self.junction_node_id,
            "linear_index": self.linear_index,
            "physical_position_validated": False,
            "rank": self.rank,
            "route_reachability_evaluated": False,
            "seed_l1_distance": self.seed_l1_distance,
            "system": self.system,
            "unresolved_physical_requirements": [item.to_canonical_dict() for item in self.unresolved_physical_requirements],
            "world_position_m": [_round(value) for value in self.world_position_m],
        }


@dataclass(frozen=True, slots=True)
class JunctionPlacementResult:
    """Placement outcome for one logical junction of one topology candidate."""

    candidate_id: str
    junction_node_id: str
    system: str
    source_endpoint_id: str | None
    terminal_endpoint_ids: tuple[str, ...]
    main_edge_id: str | None
    branch_edge_ids: tuple[str, ...]
    connection_ids: tuple[str, ...]
    status: JunctionPlacementStatus
    unresolved_reason: UnresolvedPlacementReason | None
    anchors: tuple[EndpointAnchor, ...]
    region: SearchRegion | None
    termination: SearchTermination
    explored_cell_count: int
    skipped_state_counts: tuple[tuple[str, int], ...]
    excluded_endpoint_cell_count: int
    proposals: tuple[JunctionPlacementProposal, ...]
    diagnostics: tuple[SpatialDiagnostic, ...]
    ledger: ValidationLedger
    globally_infeasible_claimed: bool = False

    def __post_init__(self) -> None:
        if self.globally_infeasible_claimed:
            raise ValueError("A bounded junction search cannot claim global infeasibility.")
        if not isinstance(self.status, JunctionPlacementStatus) or not isinstance(self.termination, SearchTermination):
            raise ValueError("Junction placement status and termination must use placement enums.")
        _require_tuple_of(self.anchors, EndpointAnchor, "Junction anchors")
        _require_tuple_of(self.proposals, JunctionPlacementProposal, "Junction proposals")
        _require_tuple_of(self.diagnostics, SpatialDiagnostic, "Junction diagnostics")
        if self.status is JunctionPlacementStatus.PROPOSALS_GENERATED:
            if not self.proposals or self.unresolved_reason is not None:
                raise ValueError("Generated placements require proposals and no unresolved reason.")
        elif self.proposals or not isinstance(self.unresolved_reason, UnresolvedPlacementReason):
            raise ValueError("Unresolved placements require a reason and carry no proposals.")
        if (self.termination is SearchTermination.NOT_SEARCHED) != (self.region is None):
            raise ValueError("Exactly the searched junctions carry a search region.")
        if any(item.system != self.system or item.junction_node_id != self.junction_node_id
               or item.candidate_id != self.candidate_id for item in self.proposals):
            raise ValueError("Proposals must belong to this junction, candidate and system.")
        if tuple(item.rank for item in self.proposals) != tuple(range(len(self.proposals))):
            raise ValueError("Proposal ranks must be contiguous and ordered.")
        if len({item.linear_index for item in self.proposals}) != len(self.proposals):
            raise ValueError("Junction proposals must be unique cells.")
        if self.diagnostics != tuple(sorted(self.diagnostics, key=SpatialDiagnostic.sort_key)):
            raise ValueError("Junction diagnostics must have deterministic ordering.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "anchors": [item.to_canonical_dict() for item in self.anchors],
            "branch_edge_ids": list(self.branch_edge_ids),
            "candidate_id": self.candidate_id,
            "connection_ids": list(self.connection_ids),
            "diagnostics": [item.to_canonical_dict() for item in self.diagnostics],
            "excluded_endpoint_cell_count": self.excluded_endpoint_cell_count,
            "explored_cell_count": self.explored_cell_count,
            "globally_infeasible_claimed": False,
            "junction_node_id": self.junction_node_id,
            "ledger": self.ledger.to_canonical_dict(),
            "main_edge_id": self.main_edge_id,
            "ordering_basis": ORDERING_BASIS,
            "proposals": [item.to_canonical_dict() for item in self.proposals],
            "region": None if self.region is None else self.region.to_canonical_dict(),
            "skipped_state_counts": dict(self.skipped_state_counts),
            "source_endpoint_id": self.source_endpoint_id,
            "status": self.status.value,
            "system": self.system,
            "terminal_endpoint_ids": list(self.terminal_endpoint_ids),
            "termination": self.termination.value,
            "unresolved_reason": None if self.unresolved_reason is None else self.unresolved_reason.value,
            "usable_cell_semantics": USABLE_CELL_SEMANTICS,
        }


@dataclass(frozen=True, slots=True)
class CandidateJunctionPlacement:
    """All junction placement results for one topology candidate."""

    candidate_id: str
    candidate_kind: str
    evidence_frame_id: str
    endpoint_frame_id: str
    evidence_status: SpatialEvidenceStatus
    limits: JunctionSearchLimits
    junctions: tuple[JunctionPlacementResult, ...]
    diagnostics: tuple[SpatialDiagnostic, ...]

    def __post_init__(self) -> None:
        _require_tuple_of(self.junctions, JunctionPlacementResult, "Candidate junction results")
        _require_tuple_of(self.diagnostics, SpatialDiagnostic, "Candidate placement diagnostics")
        ids = tuple(item.junction_node_id for item in self.junctions)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("Junction results must be unique and ordered by node ID.")
        if any(item.candidate_id != self.candidate_id for item in self.junctions):
            raise ValueError("Junction results must belong to this candidate.")
        if any(len(item.proposals) > self.limits.max_candidates_per_junction
               or item.explored_cell_count > self.limits.max_explored_cells for item in self.junctions):
            raise ValueError("Junction results exceed the declared search limits.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "diagnostics": [item.to_canonical_dict() for item in self.diagnostics],
            "endpoint_frame_id": self.endpoint_frame_id,
            "evidence_frame_id": self.evidence_frame_id,
            "evidence_status": self.evidence_status.value,
            "format_version": PLACEMENT_FORMAT_VERSION,
            "junctions": [item.to_canonical_dict() for item in self.junctions],
            "limits": self.limits.to_canonical_dict(),
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"))


class _Diagnostics:
    def __init__(self) -> None:
        self.items: list[SpatialDiagnostic] = []
        self.reasons: list[UnresolvedPlacementReason] = []

    def add(self, code: str, severity: DiagnosticSeverity, message: str, subject_id: str | None = None) -> None:
        self.items.append(SpatialDiagnostic(code, severity, message, subject_id))

    def unresolved(self, reason: UnresolvedPlacementReason, severity: DiagnosticSeverity, message: str,
                   subject_id: str | None = None) -> None:
        self.reasons.append(reason)
        self.add(reason.value, severity, message, subject_id)

    def reason(self) -> UnresolvedPlacementReason | None:
        return next((item for item in _REASON_PRECEDENCE if item in self.reasons), None)

    def ordered(self) -> tuple[SpatialDiagnostic, ...]:
        return tuple(sorted(set(self.items), key=SpatialDiagnostic.sort_key))


@dataclass(frozen=True, slots=True)
class _JunctionGroup:
    node_id: str
    system: System
    main: TopologyEdge | None
    branches: tuple[TopologyEdge, ...]
    source_endpoint_id: str | None
    terminal_endpoint_ids: tuple[str, ...]
    connection_ids: tuple[str, ...]
    structure_error: str | None


def _identify_group(candidate: TopologyCandidate, node_id: str) -> _JunctionGroup:
    topology = candidate.demand.topology
    node_by_id = {node.node_id: node for node in topology.nodes}
    node = node_by_id[node_id]
    incident = [edge for edge in topology.edges if node_id in (edge.start_node_id, edge.end_node_id)]

    def far(edge: TopologyEdge) -> Any:
        return node_by_id[edge.end_node_id if edge.start_node_id == node_id else edge.start_node_id]

    mains = [edge for edge in incident if edge.role is TopologyEdgeRole.MAIN]
    branches = tuple(edge for edge in incident if edge.role is TopologyEdgeRole.BRANCH)
    error = None
    if node.kind is not TopologyNodeKind.JUNCTION:
        error = f"virtual node kind {node.kind.value} is not a JUNCTION"
    elif len(mains) != 1 or len(branches) < 2 or len(incident) != 1 + len(branches):
        error = "a SHARED_MAIN junction needs exactly one MAIN edge, two or more BRANCH edges and no other incident edges"
    elif any(edge.owner_edge_id is not None for edge in incident):
        error = "shared-ownership edges at a junction have no placement semantics yet"
    elif far(mains[0]).endpoint_id is None:
        error = "the MAIN edge must lead to an authoritative start endpoint"
    elif any(far(edge).endpoint_id is None for edge in branches):
        error = "every BRANCH edge must lead to an authoritative endpoint"
    main = mains[0] if len(mains) == 1 else None
    source = far(main).endpoint_id if main is not None else None
    terminals = tuple(sorted(far(edge).endpoint_id for edge in branches if far(edge).endpoint_id is not None))
    connections = tuple(sorted(
        item.connection_id for item in candidate.demand.assignments if main is not None and main.edge_id in item.edge_ids
    ))
    if error is None:
        connection_by_id = {item.connection_id: item for item in candidate.demand.problem.connections}
        if len(connections) < 2 or any(connection_by_id[item].start_endpoint_id != source for item in connections):
            error = "the MAIN edge must carry two or more connections that all start at its source endpoint"
        elif len(set(terminals)) != len(terminals):
            error = "branch terminals must be distinct"
    return _JunctionGroup(
        node_id, node.system, main, tuple(sorted(branches, key=lambda edge: edge.edge_id)), source, terminals,
        connections, error,
    )


def _requirements(group: _JunctionGroup) -> tuple[UnresolvedPhysicalRequirement, ...]:
    assert group.main is not None
    items = [
        UnresolvedPhysicalRequirement("JUNCTION_FITTING_GEOMETRY_UNEVALUATED", group.node_id,
                                      "fitting take-off geometry and minimum straight lengths (later phase)"),
        UnresolvedPhysicalRequirement("SHARED_TRUNK_PROFILE_UNDEFINED", group.main.edge_id,
                                      "trunk sizing for combined downstream demand (later phase)"),
        UnresolvedPhysicalRequirement("INTER_SYSTEM_CLEARANCE_UNEVALUATED", group.node_id,
                                      "authoritative clearance policy (Phase 9C.2)"),
        UnresolvedPhysicalRequirement("COLLISION_FREE_CERTIFICATION_UNAVAILABLE", group.node_id,
                                      "independent geometric validation (Phase 9H)"),
    ]
    for edge in (group.main, *group.branches):
        items.append(UnresolvedPhysicalRequirement("EDGE_ROUTE_GEOMETRY_UNRESOLVED", edge.edge_id,
                                                   "constrained routing search (Phase 9D)"))
    if group.system is System.DRAINAGE:
        items.append(UnresolvedPhysicalRequirement("GRAVITY_SLOPE_AND_INVERT_UNEVALUATED", group.main.edge_id,
                                                   "drainage slope and invert constraints (Phase 9D)"))
    return tuple(sorted(items, key=lambda item: (item.kind, item.subject_id)))


def _anchor(endpoint: Endpoint, role: str, grid: OccupancyGridReference | None, frame_matches: bool,
            diagnostics: _Diagnostics) -> EndpointAnchor:
    position = (endpoint.position.x, endpoint.position.y, endpoint.position.z)
    authoritative = endpoint.provenance in AUTHORITATIVE_POSITION_PROVENANCE
    if not authoritative:
        diagnostics.unresolved(
            UnresolvedPlacementReason.ENDPOINT_POSITION_NOT_AUTHORITATIVE, DiagnosticSeverity.INSUFFICIENT,
            f"Endpoint provenance {endpoint.provenance.value} does not establish a position to derive a search region from.",
            endpoint.endpoint_id,
        )
    cell = state = None
    if grid is not None and frame_matches:
        try:
            cell = grid.spec.world_to_containing_cell(*position)
        except ValueError:
            diagnostics.unresolved(
                UnresolvedPlacementReason.ENDPOINT_OUTSIDE_GRID, DiagnosticSeverity.INSUFFICIENT,
                "Endpoint lies outside the system grid domain; no spatial evidence covers it.", endpoint.endpoint_id,
            )
        else:
            state = CELL_STATE_NAMES.get(grid.cells[grid.spec.linear_index(*cell)], "UNKNOWN")
            if state != USABLE_CELL_STATE:
                diagnostics.add(
                    "ENDPOINT_CELL_NOT_FREE", DiagnosticSeverity.LIMITATION,
                    f"Endpoint containing cell is {state}; its position is used unchanged and is not snapped.",
                    endpoint.endpoint_id,
                )
    return EndpointAnchor(endpoint.endpoint_id, role, endpoint.flow_direction.value, endpoint.provenance.value,
                          authoritative, position, cell, state)


def _region(grid: OccupancyGridReference, cells: list[tuple[int, int, int]], margin: int) -> SearchRegion:
    spec = grid.spec
    dims = (spec.nx, spec.ny, spec.nz)
    low = tuple(max(0, min(cell[a] for cell in cells) - margin) for a in range(3))
    high = tuple(min(dims[a] - 1, max(cell[a] for cell in cells) + margin) for a in range(3))
    seed = tuple(sorted(cell[a] for cell in cells)[(len(cells) - 1) // 2] for a in range(3))
    size, origin = spec.voxel_size_m, (spec.origin_x, spec.origin_y, spec.origin_z)
    world = Bounds3(
        origin[0] + low[0] * size, origin[0] + (high[0] + 1) * size,
        origin[1] + low[1] * size, origin[1] + (high[1] + 1) * size,
        origin[2] + low[2] * size, origin[2] + (high[2] + 1) * size,
    )
    count = (high[0] - low[0] + 1) * (high[1] - low[1] + 1) * (high[2] - low[2] + 1)
    return SearchRegion(low, high, world, seed, count, margin)  # type: ignore[arg-type]


def _shell(region: SearchRegion, radius: int) -> list[tuple[int, int, int]]:
    """Cells of the region at exactly L1 index distance ``radius`` from the seed, as (k, j, i)."""
    seed, low, high = region.seed_cell, region.min_cell, region.max_cell
    cells = []
    for dk in range(max(low[2] - seed[2], -radius), min(high[2] - seed[2], radius) + 1):
        rest_k = radius - abs(dk)
        for dj in range(max(low[1] - seed[1], -rest_k), min(high[1] - seed[1], rest_k) + 1):
            rest = rest_k - abs(dj)
            for di in ((-rest, rest) if rest else (0,)):
                i = seed[0] + di
                if low[0] <= i <= high[0]:
                    cells.append((seed[2] + dk, seed[1] + dj, i))
    cells.sort()
    return cells


def _search(
    candidate_id: str, group: _JunctionGroup, grid: OccupancyGridReference, region: SearchRegion,
    excluded: set[int], evidence_ref: GridEvidenceReference, limits: JunctionSearchLimits, diagnostics: _Diagnostics,
) -> tuple[list[JunctionPlacementProposal], SearchTermination, int, Counter[str], int]:
    spec = grid.spec
    requirements = _requirements(group)
    seed = region.seed_cell
    max_radius = sum(max(seed[a] - region.min_cell[a], region.max_cell[a] - seed[a]) for a in range(3))
    proposals: list[JunctionPlacementProposal] = []
    skipped: Counter[str] = Counter()
    explored = excluded_count = 0
    for radius in range(max_radius + 1):
        for k, j, i in _shell(region, radius):
            if explored >= limits.max_explored_cells:
                return proposals, SearchTermination.EXPLORATION_LIMIT_REACHED, explored, skipped, excluded_count
            explored += 1
            linear = spec.linear_index(i, j, k)
            if linear in excluded:
                excluded_count += 1
                continue
            state = grid.cells[linear]
            if state != FREE:
                skipped[CELL_STATE_NAMES.get(state, "UNKNOWN")] += 1
                continue
            world = spec.cell_center(i, j, k)
            if spec.world_to_containing_cell(*world) != (i, j, k):
                diagnostics.add("GRID_WORLD_ROUNDTRIP_MISMATCH", DiagnosticSeverity.INVALID,
                                f"Cell {(i, j, k)} centre does not map back to its own index; cell not proposed.", group.node_id)
                continue
            proposals.append(JunctionPlacementProposal(
                candidate_id, group.node_id, group.system.value, len(proposals), (i, j, k), linear, world,
                USABLE_CELL_STATE, radius, evidence_ref, requirements,
            ))
            if len(proposals) == limits.max_candidates_per_junction:
                return proposals, SearchTermination.CANDIDATE_LIMIT_REACHED, explored, skipped, excluded_count
    return proposals, SearchTermination.REGION_EXHAUSTED, explored, skipped, excluded_count


def _place_junction(
    candidate: TopologyCandidate, node_id: str, evidence: SpatialEvidence, frame_matches: bool,
    endpoint_frame_id: str, limits: JunctionSearchLimits,
    extra_excluded_cells: tuple[tuple[int, int, int], ...] = (),
) -> JunctionPlacementResult:
    group = _identify_group(candidate, node_id)
    diagnostics = _Diagnostics()
    system = group.system.value
    common = dict(
        candidate_id=candidate.candidate_id, junction_node_id=node_id, system=system,
        source_endpoint_id=group.source_endpoint_id, terminal_endpoint_ids=group.terminal_endpoint_ids,
        main_edge_id=None if group.main is None else group.main.edge_id,
        branch_edge_ids=tuple(edge.edge_id for edge in group.branches), connection_ids=group.connection_ids,
    )

    def unresolved(anchors: tuple[EndpointAnchor, ...] = (), evidence_ok: bool = False) -> JunctionPlacementResult:
        return JunctionPlacementResult(
            **common, status=JunctionPlacementStatus.UNRESOLVED, unresolved_reason=diagnostics.reason(),
            anchors=anchors, region=None, termination=SearchTermination.NOT_SEARCHED, explored_cell_count=0,
            skipped_state_counts=(), excluded_endpoint_cell_count=0, proposals=(), diagnostics=diagnostics.ordered(),
            ledger=ValidationLedger(
                Establishment.NOT_ESTABLISHED if group.structure_error else Establishment.ESTABLISHED,
                Establishment.ESTABLISHED if evidence_ok else Establishment.NOT_ESTABLISHED,
                Establishment.NOT_ESTABLISHED,
            ),
        )

    if group.structure_error is not None:
        diagnostics.unresolved(UnresolvedPlacementReason.UNSUPPORTED_JUNCTION_STRUCTURE, DiagnosticSeverity.UNSUPPORTED,
                               f"Junction is not a supported SHARED_MAIN group: {group.structure_error}.", node_id)
        return unresolved()

    if evidence.status is not SpatialEvidenceStatus.EVIDENCE_AVAILABLE:
        blocking = sorted({item.code for item in evidence.diagnostics if item.severity is not DiagnosticSeverity.LIMITATION})
        diagnostics.unresolved(
            UnresolvedPlacementReason.SPATIAL_EVIDENCE_NOT_USABLE, DiagnosticSeverity.INSUFFICIENT,
            f"Spatial evidence status is {evidence.status.value} ({', '.join(blocking)}); unknown space is not treated as free.",
            node_id,
        )
    if not frame_matches:
        diagnostics.unresolved(
            UnresolvedPlacementReason.COORDINATE_FRAME_MISMATCH, DiagnosticSeverity.INVALID,
            f"Endpoint frame {endpoint_frame_id} differs from evidence frame {evidence.coordinate_reference.frame_id}; "
            "positions are not reinterpreted.", node_id,
        )
    grid = evidence.grid_for(system)
    if grid is None:
        diagnostics.unresolved(UnresolvedPlacementReason.NO_COMPATIBLE_GRID, DiagnosticSeverity.INSUFFICIENT,
                               f"No occupancy grid exists for {system}; other systems' grids are never substituted.", node_id)
    endpoint_by_id = {item.endpoint_id: item for item in candidate.demand.problem.endpoints}
    anchors = tuple(
        _anchor(endpoint_by_id[endpoint_id], role, grid, frame_matches, diagnostics)
        for endpoint_id, role in [(group.source_endpoint_id, "SOURCE"),
                                  *((item, "TERMINAL") for item in group.terminal_endpoint_ids)]
    )
    evidence_ok = evidence.status is SpatialEvidenceStatus.EVIDENCE_AVAILABLE and frame_matches and grid is not None
    if diagnostics.reason() is not None or grid is None:
        return unresolved(anchors, evidence_ok)

    cells = [anchor.containing_cell for anchor in anchors]
    region = _region(grid, cells, limits.region_margin_cells)  # type: ignore[arg-type]
    excluded = {grid.spec.linear_index(*cell) for cell in cells}  # type: ignore[misc]
    for cell in extra_excluded_cells:
        try:
            excluded.add(grid.spec.linear_index(*cell))
        except (ValueError, IndexError):
            diagnostics.add(
                "EXCLUDED_CELL_OUTSIDE_GRID", DiagnosticSeverity.LIMITATION,
                f"Extra excluded cell {list(cell)} lies outside the {system} grid and was ignored.", node_id,
            )
    if extra_excluded_cells:
        diagnostics.add(
            "EXTRA_CELLS_EXCLUDED_FROM_JUNCTION_SEARCH", DiagnosticSeverity.LIMITATION,
            f"Excluded {len(extra_excluded_cells)} additional cell(s) (endpoint-entry / mode cells) from junction search "
            "so a shared MAIN cannot collapse onto a selected endpoint or entry cell.",
            node_id,
        )
    reference = GridEvidenceReference(
        evidence.coordinate_reference.frame_id, evidence.provenance.source_model_id, grid.scenario_id, grid.system,
        grid.cells_sha256, grid.spec.voxel_size_m, grid.clearance_m, grid.required_planar_margin_m,
        grid.required_vertical_margin_m, grid.clearance_policy_digest_sha256,
    )
    proposals, termination, explored, skipped, excluded_count = _search(
        candidate.candidate_id, group, grid, region, excluded, reference, limits, diagnostics,
    )
    if termination is SearchTermination.CANDIDATE_LIMIT_REACHED:
        diagnostics.add("CANDIDATE_LIMIT_REACHED", DiagnosticSeverity.LIMITATION,
                        "Search stopped at the candidate limit; further usable cells may exist.", node_id)
    elif termination is SearchTermination.EXPLORATION_LIMIT_REACHED:
        diagnostics.add("EXPLORATION_LIMIT_REACHED", DiagnosticSeverity.LIMITATION,
                        "Search stopped at the exploration limit before exhausting its region.", node_id)
    if not proposals:
        scope = "the explored part of" if termination is SearchTermination.EXPLORATION_LIMIT_REACHED else "the whole"
        diagnostics.unresolved(
            UnresolvedPlacementReason.NO_USABLE_CELL_IN_BOUNDED_REGION, DiagnosticSeverity.INSUFFICIENT,
            f"No FREE cell was found in {scope} bounded search region; this is not evidence that no junction placement "
            "or route exists.", node_id,
        )
    status = JunctionPlacementStatus.PROPOSALS_GENERATED if proposals else JunctionPlacementStatus.UNRESOLVED
    return JunctionPlacementResult(
        **common, status=status, unresolved_reason=None if proposals else diagnostics.reason(), anchors=anchors,
        region=region, termination=termination, explored_cell_count=explored,
        skipped_state_counts=tuple(sorted(skipped.items())), excluded_endpoint_cell_count=excluded_count,
        proposals=tuple(proposals), diagnostics=diagnostics.ordered(),
        ledger=ValidationLedger(
            Establishment.ESTABLISHED, Establishment.ESTABLISHED,
            Establishment.ESTABLISHED if proposals else Establishment.NOT_ESTABLISHED,
        ),
    )


def _coincidence_diagnostics(junctions: tuple[JunctionPlacementResult, ...]) -> list[SpatialDiagnostic]:
    """Flag distinct junctions proposing the same world cell; proposals are never merged or shared."""
    owners: dict[tuple[float, float, float], set[tuple[str, str]]] = {}
    for junction in junctions:
        for proposal in junction.proposals:
            key = tuple(_round(value) for value in proposal.world_position_m)
            owners.setdefault(key, set()).add((junction.junction_node_id, junction.system))  # type: ignore[arg-type]
    items = []
    for key, members in sorted(owners.items()):
        if len(members) < 2:
            continue
        nodes = ", ".join(sorted(node for node, _ in members))
        cross_system = len({system for _, system in members}) > 1
        items.append(SpatialDiagnostic(
            "CROSS_SYSTEM_COINCIDENT_PROPOSALS" if cross_system else "COINCIDENT_PROPOSALS_ACROSS_JUNCTIONS",
            DiagnosticSeverity.LIMITATION,
            f"Junctions {nodes} propose the same cell centre {list(key)}; they remain independent alternatives and "
            "must not be selected together without clearance evaluation.",
        ))
    return items


def propose_junction_placements(
    candidate: TopologyCandidate,
    evidence: SpatialEvidence,
    *,
    endpoint_frame_id: str,
    limits: JunctionSearchLimits | None = None,
    extra_excluded_cells: tuple[tuple[int, int, int], ...] = (),
) -> CandidateJunctionPlacement:
    """Generate bounded FREE-cell junction proposals for every virtual junction of a candidate.

    ``endpoint_frame_id`` declares the frame of the candidate's endpoint
    positions; a mismatch with the evidence frame leaves every junction
    unresolved.  The candidate and its demand are not modified.
    ``extra_excluded_cells`` may list selected endpoint-entry cells that must
    not become junction centres (prevents zero-length shared MAIN collapses).
    """
    if not isinstance(candidate, TopologyCandidate):
        raise ValueError("Junction placement requires a TopologyCandidate.")
    if not isinstance(evidence, SpatialEvidence):
        raise ValueError("Junction placement requires SpatialEvidence.")
    _require_text(endpoint_frame_id, "endpoint_frame_id")
    limits = limits or JunctionSearchLimits()
    if not isinstance(limits, JunctionSearchLimits):
        raise ValueError("Junction placement limits must be JunctionSearchLimits.")
    if any(len(cell) != 3 or any(isinstance(v, bool) or not isinstance(v, int) for v in cell) for cell in extra_excluded_cells):
        raise ValueError("extra_excluded_cells must be integer grid index triples.")
    frame_matches = endpoint_frame_id == evidence.coordinate_reference.frame_id
    virtual = [node.node_id for node in candidate.demand.topology.nodes if node.endpoint_id is None]
    junctions = tuple(
        _place_junction(candidate, node_id, evidence, frame_matches, endpoint_frame_id, limits, extra_excluded_cells)
        for node_id in virtual
    )
    diagnostics = _coincidence_diagnostics(junctions)
    if not junctions:
        diagnostics.append(SpatialDiagnostic(
            "NO_VIRTUAL_JUNCTIONS", DiagnosticSeverity.LIMITATION,
            f"{candidate.kind.value} candidate has no logical junction to place.",
        ))
    return CandidateJunctionPlacement(
        candidate.candidate_id, candidate.kind.value, evidence.coordinate_reference.frame_id, endpoint_frame_id,
        evidence.status, limits, junctions, tuple(sorted(diagnostics, key=SpatialDiagnostic.sort_key)),
    )


def propose_junction_placements_for_candidates(
    candidates: tuple[TopologyCandidate, ...],
    evidence: SpatialEvidence,
    *,
    endpoint_frame_id: str,
    limits: JunctionSearchLimits | None = None,
) -> tuple[CandidateJunctionPlacement, ...]:
    """Place junctions independently per candidate, in the planner's candidate order."""
    return tuple(
        propose_junction_placements(item, evidence, endpoint_frame_id=endpoint_frame_id, limits=limits)
        for item in candidates
    )

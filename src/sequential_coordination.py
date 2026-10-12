"""Phase 9E.0 deterministic sequential multi-system coordination.

Higher-priority systems route first. Successful physical edges reserve
world-space swept envelopes onto lower-priority derived grids using
``BENCHMARK_V1`` / ``legacy_reservation_margin_m`` (same provenance as B1).

Immutable SpatialEvidence grids are never mutated. Reservation success is not
continuous-geometry clash freedom. Same-system clearance stays UNDEFINED.
Physical connector engagement is never certified. PBS/LNS are out of scope.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import time
from typing import Any

from .clearance_policy import ClearancePolicy, legacy_reservation_margin_m
from .endpoint_entry import diagnose_routing_problem_endpoints
from .endpoint_entry_policy import authorize_controlled_entries, benchmark_controlled_entry_v1
from .endpoints import RoutingProblem
from .multi_system_diagnostics import diagnose_multi_system_conflicts
from .multi_system_routing import (
    ConnectionRouteRecord,
    ConnectionRouteStatus,
    EndpointCellMode,
    MultiSystemRoutingResult,
    PhysicalEdgeRecord,
    _normalize_endpoint_mode,
    route_multi_system,
)
from .physical_edge_routing import PhysicalEdgeRoute, PhysicalEdgeRouteStatus
from .route_geometry import compress_path_cells, swept_segment_aabb
from .spatial_evidence import OccupancyGridReference, SpatialEvidence
from .voxel import BLOCKED_PRIOR_ROUTE, FREE, SYSTEM_ORDER, aabb_index_ranges

COORDINATION_METHOD_ID = "SEQUENTIAL_RESERVATION_V1"
RESERVATION_PROVENANCE = (
    "legacy_reservation_margin_m = prior_envelope + target_envelope + BENCHMARK_V1 inter-system rule "
    "(legacy 0.05 m pairwise provenance, identical to B1 block_prior_routes; not a measured engineering standard)"
)


class ReservationStatus(str, Enum):
    APPLIED = "APPLIED"
    SKIPPED_NO_ROUTED_EDGES = "SKIPPED_NO_ROUTED_EDGES"
    SKIPPED_NO_LOWER_PRIORITY_TARGETS = "SKIPPED_NO_LOWER_PRIORITY_TARGETS"


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


def coordination_system_order(problem: RoutingProblem) -> tuple[str, ...]:
    """Deterministic research priority: stable SYSTEM_ORDER intersected with demand."""
    demand = {connection.system.value for connection in problem.connections}
    known = tuple(system for system in SYSTEM_ORDER if system in demand)
    extra = tuple(sorted(demand - set(SYSTEM_ORDER)))
    return known + extra


def system_routing_problem(problem: RoutingProblem, system: str) -> RoutingProblem:
    """Immutable one-system demand slice; preserves authoritative IDs and ordering."""
    connections = tuple(item for item in problem.connections if item.system.value == system)
    if not connections:
        raise ValueError(f"No authoritative connections for system {system}.")
    used = {item.start_endpoint_id for item in connections} | {item.end_endpoint_id for item in connections}
    endpoints = tuple(item for item in problem.endpoints if item.endpoint_id in used)
    return RoutingProblem(endpoints, connections, dict(problem.metadata))


def clone_grid_cells(evidence: SpatialEvidence) -> dict[str, bytearray]:
    """Mutable per-system copies; source evidence grids remain immutable bytes."""
    return {grid.system: bytearray(grid.cells) for grid in evidence.grids}


def evidence_with_derived_grids(evidence: SpatialEvidence, derived_cells: dict[str, bytearray]) -> SpatialEvidence:
    """Build an isolated SpatialEvidence view over derived reservation cells."""
    grids: list[OccupancyGridReference] = []
    for grid in evidence.grids:
        cells = bytes(derived_cells.get(grid.system, grid.cells))
        grids.append(
            OccupancyGridReference(
                grid.scenario_id,
                grid.system,
                grid.spec,
                grid.clearance_m,
                grid.required_planar_margin_m,
                grid.required_vertical_margin_m,
                grid.shaft_bounds,
                hashlib.sha256(cells).hexdigest(),
                cells,
                grid.clearance_policy_digest_sha256,
            )
        )
    return replace(evidence, grids=tuple(grids))


def reserve_physical_edge_on_target(
    *,
    target_cells: bytearray,
    target_spec,
    source_spec,
    path_cells: tuple[tuple[int, int, int], ...],
    prior_system: str,
    target_system: str,
    policy: ClearancePolicy,
) -> int:
    """Mark FREE target cells excluded by one prior physical edge via world-space envelopes.

    Path cells are interpreted in ``source_spec`` world coordinates, then rasterized
    onto ``target_spec``. Indices are never copied between grids.
    """
    if not path_cells:
        return 0
    planar, vertical = legacy_reservation_margin_m(policy, prior_system, target_system)
    marked = 0
    for segment in compress_path_cells([list(cell) for cell in path_cells], source_spec):
        exclusion = swept_segment_aabb(segment, planar, vertical)
        xs, ys, zs = aabb_index_ranges(target_spec, exclusion)
        for k in zs:
            for j in ys:
                base = target_spec.linear_index(0, j, k)
                for i in xs:
                    index = base + i
                    if target_cells[index] == FREE:
                        target_cells[index] = BLOCKED_PRIOR_ROUTE
                        marked += 1
    return marked


@dataclass(frozen=True, slots=True)
class ReservationEvent:
    prior_system: str
    target_system: str
    physical_edge_id: str
    cells_marked: int
    planar_margin_m: float
    vertical_margin_m: float

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "cells_marked": self.cells_marked,
            "physical_edge_id": self.physical_edge_id,
            "planar_margin_m": _round(self.planar_margin_m),
            "prior_system": self.prior_system,
            "target_system": self.target_system,
            "vertical_margin_m": _round(self.vertical_margin_m),
        }


@dataclass(frozen=True, slots=True)
class SystemCoordinationStep:
    system: str
    priority_rank: int
    required_connection_ids: tuple[str, ...]
    routed_connection_ids: tuple[str, ...]
    reservation_status: ReservationStatus
    dynamic_blocked_cell_count_before: int
    free_cell_count_before: int
    cells_marked_onto_lower_systems: int
    expanded_node_count: int
    selected_strategy: str | None
    reason: str | None

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "cells_marked_onto_lower_systems": self.cells_marked_onto_lower_systems,
            "dynamic_blocked_cell_count_before": self.dynamic_blocked_cell_count_before,
            "expanded_node_count": self.expanded_node_count,
            "free_cell_count_before": self.free_cell_count_before,
            "priority_rank": self.priority_rank,
            "reason": self.reason,
            "required_connection_ids": list(self.required_connection_ids),
            "reservation_status": self.reservation_status.value,
            "routed_connection_ids": list(self.routed_connection_ids),
            "selected_strategy": self.selected_strategy,
            "system": self.system,
        }


@dataclass(frozen=True, slots=True)
class SequentialCoordinationResult:
    method_id: str
    scenario_id: str
    evidence_frame_id: str
    policy_id: str
    policy_digest_sha256: str
    system_order: tuple[str, ...]
    endpoint_cell_mode: EndpointCellMode
    routing: MultiSystemRoutingResult
    steps: tuple[SystemCoordinationStep, ...]
    reservation_events: tuple[ReservationEvent, ...]
    source_grid_digests: tuple[tuple[str, str], ...]
    derived_grid_digests_after: tuple[tuple[str, str], ...]
    reservation_provenance: str
    elapsed_s: float
    collision_free_claimed: bool = False
    connector_engagement_validated: bool = False

    def __post_init__(self) -> None:
        if self.method_id != COORDINATION_METHOD_ID:
            raise ValueError("Only SEQUENTIAL_RESERVATION_V1 is defined in this milestone.")
        if self.collision_free_claimed:
            raise ValueError("Sequential coordination cannot claim collision freedom.")
        if self.connector_engagement_validated:
            raise ValueError("Sequential coordination cannot claim connector engagement validation.")
        if not self.routing.inter_system_reservation_applied:
            raise ValueError("Coordinated routing results must record inter_system_reservation_applied.")
        if tuple(item.connection_id for item in self.routing.connections) != self.routing.required_connection_ids:
            raise ValueError("Coordinated results must preserve every required connection identity.")

    def summary(self) -> dict[str, Any]:
        routing_summary = self.routing.summary()
        return {
            **routing_summary,
            "elapsed_s": _round(self.elapsed_s),
            "method_id": self.method_id,
            "reservation_event_count": len(self.reservation_events),
            "reservation_cells_marked_total": sum(item.cells_marked for item in self.reservation_events),
            "system_order": list(self.system_order),
            "collision_free_claimed": False,
            "connector_engagement_validated": False,
            "continuous_geometry_certified": False,
        }

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "collision_free_claimed": False,
            "connector_engagement_validated": False,
            "derived_grid_digests_after": [{"system": system, "cells_sha256": digest} for system, digest in self.derived_grid_digests_after],
            "elapsed_s": _round(self.elapsed_s),
            "endpoint_cell_mode": self.endpoint_cell_mode.value,
            "evidence_frame_id": self.evidence_frame_id,
            "method_id": self.method_id,
            "policy_digest_sha256": self.policy_digest_sha256,
            "policy_id": self.policy_id,
            "reservation_events": [item.to_canonical_dict() for item in self.reservation_events],
            "reservation_provenance": self.reservation_provenance,
            "routing": self.routing.to_canonical_dict(),
            "scenario_id": self.scenario_id,
            "source_grid_digests": [{"system": system, "cells_sha256": digest} for system, digest in self.source_grid_digests],
            "steps": [item.to_canonical_dict() for item in self.steps],
            "summary": self.summary(),
            "system_order": list(self.system_order),
            "unresolved_physical_requirements": [
                "FITTING_GEOMETRY_UNEVALUATED",
                "CONNECTOR_ENGAGEMENT_UNEVALUATED",
                "EXACT_COLLISION_CERTIFICATION_UNAVAILABLE",
                "SAME_SYSTEM_CLEARANCE_UNDEFINED",
                "RESERVATION_IS_AABB_PROXY_NOT_CONTINUOUS_GEOMETRY",
            ],
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"))


def _remint_edge_to_source_digest(edge: PhysicalEdgeRecord, source_digest: str) -> PhysicalEdgeRecord:
    """Stamp the immutable source-grid digest so independent diagnostics can evaluate paths."""
    route = edge.route
    reminted = PhysicalEdgeRoute(
        route.status,
        route.edge_id,
        route.system,
        route.start_node_id,
        route.end_node_id,
        route.grid_cells,
        route.world_centerline_m,
        route.grid_path_length_m,
        route.expanded_node_count,
        route.policy_id,
        route.policy_digest_sha256,
        source_digest,
        route.reason,
    )
    return replace(edge, route=reminted)


def _selected_strategy(result: MultiSystemRoutingResult, system: str) -> tuple[str | None, str | None]:
    selected = [item for item in result.attempts if item.system == system and item.selected]
    if not selected:
        return None, "NO_SELECTED_ATTEMPT"
    item = selected[0]
    return item.strategy.value, item.reason


def coordinate_systems_sequentially(
    problem: RoutingProblem,
    evidence: SpatialEvidence,
    policy: ClearancePolicy,
    *,
    endpoint_frame_id: str,
    endpoint_cell_mode: EndpointCellMode = EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY,
    max_expansions: int = 50000,
    max_proposal_attempts: int = 3,
) -> SequentialCoordinationResult:
    """Route systems in SYSTEM_ORDER with pairwise world-space reservation between systems."""
    if not isinstance(problem, RoutingProblem) or not isinstance(evidence, SpatialEvidence) or not isinstance(policy, ClearancePolicy):
        raise ValueError("Sequential coordination requires RoutingProblem, SpatialEvidence and ClearancePolicy.")
    if max_expansions < 1 or max_proposal_attempts < 1:
        raise ValueError("Coordination limits must be positive.")
    if not isinstance(endpoint_cell_mode, EndpointCellMode):
        raise ValueError("Endpoint cell mode must be an EndpointCellMode.")
    if endpoint_frame_id != evidence.coordinate_reference.frame_id:
        raise ValueError("Endpoint frame must match the evidence coordinate frame.")

    started = time.perf_counter()
    mode = _normalize_endpoint_mode(endpoint_cell_mode)
    order = coordination_system_order(problem)
    source_digests = tuple((grid.system, grid.cells_sha256) for grid in evidence.grids)
    source_by_system = {grid.system: grid for grid in evidence.grids}
    derived = clone_grid_cells(evidence)
    source_bytes = {system: bytes(cells) for system, cells in derived.items()}

    entry_report = diagnose_routing_problem_endpoints(problem, evidence, policy, endpoint_frame_id=endpoint_frame_id)
    controlled = None
    if mode is EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY:
        controlled = authorize_controlled_entries(entry_report, evidence, policy, benchmark_controlled_entry_v1())

    records: dict[str, ConnectionRouteRecord] = {}
    edges: list[PhysicalEdgeRecord] = []
    attempts = []
    steps: list[SystemCoordinationStep] = []
    events: list[ReservationEvent] = []
    snaps = []

    for rank, system in enumerate(order, start=1):
        required_ids = tuple(item.connection_id for item in problem.connections if item.system.value == system)
        working_evidence = evidence_with_derived_grids(evidence, derived)
        grid = working_evidence.grid_for(system)
        blocked_before = 0 if grid is None else grid.cells.count(BLOCKED_PRIOR_ROUTE)
        free_before = 0 if grid is None else grid.cells.count(FREE)

        # Plan on the full problem so candidate/physical-edge IDs stay globally unique.
        partial = route_multi_system(
            problem,
            working_evidence,
            policy,
            endpoint_frame_id=endpoint_frame_id,
            max_expansions=max_expansions,
            max_proposal_attempts=max_proposal_attempts,
            endpoint_cell_mode=mode,
            only_systems=(system,),
        )
        snaps.extend(partial.endpoint_snaps)
        attempts.extend(partial.attempts)
        for record in partial.connections:
            records[record.connection_id] = record
        source_digest = source_by_system[system].cells_sha256 if system in source_by_system else ""
        reminted_edges = []
        for edge in partial.edges:
            reminted_edges.append(_remint_edge_to_source_digest(edge, source_digest) if source_digest else edge)
        edges.extend(reminted_edges)

        routed_ids = tuple(item.connection_id for item in partial.connections if item.status is ConnectionRouteStatus.ROUTED)
        strategy, reason = _selected_strategy(partial, system)
        expanded = sum(item.expanded_node_count for item in partial.attempts if item.selected)

        lower = [item for item in order[rank:] if item in derived]
        marked_total = 0
        connection_status = {item.connection_id: item.status for item in partial.connections}
        # Reserve only edges whose owning connections are fully ROUTED (same rule as diagnostics).
        routed_physical = [
            edge for edge in reminted_edges
            if edge.route.status is PhysicalEdgeRouteStatus.ROUTED
            and edge.route.grid_cells
            and all(connection_status.get(connection_id) is ConnectionRouteStatus.ROUTED for connection_id in edge.connection_ids)
        ]
        if not routed_physical:
            reservation_status = ReservationStatus.SKIPPED_NO_ROUTED_EDGES
        elif not lower:
            reservation_status = ReservationStatus.SKIPPED_NO_LOWER_PRIORITY_TARGETS
        else:
            reservation_status = ReservationStatus.APPLIED
            source_spec = source_by_system[system].spec
            # Shared MAIN is one physical edge: reserve each accepted edge once.
            for edge in routed_physical:
                for target in lower:
                    target_grid = source_by_system[target]
                    planar, vertical = legacy_reservation_margin_m(policy, system, target)
                    marked = reserve_physical_edge_on_target(
                        target_cells=derived[target],
                        target_spec=target_grid.spec,
                        source_spec=source_spec,
                        path_cells=edge.route.grid_cells,
                        prior_system=system,
                        target_system=target,
                        policy=policy,
                    )
                    marked_total += marked
                    events.append(
                        ReservationEvent(system, target, edge.physical_edge_id, marked, planar, vertical)
                    )

        steps.append(
            SystemCoordinationStep(
                system,
                rank,
                required_ids,
                routed_ids,
                reservation_status,
                blocked_before,
                free_before,
                marked_total,
                expanded,
                strategy,
                reason,
            )
        )

    for grid in evidence.grids:
        if bytes(grid.cells) != source_bytes[grid.system]:
            raise RuntimeError("Sequential coordination mutated immutable source evidence grids.")

    required = tuple(item.connection_id for item in problem.connections)
    missing = [item for item in required if item not in records]
    if missing:
        raise RuntimeError(f"Sequential coordination lost connection identities: {missing}")

    final_connections = tuple(records[item] for item in required)
    routed_count = sum(item.status is ConnectionRouteStatus.ROUTED for item in final_connections)
    sorted_edges = tuple(
        sorted(
            edges,
            key=lambda edge: (
                SYSTEM_ORDER.index(edge.system) if edge.system in SYSTEM_ORDER else len(SYSTEM_ORDER),
                edge.physical_edge_id,
            ),
        )
    )
    unique_snaps = tuple(sorted({item.endpoint_id: item for item in snaps}.values(), key=lambda item: item.endpoint_id))
    routing = MultiSystemRoutingResult(
        evidence.provenance.scenario_id,
        evidence.coordinate_reference.frame_id,
        policy.policy_id,
        policy.digest_sha256,
        order,
        required,
        final_connections,
        sorted_edges,
        tuple(attempts),
        (
            ("max_expansions_per_edge", max_expansions),
            ("max_proposal_attempts_per_junction", max_proposal_attempts),
        ),
        mode,
        unique_snaps,
        entry_report,
        controlled,
        routed_count == len(required),
        False,
        mode is EndpointCellMode.CONTAINING_CELL_ONLY,
        True,
        False,
    )
    derived_after = tuple(
        (system, hashlib.sha256(bytes(derived[system])).hexdigest()) for system in order if system in derived
    )
    elapsed = time.perf_counter() - started
    return SequentialCoordinationResult(
        COORDINATION_METHOD_ID,
        evidence.provenance.scenario_id,
        evidence.coordinate_reference.frame_id,
        policy.policy_id,
        policy.digest_sha256,
        order,
        mode,
        routing,
        tuple(steps),
        tuple(events),
        source_digests,
        derived_after,
        RESERVATION_PROVENANCE,
        elapsed,
    )


def coordinate_and_diagnose(
    problem: RoutingProblem,
    evidence: SpatialEvidence,
    policy: ClearancePolicy,
    *,
    endpoint_frame_id: str,
    endpoint_cell_mode: EndpointCellMode = EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY,
    max_expansions: int = 50000,
    max_proposal_attempts: int = 3,
) -> tuple[SequentialCoordinationResult, dict[str, Any]]:
    """Coordinate then run independent multi-system diagnostics against source evidence."""
    result = coordinate_systems_sequentially(
        problem,
        evidence,
        policy,
        endpoint_frame_id=endpoint_frame_id,
        endpoint_cell_mode=endpoint_cell_mode,
        max_expansions=max_expansions,
        max_proposal_attempts=max_proposal_attempts,
    )
    diagnostics = diagnose_multi_system_conflicts(result.routing, evidence, policy)
    return result, diagnostics

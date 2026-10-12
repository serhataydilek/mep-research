"""Phase 9D.3 bounded multi-system physical routing for one controlled scenario.

Each MEP system is routed independently on its own existing occupancy grid.
No inter-system reservation is applied, so conflicts are measured afterwards
by ``multi_system_diagnostics``; they are never prevented or certified here.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import json
from typing import Any
from .clearance_policy import ClearancePolicy
from .endpoints import RoutingProblem
from .junction_placement import JunctionPlacementStatus, propose_junction_placements
from .physical_edge_routing import PhysicalEdgeRoute, PhysicalEdgeRouteStatus, route_physical_edge
from .physical_network_routing import route_shared_main_group
from .endpoint_entry import EndpointEntryReport, diagnose_routing_problem_endpoints
from .endpoint_entry_policy import ControlledEntryDecision, authorize_controlled_entries, benchmark_controlled_entry_v1
from .spatial_evidence import EndpointGridCompatibility, SpatialEvidence, assess_topology_candidate
from .topology import TopologyEdgeRole
from .topology_planner import TopologyCandidate, TopologyCandidateKind, plan_topology_candidates
from .voxel import SYSTEM_ORDER

GRAVITY_SYSTEMS = frozenset({"drainage"})


class ConnectionRouteStatus(str, Enum):
    ROUTED = "ROUTED"; FAILED = "FAILED"; UNSUPPORTED = "UNSUPPORTED"; UNRESOLVED = "UNRESOLVED"


class RoutingStrategy(str, Enum):
    SHARED_MAIN = "SHARED_MAIN"; DIRECT = "DIRECT"


class EndpointCellMode(str, Enum):
    """Three distinct concepts: strict anchors, policy-authorized benchmark entry, legacy snap exploration.

    None of these modes certifies physical connector engagement.
    """
    CONTAINING_CELL_ONLY = "CONTAINING_CELL_ONLY"
    BENCHMARK_CONTROLLED_ENTRY = "BENCHMARK_CONTROLLED_ENTRY"
    EXPLORATORY_ENTRY_CANDIDATE = "EXPLORATORY_ENTRY_CANDIDATE"  # alias retained; resolves to BENCHMARK_CONTROLLED_ENTRY
    EVIDENCE_SNAP_WITHIN_TOLERANCE = "EVIDENCE_SNAP_WITHIN_TOLERANCE"


AUTHORIZED_BENCHMARK_MODES = frozenset({
    EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY,
    EndpointCellMode.EXPLORATORY_ENTRY_CANDIDATE,
})
EXPLORATORY_MODES = frozenset({
    EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY,
    EndpointCellMode.EXPLORATORY_ENTRY_CANDIDATE,
    EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE,
})


def _normalize_endpoint_mode(mode: EndpointCellMode) -> EndpointCellMode:
    if mode is EndpointCellMode.EXPLORATORY_ENTRY_CANDIDATE:
        return EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY
    return mode


@dataclass(frozen=True, slots=True)
class EndpointSnap:
    endpoint_id: str; system: str; containing_cell: tuple[int, int, int]; containing_cell_state: str
    snap_cell: tuple[int, int, int]; snap_world_m: tuple[float, float, float]; snap_distance_m: float

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"endpoint_id": self.endpoint_id, "system": self.system, "containing_cell": list(self.containing_cell),
                "containing_cell_state": self.containing_cell_state, "snap_cell": list(self.snap_cell),
                "snap_world_m": [_round(v) for v in self.snap_world_m], "snap_distance_m": _round(self.snap_distance_m)}


_EDGE_TO_CONNECTION_STATUS = {
    PhysicalEdgeRouteStatus.NOT_FOUND: ConnectionRouteStatus.FAILED,
    PhysicalEdgeRouteStatus.SEARCH_LIMIT_REACHED: ConnectionRouteStatus.FAILED,
    PhysicalEdgeRouteStatus.INVALID_INPUT: ConnectionRouteStatus.UNRESOLVED,
    PhysicalEdgeRouteStatus.UNSUPPORTED: ConnectionRouteStatus.UNSUPPORTED,
}
_STATUS_SEVERITY = (ConnectionRouteStatus.UNSUPPORTED, ConnectionRouteStatus.UNRESOLVED, ConnectionRouteStatus.FAILED)


def physical_edge_id(candidate_id: str, edge_id: str) -> str:
    """Topology edge IDs repeat across candidates, so physical identity is candidate-qualified."""
    return f"{candidate_id}#{edge_id}"


@dataclass(frozen=True, slots=True)
class PhysicalEdgeRecord:
    physical_edge_id: str; candidate_id: str; edge_id: str; system: str; role: str
    owner_system: str; connection_ids: tuple[str, ...]; start_node_key: str; end_node_key: str
    route: PhysicalEdgeRoute

    def to_canonical_dict(self) -> dict[str, Any]:
        r = self.route
        return {"physical_edge_id": self.physical_edge_id, "candidate_id": self.candidate_id, "edge_id": self.edge_id,
                "system": self.system, "role": self.role, "owner_system": self.owner_system,
                "connection_ids": list(self.connection_ids), "start_node_key": self.start_node_key, "end_node_key": self.end_node_key,
                "status": r.status.value, "reason": r.reason, "grid_cells": [list(x) for x in r.grid_cells],
                "world_centerline_m": [[_round(v) for v in p] for p in r.world_centerline_m],
                "grid_path_length_m": None if r.grid_path_length_m is None else _round(r.grid_path_length_m),
                "expanded_node_count": r.expanded_node_count, "grid_cells_sha256": r.grid_cells_sha256,
                "policy_id": r.policy_id, "policy_digest_sha256": r.policy_digest_sha256}


@dataclass(frozen=True, slots=True)
class StrategyAttempt:
    system: str; group_id: str; strategy: RoutingStrategy; candidate_id: str
    junction_node_id: str | None; proposal_rank: int | None; junction_world_m: tuple[float, float, float] | None
    required_connection_ids: tuple[str, ...]; routed_connection_ids: tuple[str, ...]; reason: str | None; selected: bool
    expanded_node_count: int

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"system": self.system, "group_id": self.group_id, "strategy": self.strategy.value, "candidate_id": self.candidate_id,
                "junction_node_id": self.junction_node_id, "proposal_rank": self.proposal_rank,
                "junction_world_m": None if self.junction_world_m is None else [_round(v) for v in self.junction_world_m],
                "required_connection_ids": list(self.required_connection_ids), "routed_connection_ids": list(self.routed_connection_ids),
                "complete": len(self.routed_connection_ids) == len(self.required_connection_ids),
                "reason": self.reason, "selected": self.selected, "expanded_node_count": self.expanded_node_count}


@dataclass(frozen=True, slots=True)
class ConnectionRouteRecord:
    connection_id: str; system: str; group_id: str; strategy: RoutingStrategy; candidate_id: str
    physical_edge_ids: tuple[str, ...]; status: ConnectionRouteStatus; reason: str | None
    grid_cells: tuple[tuple[int, int, int], ...]; unvalidated_requirements: tuple[str, ...]

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"connection_id": self.connection_id, "system": self.system, "group_id": self.group_id, "strategy": self.strategy.value,
                "candidate_id": self.candidate_id, "physical_edge_ids": list(self.physical_edge_ids), "status": self.status.value,
                "reason": self.reason, "grid_cell_count": len(self.grid_cells), "unvalidated_requirements": list(self.unvalidated_requirements)}


@dataclass(frozen=True, slots=True)
class MultiSystemRoutingResult:
    scenario_id: str; evidence_frame_id: str; policy_id: str; policy_digest_sha256: str
    systems: tuple[str, ...]; required_connection_ids: tuple[str, ...]
    connections: tuple[ConnectionRouteRecord, ...]; edges: tuple[PhysicalEdgeRecord, ...]; attempts: tuple[StrategyAttempt, ...]
    limits: tuple[tuple[str, int], ...]; endpoint_cell_mode: EndpointCellMode = EndpointCellMode.CONTAINING_CELL_ONLY
    endpoint_snaps: tuple[EndpointSnap, ...] = (); endpoint_entry: EndpointEntryReport | None = None
    controlled_entry: ControlledEntryDecision | None = None
    grid_paths_complete: bool = False; connector_engagement_validated: bool = False
    strict_mode: bool = True; inter_system_reservation_applied: bool = False; collision_free_claimed: bool = False

    def __post_init__(self) -> None:
        if self.collision_free_claimed: raise ValueError('Multi-system routing cannot claim collision freedom.')
        if self.connector_engagement_validated: raise ValueError('Multi-system routing cannot claim connector engagement validation.')
        if tuple(x.connection_id for x in self.connections) != self.required_connection_ids:
            raise ValueError('Every authoritative connection must have exactly one route record, in demand order.')
        ids = [x.physical_edge_id for x in self.edges]
        if len(ids) != len(set(ids)): raise ValueError('A physical edge may be routed only once.')
        if self.grid_paths_complete != (self.count(ConnectionRouteStatus.ROUTED) == len(self.required_connection_ids)):
            raise ValueError('grid_paths_complete must equal full ROUTED coverage of required connections.')
        if self.strict_mode and self.endpoint_cell_mode is not EndpointCellMode.CONTAINING_CELL_ONLY:
            raise ValueError('strict_mode is reserved for CONTAINING_CELL_ONLY results.')
        if self.strict_mode and self.controlled_entry is not None and self.controlled_entry.authorized_entry_cells:
            raise ValueError('Strict mode cannot carry authorized benchmark entry cells.')

    def count(self, status: ConnectionRouteStatus) -> int:
        return sum(x.status is status for x in self.connections)

    def summary(self) -> dict[str, Any]:
        per_system = []
        for system in self.systems:
            items = [x for x in self.connections if x.system == system]
            per_system.append({"system": system, "required": len(items), **{s.value.lower(): sum(x.status is s for x in items) for s in ConnectionRouteStatus},
                               "strategies": sorted({x.strategy.value for x in items})})
        return {"required_connection_count": len(self.required_connection_ids),
                **{f"{s.value.lower()}_connection_count": self.count(s) for s in ConnectionRouteStatus},
                "physical_edge_count": len(self.edges), "routed_physical_edge_count": sum(x.route.status is PhysicalEdgeRouteStatus.ROUTED for x in self.edges),
                "grid_paths_complete": self.grid_paths_complete, "connector_engagement_validated": False,
                "strict_mode": self.strict_mode,
                "inter_system_reservation_applied": self.inter_system_reservation_applied,
                "benchmark_controlled_entry_mode": self.endpoint_cell_mode is EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY,
                "legacy_evidence_snap_mode": self.endpoint_cell_mode is EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE,
                "exploratory_endpoint_mode": self.endpoint_cell_mode in EXPLORATORY_MODES, "per_system": per_system}

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"scenario_id": self.scenario_id, "evidence_frame_id": self.evidence_frame_id, "policy_id": self.policy_id,
                "policy_digest_sha256": self.policy_digest_sha256, "systems": list(self.systems),
                "required_connection_ids": list(self.required_connection_ids),
                "connections": [x.to_canonical_dict() for x in self.connections], "edges": [x.to_canonical_dict() for x in self.edges],
                "attempts": [x.to_canonical_dict() for x in self.attempts], "limits": dict(self.limits), "summary": self.summary(),
                "endpoint_cell_mode": self.endpoint_cell_mode.value, "endpoint_snaps": [x.to_canonical_dict() for x in self.endpoint_snaps],
                "endpoint_entry": None if self.endpoint_entry is None else self.endpoint_entry.to_canonical_dict(),
                "controlled_entry": None if self.controlled_entry is None else self.controlled_entry.to_canonical_dict(),
                "grid_paths_complete": self.grid_paths_complete, "connector_engagement_validated": False, "strict_mode": self.strict_mode,
                "inter_system_reservation_applied": self.inter_system_reservation_applied, "collision_free_claimed": False,
                "unresolved_physical_requirements": ["FITTING_GEOMETRY_UNEVALUATED", "CONNECTOR_ENGAGEMENT_UNEVALUATED", "EXACT_COLLISION_CERTIFICATION_UNAVAILABLE"]}

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"))


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


def _node_key(candidate: TopologyCandidate, node_id: str) -> str:
    node = next(x for x in candidate.demand.topology.nodes if x.node_id == node_id)
    return node_id if node.endpoint_id is not None else f"{candidate.candidate_id}#{node_id}"


def _edge_record(candidate: TopologyCandidate, route: PhysicalEdgeRoute) -> PhysicalEdgeRecord:
    edge = next(x for x in candidate.demand.topology.edges if x.edge_id == route.edge_id)
    users = tuple(sorted(a.connection_id for a in candidate.demand.assignments if edge.edge_id in a.edge_ids))
    return PhysicalEdgeRecord(physical_edge_id(candidate.candidate_id, edge.edge_id), candidate.candidate_id, edge.edge_id, edge.system.value,
                              edge.role.value, edge.system.value, users, _node_key(candidate, edge.start_node_id), _node_key(candidate, edge.end_node_id), route)


def _join(routes: list[PhysicalEdgeRoute]) -> tuple[tuple[int, int, int], ...]:
    cells: list[tuple[int, int, int]] = []
    for route in routes:
        segment = list(route.grid_cells)
        if cells and segment and cells[-1] == segment[0]: segment = segment[1:]
        cells.extend(segment)
    return tuple(cells)


def _connection_records(candidate: TopologyCandidate, connection_ids: tuple[str, ...], by_edge: dict[str, PhysicalEdgeRoute], group_id: str,
                        strategy: RoutingStrategy, override: tuple[ConnectionRouteStatus, str] | None = None) -> list[ConnectionRouteRecord]:
    records = []
    for assignment in candidate.demand.assignments:
        if assignment.connection_id not in connection_ids: continue
        system = next(x.system.value for x in candidate.demand.problem.connections if x.connection_id == assignment.connection_id)
        notes = ("DRAINAGE_GRAVITY_SLOPE_UNVALIDATED",) if system in GRAVITY_SYSTEMS else ()
        edge_ids = tuple(physical_edge_id(candidate.candidate_id, x) for x in assignment.edge_ids)
        if override is not None:
            records.append(ConnectionRouteRecord(assignment.connection_id, system, group_id, strategy, candidate.candidate_id, edge_ids, override[0], override[1], (), notes)); continue
        routes = [by_edge.get(x) for x in assignment.edge_ids]
        if all(r is not None and r.status is PhysicalEdgeRouteStatus.ROUTED for r in routes):
            records.append(ConnectionRouteRecord(assignment.connection_id, system, group_id, strategy, candidate.candidate_id, edge_ids, ConnectionRouteStatus.ROUTED, None, _join(routes), notes)); continue
        problems = [(x, by_edge.get(x)) for x in assignment.edge_ids if by_edge.get(x) is None or by_edge[x].status is not PhysicalEdgeRouteStatus.ROUTED]
        mapped = [(ConnectionRouteStatus.UNRESOLVED if r is None else _EDGE_TO_CONNECTION_STATUS[r.status], x, 'EDGE_NOT_ATTEMPTED' if r is None else r.reason) for x, r in problems]
        status, edge_id, reason = min(mapped, key=lambda item: _STATUS_SEVERITY.index(item[0]))
        records.append(ConnectionRouteRecord(assignment.connection_id, system, group_id, strategy, candidate.candidate_id, edge_ids, status, f"{reason}@{physical_edge_id(candidate.candidate_id, edge_id)}", (), notes))
    return records


def _route_direct(direct: TopologyCandidate, connection_ids: tuple[str, ...], evidence: SpatialEvidence, policy: ClearancePolicy, frame: str, max_expansions: int, cells):
    edge_ids = [e for a in direct.demand.assignments if a.connection_id in connection_ids for e in a.edge_ids]
    routes = {e: route_physical_edge(direct, evidence, policy, e, endpoint_frame_id=frame, max_expansions=max_expansions, endpoint_cells=cells) for e in edge_ids}
    return routes, _connection_records(direct, connection_ids, routes, '', RoutingStrategy.DIRECT)


def _endpoint_snaps(direct: TopologyCandidate, evidence: SpatialEvidence, frame: str) -> tuple[EndpointSnap, ...]:
    snaps = []
    for item in assess_topology_candidate(direct, evidence, endpoint_frame_id=frame).endpoints:
        if item.grid_compatibility is EndpointGridCompatibility.SNAPPABLE_WITHIN_TOLERANCE and item.snap_cell is not None:
            spec = evidence.grid_for(item.system).spec
            snaps.append(EndpointSnap(item.endpoint_id, item.system, item.containing_cell, item.containing_cell_state, item.snap_cell, spec.cell_center(*item.snap_cell), item.snap_distance_m))
    return tuple(sorted(snaps, key=lambda x: x.endpoint_id))


def route_multi_system(problem: RoutingProblem, evidence: SpatialEvidence, policy: ClearancePolicy, *, endpoint_frame_id: str,
                       max_expansions: int = 50000, max_proposal_attempts: int = 3,
                       endpoint_cell_mode: EndpointCellMode = EndpointCellMode.CONTAINING_CELL_ONLY,
                       only_systems: tuple[str, ...] | None = None) -> MultiSystemRoutingResult:
    """Route every authoritative connection once; record every success, failure and unresolved case.

    ``only_systems`` optionally limits routing to a subset while still planning topology on the
    full problem so physical edge identities stay globally unique (used by sequential coordination).
    """
    if not isinstance(problem, RoutingProblem) or not isinstance(evidence, SpatialEvidence) or not isinstance(policy, ClearancePolicy):
        raise ValueError('Multi-system routing requires a RoutingProblem, SpatialEvidence and ClearancePolicy.')
    if max_expansions < 1 or max_proposal_attempts < 1: raise ValueError('Routing limits must be positive.')
    if not isinstance(endpoint_cell_mode, EndpointCellMode): raise ValueError('Endpoint cell mode must be an EndpointCellMode.')
    if only_systems is not None:
        if not only_systems or any(not isinstance(item, str) or not item for item in only_systems):
            raise ValueError('only_systems must be a non-empty tuple of system names when provided.')
        if len(set(only_systems)) != len(only_systems):
            raise ValueError('only_systems must not contain duplicates.')
    requested_mode = endpoint_cell_mode
    endpoint_cell_mode = _normalize_endpoint_mode(endpoint_cell_mode)
    candidates = plan_topology_candidates(problem)
    direct = next(x for x in candidates if x.kind is TopologyCandidateKind.DIRECT)
    entry_report = diagnose_routing_problem_endpoints(problem, evidence, policy, endpoint_frame_id=endpoint_frame_id)
    controlled = None
    snaps = ()
    excluded_for_junctions: tuple[tuple[int, int, int], ...] = ()
    if endpoint_cell_mode is EndpointCellMode.CONTAINING_CELL_ONLY:
        cells = None
    elif endpoint_cell_mode is EndpointCellMode.BENCHMARK_CONTROLLED_ENTRY:
        controlled = authorize_controlled_entries(entry_report, evidence, policy, benchmark_controlled_entry_v1())
        cells = controlled.entry_cells() or None
        excluded_for_junctions = tuple(sorted(set(controlled.entry_cells().values())))
    elif endpoint_cell_mode is EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE:
        snaps = _endpoint_snaps(direct, evidence, endpoint_frame_id)
        cells = {x.endpoint_id: x.snap_cell for x in snaps} or None
        excluded_for_junctions = tuple(sorted({x.snap_cell for x in snaps}))
    else:
        raise ValueError(f'Unsupported endpoint cell mode: {requested_mode.value}')
    demand_systems = {x.system.value for x in problem.connections}
    systems = tuple(s for s in SYSTEM_ORDER if s in demand_systems) + tuple(sorted(demand_systems - set(SYSTEM_ORDER)))
    if only_systems is not None:
        unknown = [item for item in only_systems if item not in demand_systems]
        if unknown:
            raise ValueError(f'only_systems references systems absent from demand: {unknown}')
        systems = tuple(item for item in systems if item in set(only_systems))
    groups: dict[str, list[tuple[TopologyCandidate, tuple[str, ...]]]] = {}
    for candidate in candidates:
        if candidate.kind is not TopologyCandidateKind.SHARED_MAIN: continue
        main = next(e for e in candidate.demand.topology.edges if e.role is TopologyEdgeRole.MAIN)
        members = tuple(sorted(a.connection_id for a in candidate.demand.assignments if main.edge_id in a.edge_ids))
        groups.setdefault(main.system.value, []).append((candidate, members))
    records: dict[str, ConnectionRouteRecord] = {}; edges: list[PhysicalEdgeRecord] = []; attempts: list[StrategyAttempt] = []

    def accept(candidate, routes, connection_records):
        for route in routes: edges.append(_edge_record(candidate, route))
        for record in connection_records: records[record.connection_id] = record

    for system in systems:
        system_ids = tuple(x.connection_id for x in problem.connections if x.system.value == system)
        if evidence.grid_for(system) is None:
            accept(direct, (), _connection_records(direct, system_ids, {}, f"{system}/direct", RoutingStrategy.DIRECT, (ConnectionRouteStatus.UNSUPPORTED, 'NO_COMPATIBLE_GRID')))
            attempts.append(StrategyAttempt(system, f"{system}/direct", RoutingStrategy.DIRECT, direct.candidate_id, None, None, None, system_ids, (), 'NO_COMPATIBLE_GRID', True, 0)); continue
        grouped = set()
        for candidate, members in groups.get(system, []):
            grouped.update(members); group_id = f"{system}/{candidate.candidate_id}"
            system_cell_map = {}
            if cells:
                for endpoint_id, cell in cells.items():
                    ep = next((x for x in problem.endpoints if x.endpoint_id == endpoint_id), None)
                    if ep is not None and ep.system.value == system:
                        system_cell_map[endpoint_id] = cell
            system_exclusions = tuple(sorted(set(system_cell_map.values())))
            placement = propose_junction_placements(
                candidate, evidence, endpoint_frame_id=endpoint_frame_id, extra_excluded_cells=system_exclusions,
            )
            junction = placement.junctions[0] if len(placement.junctions) == 1 else None
            shared_options = []
            if junction is None:
                reason = 'UNSUPPORTED_JUNCTION_COUNT'
            elif junction.status is not JunctionPlacementStatus.PROPOSALS_GENERATED:
                reason = f"JUNCTION_PLACEMENT_{junction.unresolved_reason.value if junction.unresolved_reason else 'UNRESOLVED'}"
            else:
                reason = None
                forbidden = set(system_exclusions)
                usable_ranks = [rank for rank, proposal in enumerate(junction.proposals) if proposal.grid_index not in forbidden]
                if not usable_ranks and junction.proposals:
                    reason = 'ALL_JUNCTION_PROPOSALS_COINCIDE_WITH_ENDPOINT_OR_ENTRY_CELL'
                for rank in usable_ranks[:max_proposal_attempts]:
                    network = route_shared_main_group(candidate, evidence, policy, placement, junction_node_id=junction.junction_node_id,
                                                      proposal_rank=rank, endpoint_frame_id=endpoint_frame_id, max_expansions=max_expansions, endpoint_cells=cells)
                    routes = (network.main, *network.branches); by_edge = {r.edge_id: r for r in routes}
                    conn = _connection_records(candidate, members, by_edge, group_id, RoutingStrategy.SHARED_MAIN)
                    # Defense in depth: never accept a zero-length MAIN even if exclusion missed a cell.
                    degenerate = network.main.status is PhysicalEdgeRouteStatus.ROUTED and len(network.main.grid_cells) < 2
                    shared_options.append((rank, network, routes, conn, degenerate))
                    if network.complete and not degenerate: break
            if junction is None or reason is not None:
                attempts.append(StrategyAttempt(system, group_id, RoutingStrategy.SHARED_MAIN, candidate.candidate_id, None if junction is None else junction.junction_node_id, None, None, members, (), reason, False, 0))
            best = max((o for o in shared_options if not o[4]), key=lambda o: (sum(c.status is ConnectionRouteStatus.ROUTED for c in o[3]), -o[0]), default=None)
            best_count = -1 if best is None else sum(c.status is ConnectionRouteStatus.ROUTED for c in best[3])
            fallback = None
            if best is None or best_count < len(members):
                d_routes, d_records = _route_direct(direct, members, evidence, policy, endpoint_frame_id, max_expansions, cells)
                d_records = [_regroup(x, group_id) for x in d_records]
                fallback = (d_routes, d_records, sum(c.status is ConnectionRouteStatus.ROUTED for c in d_records))
            use_direct = fallback is not None and fallback[2] > best_count
            for rank, network, routes, conn, degenerate in shared_options:
                routed = () if degenerate else tuple(c.connection_id for c in conn if c.status is ConnectionRouteStatus.ROUTED)
                why = ("DEGENERATE_ZERO_LENGTH_MAIN" if degenerate else None if network.complete
                       else next((c.reason for c in conn if c.status is not ConnectionRouteStatus.ROUTED), f"MAIN_{network.main.reason}"))
                attempts.append(StrategyAttempt(system, group_id, RoutingStrategy.SHARED_MAIN, candidate.candidate_id, network.junction_node_id, rank,
                                                network.selected_proposal.world_position_m, members, routed, why,
                                                not use_direct and best is not None and rank == best[0], sum(r.expanded_node_count for r in routes)))
            if fallback is not None:
                routed = tuple(c.connection_id for c in fallback[1] if c.status is ConnectionRouteStatus.ROUTED)
                why = next((c.reason for c in fallback[1] if c.status is not ConnectionRouteStatus.ROUTED), None)
                attempts.append(StrategyAttempt(system, group_id, RoutingStrategy.DIRECT, direct.candidate_id, None, None, None, members, routed, why, use_direct, sum(r.expanded_node_count for r in fallback[0].values())))
            if use_direct: accept(direct, fallback[0].values(), fallback[1])
            else: accept(candidate, best[2], best[3])
        rest = tuple(x for x in system_ids if x not in grouped)
        if rest:
            d_routes, d_records = _route_direct(direct, rest, evidence, policy, endpoint_frame_id, max_expansions, cells)
            d_records = [_regroup(x, f"{system}/direct") for x in d_records]; accept(direct, d_routes.values(), d_records)
            routed = tuple(c.connection_id for c in d_records if c.status is ConnectionRouteStatus.ROUTED)
            attempts.append(StrategyAttempt(system, f"{system}/direct", RoutingStrategy.DIRECT, direct.candidate_id, None, None, None, rest, routed,
                                            next((c.reason for c in d_records if c.status is not ConnectionRouteStatus.ROUTED), None), True, sum(r.expanded_node_count for r in d_routes.values())))
    required = tuple(x.connection_id for x in problem.connections if x.system.value in systems)
    missing = [x for x in required if x not in records]
    if missing: raise RuntimeError(f"Multi-system routing lost connection identities: {missing}")
    authorized_ids = set(controlled.entry_cells()) if controlled is not None else set()
    snapped = {x.endpoint_id for x in snaps}
    ends = {x.connection_id: {x.start_endpoint_id, x.end_endpoint_id} for x in problem.connections}
    final_records = []
    for connection_id in required:
        extra: tuple[str, ...] = ()
        touched = ends[connection_id]
        if touched & snapped:
            extra += ("ENDPOINT_SNAPPED_CONNECTOR_ENGAGEMENT_UNVALIDATED", "GRID_PATH_COMPLETE_DOES_NOT_IMPLY_CONNECTOR_ENGAGEMENT",
                      "LEGACY_EVIDENCE_SNAP_MODE_NOT_STRICT")
        if touched & authorized_ids:
            extra += ("BENCHMARK_CONTROLLED_ENTRY_AUTHORIZED", "CONNECTOR_TRANSITION_TO_ENTRY_CELL_UNEVALUATED",
                      "PHYSICAL_CONNECTOR_ENGAGEMENT_UNVALIDATED", "AUTHORITATIVE_ENDPOINT_COORDINATE_UNCHANGED",
                      "GRID_PATH_COMPLETE_DOES_NOT_IMPLY_CONNECTOR_ENGAGEMENT", "NOT_STRICT_AUTHORITATIVE_ENDPOINT_ROUTING")
        final_records.append(_regroup(records[connection_id], records[connection_id].group_id, extra))
    routed_count = sum(item.status is ConnectionRouteStatus.ROUTED for item in final_records)
    return MultiSystemRoutingResult(evidence.provenance.scenario_id, evidence.coordinate_reference.frame_id, policy.policy_id, policy.digest_sha256,
                                    systems, required, tuple(final_records), tuple(sorted(edges, key=lambda e: (SYSTEM_ORDER.index(e.system) if e.system in SYSTEM_ORDER else len(SYSTEM_ORDER), e.physical_edge_id))),
                                    tuple(attempts), (("max_expansions_per_edge", max_expansions), ("max_proposal_attempts_per_junction", max_proposal_attempts)),
                                    endpoint_cell_mode, snaps, entry_report, controlled, routed_count == len(required), False,
                                    endpoint_cell_mode is EndpointCellMode.CONTAINING_CELL_ONLY)


def _regroup(record: ConnectionRouteRecord, group_id: str, extra_requirements: tuple[str, ...] = ()) -> ConnectionRouteRecord:
    return ConnectionRouteRecord(record.connection_id, record.system, group_id, record.strategy, record.candidate_id, record.physical_edge_ids,
                                 record.status, record.reason, record.grid_cells, record.unvalidated_requirements + extra_requirements)

"""Deterministic abstract topology candidates for authoritative routing demand.

Candidates describe logical connectivity only.  They contain no geometric
positions, fitting choices, clearance checks, or physical feasibility claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .endpoints import FlowDirection, RoutingProblem
from .topology import TopologyEdge, TopologyEdgeRole, TopologyNode, TopologyNodeKind, build_topology_graph
from .topology_demand import (
    ConnectionTopologyAssignment,
    TopologicalRoutingDemand,
    associate_routing_problem_with_topology,
    inspect_topology_demand_coverage,
)


class TopologyCandidateKind(str, Enum):
    DIRECT = "DIRECT"
    SHARED_MAIN = "SHARED_MAIN"


@dataclass(frozen=True, slots=True)
class TopologyCandidate:
    """A validated structural topology candidate, not a physical route plan."""

    candidate_id: str
    kind: TopologyCandidateKind
    demand: TopologicalRoutingDemand
    geometry_validated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("Topology candidate ID must be non-empty.")
        if not isinstance(self.kind, TopologyCandidateKind):
            raise ValueError("Topology candidate kind must be a TopologyCandidateKind.")
        if not isinstance(self.demand, TopologicalRoutingDemand):
            raise ValueError("Topology candidate demand must be a TopologicalRoutingDemand.")
        if self.geometry_validated:
            raise ValueError("Abstract topology candidates cannot claim geometry validation.")
        coverage = inspect_topology_demand_coverage(self.demand.problem, self.demand.assignments)
        if coverage.coverage_status != "COMPLETE":
            raise ValueError("Topology candidate must completely cover authoritative demand.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "geometry_validated": False,
            "kind": self.kind.value,
            "demand": self.demand.to_canonical_dict(),
        }


def plan_topology_candidates(problem: RoutingProblem) -> tuple[TopologyCandidate, ...]:
    """Return deterministic direct and compatible shared-main candidates.

    A compatible group contains two or more connections for one system with
    the same authoritative ``start_endpoint_id`` and distinct terminal IDs.
    Each shared-main candidate replaces only that group's direct branches;
    unrelated connections remain direct.  This avoids speculative trunks and
    bounds output to one fallback plus one candidate per compatible group.
    """
    if not isinstance(problem, RoutingProblem):
        raise ValueError("Topology planning requires a RoutingProblem.")
    if not problem.connections:
        raise ValueError("Topology planning requires at least one authoritative connection.")
    _reject_parallel_connections(problem)

    candidates = [_candidate(problem, "candidate/direct", TopologyCandidateKind.DIRECT, None)]
    for index, group in enumerate(_compatible_groups(problem), start=1):
        candidates.append(
            _candidate(problem, f"candidate/shared-main/{index:03d}", TopologyCandidateKind.SHARED_MAIN, group)
        )
    return tuple(candidates)


def _reject_parallel_connections(problem: RoutingProblem) -> None:
    """Parallel demand on one endpoint pair needs an explicit sharing policy."""
    seen: dict[tuple[str, tuple[str, ...]], str] = {}
    for connection in problem.connections:
        key = (connection.system.value, tuple(sorted((connection.start_endpoint_id, connection.end_endpoint_id))))
        if key in seen:
            raise ValueError(
                "Topology planning does not support parallel connections on one endpoint pair: "
                f"{seen[key]} and {connection.connection_id}."
            )
        seen[key] = connection.connection_id


def _compatible_groups(problem: RoutingProblem) -> tuple[tuple[str, ...], ...]:
    groups: dict[tuple[str, str], list[str]] = {}
    for connection in problem.connections:
        groups.setdefault((connection.system.value, connection.start_endpoint_id), []).append(connection.connection_id)
    connection_by_id = {connection.connection_id: connection for connection in problem.connections}
    compatible = []
    for key, connection_ids in groups.items():
        terminals = [connection_by_id[item].end_endpoint_id for item in connection_ids]
        if len(connection_ids) >= 2 and len(set(terminals)) == len(terminals):
            compatible.append((key, tuple(sorted(connection_ids))))
    return tuple(group for _, group in sorted(compatible, key=lambda item: item[0]))


def _candidate(
    problem: RoutingProblem,
    candidate_id: str,
    kind: TopologyCandidateKind,
    shared_connection_ids: tuple[str, ...] | None,
) -> TopologyCandidate:
    endpoint_by_id = {endpoint.endpoint_id: endpoint for endpoint in problem.endpoints}
    used_endpoint_ids = sorted(
        {endpoint_id for connection in problem.connections for endpoint_id in (connection.start_endpoint_id, connection.end_endpoint_id)}
    )
    nodes = [
        TopologyNode(
            _endpoint_node_id(endpoint_id),
            endpoint_by_id[endpoint_id].system,
            _node_kind(endpoint_by_id[endpoint_id].flow_direction),
            endpoint_id,
        )
        for endpoint_id in used_endpoint_ids
    ]
    edges: list[TopologyEdge] = []
    assignments: list[ConnectionTopologyAssignment] = []
    shared_set = set(shared_connection_ids or ())
    if shared_connection_ids:
        first = next(connection for connection in problem.connections if connection.connection_id == shared_connection_ids[0])
        junction_id = "node/virtual/junction/001"
        nodes.append(TopologyNode(junction_id, first.system, TopologyNodeKind.JUNCTION))
        main_id = "edge/shared-main/001"
        edges.append(
            TopologyEdge(main_id, first.system, TopologyEdgeRole.MAIN, _endpoint_node_id(first.start_endpoint_id), junction_id)
        )
    for connection in problem.connections:
        if connection.connection_id in shared_set:
            branch_id = _shared_branch_edge_id(connection.connection_id)
            edges.append(
                TopologyEdge(
                    branch_id,
                    connection.system,
                    TopologyEdgeRole.BRANCH,
                    "node/virtual/junction/001",
                    _endpoint_node_id(connection.end_endpoint_id),
                )
            )
            assignments.append(ConnectionTopologyAssignment(connection.connection_id, ("edge/shared-main/001", branch_id)))
        else:
            edge_id = _direct_edge_id(connection.connection_id)
            edges.append(
                TopologyEdge(
                    edge_id,
                    connection.system,
                    TopologyEdgeRole.BRANCH,
                    _endpoint_node_id(connection.start_endpoint_id),
                    _endpoint_node_id(connection.end_endpoint_id),
                )
            )
            assignments.append(ConnectionTopologyAssignment(connection.connection_id, (edge_id,)))
    topology = build_topology_graph(nodes, edges, metadata={"planner": "deterministic_abstract", "candidate_kind": kind.value})
    demand = associate_routing_problem_with_topology(problem, topology, assignments)
    return TopologyCandidate(candidate_id, kind, demand)


def _node_kind(flow: FlowDirection) -> TopologyNodeKind:
    if flow is FlowDirection.SOURCE:
        return TopologyNodeKind.SOURCE
    if flow is FlowDirection.SINK:
        return TopologyNodeKind.SINK
    return TopologyNodeKind.JUNCTION


def _endpoint_node_id(endpoint_id: str) -> str:
    return f"node/authoritative/{endpoint_id}"


def _direct_edge_id(connection_id: str) -> str:
    return f"edge/direct/{connection_id}"


def _shared_branch_edge_id(connection_id: str) -> str:
    return f"edge/shared-branch/{connection_id}"

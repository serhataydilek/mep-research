"""Focused Phase 9B tests for topology–routing-demand integration."""

from __future__ import annotations

from pathlib import Path
import json
import unittest

from src.demand import generate_benchmark_cases, load_service_definitions
from src.endpoints import (
    ConnectorDimensions,
    ConnectorShape,
    ConnectionRequest,
    Endpoint,
    EndpointProvenance,
    FlowDirection,
    RoutingProblem,
    System,
    Vector3,
    benchmark_case_to_routing_problem,
)
from src.topology import (
    TopologyEdge,
    TopologyEdgeRole,
    TopologyNode,
    TopologyNodeKind,
    build_topology_graph,
)
from src.topology_demand import (
    ConnectionTopologyAssignment,
    TopologicalRoutingDemand,
    associate_routing_problem_with_topology,
    benchmark_case_to_topological_demand,
    star_topology_for_routing_problem,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"


def endpoint(
    endpoint_id: str,
    *,
    system: System = System.WATER,
    flow: FlowDirection = FlowDirection.UNKNOWN,
) -> Endpoint:
    return Endpoint(
        endpoint_id=endpoint_id,
        system=system,
        position=Vector3(0.0, 0.0, 0.0),
        orientation=None,
        flow_direction=flow,
        connector_shape=ConnectorShape.CIRCULAR,
        connector_dimensions=ConnectorDimensions(diameter=0.05),
        provenance=EndpointProvenance.MANUAL,
    )


def water_problem() -> RoutingProblem:
    endpoints = (
        endpoint("endpoint/egress", flow=FlowDirection.UNKNOWN),
        endpoint("endpoint/junction", flow=FlowDirection.BIDIRECTIONAL),
        endpoint("endpoint/terminal", flow=FlowDirection.SINK),
    )
    connections = (
        ConnectionRequest(
            "connection/a", System.WATER, "endpoint/egress", "endpoint/terminal", EndpointProvenance.MANUAL
        ),
    )
    return RoutingProblem(
        tuple(sorted(endpoints, key=lambda item: item.endpoint_id)),
        connections,
    )


def water_main_branch_topology(*, shared: bool = False):
    nodes = [
        TopologyNode("node/egress", System.WATER, TopologyNodeKind.JUNCTION, "endpoint/egress"),
        TopologyNode("node/junction", System.WATER, TopologyNodeKind.JUNCTION, "endpoint/junction"),
        TopologyNode("node/terminal", System.WATER, TopologyNodeKind.SINK, "endpoint/terminal"),
    ]
    owner = TopologyEdge(
        "edge/main", System.WATER, TopologyEdgeRole.MAIN, "node/egress", "node/junction"
    )
    edges = [
        owner,
        TopologyEdge(
            "edge/branch", System.WATER, TopologyEdgeRole.BRANCH, "node/junction", "node/terminal"
        ),
    ]
    if shared:
        edges.append(
            TopologyEdge(
                "edge/main-share",
                System.WATER,
                TopologyEdgeRole.MAIN,
                "node/egress",
                "node/junction",
                owner_edge_id="edge/main",
            )
        )
    return build_topology_graph(nodes, edges)


class TopologyDemandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.systems = load_service_definitions(SYSTEMS)
        cls.case = generate_benchmark_cases(SCENARIOS, DEMANDS, SYSTEMS)[0]

    def test_valid_topology_assignment(self) -> None:
        problem = water_problem()
        topology = water_main_branch_topology()
        demand = associate_routing_problem_with_topology(
            problem,
            topology,
            [
                ConnectionTopologyAssignment(
                    "connection/a", ("edge/main", "edge/branch"), metadata={"path": "main+branch"}
                )
            ],
        )
        self.assertEqual(demand.assignments[0].edge_ids, ("edge/main", "edge/branch"))
        self.assertEqual(demand.problem.connections[0].connection_id, "connection/a")

    def test_missing_connection_assignment_is_rejected(self) -> None:
        problem = water_problem()
        topology = water_main_branch_topology()
        with self.assertRaisesRegex(ValueError, "missing assignments"):
            TopologicalRoutingDemand(problem, topology, ())

    def test_unknown_connection_id_is_rejected(self) -> None:
        problem = water_problem()
        topology = water_main_branch_topology()
        with self.assertRaisesRegex(ValueError, "unknown connection"):
            associate_routing_problem_with_topology(
                problem,
                topology,
                [
                    ConnectionTopologyAssignment("connection/a", ("edge/main", "edge/branch")),
                    ConnectionTopologyAssignment("connection/missing", ("edge/branch",)),
                ],
            )

    def test_duplicate_assignment_is_rejected(self) -> None:
        problem = water_problem()
        topology = water_main_branch_topology()
        with self.assertRaisesRegex(ValueError, "unique"):
            TopologicalRoutingDemand(
                problem,
                topology,
                (
                    ConnectionTopologyAssignment("connection/a", ("edge/main", "edge/branch")),
                    ConnectionTopologyAssignment("connection/a", ("edge/branch",)),
                ),
            )

    def test_invalid_edge_reference_is_rejected(self) -> None:
        problem = water_problem()
        topology = water_main_branch_topology()
        with self.assertRaisesRegex(ValueError, "missing edge"):
            associate_routing_problem_with_topology(
                problem,
                topology,
                [ConnectionTopologyAssignment("connection/a", ("edge/missing",))],
            )

    def test_missing_endpoint_node_binding_is_rejected(self) -> None:
        problem = water_problem()
        topology = build_topology_graph(
            [
                TopologyNode("node/egress", System.WATER, TopologyNodeKind.JUNCTION, "endpoint/egress"),
                TopologyNode("node/terminal", System.WATER, TopologyNodeKind.SINK),  # no endpoint binding
            ],
            [
                TopologyEdge(
                    "edge/direct", System.WATER, TopologyEdgeRole.BRANCH, "node/egress", "node/terminal"
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "no topology node binding"):
            associate_routing_problem_with_topology(
                problem,
                topology,
                [ConnectionTopologyAssignment("connection/a", ("edge/direct",))],
            )

    def test_cross_system_inconsistency_is_rejected(self) -> None:
        problem = water_problem()
        topology = build_topology_graph(
            [
                TopologyNode("node/egress", System.FIRE, TopologyNodeKind.JUNCTION, "endpoint/egress"),
                TopologyNode("node/terminal", System.FIRE, TopologyNodeKind.SINK, "endpoint/terminal"),
            ],
            [
                TopologyEdge(
                    "edge/fire", System.FIRE, TopologyEdgeRole.BRANCH, "node/egress", "node/terminal"
                )
            ],
        )
        # Endpoint IDs match but systems on topology nodes/edges differ from the water connection.
        with self.assertRaisesRegex(ValueError, "system"):
            associate_routing_problem_with_topology(
                problem,
                topology,
                [ConnectionTopologyAssignment("connection/a", ("edge/fire",))],
            )

    def test_valid_shared_topology_edge_assignment(self) -> None:
        problem = water_problem()
        topology = water_main_branch_topology(shared=True)
        demand = associate_routing_problem_with_topology(
            problem,
            topology,
            [
                ConnectionTopologyAssignment(
                    "connection/a",
                    ("edge/main-share", "edge/branch"),
                    metadata={"uses_shared_main": True},
                )
            ],
        )
        share = next(edge for edge in demand.topology.edges if edge.edge_id == "edge/main-share")
        self.assertEqual(share.owner_edge_id, "edge/main")
        self.assertEqual(demand.assignments[0].edge_ids[0], "edge/main-share")

    def test_disconnected_edge_sequence_is_rejected(self) -> None:
        problem = water_problem()
        topology = water_main_branch_topology()
        with self.assertRaisesRegex(ValueError, "contiguous walk"):
            associate_routing_problem_with_topology(
                problem,
                topology,
                [ConnectionTopologyAssignment("connection/a", ("edge/branch",))],
            )

    def test_star_adapter_is_deterministic_and_preserves_legacy_ids(self) -> None:
        first = benchmark_case_to_topological_demand(self.case, self.systems)
        second = benchmark_case_to_topological_demand(self.case, self.systems)
        legacy = benchmark_case_to_routing_problem(self.case, self.systems)
        self.assertEqual(first, second)
        self.assertEqual(
            [connection.connection_id for connection in first.problem.connections],
            [connection.connection_id for connection in legacy.connections],
        )
        self.assertEqual(
            {connection.connection_id for connection in first.problem.connections},
            {request["connection_id"] for request in self.case["connection_requests"]},
        )
        self.assertEqual(
            {endpoint.endpoint_id for endpoint in first.problem.endpoints},
            {endpoint.endpoint_id for endpoint in legacy.endpoints},
        )
        # Breakouts remain metadata only: required count equals legacy connection_requests.
        self.assertEqual(len(first.assignments), len(self.case["connection_requests"]))
        self.assertFalse(first.topology.metadata["shared_trunks"])
        payload = json.dumps(first.to_canonical_dict(), indent=2, sort_keys=True)
        again = json.dumps(second.to_canonical_dict(), indent=2, sort_keys=True)
        self.assertEqual(payload, again)

    def test_star_topology_assigns_one_branch_edge_per_connection(self) -> None:
        problem = benchmark_case_to_routing_problem(self.case, self.systems)
        topology, assignments = star_topology_for_routing_problem(problem)
        demand = associate_routing_problem_with_topology(problem, topology, assignments)
        self.assertEqual(len(demand.topology.edges), len(problem.connections))
        self.assertTrue(all(edge.role is TopologyEdgeRole.BRANCH for edge in demand.topology.edges))
        self.assertTrue(all(len(assignment.edge_ids) == 1 for assignment in demand.assignments))
        self.assertTrue(all(edge.owner_edge_id is None for edge in demand.topology.edges))


if __name__ == "__main__":
    unittest.main()

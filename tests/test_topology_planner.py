"""Focused tests for deterministic abstract topology candidate planning."""

import json
import unittest
from pathlib import Path

from src.demand import generate_benchmark_cases, load_service_definitions
from src.endpoints import benchmark_case_to_routing_problem
from src.endpoints import ConnectionRequest, EndpointProvenance, RoutingProblem, System
from src.topology_demand import inspect_topology_demand_coverage
from src.topology_planner import TopologyCandidateKind, plan_topology_candidates
from tests.test_topology_demand import endpoint


def problem(*connections: tuple[str, System, str, str]) -> RoutingProblem:
    endpoint_ids = sorted({item for connection in connections for item in connection[2:]})
    endpoints = tuple(sorted((endpoint(item, system=next(connection[1] for connection in connections if item in connection[2:])) for item in endpoint_ids), key=lambda item: item.endpoint_id))
    requests = tuple(sorted((ConnectionRequest(connection_id, system, start, end, EndpointProvenance.MANUAL) for connection_id, system, start, end in connections), key=lambda item: item.connection_id))
    return RoutingProblem(endpoints, requests)


class TopologyPlannerTests(unittest.TestCase):
    def test_single_connection_has_direct_fallback(self):
        candidates = plan_topology_candidates(problem(("connection/a", System.WATER, "endpoint/source", "endpoint/terminal")))
        self.assertEqual([candidate.kind for candidate in candidates], [TopologyCandidateKind.DIRECT])

    def test_compatible_connections_get_shared_main_without_duplicate_primary_edge(self):
        routing_problem = problem(
            ("connection/a", System.WATER, "endpoint/source", "endpoint/a"),
            ("connection/b", System.WATER, "endpoint/source", "endpoint/b"),
        )
        shared = plan_topology_candidates(routing_problem)[1]
        self.assertEqual(shared.kind, TopologyCandidateKind.SHARED_MAIN)
        self.assertEqual(sum(edge.edge_id == "edge/shared-main/001" for edge in shared.demand.topology.edges), 1)
        self.assertEqual([item.edge_ids[0] for item in shared.demand.assignments], ["edge/shared-main/001"] * 2)

    def test_systems_and_distinct_upstreams_never_share(self):
        mixed = problem(
            ("connection/fire", System.FIRE, "endpoint/fire-source", "endpoint/fire-terminal"),
            ("connection/water", System.WATER, "endpoint/water-source", "endpoint/water-terminal"),
        )
        distinct = problem(
            ("connection/a", System.WATER, "endpoint/source-a", "endpoint/a"),
            ("connection/b", System.WATER, "endpoint/source-b", "endpoint/b"),
        )
        self.assertEqual(len(plan_topology_candidates(mixed)), 1)
        self.assertEqual(len(plan_topology_candidates(distinct)), 1)

    def test_candidates_are_deterministic_complete_and_preserve_problem(self):
        routing_problem = problem(
            ("connection/a", System.WATER, "endpoint/source", "endpoint/a"),
            ("connection/b", System.WATER, "endpoint/source", "endpoint/b"),
        )
        first = plan_topology_candidates(routing_problem)
        second = plan_topology_candidates(routing_problem)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps([item.to_canonical_dict() for item in first], sort_keys=True), json.dumps([item.to_canonical_dict() for item in second], sort_keys=True))
        self.assertEqual(tuple(item.connection_id for item in routing_problem.connections), ("connection/a", "connection/b"))
        for candidate in first:
            self.assertFalse(candidate.geometry_validated)
            self.assertEqual(inspect_topology_demand_coverage(routing_problem, candidate.demand.assignments).coverage_status, "COMPLETE")
            self.assertEqual(len({node.node_id for node in candidate.demand.topology.nodes}), len(candidate.demand.topology.nodes))

    def test_empty_and_wrong_inputs_are_explicit(self):
        empty = RoutingProblem((), ())
        with self.assertRaisesRegex(ValueError, "at least one"):
            plan_topology_candidates(empty)
        with self.assertRaisesRegex(ValueError, "RoutingProblem"):
            plan_topology_candidates(None)  # type: ignore[arg-type]

    def test_parallel_connections_on_one_endpoint_pair_are_rejected_explicitly(self):
        same = problem(
            ("connection/a", System.WATER, "endpoint/source", "endpoint/terminal"),
            ("connection/b", System.WATER, "endpoint/source", "endpoint/terminal"),
        )
        reversed_pair = problem(
            ("connection/a", System.WATER, "endpoint/source", "endpoint/terminal"),
            ("connection/b", System.WATER, "endpoint/terminal", "endpoint/source"),
        )
        for routing_problem in (same, reversed_pair):
            with self.assertRaisesRegex(ValueError, "parallel connections.*connection/a and connection/b"):
                plan_topology_candidates(routing_problem)

    def test_multiple_groups_each_get_one_isolated_shared_candidate(self):
        routing_problem = problem(
            ("connection/a", System.WATER, "endpoint/source-1", "endpoint/a"),
            ("connection/b", System.WATER, "endpoint/source-1", "endpoint/b"),
            ("connection/c", System.WATER, "endpoint/source-2", "endpoint/c"),
            ("connection/d", System.WATER, "endpoint/source-2", "endpoint/d"),
        )
        candidates = plan_topology_candidates(routing_problem)
        self.assertEqual([item.candidate_id for item in candidates], ["candidate/direct", "candidate/shared-main/001", "candidate/shared-main/002"])
        first, second = candidates[1], candidates[2]
        self.assertEqual({item.connection_id for item in first.demand.assignments if len(item.edge_ids) == 2}, {"connection/a", "connection/b"})
        self.assertEqual({item.connection_id for item in second.demand.assignments if len(item.edge_ids) == 2}, {"connection/c", "connection/d"})

    def test_legacy_benchmark_required_demand_totals_are_preserved(self):
        root = Path(__file__).resolve().parents[1]
        systems_path = root / "config" / "mep_systems.json"
        systems = load_service_definitions(systems_path)
        case = generate_benchmark_cases(root / "experiments" / "scenarios", root / "experiments" / "demands", systems_path)[0]
        routing_problem = benchmark_case_to_routing_problem(case, systems)
        for candidate in plan_topology_candidates(routing_problem):
            self.assertEqual(len(candidate.demand.assignments), len(case["connection_requests"]))
            self.assertEqual(
                {item.connection_id for item in candidate.demand.assignments},
                {item["connection_id"] for item in case["connection_requests"]},
            )

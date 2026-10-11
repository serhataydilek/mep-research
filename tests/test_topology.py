"""Focused Phase 9B tests for immutable topology demand models."""

from __future__ import annotations

import json
import unittest

from src.endpoints import ConnectorDimensions, ConnectorShape, System
from src.topology import (
    EdgeProfile,
    TopologyEdge,
    TopologyEdgeRole,
    TopologyGraph,
    TopologyNode,
    TopologyNodeKind,
    build_topology_graph,
)


def node(
    node_id: str,
    *,
    system: System = System.WATER,
    kind: TopologyNodeKind = TopologyNodeKind.JUNCTION,
    endpoint_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> TopologyNode:
    return TopologyNode(node_id, system, kind, endpoint_id, metadata or {})


def edge(
    edge_id: str,
    start: str,
    end: str,
    *,
    system: System = System.WATER,
    role: TopologyEdgeRole = TopologyEdgeRole.MAIN,
    owner_edge_id: str | None = None,
    profile: EdgeProfile | None = None,
    metadata: dict[str, object] | None = None,
) -> TopologyEdge:
    return TopologyEdge(edge_id, system, role, start, end, owner_edge_id, profile, metadata or {})


def circular_profile(diameter: float = 0.05, capacity: float | None = 1.0) -> EdgeProfile:
    return EdgeProfile(
        connector_shape=ConnectorShape.CIRCULAR,
        dimensions=ConnectorDimensions(diameter=diameter),
        nominal_capacity=capacity,
    )


def fixture_simple_main_and_branch() -> TopologyGraph:
    """1. Simple main and branch."""
    return build_topology_graph(
        [
            node("node/source", kind=TopologyNodeKind.SOURCE, endpoint_id="endpoint/source"),
            node("node/junction", kind=TopologyNodeKind.JUNCTION),
            node("node/terminal", kind=TopologyNodeKind.SINK, endpoint_id="endpoint/terminal"),
        ],
        [
            edge("edge/main", "node/source", "node/junction", role=TopologyEdgeRole.MAIN, profile=circular_profile()),
            edge("edge/branch", "node/junction", "node/terminal", role=TopologyEdgeRole.BRANCH, profile=circular_profile(0.04)),
        ],
        metadata={"fixture": "simple_main_and_branch"},
    )


def fixture_main_with_multiple_branches() -> TopologyGraph:
    """2. Main with multiple branch connections."""
    return build_topology_graph(
        [
            node("node/header", kind=TopologyNodeKind.HEADER, endpoint_id="endpoint/header"),
            node("node/a", kind=TopologyNodeKind.SINK, endpoint_id="endpoint/a"),
            node("node/b", kind=TopologyNodeKind.SINK, endpoint_id="endpoint/b"),
            node("node/source", kind=TopologyNodeKind.SOURCE, endpoint_id="endpoint/source"),
        ],
        [
            edge("edge/branch-a", "node/header", "node/a", role=TopologyEdgeRole.BRANCH),
            edge("edge/branch-b", "node/header", "node/b", role=TopologyEdgeRole.BRANCH),
            edge("edge/main", "node/source", "node/header", role=TopologyEdgeRole.MAIN, profile=circular_profile(0.08, 2.0)),
        ],
        metadata={"fixture": "main_with_multiple_branches"},
    )


def fixture_explicit_shared_trunk() -> TopologyGraph:
    """3. Explicitly shared same-system trunk via owner_edge_id."""
    owner = edge("edge/trunk-owner", "node/source", "node/junction", role=TopologyEdgeRole.MAIN, profile=circular_profile())
    share = edge(
        "edge/trunk-share",
        "node/source",
        "node/junction",
        role=TopologyEdgeRole.MAIN,
        owner_edge_id="edge/trunk-owner",
        metadata={"consumer": "branch-feed"},
    )
    return build_topology_graph(
        [
            node("node/junction", kind=TopologyNodeKind.JUNCTION),
            node("node/source", kind=TopologyNodeKind.SOURCE, endpoint_id="endpoint/source"),
            node("node/terminal", kind=TopologyNodeKind.SINK, endpoint_id="endpoint/terminal"),
        ],
        [
            share,
            owner,
            edge("edge/branch", "node/junction", "node/terminal", role=TopologyEdgeRole.BRANCH),
        ],
        metadata={"fixture": "explicit_shared_trunk"},
    )


class TopologyTests(unittest.TestCase):
    def test_simple_main_and_branch_fixture(self) -> None:
        graph = fixture_simple_main_and_branch()
        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(len(graph.edges), 2)
        self.assertEqual([item.role for item in graph.edges], [TopologyEdgeRole.BRANCH, TopologyEdgeRole.MAIN])
        source = next(item for item in graph.nodes if item.node_id == "node/source")
        self.assertEqual(source.endpoint_id, "endpoint/source")
        self.assertIs(graph.edges[1].profile.connector_shape, ConnectorShape.CIRCULAR)

    def test_main_with_multiple_branch_connections(self) -> None:
        graph = fixture_main_with_multiple_branches()
        branch_roles = [item.role for item in graph.edges if item.role is TopologyEdgeRole.BRANCH]
        self.assertEqual(len(branch_roles), 2)
        self.assertEqual(graph.edges[-1].edge_id, "edge/main")
        self.assertEqual(graph.edges[-1].end_node_id, "node/header")

    def test_explicit_shared_same_system_trunk(self) -> None:
        graph = fixture_explicit_shared_trunk()
        owner = next(item for item in graph.edges if item.edge_id == "edge/trunk-owner")
        share = next(item for item in graph.edges if item.edge_id == "edge/trunk-share")
        self.assertTrue(owner.is_primary)
        self.assertFalse(share.is_primary)
        self.assertEqual(share.owner_edge_id, owner.edge_id)
        self.assertEqual(share.undirected_node_pair, owner.undirected_node_pair)

    def test_invalid_shared_ownership_missing_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing owner edge"):
            build_topology_graph(
                [node("node/a"), node("node/b")],
                [edge("edge/share", "node/a", "node/b", owner_edge_id="edge/missing")],
            )

    def test_invalid_shared_ownership_chained_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "primary owner"):
            build_topology_graph(
                [node("node/a"), node("node/b")],
                [
                    edge("edge/owner", "node/a", "node/b"),
                    edge("edge/mid", "node/a", "node/b", owner_edge_id="edge/owner"),
                    edge("edge/share", "node/a", "node/b", owner_edge_id="edge/mid"),
                ],
            )

    def test_unauthorized_duplicate_primary_segment(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unauthorized duplicate segment"):
            build_topology_graph(
                [node("node/a"), node("node/b")],
                [
                    edge("edge/a", "node/a", "node/b"),
                    edge("edge/b", "node/b", "node/a"),
                ],
            )

    def test_missing_node_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing node"):
            build_topology_graph(
                [node("node/a")],
                [edge("edge/a", "node/a", "node/missing")],
            )

    def test_mixed_system_ownership_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "nodes from its system"):
            build_topology_graph(
                [node("node/a", system=System.WATER), node("node/b", system=System.FIRE)],
                [edge("edge/a", "node/a", "node/b", system=System.WATER)],
            )
        with self.assertRaisesRegex(ValueError, "cannot share across systems"):
            build_topology_graph(
                [
                    node("node/a", system=System.WATER),
                    node("node/b", system=System.WATER),
                    node("node/c", system=System.FIRE),
                    node("node/d", system=System.FIRE),
                ],
                [
                    edge("edge/fire", "node/c", "node/d", system=System.FIRE),
                    edge("edge/water-share", "node/a", "node/b", system=System.WATER, owner_edge_id="edge/fire"),
                ],
            )

    def test_duplicate_node_or_edge_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "node IDs must be unique"):
            TopologyGraph((node("node/a"), node("node/a")), ())
        with self.assertRaisesRegex(ValueError, "same node"):
            edge("edge/a", "node/a", "node/a")
        with self.assertRaisesRegex(ValueError, "edge IDs must be unique"):
            TopologyGraph(
                (node("node/a"), node("node/b")),
                (edge("edge/a", "node/a", "node/b"), edge("edge/a", "node/a", "node/b")),
            )

    def test_deterministic_ordering_and_canonical_serialization(self) -> None:
        graph = fixture_main_with_multiple_branches()
        first = graph.to_canonical_dict()
        second = build_topology_graph(list(reversed(graph.nodes)), list(reversed(graph.edges)), metadata=graph.metadata)
        self.assertEqual(first, second.to_canonical_dict())
        payload = json.dumps(first, indent=2, sort_keys=True) + "\n"
        again = json.dumps(second.to_canonical_dict(), indent=2, sort_keys=True) + "\n"
        self.assertEqual(payload, again)
        self.assertEqual(json.loads(payload)["edges"][0]["edge_id"], "edge/branch-a")

    def test_metadata_and_profile_are_immutable(self) -> None:
        graph = fixture_simple_main_and_branch()
        with self.assertRaises(TypeError):
            graph.metadata["fixture"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            graph.nodes[0].metadata["x"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "positive"):
            EdgeProfile(nominal_capacity=0.0)
        with self.assertRaisesRegex(ValueError, "Circular"):
            EdgeProfile(connector_shape=ConnectorShape.CIRCULAR, dimensions=ConnectorDimensions(width=0.1, height=0.1))

    def test_riser_and_header_roles_are_supported(self) -> None:
        graph = build_topology_graph(
            [
                node("node/base", kind=TopologyNodeKind.SOURCE),
                node("node/header", kind=TopologyNodeKind.HEADER),
                node("node/riser-top", kind=TopologyNodeKind.RISER),
            ],
            [
                edge("edge/header", "node/base", "node/header", role=TopologyEdgeRole.HEADER),
                edge("edge/riser", "node/header", "node/riser-top", role=TopologyEdgeRole.RISER),
            ],
        )
        self.assertEqual({item.role for item in graph.edges}, {TopologyEdgeRole.HEADER, TopologyEdgeRole.RISER})

    def test_share_requires_matching_undirected_pair(self) -> None:
        with self.assertRaisesRegex(ValueError, "same undirected node pair"):
            build_topology_graph(
                [node("node/a"), node("node/b"), node("node/c")],
                [
                    edge("edge/owner", "node/a", "node/b"),
                    edge("edge/share", "node/a", "node/c", owner_edge_id="edge/owner"),
                ],
            )


if __name__ == "__main__":
    unittest.main()

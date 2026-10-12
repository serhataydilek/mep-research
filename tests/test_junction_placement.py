"""Phase 9C.1 bounded, evidence-derived junction candidate placement."""

import dataclasses
import json
import unittest
from pathlib import Path

import ifcopenshell.api.geometry

from src.config import load_building_config
from src.demand import generate_benchmark_cases, load_service_definitions
from src.endpoints import (
    ConnectionRequest,
    ConnectorDimensions,
    ConnectorShape,
    Endpoint,
    EndpointProvenance,
    FlowDirection,
    RoutingProblem,
    System,
    Vector3,
    benchmark_case_to_routing_problem,
)
from src.guid import semantic_ifc_guid
from src.ifc_model import create_ifc_model
from src.junction_placement import (
    Establishment,
    JunctionPlacementStatus,
    JunctionSearchLimits,
    SearchTermination,
    UnresolvedPlacementReason,
    ValidationLedger,
    propose_junction_placements,
    propose_junction_placements_for_candidates,
)
from src.spatial_evidence import (
    SpatialEvidenceStatus,
    build_synthetic_spatial_evidence,
    extract_spatial_evidence,
    synthetic_frame_id,
)
from src.topology import TopologyEdge, TopologyEdgeRole, TopologyNode, TopologyNodeKind, build_topology_graph
from src.topology_demand import (
    ConnectionTopologyAssignment,
    associate_routing_problem_with_topology,
    inspect_topology_demand_coverage,
)
from src.topology_planner import TopologyCandidate, TopologyCandidateKind, plan_topology_candidates
from src.voxel import FREE


ROOT = Path(__file__).resolve().parents[1]
BUILDING = ROOT / "config" / "building.json"
SYSTEMS = ROOT / "config" / "mep_systems.json"
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
Z = 3.25
FORBIDDEN_TEXT = ('"ROUTED"', '"INFEASIBLE"', '"route_length', '"clash_count', '"geometric_cost', '"feasible"')
OPEN_FLOOR = ((3.05, 2.05), ((6.05, 2.05), (3.05, 4.05)))
AROUND_COLUMN = ((3.05, 5.05), ((7.05, 5.05), (5.05, 7.05)))
AROUND_SHAFT = ((10.05, 6.05), ((10.05, 9.05), (8.55, 7.55)))


def endpoint(endpoint_id, system, x, y, *, flow=FlowDirection.SINK, provenance=EndpointProvenance.MANUAL):
    return Endpoint(endpoint_id, system, Vector3(x, y, Z), None, flow, ConnectorShape.CIRCULAR,
                    ConnectorDimensions(diameter=0.05), provenance)


def problem(*groups):
    """groups: (system, name, source_xy, terminal_xys[, terminal_provenance])."""
    endpoints, connections = [], []
    for system, name, source, terminals, *rest in groups:
        provenance = rest[0] if rest else EndpointProvenance.MANUAL
        endpoints.append(endpoint(f"endpoint/{name}/source", system, *source, flow=FlowDirection.SOURCE))
        for index, position in enumerate(terminals):
            terminal_id = f"endpoint/{name}/terminal/{index}"
            endpoints.append(endpoint(terminal_id, system, *position,
                                      provenance=provenance if index == 0 else EndpointProvenance.MANUAL))
            connections.append(ConnectionRequest(f"connection/{name}/{index}", system, f"endpoint/{name}/source",
                                                 terminal_id, EndpointProvenance.MANUAL))
    return RoutingProblem(tuple(sorted(endpoints, key=lambda item: item.endpoint_id)),
                          tuple(sorted(connections, key=lambda item: item.connection_id)))


def shared(routing_problem):
    return next(item for item in plan_topology_candidates(routing_problem) if item.kind is TopologyCandidateKind.SHARED_MAIN)


def multi_junction_candidate(routing_problem, names):
    """Hand-built candidate with one virtual junction per named source group."""
    nodes = [TopologyNode(f"node/{item.endpoint_id}", item.system,
                          TopologyNodeKind.SOURCE if item.flow_direction is FlowDirection.SOURCE else TopologyNodeKind.SINK,
                          item.endpoint_id) for item in routing_problem.endpoints]
    edges, assignments = [], []
    for index, name in enumerate(names, start=1):
        connections = [item for item in routing_problem.connections if item.connection_id.startswith(f"connection/{name}/")]
        system = connections[0].system
        junction = f"node/virtual/junction/{index:03d}"
        main = f"edge/shared-main/{index:03d}"
        nodes.append(TopologyNode(junction, system, TopologyNodeKind.JUNCTION))
        edges.append(TopologyEdge(main, system, TopologyEdgeRole.MAIN, f"node/endpoint/{name}/source", junction))
        for connection in connections:
            branch = f"edge/shared-branch/{connection.connection_id}"
            edges.append(TopologyEdge(branch, system, TopologyEdgeRole.BRANCH, junction, f"node/{connection.end_endpoint_id}"))
            assignments.append(ConnectionTopologyAssignment(connection.connection_id, (main, branch)))
    demand = associate_routing_problem_with_topology(routing_problem, build_topology_graph(nodes, edges), assignments)
    return TopologyCandidate("candidate/test/multi-junction", TopologyCandidateKind.SHARED_MAIN, demand)


class ControlledIfcJunctionPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.systems = load_service_definitions(SYSTEMS)
        cls.config = load_building_config(BUILDING)
        cls.evidence = build_synthetic_spatial_evidence("building", cls.config, cls.systems)
        cls.frame = synthetic_frame_id("building", cls.config)

    def place(self, candidate, evidence=None, frame=None, **limits):
        return propose_junction_placements(candidate, evidence or self.evidence, endpoint_frame_id=frame or self.frame,
                                           limits=JunctionSearchLimits(**limits) if limits else None)

    def single(self, system, layout, **limits):
        candidate = shared(problem((system, "g", *layout)))
        result = self.place(candidate, **limits)
        self.assertEqual(len(result.junctions), 1)
        return candidate, result.junctions[0]

    def assert_proposals_valid(self, junction):
        grid = self.evidence.grid_for(junction.system)
        spec = grid.spec
        for proposal in junction.proposals:
            i, j, k = proposal.grid_index
            self.assertEqual(grid.cells[spec.linear_index(i, j, k)], FREE)
            self.assertEqual(proposal.cell_state, "FREE")
            self.assertEqual(proposal.linear_index, spec.linear_index(i, j, k))
            self.assertEqual(proposal.evidence.cells_sha256, grid.cells_sha256)
            self.assertEqual(proposal.evidence.system, junction.system)
            self.assertTrue(all(junction.region.min_cell[a] <= proposal.grid_index[a] <= junction.region.max_cell[a] for a in range(3)))
            self.assertTrue(self.evidence.grid_domain.contains_point(*proposal.world_position_m, tolerance=0.0))
            self.assertTrue(junction.region.world_bounds.contains_point(*proposal.world_position_m, tolerance=0.0))

    def test_valid_candidate_in_supported_free_cell(self):
        candidate, junction = self.single(System.WATER, OPEN_FLOOR)
        self.assertIs(junction.status, JunctionPlacementStatus.PROPOSALS_GENERATED)
        self.assertIsNone(junction.unresolved_reason)
        self.assertEqual(junction.junction_node_id, "node/virtual/junction/001")
        self.assertEqual(junction.main_edge_id, "edge/shared-main/001")
        self.assertEqual(junction.source_endpoint_id, "endpoint/g/source")
        self.assertEqual(junction.terminal_endpoint_ids, ("endpoint/g/terminal/0", "endpoint/g/terminal/1"))
        self.assertEqual(junction.region.seed_cell, (30, 20, 3))
        self.assertEqual(len(junction.proposals), 8)
        self.assertIs(junction.termination, SearchTermination.CANDIDATE_LIMIT_REACHED)
        self.assertEqual(junction.excluded_endpoint_cell_count, 1)
        self.assertNotIn((30, 20, 3), {item.grid_index for item in junction.proposals})
        self.assert_proposals_valid(junction)
        self.assertEqual(junction.ledger.to_canonical_dict(), {
            "candidate_placement": "ESTABLISHED", "collision_free_certification": "NOT_EVALUATED",
            "physical_fitting_feasibility": "NOT_EVALUATED", "reachable_route_geometry": "NOT_EVALUATED",
            "spatial_evidence": "ESTABLISHED", "structural_topology": "ESTABLISHED",
        })

    def test_candidates_never_in_blocked_or_reserved_cells_and_stay_in_bounds(self):
        for layout, expected_skipped in ((AROUND_COLUMN, "BLOCKED_FIXED"), (AROUND_SHAFT, "RESERVED_SHAFT")):
            for system in (System.HVAC, System.WATER):
                with self.subTest(system=system.value, skipped=expected_skipped):
                    _, junction = self.single(system, layout, max_candidates_per_junction=20_000, max_explored_cells=200_000)
                    self.assertIs(junction.status, JunctionPlacementStatus.PROPOSALS_GENERATED)
                    self.assertIs(junction.termination, SearchTermination.REGION_EXHAUSTED)
                    self.assertEqual(junction.explored_cell_count, junction.region.cell_count)
                    skipped = dict(junction.skipped_state_counts)
                    self.assertGreater(skipped[expected_skipped], 0)
                    grid = self.evidence.grid_for(system.value)
                    seed = junction.region.seed_cell
                    self.assertNotEqual(grid.cells[grid.spec.linear_index(*seed)], FREE)
                    self.assertEqual(
                        len(junction.proposals) + sum(skipped.values()) + junction.excluded_endpoint_cell_count,
                        junction.region.cell_count,
                    )
                    self.assert_proposals_valid(junction)

    def test_world_grid_round_trip_is_exact(self):
        _, junction = self.single(System.HVAC, AROUND_COLUMN, max_candidates_per_junction=200)
        spec = self.evidence.grid_for("hvac").spec
        for proposal in junction.proposals:
            self.assertEqual(spec.world_to_containing_cell(*proposal.world_position_m), proposal.grid_index)
            self.assertEqual(spec.index_from_linear(proposal.linear_index), proposal.grid_index)
            self.assertEqual(spec.cell_center(*proposal.grid_index), proposal.world_position_m)

    def test_repeated_runs_are_identical_and_ordering_is_stable(self):
        candidate = shared(problem((System.HVAC, "g", *AROUND_COLUMN)))
        first, second = self.place(candidate), self.place(candidate)
        self.assertEqual(first, second)
        self.assertEqual(first.to_canonical_json(), second.to_canonical_json())
        proposals = first.junctions[0].proposals
        keys = [(item.seed_l1_distance, item.grid_index[2], item.grid_index[1], item.grid_index[0]) for item in proposals]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([item.rank for item in proposals], list(range(len(proposals))))

    def test_system_specific_grid_selection_respects_service_envelopes(self):
        routing_problem = problem((System.HVAC, "duct", *AROUND_COLUMN), (System.WATER, "pipe", *AROUND_COLUMN))
        candidates = [item for item in plan_topology_candidates(routing_problem) if item.kind is TopologyCandidateKind.SHARED_MAIN]
        results = propose_junction_placements_for_candidates(tuple(candidates), self.evidence, endpoint_frame_id=self.frame)
        by_system = {result.junctions[0].system: result.junctions[0] for result in results}
        self.assertEqual(set(by_system), {"hvac", "water"})
        hvac_grid, water_grid = self.evidence.grid_for("hvac"), self.evidence.grid_for("water")
        self.assertNotEqual(hvac_grid.cells_sha256, water_grid.cells_sha256)
        for system, junction in by_system.items():
            self.assert_proposals_valid(junction)
            self.assertEqual(junction.proposals[0].evidence.required_planar_margin_m,
                             self.evidence.grid_for(system).required_planar_margin_m)
        water_cells_blocked_for_hvac = [
            item for item in by_system["water"].proposals
            if hvac_grid.cells[hvac_grid.spec.linear_index(*item.grid_index)] != FREE
        ]
        self.assertTrue(water_cells_blocked_for_hvac, "the larger HVAC envelope must exclude cells usable by water")

    def test_multiple_independent_junction_groups(self):
        layout_b = ((12.05, 2.05), ((15.05, 2.05), (12.05, 4.05)))
        routing_problem = problem((System.WATER, "a", *OPEN_FLOOR), (System.WATER, "b", *layout_b))
        candidate = multi_junction_candidate(routing_problem, ("a", "b"))
        result = self.place(candidate)
        first, second = result.junctions
        self.assertEqual((first.junction_node_id, second.junction_node_id),
                         ("node/virtual/junction/001", "node/virtual/junction/002"))
        self.assertEqual(first.connection_ids, ("connection/a/0", "connection/a/1"))
        self.assertEqual(second.connection_ids, ("connection/b/0", "connection/b/1"))
        self.assertEqual({item.endpoint_id for item in first.anchors},
                         {"endpoint/a/source", "endpoint/a/terminal/0", "endpoint/a/terminal/1"})
        self.assertEqual(first.region.seed_cell, (30, 20, 3))
        self.assertEqual(second.region.seed_cell, (120, 20, 3))
        self.assertTrue(all(item.status is JunctionPlacementStatus.PROPOSALS_GENERATED for item in result.junctions))
        self.assertEqual(result.diagnostics, ())
        planner = [item for item in plan_topology_candidates(routing_problem) if item.kind is TopologyCandidateKind.SHARED_MAIN]
        self.assertEqual(len(planner), 2)
        placements = propose_junction_placements_for_candidates(tuple(planner), self.evidence, endpoint_frame_id=self.frame)
        self.assertEqual([item.junctions[0].source_endpoint_id for item in placements], ["endpoint/a/source", "endpoint/b/source"])

    def test_no_cross_system_junction_sharing_and_duplicates_are_flagged(self):
        routing_problem = problem((System.HVAC, "duct", *OPEN_FLOOR), (System.WATER, "pipe", *OPEN_FLOOR))
        result = self.place(multi_junction_candidate(routing_problem, ("duct", "pipe")))
        duct, pipe = result.junctions
        self.assertEqual((duct.system, pipe.system), ("hvac", "water"))
        self.assertTrue(all(item.system == "hvac" and item.evidence.system == "hvac" for item in duct.proposals))
        self.assertTrue(all(item.system == "water" and item.evidence.system == "water" for item in pipe.proposals))
        self.assertTrue({item.world_position_m for item in duct.proposals} & {item.world_position_m for item in pipe.proposals})
        self.assertIn("CROSS_SYSTEM_COINCIDENT_PROPOSALS", {item.code for item in result.diagnostics})
        nearby = ((3.15, 2.15), ((6.15, 2.15), (3.15, 4.15)))
        same_system = problem((System.WATER, "a", *OPEN_FLOOR), (System.WATER, "b", *nearby))
        flagged = self.place(multi_junction_candidate(same_system, ("a", "b")))
        self.assertIn("COINCIDENT_PROPOSALS_ACROSS_JUNCTIONS", {item.code for item in flagged.diagnostics})
        self.assertTrue(all(len({p.linear_index for p in item.proposals}) == len(item.proposals) for item in flagged.junctions))

    def test_fully_blocked_region_is_unresolved_not_infeasible(self):
        blocked = ((4.95, 4.95), ((5.05, 4.95), (4.95, 5.05)))
        _, junction = self.single(System.HVAC, blocked, region_margin_cells=0)
        self.assertIs(junction.status, JunctionPlacementStatus.UNRESOLVED)
        self.assertIs(junction.unresolved_reason, UnresolvedPlacementReason.NO_USABLE_CELL_IN_BOUNDED_REGION)
        self.assertIs(junction.termination, SearchTermination.REGION_EXHAUSTED)
        self.assertEqual(junction.region.cell_count, 4)
        self.assertEqual(junction.explored_cell_count, 4)
        self.assertEqual(junction.excluded_endpoint_cell_count, 3)
        self.assertEqual(dict(junction.skipped_state_counts), {"BLOCKED_FIXED": 1})
        self.assertFalse(junction.globally_infeasible_claimed)
        self.assertIs(junction.ledger.candidate_placement, Establishment.NOT_ESTABLISHED)
        codes = {item.code for item in junction.diagnostics}
        self.assertIn("ENDPOINT_CELL_NOT_FREE", codes)
        message = next(item.message for item in junction.diagnostics if item.code == "NO_USABLE_CELL_IN_BOUNDED_REGION")
        self.assertIn("not evidence that no junction placement", message)
        with self.assertRaisesRegex(ValueError, "global infeasibility"):
            dataclasses.replace(junction, globally_infeasible_claimed=True)

    def test_missing_and_invalid_spatial_evidence_are_unresolved(self):
        candidate = shared(problem((System.WATER, "g", *OPEN_FLOOR)))
        without_water = dataclasses.replace(self.evidence, grids=tuple(g for g in self.evidence.grids if g.system != "water"))
        self.assertIs(without_water.status, SpatialEvidenceStatus.EVIDENCE_AVAILABLE)
        cases = {
            UnresolvedPlacementReason.NO_COMPATIBLE_GRID: self.place(candidate, evidence=without_water),
            UnresolvedPlacementReason.COORDINATE_FRAME_MISMATCH: self.place(candidate, frame="other-frame"),
            UnresolvedPlacementReason.ENDPOINT_OUTSIDE_GRID: self.place(
                shared(problem((System.WATER, "g", (3.05, 2.05), ((-1.0, 2.05), (3.05, 4.05)))))),
            UnresolvedPlacementReason.ENDPOINT_POSITION_NOT_AUTHORITATIVE: self.place(
                shared(problem((System.WATER, "g", *OPEN_FLOOR, EndpointProvenance.INFERRED)))),
        }
        for reason, result in cases.items():
            with self.subTest(reason=reason.value):
                (junction,) = result.junctions
                self.assertIs(junction.status, JunctionPlacementStatus.UNRESOLVED)
                self.assertIs(junction.unresolved_reason, reason)
                self.assertIs(junction.termination, SearchTermination.NOT_SEARCHED)
                self.assertEqual(junction.proposals, ())
                self.assertIsNone(junction.region)
        frame_junction = cases[UnresolvedPlacementReason.COORDINATE_FRAME_MISMATCH].junctions[0]
        self.assertTrue(all(item.containing_cell is None for item in frame_junction.anchors))

    def test_unsupported_ifc_geometry_blocks_placement_even_where_grid_is_free(self):
        model = create_ifc_model(self.config)
        context = model.by_type("IfcGeometricRepresentationSubContext")[0]
        storey = model.by_type("IfcBuildingStorey")[0]
        representation = ifcopenshell.api.geometry.add_slab_representation(
            model, context=context, depth=0.3, polyline=[(0.0, 0.0), (6.0, 0.0), (6.0, 0.3), (0.0, 0.3)])
        model.create_entity(
            "IfcBeam", GlobalId=semantic_ifc_guid("test/junction/beam"), Name="Test Beam",
            ObjectPlacement=model.create_entity(
                "IfcLocalPlacement", PlacementRelTo=storey.ObjectPlacement,
                RelativePlacement=model.create_entity("IfcAxis2Placement3D",
                                                      Location=model.create_entity("IfcCartesianPoint", Coordinates=(2.0, 1.9, 3.0)))),
            Representation=model.create_entity("IfcProductDefinitionShape", Representations=[representation]),
        )
        evidence = extract_spatial_evidence(model, self.config, self.systems, scenario_id="building", frame_id=self.frame)
        self.assertIs(evidence.status, SpatialEvidenceStatus.UNSUPPORTED_GEOMETRY)
        self.assertEqual(evidence.grid_for("water").cells, self.evidence.grid_for("water").cells)
        (junction,) = self.place(shared(problem((System.WATER, "g", *OPEN_FLOOR))), evidence=evidence).junctions
        self.assertIs(junction.unresolved_reason, UnresolvedPlacementReason.SPATIAL_EVIDENCE_NOT_USABLE)
        message = next(item.message for item in junction.diagnostics if item.code == "SPATIAL_EVIDENCE_NOT_USABLE")
        self.assertIn("UNMODELLED_GEOMETRY_IN_GRID_DOMAIN", message)
        self.assertIs(junction.ledger.spatial_evidence, Establishment.NOT_ESTABLISHED)

    def test_unsupported_junction_structure_and_direct_candidates(self):
        routing_problem = problem((System.WATER, "g", *OPEN_FLOOR))
        direct = plan_topology_candidates(routing_problem)[0]
        result = self.place(direct)
        self.assertEqual(result.junctions, ())
        self.assertEqual([item.code for item in result.diagnostics], ["NO_VIRTUAL_JUNCTIONS"])
        nodes = [TopologyNode(f"node/{item.endpoint_id}", System.WATER,
                              TopologyNodeKind.SOURCE if item.flow_direction is FlowDirection.SOURCE else TopologyNodeKind.SINK,
                              item.endpoint_id) for item in routing_problem.endpoints]
        nodes.append(TopologyNode("node/virtual/junction/001", System.WATER, TopologyNodeKind.JUNCTION))
        edges = [
            TopologyEdge("edge/main", System.WATER, TopologyEdgeRole.MAIN, "node/endpoint/g/source", "node/virtual/junction/001"),
            TopologyEdge("edge/branch/0", System.WATER, TopologyEdgeRole.BRANCH, "node/virtual/junction/001", "node/endpoint/g/terminal/0"),
            TopologyEdge("edge/direct/1", System.WATER, TopologyEdgeRole.BRANCH, "node/endpoint/g/source", "node/endpoint/g/terminal/1"),
        ]
        assignments = [ConnectionTopologyAssignment("connection/g/0", ("edge/main", "edge/branch/0")),
                       ConnectionTopologyAssignment("connection/g/1", ("edge/direct/1",))]
        demand = associate_routing_problem_with_topology(routing_problem, build_topology_graph(nodes, edges), assignments)
        (junction,) = self.place(TopologyCandidate("candidate/test/one-branch", TopologyCandidateKind.SHARED_MAIN, demand)).junctions
        self.assertIs(junction.unresolved_reason, UnresolvedPlacementReason.UNSUPPORTED_JUNCTION_STRUCTURE)
        self.assertIs(junction.ledger.structural_topology, Establishment.NOT_ESTABLISHED)

    def test_authoritative_endpoints_topology_and_demand_are_unchanged(self):
        routing_problem = problem((System.HVAC, "g", *AROUND_COLUMN))
        before_problem = repr(routing_problem)
        candidate = shared(routing_problem)
        before_demand = json.dumps(candidate.demand.to_canonical_dict(), sort_keys=True)
        (junction,) = self.place(candidate).junctions
        self.assertEqual(repr(routing_problem), before_problem)
        self.assertEqual(json.dumps(candidate.demand.to_canonical_dict(), sort_keys=True), before_demand)
        positions = {item.endpoint_id: (item.position.x, item.position.y, item.position.z) for item in routing_problem.endpoints}
        for anchor in junction.anchors:
            self.assertEqual(anchor.position_m, positions[anchor.endpoint_id])
            self.assertTrue(anchor.position_authoritative)
        node = next(item for item in candidate.demand.topology.nodes if item.node_id == junction.junction_node_id)
        self.assertIsNone(node.endpoint_id)
        self.assertEqual(dict(node.metadata), {})
        coverage = inspect_topology_demand_coverage(routing_problem, candidate.demand.assignments)
        self.assertEqual(coverage.coverage_status, "COMPLETE")
        self.assertEqual(junction.connection_ids, tuple(item.connection_id for item in routing_problem.connections))

    def test_immutability_and_canonical_serialization(self):
        result = self.place(shared(problem((System.DRAINAGE, "g", *OPEN_FLOOR))))
        proposal = result.junctions[0].proposals[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            proposal.world_position_m = (0.0, 0.0, 0.0)  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.junctions = ()  # type: ignore[misc]
        for flag in ("physical_position_validated", "fitting_feasibility_evaluated", "route_reachability_evaluated",
                     "collision_free_claimed"):
            with self.assertRaisesRegex(ValueError, "cannot claim"):
                dataclasses.replace(proposal, **{flag: True})
        with self.assertRaisesRegex(ValueError, "FREE"):
            dataclasses.replace(proposal, cell_state="RESERVED_SHAFT")
        with self.assertRaisesRegex(ValueError, "cannot evaluate"):
            ValidationLedger(Establishment.ESTABLISHED, Establishment.ESTABLISHED, Establishment.ESTABLISHED,
                             physical_fitting_feasibility=Establishment.ESTABLISHED)
        text = result.to_canonical_json()
        self.assertEqual(text, json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")))
        payload = json.loads(text)
        first = payload["junctions"][0]["proposals"][0]
        self.assertEqual(first["world_position_m"], [round(value, 9) for value in proposal.world_position_m])
        self.assertIn({"dependency": "drainage slope and invert constraints (Phase 9D)",
                       "kind": "GRAVITY_SLOPE_AND_INVERT_UNEVALUATED", "subject_id": "edge/shared-main/001"},
                      first["unresolved_physical_requirements"])

    def test_bounded_search_terminates_explicitly(self):
        candidate = shared(problem((System.HVAC, "g", *AROUND_COLUMN)))
        full = self.place(candidate, max_candidates_per_junction=50).junctions[0]
        limited = self.place(candidate, max_candidates_per_junction=3).junctions[0]
        self.assertIs(limited.termination, SearchTermination.CANDIDATE_LIMIT_REACHED)
        self.assertEqual(limited.proposals, full.proposals[:3])
        self.assertIn("CANDIDATE_LIMIT_REACHED", {item.code for item in limited.diagnostics})
        explored = self.place(candidate, max_explored_cells=5).junctions[0]
        self.assertIs(explored.termination, SearchTermination.EXPLORATION_LIMIT_REACHED)
        self.assertEqual(explored.explored_cell_count, 5)
        self.assertIs(explored.unresolved_reason, UnresolvedPlacementReason.NO_USABLE_CELL_IN_BOUNDED_REGION)
        self.assertIn("explored part", next(item.message for item in explored.diagnostics
                                            if item.code == "NO_USABLE_CELL_IN_BOUNDED_REGION"))
        for bad in ({"max_candidates_per_junction": 0}, {"max_explored_cells": True},
                    {"region_margin_cells": -1}, {"max_explored_cells": 25_000_001}):
            with self.subTest(limits=bad), self.assertRaises(ValueError):
                JunctionSearchLimits(**bad)


class BenchmarkJunctionPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.systems = load_service_definitions(SYSTEMS)
        cls.case = generate_benchmark_cases(SCENARIOS, DEMANDS, SYSTEMS)[0]
        manifest = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
        config_file = next(item["config_file"] for item in manifest["scenarios"] if item["scenario_id"] == cls.case["scenario_id"])
        cls.config = load_building_config(SCENARIOS / config_file)
        cls.evidence = build_synthetic_spatial_evidence(cls.case["scenario_id"], cls.config, cls.systems)
        cls.frame = synthetic_frame_id(cls.case["scenario_id"], cls.config)
        cls.problem = benchmark_case_to_routing_problem(cls.case, cls.systems)
        cls.candidates = plan_topology_candidates(cls.problem)
        cls.results = propose_junction_placements_for_candidates(cls.candidates, cls.evidence, endpoint_frame_id=cls.frame)

    def test_shared_main_candidates_get_evidence_derived_proposals_without_claims(self):
        shared_results = [item for item in self.results if item.candidate_kind == "SHARED_MAIN"]
        self.assertTrue(shared_results)
        generated = 0
        for result in shared_results:
            (junction,) = result.junctions
            grid = self.evidence.grid_for(junction.system)
            source = junction.anchors[0]
            self.assertEqual((source.role, source.flow_direction), ("SOURCE", "UNKNOWN"))
            self.assertTrue(source.endpoint_id.startswith("egress/"))
            if junction.status is JunctionPlacementStatus.PROPOSALS_GENERATED:
                generated += 1
                for proposal in junction.proposals:
                    self.assertEqual(grid.cells[proposal.linear_index], FREE)
                    self.assertEqual(proposal.evidence.cells_sha256, grid.cells_sha256)
            else:
                self.assertIsInstance(junction.unresolved_reason, UnresolvedPlacementReason)
            self.assertFalse(junction.globally_infeasible_claimed)
            ledger = junction.ledger
            self.assertEqual({ledger.physical_fitting_feasibility, ledger.reachable_route_geometry,
                              ledger.collision_free_certification}, {Establishment.NOT_EVALUATED})
            text = result.to_canonical_json()
            for token in FORBIDDEN_TEXT:
                self.assertNotIn(token, text)
        self.assertEqual(generated, len(shared_results))

    def test_benchmark_demand_coverage_and_endpoints_are_unchanged(self):
        self.assertEqual(self.problem, benchmark_case_to_routing_problem(self.case, self.systems))
        required = {item.connection_id for item in self.problem.connections}
        for candidate in self.candidates:
            coverage = inspect_topology_demand_coverage(self.problem, candidate.demand.assignments)
            self.assertEqual(coverage.coverage_status, "COMPLETE")
            self.assertEqual(set(coverage.assigned_connection_ids), required)
        positions = {item.endpoint_id: (item.position.x, item.position.y, item.position.z) for item in self.problem.endpoints}
        for result in self.results:
            for junction in result.junctions:
                for anchor in junction.anchors:
                    self.assertEqual(anchor.position_m, positions[anchor.endpoint_id])

    def test_benchmark_results_are_deterministic(self):
        repeated = propose_junction_placements_for_candidates(self.candidates, self.evidence, endpoint_frame_id=self.frame)
        self.assertEqual(repeated, self.results)
        self.assertEqual([item.to_canonical_json() for item in repeated], [item.to_canonical_json() for item in self.results])


if __name__ == "__main__":
    unittest.main()

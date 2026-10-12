"""Phase 9C.0 spatial evidence extraction and read-only candidate assessment."""

import copy
import dataclasses
import hashlib
import json
import unittest
from pathlib import Path

import ifcopenshell.api.geometry

from src.config import interior_grid_positions, load_building_config
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
from src.spatial_evidence import (
    AabbFidelity,
    ElementClassification,
    EndpointGridCompatibility,
    OccupancyGridReference,
    SpatialEvidenceStatus,
    assess_topology_candidate,
    build_synthetic_spatial_evidence,
    extract_spatial_evidence,
    read_only_occupancy_view,
    synthetic_frame_id,
)
from src.topology_demand import inspect_topology_demand_coverage
from src.topology_planner import TopologyCandidateKind, plan_topology_candidates
from src.voxel import BLOCKED_FIXED, build_occupancy_grid, extract_ifc_obstacles


ROOT = Path(__file__).resolve().parents[1]
BUILDING = ROOT / "config" / "building.json"
SYSTEMS = ROOT / "config" / "mep_systems.json"
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
FORBIDDEN_CLAIM_KEYS = ("route_length", "clash_count", "clearance_m_achieved", "geometric_cost", "feasible", "junction_position")


def codes(items):
    return {(item.code, item.subject_id) for item in items}


def water_endpoint(endpoint_id, x, y, z, *, provenance=EndpointProvenance.MANUAL, flow=FlowDirection.SINK):
    return Endpoint(
        endpoint_id, System.WATER, Vector3(x, y, z), None, flow, ConnectorShape.CIRCULAR,
        ConnectorDimensions(diameter=0.05), provenance,
    )


def water_problem(*terminals, source=(2.5, 2.5, 3.2)):
    endpoints = [water_endpoint("endpoint/source", *source, flow=FlowDirection.SOURCE)]
    endpoints += [water_endpoint(endpoint_id, *position, provenance=provenance) for endpoint_id, position, provenance in terminals]
    connections = tuple(
        ConnectionRequest(f"connection/{index}", System.WATER, "endpoint/source", endpoint_id, EndpointProvenance.MANUAL)
        for index, (endpoint_id, _, _) in enumerate(terminals)
    )
    return RoutingProblem(tuple(sorted(endpoints, key=lambda item: item.endpoint_id)), connections)


class SyntheticSpatialEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.systems = load_service_definitions(SYSTEMS)
        cls.config = load_building_config(BUILDING)
        cls.evidence = build_synthetic_spatial_evidence("building", cls.config, cls.systems)
        cls.frame = synthetic_frame_id("building", cls.config)

    def extract(self, model, config=None):
        return extract_spatial_evidence(model, config or self.config, self.systems, scenario_id="building")

    def test_extracts_classified_elements_grids_and_shaft_from_generated_ifc(self):
        evidence = self.evidence
        self.assertIs(evidence.status, SpatialEvidenceStatus.EVIDENCE_AVAILABLE)
        counts = {}
        for element in evidence.elements:
            counts[element.classification] = counts.get(element.classification, 0) + 1
        columns = len(interior_grid_positions(5.0, 20.0)) * len(interior_grid_positions(5.0, 15.0))
        self.assertEqual(counts, {
            ElementClassification.PERIMETER_WALL: 4, ElementClassification.SHAFT_WALL: 4,
            ElementClassification.STRUCTURAL_COLUMN: columns, ElementClassification.SLAB: 1,
            ElementClassification.SHAFT_SPACE: 1,
        })
        self.assertEqual([grid.system for grid in evidence.grids], ["hvac", "drainage", "water", "fire", "electrical"])
        self.assertEqual(len(evidence.shafts), 1)
        self.assertEqual(evidence.shafts[0].global_id, semantic_ifc_guid("storey/0/shaft/main/space"))
        self.assertTrue(all(element.aabb_fidelity is AabbFidelity.VOLUME_MATCH_UNCERTAIN for element in evidence.elements
                            if element.classification is not ElementClassification.SLAB))
        self.assertIn(("AABB_FIDELITY_LIMITATION", semantic_ifc_guid("storey/0/column/0/0")), codes(evidence.diagnostics))
        self.assertIn("EXACT_COLLISION_CERTIFICATION", evidence.unsupported_capabilities)

    def test_obstacle_references_preserve_ifc_identity_and_match_existing_voxel_engine(self):
        by_id = {element.global_id: element for element in self.evidence.elements}
        column = by_id[semantic_ifc_guid("storey/0/column/0/0")]
        self.assertEqual(column.classification, ElementClassification.STRUCTURAL_COLUMN)
        expected = (4.8, 5.2, 4.8, 5.2, 0.2, 3.5)
        for actual, target in zip(column.world_bounds.as_tuple(), expected):
            self.assertAlmostEqual(actual, target, places=9)
        self.assertEqual(by_id[semantic_ifc_guid("storey/0/shaft/main/wall/east")].classification, ElementClassification.SHAFT_WALL)
        self.assertEqual(by_id[semantic_ifc_guid("storey/0/wall/north")].classification, ElementClassification.PERIMETER_WALL)
        legacy_obstacles, legacy_shaft = extract_ifc_obstacles(self.config)
        blocked = sorted(element.world_bounds.as_tuple() for element in self.evidence.elements
                         if element.classification in (ElementClassification.PERIMETER_WALL, ElementClassification.SHAFT_WALL, ElementClassification.STRUCTURAL_COLUMN))
        self.assertEqual(blocked, sorted(bounds for _, bounds in legacy_obstacles))
        self.assertEqual(self.evidence.shafts[0].world_bounds.as_tuple(), legacy_shaft)
        for grid in self.evidence.grids:
            legacy = build_occupancy_grid("building", self.config, grid.system, self.systems[grid.system], legacy_obstacles, legacy_shaft)
            self.assertEqual(grid.cells, bytes(legacy.cells))

    def test_coordinates_units_and_nested_placements_are_consistent(self):
        reference = self.evidence.coordinate_reference
        self.assertEqual(reference.frame_id, self.frame)
        self.assertEqual(reference.length_unit, "METRE")
        self.assertEqual(reference.source_length_unit_scale_to_metre, 1.0)
        self.assertFalse(reference.georeferencing_entities_present)
        self.assertTrue(self.evidence.model_bounds.contains_bounds(self.evidence.grid_domain))
        domain = self.evidence.grid_domain.as_tuple()
        for actual, target in zip(domain, (0.0, 20.0, 0.0, 15.0, 2.9, 3.5)):
            self.assertAlmostEqual(actual, target, places=9)
        model = create_ifc_model(self.config)
        storey = model.by_type("IfcBuildingStorey")[0]
        storey.ObjectPlacement.RelativePlacement.Location.Coordinates = (1.0, 0.0, 1.0)
        shifted = {element.global_id: element for element in self.extract(model).elements}
        column = shifted[semantic_ifc_guid("storey/0/column/0/0")].world_bounds
        self.assertAlmostEqual(column.min_x, 5.8, places=9)
        self.assertAlmostEqual(column.min_z, 1.2, places=9)
        self.assertNotIn("PLACEMENT_TRANSFORM_MISMATCH", {item.code for item in self.extract(model).diagnostics})

    def test_existing_opening_is_recorded_but_never_an_approved_penetration(self):
        (opening,) = self.evidence.openings
        self.assertEqual(opening.global_id, semantic_ifc_guid("storey/0/shaft/main/slab-opening"))
        self.assertEqual(opening.host_global_id, semantic_ifc_guid("storey/0/slab/main"))
        self.assertEqual(opening.host_ifc_class, "IfcSlab")
        self.assertFalse(opening.approved_mep_penetration)
        self.assertFalse(opening.intersects_grid_domain)
        with self.assertRaisesRegex(ValueError, "approved MEP penetrations"):
            dataclasses.replace(opening, approved_mep_penetration=True)
        slab = next(item for item in self.evidence.elements if item.classification is ElementClassification.SLAB)
        self.assertIs(slab.aabb_fidelity, AabbFidelity.OVER_APPROXIMATION)

    def test_missing_shaft_and_invalid_grid_are_explicit(self):
        model = create_ifc_model(self.config)
        next(space for space in model.by_type("IfcSpace") if space.Name == "Service Shaft 0").Name = "Renamed"
        missing = self.extract(model)
        self.assertIs(missing.status, SpatialEvidenceStatus.INSUFFICIENT_EVIDENCE)
        self.assertIn(("SHAFT_RESERVATION_UNAVAILABLE", None), codes(missing.diagnostics))
        self.assertEqual(missing.grids, ())
        invalid_config = copy.deepcopy(self.config)
        invalid_config["routing"]["voxel_size_m"] = 0.3
        invalid = self.extract(create_ifc_model(self.config), invalid_config)
        self.assertIs(invalid.status, SpatialEvidenceStatus.INVALID_SPATIAL_INPUT)
        self.assertIn("GRID_SPEC_INVALID", {item.code for item in invalid.diagnostics})
        self.assertEqual(invalid.grids, ())

    def test_failed_geometry_is_unsupported_not_free(self):
        model = create_ifc_model(self.config)
        column = model.by_type("IfcColumn")[0]
        context = column.Representation.Representations[0].ContextOfItems
        column.Representation = model.create_entity("IfcProductDefinitionShape", Representations=[
            model.create_entity("IfcShapeRepresentation", ContextOfItems=context, RepresentationIdentifier="Body",
                                RepresentationType="SweptSolid", Items=[]),
        ])
        evidence = self.extract(model)
        self.assertIs(evidence.status, SpatialEvidenceStatus.UNSUPPORTED_GEOMETRY)
        self.assertIn(("GEOMETRY_EXTRACTION_FAILED", column.GlobalId), codes(evidence.diagnostics))
        self.assertNotIn(column.GlobalId, {element.global_id for element in evidence.elements})

    def test_unmodelled_element_in_plenum_is_reported_unsupported(self):
        model = create_ifc_model(self.config)
        context = model.by_type("IfcGeometricRepresentationSubContext")[0]
        storey = model.by_type("IfcBuildingStorey")[0]
        representation = ifcopenshell.api.geometry.add_slab_representation(
            model, context=context, depth=0.3, polyline=[(0.0, 0.0), (6.0, 0.0), (6.0, 0.3), (0.0, 0.3)])
        location = model.create_entity("IfcCartesianPoint", Coordinates=(2.0, 3.0, 3.0))
        beam = model.create_entity(
            "IfcBeam", GlobalId=semantic_ifc_guid("test/beam"), Name="Test Beam",
            ObjectPlacement=model.create_entity("IfcLocalPlacement", PlacementRelTo=storey.ObjectPlacement,
                                                RelativePlacement=model.create_entity("IfcAxis2Placement3D", Location=location)),
            Representation=model.create_entity("IfcProductDefinitionShape", Representations=[representation]),
        )
        evidence = self.extract(model)
        self.assertIs(evidence.status, SpatialEvidenceStatus.UNSUPPORTED_GEOMETRY)
        self.assertIn(("UNMODELLED_GEOMETRY_IN_GRID_DOMAIN", beam.GlobalId), codes(evidence.diagnostics))
        element = next(item for item in evidence.elements if item.global_id == beam.GlobalId)
        self.assertIs(element.classification, ElementClassification.UNCLASSIFIED)
        self.assertTrue(element.intersects_grid_domain)

    def test_rotated_obstacle_is_conservative_approximation_with_diagnostic(self):
        model = create_ifc_model(self.config)
        column = next(item for item in model.by_type("IfcColumn") if item.GlobalId == semantic_ifc_guid("storey/0/column/0/0"))
        relative = column.ObjectPlacement.RelativePlacement
        relative.Axis = model.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
        relative.RefDirection = model.create_entity("IfcDirection", DirectionRatios=(0.7071067811865476, 0.7071067811865476, 0.0))
        evidence = self.extract(model)
        element = next(item for item in evidence.elements if item.global_id == column.GlobalId)
        self.assertFalse(element.placement_rotation_free)
        self.assertIs(element.aabb_fidelity, AabbFidelity.OVER_APPROXIMATION)
        self.assertGreater(element.world_bounds.max_y - element.world_bounds.min_y, 0.4 + 1e-6)
        self.assertTrue({("ROTATED_PLACEMENT", column.GlobalId), ("AABB_FIDELITY_LIMITATION", column.GlobalId)} <= codes(evidence.diagnostics))
        self.assertIs(evidence.status, SpatialEvidenceStatus.EVIDENCE_AVAILABLE)

    def test_non_metre_units_are_not_silently_accepted(self):
        model = create_ifc_model(self.config)
        unit = next(item for item in model.by_type("IfcSIUnit") if item.UnitType == "LENGTHUNIT")
        unit.Prefix = "MILLI"
        evidence = self.extract(model)
        diagnostic_codes = {item.code for item in evidence.diagnostics}
        self.assertIn("NON_METRE_LENGTH_UNIT_UNVALIDATED", diagnostic_codes)
        self.assertNotIn("PLACEMENT_TRANSFORM_MISMATCH", diagnostic_codes)
        self.assertIs(evidence.status, SpatialEvidenceStatus.UNSUPPORTED_GEOMETRY)

    def test_serialization_is_stable_and_excludes_raw_cells(self):
        repeated = build_synthetic_spatial_evidence("building", self.config, self.systems)
        self.assertEqual(repeated, self.evidence)
        first, second = self.evidence.to_canonical_json(), repeated.to_canonical_json()
        self.assertEqual(first, second)
        payload = json.loads(first)
        self.assertEqual(payload["status"], "EVIDENCE_AVAILABLE")
        self.assertNotIn("cells", payload["grids"][0])
        self.assertEqual(payload["grids"][0]["cells_sha256"], self.evidence.grids[0].cells_sha256)
        self.assertTrue(all(opening["approved_mep_penetration"] is False for opening in payload["openings"]))

    def test_evidence_models_are_immutable(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.evidence.elements = ()  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.evidence.elements[0].world_bounds.min_x = 1.0  # type: ignore[misc]
        grid = self.evidence.grids[0]
        self.assertIsInstance(grid.cells, bytes)
        tampered = bytearray(grid.cells)
        tampered[0] = BLOCKED_FIXED if tampered[0] != BLOCKED_FIXED else 0
        with self.assertRaisesRegex(ValueError, "digest"):
            OccupancyGridReference(grid.scenario_id, grid.system, grid.spec, grid.clearance_m, grid.required_planar_margin_m,
                                   grid.required_vertical_margin_m, grid.shaft_bounds, grid.cells_sha256, bytes(tampered))
        unknown = bytearray(grid.cells)
        unknown[0] = 9
        with self.assertRaisesRegex(ValueError, "vocabulary"):
            OccupancyGridReference(grid.scenario_id, grid.system, grid.spec, grid.clearance_m, grid.required_planar_margin_m,
                                   grid.required_vertical_margin_m, grid.shaft_bounds, hashlib.sha256(unknown).hexdigest(),
                                   bytes(unknown))
        with self.assertRaisesRegex(ValueError, "scenario"):
            dataclasses.replace(self.evidence, grids=(dataclasses.replace(grid, scenario_id="other"),))

    def test_read_only_grid_view_shares_snapshot_without_copying(self):
        grid = self.evidence.grids[0]
        view = read_only_occupancy_view(grid)
        self.assertIs(view.cells, grid.cells)
        with self.assertRaises(TypeError):
            view.cells[0] = BLOCKED_FIXED  # type: ignore[index]

    def test_endpoint_positions_inside_outside_and_blocked(self):
        problem = water_problem(
            ("endpoint/column", (5.0, 5.0, 3.2), EndpointProvenance.MANUAL),
            ("endpoint/free", (12.5, 2.5, 3.2), EndpointProvenance.MANUAL),
            ("endpoint/inferred", (2.5, 12.5, 3.2), EndpointProvenance.INFERRED),
            ("endpoint/outside", (-1.0, 2.5, 3.2), EndpointProvenance.MANUAL),
        )
        (candidate,) = plan_topology_candidates(problem)[:1]
        assessment = assess_topology_candidate(candidate, self.evidence, endpoint_frame_id=self.frame)
        by_id = {item.endpoint_id: item for item in assessment.endpoints}
        self.assertIs(by_id["endpoint/free"].grid_compatibility, EndpointGridCompatibility.CONTAINING_CELL_FREE)
        self.assertIs(by_id["endpoint/column"].grid_compatibility, EndpointGridCompatibility.NOT_SNAPPABLE)
        self.assertEqual(by_id["endpoint/column"].containing_cell_state, "BLOCKED_FIXED")
        self.assertIs(by_id["endpoint/outside"].grid_compatibility, EndpointGridCompatibility.OUTSIDE_GRID_DOMAIN)
        self.assertFalse(by_id["endpoint/outside"].within_grid_domain)
        self.assertFalse(by_id["endpoint/inferred"].position_authoritative)
        self.assertIs(assessment.status, SpatialEvidenceStatus.INSUFFICIENT_EVIDENCE)
        self.assertTrue({
            ("ENDPOINT_OUTSIDE_SUPPORTED_BOUNDS", "endpoint/outside"),
            ("ENDPOINT_NOT_GRID_COMPATIBLE", "endpoint/column"),
            ("ENDPOINT_POSITION_NOT_AUTHORITATIVE", "endpoint/inferred"),
        } <= codes(assessment.diagnostics))

    def test_frame_mismatch_is_invalid_and_positions_are_not_reinterpreted(self):
        problem = water_problem(("endpoint/free", (12.5, 2.5, 3.2), EndpointProvenance.MANUAL))
        assessment = assess_topology_candidate(plan_topology_candidates(problem)[0], self.evidence, endpoint_frame_id="other-frame")
        self.assertIs(assessment.status, SpatialEvidenceStatus.INVALID_SPATIAL_INPUT)
        self.assertTrue(all(item.grid_compatibility is EndpointGridCompatibility.NOT_EVALUATED_FRAME_MISMATCH for item in assessment.endpoints))

    def test_unsupported_evidence_makes_candidate_assessment_unusable(self):
        model = create_ifc_model(self.config)
        unit = next(item for item in model.by_type("IfcSIUnit") if item.UnitType == "LENGTHUNIT")
        unit.Prefix = "MILLI"
        evidence = extract_spatial_evidence(model, self.config, self.systems, scenario_id="building", frame_id=self.frame)
        problem = water_problem(("endpoint/free", (12.5, 2.5, 3.2), EndpointProvenance.MANUAL))
        assessment = assess_topology_candidate(plan_topology_candidates(problem)[0], evidence, endpoint_frame_id=self.frame)
        self.assertFalse(assessment.source_model_usable)
        self.assertIsNot(assessment.status, SpatialEvidenceStatus.EVIDENCE_AVAILABLE)


class BenchmarkCandidateAssessmentTests(unittest.TestCase):
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

    def assess(self, candidate):
        return assess_topology_candidate(candidate, self.evidence, endpoint_frame_id=self.frame)

    def test_direct_candidate_has_evidence_but_no_feasibility_claim(self):
        direct = self.candidates[0]
        self.assertIs(direct.kind, TopologyCandidateKind.DIRECT)
        assessment = self.assess(direct)
        self.assertIs(assessment.status, SpatialEvidenceStatus.EVIDENCE_AVAILABLE)
        self.assertTrue(assessment.source_model_usable)
        self.assertFalse(assessment.physical_feasibility_evaluated)
        self.assertFalse(assessment.collision_free_claimed)
        self.assertTrue(all(item.grid_compatibility in (EndpointGridCompatibility.CONTAINING_CELL_FREE,
                                                        EndpointGridCompatibility.SNAPPABLE_WITHIN_TOLERANCE)
                            for item in assessment.endpoints))
        unresolved = {(item.kind, item.subject_id) for item in assessment.unresolved_physical_requirements}
        self.assertEqual(unresolved, {("EDGE_ROUTE_GEOMETRY_UNRESOLVED", edge.edge_id) for edge in direct.demand.topology.edges})
        with self.assertRaisesRegex(ValueError, "cannot claim"):
            dataclasses.replace(assessment, collision_free_claimed=True)
        with self.assertRaisesRegex(ValueError, "cannot claim"):
            dataclasses.replace(assessment, physical_feasibility_evaluated=True)

    def test_shared_main_junction_position_is_not_invented(self):
        shared = next(item for item in self.candidates if item.kind is TopologyCandidateKind.SHARED_MAIN)
        assessment = self.assess(shared)
        unresolved = {(item.kind, item.subject_id) for item in assessment.unresolved_physical_requirements}
        self.assertIn(("JUNCTION_POSITION_UNRESOLVED", "node/virtual/junction/001"), unresolved)
        self.assertIn(("SHARED_TRUNK_FIT_UNEVALUATED", "edge/shared-main/001"), unresolved)
        bound = {node.endpoint_id for node in shared.demand.topology.nodes if node.endpoint_id is not None}
        self.assertEqual({item.endpoint_id for item in assessment.endpoints}, bound)
        self.assertNotIn("junction", json.dumps([item.to_canonical_dict() for item in assessment.endpoints]))

    def test_assessment_is_deterministic_and_leaves_demand_unchanged(self):
        before_problem = repr(self.problem)
        before_demands = [json.dumps(item.demand.to_canonical_dict(), sort_keys=True) for item in self.candidates]
        first = [self.assess(item) for item in self.candidates]
        second = [self.assess(item) for item in self.candidates]
        self.assertEqual(first, second)
        self.assertEqual([json.dumps(item.to_canonical_dict(), sort_keys=True) for item in first],
                         [json.dumps(item.to_canonical_dict(), sort_keys=True) for item in second])
        self.assertEqual(repr(self.problem), before_problem)
        self.assertEqual(self.problem, benchmark_case_to_routing_problem(self.case, self.systems))
        self.assertEqual([json.dumps(item.demand.to_canonical_dict(), sort_keys=True) for item in self.candidates], before_demands)
        required = {connection.connection_id for connection in self.problem.connections}
        self.assertEqual(required, {request["connection_id"] for request in self.case["connection_requests"]})
        for candidate in self.candidates:
            coverage = inspect_topology_demand_coverage(self.problem, candidate.demand.assignments)
            self.assertEqual(coverage.coverage_status, "COMPLETE")
            self.assertEqual(set(coverage.assigned_connection_ids), required)

    def test_serialized_assessments_make_no_physical_claims(self):
        for candidate in self.candidates:
            payload = self.assess(candidate).to_canonical_dict()
            text = json.dumps(payload, sort_keys=True)
            self.assertIs(payload["collision_free_claimed"], False)
            self.assertIs(payload["physical_feasibility_evaluated"], False)
            for key in FORBIDDEN_CLAIM_KEYS:
                self.assertNotIn(f'"{key}"', text)


if __name__ == "__main__":
    unittest.main()

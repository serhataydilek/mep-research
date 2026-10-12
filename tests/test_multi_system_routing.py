from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path

from src.clearance_policy import benchmark_v1_policy
from src.demand import load_service_definitions
from src.endpoints import RoutingProblem
from src.multi_system_diagnostics import diagnose_multi_system_conflicts
from src.multi_system_routing import ConnectionRouteStatus, EndpointCellMode, MultiSystemRoutingResult, RoutingStrategy, route_multi_system
from src.physical_edge_routing import PhysicalEdgeRouteStatus, route_physical_edge
from src.topology_planner import plan_topology_candidates
from src.visualization_export import COORDINATED_FIXTURE, MULTI_SYSTEM_FIXTURES, export_multi_system_fixture, export_multi_system_fixtures, controlled_s01_inputs, validate_multi_system_fixture
from src.voxel import SYSTEM_ORDER

ROOT = Path(__file__).resolve().parents[1]
STRICT, SNAP = EndpointCellMode.CONTAINING_CELL_ONLY, EndpointCellMode.EVIDENCE_SNAP_WITHIN_TOLERANCE


class MultiSystemBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p, cls.e, cls.pol, cls.f = controlled_s01_inputs()
        cls.strict = route_multi_system(cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f)
        cls.snap = route_multi_system(cls.p, cls.e, cls.pol, endpoint_frame_id=cls.f, endpoint_cell_mode=SNAP)
        cls.strict_d = diagnose_multi_system_conflicts(cls.strict, cls.e, cls.pol)
        cls.snap_d = diagnose_multi_system_conflicts(cls.snap, cls.e, cls.pol)

    def by_id(self, result):
        return {x.connection_id: x for x in result.connections}


class MultiSystemRoutingTests(MultiSystemBase):
    def test_authoritative_multi_system_demand_coverage(self):
        demand = [x.connection_id for x in self.p.connections]
        self.assertEqual(self.strict.systems, tuple(s for s in SYSTEM_ORDER if s in {x.system.value for x in self.p.connections}))
        self.assertEqual(len(self.strict.systems), 5)
        for result in (self.strict, self.snap):
            self.assertEqual(list(result.required_connection_ids), demand)
            self.assertEqual([x.connection_id for x in result.connections], demand)
            s = result.summary()
            self.assertEqual(s["required_connection_count"], 13)
            self.assertEqual(sum(s[f"{x.value.lower()}_connection_count"] for x in ConnectionRouteStatus), 13)
            for row in s["per_system"]:
                self.assertEqual(row["required"], sum(x.system.value == row["system"] for x in self.p.connections))

    def test_missing_connection_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one route record"):
            replace(self.strict, connections=self.strict.connections[1:])
        with self.assertRaisesRegex(ValueError, "routed only once"):
            replace(self.snap, edges=self.snap.edges + self.snap.edges[:1])
        with self.assertRaisesRegex(ValueError, "collision freedom"):
            replace(self.snap, collision_free_claimed=True)

    def test_deterministic_execution_and_connection_order_independence(self):
        again = route_multi_system(self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=SNAP)
        self.assertEqual(again.to_canonical_json(), self.snap.to_canonical_json())
        rebuilt = RoutingProblem(tuple(self.p.endpoints), tuple(self.p.connections), dict(self.p.metadata))
        other = route_multi_system(rebuilt, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=SNAP)
        self.assertEqual(other.to_canonical_json(), self.snap.to_canonical_json())
        with self.assertRaisesRegex(ValueError, "deterministic ID ordering"):
            RoutingProblem(self.p.endpoints, tuple(reversed(self.p.connections)), self.p.metadata)
        self.assertEqual([x.system for x in self.snap.attempts if x.selected], list(self.snap.systems))

    def test_degenerate_zero_length_main_is_rejected(self):
        # Entry/snap cells are excluded from junction search so a shared MAIN cannot collapse.
        for result in (self.strict, self.snap):
            degenerate = [x for x in result.attempts if x.reason == "DEGENERATE_ZERO_LENGTH_MAIN"]
            self.assertTrue(all(not x.selected and x.routed_connection_ids == () for x in degenerate))
            self.assertTrue(
                all(len(x.route.grid_cells) >= 2 for x in result.edges
                    if x.role == "MAIN" and x.route.status is PhysicalEdgeRouteStatus.ROUTED)
            )
        water = [x for x in self.snap.attempts if x.system == "water" and x.selected]
        self.assertEqual(len(water), 1)
        self.assertIs(water[0].strategy, RoutingStrategy.SHARED_MAIN)
        self.assertIsNone(water[0].reason)
        main = next(x for x in self.snap.edges if x.role == "MAIN" and x.system == "water")
        self.assertGreaterEqual(len(main.route.grid_cells), 2)

    def test_shared_main_routed_once_and_owned_by_one_system(self):
        mains = [x for x in self.snap.edges if x.role == "MAIN"]
        self.assertEqual(sorted(x.system for x in mains), ["electrical", "fire", "hvac", "water"])
        for main in mains:
            self.assertEqual(sum(x.physical_edge_id == main.physical_edge_id for x in self.snap.edges), 1)
            self.assertGreaterEqual(len(main.connection_ids), 2)
            self.assertTrue(all(self.by_id(self.snap)[c].system == main.system == main.owner_system for c in main.connection_ids))
            self.assertTrue(all(main.physical_edge_id in self.by_id(self.snap)[c].physical_edge_ids for c in main.connection_ids))
        self.assertEqual(len({x.edge_id for x in mains}), 1)
        self.assertEqual(len({x.physical_edge_id for x in mains}), 4)
        self.assertEqual(self.snap_d["checks"]["shared_edge_ownership"]["status"], "PASS")

    def test_per_system_grid_and_policy_consistency(self):
        for result in (self.strict, self.snap):
            for edge in result.edges:
                self.assertEqual((edge.route.policy_id, edge.route.policy_digest_sha256), (self.pol.policy_id, self.pol.digest_sha256))
                self.assertEqual(edge.route.grid_cells_sha256, self.e.grid_for(edge.system).cells_sha256)
        check = self.snap_d["checks"]["clearance_policy_consistency"]
        self.assertEqual(check["status"], "PASS")
        self.assertTrue(check["grid_specs_identical_across_systems"])
        self.assertTrue(all(row["consistent"] for row in check["per_system_margins"]))
        self.assertEqual(len(check["inter_system_rules"]), 10)

    def test_mismatched_policy_propagates_as_unresolved(self):
        services = load_service_definitions(ROOT / "config/mep_systems.json")
        other = benchmark_v1_policy({"routing": {"clearance_m": 0.06}}, services)
        result = route_multi_system(self.p, self.e, other, endpoint_frame_id=self.f)
        self.assertEqual(result.count(ConnectionRouteStatus.ROUTED), 0)
        self.assertEqual(result.count(ConnectionRouteStatus.UNRESOLVED), 13)
        self.assertTrue(all("GRID_OR_POLICY_MISMATCH" in x.reason for x in result.connections))
        diagnostics = diagnose_multi_system_conflicts(result, self.e, other)
        self.assertEqual(diagnostics["summary"]["grid_level_verdict"], "INCONSISTENT_INPUTS")
        codes = {x["code"] for x in diagnostics["checks"]["clearance_policy_consistency"]["violations"]}
        self.assertEqual(codes, {"GRID_POLICY_DIGEST_MISMATCH", "GRID_MARGIN_POLICY_MISMATCH"})
        self.assertEqual(diagnostics["checks"]["inter_system_overlap"]["status"], "NOT_EVALUATED")

    def test_route_status_propagation_in_strict_mode(self):
        records = self.by_id(self.strict)
        for cid, record in records.items():
            if record.system in ("electrical", "drainage"):
                self.assertIs(record.status, ConnectionRouteStatus.ROUTED)
                self.assertTrue(record.grid_cells)
            else:
                self.assertIs(record.status, ConnectionRouteStatus.UNRESOLVED)
                self.assertTrue(record.reason.startswith("ENDPOINT_CELL_NOT_FREE@"))
                self.assertEqual(record.grid_cells, ())
        for system in ("hvac", "water", "fire"):
            attempts = [x for x in self.strict.attempts if x.system == system]
            self.assertEqual([x.strategy for x in attempts], [RoutingStrategy.SHARED_MAIN] * 3 + [RoutingStrategy.DIRECT])
            self.assertFalse(attempts[-1].selected)
            self.assertEqual(attempts[-1].routed_connection_ids, ())
        self.assertTrue(all("DRAINAGE_GRAVITY_SLOPE_UNVALIDATED" in records[c].unvalidated_requirements for c in ("drainage/0", "drainage/1")))

    def test_snap_mode_is_explicit_and_recorded(self):
        self.assertEqual(self.strict.endpoint_snaps, ())
        self.assertEqual([x.endpoint_id for x in self.snap.endpoint_snaps], ["egress/fire", "egress/hvac", "egress/water"])
        for snap in self.snap.endpoint_snaps:
            self.assertEqual(snap.containing_cell_state, "BLOCKED_FIXED")
            self.assertLessEqual(snap.snap_distance_m, 2 * self.e.grid_for(snap.system).spec.voxel_size_m)
            self.assertFalse(self.e.shafts[0].world_bounds.contains_point(*snap.snap_world_m, tolerance=0.0))
        self.assertEqual(self.snap.count(ConnectionRouteStatus.ROUTED), 13)
        for record in self.snap.connections:
            snapped = record.system in ("fire", "hvac", "water")
            self.assertEqual("ENDPOINT_SNAPPED_CONNECTOR_ENGAGEMENT_UNVALIDATED" in record.unvalidated_requirements, snapped)

    def test_search_limit_reports_failed_not_dropped(self):
        result = route_multi_system(self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode=SNAP, max_expansions=1)
        self.assertEqual(result.count(ConnectionRouteStatus.FAILED), 13)
        self.assertEqual(len(result.connections), 13)
        self.assertTrue(all(x.reason.startswith("SEARCH_LIMIT_REACHED@") for x in result.connections))
        self.assertTrue(all(x.route.status is PhysicalEdgeRouteStatus.SEARCH_LIMIT_REACHED for x in result.edges if x.role == "MAIN"))

    def test_system_without_grid_is_unsupported(self):
        evidence = replace(self.e, grids=tuple(g for g in self.e.grids if g.system != "water"))
        result = route_multi_system(self.p, evidence, self.pol, endpoint_frame_id=self.f)
        records = self.by_id(result)
        self.assertEqual([records[c].status for c in ("water/0", "water/1")], [ConnectionRouteStatus.UNSUPPORTED] * 2)
        self.assertEqual(records["water/0"].reason, "NO_COMPATIBLE_GRID")
        self.assertEqual(len(result.connections), 13)
        self.assertEqual(result.count(ConnectionRouteStatus.ROUTED), self.strict.count(ConnectionRouteStatus.ROUTED))

    def test_endpoint_cell_override_must_still_be_free(self):
        direct = plan_topology_candidates(self.p)[0]
        egress = next(x for x in self.p.endpoints if x.endpoint_id == "egress/hvac")
        blocked = self.e.grid_for("hvac").spec.world_to_containing_cell(egress.position.x, egress.position.y, egress.position.z)
        snap = next(x for x in self.snap.endpoint_snaps if x.endpoint_id == "egress/hvac")
        default = route_physical_edge(direct, self.e, self.pol, "edge/direct/hvac/0", endpoint_frame_id=self.f)
        forced = route_physical_edge(direct, self.e, self.pol, "edge/direct/hvac/0", endpoint_frame_id=self.f, endpoint_cells={"egress/hvac": blocked})
        snapped = route_physical_edge(direct, self.e, self.pol, "edge/direct/hvac/0", endpoint_frame_id=self.f, endpoint_cells={"egress/hvac": snap.snap_cell})
        self.assertEqual(default, forced)
        self.assertEqual(default.reason, "ENDPOINT_CELL_NOT_FREE")
        self.assertIs(snapped.status, PhysicalEdgeRouteStatus.ROUTED)
        self.assertEqual(snapped.grid_cells[0], snap.snap_cell)

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            route_multi_system(self.p, self.e, self.pol, endpoint_frame_id=self.f, max_expansions=0)
        with self.assertRaises(ValueError):
            route_multi_system(self.p, self.e, self.pol, endpoint_frame_id=self.f, endpoint_cell_mode="SNAP")


class MultiSystemDiagnosticsTests(MultiSystemBase):
    def _brute_shared(self, result, diagnostics):
        evaluated = [x for x in result.edges if x.physical_edge_id in diagnostics["evaluated_physical_edge_ids"]]
        return {(a.physical_edge_id, b.physical_edge_id): len(set(a.route.grid_cells) & set(b.route.grid_cells))
                for i, a in enumerate(evaluated) for b in evaluated[i + 1:] if a.system != b.system and set(a.route.grid_cells) & set(b.route.grid_cells)}

    def test_inter_system_overlap_matches_actual_cells(self):
        for result, diagnostics in ((self.strict, self.strict_d), (self.snap, self.snap_d)):
            check = diagnostics["checks"]["inter_system_overlap"]
            reported = {(r["edge_a"], r["edge_b"]): r["shared_cell_count"] for r in check["records"] if r["shared_cell_count"]}
            self.assertEqual(reported, self._brute_shared(result, diagnostics))
            spec = self.e.grid_for("hvac").spec
            for record in check["records"]:
                a = next(x for x in result.edges if x.physical_edge_id == record["edge_a"])
                cells = {tuple(round(v, 9) for v in spec.cell_center(*c)) for c in a.route.grid_cells}
                self.assertTrue(all(tuple(p) in cells for p in record["shared_cells_world_m"]))
                self.assertNotEqual(record["system_a"], record["system_b"])
        self.assertEqual(self.strict_d["summary"]["inter_system_conflicting_pair_count"], 2)
        self.assertEqual(self.strict_d["summary"]["inter_system_shared_cell_pair_count"], 2)
        self.assertTrue(all("drainage/1" in r["edge_a"] for r in self.strict_d["checks"]["inter_system_overlap"]["records"]))
        self.assertEqual(self.snap_d["summary"]["inter_system_conflicting_pair_count"], 28)
        self.assertEqual(self.snap_d["summary"]["inter_system_shared_cell_pair_count"], 10)
        self.assertEqual(self.snap_d["summary"]["inter_system_hard_envelope_pair_count"], 24)

    def test_same_system_unknown_clearance_stays_unresolved(self):
        for diagnostics in (self.strict_d, self.snap_d):
            check = diagnostics["checks"]["same_system_independent_run_overlap"]
            self.assertEqual(check["status"], "EVALUATED_CLEARANCE_UNRESOLVED")
            self.assertTrue(all(r["clearance_rule_status"] == "UNDEFINED" for r in check["records"]))
            self.assertEqual(check["unresolved_clearance_pair_count"], check["pair_count"])
            self.assertTrue(all(r["verdict"] in ("NO_UNEXPLAINED_SHARED_CELL_CLEARANCE_UNDEFINED", "GRID_OVERLAP_COLLISION_SEMANTICS_UNRESOLVED") for r in check["records"]))
            codes = {(x["code"], x["subject_id"]) for x in diagnostics["unresolved_collision_semantics"]}
            for system in check["clearance_rule_status_by_system"]:
                self.assertIn(("SAME_SYSTEM_CLEARANCE_UNDEFINED", system), codes)
            self.assertNotEqual(diagnostics["summary"]["grid_level_verdict"], "CONFLICT_FREE")
        overlaps = {(r["edge_a"].split("#")[1], r["edge_b"].split("#")[1]): r["unexplained_shared_cell_count"]
                    for r in self.snap_d["checks"]["same_system_independent_run_overlap"]["records"] if r["unexplained_shared_cell_count"]}
        self.assertEqual(overlaps, {("edge/shared-branch/hvac/1", "edge/shared-main/001"): 40, ("edge/shared-branch/fire/0", "edge/shared-main/001"): 1})

    def test_partial_networks_excluded_but_listed(self):
        excluded = set(self.strict_d["excluded_physical_edge_ids"])
        self.assertEqual(len(excluded), 11)
        self.assertTrue(all(any(s in x for s in ("hvac", "water", "fire", "shared-main/00")) for x in excluded))
        codes = [x["code"] for x in self.strict_d["unresolved_collision_semantics"]]
        self.assertEqual(codes.count("CONNECTION_NOT_ROUTED_NOT_EVALUATED"), 8)
        self.assertEqual(codes.count("PARTIAL_NETWORK_EDGE_EXCLUDED"), 11)
        self.assertEqual(self.snap_d["excluded_physical_edge_ids"], [])

    def test_ownership_violation_is_detected(self):
        edge = next(x for x in self.snap.edges if x.role == "MAIN" and x.system == "hvac")
        tampered = replace(self.snap, edges=tuple(replace(x, connection_ids=x.connection_ids + ("drainage/0",)) if x is edge else x for x in self.snap.edges))
        check = diagnose_multi_system_conflicts(tampered, self.e, self.pol)["checks"]["shared_edge_ownership"]
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("CROSS_SYSTEM_EDGE_SHARING", {x["code"] for x in check["violations"]})

    def test_obstacle_intersection_is_evaluated_and_detects_blocked_cells(self):
        check = self.snap_d["checks"]["obstacle_intersection"]
        self.assertEqual(check["status"], "EVALUATED")
        self.assertEqual(check["blocked_element_count"], 10)
        self.assertEqual((check["non_free_grid_cell_count"], check["centerline_inside_obstacle_count"], check["nominal_envelope_obstacle_overlap_count"]), (0, 0, 0))
        egress = next(x for x in self.p.endpoints if x.endpoint_id == "egress/hvac")
        grid = self.e.grid_for("hvac"); blocked = grid.spec.world_to_containing_cell(egress.position.x, egress.position.y, egress.position.z)
        edge = next(x for x in self.snap.edges if x.role == "MAIN" and x.system == "hvac")
        bad = replace(edge, route=replace(edge.route, grid_cells=(blocked,) + edge.route.grid_cells, world_centerline_m=(grid.spec.cell_center(*blocked),) + edge.route.world_centerline_m))
        tampered = replace(self.snap, edges=tuple(bad if x is edge else x for x in self.snap.edges))
        result = diagnose_multi_system_conflicts(tampered, self.e, self.pol)
        self.assertEqual(result["checks"]["obstacle_intersection"]["non_free_grid_cell_count"], 1)
        self.assertEqual(result["summary"]["grid_level_verdict"], "CONFLICTS_DETECTED")

    def test_absent_results_are_never_zero_conflict(self):
        empty = replace(self.strict, connections=tuple(replace(x, status=ConnectionRouteStatus.FAILED, reason="TEST", grid_cells=()) for x in self.strict.connections), edges=())
        diagnostics = diagnose_multi_system_conflicts(empty, self.e, self.pol)
        self.assertEqual(diagnostics["summary"]["grid_level_verdict"], "NOT_FULLY_EVALUATED")
        self.assertIsNone(diagnostics["summary"]["inter_system_conflicting_pair_count"])
        self.assertEqual(diagnostics["checks"]["inter_system_overlap"]["status"], "NOT_EVALUATED")
        for d in (diagnostics, self.strict_d, self.snap_d):
            self.assertFalse(d["geometric_clash_certified"]); self.assertFalse(d["collision_free_claimed"])
            self.assertIn("CONTINUOUS_GEOMETRY_CLASH_CERTIFICATION_UNAVAILABLE", {x["code"] for x in d["unresolved_collision_semantics"]})

    def test_diagnostics_are_deterministic(self):
        again = diagnose_multi_system_conflicts(self.snap, self.e, self.pol)
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.snap_d, sort_keys=True))


class MultiSystemExportTests(MultiSystemBase):
    def test_multi_system_json_export_and_fixture_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            data = export_multi_system_fixture(self.snap, self.snap_d, self.e, self.p, Path(directory) / "f.json")
            self.assertEqual(json.loads((Path(directory) / "f.json").read_text(encoding="utf-8")), data)
        self.assertEqual(data["summary"]["routed_connection_count"], 13)
        self.assertEqual(len(data["conflicts"]), 30)
        self.assertEqual(sum(c["kind"] == "SAME_SYSTEM_UNRESOLVED" for c in data["conflicts"]), 2)
        self.assertFalse(data["geometric_clash_certified"])
        self.assertIn("NOT continuous-geometry clash certification", data["certification_warning"])
        routed = {e["physical_edge_id"] for e in data["edges"] if e["status"] == "ROUTED"}
        self.assertTrue(all(set(c["physical_edge_ids"]) <= routed for c in data["conflicts"]))
        self.assertTrue(all(len(c["hard_overlap_regions_shown"]) <= data["max_regions_per_pair"] for c in data["conflicts"]))
        for edge, record in zip(data["edges"], self.snap.edges):
            self.assertEqual(edge["world_polyline_m"][0], [round(v, 9) for v in record.route.world_centerline_m[0]])
            self.assertEqual(edge["world_polyline_m"][-1], [round(v, 9) for v in record.route.world_centerline_m[-1]])
            self.assertEqual(edge["grid_cell_count"], len(record.route.grid_cells))

    def test_failed_connections_remain_in_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            data = export_multi_system_fixture(self.strict, self.strict_d, self.e, self.p, Path(directory) / "f.json")
        statuses = {c["connection_id"]: c["status"] for c in data["connections"]}
        self.assertEqual(len(statuses), 13)
        self.assertEqual(sum(v == "UNRESOLVED" for v in statuses.values()), 8)
        self.assertEqual([e["physical_edge_id"] for e in data["edges"] if e["status"] != "ROUTED"],
                         [e.physical_edge_id for e in self.strict.edges if e.route.status is not PhysicalEdgeRouteStatus.ROUTED])
        self.assertTrue(all(e["world_polyline_m"] == [] for e in data["edges"] if e["status"] != "ROUTED"))
        self.assertEqual(sum(not j["network_complete"] for j in data["junctions"]), 3)
        self.assertEqual(sum(not e["diagnostics_evaluated"] for e in data["edges"]), 11)

    def test_fixture_validation_rejects_dishonest_data(self):
        with tempfile.TemporaryDirectory() as directory:
            data = export_multi_system_fixture(self.strict, self.strict_d, self.e, self.p, Path(directory) / "f.json")
        with self.assertRaisesRegex(ValueError, "clash certification"):
            validate_multi_system_fixture({**data, "geometric_clash_certified": True})
        with self.assertRaisesRegex(ValueError, "every required connection"):
            validate_multi_system_fixture({**data, "connections": [c for c in data["connections"] if c["status"] == "ROUTED"]})
        with self.assertRaisesRegex(ValueError, "summary does not match"):
            validate_multi_system_fixture({**data, "summary": {**data["summary"], "routed_connection_count": 13}})
        bad_conflict = {**data["conflicts"][0], "physical_edge_ids": [data["edges"][0]["physical_edge_id"], "missing"]}
        with self.assertRaisesRegex(ValueError, "Conflict marker"):
            validate_multi_system_fixture({**data, "conflicts": [bad_conflict]})

    def test_committed_viewer_fixtures_are_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            export_multi_system_fixtures(Path(directory))
            for name in MULTI_SYSTEM_FIXTURES.values():
                committed = ROOT / "viewer" / name
                self.assertEqual((Path(directory) / name).read_bytes(), committed.read_bytes(), name)
                validate_multi_system_fixture(json.loads(committed.read_text(encoding="utf-8")))
        self.assertTrue((ROOT / "viewer" / "viewer_fixture.json").exists())

    def test_viewer_supports_multi_system_features(self):
        text = (ROOT / "viewer" / "index.html").read_text(encoding="utf-8")
        for required in ("viewer_fixture.json", *MULTI_SYSTEM_FIXTURES.values(), COORDINATED_FIXTURE, "SYSTEM_COLORS", "data-system", "LineDashedMaterial",
                         "id=\"conflicts\"", "certification_warning", "geometric_clash_certified!==false", "connector_engagement_validated===true",
                         "required_connection_count", "failed_connection_count", "showConnection", "showConflict", "diagnostics_evaluated",
                         "schema_version===1", "shortId", "physical_edge_ids.join"):
            self.assertIn(required, text)
        self.assertNotIn("x.split('#').pop()", text)

    def test_conflict_records_use_qualified_edge_identities(self):
        for diagnostics in (self.strict_d, self.snap_d):
            for record in diagnostics["checks"]["inter_system_overlap"].get("records", []):
                self.assertIn("#", record["edge_a"])
                self.assertIn("#", record["edge_b"])
                self.assertTrue(record["edge_a"].startswith("candidate/"))
                self.assertTrue(record["edge_b"].startswith("candidate/"))
            for edge_id in diagnostics["evaluated_physical_edge_ids"] + diagnostics["excluded_physical_edge_ids"]:
                self.assertIn("#", edge_id)


if __name__ == "__main__":
    unittest.main()

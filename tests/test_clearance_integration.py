"""Parity gates for Phase 9C.3 policy adapters; legacy outputs remain authoritative."""

from __future__ import annotations

import dataclasses
import json
from copy import deepcopy
from pathlib import Path
import unittest

from src.clash import _evaluate_route_pair
from src.clearance_policy import benchmark_v1_compatibility_report, benchmark_v1_policy
from src.config import load_building_config
from src.demand import generate_benchmark_cases, load_service_definitions
from src.endpoints import benchmark_case_to_routing_problem
from src.junction_placement import propose_junction_placements
from src.routing_readiness import assess_routing_readiness
from src.spatial_evidence import build_synthetic_spatial_evidence, synthetic_frame_id
from src.topology_planner import TopologyCandidateKind, plan_topology_candidates
from src.voxel import GridSpec, build_occupancy_grid
from src.sequential import block_prior_routes
from src.voxel import OccupancyGrid


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
DEMANDS = ROOT / "experiments" / "demands"
SYSTEMS = ROOT / "config" / "mep_systems.json"


class ClearanceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.definitions = load_service_definitions(SYSTEMS)
        manifest = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
        cls.config = load_building_config(SCENARIOS / manifest["scenarios"][0]["config_file"])
        cls.policy = benchmark_v1_policy(cls.config, cls.definitions)

    def test_voxel_policy_adapter_has_grid_and_checksum_parity(self):
        evidence = build_synthetic_spatial_evidence("parity", self.config, self.definitions)
        self.assertTrue(all(grid.clearance_policy_digest_sha256 == self.policy.digest_sha256 for grid in evidence.grids))
        for grid in evidence.grids:
            rebuilt = build_occupancy_grid(grid.scenario_id, self.config, grid.system, self.definitions[grid.system],
                                           clearance_policy=self.policy)
            self.assertEqual(bytes(rebuilt.cells), grid.cells)
            self.assertEqual(rebuilt.spec, grid.spec)

    def test_configured_and_legacy_hardcoded_values_remain_distinct(self):
        altered = deepcopy(self.config)
        altered["routing"]["clearance_m"] = 0.07
        policy = benchmark_v1_policy(altered, self.definitions)
        report = benchmark_v1_compatibility_report(policy)
        self.assertTrue(report.obstacle_and_legacy_values_diverge)
        self.assertEqual(policy.inter_system_clearance("hvac", "water").value_m, 0.05)
        spec = GridSpec(0.1, 0, 0, 0, 30, 20, 5)
        prior = {"system": "hvac", "path_cells": [[5, 5, 2], [20, 5, 2]]}
        default = OccupancyGrid("case", "water", spec, bytearray(spec.total_cell_count), (0, 0, 0, 0, 0, 0), {})
        explicit = OccupancyGrid("case", "water", spec, bytearray(spec.total_cell_count), (0, 0, 0, 0, 0, 0), {})
        block_prior_routes(default, [prior], self.definitions["water"], self.definitions, target_system="water")
        block_prior_routes(explicit, [prior], self.definitions["water"], self.definitions, policy, target_system="water")
        self.assertEqual(bytes(default.cells), bytes(explicit.cells))

    def test_clash_policy_adapter_preserves_hard_and_clearance_classification(self):
        definitions = {"hvac": self.definitions["hvac"], "water": self.definitions["water"]}
        spec = GridSpec(0.01, 0, 0, 0, 200, 200, 1)
        first = {"system": "hvac", "connection_id": "h", "path_cells": [[0, 0, 0], [100, 0, 0]]}
        second = {"system": "water", "connection_id": "w", "path_cells": [[0, 24, 0], [100, 24, 0]]}
        baseline = _evaluate_route_pair("case", first, second, spec, definitions)
        via_policy = _evaluate_route_pair("case", first, second, spec, definitions, self.policy)
        self.assertEqual(baseline, via_policy)
        self.assertFalse(via_policy["hard_envelope_conflict"])
        self.assertTrue(via_policy["clearance_violation"])

    def test_readiness_requires_junction_evidence_but_never_claims_feasibility(self):
        case = generate_benchmark_cases(SCENARIOS, DEMANDS, SYSTEMS)[0]
        problem = benchmark_case_to_routing_problem(case, self.definitions)
        candidate = next(item for item in plan_topology_candidates(problem) if item.kind is TopologyCandidateKind.SHARED_MAIN)
        evidence = build_synthetic_spatial_evidence(case["scenario_id"], self.config, self.definitions)
        frame = synthetic_frame_id(case["scenario_id"], self.config)
        missing = assess_routing_readiness(candidate, evidence, self.policy, endpoint_frame_id=frame)
        self.assertFalse(missing.bounded_edge_routing_can_start)
        self.assertIn("JUNCTION_PLACEMENT_MISSING", {item.code for item in missing.findings})
        placement = propose_junction_placements(candidate, evidence, endpoint_frame_id=frame)
        ready = assess_routing_readiness(candidate, evidence, self.policy, endpoint_frame_id=frame, junction_placement=placement)
        self.assertTrue(ready.bounded_edge_routing_can_start)
        self.assertTrue(ready.shared_trunk_geometry_unresolved)
        self.assertFalse(ready.physical_feasibility_claimed)
        self.assertFalse(ready.collision_free_claimed)
        self.assertEqual(ready.authoritative_connection_ids, tuple(item.connection_id for item in problem.connections))

    def test_readiness_rejects_frame_and_policy_mismatch(self):
        case = generate_benchmark_cases(SCENARIOS, DEMANDS, SYSTEMS)[0]
        candidate = plan_topology_candidates(benchmark_case_to_routing_problem(case, self.definitions))[0]
        evidence = build_synthetic_spatial_evidence(case["scenario_id"], self.config, self.definitions)
        altered = deepcopy(self.config)
        altered["routing"]["clearance_m"] = 0.07
        result = assess_routing_readiness(candidate, evidence, benchmark_v1_policy(altered, self.definitions), endpoint_frame_id="wrong-frame")
        codes = {item.code for item in result.findings}
        self.assertFalse(result.bounded_edge_routing_can_start)
        self.assertTrue({"COORDINATE_FRAME_MISMATCH", "CLEARANCE_POLICY_DIGEST_MISMATCH"} <= codes)


if __name__ == "__main__":
    unittest.main()

import dataclasses
import json
from copy import deepcopy
from math import inf, nan
from pathlib import Path
import unittest

from src.clearance_policy import (
    BENCHMARK_POLICY_ID,
    ClearancePolicy,
    EnvelopeShape,
    LengthUnit,
    SameSystemClearanceStatus,
    SharedSegmentEvidence,
    benchmark_v1_compatibility_report,
    benchmark_v1_policy,
    legacy_obstacle_margin_m,
    legacy_reservation_margin_m,
)
from src.config import load_building_config
from src.demand import SYSTEM_ORDER, load_service_definitions, service_routing_margins


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "experiments" / "scenarios"
SYSTEMS = ROOT / "config" / "mep_systems.json"


class ClearancePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = load_service_definitions(SYSTEMS)
        manifest = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
        cls.config = load_building_config(SCENARIOS / manifest["scenarios"][0]["config_file"])
        cls.policy = benchmark_v1_policy(cls.config, cls.definitions)

    def test_immutable_canonical_and_deterministic(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.policy.policy_id = "other"  # type: ignore[misc]
        repeated = benchmark_v1_policy(self.config, self.definitions)
        self.assertEqual(self.policy, repeated)
        self.assertEqual(self.policy.to_canonical_json(), repeated.to_canonical_json())
        self.assertEqual(self.policy.digest_sha256, repeated.digest_sha256)
        self.assertEqual(self.policy.to_canonical_json(), json.dumps(json.loads(self.policy.to_canonical_json()), sort_keys=True, separators=(",", ":")))

    def test_supported_legacy_values_and_envelopes_are_faithful(self):
        self.assertEqual(self.policy.policy_id, BENCHMARK_POLICY_ID)
        self.assertIs(self.policy.length_unit, LengthUnit.METRE)
        self.assertIs(self.policy.envelope_for("hvac").shape, EnvelopeShape.RECTANGULAR)
        self.assertIs(self.policy.envelope_for("water").shape, EnvelopeShape.CIRCULAR)
        for system in SYSTEM_ORDER:
            expected = service_routing_margins(self.definitions[system], self.config["routing"]["clearance_m"])
            actual = legacy_obstacle_margin_m(self.policy, system, "STRUCTURAL_COLUMN")
            self.assertEqual(actual, (expected["required_planar_margin_m"], expected["required_vertical_margin_m"]))
        self.assertEqual(legacy_reservation_margin_m(self.policy, "hvac", "water"), (0.275, 0.2))

    def test_pair_lookup_is_symmetric_and_same_system_is_explicitly_undefined(self):
        self.assertEqual(self.policy.inter_system_clearance("water", "hvac"), self.policy.inter_system_clearance("hvac", "water"))
        rule = self.policy.same_system_clearance("water")
        self.assertIs(rule.status, SameSystemClearanceStatus.UNDEFINED)
        self.assertIsNone(rule.value_m)
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.policy.inter_system_clearance("water", "water")
        with self.assertRaisesRegex(ValueError, "No inter-system"):
            self.policy.inter_system_clearance("water", "unknown")

    def test_invalid_values_units_and_missing_configuration_are_rejected(self):
        bad = deepcopy(self.config)
        bad["routing"]["clearance_m"] = -0.01
        with self.assertRaisesRegex(ValueError, "non-negative"):
            benchmark_v1_policy(bad, self.definitions)
        for value in (nan, inf):
            bad = deepcopy(self.config)
            bad["routing"]["clearance_m"] = value
            with self.assertRaises(ValueError):
                benchmark_v1_policy(bad, self.definitions)
        with self.assertRaisesRegex(ValueError, "Missing configuration"):
            benchmark_v1_policy({}, self.definitions)
        with self.assertRaisesRegex(ValueError, "Only normalized metre"):
            dataclasses.replace(self.policy, length_unit="mm")

    def test_unknown_rules_do_not_fall_back_to_zero(self):
        with self.assertRaisesRegex(ValueError, "Unknown system"):
            self.policy.obstacle_clearance("unknown", "STRUCTURAL_COLUMN")
        with self.assertRaisesRegex(ValueError, "No obstacle"):
            self.policy.obstacle_clearance("water", "UNKNOWN_OBSTACLE")

    def test_shared_segment_evidence_is_not_a_collision_free_claim(self):
        self.assertTrue(self.policy.classify_shared_segment(SharedSegmentEvidence.INTENTIONALLY_SHARED_PHYSICAL_ELEMENT))
        self.assertFalse(self.policy.classify_shared_segment(SharedSegmentEvidence.INDEPENDENT_ELEMENTS))
        self.assertFalse(self.policy.classify_shared_segment(SharedSegmentEvidence.INSUFFICIENT_EVIDENCE))
        self.assertIn("topology ownership alone is insufficient", self.policy.shared_segment_requirement)

    def test_policy_does_not_mutate_authoritative_configuration(self):
        config, definitions = deepcopy(self.config), deepcopy(self.definitions)
        benchmark_v1_policy(config, definitions)
        self.assertEqual(config, self.config)
        self.assertEqual(definitions, self.definitions)

    def test_all_twelve_benchmark_cases_represent_current_configured_and_legacy_values(self):
        manifest = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
        policies = []
        for scenario in manifest["scenarios"]:
            config = load_building_config(SCENARIOS / scenario["config_file"])
            policy = benchmark_v1_policy(config, self.definitions)
            report = benchmark_v1_compatibility_report(policy)
            policies.extend([policy] * 3)  # each scenario has the three synthetic demand profiles
            self.assertEqual(report.configured_obstacle_clearance_m, config["routing"]["clearance_m"])
            self.assertEqual(report.legacy_inter_system_clearance_m, 0.05)
            self.assertFalse(report.obstacle_and_legacy_values_diverge)
        self.assertEqual(len(policies), 12)

    def test_divergence_is_reported_not_harmonized(self):
        config = deepcopy(self.config)
        config["routing"]["clearance_m"] = 0.07
        report = benchmark_v1_compatibility_report(benchmark_v1_policy(config, self.definitions))
        self.assertTrue(report.obstacle_and_legacy_values_diverge)
        self.assertEqual((report.configured_obstacle_clearance_m, report.legacy_inter_system_clearance_m), (0.07, 0.05))


if __name__ == "__main__":
    unittest.main()

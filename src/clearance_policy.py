"""Immutable, read-only clearance-policy contract for the controlled benchmark.

``BENCHMARK_V1`` describes existing behaviour without changing any routing,
reservation, clash, or validation consumer.  It intentionally records gaps in
that behaviour (notably same-system independent runs) as undefined.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from math import isfinite
from typing import Any, Mapping


BENCHMARK_POLICY_ID = "BENCHMARK_V1"
BENCHMARK_POLICY_VERSION = 1
LEGACY_INTER_SYSTEM_CLEARANCE_M = 0.05
OBSTACLE_CLASSES = (
    "PERIMETER_WALL", "SHAFT_WALL", "STRUCTURAL_COLUMN", "SLAB", "PLENUM_BOUNDARY",
)


class LengthUnit(str, Enum):
    METRE = "m"


class EnvelopeShape(str, Enum):
    CIRCULAR = "CIRCULAR"
    RECTANGULAR = "RECTANGULAR"


class PlanarExtentRule(str, Enum):
    ORIENTATION_AGNOSTIC_MAX = "ORIENTATION_AGNOSTIC_MAX"


class ValueProvenance(str, Enum):
    CONFIGURED = "CONFIGURED"
    LEGACY_HARDCODED = "LEGACY_HARDCODED"


class SameSystemClearanceStatus(str, Enum):
    UNDEFINED = "UNDEFINED"
    DEFINED = "DEFINED"


class SharedSegmentEvidence(str, Enum):
    INTENTIONALLY_SHARED_PHYSICAL_ELEMENT = "INTENTIONALLY_SHARED_PHYSICAL_ELEMENT"
    INDEPENDENT_ELEMENTS = "INDEPENDENT_ELEMENTS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _finite_positive(value: float, label: str, *, zero_allowed: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0 or (value == 0 and not zero_allowed):
        raise ValueError(f"{label} must be a finite {'non-negative' if zero_allowed else 'positive'} number of metres.")
    return float(value)


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty text.")
    return value


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


@dataclass(frozen=True)
class ServiceEnvelope:
    system: str
    shape: EnvelopeShape
    planar_extent_rule: PlanarExtentRule
    width_m: float | None = None
    height_m: float | None = None
    diameter_m: float | None = None
    provenance: ValueProvenance = ValueProvenance.CONFIGURED

    def __post_init__(self) -> None:
        _text(self.system, "Service system")
        if not isinstance(self.shape, EnvelopeShape) or not isinstance(self.planar_extent_rule, PlanarExtentRule):
            raise ValueError("Service envelope shape and planar extent rule must use policy enums.")
        if not isinstance(self.provenance, ValueProvenance):
            raise ValueError("Service envelope provenance must use a policy enum.")
        if self.shape is EnvelopeShape.CIRCULAR:
            _finite_positive(self.diameter_m, "Circular service diameter")  # type: ignore[arg-type]
            if self.width_m is not None or self.height_m is not None:
                raise ValueError("Circular service envelopes use diameter only.")
        else:
            _finite_positive(self.width_m, "Rectangular service width")  # type: ignore[arg-type]
            _finite_positive(self.height_m, "Rectangular service height")  # type: ignore[arg-type]
            if self.diameter_m is not None:
                raise ValueError("Rectangular service envelopes use width and height only.")

    @property
    def planar_half_extent_m(self) -> float:
        if self.shape is EnvelopeShape.CIRCULAR:
            return float(self.diameter_m) / 2
        return max(float(self.width_m), float(self.height_m)) / 2

    @property
    def vertical_half_extent_m(self) -> float:
        return float(self.diameter_m) / 2 if self.shape is EnvelopeShape.CIRCULAR else float(self.height_m) / 2

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "diameter_m": None if self.diameter_m is None else _round(self.diameter_m),
            "height_m": None if self.height_m is None else _round(self.height_m),
            "planar_extent_rule": self.planar_extent_rule.value,
            "provenance": self.provenance.value,
            "shape": self.shape.value,
            "system": self.system,
            "width_m": None if self.width_m is None else _round(self.width_m),
        }


@dataclass(frozen=True)
class ClearanceRule:
    value_m: float
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        _finite_positive(self.value_m, "Clearance", zero_allowed=True)
        if not isinstance(self.provenance, ValueProvenance):
            raise ValueError("Clearance provenance must use a policy enum.")

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"provenance": self.provenance.value, "value_m": _round(self.value_m)}


@dataclass(frozen=True)
class SameSystemRule:
    status: SameSystemClearanceStatus
    value_m: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SameSystemClearanceStatus):
            raise ValueError("Same-system clearance status must use a policy enum.")
        if self.status is SameSystemClearanceStatus.UNDEFINED:
            if self.value_m is not None:
                raise ValueError("Undefined same-system clearance cannot carry a numeric value.")
        else:
            _finite_positive(self.value_m, "Same-system clearance", zero_allowed=True)  # type: ignore[arg-type]

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "value_m": None if self.value_m is None else _round(self.value_m)}


@dataclass(frozen=True)
class ClearancePolicy:
    policy_id: str
    version: int
    length_unit: LengthUnit
    envelopes: tuple[ServiceEnvelope, ...]
    obstacle_rules: tuple[tuple[str, str, ClearanceRule], ...]
    inter_system_rules: tuple[tuple[str, str, ClearanceRule], ...]
    same_system_rules: tuple[tuple[str, SameSystemRule], ...]
    shared_segment_requirement: str = "Physical geometry evidence is required; topology ownership alone is insufficient."

    def __post_init__(self) -> None:
        _text(self.policy_id, "Policy identity")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Policy version must be a positive integer.")
        if self.length_unit is not LengthUnit.METRE:
            raise ValueError("Only normalized metre policies are supported.")
        systems = [item.system for item in self.envelopes]
        if not systems or len(systems) != len(set(systems)):
            raise ValueError("Policy must define one envelope for each unique system.")
        if tuple(sorted(systems)) != tuple(systems):
            raise ValueError("Policy envelopes must be deterministically sorted by system.")
        obstacle_keys = [(system, obstacle) for system, obstacle, _ in self.obstacle_rules]
        if len(obstacle_keys) != len(set(obstacle_keys)) or tuple(sorted(obstacle_keys)) != tuple(obstacle_keys):
            raise ValueError("Obstacle rules must be unique and deterministically sorted.")
        pairs = [(first, second) for first, second, _ in self.inter_system_rules]
        if any(first >= second for first, second in pairs) or len(pairs) != len(set(pairs)) or tuple(sorted(pairs)) != tuple(pairs):
            raise ValueError("Inter-system rules must use unique sorted system pairs.")
        same_systems = [system for system, _ in self.same_system_rules]
        if len(same_systems) != len(set(same_systems)) or tuple(sorted(same_systems)) != tuple(same_systems):
            raise ValueError("Same-system rules must be unique and deterministically sorted.")

    def envelope_for(self, system: str) -> ServiceEnvelope:
        for item in self.envelopes:
            if item.system == system:
                return item
        raise ValueError(f"Unknown system identifier: {system}")

    def obstacle_clearance(self, system: str, obstacle_class: str) -> ClearanceRule:
        self.envelope_for(system)
        for item_system, item_class, rule in self.obstacle_rules:
            if item_system == system and item_class == obstacle_class:
                return rule
        raise ValueError(f"No obstacle clearance rule for {system}/{obstacle_class}.")

    def inter_system_clearance(self, first: str, second: str) -> ClearanceRule:
        if first == second:
            raise ValueError("Inter-system clearance requires two distinct systems.")
        pair = tuple(sorted((first, second)))
        for left, right, rule in self.inter_system_rules:
            if (left, right) == pair:
                return rule
        raise ValueError(f"No inter-system clearance rule for {pair[0]}/{pair[1]}.")

    def same_system_clearance(self, system: str) -> SameSystemRule:
        self.envelope_for(system)
        for item_system, rule in self.same_system_rules:
            if item_system == system:
                return rule
        raise ValueError(f"No same-system clearance rule for {system}.")

    def classify_shared_segment(self, evidence: SharedSegmentEvidence) -> bool:
        """Return whether evidence establishes a deliberately shared element, never collision permission."""
        if not isinstance(evidence, SharedSegmentEvidence):
            raise ValueError("Shared-segment evidence must use a policy enum.")
        return evidence is SharedSegmentEvidence.INTENTIONALLY_SHARED_PHYSICAL_ELEMENT

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "envelopes": [item.to_canonical_dict() for item in self.envelopes],
            "inter_system_rules": [
                {"first_system": first, "rule": rule.to_canonical_dict(), "second_system": second}
                for first, second, rule in self.inter_system_rules
            ],
            "length_unit": self.length_unit.value,
            "obstacle_rules": [
                {"obstacle_class": obstacle, "rule": rule.to_canonical_dict(), "system": system}
                for system, obstacle, rule in self.obstacle_rules
            ],
            "policy_id": self.policy_id,
            "same_system_rules": [
                {"rule": rule.to_canonical_dict(), "system": system} for system, rule in self.same_system_rules
            ],
            "shared_segment_requirement": self.shared_segment_requirement,
            "version": self.version,
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def digest_sha256(self) -> str:
        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BenchmarkCompatibilityReport:
    configured_obstacle_clearance_m: float
    legacy_inter_system_clearance_m: float
    obstacle_and_legacy_values_diverge: bool
    same_system_independent_run_clearance: SameSystemClearanceStatus
    consumers_still_legacy: tuple[str, ...]

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "configured_obstacle_clearance_m": _round(self.configured_obstacle_clearance_m),
            "consumers_still_legacy": list(self.consumers_still_legacy),
            "legacy_inter_system_clearance_m": _round(self.legacy_inter_system_clearance_m),
            "obstacle_and_legacy_values_diverge": self.obstacle_and_legacy_values_diverge,
            "same_system_independent_run_clearance": self.same_system_independent_run_clearance.value,
        }


def _envelope_from_definition(system: str, definition: Mapping[str, Any]) -> ServiceEnvelope:
    envelope_type = definition.get("envelope_type")
    if envelope_type == "circular":
        return ServiceEnvelope(system, EnvelopeShape.CIRCULAR, PlanarExtentRule.ORIENTATION_AGNOSTIC_MAX,
                               diameter_m=definition.get("diameter_m"))
    if envelope_type == "rectangular":
        return ServiceEnvelope(system, EnvelopeShape.RECTANGULAR, PlanarExtentRule.ORIENTATION_AGNOSTIC_MAX,
                               width_m=definition.get("width_m"), height_m=definition.get("height_m"))
    raise ValueError(f"Unsupported envelope type for {system}: {envelope_type!r}.")


def benchmark_v1_policy(config: Mapping[str, Any], service_definitions: Mapping[str, Mapping[str, Any]]) -> ClearancePolicy:
    """Describe one scenario's legacy benchmark values without mutating inputs."""
    routing = config.get("routing")
    if not isinstance(routing, Mapping) or "clearance_m" not in routing:
        raise ValueError("Missing configuration: routing.clearance_m.")
    configured = _finite_positive(routing["clearance_m"], "routing.clearance_m", zero_allowed=True)
    if not service_definitions:
        raise ValueError("Missing configuration: service definitions.")
    envelopes = tuple(sorted((_envelope_from_definition(system, definition) for system, definition in service_definitions.items()), key=lambda item: item.system))
    systems = tuple(item.system for item in envelopes)
    obstacle_rules = tuple(sorted(
        (system, obstacle, ClearanceRule(configured, ValueProvenance.CONFIGURED))
        for system in systems for obstacle in OBSTACLE_CLASSES
    ))
    inter_system_rules = tuple(
        (first, second, ClearanceRule(LEGACY_INTER_SYSTEM_CLEARANCE_M, ValueProvenance.LEGACY_HARDCODED))
        for index, first in enumerate(systems) for second in systems[index + 1:]
    )
    same_system_rules = tuple((system, SameSystemRule(SameSystemClearanceStatus.UNDEFINED)) for system in systems)
    return ClearancePolicy(BENCHMARK_POLICY_ID, BENCHMARK_POLICY_VERSION, LengthUnit.METRE, envelopes,
                           obstacle_rules, inter_system_rules, same_system_rules)


def benchmark_v1_compatibility_report(policy: ClearancePolicy) -> BenchmarkCompatibilityReport:
    if (policy.policy_id, policy.version) != (BENCHMARK_POLICY_ID, BENCHMARK_POLICY_VERSION):
        raise ValueError("Compatibility report requires BENCHMARK_V1.")
    configured = {rule.value_m for _, _, rule in policy.obstacle_rules}
    legacy = {rule.value_m for _, _, rule in policy.inter_system_rules}
    if len(configured) != 1 or len(legacy) != 1:
        raise ValueError("BENCHMARK_V1 requires uniform configured obstacle and legacy inter-system values.")
    configured_value, legacy_value = next(iter(configured)), next(iter(legacy))
    return BenchmarkCompatibilityReport(
        configured_value, legacy_value, configured_value != legacy_value, SameSystemClearanceStatus.UNDEFINED,
        ("src/voxel.py", "src/demand.py", "src/sequential.py", "src/clash.py", "src/ifc_validation.py", "src/junction_placement.py"),
    )


def legacy_obstacle_margin_m(policy: ClearancePolicy, system: str, obstacle_class: str) -> tuple[float, float]:
    """Read-only adapter for Phase 3A's planar and vertical obstacle margins."""
    envelope, rule = policy.envelope_for(system), policy.obstacle_clearance(system, obstacle_class)
    return envelope.planar_half_extent_m + rule.value_m, envelope.vertical_half_extent_m + rule.value_m


def legacy_reservation_margin_m(policy: ClearancePolicy, prior_system: str, target_system: str) -> tuple[float, float]:
    """Read-only adapter for B1's pairwise reservation expansion."""
    prior, target = policy.envelope_for(prior_system), policy.envelope_for(target_system)
    rule = policy.inter_system_clearance(prior_system, target_system)
    return prior.planar_half_extent_m + target.planar_half_extent_m + rule.value_m, prior.vertical_half_extent_m + target.vertical_half_extent_m + rule.value_m

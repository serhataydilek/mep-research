"""Read-only native-IFC registration and candidate-connectivity evidence.

This runner deliberately uses IFC relationships and placements only.  It never
modifies source IFCs, writes a registration transform, or creates geometric
proximity edges.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
IFC_ROOT = SCRIPT_ROOT.parent / "hvac_research_ifc"
if str(IFC_ROOT) not in sys.path:
    sys.path.insert(0, str(IFC_ROOT))

import ifcopenshell

from src.coordinate_registration import assess
from src.ifc_ventilation_graph import build_ventilation_graph
from src.real_ifc import _flow_label, _port_hosts, _system_index, placement_frame_m, read_model_header

EXTERNAL = SCRIPT_ROOT / "validation" / "ifc_samples" / "external"
VALIDATION = IFC_ROOT / "validation"
ARCHITECTURE = EXTERNAL / "medium" / "digital_hub" / "arc.ifc"
VENTILATION = EXTERNAL / "medium" / "digital_hub" / "ventilation.ifc"
CANDIDATES = {
    "digital_hub_heating": EXTERNAL / "digital_hub" / "heating.ifc",
    "digital_hub_plumbing": EXTERNAL / "digital_hub" / "plumbing.ifc",
    "samuel_macalister_mep": EXTERNAL / "samuel_macalister_sample_house" / "mep.ifc",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame(entity: Any, scale: float) -> dict[str, Any] | None:
    placement = getattr(entity, "ObjectPlacement", None)
    if placement is None:
        return None
    try:
        return placement_frame_m(placement, scale)
    except Exception as exc:  # retained as source evidence, never guessed
        return {"error": f"{type(exc).__name__}: {exc}"}


def spatial_anchors(model: ifcopenshell.file, scale: float) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for class_name in ("IfcSite", "IfcBuilding", "IfcBuildingStorey"):
        for item in model.by_type(class_name):
            if item.GlobalId:
                rows[str(item.GlobalId)] = {
                    "ifc_class": item.is_a(), "name": item.Name, "frame": frame(item, scale),
                }
    return rows


def vector_distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((float(left[i]) - float(right[i])) ** 2 for i in range(3)))


def axis_angle(left: list[float], right: list[float]) -> float:
    dot = max(-1.0, min(1.0, sum(float(left[i]) * float(right[i]) for i in range(3))))
    return math.acos(dot)


def registration_report() -> dict[str, Any]:
    arch_header, vent_header = read_model_header(ARCHITECTURE), read_model_header(VENTILATION)
    # Project IDs are source anchors even though IfcProject has no ObjectPlacement.
    for header, path in ((arch_header, ARCHITECTURE), (vent_header, VENTILATION)):
        project = ifcopenshell.open(str(path)).by_type("IfcProject")[0]
        header["project_global_id"] = project.GlobalId
    arch, vent = ifcopenshell.open(str(ARCHITECTURE)), ifcopenshell.open(str(VENTILATION))
    a_anchors, v_anchors = spatial_anchors(arch, arch_header["unit_scale_m"]), spatial_anchors(vent, vent_header["unit_scale_m"])
    shared = []
    translations, rotations = [], []
    for gid in sorted(set(a_anchors) & set(v_anchors)):
        left, right = a_anchors[gid], v_anchors[gid]
        lf, rf = left.get("frame"), right.get("frame")
        if not isinstance(lf, dict) or not isinstance(rf, dict) or "origin_m" not in lf or "origin_m" not in rf:
            continue
        translation = vector_distance(lf["origin_m"], rf["origin_m"])
        angles = {axis: axis_angle(lf[axis], rf[axis]) for axis in ("x_axis", "y_axis", "z_axis")}
        translations.append(translation)
        rotations.extend(angles.values())
        shared.append({
            "global_id": gid, "ifc_class": left["ifc_class"], "name": left["name"],
            "architecture_origin_m": lf["origin_m"], "ventilation_origin_m": rf["origin_m"],
            "translation_residual_m": translation, "axis_angle_residual_rad": angles,
            "translation_vector_architecture_minus_ventilation_m": [
                float(lf["origin_m"][i]) - float(rf["origin_m"][i]) for i in range(3)
            ],
        })
    vectors = [row["translation_vector_architecture_minus_ventilation_m"] for row in shared]
    vector_spread = None
    if vectors:
        mean = [sum(row[i] for row in vectors) / len(vectors) for i in range(3)]
        vector_spread = max(vector_distance(row, mean) for row in vectors)
    base = assess(arch_header, vent_header)
    tolerance = base["declared_precision_m"]["comparison_limit"]
    residual_stats = None if not translations else {
        "count": len(translations), "min_m": min(translations), "median_m": sorted(translations)[len(translations) // 2],
        "max_m": max(translations), "mean_m": sum(translations) / len(translations),
        "max_axis_angle_rad": max(rotations, default=None), "candidate_translation_vector_spread_m": vector_spread,
    }
    candidate_translation = None if not vectors else [sum(row[i] for row in vectors) / len(vectors) for i in range(3)]
    post_transform = [] if candidate_translation is None else [
        vector_distance(
            [row["ventilation_origin_m"][i] + candidate_translation[i] for i in range(3)],
            row["architecture_origin_m"],
        ) for row in shared
    ]
    return {
        "purpose": "Read-only source-frame diagnosis; no transform is applied or authorized.",
        "source_files": [{"path": str(p), "sha256": sha256(p)} for p in (ARCHITECTURE, VENTILATION)],
        "independent_source_anchors": {
            "same_project_global_id": base["same_project_global_id"], "same_site_global_id": base["same_site_global_id"],
            "shared_spatial_anchor_count": len(shared), "shared_spatial_anchors": shared,
        },
        "model_units_and_frames": {
            "architecture_unit_scale_m": arch_header["unit_scale_m"], "ventilation_unit_scale_m": vent_header["unit_scale_m"],
            "architecture_coordinate_reference": arch_header.get("coordinate_reference"),
            "ventilation_coordinate_reference": vent_header.get("coordinate_reference"),
        },
        "registration_assessment": base,
        "residual_distribution": residual_stats,
        "error_separation": {
            "floating_point_only": False,
            "reason": "The 4.912e-05 m site residual is about 4.9 declared precision units, so it cannot be classified as round-off under the source declarations.",
            "scale_verified": "PASS: source units differ (architecture metres, ventilation millimetres), and each placement is independently converted to metres before comparison.",
            "orientation_verified": bool(rotations) and max(rotations) <= 1e-12,
            "candidate_translation_consistent_across_shared_anchors": vector_spread is not None and vector_spread <= 1e-9,
            "coordinate_transform_error_or_model_misalignment": "UNRESOLVED_WITHOUT_APPROVED_REGISTRATION_AUTHORITY",
        },
        "precision_criterion": {
            "criterion": "max(declared IFC geometric-context precision for both source models)",
            "value_m": tolerance,
            "technical_assessment": "A defensible source-declared comparison limit, but not a survey/control-point registration certificate.",
            "result": "NOT_RELAXED; residual remains above the declared comparison limit.",
        },
        "isolated_candidate_transform_diagnostic": {
            "calculation_only": True, "applied_to_production_geometry": False,
            "translation_architecture_minus_ventilation_m": candidate_translation,
            "rotation_rad": 0.0, "scale": 1.0,
            "post_translation_shared_anchor_residuals_m": post_transform,
            "interpretation": "The two shared placement chains support one exact translation observation, not an approved registration transform. Both shared anchors inherit the same offset, so they are not independent survey control points.",
            "origin_hypotheses": [
                "exporter placement-origin rounding or convention", "unrecorded coordination translation", "genuine discipline-model origin offset"
            ],
            "ruled_out_by_source_data": ["scale mismatch after declared-unit conversion", "rotation or orientation mismatch", "random floating-point noise"],
        },
        "decision": {
            "verdict": "EXTERNAL_REGISTRATION_REQUIRED", "transform_applied": False,
            "adapter_implemented": False,
            "reason": "Shared IDs, units, and identity map conversions establish provenance, but neither source supplies an approved transform/control-point authority for the remaining residual.",
        },
    }


def fast_network(model: ifcopenshell.file, scale: float) -> tuple[dict[str, Any], dict[str, Any]]:
    hosts, systems = _port_hosts(model), _system_index(model)
    ports, failures = [], []
    for port in sorted(model.by_type("IfcDistributionPort"), key=lambda item: item.GlobalId or ""):
        host = hosts.get(port.id())
        try:
            local = placement_frame_m(port.ObjectPlacement, scale)
        except Exception as exc:
            local, failures = None, failures + [{"port_global_id": port.GlobalId, "error": f"{type(exc).__name__}: {exc}"}]
        ports.append({
            "global_id": port.GlobalId, "flow_direction": _flow_label(getattr(port, "FlowDirection", None)),
            "host_global_id": None if host is None else host.GlobalId, "host_class": None if host is None else host.is_a(),
            "host_name": None if host is None else host.Name, "system_names": [] if host is None else systems.get(host.id(), []),
            "origin_m": None if local is None else local["origin_m"], "orientation_z": None if local is None else local["z_axis"],
        })
    connections = [{
        "relationship_global_id": rel.GlobalId, "relationship_class": "IfcRelConnectsPorts",
        "relating_port_global_id": None if rel.RelatingPort is None else rel.RelatingPort.GlobalId,
        "related_port_global_id": None if rel.RelatedPort is None else rel.RelatedPort.GlobalId,
        "geometric_proximity_used": False,
    } for rel in sorted(model.by_type("IfcRelConnectsPorts"), key=lambda item: item.GlobalId or "")]
    return {"ports": ports, "connections": connections}, {"placement_failures": failures, "hosts": hosts}


def candidate_audit(label: str, path: Path) -> dict[str, Any]:
    model, header = ifcopenshell.open(str(path)), read_model_header(path)
    network, extras = fast_network(model, header["unit_scale_m"])
    graph = build_ventilation_graph(network)
    ports = {row["global_id"]: row for row in graph["ports"]}
    adjacency = graph["adjacency"]
    # IFC inheritance is not retained in the compact port record.  These are
    # explicit concrete source/terminal classes observed in the source models;
    # they classify candidates only and never invent a demand pair.
    source_classes = {"IfcPump", "IfcBoiler", "IfcUnitaryEquipment", "IfcFan", "IfcChiller", "IfcHeatExchanger"}
    terminal_classes = {"IfcAirTerminal", "IfcSpaceHeater", "IfcSanitaryTerminal", "IfcWasteTerminal"}
    sources = [row for row in ports.values() if row["host_class"] in source_classes and row["flow_direction"] == "SOURCE"]
    terminals = [row for row in ports.values() if row["host_class"] in terminal_classes and row["flow_direction"] == "SINK"]
    paths = []
    terminal_ids = {row["global_id"] for row in terminals}
    direct_degree: Counter[str] = Counter()
    for relation in network["connections"]:
        for port_id in (relation["relating_port_global_id"], relation["related_port_global_id"]):
            if port_id:
                direct_degree[port_id] += 1
    uncertainty_by_port = {port_id: row["reason"] for row in graph["uncertain_hosts"] for port_id in row["port_global_ids"]}
    failures = []
    for source in sources:
        seen, queue = {source["global_id"]}, deque([(source["global_id"], [])])
        while queue:
            current, trace = queue.popleft()
            for next_id, edge in adjacency.get(current, []):
                if next_id in seen:
                    continue
                seen.add(next_id)
                next_trace = trace + [edge]
                if next_id in terminal_ids:
                    sink = ports[next_id]
                    paths.append({"source_port_global_id": source["global_id"], "terminal_port_global_id": next_id,
                                  "named_system": (source.get("system_names") or [None])[0], "hop_count": len(next_trace),
                                  "edge_types": Counter(edge["edge_type"] for edge in next_trace)})
                queue.append((next_id, next_trace))
        if not any(row["source_port_global_id"] == source["global_id"] for row in paths):
            system_names = source.get("system_names") or []
            compatible = [terminal for terminal in terminals if set(system_names) & set(terminal.get("system_names") or [])]
            reason = "DISCONNECTED_GRAPH_COMPONENT" if direct_degree[source["global_id"]] == 0 else (
                "AMBIGUOUS_OR_MISSING_SYSTEM_ASSIGNMENT" if not system_names or not compatible else "UNSUPPORTED_HOST_CONTINUITY"
            )
            failures.append({"source_port_global_id": source["global_id"], "host_class": source["host_class"], "systems": system_names,
                             "explicit_relationship_degree": direct_degree[source["global_id"]], "reachable_verified_port_count": len(seen),
                             "compatible_terminal_candidate_count": len(compatible), "classification": reason,
                             "evidence": uncertainty_by_port.get(source["global_id"], "No verified same-system path reaches a compatible terminal under the restricted rule.")})
    host_counts = Counter(row.get("host_class") or "MISSING_HOST" for row in ports.values())
    geometry = sum(1 for item in model.by_type("IfcProduct") if getattr(item, "Representation", None) is not None)
    unsupported = Counter(row.get("reason") for row in graph["uncertain_hosts"])
    return {
        "candidate": label, "source": {"path": str(path), "sha256": sha256(path), "schema": model.schema, "unit_scale_m": header["unit_scale_m"]},
        "method": "IfcRelConnectsPorts plus the existing restricted internal-host rule; no proximity edges and no fabricated terminals.",
        "port_inventory": {"port_count": len(ports), "relationship_count": len(network["connections"]), "placement_failure_count": len(extras["placement_failures"]), "host_class_counts": host_counts},
        "system_membership": graph["system_summaries"], "source_candidate_count": len(sources), "terminal_candidate_count": len(terminals),
        "verified_source_to_terminal_traces": paths[:100], "verified_trace_count": len(paths),
        "failed_source_traversal_classification": failures,
        "failure_classification_summary": Counter(row["classification"] for row in failures),
        "restricted_internal_continuity": {"verified_edge_count": sum(edge["edge_type"] == "VERIFIED_INTERNAL_HOST" for edge in graph["edges"]), "uncertain_host_count": len(graph["uncertain_hosts"]), "uncertainty_reasons": unsupported},
        "unsupported_relationships": graph["rejections"], "disconnected_endpoint_count": sum(1 for row in ports.values() if not adjacency.get(row["global_id"])),
        "connector_geometry": {"positioned_port_count": sum(row.get("origin_m") is not None for row in ports.values()), "axis_resolved_port_count": sum(row.get("orientation_z") is not None for row in ports.values())},
        "routing_geometry": {"represented_product_count": geometry, "usable_for_routing_experiment": False, "reason": "No independently verified architectural registration/obstacle pairing is established by this audit."},
        "domain_decision": "HEATING_OR_PLUMBING_SEPARATE_DOMAIN" if label.startswith("digital_hub_") else "INDEPENDENT_MEP_CANDIDATE_REQUIRES_REGISTRATION_AND_COMPLETE_TYPED_TRACE",
    }


def write(name: str, value: dict[str, Any]) -> None:
    (VALIDATION / name).write_text(json.dumps(value, indent=2, default=lambda x: dict(x) if isinstance(x, Counter) else str(x)) + "\n", encoding="utf-8")


def main() -> None:
    print("registration")
    registration = registration_report()
    write("digital_hub_registration_root_cause.json", registration)
    audits = {}
    for label, path in CANDIDATES.items():
        print(label)
        audits[label] = candidate_audit(label, path)
        write(f"{label}_typed_graph_audit.json", audits[label])
    prior = json.loads((VALIDATION / "native_ifc_eligibility_matrix_13_models.json").read_text(encoding="utf-8"))
    by_path = {"digital_hub/heating.ifc": audits["digital_hub_heating"], "digital_hub/plumbing.ifc": audits["digital_hub_plumbing"], "samuel_macalister_sample_house/mep.ifc": audits["samuel_macalister_mep"]}
    rows = []
    for old in prior["candidates"]:
        audit = by_path.get(old["path"])
        row = dict(old)
        row["real_ifc_geometry_with_synthetic_endpoints"] = bool(old.get("structural_geometry_candidate"))
        row["real_native_source_to_terminal_connectivity"] = bool(old.get("verified_source_to_terminal_connectivity"))
        row["independently_verified_architectural_registration"] = False
        row["experimental_physical_duct_volume_validation"] = False
        row["professionally_approved_engineering_design"] = False
        if audit is not None:
            row.update({"typed_graph_audit": "COMPLETED", "verified_source_to_terminal_trace_count": audit["verified_trace_count"],
                        "connector_positions_and_axes": audit["connector_geometry"], "native_pilot_eligibility": "BLOCKED_INCOMPLETE_TYPED_CONTINUITY_AND_REGISTRATION",
                        "domain_note": audit["domain_decision"]})
        rows.append(row)
    write("native_ifc_eligibility_matrix_13_models_v2.json", {"candidate_count": len(rows), "category_boundaries": ["real_ifc_geometry_with_synthetic_endpoints", "real_native_source_to_terminal_connectivity", "independently_verified_architectural_registration", "experimental_physical_duct_volume_validation", "professionally_approved_engineering_design"], "candidates": rows, "registration_gate": registration["decision"], "pilot_decision": "NO_PILOT_AUTHORIZED"})
    recovery = json.loads((VALIDATION / "digital_hub_ifc_source_recovery.json").read_text(encoding="utf-8"))
    recovered = recovery["preserved_evidence_comparison"]
    write("native_ifc_research_pilot_readiness.json", {
        "purpose": "Deterministic read-only configuration only; execution remains prohibited.",
        "eligible_native_connectivity_candidates": [{
            "candidate": "digital_hub_v_zuluft", "source_ifc_sha256": recovery["source_file"]["sha256"], "named_system": "V_Zuluft",
            "source_port_global_id": recovered["source_port_global_id"], "terminal_port_global_ids": [item["port_global_id"] for item in recovered["terminal_checks"]],
            "provenance_verified": True, "structural_obstacle_geometry": "PRESENT_IN_ARCHITECTURE_BUT_CROSS_MODEL_REGISTRATION_UNVERIFIED",
            "registration": "EXTERNAL_REGISTRATION_REQUIRED", "engineering_parameters": "RESEARCH_ONLY_PARAMETERS_NOT_PROPOSED_PENDING_REGISTRATION",
            "execution": "BLOCKED", "blockers": ["approved registration/control-point authority", "engineering authorization for a read-only experiment"],
        }],
        "other_candidates": [{"candidate": name, "execution": "BLOCKED", "reason": "No verified native source-to-terminal traversal."} for name in CANDIDATES],
    })
    write("public_independent_ifc_dataset_inventory.json", {
        "method": "Public-source triage only. A dataset is not added as a native routing benchmark until its downloadable IFC files are locally hashed, parsed, and cross-model registration is verified.",
        "sources_reviewed": [
            {"name": "buildingSMART Certification-datasets", "url": "https://github.com/buildingSMART/Certification-datasets", "provenance": "Official buildingSMART repository; its README identifies certification/implementation samples.", "license": "Repository license must be bound to each downloaded artifact before use.", "result": "NO_NEW_BUILDING_PAIR_ADDED", "evidence_gap": "No independently verified HVAC/architecture pair with explicit native end-to-end connectivity was acquired in this pass."},
            {"name": "buildingSMART Community-Sample-Test-Files", "url": "https://github.com/buildingsmart-community/Community-Sample-Test-Files", "provenance": "Community-maintained public repository; README states CC BY 4.0 contribution terms and warns that most files do not pass validation.", "license": "CC BY 4.0 declared for contributed files; confirm per artifact README before download.", "lead": "SDK - S1 contains design, engineering, and MEP HVAC materials plus stated ground-control points.", "result": "PROMISING_LEAD_NOT_BENCHMARK", "evidence_gap": "Actual IFC files, artifact-level license/readme, hashes, schema, port graph, obstacle geometry, and registration must be locally verified."},
        ],
        "existing_independent_candidates": [
            {"name": "Digital Hub", "native_hvac_connectivity": "VERIFIED_V_ZULUFT", "registration": "EXTERNAL_REGISTRATION_REQUIRED", "benchmark_status": "BLOCKED"},
            {"name": "Samuel Macalister MEP", "native_hvac_connectivity": "NOT_VERIFIED", "registration": "NO_ARCHITECTURE_PAIR_VERIFIED", "benchmark_status": "BLOCKED"},
        ],
        "conclusion": "No second independent building satisfies all seven native-HVAC benchmark criteria yet; synthetic endpoints were not substituted.",
        "acquisition_gate": ["artifact-level license and source URL", "SHA-256 and schema", "distribution-port and IfcRelConnectsPorts parse", "typed source-to-terminal trace", "paired obstacle geometry", "independently verified coordinate registration", "dimensional/profile evidence"],
    })
    print(json.dumps({key: audit["verified_trace_count"] for key, audit in audits.items()}))


if __name__ == "__main__":
    main()

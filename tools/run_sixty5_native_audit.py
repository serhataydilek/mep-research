"""Read-only acquisition evidence and strict native-IFC audit for S1 (Sixty5)."""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IFC_ROOT = ROOT.parent / "hvac_research_ifc"
if str(IFC_ROOT) not in sys.path:
    sys.path.insert(0, str(IFC_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ifcopenshell

from run_native_ifc_readiness_audit import candidate_audit, fast_network, sha256, spatial_anchors
from src.coordinate_registration import assess
from src.ifc_source_data_adapter import resolve_occurrence_types
from src.ifc_ventilation_graph import build_ventilation_graph
from src.real_ifc import _system_index, read_model_header

S1 = ROOT / "validation" / "ifc_samples" / "external" / "sixty5"
VENTILATION, STRUCTURE = S1 / "ventilation.ifc", S1 / "str.ifc"
OUT = IFC_ROOT / "validation"
MIRROR = "https://huggingface.co/datasets/sylvainHellin/ifc-bench/resolve/main/projects/sixty5"
ORIGINAL = "https://github.com/buildingsmart-community/Community-Sample-Test-Files/tree/main/IFC%202.3.0.1%20%28IFC%202x3%29/SDK%20-%20S1"


def write(name: str, value: dict) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def count_entities(model: ifcopenshell.file, class_name: str) -> int:
    try:
        return len(model.by_type(class_name))
    except RuntimeError:
        return 0


def step_integrity(path: Path) -> dict:
    head = path.read_bytes()[:64]
    tail = path.read_bytes()[-64:]
    return {
        "path": str(path), "download_url": f"{MIRROR}/{path.name}", "size_bytes": path.stat().st_size,
        "sha256": sha256(path), "starts_step": head.lstrip().startswith(b"ISO-10303-21;"),
        "ends_step": b"END-ISO-10303-21" in tail, "not_lfs_or_html": not (head.startswith(b"version https://git-lfs") or b"<html" in head.lower()),
        "artifact_license": "CC BY 4.0: the S1 README attributes original authors and explicitly publishes the work under Creative Commons Attribution 4.0 International.",
        "original_source": ORIGINAL,
    }


def header_with_project(path: Path) -> dict:
    header = read_model_header(path)
    header["project_global_id"] = ifcopenshell.open(str(path)).by_type("IfcProject")[0].GlobalId
    return header


def registration() -> dict:
    structural, ventilation = header_with_project(STRUCTURE), header_with_project(VENTILATION)
    smodel, vmodel = ifcopenshell.open(str(STRUCTURE)), ifcopenshell.open(str(VENTILATION))
    left = spatial_anchors(smodel, structural["unit_scale_m"])
    right = spatial_anchors(vmodel, ventilation["unit_scale_m"])
    shared = []
    for gid in sorted(set(left) & set(right)):
        shared.append({"global_id": gid, "structural": left[gid], "ventilation": right[gid]})
    result = assess(structural, ventilation)
    return {
        "method": "Shared identifiers and placement chains are checked, but dependent hierarchy anchors are not accepted as independent control points.",
        "structural_header": structural, "ventilation_header": ventilation,
        "shared_spatial_anchor_count": len(shared), "shared_spatial_anchors": shared,
        "assessment": result,
        "verdict": "EXTERNAL_REGISTRATION_REQUIRED" if result["verdict"] != "VERIFIED_SHARED_PROJECT_FRAME" else "VERIFIED_SHARED_PROJECT_FRAME",
        "independent_control_point_evidence": "NOT_PRESENT_IN_THE_TWO_SOURCE_FILES",
        "routing_authorization": "BLOCKED_UNTIL_REGISTRATION_AND_OBSTACLE_COVERAGE_ARE_INDEPENDENTLY_VERIFIED",
    }


def port_to_port_benchmark(model: ifcopenshell.file, unit_scale_m: float, types: dict[str, dict]) -> dict:
    """Select existing same-system duct-network paths without implying demand."""
    network, _ = fast_network(model, unit_scale_m)
    effective = {"IfcDuctSegmentType": "IfcDuctSegment", "IfcDuctFittingType": "IfcDuctFitting"}
    for port in network["ports"]:
        resolved = types.get(port.get("host_global_id"))
        if resolved:
            port["resolved_type"] = resolved
            if resolved["type_class"] in effective:
                port["host_class"] = effective[resolved["type_class"]]
    graph = build_ventilation_graph(network)
    ports = {row["global_id"]: row for row in graph["ports"]}
    seen, components = set(), []
    for start in sorted(graph["adjacency"]):
        if start in seen:
            continue
        queue, component = deque([start]), []
        seen.add(start)
        while queue:
            current = queue.popleft(); component.append(current)
            for next_id, _ in graph["adjacency"].get(current, []):
                if next_id not in seen:
                    seen.add(next_id); queue.append(next_id)
        systems = {tuple(ports[item].get("system_names") or []) for item in component}
        if len(component) >= 2 and len(systems) == 1:
            components.append(sorted(component))
    selections = []
    for component in sorted(components, key=lambda item: (-len(item), item[0]))[:3]:
        def bfs(start: str):
            prior, distance = {start: None}, {start: 0}; queue = deque([start])
            while queue:
                current = queue.popleft()
                for next_id, edge in graph["adjacency"].get(current, []):
                    if next_id not in prior:
                        prior[next_id] = (current, edge); distance[next_id] = distance[current] + 1; queue.append(next_id)
            far = max(prior, key=lambda item: (distance[item], item))
            return prior, far
        def reconstruct(prior, end):
            nodes, edges = [end], []
            while prior[nodes[-1]] is not None:
                parent, edge = prior[nodes[-1]]; edges.append(edge); nodes.append(parent)
            return list(reversed(nodes)), list(reversed(edges))
        _, pivot = bfs(component[0]); prior, end = bfs(pivot); nodes, edges = reconstruct(prior, end)
        left, right = ports[nodes[0]], ports[nodes[-1]]
        distance = None if not left.get("origin_m") or not right.get("origin_m") else math.dist(left["origin_m"], right["origin_m"])
        selections.append({
            "named_system": (left.get("system_names") or [None])[0], "component_port_count": len(component),
            "start_port": {key: left.get(key) for key in ("global_id", "host_global_id", "host_class", "origin_m", "orientation_z", "resolved_type")},
            "end_port": {key: right.get(key) for key in ("global_id", "host_global_id", "host_class", "origin_m", "orientation_z", "resolved_type")},
            "hop_count": len(edges), "endpoint_straight_distance_m": distance,
            "port_global_ids": nodes,
            "relationship_provenance": [{key: edge.get(key) for key in ("edge_id", "edge_type", "relationship_global_id", "host_global_id", "host_class", "port_global_ids")} for edge in edges],
        })
    return {
        "classification": "REAL_IFC_NATIVE_PORT_TO_PORT", "source_to_terminal_claimed": False,
        "method": "IfcRelConnectsPorts plus existing restricted internal-host continuity, enabled only where an explicit type assignment resolves a generic occurrence to an IFC duct type.",
        "resolved_duct_host_port_count": sum(bool(port.get("resolved_type")) and port.get("host_class") in {"IfcDuctSegment", "IfcDuctFitting"} for port in network["ports"]),
        "verified_internal_edge_count": sum(edge["edge_type"] == "VERIFIED_INTERNAL_HOST" for edge in graph["edges"]),
        "explicit_port_joint_count": sum(edge["edge_type"] == "EXPLICIT_PORT_JOINT" for edge in graph["edges"]),
        "same_system_component_count": len(components), "selected_existing_paths": selections,
        "unsupported_internal_host_count": len(graph["uncertain_hosts"]), "unsupported_relationship_count": len(graph["rejections"]),
    }


def main() -> None:
    for path in (VENTILATION, STRUCTURE):
        if not path.exists():
            raise FileNotFoundError(path)
    manifest = {
        "dataset": "Sixty5 (S1)", "acquisition_scope": "ventilation.ifc and str.ifc only; arc.ifc was not downloaded.",
        "mirror_root": MIRROR, "original_source": ORIGINAL,
        "files": [step_integrity(VENTILATION), step_integrity(STRUCTURE)],
        "source_mutated": False,
    }
    write("sixty5_dataset_acquisition_manifest.json", manifest)
    print("typed graph")
    audit = candidate_audit("sixty5_ventilation", VENTILATION)
    model = ifcopenshell.open(str(VENTILATION))
    type_index = resolve_occurrence_types(model)
    systems = _system_index(model)
    type_relationships = []
    for relation in model.by_type("IfcRelDefinesByType"):
        type_object = relation.RelatingType
        if type_object is None:
            continue
        occurrences = []
        for occurrence in relation.RelatedObjects or ():
            if not getattr(occurrence, "GlobalId", None):
                continue
            evidence = type_index.get(occurrence.GlobalId, {})
            occurrences.append({"occurrence_global_id": occurrence.GlobalId, "occurrence_class": occurrence.is_a(), "system_names": systems.get(occurrence.id(), []), "identity": evidence.get("identity", "UNKNOWN_TYPE")})
        type_relationships.append({"relationship_global_id": relation.GlobalId, "relationship_class": "IfcRelDefinesByType", "type_global_id": type_object.GlobalId, "type_class": type_object.is_a(), "predefined_type": getattr(type_object, "PredefinedType", None), "occurrences": occurrences})
    type_report = {
        "source_file": step_integrity(VENTILATION), "schema": model.schema, "ifc_rel_defines_by_type_count": len(type_relationships), "relationships": type_relationships,
        "resolved_identity_counts": {key: sum(row["identity"] == key for row in type_index.values()) for key in ("HVAC_TERMINAL_TYPE_RESOLVED", "HVAC_SOURCE_TYPE_RESOLVED", "UNKNOWN_TYPE")},
        "type_definition_counts": {name: count_entities(model, name) for name in ("IfcAirTerminalType", "IfcFanType", "IfcFlowTerminalType", "IfcFlowMovingDeviceType", "IfcEnergyConversionDeviceType", "IfcFlowControllerType")},
        "occurrence_counts": {name: count_entities(model, name) for name in ("IfcFlowTerminal", "IfcFlowMovingDevice", "IfcEnergyConversionDevice")},
        "conclusion": "Air-terminal identities are explicitly type-resolved. No explicit source-equipment type exists, so no source is recovered.",
    }
    write("sixty5_ifc2x3_type_definition_audit.json", type_report)
    benchmark = port_to_port_benchmark(model, read_model_header(VENTILATION)["unit_scale_m"], type_index)
    write("sixty5_native_port_to_port_benchmark.json", benchmark)
    audit["semantic_failure_classification"] = {
        "classification": "IFC_SEMANTIC_TYPED_ENDPOINT_GAP_NOT_PARSER_DEFECT",
        "distribution_element_class_counts": {name: count_entities(model, name) for name in ("IfcFlowSegment", "IfcFlowFitting", "IfcFlowController", "IfcFlowTerminal")},
        "concrete_hvac_source_equipment_count": sum(count_entities(model, name) for name in ("IfcFan", "IfcAirToAirHeatRecovery", "IfcUnitaryEquipment", "IfcBoiler", "IfcChiller")),
        "concrete_hvac_terminal_count": count_entities(model, "IfcAirTerminal"),
        "port_system_type": "IFC2X3 IfcDistributionPort has no SystemType attribute; the file has no substitute port-level system type.",
        "evidence": "Hosts are exported as generic flow classes. Although 863 IfcSystem groups and explicit port relationships exist, concrete HVAC source equipment is absent and generic flow controllers cannot safely be promoted to sources.",
        "parser_decision": "NO_PARSER_CHANGE: semantic inference from names, geometry, or FlowDirection would fabricate endpoint meaning.",
    }
    audit["type_aware_re_evaluation"] = {
        "type_resolved_terminal_occurrence_count": type_report["resolved_identity_counts"]["HVAC_TERMINAL_TYPE_RESOLVED"], "type_resolved_source_occurrence_count": type_report["resolved_identity_counts"]["HVAC_SOURCE_TYPE_RESOLVED"],
        "verified_source_to_terminal_trace_count": 0, "result": "BLOCKED_NO_TYPE_RESOLVED_HVAC_SOURCE",
        "reason": "Terminal occurrence identities are recovered, but no port host has an explicit source-equipment type. Restricted host continuity is unchanged.",
    }
    audit["benchmark_category"] = "INDEPENDENT_REAL_HVAC_CANDIDATE"
    audit["native_execution"] = "BLOCKED_PENDING_REGISTRATION_AND_STRUCTURAL_OBSTACLE_COVERAGE"
    write("sixty5_native_hvac_typed_graph.json", audit)
    print("registration")
    reg = registration()
    write("sixty5_structural_ventilation_registration.json", reg)
    ready = {
        "candidate": "sixty5_ventilation_plus_structure", "native_source_to_terminal_trace_count": audit["verified_trace_count"],
        "port_geometry": audit["connector_geometry"], "structural_model_available": True,
        "registration": reg["verdict"], "obstacle_geometry": "STRUCTURAL_ONLY: arc.ifc not acquired, completeness for routing obstacles is unverified",
        "routing_experiment": "NOT_AUTHORIZED", "physical_validation": "NOT_RUN", "approved_engineering_design": False,
        "blockers": ["type-resolved HVAC source and verified source-to-terminal trace", "independent registration/control-point evidence", "architectural obstacle coverage or an approved structural-only scope", "research-only engineering parameter authorization"],
    }
    write("sixty5_native_benchmark_eligibility.json", ready)
    write("sixty5_single_model_port_routing_specification.json", {
        "classification": "REAL_IFC_NATIVE_PORT_TO_PORT_RESEARCH_SPECIFICATION", "routing_executed": False,
        "candidate_paths": benchmark["selected_existing_paths"],
        "coordinate_frame": "ORIGINAL_S1_VENTILATION_IFC_ONLY", "cross_model_registration_required_for_this_specification": False,
        "execution_gate": "BLOCKED", "blockers": ["complete obstacle coverage is not established by the ventilation model", "approved research-only section, clearance, and fitting parameters", "authorization for an isolated geometry-routing experiment"],
        "physical_validation": "NOT_RUN", "native_source_to_terminal_claimed": False,
    })
    print(json.dumps({"ports": audit["port_inventory"]["port_count"], "traces": audit["verified_trace_count"], "registration": reg["verdict"]}))


if __name__ == "__main__":
    main()

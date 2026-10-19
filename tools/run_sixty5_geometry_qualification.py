"""Read-only geometry qualification for the three S1 native port-path cases.

This tool intentionally does not create a route, change IFC data, infer a duct
size, or treat the existing duct bodies as collision obstacles.  It only
records what the original ventilation IFC represents for the previously
selected, relationship-provenanced paths.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT.parent / "hvac_research_ifc"
# The runner imported below is in this repository, while its ``src`` package is
# deliberately supplied by the companion research checkout.
sys.path[:0] = [str(COMPANION), str(ROOT)]

import ifcopenshell  # noqa: E402

from run_native_ifc_readiness_audit import fast_network, sha256  # noqa: E402
from src.ifc_mesh_geometry import boundary_edge_count  # noqa: E402
from src.ifc_source_data_adapter import resolve_occurrence_types  # noqa: E402
from src.real_ifc import _element_profiles, extract_world_meshes, placement_frame_m, read_model_header  # noqa: E402


VENTILATION = ROOT / "validation" / "ifc_samples" / "external" / "sixty5" / "ventilation.ifc"
BENCHMARK = COMPANION / "validation" / "sixty5_native_port_to_port_benchmark.json"
OUT = COMPANION / "validation"


def write(name: str, payload: dict) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def distance(left, right):
    if left is None or right is None:
        return None
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def port_hosts(model):
    hosts = {}
    for rel in model.by_type("IfcRelNests"):
        parent = rel.RelatingObject
        if parent is None or parent.is_a("IfcDistributionPort"):
            continue
        for child in rel.RelatedObjects or ():
            if child is not None and child.is_a("IfcDistributionPort"):
                hosts[child.id()] = parent
    for rel in model.by_type("IfcRelConnectsPortToElement"):
        if rel.RelatingPort is not None and rel.RelatedElement is not None:
            hosts.setdefault(rel.RelatingPort.id(), rel.RelatedElement)
    return hosts


def effective_class(host, types):
    resolved = types.get(host.GlobalId)
    type_class = None if resolved is None else resolved["type_class"]
    mapping = {"IfcDuctSegmentType": "IfcDuctSegment", "IfcDuctFittingType": "IfcDuctFitting"}
    return mapping.get(type_class, host.is_a()), resolved


def mesh_summary(record):
    positions, indices = record["positions"], record["indices"]
    coords = list(zip(positions[0::3], positions[1::3], positions[2::3]))
    return {
        "geometry_status": "EXACT_TESSELLATED_IFC_BODY",
        "vertex_count": record["vertex_count"],
        "triangle_count": record["triangle_count"],
        "closed_shell": boundary_edge_count(indices, positions) == 0,
        "open_boundary_edge_count": boundary_edge_count(indices, positions),
        "bounds_m": {
            "min": [min(c[i] for c in coords) for i in range(3)],
            "max": [max(c[i] for c in coords) for i in range(3)],
        },
    }


def category_inventory(model, resolved):
    products = list(model.by_type("IfcProduct"))
    direct = Counter(item.is_a() for item in products)
    resolved_ducts, resolved_fittings = [], []
    for item in products:
        identity = resolved.get(getattr(item, "GlobalId", None))
        type_class = None if identity is None else identity["type_class"]
        if type_class == "IfcDuctSegmentType":
            resolved_ducts.append(item)
        elif type_class == "IfcDuctFittingType":
            resolved_fittings.append(item)
    def facts(classes, resolved_items=()):
        selected = [item for item in products if item.is_a() in classes]
        unique = {item.id(): item for item in selected + list(resolved_items)}.values()
        return {"entity_count": len(unique), "direct_class_counts": {c: direct[c] for c in classes if direct[c]},
                "type_resolved_entity_count": len(resolved_items),
                "representation_count": sum(getattr(item, "Representation", None) is not None for item in unique),
                "coverage": "PRESENT_IN_VENTILATION_IFC" if unique else "NOT_PRESENT_IN_VENTILATION_IFC"}
    return {
        "existing_ducts": facts(["IfcDuctSegment"], resolved_ducts),
        "existing_fittings": facts(["IfcDuctFitting"], resolved_fittings),
        "mechanical_equipment": facts(["IfcFlowController", "IfcFlowTerminal", "IfcFlowMovingDevice", "IfcEnergyConversionDevice"]),
        "walls": facts(["IfcWall", "IfcWallStandardCase"]),
        "floors_and_slabs": facts(["IfcSlab"]),
        "columns": facts(["IfcColumn"]),
        "structural_beams": facts(["IfcBeam"]),
        "other_mep_services": facts(["IfcPipeSegment", "IfcPipeFitting", "IfcCableCarrierSegment", "IfcCableCarrierFitting"]),
    }


def main():
    if not VENTILATION.exists() or not BENCHMARK.exists():
        raise SystemExit("S1 ventilation source or native benchmark artifact is missing; no report written.")
    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    model = ifcopenshell.open(str(VENTILATION))
    header = read_model_header(VENTILATION)
    scale = header["unit_scale_m"]
    _, extras = fast_network(model, scale)
    ports = {item.GlobalId: item for item in model.by_type("IfcDistributionPort")}
    hosts = port_hosts(model)
    types = resolve_occurrence_types(model)
    path_host_ids = set()
    ordered_paths = []
    for path in benchmark["selected_existing_paths"]:
        ordered = []
        for port_id in path["port_global_ids"]:
            port = ports.get(port_id)
            host = None if port is None else hosts.get(port.id())
            if host is None:
                ordered.append({"port_global_id": port_id, "host_status": "MISSING_HOST"})
                continue
            eff, type_record = effective_class(host, types)
            path_host_ids.add(host.GlobalId)
            ordered.append({"port_global_id": port_id, "host_status": "RESOLVED", "host_global_id": host.GlobalId,
                            "occurrence_class": host.is_a(), "effective_host_class": eff,
                            "type_resolution": type_record,
                            "host_placement_m": placement_frame_m(host.ObjectPlacement, scale),
                            "representation_declared": getattr(host, "Representation", None) is not None,
                            "profiles": _element_profiles(host, scale)})
        ordered_paths.append((path, ordered))
    host_products = [model.by_guid(gid) for gid in sorted(path_host_ids)]
    mesh_result = extract_world_meshes(model, host_products, scale)
    mesh_by_id = {item["global_id"]: mesh_summary(item) for item in mesh_result["retained_meshes"]}
    failure_by_id = {item["global_id"]: item["reason"] for item in mesh_result["failures"]}
    per_path = []
    all_continuity = []
    network, _ = fast_network(model, scale)
    port_location = {row["global_id"]: row["origin_m"] for row in network["ports"]}
    for path, ordered in ordered_paths:
        seen_hosts = []
        for item in ordered:
            gid = item.get("host_global_id")
            if gid and (not seen_hosts or seen_hosts[-1] != gid):
                seen_hosts.append(gid)
        host_records = []
        for gid in seen_hosts:
            row = next(item for item in ordered if item.get("host_global_id") == gid)
            geometry = mesh_by_id.get(gid, {"geometry_status": "UNSUPPORTED_OR_TESSELLATION_FAILED", "reason": failure_by_id.get(gid, "no retained mesh")})
            host_records.append({**row, "geometry": geometry})
        joints = []
        for edge in path["relationship_provenance"]:
            a, b = edge["port_global_ids"]
            gap = distance(port_location.get(a), port_location.get(b))
            row = {"edge_id": edge["edge_id"], "edge_type": edge["edge_type"], "port_global_ids": [a, b],
                   "connector_origin_separation_m": gap,
                   "relationship_provenance": edge}
            if edge["edge_type"] == "EXPLICIT_PORT_JOINT":
                row["continuity_evidence"] = "EXPLICIT_IFC_REL_CONNECTS_PORTS_WITH_RESOLVED_PORT_POSITIONS" if gap is not None else "EXPLICIT_IFC_REL_CONNECTS_PORTS_POSITION_UNRESOLVED"
            else:
                row["continuity_evidence"] = "RESTRICTED_INTERNAL_HOST_CONTINUITY_ALREADY_VERIFIED"
            joints.append(row)
        all_continuity.extend(joints)
        per_path.append({"named_system": path["named_system"], "start_port_global_id": path["start_port"]["global_id"],
                         "end_port_global_id": path["end_port"]["global_id"], "hop_count": path["hop_count"],
                         "relationship_edge_count": len(path["relationship_provenance"]), "ordered_port_hosts": host_records,
                         "connector_continuity": joints,
                         "physical_verdict": "UNVERIFIED_NO_DECLARED_CLEARANCE_OR_FULL_OBSTACLE_SET"})
    mesh_meta = {key: value for key, value in mesh_result.items() if key not in {"retained_meshes", "failures"}}
    extraction = {"dataset": {"path": str(VENTILATION), "sha256": sha256(VENTILATION), "schema": model.schema, "unit_scale_m": scale},
                  "scope": "Only unique hosts referenced by the three existing REAL_IFC_NATIVE_PORT_TO_PORT paths.",
                  "mesh_extraction": {**mesh_meta, "failure_count": len(mesh_result["failures"]), "failures": mesh_result["failures"]},
                  "paths": per_path,
                  "interpretation": "Tessellation is an exact rendering of supported IFC body representations, not an engineering duct specification or a clash-free finding."}
    write("sixty5_native_duct_geometry_extraction.json", extraction)
    explicit_gaps = [row["connector_origin_separation_m"] for row in all_continuity if row["edge_type"] == "EXPLICIT_PORT_JOINT" and row["connector_origin_separation_m"] is not None]
    continuity = {"classification": "READ_ONLY_IFC_GEOMETRIC_CONTINUITY_EVIDENCE", "paths": per_path,
                  "aggregate": {"edge_count": len(all_continuity), "explicit_joint_count": len(explicit_gaps),
                                "explicit_connector_separation_m": {"min": min(explicit_gaps) if explicit_gaps else None, "max": max(explicit_gaps) if explicit_gaps else None},
                                "placement_failure_count": len(extras["placement_failures"])},
                  "verdict": "UNVERIFIED_FOR_PHYSICAL_CONTINUITY", "reason": "Relationship and connector-origin evidence is available, but no approved tolerance, fitting interface specification, clearance requirement, or independently complete obstacle mesh set is supplied."}
    write("sixty5_native_path_geometric_continuity.json", continuity)
    inventory = category_inventory(model, types)
    obstacle = {"source": {"ventilation_ifc": str(VENTILATION), "sha256": sha256(VENTILATION), "schema": model.schema}, "coverage": inventory,
                "assessment": {"obstacle_coverage_complete": False, "minimum_additional_companion_file_to_acquire": "arc.ifc", "why": "The ventilation IFC contains no wall or slab entities; arc.ifc is the minimum S1 companion expected to supply architectural walls/floors. Existing str.ifc can supply structural members only after independent coordinate-registration evidence is established.", "registration_gate": "EXTERNAL_REGISTRATION_REQUIRED", "do_not_infer": "No cross-model obstacle coverage is inferred from bounding boxes or shared dependent placements."}}
    write("sixty5_obstacle_completeness_assessment.json", obstacle)
    coverage = {"classification": "READ_ONLY_PHYSICAL_VALIDATION_COVERAGE", "mesh_representation": {"status": "PARTIAL_AVAILABLE", "evidence": mesh_meta},
                "duct_dimensions": {"status": "PARTIAL_IFC_PROFILE_EVIDENCE_RECORDED", "verdict": "NOT_SUFFICIENT_FOR_DECLARED_DUCT_VOLUME_VALIDATOR"},
                "interface_continuity": {"status": "RELATIONSHIP_AND_PORT_POSITION_EVIDENCE_ONLY", "verdict": "UNVERIFIED"},
                "clearance": {"status": "MISSING_APPROVED_REQUIREMENT", "verdict": "NOT_EVALUATED"},
                "obstacle_set": {"status": "INCOMPLETE_SINGLE_MODEL", "verdict": "NOT_EVALUATED"},
                "self_collision_handling": {"status": "EXCLUDED", "evidence": "Existing duct/fitting bodies were not supplied as structural collision obstacles."},
                "overall_verdict": "INCOMPLETE_UNVERIFIED", "reason": "No false physical pass: supported geometry exists only for selected network hosts, while clearance, interface criteria, and registered complete obstacles are absent."}
    write("sixty5_physical_validation_coverage_matrix.json", coverage)
    config = {"classification": "REAL_IFC_NATIVE_PORT_TO_PORT_RESEARCH_CONFIGURATION", "routing_executed": False,
              "coordinate_frame": "Original S1 ventilation IFC world coordinates in SI metres", "dataset": extraction["dataset"],
              "cases": [{"named_system": p["named_system"], "start_port_global_id": p["start_port_global_id"], "end_port_global_id": p["end_port_global_id"], "existing_hop_count": p["hop_count"], "existing_relationship_edge_count": p["relationship_edge_count"]} for p in per_path],
              "available_inputs": ["native port GlobalIds, placements, axes, explicit connection provenance", "supported tessellated host bodies and profile evidence where represented"],
              "missing_engineering_inputs": ["approved duct section and clearance requirements", "fitting and transition specifications", "registered complete architectural/structural obstacle geometry", "authorization for a research-only rerouting execution"],
              "research_only_parameters": {"status": "NOT_PROPOSED_OR_APPROVED", "values": None},
              "execution_gates": {"external_registration": "EXTERNAL_REGISTRATION_REQUIRED", "physical_validation": "INCOMPLETE_UNVERIFIED", "authorization": "NOT_ESTABLISHED", "verdict": "BLOCKED_READY_TO_RUN_AFTER_GATES"},
              "expected_metrics_when_authorized": ["path length and bend count", "centerline collision result against registered obstacle set", "declared-section/clearance physical result", "comparison to existing provenanced network path"]}
    write("sixty5_native_port_routing_experiment_configuration.json", config)
    print(json.dumps({"reports_written": 5, "unique_hosts": len(host_products), "mesh_extraction_seconds": mesh_result["extraction_seconds"], "physical_verdict": coverage["overall_verdict"]}, indent=2))


if __name__ == "__main__":
    main()

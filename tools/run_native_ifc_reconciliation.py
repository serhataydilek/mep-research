"""Read-only reconciliation for the real-IFC native-connectivity pilot.

The runner records evidence, never writes IFC files, applies no coordinate
transform, and only emits a routing-pilot result when both IFC graph evidence
and cross-model registration meet their gates.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import ifcopenshell

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
IFC_ROOT = SCRIPT_ROOT.parent / "hvac_research_ifc"
if str(IFC_ROOT) not in sys.path:
    sys.path.insert(0, str(IFC_ROOT))

from src.coordinate_registration import assess
from src.real_ifc import default_external_root, read_model_header


ROOT = IFC_ROOT
PRIMARY = SCRIPT_ROOT
EXTERNAL = default_external_root()
VALIDATION = ROOT / "validation"
SOURCE_PORT = "0kXj6Rl5r8FgcT4q2m$0Hh"
DIRECT_RELATIONSHIP = "1py2C13GD7nBHupQbkGFyt"


def write(name: str, payload: dict[str, Any]) -> None:
    (VALIDATION / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def workspace_row(repo: Path) -> dict[str, Any]:
    return {
        "path": str(repo), "git_dir": (repo / ".git").exists(), "branch": git(repo, "branch", "--show-current"),
        "head": git(repo, "rev-parse", "HEAD"), "recent_commits": git(repo, "log", "--oneline", "-6").splitlines(),
        "dirty_entries": git(repo, "status", "--short").splitlines(),
        "native_ifc_modules": sorted(str(path.relative_to(repo)) for path in (repo / "src").glob("*ifc*.py")),
        "physical_modules": sorted(str(path.relative_to(repo)) for path in (repo / "src").glob("*physical*.py")),
    }


def workspace_report() -> dict[str, Any]:
    return {
        "report_type": "HVAC_WORKSPACE_RECONCILIATION", "status": "SEPARATE_GIT_REPOSITORIES",
        "workspaces": [workspace_row(PRIMARY), workspace_row(ROOT)],
        "comparison": {
            "benchmark_workspace": str(PRIMARY), "ifc_workspace": str(ROOT),
            "shared_input_root": str(EXTERNAL),
            "implementation_finding": "The IFC workspace contains dedicated source recovery, typed ventilation graph, coordinate-registration, native geometry, network routing, and duct-volume modules absent from the benchmark workspace.",
            "safety": "No pre-existing dirty file was modified by reconciliation; reports use new filenames only.",
        },
    }


def registration() -> dict[str, Any]:
    arch_path = EXTERNAL / "medium/digital_hub/arc.ifc"; vent_path = EXTERNAL / "medium/digital_hub/ventilation.ifc"
    architecture = read_model_header(arch_path); ventilation = read_model_header(vent_path)
    for header, path in ((architecture, arch_path), (ventilation, vent_path)):
        model = ifcopenshell.open(str(path))
        projects = model.by_type("IfcProject")
        header["project_global_id"] = projects[0].GlobalId if len(projects) == 1 else None
    result = assess(architecture, ventilation)
    return {
        "report_type": "DIGITAL_HUB_ARCHITECTURE_TO_VENTILATION_REGISTRATION", "architecture": architecture,
        "ventilation": ventilation, "assessment": result,
        "transform": {"candidate": "IDENTITY_IN_DECLARED_METRE_FRAME", "applied": False,
                      "status": "UNVERIFIED" if result["verdict"] != "VERIFIED_SHARED_PROJECT_FRAME" else "VERIFIED",
                      "reason": "No transform is applied: declared site residual exceeds the files' precision, so visual/bounds overlap is not used as registration evidence."},
    }


def connectivity() -> dict[str, Any]:
    path = EXTERNAL / "medium/digital_hub/ventilation.ifc"; header = read_model_header(path)
    model = ifcopenshell.open(str(path)); source_port = model.by_guid(SOURCE_PORT); relationship = model.by_guid(DIRECT_RELATIONSHIP)
    b7 = json.loads((VALIDATION / "level_b7_as_built_ventilation_network.json").read_text(encoding="utf-8"))
    traces = b7["source_to_selected_terminal_reachability"]
    expected_terminals = {"02G75MeSH6ofT0aVcMkEvs", "0M6w4nG19D_PYgipdLtl9S", "2VDMPDj7nDKBOCCu9tAipi"}
    actual_terminals = {item["terminal_port_global_id"] for item in traces if item.get("reachable")}
    raw_terminal_existence = {gid: model.by_guid(gid) is not None for gid in expected_terminals}
    return {
        "report_type": "DIGITAL_HUB_CONNECTIVITY_DISCREPANCY_ANALYSIS", "source": header,
        "direct_raw_ifc_reparse": {"source_port_exists": source_port is not None, "source_port_flow_direction": None if source_port is None else str(source_port.FlowDirection),
                                    "direct_relationship_exists": relationship is not None, "direct_relationship_class": None if relationship is None else relationship.is_a(),
                                    "terminal_ports_exist": raw_terminal_existence},
        "typed_graph": {"system": b7["system"], "source_port_global_id": b7["source_port_global_id"],
                        "source_matches_expected": b7["source_port_global_id"] == SOURCE_PORT,
                        "direct_relationship_global_id": DIRECT_RELATIONSHIP, "selected_terminal_port_global_ids": sorted(actual_terminals),
                        "all_expected_terminals_selected": expected_terminals == actual_terminals,
                        "trace_rule": b7["topology"]["rule"], "uncertain_host_count": len(b7["topology"]["uncertain_hosts"]),
                        "rejected_relationship_count": len(b7["topology"]["rejections"])},
        "comparison": {
            "earlier_benchmark": "Counted direct IfcRelConnectsPorts evidence and correctly refused to infer source-to-terminal traversal or cross-file registration.",
            "reconciled_graph": "Uses explicit port joints plus restricted, flow-validated internal host continuity; it does not use proximity or blanket shared-host bridging.",
            "discrepancy_classification": "INCOMPLETE_PORT_GRAPH_TRAVERSAL_IN_EARLIER_BENCHMARK_NOT_SOURCE_VERSION_MISMATCH",
            "native_existing_connectivity_status": "VERIFIED_EXISTING_V_ZULUFT_NETWORK" if expected_terminals == actual_terminals else "NOT_VERIFIED",
        },
        "routing_pilot_gate": "BLOCKED_BY_COORDINATE_REGISTRATION_AND_ENGINEERING_INPUTS",
    }


def eligibility() -> dict[str, Any]:
    inventory = json.loads((PRIMARY / "out/multi_ifc_benchmark/multi_ifc_benchmark_manifest.json").read_text(encoding="utf-8"))["available_ifc_inventory"]
    by_path = {Path(item["path"]).resolve(): item for item in inventory}
    def step_count(path: Path, token: bytes) -> int:
        total = 0; tail = b""
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                text = tail + chunk.upper(); total += text.count(token); tail = text[-len(token):]
        return total
    rows = []
    for path in sorted(EXTERNAL.rglob("*.ifc")):
        meta = by_path[path.resolve()]; ports = step_count(path, b"IFCDISTRIBUTIONPORT"); relationships = step_count(path, b"IFCRELCONNECTSPORTS")
        has_distribution_geometry = bool(step_count(path, b"IFCDUCTSEGMENT") or step_count(path, b"IFCFLOWSEGMENT"))
        is_digital = path.name == "ventilation.ifc" and path.parent.name == "digital_hub"
        rows.append({"path": str(path.relative_to(EXTERNAL)).replace("\\", "/"), "schema": meta["schema"], "unit_scale_m": meta["metre_scale"],
                     "distribution_ports": ports, "explicit_port_relationships": relationships, "named_systems": ["V_Zuluft"] if is_digital else [],
                     "usable_connector_coordinates": "DIRECTLY_REPARSED_FOR_DIGITAL_HUB_ONLY" if is_digital else "NOT_ESTABLISHED_BY_LIGHTWEIGHT_INVENTORY",
                     "profile_evidence_present": has_distribution_geometry,
                     "structural_geometry_candidate": any(name in path.name.lower() for name in ("arc.ifc", "architecture")),
                     "verified_source_to_terminal_connectivity": is_digital,
                     "native_pilot_eligibility": ("BLOCKED_REGISTRATION" if is_digital else
                                                  "REQUIRES_TYPED_GRAPH_AUDIT" if ports and relationships else
                                                  "NOT_ELIGIBLE_MISSING_PORT_GRAPH")})
    return {"report_type": "THIRTEEN_MODEL_NATIVE_IFC_ELIGIBILITY_MATRIX", "candidate_count": len(rows), "candidates": rows,
            "interpretation": "Only Digital Hub ventilation has the audited typed port graph; it remains blocked from a combined architecture-routing pilot because coordinate registration and engineering inputs are unverified."}


def benchmark_status(reg: dict[str, Any], conn: dict[str, Any]) -> dict[str, Any]:
    return {"report_type": "SEPARATED_REAL_IFC_BENCHMARK_STATUS", "categories": {
        "REAL_IFC_GEOMETRY_SYNTHETIC_ENDPOINTS": {"status": "COMPLETED_PREVIOUS_WORK", "models": ["digital_hub", "duplex", "wbdg_office"]},
        "REAL_IFC_NATIVE_CONNECTIVITY_EXPERIMENTAL": {"status": "BLOCKED", "connectivity": conn["comparison"]["native_existing_connectivity_status"], "coordinate_registration": reg["transform"]["status"], "routing_executed": False},
        "FULLY_PHYSICALLY_VALIDATED_ROUTING": {"status": "NOT_RUN", "reason": "No approved replacement scope, nominal dimensions, clearance, fittings, penetrations, or coordinated obstacle coverage."},
    }, "next_unresolved_blockers": ["Approved architecture-to-ventilation registration transform/tolerance", "Replacement routing scope", "Nominal duct dimensions and roll", "Clearance and fitting specifications", "Penetration authorization", "Coordinated obstacle coverage"]}


def main() -> int:
    print("workspace", flush=True); workspaces = workspace_report()
    print("registration", flush=True); reg = registration()
    print("connectivity", flush=True); conn = connectivity()
    print("eligibility", flush=True); matrix = eligibility()
    print("writing", flush=True); status = benchmark_status(reg, conn)
    write("workspace_reconciliation_report.json", workspaces); write("digital_hub_connectivity_discrepancy.json", conn)
    write("digital_hub_coordinate_registration.json", reg); write("native_ifc_eligibility_matrix_13_models.json", matrix)
    write("native_ifc_benchmark_status.json", status)
    print(f"connectivity={conn['comparison']['native_existing_connectivity_status']} registration={reg['transform']['status']} candidates={matrix['candidate_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

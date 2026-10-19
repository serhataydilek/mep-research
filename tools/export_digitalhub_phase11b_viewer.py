"""Build bounded, read-only source-IFC comparison fixtures for Phase 11B."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "experiments" / "digitalhub_v1"
DATA = ROOT / "validation" / "ifc_samples" / "external" / "rwth_digitalhub_v1"
ROLE_FILES = {
    "architecture": "FM_ARC_DigitalHub_v1.ifc",
    "heating": "FM_HZG_DigitalHub_v1.ifc",
    "ventilation": "FM_LFT_DigitalHub_v1.ifc",
    "sanitary": "FM_SAN_DigitalHub_v1.ifc",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def overlaps(bounds: dict, roi: list[float]) -> bool:
    return not (
        bounds["max_x_m"] < roi[0] or bounds["min_x_m"] > roi[1]
        or bounds["max_y_m"] < roi[2] or bounds["min_y_m"] > roi[3]
        or bounds["max_z_m"] < roi[4] or bounds["min_z_m"] > roi[5]
    )


def bounds(vertices: list[float]) -> dict:
    return {
        "min_x_m": min(vertices[0::3]), "max_x_m": max(vertices[0::3]),
        "min_y_m": min(vertices[1::3]), "max_y_m": max(vertices[1::3]),
        "min_z_m": min(vertices[2::3]), "max_z_m": max(vertices[2::3]),
    }


def baseline(results: dict, request_id: str) -> dict:
    return next(item for item in results["cases"] if item["request_id"] == request_id and item["mode"] == "BASELINE_BODY_AABB_1MM_COVERING")


def make_chunk(request: dict, result: dict, evidence: dict, cache: dict, models: dict) -> dict:
    roi = request["roi_bounds_m"]
    source_role = request["discipline"]
    original = set(request["replaced_host_global_ids"])
    retained = set(request["boundary_host_global_ids"])
    offenders = set()
    if request["request_id"] == "DH11B-V1":
        offenders = {item["global_id"] for item in evidence["offenders"]}
    selected: dict[str, set[str]] = defaultdict(set)
    for role, record in cache["roles"].items():
        for gid, item in record["collected"]["elements"].items():
            if overlaps(item["bounds_m"], roi):
                selected[role].add(gid)
    selected[source_role].update(original | retained)
    for gid in offenders:
        for role, model in models.items():
            try:
                entity = model.by_guid(gid)
            except RuntimeError:
                entity = None
            if entity is not None:
                selected[role].add(gid)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    meshes, failures = [], []
    for role in ROLE_FILES:
        model = models[role]
        for gid in sorted(selected[role]):
            try:
                entity = model.by_guid(gid)
            except RuntimeError:
                entity = None
            if entity is None:
                failures.append({"global_id": gid, "role": role, "reason": "not found in source IFC"})
                continue
            try:
                shape = ifcopenshell.geom.create_shape(settings, entity)
                vertices = [float(value) for value in shape.geometry.verts]
                indices = [int(value) for value in shape.geometry.faces]
                if not vertices or not indices:
                    raise ValueError("empty mesh")
                category = "ARCHITECTURE" if role == "architecture" else "RETAINED_MEP_OBSTACLE"
                if gid in original:
                    category = "ORIGINAL_REPLACED_HOST"
                elif gid in retained:
                    category = "RETAINED_BOUNDARY_HOST"
                elif gid in offenders:
                    category = "VALIDATION_OFFENDER"
                meshes.append({
                    "global_id": gid, "ifc_class": entity.is_a(), "name": entity.Name,
                    "role": role, "viewer_category": category, "positions": vertices,
                    "indices": indices, "bounds_m": bounds(vertices),
                })
            except Exception as exc:
                failures.append({"global_id": gid, "role": role, "reason": f"{type(exc).__name__}: {exc}"})
    validation = result.get("collision_validation") or result.get("validation") or {}
    events = []
    for item in validation.get("clearance_violations") or []:
        events.append({**item, "status": "CLEARANCE_VIOLATION", "validation_method": validation.get("method")})
    return {
        "schema_version": 1, "kind": "DIGITALHUB_PHASE11B_GEOMETRY_CHUNK", "request_id": request["request_id"],
        "meshes": meshes, "geometry_failures": failures, "selected_mesh_count": len(meshes),
        "route": {
            "original_host_global_ids": sorted(original), "retained_boundary_global_ids": sorted(retained),
            "start_m": result.get("immutable_start_m") or request["start_m"],
            "goal_m": result.get("immutable_goal_m") or request["goal_m"],
            "generated_polyline_m": result.get("world_polyline_m") or [],
            "body_half_extent_m": result.get("body_half_extent_m") or request["body_half_extent_m"],
            "search_state": result["search_state"], "validation_label": result.get("validation_label"),
            "geometric_state": result.get("geometric_state"), "physical_route_length_m": result.get("physical_route_length_m"),
            "physical_vertical_travel_m": result.get("physical_vertical_travel_m"),
            "expanded_node_count": result.get("expanded_node_count"), "events": events,
        },
    }


def build(output: Path, chunk_dir: Path) -> dict:
    requests = json.loads((EXPERIMENTS / "phase11b_requests.json").read_text())["requests"]
    results = json.loads((EXPERIMENTS / "phase11b_results.json").read_text())
    evidence = json.loads((EXPERIMENTS / "phase11b_clearance_evidence.json").read_text())
    manifest = json.loads((EXPERIMENTS / "dataset_manifest.json").read_text())
    cache = json.loads((EXPERIMENTS / "world_aabb_cache.json").read_text())
    expected = {item["file_name"]: item["sha256"] for item in manifest["files"]}
    for name in ROLE_FILES.values():
        actual = digest(DATA / name)
        if actual != expected[name]:
            raise ValueError(f"Dataset hash mismatch for {name}")
    models = {role: ifcopenshell.open(str(DATA / name)) for role, name in ROLE_FILES.items()}
    chunk_dir.mkdir(parents=True, exist_ok=True)
    experiments = []
    for request in requests:
        result = baseline(results, request["request_id"])
        chunk = make_chunk(request, result, evidence, cache, models)
        name = f"{request['request_id'].lower()}_geometry.json"
        body = json.dumps(chunk, separators=(",", ":"))
        (chunk_dir / name).write_text(body + "\n", encoding="utf8")
        experiments.append({
            "request_id": request["request_id"], "discipline": request["discipline"],
            "system_names": request["system_names"], "roi_bounds_m": request["roi_bounds_m"],
            "chunk": f"{chunk_dir.name}/{name}", "chunk_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "search_state": result["search_state"], "validation_label": result.get("validation_label"),
        })
    fixture = {
        "schema_version": 6, "kind": "DIGITALHUB_PHASE11B_COMPARISON_MANIFEST", "units": "m",
        "scenario_id": "digitalhub-v1-phase11b-real-ifc-routing-comparison",
        "coordinate_frame": "ORIGINAL_IFC_WORLD_METRES_USE_WORLD_COORDS_NO_ALIGNMENT_TRANSFORM",
        "applied_alignment_transform": None, "dataset_manifest": manifest, "experiments": experiments,
        "warnings": ["Original layers are bounded source IFC meshes identified by GlobalId.", "Generated geometry is only the saved Phase 11B physical execution polyline.", "DH11B-V1 is a clearance violation, not an acceptable route; the other cases have no generated route."],
    }
    output.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf8")
    return fixture


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "viewer" / "digitalhub_phase11b_fixture.json")
    parser.add_argument("--chunk-dir", type=Path, default=ROOT / "viewer" / "digitalhub_phase11b_chunks")
    args = parser.parse_args()
    fixture = build(args.output, args.chunk_dir)
    print(json.dumps({"output": str(args.output), "experiments": len(fixture["experiments"])}))


if __name__ == "__main__":
    main()

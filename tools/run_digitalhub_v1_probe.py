"""Probe the acquired DigitalHub V1 IFC files and write a dataset manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.external_ifc_probe import federation_assessment, probe_model, write_json
from src.external_ifc_routing import DISCIPLINE_ORDER, discover_discipline_files


DEFAULT_DATA = ROOT / "validation" / "ifc_samples" / "external" / "rwth_digitalhub_v1"
DEFAULT_OUTPUT = ROOT / "experiments" / "digitalhub_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe DigitalHub V1 IFC discipline files.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    files = discover_discipline_files(args.data_dir)
    missing = [role for role in ("architecture", *DISCIPLINE_ORDER) if role not in files]
    if missing:
        raise SystemExit(f"Missing IFC files for: {missing}")
    acquisition_path = args.data_dir / "acquisition_record.json"
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8-sig")) if acquisition_path.exists() else None
    probed = {}
    for role in ("architecture", *DISCIPLINE_ORDER):
        print(f"probing {role}: {files[role].name}", file=sys.stderr, flush=True)
        probed[role] = probe_model(files[role], tessellate=True, threads=args.threads)
    manifest_files = []
    for role, record in probed.items():
        manifest_files.append({
            "role": role,
            "file_name": record["file_name"],
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "schema": record["schema"],
            "view_definition": record["header"]["view_definition"],
            "length_unit_scale_m": record["length_unit_scale_m"],
            "length_units": record["length_units"],
        })
    manifest = {
        "dataset_id": "rwth-digitalhub-v1",
        "repository": "https://github.com/RWTH-E3D/DigitalHub",
        "revision": None if acquisition is None else acquisition.get("revision"),
        "license": "MIT",
        "license_attribution": "Copyright (c) 2020 RWTH Aachen University - E3D Institute of Energy Efficiency and Sustainable Building",
        "technical_report_doi": "10.18154/RWTH-2020-11683",
        "acquisition_record": str(acquisition_path) if acquisition_path.exists() else None,
        "files": manifest_files,
        "note": "SHA-256 values are computed from the local payloads. Schema and view definition are read from each file.",
    }
    payload = {
        "dataset_manifest": manifest,
        "files": probed,
        "federation": federation_assessment(probed),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "dataset_manifest.json", manifest)
    write_json(args.output_dir / "probe_results.json", payload)
    write_json(args.data_dir / "dataset_manifest.json", manifest)
    print(json.dumps({
        "probe_results": str(args.output_dir / "probe_results.json"),
        "federation_status": payload["federation"]["status"],
        "files": [(item["role"], item["schema"], item["length_unit_scale_m"], item["sha256"][:12]) for item in manifest_files],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

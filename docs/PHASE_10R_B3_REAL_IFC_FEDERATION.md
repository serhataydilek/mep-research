# Phase 10R-B3: Real IFC Acquisition, Federation & 3D Integration

## Acceptance gates

| Gate | Result |
|---|---|
| Files acquired | **PASS.** The five official Medical-Dental Clinic Git-LFS payloads were retrieved from `buildingsmart-community/Community-Sample-Test-Files` revision `7ddf57a201f88a0c213d5322b02ed15e94a60a40`. |
| Hash / STEP / parse provenance | **PASS.** Each file is a STEP payload, parses with IfcOpenShell, has IFC2X3 schema and its SHA-256 equals the LFS object ID supplied in the phase request. |
| Geometry correctly federated | **NOT VERIFIED.** Each discipline is extracted in its own authoritative IFC world-metre frame. No translation, rotation, scale, or visual registration was applied. Cross-discipline registration needs independent source evidence. |
| Viewer integration | **PASS, bounded.** The viewer loads a deterministic First Floor fixture containing eight actual world-space source meshes per discipline, toggles, clipping, orbit/pan/zoom, and element/GlobalId selection. |
| Native connectivity verified | **PARTIAL.** Explicit port/relationship inventories are shown; no source-to-terminal traversal has been asserted and no geometric relation is inferred. |
| Routing benchmark authorized | **NO.** No endpoint or routing experiment was created. |

## Files and reproducibility

Large data lives in ignored `validation/ifc_samples/external/official_medical_dental/`; it is not tracked by Git.  The acquisition command uses sparse checkout plus Git LFS, rejects pointers, verifies the expected object hash before copying, and writes an ignored manifest:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\acquire_medical_dental_ifc.ps1
$env:PYTHONPATH='.'
python -m src.medical_dental_ifc --data-dir validation\ifc_samples\external\official_medical_dental --output viewer\medical_dental_clinic_fixture.json
python -m http.server 8000 -d viewer
```

The monolithic output is deliberately ignored because it exceeds GitHub's
single-file limit. Convert it to the tracked progressive viewer artifact before
sharing:

```powershell
python -m src.medical_dental_chunks
```

Open `http://localhost:8000/?fixture=medical_dental_clinic_progressive.json`.

The source hashes are: architectural `2ac970ce…7880`; structural `325287b3…533d8`; HVAC `39c88a79…eab9b`; plumbing `e662a8d…e23`; electrical `70c436de…95d32`.  The generated fixture records full hashes, size, schema, unit scale, project/storey identifiers, element counts, source file names, and mesh GlobalIds.

## Measured export

The selected storey is `First Floor` (`3eM8WbY_59RR5TDWs3wwJP`, elevation approximately 0 m).  The deterministic sample cap is eight meshes per discipline: architecture 8/983 represented products, structure 8/463, HVAC 8/897, plumbing 8/6,587, and electrical 8/2,384.  This cap prevents the browser from loading a complete 122 MB+ federation; it does not alter IFC geometry and is conspicuously recorded in the fixture.

The explicit-relationship inventory reports 7,390 HVAC ports/3,695 `IfcRelConnectsPorts` relationships and 13,058 plumbing ports/6,529 relationships.  Architecture, structure, and electrical report no distribution ports/port links in this source.  Counts are inventory facts only; port-pair completeness, equipment/terminal classifications, ambiguous links, and source-to-terminal traces require the next read-only connectivity pass.

## Limitations and readiness

Meshes are exact tessellated source geometry after IFC declared-unit conversion but are a bounded representative selection.  They do not prove complete obstacle coverage, model registration, clearance, fitting compatibility, clash freedom, physical feasibility, or engineering compliance.  Opening/shaft semantics are not inferred from proxy bounds.  The existing frozen grid router and validators were not modified or invoked.

The work is ready for a controlled **read-only federation-registration and connectivity classification** step: establish shared placement/control evidence first, then identify source/terminal pairs from explicit IFC relationships. It is not ready for real-IFC routing.

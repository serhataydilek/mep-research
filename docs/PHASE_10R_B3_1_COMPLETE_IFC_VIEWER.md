# Phase 10R-B3.1: Complete and Debuggable Real-IFC Viewer

The B3 eight-mesh sampling cap was removed from the exporter. `python -m src.medical_dental_ifc` now defaults to every supported tessellated element in the explicitly defined First Floor convention: Structural `First Floor`, Plumbing `Level 1`, and architecture/HVAC/electrical containment names beginning with `1`. This is a source-model convention, not a federation transform.

`--max-meshes N` is available only as an explicit diagnostic/performance limit. Its omitted GlobalIds are exported in `limit_omitted_global_ids`; skipped tessellation failures and their reasons are also exported. Layer metadata reports candidates, exported/skipped elements, triangles, bounds, and export seconds. The old B3 fixture remains a bounded artifact until the new complete export is run; it is not relabelled as complete.

Viewer changes add a real-IFC fixture option, per-discipline visibility, IFC GlobalId selection, ambient/directional lighting, double-sided IFC materials, reset-view, and clipping disabled by default. Camera fit uses loaded mesh bounds. No coordinate adjustment is applied; fixture status remains `UNVERIFIED_CROSS_DISCIPLINE_REGISTRATION`.

## Run

```powershell
$env:PYTHONPATH='.'
python -m src.medical_dental_ifc --output viewer\medical_dental_clinic_fixture.json
python -m http.server 8000 -d viewer
```

The full export is intentionally potentially large. It must be measured from its reported `export_seconds`, fixture byte size, mesh/triangle counts, and viewer diagnostics before treating it as browser-safe. This implementation does not silently truncate it. Existing B3 source inventories show that containment naming differs across disciplines, which is the primary remaining storey-selection uncertainty.

The monolithic local fixture is ignored for GitHub publication; use the B3.3
progressive manifest and chunks when distributing the complete viewer evidence.

No routing algorithms, validator semantics, benchmark cases, or endpoints were changed. No physical clash or routing-feasibility claim is made.

# Phase 10R-B2: Real IFC Benchmark Feasibility Audit

**Status:** audit only; no new routing benchmark, routing algorithm, validator, or frozen input was changed.
**Audit date:** 2026-10-19
**Decision:** **NO_NATIVE_IFC_BENCHMARK_AUTHORIZED**.  Candidate A is retained as the best acquisition and federation lead, not as a runnable native-routing benchmark.  Candidate B is a useful public sample source, not a selected replacement.

## Scope and method

This audit examined the frozen Phase 10R-A/B work, the checked-in IFC sample manifest, ignored local IFC payload registry, persisted real-IFC experiment artifacts, and the existing extraction, grid, routing, validation, and viewer code.  No external model was rewritten, transformed, recentered, or added to the repository.  No synthetic endpoint was presented as native.

Commands used (all read-only except the already-existing historical experiment outputs):

```powershell
Get-Content -Raw validation\ifc_samples\manifest.json
Get-Content -Raw validation\ifc_samples\external\download_manifest.json
python - < local IFC inventory script using ifcopenshell.open and calculate_unit_scale
python -m pytest -q tests/test_phase10r_visual_debug.py tests/test_multistorey_benchmark.py
git diff --check
```

The candidate-source pages were also checked on 2026-10-19.  Access succeeded for the public repository/index pages, but this audit did **not** download a new Candidate B payload or infer an artefact hash from a web listing.  That is an intentional access/evidence limitation, not a substitution.

## Existing-engine audit

| Concern | Measured implementation boundary | Consequence for real IFC |
|---|---|---|
| Coordinate extraction | `src/voxel.py` and `src/spatial_evidence.py` use IfcOpenShell `USE_WORLD_COORDS`; unit scale is read from IFC. | World-coordinate rendering/extraction is available, but federation registration remains a source-data responsibility. |
| Obstacle semantics | The frozen baseline extractor accepts only `IfcWall` and `IfcColumn`; the historical multi-IFC runner additionally rasterized `IfcBeam` and `IfcSlab` as conservative AABBs. | The latter is an experimental overlay, not baseline geometry semantics; neither supports openings, curved solids, or true clearance solids. |
| Grid/routing | `src.routing.astar_3d` is six-neighbour grid A*.  Baseline grids originate from controlled benchmark configuration. | It has no generic IFC demand/connector/permission importer and must not be treated as one. |
| Validation | `src.constructability.py` is the frozen B0/B1 proxy validator. | It does not establish real-installation clearance, fitting, connector engagement, or engineering compliance. |
| Endpoint evidence | Existing endpoint generation is benchmark configuration based. | IFC ports, system assignments, profiles, and `IfcRelConnectsPorts` need a separate, verified adapter before native routing. |
| Viewer | `src/visualization_export.py` exports authoritative world-space fixture coordinates; `viewer/index.html` renders that JSON, including conflicts/clearance envelopes. | It can visualize a versioned real-IFC overlay only after the overlay has verified source coordinates. It is not an IFC model federator. |

The Phase 10R-A result remains relevant but separate: the frozen 12-case benchmark's coincident elevations arise from its endpoint/objective construction, not from evidence about real IFC feasibility.  The Phase 10R-B two-storey configuration is independently identifiable synthetic research and is not evidence about Candidate A or B.

## Candidate A — buildingSMART Medical-Dental Clinic

The buildingSMART community repository exposes the Medical-Dental Clinic directory; the project’s pinned `validation/ifc_samples/manifest.json` records the exact `Clinic_HVAC.ifc` upstream commit `7ddf57a...`, IFC2X3, 26,914,597 bytes, and SHA-256 `39c88a79…aaeab9b`.  The local IFC-Bench mirror contains a related three-discipline Dental Clinic package and preserves the model card attribution to the buildingSMART Medical-Dental Test Files.

### Persisted local provenance and inventory

| Local immutable input | Discipline | Schema / scale | Bytes | SHA-256 | Evidence status |
|---|---:|---:|---:|---|---|
| `validation/ifc_samples/external/medium/dental_clinic/arc.ifc` | architecture | IFC2X3 / 1 m | 13,208,096 | `b90fe57b8aa9329d762a564770e697d4f7357677484e3a78ede4ab6d2163c0c0` | Hash-pinned local mirror; no georeferencing entities reported by the persisted inventory. |
| `.../dental_clinic/mep.ifc` | MEP | IFC2X3 / 1 m | 125,780,663 | `72bdbfeb104d044bcd1ae644f9a5c6e72fd7750c2fd15fe98de2c53a04850e8d` | Hash-pinned local mirror; no georeferencing entities reported by the persisted inventory. |
| `medical_dental_clinic_hvac_ifc2x3` in the tracked manifest | HVAC | IFC2X3 / m | 26,914,597 | `39c88a79f48fbe56da86afb0fb3ebd188f8930df3df7dbfbd9ecaa535aaeab9b` | Separately pinned community-repository HVAC file; it is **not assumed byte-identical** to local `mep.ifc`. |

The retained local model card describes a two-storey, redacted clinic and names `arc.ifc`, `mep.ifc`, and `str.ifc`; however, `str.ifc` is not present in the local ignored payload registry.  Therefore structural completeness, openings, and architectural/structural federation cannot be claimed.

### Architectural/structural audit

* **Architecture:** present locally, IFC2X3 and metre-scaled.  It is a plausible obstacle source only after tessellation, element-class inventory, and bounded test-region selection.
* **Structure:** described in retained package metadata, but absent locally.  No structural slabs/beams/openings can be treated as authoritative for a real benchmark.
* **Openings/doors/windows/space/containment:** the existing baseline grid does not consume these semantic features.  An opening must not be silently converted to a passable cell; current AABB obstacle policy is incapable of recognizing it safely.
* **Cross-discipline coordinates:** no map conversion/georeferencing entity was recorded for either local file.  Matching units alone do not prove that IFC local-placement chains share a federated frame.  No translation, scale, or rotation was applied in this audit.

### MEP and native connectivity audit

The separately validated pinned `Clinic_HVAC.ifc` yielded 3,704/3,704 usable distribution geometries, 1,548 `IfcFlowSegment`, 1,590 `IfcFlowFitting`, four storeys, and 1,550 rotated local placements.  It contained no current-policy wall/column obstacles.  These are geometry/inventory facts, **not** a source-to-terminal route proof.

The local `mep.ifc` is a distinct, much larger artifact.  The existing multi-IFC manifest deliberately did not select either local dental file for geometry routing or native-connectivity inspection.  No persisted audit supplies verified `IfcDistributionPort`/`IfcRelConnectsPorts` source-to-terminal traces for it.  Accordingly, port presence, profiles/diameters, system membership, sources, terminals, and connectivity are **UNVERIFIED** rather than absent.

**Candidate A result:** viable as an acquisition/federation investigation; not viable as a native routing benchmark today.  The primary blockers are missing local structure, unverified architecture/MEP registration, unverified native endpoint/connectivity trace, and current obstacle/clearance limits—not a demonstrated lack of vertical space.

## Candidate B — BIMData / BIMcollab public samples

The BIMData research index describes a catalogue of IFC files from several sources and explicitly lists Medical Clinic discipline variants.  It is a catalogue, not an artefact-level immutable manifest.  BIMcollab's current documentation describes an example project of nine discipline IFC models and makes an IFC download available for non-native tools.

* **Availability:** public documentation/index pages were reachable.
* **What is known:** BIMcollab describes nine separately exported discipline models; BIMData lists heterogeneous samples/schemas and sources.
* **What is not known in this repository:** exact Candidate B download URL selected, byte size, SHA-256, licence binding for the chosen payload, schema, units, spatial frame, architectural/structural pair, ports/connections, system trace, profiles, or validation outcome.
* **Result:** **NOT_ACQUIRED / NOT_EVALUATED_AS_A_BENCHMARK**.  No candidate was silently substituted and no claim of suitability follows from a catalogue listing.

## Existing real-IFC experiment: useful evidence and strict boundary

`out/multi_ifc_benchmark/` is a persisted prior experiment.  It used real tessellated architectural geometry at 1 m resolution but explicitly synthetic endpoints and a 0.4 m × 0.25 m duct proxy.  All three outputs are `REAL_IFC_GEOMETRY_SYNTHETIC_ENDPOINTS`; each says physical duct validation is `INCOMPLETE_NO_PASS` and clearance is only `PARTIAL` because obstacles are conservative AABBs.

| Independent model | Structural AABB source counts (wall / column / beam / slab) | Grid (m) | Geometry overlay result | Native evidence/result |
|---|---:|---:|---|---|
| Digital Hub | 178 / 62 / 14 / 23 | 163×41×15, 5,311 blocked / 94,934 free | 3/3 synthetic edges completed; vertical extents -4.5 to 9.5 m | 2,656 ports, 1,310 port links, 22 candidate sources, 148 terminals; **BLOCKED** because architecture-to-MEP registration and an explicit source-to-terminal traversal were unverified. |
| Duplex | 57 / 0 / 8 / 21 | 12×30×11, 321 blocked / 3,639 free | 3/3 synthetic edges completed; vertical extents -2.5 to 7.5 m | 0 ports and 0 port links; 30 candidate sources/105 terminals; **BLOCKED**. |
| WBDG Office | 487 / 0 / 0 / 2 | 54×40×17, 235 blocked / 36,485 free | 3/3 synthetic edges completed; vertical extents -0.5 to 15.5 m | 0 ports and 0 port links; 15 candidate sources/1,456 terminals; **BLOCKED**. |

This is evidence that a bounded structural-world-coordinate overlay can be constructed.  It is not evidence that a native MEP demand can be routed, that endpoints are legitimate, that the bodies/fittings are clear, or that either candidate is physically feasible/infeasible.

## Coordinate and viewer fidelity assessment

The viewer is faithful to the coordinates contained in its JSON fixture: it does not reproject, scale, or invent route geometry.  `src/visualization_export.py` rejects a fixture that claims unearned connector engagement or collision certification, and preserves source configuration/commit identity.  This supports honest inspection of a **verified** overlay.

It does not load an IFC, evaluate `IfcLocalPlacement`, apply map conversion, or register multiple discipline files.  The existing real-IFC runner uses `USE_WORLD_COORDS`, reads each file's unit scale, and records “no recentering applied”; that establishes per-file coordinates, not cross-file coordination.  Until registration is independently evidenced, displaying an architecture and MEP file together would be potentially misleading.

## Actual meaningful test region

No real IFC test region is authorized.  A 1 m grid geometry-only experiment exists for Digital Hub, but its endpoints are synthetic and its cross-file registration remains unverified.  Selecting a clinic region now would require inventing endpoint permissions and treating unresolved openings/clearances as passable; that fails the research-integrity gate.

## Staged implementation proposal (not implemented)

1. **Acquire and freeze evidence.** Obtain Candidate A architecture, structure, and the exact HVAC/MEP file from an immutable upstream revision; record direct URLs, licences, size, SHA-256, schema, units, and IFC STEP validation.  Independently acquire one named Candidate B federation only if the same fields can be recorded.
2. **Read-only federation gate.** Extract world placement frames, storeys, shared global IDs/map-conversion data and residuals.  Accept a pairing only with documented registration evidence; otherwise retain it as separate disciplines with no overlay.
3. **Semantic inventory gate.** Count/tessellate architectural and structural obstacles; enumerate openings, spaces, containment, systems, ports, `IfcRelConnectsPorts`, profiles/dimensions, candidate sources/terminals, and same-system source-to-terminal traces.  Missing relations remain missing—no proximity graph or fabricated connection.
4. **Region and adapter design review.** Only after gates 1–3, choose a bounded service-zone region with recorded authorized endpoints and route-volume assumptions.  Specify a new, versioned adapter outside frozen baseline semantics; test coordinate mapping, obstacle treatment, endpoints, determinism, and validator boundary before execution.
5. **Execute and visualize only after approval.** Run existing algorithms unchanged against the approved adapter input, label all validation limits, and export the viewer overlay with input hashes.  Do not call the result construction-approved or physically feasible without appropriate solid/clearance and connector evidence.

## Root-cause assessment and uncertainties

The current blocker is **evidence integration**, not an observed geometric impossibility or a demonstrated search failure.  Candidate A has the strongest provenance and multi-storey MEP promise, but lacks verified federation/structure/native demand evidence in the available local set.  Candidate B is discoverable but not yet artefact-pinned or audited.  The retained 3,704-entity HVAC extraction and the historical geometry overlays demonstrate parser/visualization capability only.

Uncertainties deliberately left unresolved: whether the local `mep.ifc` is semantically compatible with the pinned `Clinic_HVAC.ifc`; whether the missing `str.ifc` can be acquired at a matching revision; whether either clinic federation shares an approved coordinate frame; and whether an IFC-native source-to-terminal problem satisfying the frozen engine's limitations exists.  None should be resolved by route fabrication, coordinate guessing, or changes to the frozen benchmark.

## Validation result

No code was changed.  `PYTHONPATH=. pytest -q tests/test_phase10r_visual_debug.py tests/test_multistorey_benchmark.py` passed **10/10** tests in 12.05 s.  `git diff --check` passed; Git emitted only pre-existing LF-to-CRLF working-copy notices for `src/visualization_export.py` and `viewer/index.html`.  No commit or push was made.

## External references

* [buildingSMART Community Sample Test Files](https://github.com/buildingsmart-community/Community-Sample-Test-Files)
* [BIMData IFC sample catalogue](https://github.com/bimdata/BIMData-Research-and-Development/blob/master/pages/IFC_FILES.md)
* [BIMcollab example-project documentation](https://helpcenter.bimcollab.com/en/articles/325099-example-projects-and-templates)

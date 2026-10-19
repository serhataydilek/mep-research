# Phase 10R-B3.4: Unit Scale and Routing Readiness

## Root cause and correction

**Confirmed exporter bug:** `ifcopenshell.geom.create_shape(... USE_WORLD_COORDS=True)` already returned SI/world-metre geometry. `src.medical_dental_ifc` then multiplied vertices by `ifcopenshell.util.unit.calculate_unit_scale`. That second multiplication was harmless for metre-authored architecture/structure/electrical (`1.0`) but shrank HVAC and plumbing by a second `0.001` factor.

The exporter now preserves the kernel vertex values exactly. No source IFC changed, no GlobalId changed, and no discipline-specific registration transform was introduced. Existing cached HVAC/plumbing progressive chunks were corrected by the reversible inverse of the confirmed erroneous export factor; their chunk hashes were regenerated. The manifest records this correction explicitly.

| Layer | Project scale | Before world bounds (m) | Corrected world bounds (m) |
|---|---:|---|---|
| Architecture | 1 | x -50.742..0.113; y -0.113..55.910; z 0..2.000 | unchanged |
| Structure | 1 | x -52.016..2.196; y -19.096..56.256; z -1.370..7.261 | unchanged |
| HVAC | 0.001 | x -0.0516..-0.0002; y 0.0006..0.0558; z -0.00015..0.00448 | x -51.606..-0.201; y 0.644..55.820; z -0.150..4.485 |
| Plumbing | 0.001 | x -0.0538..0.0004; y -0.0070..0.0580; z -0.00068..0.00504 | x -53.758..0.435; y -7.017..57.961; z -0.684..5.043 |
| Electrical | 1 | x -52.200..0.167; y -0.167..56.307; z 0..4.468 | unchanged |

The corrected representative extents now agree in order of magnitude, spatial range, and floor/ceiling Z range. This is independent quantitative evidence beyond bounding-box overlap: the only former three-order discrepancy exactly equals the twice-applied IFC project scale.

## Federation verdict

**PARTIALLY VERIFIED.** All five disciplines now share a plausible metre-scale world frame without arbitrary transforms. The dataset supports visual federation and real-geometry obstacle inspection. It is not fully verified: shared control points, exporter placement documentation, and semantic opening/penetration authorization are absent.

## Routing readiness

The First Floor candidate region is approximately `x=-51.6..0.1`, `y=0.6..55.8`, `z=-0.15..4.48` for HVAC, within architecture/structure coverage. It contains real architecture/structure and existing MEP geometry, but is not yet a legitimate routing request: native port relationships do not automatically identify a new source-to-terminal demand; existing routes must not be removed without a documented replacement scope; openings and clearance bodies remain unqualified; and a bounded voxel policy is not approved.

A separately labelled real-geometry-derived controlled experiment may later select reproducible free-space endpoints only after a documented obstacle/clearance and region policy. It must not be called native IFC connectivity ground truth.

## Verification

`tests/test_medical_dental_unit_scale.py` protects against multiplying already-SI kernel coordinates by an IFC project factor. Focused tests and `git diff --check` were run. The progressive viewer remains launched/inspectable through its chunked manifest; no routing was executed.

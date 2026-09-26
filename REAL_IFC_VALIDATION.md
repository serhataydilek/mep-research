# Real IFC Sanity Validation

## Purpose

This post-research harness measures how far the completed IFC-based prototype can process supplied building models. It does not add a research phase, change routing algorithms, alter Phase 8 metrics, or claim general IFC support.

## Existing IFC flow

- `src/ifc_model.py` generates an IFC4 project/site/building/storey hierarchy in metres, with slabs, orthogonal walls, columns, a named service-shaft space, and slab openings.
- IfcOpenShell 0.8.5 provides parsing, project-unit interpretation, world-coordinate geometry creation, and tessellation.
- `src/voxel.py` regenerates the controlled IFC from configuration, tessellates with `USE_WORLD_COORDS`, and extracts wall/column AABBs. Walls are classified by synthetic names and exactly one `Service Shaft 0` space supplies the reserved shaft bounds.
- The validation inventory covers slabs, walls, columns, beams, roofs, doors, windows, spaces, openings, building-element proxies, pipe/duct/cable-carrier segments and fittings, and their IFC flow/distribution supertypes. Supertype counts intentionally overlap subtype counts.
- The controlled routing grid comes from configuration rather than arbitrary IFC extents. Its AABB rasterization is exact for the orthogonal benchmark, not rotated or curved real geometry.
- The controlled generator assumes IFC4, metres, explicit nested local placements, configured storey elevations, one controlled floor structure in the current configuration, and deterministic containment/naming.

## Run

Generate the regression control, then inspect one or more files:

```powershell
python -m src.building --config config/building.json --output out/ifc/architecture.ifc
python -m src.ifc_validation --ifc out/ifc/architecture.ifc C:\path\external.ifc --control-generated out/ifc/architecture.ifc --output validation/results/real_ifc_sanity.json --markdown validation/results/real_ifc_sanity.md
```

Results are deterministic for the same paths, files, options, IfcOpenShell version, and geometry kernel. Validation results are ignored by Git; the sample manifest is tracked.

## External validation run

On 2026-09-26 the harness was run independently and as a combined matrix against three public files recorded at immutable upstream commits in `validation/ifc_samples/manifest.json`. The IFC payloads remain ignored and are not part of the repository.

| Sample | Schema | Architectural geometry | MEP inspection | Voxelization | Current router ready |
|---|---|---|---|---|---|
| Esplanades architectural project | IFC2X3 | PARTIAL: 2,609/2,611 entities usable; two missing representations | NOT_APPLICABLE | FAIL: 51,140,320 cells exceeds the 25,000,000-cell safety limit | No |
| Certification Building-Architecture | IFC4 | PARTIAL: 12/13 entities usable; one missing representation | NOT_APPLICABLE | PASS: 124,320 cells, 9,336 occupied | No |
| Medical-Dental Clinic HVAC | IFC2X3 | PASS: 263/263 inspected architectural entities (spaces) usable | PASS: 3,704/3,704 distribution entities have usable geometry | FAIL: no wall/column obstacle geometry in the discipline model | No |

The external files confirm parsing, unit interpretation, hierarchy inspection, geometry extraction, storey mapping, placement inspection, MEP inventory, and guarded voxel reasoning across IFC2X3 and IFC4. Esplanades has seven storeys, millimetre project units, large world-coordinate offsets, and 1,846 explicitly rotated local placements. The IFC4 certification model contains explicit map-conversion/CRS data and 10 rotated local placements. The HVAC model has four storeys, 1,548 `IfcFlowSegment` entities, 1,590 `IfcFlowFitting` entities, and 1,550 rotated local placements; IFC2X3 represents these through generic flow classes rather than IFC4 pipe/duct subclasses.

These are validation-harness compatibility results. They do not mean that the research router can consume the models directly. None supplies compatible routing endpoints, Esplanades is unsafe for dense 0.1 m voxel allocation under the configured limit, and the HVAC discipline file contains no current-policy wall/column obstacles.

## Stages

The report covers parsing, schema, units, hierarchy, inheritance-aware entity inventory, world-coordinate geometry, placements and storeys, compatibility with current wall/column obstacle semantics, guarded validation-only voxelization, existing MEP inspection, and explicit router-readiness conditions. Stage statuses are `PASS`, `PARTIAL`, `FAIL`, or `NOT_APPLICABLE`.

## Limitations

- No external IFC is bundled. Reproducing the external results requires downloading the files recorded in the sample manifest.
- Validation voxelization uses current-policy wall/column AABBs and a reported grid-origin transform. It does not replace benchmark voxel semantics.
- Rotated and curved elements can be over-approximated by AABBs.
- Existing distribution elements are inspected but cannot be consumed directly as routing demands.
- The harness never invents routing endpoints. A parsed model can legitimately report `router_ready = false`.
- Georeferencing is reported; arbitrary recentering is not applied.

## Research boundary

The completed Phase 8 benchmark remains a controlled synthetic experiment. External-file failures identify future product-engineering work and do not retroactively change its results or conclusions.

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

## Stages

The report covers parsing, schema, units, hierarchy, inheritance-aware entity inventory, world-coordinate geometry, placements and storeys, compatibility with current wall/column obstacle semantics, guarded validation-only voxelization, existing MEP inspection, and explicit router-readiness conditions. Stage statuses are `PASS`, `PARTIAL`, `FAIL`, or `NOT_APPLICABLE`.

## Limitations

- No external IFC is bundled. External compatibility is not validated until independently sourced files are supplied and recorded in the sample manifest.
- Validation voxelization uses current-policy wall/column AABBs and a reported grid-origin transform. It does not replace benchmark voxel semantics.
- Rotated and curved elements can be over-approximated by AABBs.
- Existing distribution elements are inspected but cannot be consumed directly as routing demands.
- The harness never invents routing endpoints. A parsed model can legitimately report `router_ready = false`.
- Georeferencing is reported; arbitrary recentering is not applied.

## Research boundary

The completed Phase 8 benchmark remains a controlled synthetic experiment. External-file failures identify future product-engineering work and do not retroactively change its results or conclusions.

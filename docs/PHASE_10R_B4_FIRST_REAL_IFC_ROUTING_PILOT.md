# Phase 10R-B4: First Real-Geometry Routing Pilot Gate

## Decision

**No routing execution was authorized.** This is an evidence-based stop, not a search failure or a claim of infeasibility. The corrected real geometry is suitable for inspection, but a defensible routing request and obstacle/replacement policy are not yet established.

## Coordinate sanity check

After removal of the confirmed double project-scale conversion, First Floor world bounds are compatible in magnitude:

| Discipline | Bounds (m) |
|---|---|
| Architecture | x -50.742..0.113; y -0.113..55.910; z 0.000..2.000 |
| Structure | x -52.016..2.196; y -19.096..56.256; z -1.370..7.261 |
| HVAC | x -51.606..-0.201; y 0.644..55.820; z -0.150..4.485 |
| Plumbing | x -53.758..0.435; y -7.017..57.961; z -0.684..5.043 |
| Electrical | x -52.200..0.167; y -0.167..56.307; z 0.000..4.468 |

This is sufficient to reject the previous millimetre-scale export bug. It does **not** by itself verify every placement chain, penetrations, or clearance relationship.

## Native request evidence

`Clinic_HVAC.ifc` contains 7,390 `IfcDistributionPort` and 3,695 explicit `IfcRelConnectsPorts` relationships. Direct inspection of ten representative relationships found every port's own `ObjectPlacement` origin to be `(0,0,0)`. This is expected for nested/hosted ports but means the port entity alone cannot provide an endpoint. A valid request needs the relationship-provenanced host element, resolved nested placement, host geometry, system/terminal role, and a documented replacement scope.

Those host-placement and source-to-terminal records have not been produced for this dataset. Treating arbitrary connected ports as a new request would be invalid; using arbitrary free-space points would be a separate controlled experiment, not native IFC evidence.

## Candidate real region and blockers

The physically interesting First Floor overlap envelope is approximately `x=-51.6..0.1`, `y=0.6..55.8`, `z=-0.15..4.48`. It contains complete exported architectural/structural and MEP source geometry, but it is too broad for a voxel experiment. A bounded region cannot be selected responsibly until a relationship-provenanced port-host pair is located and an exact surrounding obstacle set is defined.

The existing routing engine accepts a `GridSpec`/`OccupancyGrid`, but no approved conversion exists for this model's architectural/structural openings, MEP exclusion scope, body dimensions, or clearance. AABB voxelization alone would overblock/open incorrectly and can create self-collision unless only exact hosts from the documented replacement path are excluded.

## Required next gate

Implement a read-only clinic host/port resolver that records: port GlobalId, host GlobalId, full nested placement in original metres, named system, source/terminal classification, and only explicit `IfcRelConnectsPorts` traversal. Then select one compact port-host pair plus a declared replacement-host set; extract an obstacle policy and voxel memory estimate; and only then run unchanged `src.routing.astar_3d` and the unchanged validator. No frozen input, router, or validator was modified here.

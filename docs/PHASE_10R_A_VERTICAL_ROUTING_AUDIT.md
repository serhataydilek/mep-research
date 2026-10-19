# Phase 10R-A — Vertical Routing and Benchmark Realism Audit

## Integrity and evidence boundary

This is a read-only audit. No frozen 12-case input, router, validator,
historical result, or metric was modified. Measurements were reproduced from
`experiments/scenarios`, `experiments/demands`, `config/mep_systems.json`, and
the unchanged B0 implementation at Git `7cb5df82e8f22d23be71e73f347c48d9c4e027cd`.
Persisted Phase 8 totals are cited only for the other frozen methods. Every
`sNN-dNN` entry below remains an independent scenario-demand case.

## Conclusion

All 292 B0 routes are found, and all 292 have zero vertical travel. This is
not because the grid has no vertical cells: free layers exist in each scenario.
It is primarily the consequence of assigning every generated endpoint in a
scenario the same midpoint Z, then minimizing unit-cost Manhattan distance. A
route that rises and returns is strictly longer whenever a same-Z XY path
exists. Full-height obstacle AABBs also give no over/under alternative.

The frozen benchmark is therefore a 3D navigable grid with a largely 2.5D
demand and obstacle model. This finding is a modeling assessment, not a proof
of physical infeasibility.

## Reproduction

```powershell
$env:PYTHONPATH='.'
python -m src.routing --scenarios experiments/scenarios --demands experiments/demands --systems config/mep_systems.json --output $env:TEMP\phase10r-a-b0.json
python -m src.constructability --scenarios experiments/scenarios --demands experiments/demands --systems config/mep_systems.json --constraints config/constructability.json --output $env:TEMP\phase10r-a-c0.json
```

These commands regenerate evidence to a temporary location; they do not
replace historical outputs. The audit also built the existing service-specific
occupancy grids in memory and counted every Z layer.

## Geometry, endpoints, and routing volume

The grid is one floor's plenum: `slab_thickness + ceiling_height` through
`floor_to_floor`. All voxels are 0.1 m. `_routing_z` assigns the arithmetic
plenum midpoint to every source, egress, and terminal anchor regardless of
system; nearest-free-cell snapping produces the final B0 Z.

| Scenario | Plenum / raw grid Z (m) | Anchor Z | B0 snapped Z | Fixed full-height objects |
|---|---:|---:|---:|---|
| s01 | 1.60 / 2.90–4.50 | 3.70 | 3.65 | perimeter + shaft walls, 2 columns |
| s02 | 1.00 / 3.00–4.00 | 3.50 | 3.55 | perimeter + shaft walls, 6 columns |
| s03 | 0.60 / 3.10–3.70 | 3.40 | 3.45 | perimeter + shaft walls, 16 columns |
| s04 | 0.50 / 3.00–3.50 | 3.25 | 3.25 | perimeter + shaft walls, 16 columns |

Extracted walls/columns run from slab top (0.20 m) to floor-to-floor top; the
shaft reservation runs from 0 to floor-to-floor top. Their XY footprints are
blocked at every usable Z. Slabs exist in the IFC but the grid begins above the
ceiling; no beams, explicit ceiling-void solids, service bodies, or cross-floor
riser connectivity are modeled.

## Usable vertical space after envelope and clearance

Grid boundaries are blocked using nominal vertical half-extent + 0.05 m
clearance: HVAC 0.175 m, drainage/electrical 0.100 m, water 0.075 m, and fire
0.070 m. Intervals are free voxel-centre Z values; free layers do not mean all
XY cells are free.

| Scenario | HVAC | Drainage | Water | Fire | Electrical |
|---|---|---|---|---|---|
| s01 | 12 layers, 3.15–4.25 | 14, 3.05–4.35 | 14, 3.05–4.35 | 14, 3.05–4.35 | 14, 3.05–4.35 |
| s02 | 6 layers, 3.25–3.75 | 8, 3.15–3.85 | 8, 3.15–3.85 | 8, 3.15–3.85 | 8, 3.15–3.85 |
| s03 | 2 layers, 3.35–3.45 | 4, 3.25–3.55 | 4, 3.25–3.55 | 4, 3.25–3.55 | 4, 3.25–3.55 |
| s04 | 1 layer, 3.25 | 3, 3.15–3.35 | 3, 3.15–3.35 | 3, 3.15–3.35 | 3, 3.15–3.35 |

s03/s04 provide little stacking headroom, especially for HVAC. s01 nevertheless
has 12 HVAC and 14 smaller-service free layers, so its zero B0 vertical travel
cannot be attributed to missing vertical capacity.

## Route-level B0 elevation distribution

All B0 routes succeed. `n/cells@z` gives route count, all route path-cell
occupancy at that sole Z, and the route-level min=max Z. Thus every route in
this table has `vertical_travel_m = 0.0`; no vertical route is omitted.

| Case | HVAC | Drainage | Water | Fire | Electrical |
|---|---|---|---|---|---|
| s01-d01 | 3/454@3.65 | 2/350@3.65 | 2/287@3.65 | 3/477@3.65 | 3/355@3.65 |
| s01-d02 | 5/709@3.65 | 4/515@3.65 | 4/504@3.65 | 6/934@3.65 | 5/705@3.65 |
| s01-d03 | 8/1181@3.65 | 6/572@3.65 | 6/800@3.65 | 8/1135@3.65 | 8/1241@3.65 |
| s02-d01 | 3/410@3.55 | 2/267@3.55 | 2/232@3.55 | 3/313@3.55 | 3/271@3.55 |
| s02-d02 | 5/669@3.55 | 4/429@3.55 | 4/412@3.55 | 6/672@3.55 | 5/496@3.55 |
| s02-d03 | 8/869@3.55 | 6/555@3.55 | 6/637@3.55 | 8/800@3.55 | 8/749@3.55 |
| s03-d01 | 3/377@3.45 | 2/241@3.45 | 2/246@3.45 | 3/341@3.45 | 3/323@3.45 |
| s03-d02 | 5/610@3.45 | 4/344@3.45 | 4/541@3.45 | 6/580@3.45 | 5/546@3.45 |
| s03-d03 | 8/1041@3.45 | 6/650@3.45 | 6/722@3.45 | 8/728@3.45 | 8/884@3.45 |
| s04-d01 | 3/394@3.25 | 2/253@3.25 | 2/278@3.25 | 3/461@3.25 | 3/429@3.25 |
| s04-d02 | 5/724@3.25 | 4/414@3.25 | 4/517@3.25 | 6/736@3.25 | 5/635@3.25 |
| s04-d03 | 8/1037@3.25 | 6/721@3.25 | 6/651@3.25 | 8/896@3.25 | 8/1049@3.25 |

This is direct evidence of artificial elevation concentration across HVAC,
drainage, water, fire, and electrical in B0.

## What discourages or prevents Z movement

Measured constraints are: six-neighbour A* includes Z (it is not disabled);
each Z step costs one voxel; all endpoints in a scenario have equal Z; and
horizontal offsets are evaluated before Z offsets in otherwise tied expansion.
An up/down pair costs at least two extra steps. Obstacles are vertically uniform,
and B0 has no prior-route reservation or system-elevation preference.

Model limitations are equally important: no system-specific elevations/riser
requirements, named zones, beams, partial-height obstacles, existing MEP,
oriented bodies, fittings, supports, access zones, or continuous-solid check.
Baseline B0 does not apply drainage slope during search.

## Other frozen methods

The persisted Phase 8 synthesis proves that Z moves are possible when
coordination changes occupancy or invokes benchmark-only vertical endpoint
flexibility. These are independent case results, not a single building.

| Method | Successful / requested | Total vertical travel (m) |
|---|---:|---:|
| B0 | 292 / 292 | 0.0 |
| B1 | 144 / 292 | 12.8 |
| B2 | 292 / 292 | 11.6 |
| P-CORE-SEED | 292 / 292 | 7.4 |
| P-CORE-6C2B | 292 / 292 | 17.7 |

Per-system vertical totals (B0 / B1 / B2 / seed / 6C2B, metres): HVAC
`0 / 0 / 0.6 / 0 / 0.4`; drainage `0 / 1.2 / 1.4 / 7.4 / 7.5`; water
`0 / 0 / 3.4 / 0 / 3.6`; fire `0 / 11.6 / 6.2 / 0 / 2.8`; electrical
`0 / 0 / 0 / 0 / 3.4`. These remain grid-centreline metrics, not
constructability certificates.

## Validator, failure classification, and Phase 10Q

C0 is a controlled benchmark validator, not the future independent geometric
validator. In the reproduced B0 run, 292/292 grid paths exist. All 48/48
drainage routes fail the 1% net-slope check because their net Z drop is zero;
none is uphill. Every B0 case also has inter-system hard and clearance
violations. Those are validation rejections of routed paths, not a finding of
insufficient vertical space.

The claimed Phase 10Q record that “45 geometric paths failed the unchanged
validator” is not present in this checkout. Searches of source, `out/`,
`validation/`, tools, logs, branches, and artifacts found no Phase 10Q export,
route IDs, validator schema/version, or matching failure records. It would be
unsound to attribute the 45 failures to Z space or to classify them without
that evidence.

| Classification | Meaning here |
|---|---|
| Physical infeasibility | Not established by B1 failure, B0 `NOT_FOUND`, or C0 rejection; needs a declared geometry/constraint proof. |
| Modeling limitation | Equal-Z anchors, full-height AABBs, missing zones/beams/MEP solids/exact bodies. |
| Validation rejection | Path exists but violates a declared condition, e.g. B0 drainage slope or inter-system conflict. |
| Search failure | No route under a specific occupancy/search state. B1's 148 failures are reservation-state failures, not physical impossibility. |

To audit Phase 10Q, provide its versioned route artifact, validator version,
input hashes, route IDs, and per-route reason/location. Then rerun the
unchanged validator and join each record to Z span, free layers, obstacles, and
endpoint transformations.

## Viewer fidelity

The Phase 10R viewer accurately renders the authoritative world values present
in its selected fixture: exported obstacle AABBs, voxel-centre polylines,
endpoints, conflict proxy boxes, and explicit cell-to-world mapping. The export
validator checks first/last cells against world coordinates. Clipping is visual
only. The viewer does not re-run routing and does not represent all 12 B0
cases, exact solids, fittings, oriented bodies, supports, access zones, or a
continuous clash certificate. It is coordinate-faithful within its export
contract, not a physical-volume validator.

## Separate realistic benchmark specification — proposal only

Create a new versioned benchmark family; never mutate or directly score it
against the frozen 12 cases.

| Feature | Proposal | Existing engine status |
|---|---|---|
| Floors and risers | 3–6 named storeys, cross-floor requests, permitted riser cells | `floors` exists in IFC generation; routing/evidence is single-plenum and needs implementation. |
| Dimensions/zones | Finished floor, slab, ceiling, named routing zones per storey | Current scalar plenum is partial; zones need implementation. |
| Structure | Storey-specific slabs, beams, columns, walls; partial-height alternatives | Walls/columns/slabs are generated; routing extracts only walls/columns. Beams/zone occupancy need implementation. |
| Shafts | Explicit permitted risers and approved penetrations | Current shaft is reserved, not traversable; needs implementation. |
| Endpoints | Versioned source/target elevations and allowable system bands | Endpoint model stores world coordinates; generator needs extension. |
| Drainage | Invert levels, min slope, monotonic/fitting rules during search | Limited grid gravity routing/C0 exists; full drainage design needs implementation. |
| Envelopes | Per-system bodies, pairwise clearance, orientation, fittings | Nominal envelopes/policy exist; exact oriented geometry and fittings need implementation. |
| Reproducibility | Immutable manifests, hashes, generator/engine identity | Existing canonical JSON and Phase 10R provenance can be reused. |
| Viewer | Storey/zone/elevation filters and validator disposition | Current viewer can render exports; multistorey/exact layers need implementation. |

Initial separate scenarios should include: a two-storey office with a 1.2–1.5 m
void and beam bands; a three-storey drainage case; and a constrained case where
a vertical reroute is necessary but feasible. Each needs immutable geometry,
endpoint, occupancy, expected-status, and viewer fixtures.

## Root cause, uncertainty, and Phase 10R-B plan

**Root cause — high confidence:** B0's concentration comes from common
midpoint endpoint Z plus shortest-path cost, amplified by vertically uniform
obstacle footprints and no discipline elevation requirements. Z neighbours are
available. **Secondary constraint — high confidence:** s03/s04 have narrow
post-clearance vertical bands, especially HVAC. **Uncertain:** the cause of the
separate 45 Phase 10Q failures; the artifact is absent.

Phase 10R-B should: (1) obtain/hash/replay Phase 10Q artifacts and publish
per-route dispositions; (2) add read-only per-method/case Z and validator
audit exports without changing metrics; (3) define a new benchmark schema;
(4) implement its generator/evidence/export path under new IDs only;
(5) add multistorey zones, beams/slabs, permitted risers and transform tests;
(6) extend viewer layers after authoritative exports exist; and (7) report the
new experiment separately from Phase 8/10 history.

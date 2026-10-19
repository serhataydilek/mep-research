# Phase 10R-B — Realistic Multistorey MEP Benchmark

## Scope and status

This is a separate versioned namespace, `PHASE10R_B_MULTISTOREY_V1`. It does
not modify frozen scenario manifests, demand profiles, routing algorithms,
validator semantics, or Phase 8 results. The adapter calls unchanged
`astar_3d` for HVAC, water, fire, and electrical and unchanged `gravity_route`
for drainage.

`VALIDATED_GRID_AABB_PROXY` means every request was routed and the declared
grid-cell/swept-AABB checks found no inter-system overlap. It is not a claim of
physical feasibility, mathematical feasibility/infeasibility, engineering
compliance, fitting feasibility, or continuous-solid clearance.

## Assumptions and geometry

- Two storeys at 3.5 m floor-to-floor; 0.20 m slabs; 2.70 m ceiling height;
  0.60 m ceiling service zone per storey.
- World units are metres. The service zones are `z=2.90–3.50 m` and
  `z=6.40–7.00 m`.
- A 3.0 m by 3.0 m continuous shaft has an explicit intermediate slab opening.
  It is the only legal vertical connection between zones.
- Perimeter and shaft-enclosure walls, slabs, columns, two beams, room/door-opening metadata,
  service zones, and the shaft are explicit world-coordinate geometry.
- Routing is allowed only inside a named service zone or shaft, then blocked by
  service-inflated architectural AABBs. Existing nominal service dimensions and
  0.05 m clearance are reused as routing-envelope assumptions only.
- Drainage is terminal-to-source and calls the existing 1% gravity primitive.
  Its controlled fixture route has 3.5 m net drop over 8.0 m horizontal run
  (43.75% effective slope): valid under the existing rule, not representative
  drainage design guidance.

## Fixtures and measured baseline

| Fixture | Controlled intent | Measured outcome | Interpretation |
|---|---|---|---|
| A `10rb-a-known-feasible` | Separated shaft lanes/system elevations | 5/5 routed; 0 proxy hard/clearance conflicts; gravity passes | Valid only under declared grid/AABB rules. |
| B `10rb-b-constrained` | 1.0 m corridor and shared shaft lane | 5/5 routed; 10 proxy conflict records | Constrained diagnostic fixture; not labelled infeasible. |

Every connection crosses floors and has 3.5 m vertical travel. Fixture A route
lengths: HVAC/electrical 12.5 m; drainage/water/fire 11.5 m. Fixture B has
11.5 m for every route. These are grid-centreline measurements. Exported
fixtures carry completion, lengths, vertical travel, path cells, per-route Z
occupancy, drainage metrics, conflicts, endpoint snaps, source hashes, and the
cell-to-world transform.

`B0` and the drainage gravity primitive run unchanged. Frozen B1, B2, and
P-CORE entry points deliberately require the legacy 12-case manifests/grids;
they expose no generic-fixture interface. They were not silently copied or
reinterpreted. A future extension must preserve their existing semantics.

Classifications are explicit: `SEARCH_FAILURE` is no path under this fixture's
service-inflated occupancy; `VALIDATION_REJECTION` is continuity or gravity
failure after routing; and `CONSTRAINED_CONFLICTS_DETECTED` is a path set with
proxy conflicts, never a proof of infeasibility.

## Run and inspect

```powershell
$env:PYTHONPATH='.'
python -m src.multistorey_benchmark --fixture experiments/multistorey/fixture_a_known_feasible.json --output out/phase10rb/fixture_a_baseline.json
python -m src.multistorey_benchmark --fixture experiments/multistorey/fixture_b_constrained.json --output out/phase10rb/fixture_b_baseline.json
python -m http.server 8000 -d viewer
```

Open `http://localhost:8000`, then select either Phase 10R-B fixture. The
viewer uses exported world coordinates directly and displays architectural
obstacle AABBs, endpoints, system toggles, clearance envelopes, conflicts,
shaft/slab-opening outlines, orbit/pan/zoom, and top-elevation clipping.

## Verification

`tests/test_multistorey_benchmark.py` checks two-storey geometry, openings,
walls/columns/beams/slabs, globally valid Fixture A proxy result, Fixture B
status, drainage gravity, deterministic bytes, cell/world mapping, and viewer
integration. It does not assert engineering feasibility.

## Phase 10R-C readiness

Not ready for production/manual-layout claims. It is ready as a read-only,
deterministic diagnostic target with explicit coordinates and honest proxy
validation. Manual waypoint editing must wait for waypoint provenance,
editable-zone authorization, deterministic revalidation, route ownership,
drainage preservation, and a continuous geometry/fitting validation boundary.

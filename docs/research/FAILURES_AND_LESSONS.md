# Failures, corrections, and lessons

## Verified negative findings

1. **Completeness versus conflict reduction.** B0 completed all 292 synthetic
   requests but recorded 1,256 hard conflicts; B1 recorded zero observed
   inter-system conflicts only by leaving 148 requests unrouted. The trade-off
   is preserved in [`../../experiments/results/phase8_final_synthesis.json`](../../experiments/results/phase8_final_synthesis.json).
2. **P-CORE was not clash-free.** P-CORE-6C2B reduced the hard-conflict count
   to 747 while retaining 292/292 routes; this is a reduction, not proof of
   physical feasibility.
3. **C0 did not establish engineering approval.** Final metrics report zero
   hard-feasible C0 cases. The check is intentionally simplified.
4. **Real IFC parsing is not route readiness.** Unit, placement, geometry,
   obstacle and endpoint semantics can block use even when an IFC file renders.
5. **Phase 11A is invalidated.** Its saved polyline connects voxel centres
   after endpoint displacement, rather than original IFC ports.
6. **Phase 11A.1 preserved endpoint-blocked evidence.** The correction stopped
   the free-cell relocation that could otherwise hide endpoint failure.
7. **Phase 11A.2 / 11B V1 is not acceptable.** A found A* path has a recorded
   clearance violation. Broad-phase candidates merely select meshes for the
   local distance test; they are not collisions.
8. **Four Phase 11B requests have no route.** H1, H2, S1 and S2 are genuine
   `SEARCH_NO_PATH` observations, not omitted visualizations.

## Demonstrated root causes or mechanisms

- The historical proxy failure is demonstrated by its preserved label, which
  states that ports were moved and the result connected voxel centres.
- The V1 clearance failure is demonstrated by saved mesh-distance evidence:
  a retained ventilation host and an architectural covering were below the
  0.105 m required body-envelope separation. It is not a confirmed mesh
  intersection.
- Source-unit conversion is a demonstrated risk for millimetre-authored IFC:
  `USE_WORLD_COORDS` geometry is already SI metres and must not receive a
  second project-scale multiplication. See
  [`../PHASE_10R_B3_4_UNIT_SCALE_AND_ROUTING_READINESS.md`](../PHASE_10R_B3_4_UNIT_SCALE_AND_ROUTING_READINESS.md).

## Open limitations

No actual multi-system real-IFC coordination, generated fitting solids,
penetration authorization, code compliance, fabrication validation, or
general arbitrary-IFC routing has been demonstrated. Screenshots show a viewer
state only; they do not independently validate collision freedom.

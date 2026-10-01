# Current Research Baseline

## Lineage and reproducibility

The completed baseline comes from `https://github.com/serhataydilek/mep-research`, `main` at `72a511f55d249f41f289bcb8f0212213cbb277d1`, tagged `research-v1.0`. It contains 36 reachable commits and was migrated unchanged into this repository before documentation was added. The source clone was clean before and after inspection.

The release uses Python 3.14.3, `ifcopenshell==0.8.5`, and `pytest==9.1.1`. It has 20 `test_*.py` modules and 170 collected tests. Scenarios, demand ordering, system ordering, GUID construction, A* tie-breaking, coordinator rounds, and JSON serialization are deterministic.

## Benchmark inputs

Four single-storey controlled architectural scenarios (`s01` low through `s04` extreme) cross three nested star-topology demand profiles: `d01=13`, `d02=24`, and `d03=36` requests. The resulting 12 cases contain 292 requests. The five nominal systems are HVAC (0.4 × 0.25 m rectangular), drainage (0.10 m circular), water (0.05 m circular), fire (0.04 m circular), and electrical (0.3 × 0.1 m rectangular); grid resolution is 0.1 m and configured clearance is 0.05 m.

A deterministic source anchor is inside the shaft for each system. A deterministic room-side egress anchor and fixed breakout record link the source to the routing domain. A* routes `egress ↔ terminal`; the source-to-egress crossing is metadata, not optimized penetration geometry. Terminals are synthetic, service-aware lattice selections, not extracted BIM connectors.

## Implemented pipeline and methods

1. `ifc_model.py` generates controlled IFC4 architecture. `voxel.py` tessellates it, extracts orthogonal wall/column AABBs, reserves the named shaft, applies service margins, and creates grids.
2. **B0** uses six orthogonal neighbours, Manhattan heuristic, integer step cost, and stable heap ordering. Each request uses an immutable service grid; it intentionally makes no MEP reservation.
3. **B1** runs HVAC → drainage → water → fire → electrical. Successful earlier systems reserve pair-specific envelope plus 0.05 m clearance on copied lower-system grids. Same-system requests intentionally remain independent.
4. `clash.py` compresses centerlines, sweeps nominal and clearance-inflated AABBs, and reports only inter-system pairs as primary metrics. Same-system overlaps are diagnostic.
5. **B2** diagnoses a conflict graph, takes a deterministic greedy edge-cover candidate set, freezes non-candidates, reroutes candidates once, and retains an original route after failed repair.
6. Gravity drainage routes terminal-to-egress with no uphill voxel step and a 1% aggregate stair-step check. **P-CORE-SEED** replaces B0 drainage with this primitive.
7. **P-CORE-6C2B** repeats atomic repair rounds up to six times. A complete trial is accepted only on strict lexicographic improvement of `(hard_conflicting_route_pair_count, clearance_violating_route_pair_count)`; otherwise it is rolled back. It minimizes conflict burden, not feasibility-by-zero-conflict.
8. Comparison, stratification, Pareto analysis, final synthesis, and guarded external IFC sanity validation report evidence without changing routes.

## Verified release totals

| Method | Completed | Hard | Clearance | Length m | Bends | Vertical m |
|---|---:|---:|---:|---:|---:|---:|
| B0 | 292/292 | 1256 | 1257 | 3507.7 | 572 | 0.0 |
| B1 | 144/292 | 0 | 0 | 2516.6 | 603 | 12.8 |
| B2 | 292/292 | 936 | 941 | 3962.3 | 803 | 11.6 |
| P-CORE-SEED | 292/292 | 1020 | 1209 | 3515.1 | 620 | 7.4 |
| P-CORE-6C2B | 292/292 | 747 | 926 | 3662.9 | 804 | 17.7 |

Seed to final P-CORE changes are −26.8% hard conflicts, −23.4% clearance violations, +4.2% length, +29.7% bends, and +139.2% vertical travel. These results demonstrate a coverage/conflict/geometry trade-off in the controlled benchmark only.

## External IFC sanity validation

The harness parses, examines units/hierarchy/placements/geometry, inventories distribution entities, and performs guarded current-policy voxel reasoning. It does not route arbitrary IFCs. Esplanades IFC2X3 was refused at 51,140,320 dense cells (25,000,000 safety limit); Building-Architecture IFC4 safely voxelized to 124,320 cells but lacked router inputs; Medical-Dental Clinic HVAC IFC2X3 exposed 3,704 distribution entities but had no current-policy wall/column obstacle geometry or compatible endpoints. See [REAL_IFC_VALIDATION.md](../REAL_IFC_VALIDATION.md).

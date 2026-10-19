# Phase 11A.1: Endpoint-to-grid geometry integrity

## Result

The Phase 11A heating route did not connect the two IFC ports. `offset_into_replacement` moved the start port from Z layer 18 into layer 17, and `nearest_free_cell` then moved the blocked goal cell from layer 15 onto the same free layer 17. The stored polyline kept only those voxel centres, both at z = 0.05 m.

The port coordinates themselves are consistent. Both are `NATIVE_PORT_PLACEMENT`, the heating length scale 0.001 is applied once, and the unclamped containing cells are Z 18 and Z 15. Those cells are blocked by retained elements, so the corrected run does not call A* and does not invent a substitute terminal.

| | Phase 11A proxy route | Phase 11A.1 containing cell |
|---|---|---|
| Start port (m) | (50.245955, 15.827534, 0.100) | same immutable point |
| Goal port (m) | (50.149955, 14.687534, -0.120) | same immutable point |
| Port Z difference | 0.220 m, signed end − start = −0.220 m | unchanged |
| Start grid index | search node (15, 26, 17) | containing cell (16, 27, 18), blocked |
| Goal grid index | search node (15, 16, 17) | containing cell (15, 15, 15), blocked |
| Route polyline Z | 0.05 m at every vertex | no polyline emitted |
| Grid vertical travel | 0.0 m, 0 vertical steps | A* not invoked |
| Physical port-to-port | no | no |

The previous execution, request, and occupancy files are labeled `PHASE_11A_HISTORICAL_PROXY_ROUTE` in `experiments/digitalhub_v1/historical_phase11a/`.

## What the coordinates show

Heating project length scale is 0.001. Each port placement chain is composed by `get_local_placement` and scaled once.

| Stage | Start | Goal |
|---|---|---|
| Classification | `NATIVE_PORT_PLACEMENT` | `NATIVE_PORT_PLACEMENT` |
| Composed world (m), one scale | (50.24595470506687, 15.8275341260543, 0.100) | (50.1499547050666, 14.687534126054317, −0.119999976544588) |
| Rejected double-scaled world (m) | z = 0.0001 | z = −0.000120 |
| Request coordinate after 6-decimal rounding (m) | (50.245955, 15.827534, 0.100) | (50.149955, 14.687534, −0.120) |
| ROI origin (m) | (48.6, 13.100000000000001, −1.7000000000000002) | same |
| `(world − origin) / 0.1` | (16.45955, 27.27534, 18.0) | (15.49955, 15.87534, 15.8) |
| Containing index | (16, 27, 18) | (15, 15, 15) |
| Cell centre (m) | (50.25, 15.85, 0.15) | (50.15, 14.65, −0.15) |

The 1 nm floor epsilon does not move either index: the start quotient is 18.0 and the goal quotient is 15.8. Both indices sit inside the 35 × 62 × 39 grid, so no axis was clamped. Rounding the goal from −0.119999976544588 m to −0.120 m stays in the same cell. The absolute Z separation of the composed points is 0.219999976544588 m.

## Cause

Phase 11A did two extra moves after the mapping above, and neither move is in the polyline.

1. `offset_into_replacement` by 0.109927 m toward the replaced-host centroid produced the proxies (50.224799, 15.734954, 0.044635) and (50.152100, 14.797433, −0.118746). The start proxy falls in cell (16, 26, 17). The goal proxy stays in Z layer 15, cell (15, 16, 15).
2. `nearest_free_cell` clamps, then searches Manhattan shells for any free cell. The start proxy cell was blocked, so the search node became free cell (15, 26, 17), 0.076485 m from the proxy. The goal proxy cell and its radius-1 neighbourhood were blocked, so the first free shell was radius 2 and selected (15, 16, 17), 0.175298 m from the proxy. Both nodes are Z 17. Their centres are z = 0.05 m.

A* then reported a true result for those proxies: 1.0 m, 10 steps, 0 bends, 0 vertical steps, `COMPLETED_PATH_FOUND`. That result does not describe the ports.

These values rule out the other listed mechanisms for the Z collapse:

- The port Z coordinates are not equal and are not in inconsistent frames. Signed difference −0.220 m.
- Origin subtraction leaves normalized Z at 18.0 and 15.8.
- A second unit conversion is not present. It would have put the ports near z = 0.0001 m and z = −0.000120 m.
- The request coordinates were not flattened. The polyline dropped the elevations later.
- Integer floor does not put the two ports in one layer.
- The raw ports do not need grid clamping.
- The historical search did relocate both terminals onto free cells. That relocation is the collapse.

## Corrected decision

`src.routing.astar_3d` and `src/voxel.py` are unchanged. The DigitalHub experiment no longer calls `offset_into_replacement` or `nearest_free_cell`. A terminal is the unclamped containing cell of its immutable port.

- Outside the grid: `ENDPOINT_OUTSIDE_GRID`.
- Containing cell blocked: `ENDPOINT_CELL_BLOCKED`, with the obstacle GlobalIds.
- Both cells free: A* runs on those indices. The physical polyline is an orthogonal X-then-Y-then-Z stub from the start port to the first voxel centre, the voxel centres, and an orthogonal stub from the last centre to the goal port. Stub length, bends, and absolute Z travel are physical metrics. Grid metrics stay on the voxel steps. Swept AABBs of the whole polyline, including stubs, are checked against obstacles. Only the six replaced hosts are excluded.

This candidate hits the blocked-cell rule on both ends, so no stub is drawn and no obstacle is opened.

The start cell (16, 27, 18) is blocked only by retained radiator `0KDl_Pd9bF6RK3eO7wIX_z` (`IfcSpaceHeater`). The port point lies inside that heater’s uninflated AABB. The heater is a boundary host, not one of the six replaced pipes.

The goal cell (15, 15, 15) is blocked by architectural AABBs:

- `3A8hY1UoD7JhnLeZeDyU2I` IfcBuildingElementProxy
- `3BmeJtEDj3AQO77Os2w6hN` IfcSlab
- `3LJODRGPbDdfXHXShFL8tL` IfcSlab
- `3LJODRGPbDdfXHXShFL93W` IfcBuildingElementProxy
- `3LJODRGPbDdfXHXShFL9C7` IfcBuildingElementProxy
- `3LJODRGPbDdfXHXShFLAnr` IfcSlab

The goal point also lies inside the uninflated AABBs of `3A8hY1UoD7JhnLeZeDyU2I`, `3LJODRGPbDdfXHXShFL93W`, `3LJODRGPbDdfXHXShFL9C7`, `3LJODRGPbDdfXHXShFLAnr`, and the retained boundary pipe `08uzusf2n75OvoeaS_v52U`. The pipe is expected at its own port. The architectural solids are not. Inflation of about 0.0099 m is not what places the point inside those architectural boxes. No opening was subtracted. None of the six replaced hosts was treated as an obstacle, and none of them is the blocker.

Search status is `NOT_RUN_ENDPOINT_INVALID`. Collision status is `NOT_RUN_ENDPOINT_INVALID`. There is no swept-AABB result to interpret, and a zero-hit broad phase is not claimed. Where a later route does record zero AABB hits, the label is `ZERO_BROAD_PHASE_CANDIDATES` and `confirmed_collision_result` stays false.

The same subpath is still the experiment. The selector did not move on to another heating, ventilation, or sanitary candidate.

## Commands and tests

```text
py -3 -m pytest tests/test_endpoint_grid.py tests/test_external_ifc_routing.py tests/test_routing.py tests/test_medical_dental_unit_scale.py -q --tb=line
```

25 passed in 4.54 s. That is the original 15 tests plus 10 endpoint-grid regressions: the 0.220 m gap stays in the physical polyline when both cells are free, world-to-grid-to-world holds for a nonzero origin, negative and boundary coordinates keep their indices, nested millimetre placements are scaled once, clamping is reported instead of applied, a blocked cell is not replaced by a free neighbour, orthogonal stubs count length and vertical travel, replaced-host exclusion stays on the six hosts, and a stub that meets an obstacle is a broad-phase hit rather than mesh clearance.

```text
py -3 tools\run_digitalhub_v1_routing.py
```

Exit code 0 after 175 s. The run classified `ENDPOINT_CELL_BLOCKED` for the same `H_RL 68` heating subpath. `astar_invoked` is false.

## Artifacts

- `experiments/digitalhub_v1/endpoint_grid_diagnostics.json`
- `experiments/digitalhub_v1/routing_request.json`
- `experiments/digitalhub_v1/occupancy_manifest.json`
- `experiments/digitalhub_v1/routing_execution.json`
- `experiments/digitalhub_v1/historical_phase11a/`

## Acceptance

- `ROOT_CAUSE: offset_into_replacement moved the start port from containing Z 18 to Z 17, then nearest_free_cell moved that blocked proxy to (15, 26, 17) and the blocked goal cell (15, 15, 15) to (15, 16, 17); the polyline kept only those centres at z=0.05. The ports themselves differ by 0.220 m and map to Z 18 and Z 15. Both containing cells are blocked, so the corrected run does not relocate them or call A*.`
- `ORIGINAL_PORT_Z_DELTA_M: -0.220`
- `START_GRID_Z_INDEX: 18`
- `GOAL_GRID_Z_INDEX: 15`
- `PHYSICAL_ROUTE_VERTICAL_TRAVEL_M: NONE`
- `EXACT_PORT_TO_PORT_CONNECTION: NO`
- `REAL_IFC_ASTAR_RERUN: YES`
- `GEOMETRIC_VALIDATION_STATUS: ENDPOINT_CELL_BLOCKED`
- `TESTS_PASSED: 25`

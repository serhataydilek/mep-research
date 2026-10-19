# Phase 11A.2: Real-IFC port-to-port pilot

## Result

`H_RL 68` is not a usable route. Its start port lies on the retained radiator surface, but the goal cell centre sits on a structural slab. The pilot then searched other DigitalHub subpaths. The first search that found a path is a ventilation replacement on system `ZUL_R 2`, storey `E00_OKRD`. The physical polyline starts and ends on the original interface coordinates. The search was the existing orthogonal A*. The duct body does not clear the retained duct and a 25 mm suspended ceiling, so the geometric result is a clearance violation, not a confirmed free route.

Phase 11A and Phase 11A.1 artifacts were left in place, including `experiments/digitalhub_v1/historical_phase11a/` and the blocked `routing_execution.json`.

## H_RL 68

The heating request is still the six copper elements on `H_RL 68`. Only those hosts are excluded. The boundary hosts are the radiator `0KDl_Pd9bF6RK3eO7wIX_z` and the retained pipe `08uzusf2n75OvoeaS_v52U`.

| Endpoint | Finding |
|---|---|
| Start (50.245955, 15.827534, 0.100) m | `NATIVE_PORT_PLACEMENT`. Distance to the radiator mesh is about 0. A local allowance for that one host would apply only in the port cell. The rest of the radiator stays occupied. |
| Goal (−0.120 m) | The cell centre lies on the top face of slab `3LJODRGPbDdfXHXShFL8tL`, `Geschossdecke:STB 220`. A vertical section of that mesh hits z = −0.370 m and z = −0.150 m. The port at z = −0.120 m is above the slab, not inside it. The centre is on the face, so the cell is occupied by the solid. |
| Openings | That slab has voids `3LJODRGPbDdfXHXShFL8tB` and `3LJODRGPbDdfXHXShFL8tA` through relations `0R9S7Z2$fB_PK8vYkmLGSr` and `3L6_YMl1TFAA0wf4vp_7PG`. The exported slab mesh has no void at this point, so the openings do not clear the cell. |
| Other architectural AABBs | Several proxies and slabs contain the goal in their boxes while the vertical section misses the mesh. Those are not treated as solid penetration. They do not remove the slab-face block. |

Status: `QUALIFIED_BOUNDARY_ALLOWANCE` at the start and `CONTAINING_CELL_OCCUPIED_BY_NON_EXEMPT_SOLID` at the goal. A* was not called for this request.

## What was searched

Twenty subpaths were qualified in deterministic order: the original request first, then routes that avoid slab boxes, prefer a straight length of 2–8 m, share a system and a storey, and break ties by GlobalId. Six were physically qualified. Six searches were invoked.

| # | System | Length | Result |
|---|---|---|---|
| 0 | `H_RL 68` | 1.16 m | Goal cell on the STB slab. Not searched. |
| 5 | `S_SW 3` | 3.68 m | Native ports. Both cells allowed at the boundary hosts. A* found no path. |
| 6 | `ABL_R 3` | 3.66 m | Geometric interfaces. A* found no path. |
| 8 | `ZUL_R 2` | 3.46 m | Path found. Exact interfaces. Clearance violation. This is the recorded route. |
| 9 | `ZUL_R 2` | 3.43 m | Path found. Same class of clearance violation. |
| 10–11 | ventilation | 3.42 m, 3.27 m | Qualified, then A* found no path. |

The other fourteen candidates were rejected because a port or its cell centre meets a wall or slab mesh, or because the local mesh could not be resolved. No discipline or system was removed from the obstacles.

Ceiling and proxy boxes were not left as solid where the exported shell misses the cell centre. `IfcCovering` and `IfcBuildingElementProxy` marks were rebuilt from mesh enclosure or 1 mm contact. Walls, slabs, and MEP kept their bounding-box occupancy.

## Selected request

Frozen before the search in `experiments/digitalhub_v1/first_valid_routing_request.json`.

- Discipline: ventilation, `FM_LFT_DigitalHub_v1.ifc`
- System / storey: `ZUL_R 2` / `E00_OKRD`
- Replaced hosts: `0JV3InQaz9UBog62O3UPgq`, `0IU610uqLBi9PW0cPG4EXn`, `0JV3InQaz9UBog62O3UPgs`, `0IU610uqLBi9PW0cPG4EXE`, `0IU610uqLBi9PW0cPG4EYQ`, `0IU610uqLBi9PW0cPG4EZe`
- Boundary hosts: duct `0IU610uqLBi9PW0cPG4ERE` and air terminal `33LHvKw7n5VOooYSuD9Xlz`
- Relations: `3myQAW04PF0wOsrQvlrcmy` and `2JF9LjKuvEYA7nfLA$hSuu`
- Start (8.933234, 12.102481, 3.050000) m
- Goal (12.263355, 11.335481, 3.126574) m

Both interfaces are `RELATIONSHIP_CONSTRAINED_GEOMETRIC`, not native port placements. The explicit ports do not sit on the mesh contact. The start port is at (8.933239, 12.002481, 3.150000) m, about 0.10 m from the contact. The goal ports agree with each other at x = 12.324855 m, about 62 mm from the contact. The accepted points are the host-mesh contacts, with gaps of about 1.1e-5 m and 0 m. Each contact also lies on its retained boundary host, so the allowance is that host’s containing cell only.

## Execution

`src.routing.astar_3d` was called on the free cells after the local allowance and the covering/proxy mesh rebuild. It was not implied by launching the command.

| Quantity | Value |
|---|---|
| Search | `COMPLETED_PATH_FOUND` |
| Expansions | 83 |
| A* runtime | 0.004701 s |
| Tracemalloc peak around the search | 32,304 bytes |
| Process peak working set | 506,933,248 bytes |
| Grid length | 4.2 m, 42 steps, 4 bends, 1 vertical step |
| Physical length | 4.315585 m |
| Physical vertical travel | 0.123426 m |
| Physical bends | 9, orthogonal stubs included |
| Exact endpoints | the polyline begins and ends at the two interface coordinates |

## Validation

Swept bounding boxes of the full polyline, including stubs, produced 4 broad-phase candidates. Mesh tests of those candidates found no centreline inside a solid. Two centreline distances are closer than the 0.105 m duct half-extent:

- Retained boundary duct `0IU610uqLBi9PW0cPG4ERE`: 0.0504 m.
- Suspended ceiling `1U1xl08gr5ah9up5e9rM1u`, `Deckensystem:Abgehängte Decke - Rigips Die Dicke 25`: 0.0250 m.

Label: `CLEARANCE_VIOLATION`. This is not confirmed mesh penetration, not confirmed separation, and not a claim that every untested solid is clear. A zero broad-phase count was not produced and would not have been reported as mesh clearance.

## Commands

```text
py -3 -m pytest tests/test_endpoint_qualification.py tests/test_endpoint_grid.py tests/test_external_ifc_routing.py tests/test_routing.py tests/test_medical_dental_unit_scale.py -q --tb=line
```

33 passed in 3.79 s.

```text
py -3 tools\run_digitalhub_v1_routing.py
```

Exit code 0. The last run took about 215 s and reused `experiments/digitalhub_v1/world_aabb_cache.json`.

## Acceptance

- `CANDIDATES_EVALUATED: 20`
- `ORIGINAL_H_RL_68_STATUS: GOAL_CELL_OCCUPIED_BY_SLAB_MESH`
- `SELECTED_REQUEST: ventilation ZUL_R 2 / E00_OKRD, replaced 0JV3InQaz9UBog62O3UPgq 0IU610uqLBi9PW0cPG4EXn 0JV3InQaz9UBog62O3UPgs 0IU610uqLBi9PW0cPG4EXE 0IU610uqLBi9PW0cPG4EYQ 0IU610uqLBi9PW0cPG4EZe`
- `ENDPOINT_EVIDENCE: RELATIONSHIP_CONSTRAINED_GEOMETRIC mesh contact on explicit IfcRelConnectsPorts; native port placements are offset from that contact`
- `START_AND_GOAL_PHYSICALLY_QUALIFIED: YES`
- `A_STAR_ACTUALLY_INVOKED: YES`
- `SEARCH_STATUS: COMPLETED_PATH_FOUND`
- `EXACT_PORT_TO_PORT_CONNECTION: YES`
- `PHYSICAL_ROUTE_LENGTH_M: 4.315585`
- `PHYSICAL_VERTICAL_TRAVEL_M: 0.123426`
- `GEOMETRIC_VALIDATION_STATUS: CLEARANCE_VIOLATION`
- `TESTS_PASSED: 33`

# Phase 11B: Real-IFC clearance benchmark

Five DigitalHub V1 replacement requests were executed with the frozen orthogonal 3D A*. A path returned by A* is a search result. It is a physically acceptable route only when the reconstructed body, including connector stubs, meets the declared separation from retained IFC meshes.

## Frozen Phase 11A.2 baseline

- System: ZUL_R 2 on E00_OKRD.
- Endpoint class: RELATIONSHIP_CONSTRAINED_GEOMETRIC, RELATIONSHIP_CONSTRAINED_GEOMETRIC. This is not an exact native port placement.
- Start `[8.933234, 12.102481, 3.05]` m, goal `[12.263355, 11.335481, 3.126574]` m.
- Search `SEARCH_PATH_FOUND`, geometric result `CLEARANCE_VIOLATION`.
- Physical length 4.315585000000006 m, vertical travel 0.1234260000000007 m, bends 9.
- A* expansions 83, runtime 0.004537 s.

The violation was not the synthetic benchmark's 50 mm inter-system rule. The validator compared mesh surface distance of centreline samples with the measured body half-extent. Additional clearance was 0. A sample that is not enclosed by the mesh can still violate that body envelope.

## Clearance evidence

| GlobalId | Role | Boundary host | Mesh distance | Required separation | Inside allowance | Outside allowance |
| --- | --- | --- | ---: | ---: | --- | --- |
| `0IU610uqLBi9PW0cPG4ERE` | ventilation | True | 0.050392 | 0.105000 | BOUNDARY_HOST_CONNECTION_CONTACT | CLEARANCE_VIOLATION |
| `1U1xl08gr5ah9up5e9rM1u` | architecture | False | 0.025000 | 0.105000 |  | CLEARANCE_VIOLATION |

Both distances are mesh surface distances of centreline samples. The swept AABB only selected the candidates. Neither sample is enclosed by the mesh, and that non-intersection does not create clearance: 50.4 mm and 25.0 mm are both below the 105.0 mm body half-extent.

The retained duct `0IU610uqLBi9PW0cPG4ERE` is the start boundary host. Samples inside its allowance cell meet the duct surface (about 0.005 mm) and are connection contact. The validator's 50.4 mm minimum comes from segments that are not wholly inside that cell, so it still includes samples near the interface. Samples strictly outside the cell remain 103.3 mm from the duct, which is still short of 105.0 mm. The suspended ceiling `1U1xl08gr5ah9up5e9rM1u` (`IfcCovering`) is not a boundary host. All 235 samples are outside any allowance for it, and the closest is 25.0 mm.

The controlling half-extent is 105.0 mm from fitting `0JV3InQaz9UBog62O3UPgq`, whose mesh AABB minimum edge was used because the fitting has no circle or rectangle profile. The round segments on the same replaced run are 80.0 mm and 61.5 mm radius. The search uses the maximum of those replaced bodies.

Route-body profiles:

- `0JV3InQaz9UBog62O3UPgq` IfcDuctFitting source `AABB_MIN_EDGE_HALF` half-extent 0.105000 m.
- `0IU610uqLBi9PW0cPG4EXn` IfcDuctSegment source `IFC_PROFILE_TIMES_LENGTH_SCALE_ONCE` half-extent 0.080000 m.
- `0JV3InQaz9UBog62O3UPgs` IfcDuctFitting source `AABB_MIN_EDGE_HALF` half-extent 0.080001 m.
- `0IU610uqLBi9PW0cPG4EXE` IfcDuctSegment source `IFC_PROFILE_TIMES_LENGTH_SCALE_ONCE` half-extent 0.080000 m.
- `0IU610uqLBi9PW0cPG4EYQ` IfcDuctFitting source `AABB_MIN_EDGE_HALF` half-extent 0.075000 m.
- `0IU610uqLBi9PW0cPG4EZe` IfcDuctSegment source `IFC_PROFILE_TIMES_LENGTH_SCALE_ONCE` half-extent 0.061500 m.

No DigitalHub building-code clearance is defined in this project. Sensitivity cases use additional margins of 0, 25, 50, and 100 mm and are not a code-compliance claim. The 0.05 m `BENCHMARK_V1` constant remains an inter-system synthetic value. Same-system clearance is undefined.

A 0.1 m grid can miss narrow geometry. Occupancy looks at cell centres. The mesh check of the full polyline is what supports or refuses a physical-acceptability claim. Zero swept-AABB hits stay `GEOMETRIC_VALIDATION_INCOMPLETE`.

## Original and clearance-aware results

| Request | Discipline | System | Original search | Original geometry | Aware 0 mm | Aware 25 mm | Aware 50 mm | Aware 100 mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DH11B-V1 | ventilation | ZUL_R 2 | SEARCH_PATH_FOUND | CLEARANCE_VIOLATION | ENDPOINT_BLOCKED / NONE | ENDPOINT_BLOCKED / NONE | ENDPOINT_BLOCKED / NONE | ENDPOINT_BLOCKED / NONE |
| DH11B-H1 | heating | H_VL 9 | SEARCH_NO_PATH | NONE | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| DH11B-H2 | heating | H_VL 9 | SEARCH_NO_PATH | NONE | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| DH11B-S1 | sanitary | S_SW 3 | SEARCH_NO_PATH | NONE | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| DH11B-S2 | sanitary | S_SW 3 | SEARCH_NO_PATH | NONE | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |

## Request notes

- `DH11B-V1` ventilation ZUL_R 2 / E00_OKRD. Class `RELATIONSHIP_CONSTRAINED_GEOMETRIC`, not a native port placement. Body half-extent 0.105000 m. A* found a path of 4.315585 m, vertical travel 0.123426 m, 9 bends, 83 expansions, runtime 0.004562 s. That path is a clearance violation. Every clearance-aware rerun left the same endpoints in place and did not call A*: the start cell is occupied by ceiling `1U1xl08gr5ah9up5e9rM1u` once covering contact equals the body half-extent. The goal cell remains a boundary-host allowance.
  The 0.05 m grid was not run. The aligned ROI estimates 865524 cells, above the adapter limit of 800000. A* was not changed.
- `DH11B-H1` heating H_VL 9 / B01_OKFB. Both endpoints are `NATIVE_PORT_PLACEMENT`. Body half-extent 0.015387 m. Straight-line separation 3.00 m. A* was invoked and expanded 1 node: the free start cell has no free neighbour. Search result `SEARCH_NO_PATH`. No physical route was reconstructed.
- `DH11B-H2` heating H_VL 9 / B01_OKFB. Both endpoints are `NATIVE_PORT_PLACEMENT`. Body half-extent 0.028789 m. Straight-line separation 7.21 m. A* expanded 50570 nodes in 1.371 s and found no path.
- `DH11B-S1` sanitary S_SW 3 / E00_OKRD. Both endpoints are `NATIVE_PORT_PLACEMENT`. Body half-extent 0.045530 m. Straight-line separation 3.68 m. A* expanded 7090 nodes in 0.147 s and found no path.
- `DH11B-S2` sanitary S_SW 3 / E00_OKRD. Both endpoints are `NATIVE_PORT_PLACEMENT`. Body half-extent 0.053900 m. Straight-line separation 6.75 m. A* expanded 1 node. The qualified cell is isolated. Search result `SEARCH_NO_PATH`.

Heating candidates were taken from the 2–8 m band. Twelve longer heating subpaths were rejected because a terminal cell was occupied by a non-exempt solid or the port was embedded. The original `H_RL 68` identity was not revived. Clearance-aware reruns were not started for the four searches that found no path, because extra inflation cannot open a corridor and the baseline path did not exist to repair.

## Multi-system coordination

Status: `MULTI_SYSTEM_COMPARISON_NOT_YET_VALID`.

Fewer than three physically qualified requests share a ROI of at least 0.5 m on every axis. The two heating routes are on `B01_OKFB`. The ventilation route and `DH11B-S1` are on `E00_OKRD`, and their X ranges do not meet. `DH11B-S2` extends below that floor. No coordinated batch was manufactured.

Independent searches do not prove that the routes can coexist. B1, B2, and P-CORE were not run because those implementations require the synthetic benchmark anchors.

## Counts

- `REAL_IFC_REQUESTS_QUALIFIED:` 5
- `REAL_IFC_REQUESTS_EXECUTED:` 5
- `ASTAR_PATHS_FOUND:` 1
- `PHYSICALLY_ACCEPTABLE_ROUTES:` 0
- `CLEARANCE_VIOLATIONS:` 1
- `CONFIRMED_GEOMETRIC_COLLISIONS:` 0
- `VALIDATION_INCOMPLETE:` 0
- `MULTI_SYSTEM_COORDINATION_EXECUTED:` NO

- `TESTS_PASSED:` 40

Replaced hosts stay excluded. Boundary-host contact is limited to the interface cell. The frozen 292-request synthetic benchmark was not changed.

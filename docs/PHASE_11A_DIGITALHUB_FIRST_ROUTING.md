# Phase 11A: DigitalHub V1 first real-IFC routing

Phase 11A.1 supersedes the geometric reading of the route below. That 1.0 m polyline connects two relocated voxel centres on Z layer 17. The IFC ports occupy layers 18 and 15. See `docs/PHASE_11A_1_ENDPOINT_GRID_INTEGRITY.md`. The search record itself is kept under `experiments/digitalhub_v1/historical_phase11a/`.

## Result

`src.routing.astar_3d` ran on a 0.1 m orthogonal grid built from RWTH DigitalHub Version 1 IFC geometry. The search completed and found a path.

| Item | Value |
|---|---|
| Search status | `COMPLETED_PATH_FOUND` |
| Router | `src.routing.astar_3d`, neighbours +X, -X, +Y, -Y, +Z, -Z |
| Discipline | Heating, `FM_HZG_DigitalHub_v1.ifc`, IFC4 Design Transfer View |
| Request kind | `EXISTING_SUBPATH_REPLACEMENT` |
| System / storey | `H_RL 68` / `E00_OKRD` |
| Grid route | 1.0 m, 10 steps, 0 bends, 0 vertical steps, 11 nodes expanded |
| A* runtime | 0.000642 s |
| A* Python allocation peak | 6,560 bytes (`tracemalloc` around the search only) |
| Process peak working set | 1,209,798,656 bytes for the whole routing process, including IFC tessellation |
| Collision check | `BROAD_PHASE_SWEPT_AABB`, 0 hits, `confirmed_collision_result: false` |

The path is grid reachability on the voxel graph. The broad-phase result is an AABB comparison. It is not a mesh collision result and it is not a physical-clearance certificate.

## Dataset

Pinned Git revision `36565d529b4dadeca625de2b793d7e16700171e9` of https://github.com/RWTH-E3D/DigitalHub. The acquisition script listed that tree, selected the directory named `Version_1`, and downloaded the IFC files whose names contain `ARC`, `LFT`, `HZG`, and `SAN`. Version 2 was not downloaded.

License: MIT. Copyright (c) 2020 RWTH Aachen University - E3D Institute of Energy Efficiency and Sustainable Building. Technical report DOI `10.18154/RWTH-2020-11683`.

Payloads are in `validation/ifc_samples/external/rwth_digitalhub_v1/`, which `.gitignore` already excludes. Medical-Dental files were not modified.

| File | Role | Bytes | SHA-256 | Header view | Project length scale |
|---|---|---:|---|---|---:|
| `FM_ARC_DigitalHub_v1.ifc` | Architecture | 14,322,717 | `eff9b18a0a2fce6180d8f25c8cfe0e986d4d2e4a7a3ee69f375a105b20ca593c` | DesignTransferView_V1.0 | 1.0 (metre; a decimetre length unit is also declared) |
| `FM_HZG_DigitalHub_v1.ifc` | Heating | 9,869,500 | `70a40f5d0782b53ae573b89cca8990f66b54b21229527daddd58e5dc2573fbb6` | DesignTransferView_V1.0 | 0.001 (millimetre) |
| `FM_LFT_DigitalHub_v1.ifc` | Ventilation | 7,369,265 | `8647e24ea0c0e2f4188fad6222662c1004de55978f9a0d0e1190445e149dbd6c` | DesignTransferView_V1.0 | 0.001 |
| `FM_SAN_DigitalHub_v1.ifc` | Sanitary | 16,846,052 | `5d6b7998d9060626132f56010a75c238ebc00ed6b0f937f2c1db0ac46fbff87a` | ReferenceView_V1.2 | 0.001 |

The sanitary file is in `Version_1`, and its `FILE_DESCRIPTION` says Reference View 1.2. The routing experiment used heating, whose header says Design Transfer View 1.0.

Measured relationship counts, compared afterwards with the 2020 report:

| File | Measured | Report figure |
|---|---|---|
| Ventilation | 2,656 ports, 1,310 `IfcRelConnectsPorts` | same |
| Sanitary | 2,135 ports, 1,028 port connections | same |
| Heating | 3,567 ports, 1,771 port connections | same |
| Architecture | 248 openings, 248 `IfcRelVoidsElement` | same |

Every explicit port connection in the three MEP files resolved both hosts through `IfcRelNests`. Tessellation with `USE_WORLD_COORDS` succeeded for every product that has a representation (architecture 1,017, heating 1,707, ventilation 1,309, sanitary 1,012). Geometry failure count is 0 in each file.

## Units, placements, federation

Placement translations from `get_local_placement` are in project length units and are multiplied by the project scale once. World-mesh vertices from IfcOpenShell are left in SI metres. They are not multiplied by the project scale. That avoids the Medical-Dental double-scale failure, where millimetre models were shrunk a second time.

World bounds of the tessellated meshes, metres:

| Role | Min (x, y, z) | Max (x, y, z) |
|---|---|---|
| Architecture | -18.183, -22.125, -3.870 | 141.125, 41.405, 10.681 |
| Heating | 7.174, 7.184, -3.270 | 69.826, 26.419, 6.200 |
| Ventilation | 7.205, 7.148, -3.300 | 69.795, 26.395, 8.935 |
| Sanitary | 13.000, 13.754, -2.470 | 56.718, 26.090, 8.329 |

All six discipline pairs overlap in world bounds. Site and building placement origins are at the origin; the architecture site origin is about 0.045 mm from zero. Storeys `E00_OKRD` (-0.15 m) and `E01_OKRD` (3.75 m) match across all four files. Basement storey names and elevations do not: architecture `B01_OKRD` at -3.37 m, MEP `B01_OKFB` at -3.27 m, sanitary also `B01_UKRD` at -3.67 m.

Federation status: `PLACEMENT_AND_BOUNDS_COMPARED_NOT_GEOMETRICALLY_VERIFIED`. Bounds overlap and shared storey elevations are recorded. They do not verify element-level federation.

A sample of placement origins was compared with mesh AABBs. Heating: 0 of 8 scaled origins fell outside the mesh. Architecture: 1 of 8. Sanitary: several origins sit on the storey origin rather than inside the element mesh. In those samples the unscaled project-unit coordinate is also outside the mesh, which is a placement-versus-solid offset, not a second application of the millimetre scale.

## Request

Selection rule, applied to heating first: same `IfcSystem`, then same storey, then a degree-2 run of `IfcFlowSegment` or `IfcFlowFitting` whose length is nearest 6, then GlobalId order. The selected run has 6 hosts on `H_RL 68` and `E00_OKRD`.

Replaced hosts, excluded from occupancy:

| GlobalId | Class | Name |
|---|---|---|
| `0KDl_Pd9bF6RK3eO7wIX_s` | IfcPipeSegment | Kupfer hartgelötet 7413416 |
| `0KDl_Pd9bF6RK3eO7wIX_k` | IfcPipeFitting | Bogen Kupfer 7413424 |
| `2hMjjbZvP4CAN9xX2G5FAP` | IfcPipeSegment | Kupfer hartgelötet 7491872 |
| `2hMjjbZvP4CAN9xX2G5FAH` | IfcPipeFitting | Bogen Kupfer 7491880 |
| `2hMjjbZvP4CAN9xX2G5FAZ` | IfcPipeSegment | Kupfer hartgelötet 7491866 |
| `1PO8_6BBT97gCnBagBctV7` | IfcPipeFitting | Bogen Kupfer 7658325 |

Retained boundary hosts, kept as obstacles:

- `0KDl_Pd9bF6RK3eO7wIX_z`, `IfcSpaceHeater` (radiator), relation `3O9xE_EPT0iu5QzbpCyjnR`, ports `3wgcw0GrbEsxHKzVrCPXai` and `1MiHzau5b2NeD7NmRJV_2h`. Interface (50.245955, 15.827534, 0.100) m. Classification `NATIVE_PORT_PLACEMENT`. Mesh distance about 0. Port positions agree.
- `08uzusf2n75OvoeaS_v52U`, `IfcPipeSegment`, relation `2BOjbykKP1qRuxj9M1ec_E`, ports `1mzi8tqTPBWOolCkIHAXe5` and `2PWvpmDYLD6hmN4OJjVyl4`. Interface (50.149955, 14.687534, -0.120) m. Classification `NATIVE_PORT_PLACEMENT`. Mesh distance 0.009 m. Port positions agree.

Both coordinates use a non-identity local port placement whose composed world point lies on the host mesh. `RELATIONSHIP_CONSTRAINED_GEOMETRIC` and `CONTROLLED_GEOMETRY_ENDPOINT` were implemented and tested; this candidate did not need them. The request is an existing connected subpath. It is not a native source-to-terminal design demand.

Straight-line separation of the two interface points is about 1.165 m, including 0.220 m of Z. Search terminals were offset 0.110 m from each interface toward the excluded pipe volume, then snapped to free voxel centres. Snap distances were 0.076 m and 0.175 m, both inside the existing two-voxel (0.20 m) tolerance. Both terminals landed in voxel layer k=17 (centre z = 0.05 m), so the reported vertical travel is 0 because of grid quantisation.

## Occupancy and body

Route body: copper segments have a circular profile radius of 0.009 m after one millimetre-to-metre scale. Elbows had no readable profile; their half-extent is half the minimum mesh AABB edge, about 0.00993 m. The governing half-extent is 0.009927 m. Additional clearance is 0. Obstacle AABBs are inflated by that half-extent. Because 0.0099 m is well below the 0.1 m voxel, inflation often marks the same cells as the uninflated box.

ROI margin 1.5 m. Aligned domain x 48.6–52.1, y 13.1–19.3, z -1.7–2.2. Grid 35 × 62 × 39 = 84,630 cells (43,814 free, 40,816 blocked). Origin (48.6, 13.1, -1.7) m.

194 real IFC AABBs were marked: architecture walls, slabs, a column, doors, and proxies; retained heating pipes, fittings, and heaters; sanitary pipes, fittings, terminals, and equipment; one ventilation duct. No synthetic building obstacles were added. Only the six replaced hosts were left out of occupancy.

Opening policy `CONSERVATIVE_HOST_AABB_RETAINS_VOID_SPACE`. Two openings in the ROI, `279opIq2rEtREtEKSBVxUT` and `279opIq2rEtREtEKSBVxTT`, both void host `0N58olkxr7Mww1RX8p2dt_` through relations `279opIq2rEtREtEK0BVxUT` and `279opIq2rEtREtEK0BVxTT`. Their solids were inventoried and were not subtracted from the host AABB, because an opening AABB can mark solid wall as free.

The broad-phase check swept the centreline by the 0.009927 m half-extent and compared those AABBs with obstacle AABBs. The two boundary hosts and the six replaced hosts were omitted from that hit list so the connection into retained equipment is not scored as a clash. Hit count 0. Label `BROAD_PHASE_ONLY`.

## Commands and tests

```text
powershell -ExecutionPolicy Bypass -File .\tools\acquire_digitalhub_v1.ps1
py -3 tools\run_digitalhub_v1_probe.py
py -3 tools\run_digitalhub_v1_routing.py
py -3 -m pytest tests\test_external_ifc_routing.py tests\test_routing.py tests\test_medical_dental_unit_scale.py -q
```

Probe exit 0. Routing exit 0. Pytest: 15 passed, including the frozen 292-request routing tests and the Medical-Dental unit-scale guard. After the working-set query was given explicit Windows argument types, `tests\test_external_ifc_routing.py` and `tests\test_routing.py` were run again: 14 passed. The saved routing artifact is that second execution. The path, expanded-node count, and endpoint classification match the first execution. `src/routing.py`, `src/voxel.py`, the synthetic scenario and demand fixtures, and Medical-Dental assets were not edited. Nothing was committed or pushed.

## Files

- `src/external_ifc_probe.py`
- `src/external_ifc_routing.py`
- `tools/acquire_digitalhub_v1.ps1`
- `tools/run_digitalhub_v1_probe.py`
- `tools/run_digitalhub_v1_routing.py`
- `tests/test_external_ifc_routing.py`
- `experiments/digitalhub_v1/dataset_manifest.json`
- `experiments/digitalhub_v1/probe_results.json`
- `experiments/digitalhub_v1/routing_request.json`
- `experiments/digitalhub_v1/occupancy_manifest.json`
- `experiments/digitalhub_v1/routing_execution.json`
- `validation/ifc_samples/external/rwth_digitalhub_v1/` (gitignored payloads, license, acquisition record)

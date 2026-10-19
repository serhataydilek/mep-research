# Research chronology and evidence record

## Scope and evidence rules

This is an auditable record of the reachable repository history and preserved
artifacts, not a retrospective success narrative. The earliest reachable commit
is `e2252c39a6c29b2a4f4b3a3948ba4a780121b61b` (2026-09-20). The README states
that the project was migrated from `serhataydilek/mep-research` at
`72a511f55d249f41f289bcb8f0212213cbb277d1`; earlier source-repository commits
are not present locally, so their chronology is **UNVERIFIED** here.

Evidence terms: **VERIFIED** means a reachable commit and/or retained execution
artifact supports the statement; **PARTIALLY_VERIFIED** means the artifact is
present but does not establish every physical claim; **INVALIDATED** means later
evidence demonstrates that the earlier interpretation must not be used.

## Chronology

### 2026-09-20 — Project and controlled IFC environment (VERIFIED)

- **Identifiers:** initial project through deterministic spatial hierarchy;
  commits `e2252c3`, `2101a6b`, `406a01a`.
- **Question/change:** establish deterministic IFC spatial hierarchy, slabs,
  walls, columns, shafts and openings before routing.
- **Evidence:** reachable Git commits and controlled inputs in
  [`../../config`](../../config).
- **Result/lesson:** this is synthetic, controlled geometry—not evidence of
  real-building routing or physical constructability.

### 2026-09-21 to 2026-09-22 — Independent routing, reservation, and repair (VERIFIED)

- **Identifiers:** B0, B1, B2; commits `25fa753`, `1984af8`, `d977e32`,
  `a4384d4`, `7576f8c`.
- **Question:** can independent 3D A*, fixed-priority reservation, or selective
  repair balance route completeness and observed inter-system conflicts?
- **Parameters:** 4 controlled scenarios × 3 demand profiles = 292 requests;
  0.1 m grid and 0.05 m configured clearance.
- **Execution evidence:** [`../../experiments/results/phase8_final_synthesis.json`](../../experiments/results/phase8_final_synthesis.json).
- **Results:** B0 completed 292/292 but recorded 1,256 hard conflicts and
  1,257 clearance violations. B1 completed 144/292 with zero observed
  inter-system conflicts among completed routes, leaving 148 unrouted. B2
  completed 292/292 but recorded 936 hard conflicts and 941 clearance
  violations.
- **Lesson/decision:** zero observed conflicts in an incomplete reservation
  result is not a complete routing solution; repair retained completeness but
  did not establish clash freedom.

### 2026-09-22 — Constructability and gravity experiments (PARTIALLY_VERIFIED)

- **Identifiers:** C0 and gravity-aware drainage; commits `43abb9d`,
  `bacadd3`, `31b970a`, `253377c`.
- **Question/change:** add simplified constructability checks and gravity-aware
  drainage constraints.
- **Evidence:** commit history and [`../KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md).
- **Result:** the final synthesis reports zero benchmark hard-feasible C0 cases
  for all compared methods. C0 is a simplified benchmark definition, not an
  engineering approval.
- **Lesson:** gravity and C0 added constraints but did not provide fabrication,
  support, penetration, or code validation.

### 2026-09-22 to 2026-09-25 — P-CORE coordination and comparison (VERIFIED)

- **Identifiers:** P-CORE seed and multi-round P-CORE-6C2B; commits `ee22fe7`,
  `c20d037`, `563536c`, `187fd99`, `7f7c670`, `32a8594`, `5b8130d`.
- **Question/change:** reduce conflict burden without losing route completeness.
- **Execution evidence:** [`../../experiments/results/phase8_final_report.md`](../../experiments/results/phase8_final_report.md).
- **Results:** P-CORE-SEED completed 292/292 with 1,020 hard conflicts and
  1,209 clearance violations. P-CORE-6C2B completed 292/292 with 747 hard
  conflicts and 926 clearance violations; its artifact records 39 successful
  reroutes from 359 candidates.
- **Lesson/decision:** multi-round coordination reduced, but did not eliminate,
  the synthetic conflict burden. It is not a zero-clash result.

### 2026-09-26 to 2026-09-27 — External IFC validation harness and v1.0 release (VERIFIED)

- **Identifiers:** external IFC harness and `research-v1.0`; commits `c4d1de8`,
  `7d358de`, `72a511f`.
- **Question/change:** inspect external IFC schemas, units, placements and
  geometry without claiming arbitrary-model routing.
- **Result:** parsing and guarded voxel reasoning worked for selected models,
  while dense or semantically incomplete inputs were refused or not router-ready.
- **Evidence:** [`../CURRENT_RESEARCH_BASELINE.md`](../CURRENT_RESEARCH_BASELINE.md),
  [`../../REAL_IFC_VALIDATION.md`](../../REAL_IFC_VALIDATION.md).

### 2026-10-01 to 2026-10-12 — Endpoint, topology, and physical-routing foundations (VERIFIED)

- **Identifiers:** Phase 9A through multi-system diagnostics; commits `ae39bc7`
  through `c57b1ba`.
- **Question/change:** make endpoint provenance immutable, separate topology
  planning from routing, and record clearance/entry diagnostics.
- **Result:** the code introduced explicit provenance and bounded physical-edge
  routing, but did not establish general connector engagement or real-IFC
  coordination.
- **Evidence:** [`../CURRENT_RESEARCH_BASELINE.md`](../CURRENT_RESEARCH_BASELINE.md)
  and commits named above.

### 2026-10-13 to 2026-10-18 — Real IFC inspection, Medical-Dental work, and audits (PARTIALLY_VERIFIED)

- **Identifiers:** Level A/B, Medical-Dental, unit/registration and native-port
  work; commits `1c7cccfa` through `ae992a5` and Phase 10R reports.
- **Question/change:** move from controlled inputs to bounded source-IFC meshes,
  inspect coordinate registration, unit conversion and native connector evidence.
- **Result:** source mesh extraction and viewer fixtures were produced; reports
  retain failures and gate routing claims where registration, endpoints or
  physical semantics are unresolved.
- **Evidence:** [`../PHASE_10R_B3_4_UNIT_SCALE_AND_ROUTING_READINESS.md`](../PHASE_10R_B3_4_UNIT_SCALE_AND_ROUTING_READINESS.md),
  [`../PHASE_10R_B4_FIRST_REAL_IFC_ROUTING_PILOT.md`](../PHASE_10R_B4_FIRST_REAL_IFC_ROUTING_PILOT.md).

#### Recovered Phase 10 detail

- **2026-10-13 — Level A/B source inspection:** IFC geometry and metadata were
  inspected as bounded source evidence; this established neither route
  feasibility nor connector engagement.
- **2026-10-14 to 2026-10-16 — Medical-Dental federation:** extraction and
  registration work produced reviewable source-mesh fixtures. Coordinate and
  unit conclusions remain limited to the recorded artefacts.
- **2026-10-17 — unit-scale and routing-readiness gate:** a failed or unresolved
  readiness condition blocks a physical-routing claim; it is not repaired by a
  viewer overlay.
- **2026-10-18 — native-port routing pilot:** port and clearance diagnostics
  were retained as negative/qualified evidence rather than recast as a routing
  success.

### UNKNOWN date — Phase 11A historical proxy route (INVALIDATED)

- **Identifier:** Phase 11A historical routing execution.
- **Question/change:** first DigitalHub route attempt.
- **Result:** the route connected voxel centres after ports were offset/moved to
  nearby free cells; it did not connect the original IFC ports.
- **Evidence:** [`../../experiments/digitalhub_v1/historical_phase11a/LABEL.json`](../../experiments/digitalhub_v1/historical_phase11a/LABEL.json).
- **Lesson/decision:** it must never be reported as a successful real-IFC route.

### UNKNOWN date — Phase 11A.1 endpoint correction (VERIFIED negative result)

- **Identifier:** endpoint-grid integrity work.
- **Change:** immutable endpoints replaced free-cell relocation.
- **Result:** endpoint-blocked outcomes were retained rather than silently
  displaced.
- **Evidence:** [`../PHASE_11A_1_ENDPOINT_GRID_INTEGRITY.md`](../PHASE_11A_1_ENDPOINT_GRID_INTEGRITY.md),
  [`../../experiments/digitalhub_v1/endpoint_grid_diagnostics.json`](../../experiments/digitalhub_v1/endpoint_grid_diagnostics.json).

### UNKNOWN date — Phase 11A.2 port-to-port pilot (PARTIALLY_VERIFIED)

- **Identifier:** frozen historical Phase 11A.2 ventilation route.
- **Result:** a physically connected route artifact exists, but later clearance
  evidence records a clearance violation; it is not physically acceptable.
- **Evidence:** [`../PHASE_11A_2_REAL_IFC_PORT_TO_PORT_PILOT.md`](../PHASE_11A_2_REAL_IFC_PORT_TO_PORT_PILOT.md),
  [`../../experiments/digitalhub_v1/historical_phase11a2/LABEL.json`](../../experiments/digitalhub_v1/historical_phase11a2/LABEL.json).

### UNKNOWN date — Phase 11B five-request clearance benchmark (VERIFIED)

- **Identifiers:** DH11B-V1, H1, H2, S1, S2.
- **Dataset/provenance:** DigitalHub V1; exact files, unit scales and SHA-256
  values are in [`../../experiments/digitalhub_v1/dataset_manifest.json`](../../experiments/digitalhub_v1/dataset_manifest.json).
- **Parameters:** frozen A* with 0.1 m voxels; only explicitly replaced hosts
  excluded; mesh-distance validation after swept-AABB candidate selection.
- **Results:** V1 found a 4.315585 m, 48-point physical polyline with 0.123426 m
  vertical travel but `CLEARANCE_VIOLATION`; the four H/S requests were
  `SEARCH_NO_PATH`. Thus: one search path, zero physically acceptable routes.
- **Evidence:** [`../../experiments/digitalhub_v1/phase11b_results.json`](../../experiments/digitalhub_v1/phase11b_results.json),
  [`../../experiments/digitalhub_v1/phase11b_clearance_evidence.json`](../../experiments/digitalhub_v1/phase11b_clearance_evidence.json),
  [`../PHASE_11B_REAL_IFC_CLEARANCE_BENCHMARK.md`](../PHASE_11B_REAL_IFC_CLEARANCE_BENCHMARK.md).

### UNKNOWN date — Phase 11D and 11D.1 comparison viewer (PARTIALLY_VERIFIED)

- **Change:** bounded real-IFC meshes, the saved Phase 11B route and validation
  evidence were made inspectable in the existing Three.js viewer.
- **Result:** all five requests have bounded source geometry; DH11B-V1 overlays
  the saved route while preserving `CLEARANCE_VIOLATION`; four cases explicitly
  show no generated route. Screenshots are evidence of rendered state, not
  mesh-level clearance proof.
- **Evidence:** [`../../viewer/digitalhub_phase11b_fixture.json`](../../viewer/digitalhub_phase11b_fixture.json),
  [`../../experiments/digitalhub_v1/evidence/PHASE_11D_1_SCREENSHOT_EVIDENCE.json`](../../experiments/digitalhub_v1/evidence/PHASE_11D_1_SCREENSHOT_EVIDENCE.json).

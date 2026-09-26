# MEP Routing Research

A deterministic research prototype for studying multi-system mechanical, electrical, and plumbing (MEP) routing in IFC/BIM-derived building space. The project combines service-aware 3D voxel routing, conflict evaluation, gravity-aware drainage, selective repair, multi-round coordination, multi-objective analysis, and stage-specific validation against public IFC2X3 and IFC4 models.

The implementation is a controlled research benchmark, not production BIM coordination software or an engineering approval tool.

## Research Problem

Multiple MEP disciplines compete for limited space above ceilings and around structural elements. Routing each service independently preserves coverage but can create many inter-system conflicts. Strict sequential reservation can eliminate observed conflicts while making later routes impossible. This project measures that trade-off across routing completion, hard conflicts, clearance violations, routed length, bends, and vertical travel.

The central question is not which method is universally best, but how deterministic coordination methods exchange coverage, conflict burden, and geometric cost under a fixed benchmark.

## System Pipeline

```text
IFC / benchmark geometry
  -> geometry extraction
  -> service-aware voxel grids
  -> deterministic 3D A* routing
  -> conflict and constructability evaluation
  -> selective repair / multi-round coordination
  -> stratified and Pareto analysis
  -> final synthesis and external IFC validation
```

The benchmark uses 0.1 m voxels, six orthogonal neighbours, a Manhattan heuristic, stable tie-breaking, and configuration-driven service envelopes and clearances.

## Methods

### B0 — Independent 3D A*

Routes every connection independently through its service-aware base grid. B0 does not reserve space for other MEP systems, so it establishes a full-coverage routing baseline rather than a coordinated layout.

### B1 — Fixed-Priority Sequential Reservation

Routes systems in the fixed order HVAC, drainage, water, fire, and electrical. Successful higher-priority routes reserve pair-specific envelope and clearance space for lower-priority systems. This reduces conflicts but is order-dependent and can block later connections.

### B2 — Conflict-Aware Selective Repair

Starts from B0, identifies a deterministic candidate edge cover of the conflict graph, freezes non-candidates, and attempts one repair pass. Failed repairs retain their original B0 geometry.

### Gravity-Aware Drainage

Uses terminal-to-egress flow, prohibits uphill steps, and checks the benchmark's 1% aggregate stair-step slope abstraction. It is a controlled routing constraint, not a substitute for code-compliant drainage design.

### P-CORE Seed

Combines B0 geometry for non-drainage systems with gravity-aware drainage routes to create a complete deterministic initial layout.

### P-CORE Multi-Round Coordination

Runs the following deterministic loop, capped at six rounds per case:

```text
detect conflicts
  -> select repair candidates
  -> reroute candidates
  -> evaluate the complete trial globally
  -> accept strict improvement or roll back
  -> repeat
```

The global accept/rollback rule prevents a locally useful repair from being retained when it worsens the authoritative whole-layout conflict objective.

## Benchmark

The fixed benchmark is the cross-product of:

- four architectural scenarios (`s01` low through `s04` extreme);
- three nested demand profiles (`d01` with 13, `d02` with 24, and `d03` with 36 connection requests);
- five systems: HVAC, drainage, water, fire, and electrical.

This produces 12 scenario-demand cases and 292 requested routes. Inputs, ordering, GUID construction, pathfinding tie-breaks, and serialized outputs are deterministic.

## Results

The Phase 8 synthesis reports these global totals across all 12 cases:

| Method | Successful | Hard conflicts | Clearance violations | Length (m) | Bends | Vertical (m) |
|---|---:|---:|---:|---:|---:|---:|
| B0 | 292/292 | 1,256 | 1,257 | 3,507.7 | 572 | 0.0 |
| B1 | 144/292 | 0 | 0 | 2,516.6 | 603 | 12.8 |
| B2 | 292/292 | 936 | 941 | 3,962.3 | 803 | 11.6 |
| P-CORE-SEED | 292/292 | 1,020 | 1,209 | 3,515.1 | 620 | 7.4 |
| P-CORE-6C2B | 292/292 | 747 | 926 | 3,662.9 | 804 | 17.7 |

B0 preserves full routing coverage but creates many conflicts. B1 eliminates observed conflicts among its successful routes, but completes only 144 of 292 requests. B2 reduces conflict burden while preserving full coverage. P-CORE reduces it further while retaining 292/292 completion, at increased geometric cost.

Conflict counts are evaluated only over unordered inter-system pairs among successful routes. They must therefore be interpreted alongside route completion; B1's zero counts do not describe the 148 routes it did not complete.

## P-CORE Result

From seed to final coordinated geometry, P-CORE preserves 292/292 route completion while changing:

| Metric | Seed | Final | Change |
|---|---:|---:|---:|
| Hard conflicts | 1,020 | 747 | −26.8% |
| Clearance violations | 1,209 | 926 | −23.4% |
| Routed length | 3,515.1 m | 3,662.9 m | +4.2% |
| Bends | 620 | 804 | +29.7% |
| Vertical travel | 7.4 m | 17.7 m | +139.2% |

This is a conflict-versus-geometry trade-off, not universal method superiority.

## Pareto Analysis

No method dominates every objective in the global operational or common-success views. Results depend on both the objective set and the evaluated population:

- a conflict-only view identifies B1 as non-dominated because routing coverage is excluded;
- the operational view includes successful-route count alongside conflict and geometry metrics;
- the common-success view compares the 144 routes completed by all five methods and removes coverage as a differentiator.

Consequently, the repository reports exact Pareto fronts and sensitivity rather than a weighted score, overall rank, or preferred method.

## External IFC Validation

Post-research sanity validation tested three public models without changing the completed Phase 8 benchmark:

- **Esplanades IFC2X3:** parses, exposes a seven-storey hierarchy and usable architectural geometry, but its estimated 51,140,320-cell dense grid exceeds the configured 25,000,000-cell safety limit and is refused.
- **Building-Architecture IFC4:** parses, exposes usable architecture, and safely voxelizes to 124,320 cells. It is not router-ready because the validator does not fabricate routing endpoints.
- **Medical-Dental Clinic HVAC IFC2X3:** exposes 3,704 distribution entities, including 1,548 flow segments and 1,590 flow fittings. The discipline model lacks current-policy wall/column obstacle geometry and compatible routing endpoints. Existing MEP is inspected, not directly consumed or rerouted.

Compatibility is stage-specific: `router_ready = false` is not equivalent to IFC parsing or validation failure. Exact provenance, hashes, stage results, placement findings, and limitations are documented in [REAL_IFC_VALIDATION.md](REAL_IFC_VALIDATION.md).

## Repository Structure

| Path | Purpose |
|---|---|
| `src/` | IFC generation, voxelization, routing methods, coordination, comparison, Pareto analysis, and final synthesis |
| `tests/` | Deterministic unit and integration coverage for all research phases and IFC validation |
| `config/` | Building, service-envelope, constructability, and coordinator configuration |
| `experiments/scenarios/` | Four controlled architectural scenarios |
| `experiments/demands/` | Three nested routing-demand profiles |
| `experiments/results/` | Ignored reproducible research outputs; only `.gitkeep` is tracked |
| `validation/ifc_samples/` | Tracked external-sample provenance; IFC payloads are ignored |
| `validation/results/` | Ignored reproducible IFC validation outputs |
| [BUILDSPEC.md](BUILDSPEC.md) | Implemented phase-by-phase behavior and validation gates |
| [RESEARCH.md](RESEARCH.md) | Original research framing, hypotheses, and baseline definitions |
| [REAL_IFC_VALIDATION.md](REAL_IFC_VALIDATION.md) | External IFC validation method, evidence, and limitations |

## Installation

The release was validated with Python 3.14.3. The repository does not declare a broader supported Python range. Runtime and test dependencies are pinned in `requirements.txt`.

PowerShell setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Quick Start

Run commands from the repository root.

Generate the controlled IFC model and architectural benchmark metrics:

```powershell
python -m src.building --config config/building.json --output out/ifc/architecture.ifc
python -m src.benchmark --scenarios experiments/scenarios --output experiments/results/scenario_metrics.json
```

Generate the five-method comparison:

```powershell
python -m src.method_comparison --scenarios experiments/scenarios --demands experiments/demands --systems config/mep_systems.json --constraints config/constructability.json --coordinator config/coordinator.json --output experiments/results/method_comparison.json
```

Generate the exact Pareto analysis:

```powershell
python -m src.pareto_analysis --scenarios experiments/scenarios --demands experiments/demands --systems config/mep_systems.json --constraints config/constructability.json --coordinator config/coordinator.json --output experiments/results/phase7_pareto_analysis.json
```

Generate the final JSON synthesis and Markdown report:

```powershell
python -m src.final_synthesis --scenarios experiments/scenarios --demands experiments/demands --systems config/mep_systems.json --constraints config/constructability.json --coordinator config/coordinator.json
```

Inspect the generated control or user-supplied IFC files:

```powershell
python -m src.ifc_validation --ifc out/ifc/architecture.ifc C:\path\to\external.ifc --control-generated out/ifc/architecture.ifc --output validation/results/real_ifc_sanity.json --markdown validation/results/real_ifc_sanity.md
```

The IFC validation command does not download sample files or invent routing endpoints. External payloads must be supplied separately; their validated provenance is recorded in `validation/ifc_samples/manifest.json`.

## Testing

Run the complete repository suite:

```powershell
python -m pytest
```

The `research-v1.0` release candidate passed 170 tests. Runtime and exact count can vary if the suite or environment changes.

## Reproducibility

Scenario order, demand order, system order, A* tie-breaking, coordinator rounds, and JSON serialization are deterministic. Tests verify byte-identical research and validation JSON/Markdown generation. Generated files under `experiments/results/`, `validation/results/`, and `out/` are intentionally ignored; regenerate them from tracked inputs and commands rather than committing machine-local artifacts.

## Limitations

- The primary evidence comes from a controlled synthetic, single-floor benchmark.
- Voxel routing and swept AABB envelopes simplify continuous geometry and fabrication constraints.
- Rotated or curved IFC geometry can be over-approximated by axis-aligned bounds.
- P-CORE uses deterministic local repair with global acceptance; it does not prove a global optimum.
- External IFC coverage is limited to three sanity-validation samples and selected semantic stages.
- Dense project-scale grids can exceed practical memory limits without sparse, tiled, bounded, or recentered representations.
- Existing IFC MEP elements are inspected but are not directly consumed or rerouted.
- C0 is a benchmark constructability definition, not building-code compliance.
- The project is not production BIM software and does not provide engineering approval.

## Research Status

**Research scope complete at Phase 8.** Post-research external IFC sanity validation is also complete. Further productization, broader IFC semantics, sparse spatial representations, and real-project routing studies are outside this release.

## License

No repository license file is currently provided. The public IFC validation samples retain their upstream CC BY 4.0 licensing and are not redistributed in this repository.

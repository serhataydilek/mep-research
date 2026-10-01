# Master Research and Productization Report

## Executive summary

Research v1.0 is a deterministic controlled prototype for comparing multi-system MEP routing strategies in IFC/BIM-derived voxel space. It proves measurable trade-offs: independent routing preserves all 292 benchmark requests but creates many inter-system conflicts; fixed-order reservation can yield zero observed conflicts only by completing 144 requests; P-CORE reduces, but does not eliminate, conflict burden while retaining coverage. Therefore, it is evidence for conflict-reduction research—not a deployable zero-conflict router.

Repository lineage is preserved from `https://github.com/serhataydilek/mep-research` at `72a511f55d249f41f289bcb8f0212213cbb277d1` (`research-v1.0`) into `https://github.com/Construction360/smartsites_hvac_reserach` on 2026-10-01. The original source repository is read-only evidence; new commits belong only to this target repository.

The next product objective is a hard-feasible MEP routing engine. It will consume provenanced IFC endpoints, plan topology before paths, classify semantic routing space and penetrations, generate constrained routes, coordinate them with PBS-inspired conflict-driven priorities and bounded neighborhood repair, make continuous geometry, and validate independently before IFC/3D output.

## Problem definition and research evidence

MEP disciplines compete for limited plenum and plant-room volume. A route must connect the intended system topology while honoring geometry, clearance, structure, penetrations, slope, fittings, access, and reproducibility. The v1.0 research problem isolated this competition in a fixed synthetic 4-scenario × 3-demand benchmark. It used service-aware voxel occupancy, deterministic A*, route-pair AABB proxy evaluation, C0 constructability proxies, selective repair, and P-CORE coordination. The precise baseline, data, methods and totals are in [CURRENT_RESEARCH_BASELINE.md](CURRENT_RESEARCH_BASELINE.md).

P-CORE’s seed-to-final movement (hard conflicts 1020→747; clearance 1209→926) establishes that its accepted local reroutes reduce its lexicographic conflict objective. The geometry costs increase and 747 hard-conflicting pairs remain. The experiment does not demonstrate zero-clash routing, physical fabrication geometry, general IFC routing, code compliance, optimality, or arbitrary topology.

## Architecture audit and lessons

Useful retained assets are deterministic input validation, reproducible benchmark fixtures, stable A*, service-envelope semantics, external IFC inspection scaffolding, route diagnostics, and the separation between routing and comparative evaluation. They are not discarded simply because they are incomplete.

The critical changes are authoritative immutable demands, connector provenance, explicit topology, semantic/exact spatial representations, planned penetrations, exact/conservative physical envelopes, continuous drainage, hard acceptance constraints, and an independent validator. The detailed audit is [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). The central lesson is that conflict reduction is not feasibility: a production result never counts conflicting geometry as completed, and it never hides unrouted demands behind a low conflict rate.

## Algorithm and standards position

The automatic-pipe-routing survey supports making representation and constraints explicit before optimization. Belov et al. supports using MAPF ideas for spatial pipe routing, while retaining the difference between static spatial occupancy and robot time. CBS provides a reference high-level constraint tree; PBS provides the preferred starting pattern of conflict-driven partial priorities. LNS/rip-up is recovery after a feasible-prefix coordinator stagnates, not a license to accept invalid trials. The 2026 plant-room study reinforces coarse-to-fine planning and dynamic headers, but its scoped cases and preference model are not proof for building-wide routing. Full bibliographic and adoption boundaries are in [REFERENCES.md](REFERENCES.md).

IFC integration is staged. `IfcDistributionPort` is the preferred authoritative endpoint evidence; relations and geometry must be normalized before routing. Future exports use flow segments, fittings, ports, connectivity and opening relationships, but this migration implements none of them. See [IFC_AND_BIM_INTEGRATION.md](IFC_AND_BIM_INTEGRATION.md).

## Product architecture, verification and roadmap

The architecture is deliberately modular: ingestion ≠ routing ≠ validation ≠ serialization ≠ visualization. The components and their contracts are detailed in [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md); pseudocode and acceptance ordering are in [ALGORITHM_DESIGN.md](ALGORITHM_DESIGN.md). The validator receives immutable requirements separately from candidate routes, reports `VALID`, `INVALID`, or `PARTIAL`, and distinguishes proof from a budget-limited search miss. The test tiers and production metrics are in [VALIDATION_STRATEGY.md](VALIDATION_STRATEGY.md).

The implementation sequence is 9A authoritative ports/demands, 9B topology, 9C spatial/penetrations, 9D constrained routing, 9E PBS coordination, 9F recovery, 9G continuous geometry, 9H independent validation, then IFC integration/export, viewer and external validation. Each gate, artifact, dependency and risk is recorded in [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md). Decisions and risk evidence are maintained in [DECISION_LOG.md](DECISION_LOG.md).

## Immediate next step

Start Phase 9A only: define versioned immutable request/topology and endpoint-provenance models with focused fixtures. Do not implement PBS, CBS, topology optimization, penetration generation, exact geometry, IFC output or a viewer before their prerequisite contracts and tests are accepted.

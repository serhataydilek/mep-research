# Generative Constructability-Aware Coordination of Heterogeneous MEP Systems in IFC-Based Building Models

This is an IFC-native research prototype for studying heterogeneous MEP coordination. The research direction is toward generative design alternatives, constructability-aware validation, and reproducible benchmarking; these capabilities are planned, not yet implemented.

The current implementation provides a deterministic IFC4 benchmark foundation: a parametric spatial hierarchy with floor-slab geometry. It does not yet implement MEP routing, coordination, optimization, or validation rules.

Development is phased. Phase 1 establishes the deterministic architectural IFC environment from `config/building.json`; later research phases will use it for coordination experiments and evaluation.

See [RESEARCH.md](RESEARCH.md) for the research question, planned baselines, and evaluation strategy. See [BUILDSPEC.md](BUILDSPEC.md) for the current Phase 1 gate.

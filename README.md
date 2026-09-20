# Autonomous Constraint-Aware MEP Routing in BIM Models

This research project investigates whether a constraint-aware automatic routing system can generate HVAC routes with fewer clashes and lower routing cost than independent shortest-path routing.

The initial model is deliberately small: one parametric floor with perimeter walls, structural columns, a central service shaft, and multiple HVAC terminals. The eventual system will consider obstacle avoidance, IFC output, and later 3D visualization.

Development is phased. Phase 1 establishes a deterministic IFC architecture model from `config/building.json`. Later phases will add routing and evaluation only after that foundation is validated.

## Research metrics

- Terminal connection success rate
- Total route length
- Number of bends
- Clash count
- Routing runtime
- Constraint violations

## Current status

Phase 1 is specified but not implemented. See [BUILDSPEC.md](BUILDSPEC.md) for its acceptance gate and [RESEARCH.md](RESEARCH.md) for the research framing.

# Research framing

## Working title

Autonomous Constraint-Aware MEP Routing in BIM Models

## Research question

Can a constraint-aware automatic routing system generate MEP routes with fewer clashes and lower routing cost than independent shortest-path routing?

## Initial scope

- One floor
- Parametric building
- Perimeter walls
- Structural columns
- One central service shaft
- HVAC routing only
- Multiple HVAC terminals
- Obstacle avoidance
- IFC output
- Later 3D visualization

## Initial metrics

| Metric | Meaning |
| --- | --- |
| Terminal connection success rate | Share of HVAC terminals connected to the service shaft. |
| Total route length | Sum of routed HVAC segment lengths. |
| Number of bends | Total directional changes across routes. |
| Clash count | Number of unresolved route collisions with modeled constraints. |
| Routing runtime | Time required to produce a routing result. |
| Constraint violations | Count of violated clearance or routing constraints. |

The baseline comparison is independent shortest-path routing. The proposed approach will account for constraints across routes when routing is introduced in a later phase.

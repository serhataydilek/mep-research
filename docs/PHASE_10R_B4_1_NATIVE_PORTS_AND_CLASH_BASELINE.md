# Phase 10R-B4.1: Native Ports and Clash Baseline

## Executed baseline

The bounded First Floor region `x=-12..0, y=0..12, z=-1..5 m` was analyzed from corrected world-coordinate progressive meshes. It contained 3 architecture, 17 structure, 19 HVAC, 66 plumbing, and 11 electrical elements. Runtime was 55.540 s with 321,931,925 bytes peak Python-traced memory.

It produced **16 broad-phase candidates**, **0 confirmed intersections**, and **0 clearance claims**. Each candidate is recorded in `viewer/medical_dental_clashes.json` and ignored `validation/results/medical_dental_pre_routing_clashes.json`, with both GlobalIds, discipline pair, world location, method, and limitations. AABB overlap is explicitly `BROAD_PHASE_CANDIDATE_ONLY`; no collision count should be interpreted as zero clashes.

## Port resolution

The IFC2X3-specific `IfcRelConnectsPortToElement` relation resolved all HVAC **7,390/7,390** and plumbing **13,058/13,058** ports to hosts and host world locations. Their explicit topology inventories remain 3,695 and 6,529 `IfcRelConnectsPorts` respectively. Electrical contains no distribution ports in this source.

This establishes host/port placement evidence, not a complete source-to-terminal routing demand: no typed system traversal, terminal/equipment classification, or replacement route scope has yet been proven.

## Viewer baseline

The progressive viewer fetches the clash baseline alongside the chunk manifest and draws yellow markers for qualified candidates. Clicking a marker reveals classification, discipline pair, both source GlobalIds, and the AABB-only method. Markers intentionally do not claim confirmed collision.

## Federation and routing limitation

Federation remains **PARTIALLY VERIFIED** after the unit-scale correction. Continuous triangle intersection, clearance solids, openings, intentional-contact classification, and a relationship-provenanced source-to-terminal replacement scope remain blockers to routing. No frozen benchmark, router, validator, source IFC, or route was modified.

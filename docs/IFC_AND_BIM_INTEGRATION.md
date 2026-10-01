# IFC and BIM Integration

## Import contract

Support readiness is staged, not binary: `IFC_PARSE_READY`, `GEOMETRY_READY`, `PORT_READY`, `TOPOLOGY_READY`, `ROUTER_READY`, and `VALIDATION_READY`. Import records schema, units-to-metres conversion, project/site/building/storey hierarchy, placements/coordinate transforms, tessellated/solid geometry, semantic classification, and exact source GUIDs.

`IfcDistributionPort` is the preferred authoritative endpoint: it represents an inlet/outlet through which material or electrical/communication flow may pass and carries `FlowDirection`, `PredefinedType`, and `SystemType`. Resolve port-to-element ownership and port-to-port connectivity with IFC relationships; preserve incomplete metadata instead of inventing it. Refer to the official IFC 4.3 documentation in [REFERENCES.md](REFERENCES.md).

## Penetrations and output

Existing holes/openings are explicit candidates. New design openings must be represented separately from route geometry: a host element is voided using `IfcRelVoidsElement` to an `IfcOpeningElement`, optionally filled/covered according to model policy. Structural policy is external authority; the router produces candidate evidence, not approval.

Future export is:

```text
internal topology graph → validated centerline/fittings → IfcFlowSegment / subtype
→ IfcFlowFitting → IfcDistributionPort → connectivity relationships
→ opening relationships → output IFC
```

Use appropriate flow segment occurrences (for example `IfcPipeSegment`, `IfcDuctSegment`, and `IfcCableCarrierSegment`) and fitting occurrences, with product placement, representation, system assignment, ports, and connectivity. Exact subtype use must match the selected IFC schema/version and exporter policy.

## Viewer recommendation

Keep the viewer downstream. A browser stack such as Three.js with web-ifc or That Open components is suitable for layered inspection, but it must display validated IFC/geometry rather than dictate routing semantics. Route panels should show ID, system, endpoint provenance, length, bends, vertical travel, penetrations, clearance/constraint state, and validation status.

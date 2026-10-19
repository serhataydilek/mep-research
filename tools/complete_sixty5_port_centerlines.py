"""Strict connector-aware completion assessment; does not alter A* paths."""
from __future__ import annotations
import json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; B=ROOT.parent/'hvac_research_ifc'; V=B/'validation/sixty5_native_centerline_validation.json'; O=B/'validation/sixty5_connector_aware_centerline_completion.json'
def main():
 d=json.loads(V.read_text()); rows=[]
 for c in d['cases']:
  bridges=[]
  for x in c['endpoint_bridges']:
   dot=x['axis_dot_route_direction']; exact=dot is not None and abs(abs(dot)-1.0)<1e-9
   bridges.append({**x,'axis_collinear_without_tolerance':exact,'candidate_status':'REJECTED_AXIS_NOT_COLLINEAR' if not exact else 'UNVERIFIED_FITTING_INTERFACE_REQUIRED','candidate_segment_explicitly_represented':True})
  complete=all(x['candidate_status']=='SUPPORTED' for x in bridges) and not c['exact_retained_mesh_centerline_collisions']
  rows.append({'named_system':c['named_system'],'original_astar_centerline_m':c['planner_centerline_m'],'native_ports':c['native_ports'],'connector_bridges':bridges,'grid_centerline_retained_mesh_hits':c['exact_retained_mesh_centerline_collisions'],'continuous_candidate_length_m':c['planner_route_length_m']+sum(x['displacement_m'] for x in bridges),'classification':'CENTERLINE_VERIFIED' if complete else 'ROUTED_CONNECTOR_UNVERIFIED','reason':'No axis tolerance, fitting geometry, or supported off-grid transition is supplied; arbitrary diagonal port-to-grid bridges are rejected.','determinism':'Original A* path is deterministic; no connector-aware route was accepted.'})
 O.write_text(json.dumps({'method':'Exact port origins and saved A* grid endpoints are evaluated as separate straight candidate segments. No diagonal bridge, snap, fitting, or tolerance is invented.','cases':rows,'overall':'NO_CONTINUOUS_CENTERLINE_PROMOTION','physical_validation':'INCOMPLETE_UNVERIFIED','no_ifc_mutation':True},indent=2)+'\n');print(json.dumps({'cases':len(rows),'accepted':sum(x['classification']=='CENTERLINE_VERIFIED' for x in rows)}))
if __name__=='__main__':main()

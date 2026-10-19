"""Exact, read-only centerline and native-port bridge checks for saved S1 A* output."""
from __future__ import annotations
import json,math,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; B=ROOT.parent/'hvac_research_ifc';sys.path[:0]=[str(B),str(ROOT)]
import ifcopenshell
from src.real_ifc import extract_world_meshes
from src.ifc_mesh_geometry import segment_hits_mesh
RUN=B/'validation/sixty5_native_port_to_port_routing_execution.json'; GEO=B/'validation/sixty5_native_duct_geometry_extraction.json'; OUT=B/'validation/sixty5_native_centerline_validation.json'
def sub(a,b):return [a[i]-b[i] for i in range(3)]
def dot(a,b):return sum(a[i]*b[i] for i in range(3))
def norm(a):return math.sqrt(dot(a,a))
def main():
 r=json.loads(RUN.read_text()); g=json.loads(GEO.read_text()); m=ifcopenshell.open(r['source_ifc']['path']);ids=sorted({h['host_global_id'] for p in g['paths'] for h in p['ordered_port_hosts'] if h.get('host_global_id')}); meshes=extract_world_meshes(m,[m.by_guid(x) for x in ids],.001)['retained_meshes']; by={x['global_id']:x for x in meshes}; cases=[]
 for c in r['cases']:
  p=c['planner_output']; pts=p['world_centerline_m']; repl=set(c['replacement_scope']['excluded_host_global_ids']); retained=[x for x in meshes if x['global_id'] not in repl]; start=c['requested_connection']['start_m']; end=c['requested_connection']['end_m']; sa=c['requested_connection']['start_axis_z']; ea=c['requested_connection']['end_axis_z']; bridges=[('START',start,pts[0],sa),('END',pts[-1],end,ea)]; bridge_rows=[]; collisions=[]
  for role,a,b,axis in bridges:
   vec=sub(b,a); length=norm(vec); cosine=None if not length else dot([x/length for x in vec],axis); hits=[x['global_id'] for x in retained if segment_hits_mesh(a,b,x['positions'],x['indices'])]; bridge_rows.append({'role':role,'native_port_m':a,'grid_endpoint_m':b,'displacement_vector_m':vec,'displacement_m':length,'axis_dot_route_direction':cosine,'retained_mesh_hits':hits,'status':'UNVERIFIED_NO_FITTING_OR_AXIS_TOLERANCE' if not hits else 'REJECTED_RETAINED_MESH_INTERSECTION'})
  for i,(a,b) in enumerate(zip(pts,pts[1:])):
   for x in retained:
    if segment_hits_mesh(a,b,x['positions'],x['indices']):collisions.append({'segment_index':i,'global_id':x['global_id'],'ifc_class':x['ifc_class']})
  length=sum(norm(sub(b,a)) for a,b in zip(pts,pts[1:])); direct=norm(sub(end,start)); cases.append({'named_system':c['named_system'],'native_ports':c['requested_connection'],'grid_resolution_m':.25,'theoretical_cell_center_offset_bound_m':math.sqrt(3)*.25/2,'planner_centerline_m':pts,'planner_route_length_m':length,'direct_native_port_distance_m':direct,'grid_path_valid':p['physical_grid_checks']['grid_path_is_free_and_six_connected'],'exact_retained_mesh_centerline_collisions':collisions,'endpoint_bridges':bridge_rows,'classification':'ROUTED_NOT_CENTERLINE_VERIFIED','reason':'Exact retained-mesh check covers selected retained hosts only; both port bridges lack IFC-supported fitting/axis-engagement specification.'})
 OUT.write_text(json.dumps({'source_run':str(RUN),'coordinate_frame':'original S1 ventilation IFC world metres','method':'Broad phase is implicit in the bounded retained mesh set; exact segment-to-triangle tests use segment_hits_mesh.','cases':cases,'overall':'PHYSICAL_INCOMPLETE_UNVERIFIED','no_ifc_mutation':True},indent=2)+'\n');print(json.dumps({'cases':len(cases),'centerline_hits':[len(x['exact_retained_mesh_centerline_collisions']) for x in cases]}))
if __name__=='__main__':main()

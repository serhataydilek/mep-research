"""Research-only orthogonal port-transition candidates; never changes A* output."""
from __future__ import annotations
import json,itertools,math,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];B=ROOT.parent/'hvac_research_ifc';sys.path[:0]=[str(B),str(ROOT)]
import ifcopenshell
from src.real_ifc import extract_world_meshes
from src.ifc_mesh_geometry import segment_hits_mesh
RUN=B/'validation/sixty5_native_port_to_port_routing_execution.json';GEO=B/'validation/sixty5_native_duct_geometry_extraction.json';OUT=B/'validation/sixty5_parametric_connector_transitions.json'; STUB=.25
def d(a,b):return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))
def legs(a,b,axis,sign,at_start):
 stub=[a[i]+axis[i]*sign*STUB for i in range(3)]; base=[a,stub] if at_start else [a,stub]
 out=[]
 for perm in itertools.permutations(range(3)):
  p=stub[:];q=[p]
  for k in perm:p[k]=b[k];q.append(p[:])
  out.append(q)
 return out
def hits(points,meshes):
 out=set()
 for a,b in zip(points,points[1:]):
  lo=[min(a[i],b[i]) for i in range(3)];hi=[max(a[i],b[i]) for i in range(3)]
  for m in meshes:
   q=m['positions']; bb=(min(q[0::3]),max(q[0::3]),min(q[1::3]),max(q[1::3]),min(q[2::3]),max(q[2::3]))
   if hi[0]<bb[0] or lo[0]>bb[1] or hi[1]<bb[2] or lo[1]>bb[3] or hi[2]<bb[4] or lo[2]>bb[5]:continue
   if segment_hits_mesh(a,b,q,m['indices']):out.add(m['global_id'])
 return sorted(out)
def main():
 r=json.loads(RUN.read_text());g=json.loads(GEO.read_text());m=ifcopenshell.open(r['source_ifc']['path']);ids=sorted({h['host_global_id'] for p in g['paths'] for h in p['ordered_port_hosts'] if h.get('host_global_id')}); meshes=extract_world_meshes(m,[m.by_guid(x) for x in ids],.001)['retained_meshes']; rows=[]
 for c in r['cases']:
  repl=set(c['replacement_scope']['excluded_host_global_ids']);keep=[x for x in meshes if x['global_id'] not in repl];p=c['planner_output']['world_centerline_m'];ep=c['requested_connection']; choices=[]
  for ss,es in itertools.product((-1,1),repeat=2):
   for sl in legs(ep['start_m'],p[0],ep['start_axis_z'],ss,True):
    for elrev in legs(ep['end_m'],p[-1],ep['end_axis_z'],es,False):
     el=list(reversed(elrev)); allpts=sl+p[1:-1]+el; h=hits(allpts,keep); choices.append((h,allpts,ss,es,sl,el))
  choices.sort(key=lambda x:(bool(x[0]),x[0],x[2],x[3],x[1]));h,pts,ss,es,sl,el=choices[0]; added=sum(d(a,b) for a,b in zip(sl,sl[1:]))+sum(d(a,b) for a,b in zip(el,el[1:]));rows.append({'named_system':c['named_system'],'exact_native_ports':ep,'original_astar_centerline_m':p,'candidate_centerline_m':pts,'experimental_transitions':{'label':'EXPERIMENTAL_UNAPPROVED','start_axis_stub_sign':ss,'end_axis_stub_sign':es,'stub_length_m':STUB,'type':'AXIAL_STUB_PLUS_ORTHOGONAL_LEGS','candidate_count':len(choices)},'exact_port_anchored':pts[0]==ep['start_m'] and pts[-1]==ep['end_m'],'added_connector_length_m':added,'total_candidate_length_m':sum(d(a,b) for a,b in zip(pts,pts[1:])),'retained_mesh_hits':h,'status':'EXPERIMENTAL_CANDIDATE_COLLISION_FREE' if not h else 'REJECTED_RETAINED_MESH_COLLISION','classification':'ROUTED_EXPERIMENTAL_TRANSITION_UNVERIFIED','physical_body':'NOT_EVALUATED_NO_FITTING_BODY_SPECIFICATION','axis_interface':'COLLINEAR_STUBS_BUT_DIRECTION_SENSE_AND_FITTING_UNVERIFIED','deterministic':True})
 OUT.write_text(json.dumps({'method':'Finite enumeration of 2 axial signs x 6 orthogonal leg orders at each end; no diagonal bridges; original A* centerlines immutable.','retained_manifest':'Per-case replacement exclusions from source run are preserved; no additional exclusion.','cases':rows,'overall':'EXPERIMENTAL_UNAPPROVED_PHYSICAL_INCOMPLETE','no_ifc_mutation':True},indent=2)+'\n');print(json.dumps({'cases':len(rows),'collision_free':sum(not x['retained_mesh_hits'] for x in rows)}))
if __name__=='__main__':main()

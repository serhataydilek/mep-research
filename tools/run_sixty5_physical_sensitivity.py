"""36-scenario research-only OBB sensitivity study for saved S1 candidates."""
from __future__ import annotations
import json,math,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];B=ROOT.parent/'hvac_research_ifc';sys.path[:0]=[str(B),str(ROOT)]
import ifcopenshell
from src.real_ifc import extract_world_meshes
from src.duct_volume_validation import RectangularDuct,oriented_segment_volume
from src.route_geometry import Segment
from src.ifc_mesh_geometry import triangles_of,triangle_intersects_aabb
T=B/'validation/sixty5_parametric_connector_transitions.json';R=B/'validation/sixty5_native_port_to_port_routing_execution.json';G=B/'validation/sixty5_native_duct_geometry_extraction.json';O=B/'validation/sixty5_physical_duct_volume_sensitivity.json'
def sub(a,b):return tuple(a[i]-b[i] for i in range(3))
def dot(a,b):return sum(a[i]*b[i] for i in range(3))
def norm(a):return math.sqrt(dot(a,a))
def unit(a):
 n=norm(a);return None if n<1e-9 else tuple(x/n for x in a)
def up(t,roll):
 base=(0,0,1) if abs(t[2])<.9 else (1,0,0); side=unit((t[1]*base[2]-t[2]*base[1],t[2]*base[0]-t[0]*base[2],t[0]*base[1]-t[1]*base[0])); vertical=unit((side[1]*t[2]-side[2]*t[1],side[2]*t[0]-side[0]*t[2],side[0]*t[1]-side[1]*t[0]));return vertical if roll==0 else side
def bbox(m):
 p=m['positions'];return(min(p[0::3]),max(p[0::3]),min(p[1::3]),max(p[1::3]),min(p[2::3]),max(p[2::3]))
def contacts(obb,meshes):
 c=obb['center_m'];axes=obb['axes'];half=obb['half_extents_m']; lo=[];hi=[]
 for i in range(3):
  e=sum(abs(axes[j][i])*half[j] for j in range(3));lo.append(c[i]-e);hi.append(c[i]+e)
 hits=[]
 for m,bb in meshes:
  if hi[0]<bb[0] or lo[0]>bb[1] or hi[1]<bb[2] or lo[1]>bb[3] or hi[2]<bb[4] or lo[2]>bb[5]:continue
  for tri in triangles_of(m['positions'],m['indices']):
   local=[tuple(dot(sub(v,c),a) for a in axes) for v in tri]
   if triangle_intersects_aabb(*local,tuple(-x for x in half),half):hits.append(m['global_id']);break
 return sorted(set(hits))
def compact(points):
 out=[points[0]]
 for p in points[1:]:
  if len(out)<2:out.append(p);continue
  a,b=out[-2],out[-1];u=unit(sub(b,a));v=unit(sub(p,b))
  if u and v and all(abs(u[i]-v[i])<1e-9 for i in range(3)):out[-1]=p
  else:out.append(p)
 return out
def main():
 tr=json.loads(T.read_text());run=json.loads(R.read_text());geo=json.loads(G.read_text());m=ifcopenshell.open(run['source_ifc']['path']);ids=sorted({h['host_global_id'] for p in geo['paths'] for h in p['ordered_port_hosts'] if h.get('host_global_id')});allm=extract_world_meshes(m,[m.by_guid(x) for x in ids],.001)['retained_meshes']; matrix=[]; dims=[(.15,.10),(.25,.15),(.40,.20)]
 for case in tr['cases']:
  repl=set(next(x for x in run['cases'] if x['named_system']==case['named_system'])['replacement_scope']['excluded_host_global_ids']);meshes=[(x,bbox(x)) for x in allm if x['global_id'] not in repl];pts=compact(case['candidate_centerline_m'])
  for w,h in dims:
   for clear in (0,.05):
    for roll in (0,90):
     started=time.perf_counter(); body=[];env=[];unsupported=[]
     for i,(a,b) in enumerate(zip(pts,pts[1:])):
      t=unit(sub(b,a));obb=oriented_segment_volume(Segment(tuple(a),tuple(b)),RectangularDuct(w,h,clear),up(t,roll));
      if obb is None:unsupported.append(i);continue
      body+=contacts(obb,meshes); env+=contacts(oriented_segment_volume(Segment(tuple(a),tuple(b)),RectangularDuct(w,h,clear),up(t,roll),clearance_m=clear),meshes)
     bends=max(0,len(pts)-2); status='EXPERIMENTAL_BODY_COLLISION' if body else 'EXPERIMENTAL_CLEARANCE_VIOLATION' if env else 'EXPERIMENTAL_BODY_CLEAR_WITH_DEFINED_COVERAGE'
     matrix.append({'route_id':case['named_system'],'duct_width_m':w,'duct_height_m':h,'clearance_m':clear,'roll_degrees':roll,'straight_segment_count':len(pts)-1,'bend_transition_proxy_count':bends,'body_intersected_global_ids':sorted(set(body)),'clearance_intersected_global_ids':sorted(set(env)),'unsupported_segment_count':len(unsupported),'excluded_replacement_host_count':len(repl),'retained_mesh_count':len(meshes),'status':status,'proxy_limitations':'Bends/transitions are centerline-junction bounding proxies only; no manufacturer fitting solid or interface volume is validated.','runtime_s':round(time.perf_counter()-started,6),'deterministic':True})
 O.write_text(json.dumps({'classification':'RESEARCH_ONLY_UNAPPROVED_PHYSICAL_SENSITIVITY','parameter_matrix':{'sections_m':dims,'clearance_m':[0,.05],'roll_degrees':[0,90],'scenario_count':len(matrix)},'scenarios':matrix,'physical_validation':'INCOMPLETE_UNVERIFIED','architecture_structure_excluded':'unregistered cross-model geometry','no_ifc_mutation':True},indent=2)+'\n');print(json.dumps({'scenarios':len(matrix),'collisions':sum(x['status']!='EXPERIMENTAL_BODY_CLEAR_WITH_DEFINED_COVERAGE' for x in matrix)}))
if __name__=='__main__':main()

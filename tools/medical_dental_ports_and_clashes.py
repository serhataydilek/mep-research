"""Read-only native-port inventory and bounded pre-routing clash baseline."""
from __future__ import annotations
import json,time,tracemalloc
from pathlib import Path
import ifcopenshell,ifcopenshell.util.placement,ifcopenshell.util.unit
ROOT=Path(__file__).resolve().parents[1]; IFC=ROOT/'validation/ifc_samples/external/official_medical_dental'; VIEW=ROOT/'viewer'
FILES={'hvac':'Clinic_HVAC.ifc','plumbing':'Clinic_Plumbing.ifc','electrical':'Clinic_Electrical.ifc'}
REGION={'min_x_m':-12.,'max_x_m':0.,'min_y_m':0.,'max_y_m':12.,'min_z_m':-1.,'max_z_m':5.}
def overlap(a,b):return not(a['max_x_m']<b['min_x_m']or a['min_x_m']>b['max_x_m']or a['max_y_m']<b['min_y_m']or a['min_y_m']>b['max_y_m']or a['max_z_m']<b['min_z_m']or a['min_z_m']>b['max_z_m'])
def center(a):return [(a['min_x_m']+a['max_x_m'])/2,(a['min_y_m']+a['max_y_m'])/2,(a['min_z_m']+a['max_z_m'])/2]
def ports(role,path):
 m=ifcopenshell.open(str(path));scale=float(ifcopenshell.util.unit.calculate_unit_scale(m)); hosts={}
 for rel in m.by_type('IfcRelNests'):
  for child in rel.RelatedObjects:
   if child.is_a('IfcDistributionPort'):hosts[child.id()]=rel.RelatingObject
 for rel in m.by_type('IfcRelConnectsPortToElement'):
  if rel.RelatingPort: hosts[rel.RelatingPort.id()]=rel.RelatedElement
 rows=[]
 for p in m.by_type('IfcDistributionPort'):
  h=hosts.get(p.id()); origin=None
  if h and h.ObjectPlacement:
   try:
    matrix=ifcopenshell.util.placement.get_local_placement(h.ObjectPlacement);origin=[float(matrix[i][3])*scale for i in range(3)]
   except Exception:pass
  rows.append({'port_global_id':p.GlobalId,'host_global_id':None if h is None else h.GlobalId,'host_class':None if h is None else h.is_a(),'flow_direction':str(p.FlowDirection),'world_m':origin})
 links=[{'relationship_global_id':r.GlobalId,'relating_port_global_id':r.RelatingPort.GlobalId,'related_port_global_id':r.RelatedPort.GlobalId} for r in m.by_type('IfcRelConnectsPorts')]
 return {'file_name':path.name,'metre_scale':scale,'port_count':len(rows),'host_resolved_count':sum(x['host_global_id'] is not None for x in rows),'world_resolved_count':sum(x['world_m'] is not None for x in rows),'ports':rows,'explicit_connections':links}
def meshes(layer):
 manifest=json.loads((VIEW/'medical_dental_clinic_progressive.json').read_text());entry=next(x for x in manifest['layers'] if x['id']==layer);out=[]
 for r in entry['chunks']:
  out.extend(json.loads((VIEW/r['url']).read_text())['meshes'])
 return out
def main():
 started=time.perf_counter();tracemalloc.start(); port_data={k:ports(k,IFC/v) for k,v in FILES.items()}; layers={k:meshes(k) for k in ('architecture','structure','hvac','plumbing','electrical')}; selected={k:[x for x in v if overlap(x['bounds_m'],REGION)] for k,v in layers.items()}
 pairs=[('hvac','plumbing'),('hvac','structure'),('plumbing','structure'),('electrical','hvac'),('electrical','plumbing'),('electrical','structure')]; clashes=[];seen=set()
 for a,b in pairs:
  for left in selected[a]:
   for right in selected[b]:
    key=(a,left['global_id'],b,right['global_id'])
    if key not in seen and overlap(left['bounds_m'],right['bounds_m']):
     seen.add(key);clashes.append({'element_a_global_id':left['global_id'],'element_b_global_id':right['global_id'],'source_a':a,'source_b':b,'discipline_pair':f'{a}_vs_{b}','classification':'BROAD_PHASE_CANDIDATE_ONLY','detection_method':'world-space mesh AABB overlap; no triangle confirmation','confidence':'LOW','world_m':center(left['bounds_m']),'clearance_threshold_m':0.0,'limitations':['AABB overlap is not a confirmed physical clash.','Cross-discipline federation remains PARTIALLY_VERIFIED.']})
 current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
 report={'schema_version':1,'kind':'MEDICAL_DENTAL_PRE_ROUTING_CLASH_BASELINE','region_bounds_m':REGION,'federation_status':'PARTIALLY_VERIFIED','ports':port_data,'selected_element_counts':{k:len(v) for k,v in selected.items()},'clashes':clashes,'summary':{'candidate_count':len(clashes),'confirmed_geometric_intersection_count':0,'runtime_seconds':round(time.perf_counter()-started,6),'peak_python_memory_bytes':peak},'limitations':['No triangle-mesh intersection performed.','No AABB candidate is a confirmed clash.']}
 (VIEW/'medical_dental_clashes.json').write_text(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n');(ROOT/'validation/results').mkdir(parents=True,exist_ok=True);(ROOT/'validation/results/medical_dental_pre_routing_clashes.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['summary']))
if __name__=='__main__':main()

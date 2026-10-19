"""Read-only Medical-Dental Clinic IFC federation/export adapter."""
from __future__ import annotations
import argparse, hashlib, json, time, sys
from pathlib import Path
from typing import Any
import ifcopenshell, ifcopenshell.geom
import ifcopenshell.util.unit

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'validation/ifc_samples/external/official_medical_dental'
FILES={
 'architecture':'Clinic_Architectural.ifc','structure':'Clinic_Structural.ifc','hvac':'Clinic_HVAC.ifc',
 'plumbing':'Clinic_Plumbing.ifc','electrical':'Clinic_Electrical.ifc'}
EXPECTED={'Clinic_Architectural.ifc':'2ac970ce065ecac4e0c9e5f453a257169e90d0067f419b7e33533a64ef837880','Clinic_Structural.ifc':'325287b375f154dc79504c4bbf67b40c501f458f23a8c4ba13b27171e00533d8','Clinic_HVAC.ifc':'39c88a79f48fbe56da86afb0fb3ebd188f8930df3df7dbfbd9ecaa535aaeab9b','Clinic_Plumbing.ifc':'e662a8d0273694b745a313fc18ee8d4916db03453caa8f9d00d4d80b62bb6e23','Clinic_Electrical.ifc':'70c436de1f1e8a860acfba61b68da1c9243d18dcbbddb98b88b550a642d95d32'}
# None means complete selected-region coverage. Limits are opt-in and always reported.
DEFAULT_MAX_MESHES=None

def sha256(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def count(model,name):
 try:return len(model.by_type(name))
 except RuntimeError:return 0
def bounds(values):
 xs,ys,zs=values[::3],values[1::3],values[2::3]
 return {'min_x_m':min(xs),'max_x_m':max(xs),'min_y_m':min(ys),'max_y_m':max(ys),'min_z_m':min(zs),'max_z_m':max(zs)}
def element_storeys(item):
 return sorted({getattr(rel.RelatingStructure,'Name',None) or rel.RelatingStructure.GlobalId for rel in getattr(item,'ContainedInStructure',[])})
def inventory(path:Path)->dict[str,Any]:
 model=ifcopenshell.open(str(path)); scale=float(ifcopenshell.util.unit.calculate_unit_scale(model)); project=model.by_type('IfcProject')[0]
 return {'file_name':path.name,'sha256':sha256(path),'size_bytes':path.stat().st_size,'step_header':path.read_bytes()[:64].startswith(b'ISO-10303-21;'),'schema':model.schema,'metre_scale':scale,'project_global_id':project.GlobalId,'storeys':[{'global_id':x.GlobalId,'name':x.Name,'elevation':getattr(x,'Elevation',None)} for x in model.by_type('IfcBuildingStorey')],'counts':{x:count(model,x) for x in ('IfcProduct','IfcWall','IfcColumn','IfcBeam','IfcSlab','IfcOpeningElement','IfcDoor','IfcWindow','IfcSpace','IfcDistributionPort','IfcRelConnectsPorts','IfcFlowSegment','IfcFlowFitting','IfcFlowTerminal','IfcFlowController')}}
def _first_floor(item,role):
 names=element_storeys(item)
 if role=='structure': return 'First Floor' in names
 if role=='plumbing': return 'Level 1' in names
 return any(name.startswith('1') for name in names)
def progress(label:str,current:int,total:int,started:float,enabled:bool)->None:
 if not enabled:return
 width=28; filled=width if not total else round(width*current/total); elapsed=time.perf_counter()-started
 sys.stderr.write(f'\r{label:<12} [{"#"*filled}{"."*(width-filled)}] {current:>5}/{total:<5} {elapsed:6.1f}s')
 if current>=total:sys.stderr.write('\n')
 sys.stderr.flush()
def mesh_layer(layer,path:Path,selected_storey:str|None,max_meshes:int|None=DEFAULT_MAX_MESHES,show_progress:bool=False)->dict[str,Any]:
 model=ifcopenshell.open(str(path)); scale=float(ifcopenshell.util.unit.calculate_unit_scale(model)); settings=ifcopenshell.geom.settings();settings.set(settings.USE_WORLD_COORDS,True)
 candidates=[x for x in model.by_type('IfcProduct') if getattr(x,'Representation',None)]
 contained=[x for x in candidates if _first_floor(x,layer)]
 candidates=contained
 meshes=[]; failures=[]
 started=time.perf_counter(); omitted_by_limit=[]
 for ordinal,item in enumerate(sorted(candidates,key=lambda x:x.GlobalId or ''),start=1):
  if max_meshes is not None and len(meshes)>=max_meshes:omitted_by_limit.append(item.GlobalId);continue
  try:
   # IfcOpenShell geometry is already converted to SI metres; applying the IFC
   # project scale here would shrink millimetre-authored models a second time.
   shape=ifcopenshell.geom.create_shape(settings,item); verts=[float(v) for v in shape.geometry.verts]; indices=[int(v) for v in shape.geometry.faces]
   if not verts or not indices:raise ValueError('empty mesh')
   meshes.append({'global_id':item.GlobalId,'ifc_class':item.is_a(),'name':item.Name,'storeys':element_storeys(item),'positions':verts,'indices':indices,'bounds_m':bounds(verts)})
  except Exception as exc:failures.append({'global_id':item.GlobalId,'reason':f'{type(exc).__name__}: {exc}'})
  if ordinal==1 or ordinal==len(candidates) or ordinal%25==0:progress(layer,ordinal,len(candidates),started,show_progress)
 return {'id':layer,'label':layer,'source_file':path.name,'mesh_count':len(meshes),'candidate_product_count':len(candidates),'exported_element_count':len(meshes),'skipped_element_count':len(failures)+len(omitted_by_limit),'limit_omitted_global_ids':omitted_by_limit,'mesh_cap':max_meshes,'mesh_selection':'first-floor containment convention per discipline; source meshes are untransformed','meshes':meshes,'failures':failures,'triangle_count':sum(len(x['indices'])//3 for x in meshes),'export_seconds':round(time.perf_counter()-started,6)}
def build(data_dir:Path,output:Path,max_meshes:int|None=DEFAULT_MAX_MESHES,show_progress:bool=False)->dict[str,Any]:
 records={role:inventory(data_dir/name) for role,name in FILES.items()}
 for role,record in records.items():
  if record['sha256']!=EXPECTED[record['file_name']]:raise ValueError(f'hash mismatch: {record["file_name"]}')
 storey=records['architecture']['storeys'][0] if records['architecture']['storeys'] else None; selected=None if storey is None else storey['name']
 layers=[mesh_layer(role,data_dir/FILES[role],selected,max_meshes,show_progress) for role in FILES]
 connectivity={}
 for role,record in records.items():
  ports=record['counts']['IfcDistributionPort']; links=record['counts']['IfcRelConnectsPorts']
  connectivity[role]={'verified_explicit_port_connections':links,'port_count':ports,'unconnected_port_lower_bound':max(0,ports-2*links),'ambiguous_relationships':'NOT_CLASSIFIED','geometrically_inferred_candidates':0,'inference_policy':'No geometric inference performed.'}
 result={'schema_version':4,'kind':'REAL_IFC_FEDERATED_VIEWER','units':'m','scenario_id':'medical-dental-clinic-official-ifc2x3','coordinate_frame':'ORIGINAL_IFC_WORLD_METRES_PER_DISCIPLINE_NO_ALIGNMENT_TRANSFORM','applied_alignment_transform':None,'federation_status':'UNVERIFIED_CROSS_DISCIPLINE_REGISTRATION','selected_storey':storey,'source_manifest':{'upstream_revision':'7ddf57a201f88a0c213d5322b02ed15e94a60a40','files':records},'layers':layers,'connectivity_inventory':connectivity,'warnings':['Each layer uses its IFC world coordinates after declared-unit conversion. No cross-discipline registration transform was applied.','Limits, if present, are explicit GlobalId omissions; no geometry is silently sampled.','Native connection counts are explicit IFC relationships only; no inferred edge is marked verified.'],'routing_benchmark_authorized':False}
 output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n',encoding='utf8');return result
def main():
 p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,default=DEFAULT);p.add_argument('--output',type=Path,default=ROOT/'viewer/medical_dental_clinic_fixture.json');p.add_argument('--max-meshes',type=int,default=None,help='Optional explicit per-discipline limit; default exports all supported selected-region elements.');p.add_argument('--progress',action='store_true',help='Show per-discipline tessellation progress on stderr.');a=p.parse_args();d=build(a.data_dir,a.output,a.max_meshes,a.progress);print(json.dumps({'output':str(a.output),'layers':[(x['id'],x['mesh_count'],x['candidate_product_count']) for x in d['layers']],'federation_status':d['federation_status']}))
if __name__=='__main__':main()

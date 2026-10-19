"""Authorized software-only routing of S1's real, provenanced port pairs."""
from __future__ import annotations
import json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; B=ROOT.parent/'hvac_research_ifc'; sys.path[:0]=[str(B),str(ROOT)]
import ifcopenshell
from run_native_ifc_readiness_audit import sha256
from src.real_ifc import extract_world_meshes
from src.ifc_port_routing import hvac_clearance_policy
from src.clearance_policy import legacy_obstacle_margin_m
from src.regional_routing import bounds_from_endpoints,build_regional_backend,route_between_points
CFG=B/'validation/sixty5_native_port_routing_experiment_configuration.json'; BENCH=B/'validation/sixty5_native_port_to_port_benchmark.json'; GEO=B/'validation/sixty5_native_duct_geometry_extraction.json'; OUT=B/'validation/sixty5_native_port_to_port_routing_execution.json'
def main():
 c=json.loads(CFG.read_text()); b=json.loads(BENCH.read_text()); g=json.loads(GEO.read_text()); src=Path(c['dataset']['path']); m=ifcopenshell.open(str(src)); scale=c['dataset']['unit_scale_m']
 ids=sorted({h['host_global_id'] for p in g['paths'] for h in p['ordered_port_hosts'] if h.get('host_global_id')}); geo=extract_world_meshes(m,[m.by_guid(x) for x in ids],scale)
 meshes=[{'global_id':x['global_id'],'ifc_class':x['ifc_class'],'positions':x['positions'],'indices':x['indices'],'bounds_m':{'min_x_m':min(x['positions'][0::3]),'max_x_m':max(x['positions'][0::3]),'min_y_m':min(x['positions'][1::3]),'max_y_m':max(x['positions'][1::3]),'min_z_m':min(x['positions'][2::3]),'max_z_m':max(x['positions'][2::3])}} for x in geo['retained_meshes']]; policy=hvac_clearance_policy(B); planar,vertical=legacy_obstacle_margin_m(policy,'hvac','PERIMETER_WALL'); path_hosts={p['named_system']:sorted({h['host_global_id'] for h in p['ordered_port_hosts'] if h.get('host_global_id')}) for p in g['paths']}; cases=[]
 for p in b['selected_existing_paths']:
  a=tuple(p['start_port']['origin_m']); z=tuple(p['end_port']['origin_m']); replacement_ids=path_hosts[p['named_system']]; retained=[x for x in meshes if x['global_id'] not in replacement_ids]; start=time.perf_counter()
  try:
   back=build_regional_backend(retained,bounds_from_endpoints([a,z],1),.25,planar_margin_m=planar,vertical_margin_m=vertical,cell_limit=500000); first=route_between_points(back,a,z,max_expansions=50000); second=route_between_points(back,a,z,max_expansions=50000)
  except Exception as exc:
   first={'status':'ROUTING_SETUP_OR_OCCUPANCY_ERROR','error':f'{type(exc).__name__}: {exc}','path_found':False,'expanded_node_count':0}; second=first
  cases.append({'classification':'REAL_IFC_NATIVE_PORT_TO_PORT_SOFTWARE_EXPERIMENT','named_system':p['named_system'],'requested_connection':{'start_port_global_id':p['start_port']['global_id'],'end_port_global_id':p['end_port']['global_id'],'start_m':a,'end_m':z,'start_axis_z':p['start_port']['orientation_z'],'end_axis_z':p['end_port']['orientation_z']},'relationship_provenance':p['relationship_provenance'],'existing_network_path_port_global_ids':p['port_global_ids'],'existing_network_edge_count':len(p['relationship_provenance']),'planner_output':first,'deterministic_repeat':first==second,'runtime_seconds':round(time.perf_counter()-start,6),'replacement_scope':{'label':'SOFTWARE_ONLY_EXPERIMENTAL','excluded_host_global_ids':replacement_ids,'excluded_host_count':len(replacement_ids),'reason':'Only hosts belonging to this exact, relationship-provenanced existing path are scoped as candidate replacement geometry.'},'retained_existing_obstacle_count':len(retained),'physical_validation':'INCOMPLETE_UNVERIFIED'})
 out={'authorization_scope':'EXPLICIT_USER_AUTHORIZED_SOFTWARE_ONLY_S1_PORT_TO_PORT','source_ifc':{'path':str(src),'sha256':sha256(src),'schema':m.schema,'coordinate_frame':'original ventilation IFC world metres'},'routing_executed':True,'parameters':{'resolution_m':.25,'search_margin_m':1,'max_expansions':50000,'clearance_policy':policy.policy_id,'parameter_status':'EXPERIMENTAL_NOT_ENGINEERING_APPROVED'},'geometry':{'retained_ifc_host_obstacle_count':len(meshes),'tessellation_failures':geo['geometry_extraction_failures'],'architectural_or_structural_geometry_combined':False},'cases':cases,'physical_validation':'INCOMPLETE_UNVERIFIED','no_source_to_terminal_claim':True,'no_ifc_mutation':True}
 OUT.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps({'executed':True,'cases':[(x['named_system'],x['planner_output']['status']) for x in cases],'mesh_seconds':geo['extraction_seconds']}))
if __name__=='__main__':main()

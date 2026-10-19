"""Export bounded real S1 meshes and research route overlay for existing viewer."""
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];B=ROOT.parent/'hvac_research_ifc';sys.path[:0]=[str(B)]
import ifcopenshell
from src.real_ifc import extract_world_meshes
R=B/'validation/sixty5_native_port_to_port_routing_execution.json';T=B/'validation/sixty5_parametric_connector_transitions.json';G=B/'validation/sixty5_native_duct_geometry_extraction.json';V=B/'viewer'
def main():
 r=json.loads(R.read_text());t=json.loads(T.read_text());g=json.loads(G.read_text());m=ifcopenshell.open(r['source_ifc']['path']);ids=sorted({h['host_global_id'] for p in g['paths'] for h in p['ordered_port_hosts'] if h.get('host_global_id')});x=extract_world_meshes(m,[m.by_guid(i) for i in ids],.001);cat=lambda c:'DUCT' if c=='IfcDuctSegment' else 'FITTING' if c=='IfcDuctFitting' else 'EQUIPMENT'; fixture={'schema_version':3,'kind':'REAL_IFC','units':'m','scenario_id':'sixty5-s1-native-port-routing','coordinate_frame':'S1_VENTILATION_NATIVE_IFC_WORLD_METRES','native_frame_only':True,'sources':{'ventilation':{'file_name':Path(r['source_ifc']['path']).name,'sha256':r['source_ifc'].get('sha256'),'role':'ORIGINAL_READ_ONLY_IFC'}},'fabricated_routes':False,'routing_results_included':False,'applied_alignment_transform':None,'reference_geometry_is_planner_hint':False,'viewer_scope':'BOUNDED_SELECTED_S1_VENTILATION_HOSTS_ONLY','warnings':['Experimental routes and transition proxies are unapproved. No architecture or structure is shown.'],'layers':[{'id':'ventilation','label':'S1 selected retained/replacement ventilation hosts','color':'#ff9f1c','opacity':.35,'mesh_count':len(x['retained_meshes']),'triangle_count':sum(z['triangle_count'] for z in x['retained_meshes']),'failure_count':len(x['failures']),'failures':x['failures'],'meshes':[{**z,'viewer_category':cat(z['ifc_class'])} for z in x['retained_meshes']]}],'ports':[]}
 routes=[]
 for c in t['cases']:
  run=next(q for q in r['cases'] if q['named_system']==c['named_system']);ep=c['exact_native_ports'];fixture['ports'] += [{'global_id':ep['start_port_global_id'],'world_m':ep['start_m'],'classification':'NATIVE_IFC_PORT','axis_z':ep['start_axis_z']},{'global_id':ep['end_port_global_id'],'world_m':ep['end_m'],'classification':'NATIVE_IFC_PORT','axis_z':ep['end_axis_z']}];routes.append({'route_id':c['named_system'],'status':'ROUTED','world_polyline_m':c['candidate_centerline_m'],'original_astar_centerline_m':c['original_astar_centerline_m'],'ports':[{'global_id':ep['start_port_global_id'],'world_m':ep['start_m']},{'global_id':ep['end_port_global_id'],'world_m':ep['end_m']}],'experimental_transition':c['experimental_transitions'],'replacement_host_global_ids':run['replacement_scope']['excluded_host_global_ids'],'validation':'EXPERIMENTAL_UNAPPROVED_PHYSICAL_INCOMPLETE_UNVERIFIED'})
 overlay={'schema_version':1,'kind':'IFC_PORT_ROUTE_OVERLAY','units':'m','zero_clash_claimed':False,'reference_geometry_used_as_planner_hint':False,'fittings_generated':False,'selected_route_id':routes[0]['route_id'],'routes':routes,'experimental_parameters':'Select existing sensitivity result externally; viewer does not recompute.'}
 (V/'sixty5_s1_native_fixture.json').write_text(json.dumps(fixture,indent=2)+'\n');(V/'sixty5_s1_native_routes_overlay.json').write_text(json.dumps(overlay,indent=2)+'\n');(B/'validation'/'sixty5_viewer_export_error.json').unlink(missing_ok=True);print(json.dumps({'meshes':len(x['retained_meshes']),'routes':len(routes)}))
if __name__=='__main__':
 try: main()
 except Exception as exc:
  (B/'validation'/'sixty5_viewer_export_error.json').write_text(json.dumps({'error':f'{type(exc).__name__}: {exc}'},indent=2)+'\n');raise

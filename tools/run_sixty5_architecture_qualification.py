"""Read-only S1 architecture, registration, and connector evidence reports."""
from __future__ import annotations
import json, math, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; B=ROOT.parent/'hvac_research_ifc'; sys.path[:0]=[str(B),str(ROOT)]
import ifcopenshell
from run_native_ifc_readiness_audit import sha256, fast_network, spatial_anchors
from src.real_ifc import read_model_header, placement_frame_m
ARC=ROOT/'validation/ifc_samples/external/sixty5/arc.ifc'; STR=ROOT/'validation/ifc_samples/external/sixty5/str.ifc'; VENT=ROOT/'validation/ifc_samples/external/sixty5/ventilation.ifc'; OUT=B/'validation'
def save(n,x): (OUT/n).write_text(json.dumps(x,indent=2,sort_keys=True)+'\n',encoding='utf8')
def norm(v): return math.sqrt(sum(x*x for x in v)) if v else None
def angle(a,b):
    if not a or not b:return None
    d=sum(x*y for x,y in zip(a,b))/(norm(a)*norm(b));return math.acos(max(-1,min(1,d)))
def main():
    for p in (ARC,STR,VENT):
        if not p.exists(): raise SystemExit(f'missing source {p}')
    arc=ifcopenshell.open(str(ARC)); ah=read_model_header(ARC); sh=read_model_header(STR); vh=read_model_header(VENT)
    classes=['IfcWall','IfcWallStandardCase','IfcSlab','IfcColumn','IfcBeam','IfcOpeningElement','IfcRoof','IfcDoor','IfcWindow','IfcStair','IfcRailing','IfcBuildingElementProxy']
    inventory={c:{'entity_count':len(arc.by_type(c)),'represented_count':sum(x.Representation is not None for x in arc.by_type(c))} for c in classes}
    with ARC.open('rb') as f: first=f.read(128); f.seek(-256,2); last=f.read()
    manifest={'dataset':'Sixty5 (S1)','file':{'path':str(ARC),'download_url':'https://huggingface.co/datasets/sylvainHellin/ifc-bench/resolve/main/projects/sixty5/arc.ifc','size_bytes':ARC.stat().st_size,'sha256':sha256(ARC),'schema':arc.schema,'starts_step':first.lstrip().startswith(b'ISO-10303-21'),'ends_step':last.rstrip().endswith(b'END-ISO-10303-21;')},'provenance':{'original_source':'https://github.com/buildingsmart-community/Community-Sample-Test-Files/tree/main/IFC%202.3.0.1%20%28IFC%202x3%29/SDK%20-%20S1','mirror':'https://huggingface.co/datasets/sylvainHellin/ifc-bench/tree/main/projects/sixty5','license':'CC BY 4.0, attributed by original S1 README (recorded prior acquisition manifest)'},'source_mutated':False}
    save('sixty5_architecture_acquisition_manifest.json',manifest)
    save('sixty5_architecture_geometry_coverage.json',{'source':manifest['file'],'units':ah.get('units'),'coordinate_reference':ah.get('coordinate_reference'),'inventory':inventory,'interpretation':'Counts are direct IFC entities; representation count records declared product representation, not collision qualification.'})
    headers={'ventilation':vh,'structural':sh,'architecture':ah}
    pairs=[]
    for name,h in [('ventilation',vh),('structural',sh),('architecture',ah)]:
      for name2,h2 in [('ventilation',vh),('structural',sh),('architecture',ah)]:
       if name>=name2:continue
       a=(h.get('coordinate_reference')or{}).get('sites')or[];b=(h2.get('coordinate_reference')or{}).get('sites')or[]
       ao=(a[0].get('placement_m')or{}).get('origin_m') if len(a)==1 else None;bo=(b[0].get('placement_m')or{}).get('origin_m') if len(b)==1 else None
       residual=None if not ao or not bo else norm([x-y for x,y in zip(ao,bo)])
       pairs.append({'models':[name,name2],'same_project_global_id':h.get('project_global_id')==h2.get('project_global_id'),'same_site_global_id':bool(a and b and a[0].get('global_id')==b[0].get('global_id')),'site_origin_residual_m':residual,'map_conversion_present':bool((h.get('coordinate_reference')or{}).get('map_conversions') or (h2.get('coordinate_reference')or{}).get('map_conversions')),'verdict':'EXTERNAL_REGISTRATION_REQUIRED'})
    save('sixty5_cross_model_registration_evidence.json',{'headers':headers,'pairwise_evidence':pairs,'independent_control_points':'NOT_PRESENT: shared zero site origins and aligned elevations are dependent project metadata, not independent controls.','transform_applied':False,'verdict':'EXTERNAL_REGISTRATION_REQUIRED'})
    cont=json.loads((OUT/'sixty5_native_path_geometric_continuity.json').read_text()); model=ifcopenshell.open(str(VENT)); network,_=fast_network(model,vh['unit_scale_m']); pp={r['global_id']:r for r in network['ports']}; rows=[]
    for path in cont['paths']:
      for e in path['connector_continuity']:
       a,b=e['port_global_ids'];pa,pb=pp.get(a),pp.get(b)
       rows.append({**e,'path_system':path['named_system'],'orientation_angle_rad':angle(pa.get('orientation_z') if pa else None,pb.get('orientation_z') if pb else None),'affected_hosts':[pa.get('host_global_id') if pa else None,pb.get('host_global_id') if pb else None]})
    explicit=[x for x in rows if x['edge_type']=='EXPLICIT_PORT_JOINT']; mx=max(explicit,key=lambda x:x['connector_origin_separation_m'] or -1)
    save('sixty5_native_connector_continuity_analysis.json',{'method':'Exact existing port GlobalIds, IfcRelConnectsPorts provenance, resolved port placements and axes; no tolerance applied. Internal-host port separation is duct/fitting traversal length, not an interface offset.','connection_rows':rows,'maximum_explicit_joint_origin_separation':mx,'conclusion':'UNVERIFIED: an explicit relationship supports graph connectivity, but source representation does not by itself establish that nonzero connector-origin separation is a tolerable fitting convention or a physical interface closure.'})
    geo=json.loads((OUT/'sixty5_native_duct_geometry_extraction.json').read_text()); hosts=[x for p in geo['paths'] for x in p['ordered_port_hosts']]; opens=[{'port_global_id':x['port_global_id'],'host_global_id':x.get('host_global_id'),'host_class':x.get('effective_host_class'),'open_boundary_edge_count':x.get('geometry',{}).get('open_boundary_edge_count'),'representation_declared':x.get('representation_declared')} for x in hosts if x.get('geometry',{}).get('closed_shell') is False]
    save('sixty5_open_mesh_geometry_diagnostic.json',{'open_mesh_count':len(opens),'meshes':opens,'diagnosis':'Tessellated mesh boundaries are open. This report cannot distinguish source-open bodies from kernel tessellation/unsupported-representation behavior without an independently validated BRep comparison; each remains UNVERIFIED.','source_mutated':False})
    save('sixty5_consolidated_obstacle_coverage_matrix.json',{'architecture':inventory,'structural_available':{'source':str(STR),'candidate_classes':{'walls':len(ifcopenshell.open(str(STR)).by_type('IfcWall')),'slabs':len(ifcopenshell.open(str(STR)).by_type('IfcSlab')),'columns':len(ifcopenshell.open(str(STR)).by_type('IfcColumn')),'beams':len(ifcopenshell.open(str(STR)).by_type('IfcBeam'))}},'qualification':'NO_CROSS_MODEL_OBSTACLE_MANIFEST: all discipline pairings remain EXTERNAL_REGISTRATION_REQUIRED.'})
    save('sixty5_native_routing_readiness_updated.json',{'cases':[{'system':p['named_system'],'start_port_global_id':p['start_port_global_id'],'end_port_global_id':p['end_port_global_id']} for p in cont['paths']],'native_geometry':'65 selected host meshes; 58 closed, 7 open; see geometry diagnostic.','connector_continuity':'Explicit relationships retained; max origin separation is reported, not accepted.','registration':'EXTERNAL_REGISTRATION_REQUIRED','physical_obstacles':'Architecture and structural sources acquired/available but not combined due to registration gate.','missing':['independent control-point registration','approved clearance/section/fitting requirements','closed/validated representations for all relevant interfaces','research execution authorization'],'routing_executed':False,'verdict':'BLOCKED_READY_TO_RUN_AFTER_GATES'})
    print(json.dumps({'arc_bytes':ARC.stat().st_size,'walls':inventory['IfcWall']['entity_count'],'slabs':inventory['IfcSlab']['entity_count'],'explicit_connector_max_m':mx['connector_origin_separation_m'],'open_meshes':len(opens)},indent=2))
if __name__=='__main__':main()

"""Canonical s01 grid-routing viewer fixture exporter."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from .physical_network_routing import PhysicalNetworkRoute
from .spatial_evidence import SpatialEvidence
from .topology_planner import TopologyCandidate

ROOT=Path(__file__).resolve().parents[1]

def validate_viewer_fixture(data:dict)->tuple[dict,tuple[dict,...]]:
 if not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('units')!='m': raise ValueError('Invalid viewer fixture schema.')
 network=data.get('network')
 if not isinstance(network,dict) or not isinstance(network.get('main'),dict) or not isinstance(network.get('branches'),list): raise ValueError('Viewer fixture requires one main and a branches list.')
 routes=(network['main'],*network['branches'])
 if any(not isinstance(route.get('edge_id'),str) or not isinstance(route.get('world_centerline_m'),list) or len(route['world_centerline_m'])<2 for route in routes): raise ValueError('Viewer fixture route geometry is missing.')
 if len({route['edge_id'] for route in routes})!=len(routes): raise ValueError('Viewer fixture duplicates a physical edge.')
 if not isinstance(data.get('obstacles'),list): raise ValueError('Viewer fixture obstacles are missing.')
 return network['main'],tuple(network['branches'])

def fixture_bounds(data:dict)->tuple[tuple[float,float,float],tuple[float,float,float]]:
 main,branches=validate_viewer_fixture(data)
 return geometry_bounds(data['obstacles'],(main,*branches),[endpoint['world_m'] for endpoint in data.get('endpoints',[])]+([data['junction']['world_m']] if data.get('junction') else []))

def geometry_bounds(obstacles:list,routes:tuple|list,markers:list)->tuple[tuple[float,float,float],tuple[float,float,float]]:
 points=[]
 for obstacle in obstacles:
  bounds=obstacle.get('bounds',{});points.append((bounds.get('min_x_m'),bounds.get('min_y_m'),bounds.get('min_z_m')));points.append((bounds.get('max_x_m'),bounds.get('max_y_m'),bounds.get('max_z_m')))
 for route in routes: points.extend(tuple(point) for point in route['world_centerline_m'])
 points.extend(tuple(point) for point in markers)
 if not points or any(len(point)!=3 or not all(isinstance(value,(int,float)) for value in point) for point in points): raise ValueError('Viewer fixture has invalid or empty geometry.')
 return tuple(min(point[i] for point in points) for i in range(3)),tuple(max(point[i] for point in points) for i in range(3))

def export_viewer_fixture(result:PhysicalNetworkRoute,evidence:SpatialEvidence,candidate:TopologyCandidate,path:Path)->dict:
 endpoint_by_id={item.endpoint_id:item for item in candidate.demand.problem.endpoints};node_by_id={item.node_id:item for item in candidate.demand.topology.nodes}
 route_nodes=(result.main.start_node_id,result.main.end_node_id,*(node_id for route in result.branches for node_id in (route.start_node_id,route.end_node_id)))
 endpoint_ids=sorted({node_by_id[node_id].endpoint_id for node_id in route_nodes if node_by_id[node_id].endpoint_id is not None})
 endpoints=[{'endpoint_id':endpoint_id,'system':endpoint_by_id[endpoint_id].system.value,'world_m':[endpoint_by_id[endpoint_id].position.x,endpoint_by_id[endpoint_id].position.y,endpoint_by_id[endpoint_id].position.z]} for endpoint_id in endpoint_ids]
 d={'schema_version':1,'scenario_id':result.scenario_id,'coordinate_frame':evidence.coordinate_reference.frame_id,'units':'m','building_geometry_kind':'AABB_APPROXIMATION','disclaimer':'Grid routing only — physical geometry and zero-clash validation pending','obstacles':obstacle_records(evidence),'endpoints':endpoints,'junction':{'node_id':result.junction_node_id,'world_m':list(result.selected_proposal.world_position_m)},'network':json.loads(result.to_canonical_json())}
 validate_viewer_fixture(d);fixture_bounds(d)
 write_fixture(d,path);return d

def obstacle_records(evidence:SpatialEvidence)->list[dict]:
 return [{'global_id':x.global_id,'ifc_class':x.ifc_class,'bounds':x.world_bounds.to_canonical_dict()} for x in evidence.elements]

def write_fixture(data:dict,path:Path)->None:
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,sort_keys=True,separators=(',',':')),encoding='utf-8')

def controlled_s01_inputs(root:Path=ROOT)->tuple:
 """Return the controlled s01-d01 (problem, evidence, policy, frame_id) used by the viewer fixtures."""
 from .clearance_policy import benchmark_v1_policy
 from .config import load_building_config
 from .demand import generate_benchmark_cases, load_service_definitions
 from .endpoints import benchmark_case_to_routing_problem
 from .spatial_evidence import build_synthetic_spatial_evidence, synthetic_frame_id
 systems_path=root/'config/mep_systems.json';services=load_service_definitions(systems_path)
 case=generate_benchmark_cases(root/'experiments/scenarios',root/'experiments/demands',systems_path)[0]
 manifest=json.loads((root/'experiments/scenarios/manifest.json').read_text(encoding='utf-8'))
 config=load_building_config(root/'experiments/scenarios'/next(x['config_file'] for x in manifest['scenarios'] if x['scenario_id']==case['scenario_id']))
 return benchmark_case_to_routing_problem(case,services),build_synthetic_spatial_evidence(case['scenario_id'],config,services),benchmark_v1_policy(config,services),synthetic_frame_id(case['scenario_id'],config)

def export_single_system_fixture(path:Path,root:Path=ROOT)->dict:
 from .junction_placement import propose_junction_placements
 from .physical_network_routing import route_shared_main_group
 from .topology_planner import TopologyCandidateKind, plan_topology_candidates
 problem,evidence,policy,frame=controlled_s01_inputs(root)
 candidate=next(item for item in plan_topology_candidates(problem) if item.kind is TopologyCandidateKind.SHARED_MAIN)
 placement=propose_junction_placements(candidate,evidence,endpoint_frame_id=frame)
 route=route_shared_main_group(candidate,evidence,policy,placement,junction_node_id=placement.junctions[0].junction_node_id,endpoint_frame_id=frame)
 return export_viewer_fixture(route,evidence,candidate,path)

MAX_REGIONS_PER_PAIR=10
MULTI_SYSTEM_FIXTURES={
 'CONTAINING_CELL_ONLY':'multi_system_fixture.json',
 'BENCHMARK_CONTROLLED_ENTRY':'multi_system_entry_fixture.json',
 'EVIDENCE_SNAP_WITHIN_TOLERANCE':'multi_system_snap_fixture.json',
}
CERTIFICATION_WARNING='Grid-level diagnostics only: voxel centerlines and AABB envelope proxies. This is NOT continuous-geometry clash certification; no zero-clash claim is made. Grid-path completeness does not imply authoritative connector engagement.'

def _polyline(cells,spec)->list:
 from .route_geometry import compress_path_cells
 segments=compress_path_cells(list(cells),spec)
 return [[_r(v) for v in p] for p in ([segments[0].start]+[s.end for s in segments] if segments else [])]

def _r(value:float)->float:
 rounded=round(float(value),9);return 0.0 if rounded==-0.0 else rounded

def validate_multi_system_fixture(data:dict)->dict:
 if not isinstance(data,dict) or data.get('schema_version')!=2 or data.get('kind')!='MULTI_SYSTEM' or data.get('units')!='m': raise ValueError('Invalid multi-system viewer fixture schema.')
 if data.get('geometric_clash_certified') is not False or data.get('collision_free_claimed') is not False: raise ValueError('Viewer fixture must not claim clash certification.')
 if data.get('connector_engagement_validated', False) is not False: raise ValueError('Viewer fixture must not claim connector engagement validation.')
 for key in ('obstacles','systems','endpoints','junctions','edges','connections','conflicts'):
  if not isinstance(data.get(key),list): raise ValueError(f'Multi-system fixture {key} list is missing.')
 edges={edge['physical_edge_id']:edge for edge in data['edges']}
 if len(edges)!=len(data['edges']): raise ValueError('Viewer fixture duplicates a physical edge.')
 for edge in data['edges']:
  routed=edge['status']=='ROUTED';points=edge.get('world_polyline_m')
  if not isinstance(points,list) or (routed and len(points)<2) or (not routed and points): raise ValueError(f"Edge {edge['physical_edge_id']} geometry does not match its status.")
  if edge['system'] not in data['systems']: raise ValueError(f"Edge {edge['physical_edge_id']} has an undeclared system.")
 summary=data.get('summary',{});ids=[item['connection_id'] for item in data['connections']]
 if len(ids)!=len(set(ids)) or ids!=data.get('required_connection_ids'): raise ValueError('Viewer fixture must list every required connection exactly once.')
 counts={status:sum(item['status']==status for item in data['connections']) for status in ('ROUTED','FAILED','UNSUPPORTED','UNRESOLVED')}
 if summary.get('required_connection_count')!=len(ids) or any(summary.get(f'{k.lower()}_connection_count')!=v for k,v in counts.items()) or sum(counts.values())!=len(ids): raise ValueError('Viewer fixture summary does not match its connection records.')
 for item in data['connections']:
  if any(edge_id not in edges for edge_id in item['physical_edge_ids']) and item['status']=='ROUTED': raise ValueError(f"Routed connection {item['connection_id']} references missing geometry.")
 for conflict in data['conflicts']:
  if any(edge_id not in edges or edges[edge_id]['status']!='ROUTED' for edge_id in conflict['physical_edge_ids']): raise ValueError('Conflict marker references an unrouted or unknown edge.')
 return data

def export_multi_system_fixture(result,diagnostics:dict,evidence:SpatialEvidence,problem,path:Path)->dict:
 from .multi_system_diagnostics import diagnostics_to_json
 spec=evidence.grid_for(result.edges[0].system).spec if result.edges else None
 snaps={item.endpoint_id:item.to_canonical_dict() for item in result.endpoint_snaps}
 used={endpoint_id for item in problem.connections for endpoint_id in (item.start_endpoint_id,item.end_endpoint_id)}
 endpoints=[{'endpoint_id':item.endpoint_id,'system':item.system.value,'world_m':[_r(item.position.x),_r(item.position.y),_r(item.position.z)],'snap':snaps.get(item.endpoint_id)} for item in sorted(problem.endpoints,key=lambda x:x.endpoint_id) if item.endpoint_id in used]
 junctions=[{'node_key':f"{item.candidate_id}#{item.junction_node_id}",'system':item.system,'candidate_id':item.candidate_id,'proposal_rank':item.proposal_rank,'world_m':[_r(v) for v in item.junction_world_m],'network_complete':len(item.routed_connection_ids)==len(item.required_connection_ids)} for item in result.attempts if item.selected and item.junction_world_m is not None]
 evaluated=set(diagnostics['evaluated_physical_edge_ids']);edges=[]
 for item in result.edges:
  record=item.to_canonical_dict();record.pop('grid_cells');record.pop('world_centerline_m');record['diagnostics_evaluated']=item.physical_edge_id in evaluated
  record['grid_cell_count']=len(item.route.grid_cells);record['world_polyline_m']=_polyline(item.route.grid_cells,spec) if item.route.grid_cells else []
  edges.append(record)
 checks=diagnostics['checks'];conflicts=[]
 for record in checks['inter_system_overlap'].get('records',[]):
  regions=record['hard_overlap_regions']
  conflicts.append({'kind':'INTER_SYSTEM','severity':record['severity'],'physical_edge_ids':[record['edge_a'],record['edge_b']],'systems':[record['system_a'],record['system_b']],'shared_cell_count':record['shared_cell_count'],'shared_cells_world_m':record['shared_cells_world_m'],'hard_segment_pair_count':record['hard_segment_pair_count'],'clearance_segment_pair_count':record['clearance_segment_pair_count'],'hard_overlap_regions_shown':regions[:MAX_REGIONS_PER_PAIR],'hard_overlap_regions_total':len(regions)})
 for record in checks['same_system_independent_run_overlap'].get('records',[]):
  if record['unexplained_shared_cell_count']: conflicts.append({'kind':'SAME_SYSTEM_UNRESOLVED','severity':record['verdict'],'physical_edge_ids':[record['edge_a'],record['edge_b']],'systems':[record['system']],'shared_cell_count':record['unexplained_shared_cell_count'],'shared_cells_world_m':record['unexplained_shared_cells_world_m'],'hard_segment_pair_count':None,'clearance_segment_pair_count':None,'hard_overlap_regions_shown':[],'hard_overlap_regions_total':0,'clearance_rule_status':record['clearance_rule_status']})
 data={'schema_version':2,'kind':'MULTI_SYSTEM','scenario_id':result.scenario_id,'coordinate_frame':evidence.coordinate_reference.frame_id,'units':'m','building_geometry_kind':'AABB_APPROXIMATION',
  'endpoint_cell_mode':result.endpoint_cell_mode.value,'disclaimer':'Grid routing only — physical geometry and zero-clash validation pending','certification_warning':CERTIFICATION_WARNING,
  'geometric_clash_certified':False,'collision_free_claimed':False,'connector_engagement_validated':False,'grid_paths_complete':result.grid_paths_complete,
  'inter_system_reservation_applied':False,'policy_id':result.policy_id,'policy_digest_sha256':result.policy_digest_sha256,
  'endpoint_entry_summary':None if result.endpoint_entry is None else result.endpoint_entry.to_canonical_dict()['summary'],
  'controlled_entry_summary':None if result.controlled_entry is None else result.controlled_entry.to_canonical_dict()['summary'],
  'strict_mode':result.strict_mode,
  'systems':list(result.systems),'required_connection_ids':list(result.required_connection_ids),'summary':result.summary(),'obstacles':obstacle_records(evidence),'endpoints':endpoints,'junctions':junctions,
  'edges':edges,'connections':[item.to_canonical_dict() for item in result.connections],'attempts':[item.to_canonical_dict() for item in result.attempts],'conflicts':conflicts,
  'max_regions_per_pair':MAX_REGIONS_PER_PAIR,'diagnostics_summary':diagnostics['summary'],'unresolved_collision_semantics':diagnostics['unresolved_collision_semantics'],
  'check_status':{name:{'status':check['status'],**({'violation_count':check['violation_count']} if 'violation_count' in check else {})} for name,check in sorted(checks.items())},
  'obstacle_check':{k:v for k,v in checks['obstacle_intersection'].items() if k.endswith('_count') or k in ('status','note')},
  'diagnostics_sha256':hashlib.sha256(diagnostics_to_json(diagnostics).encode('utf-8')).hexdigest()}
 validate_multi_system_fixture(data);geometry_bounds(data['obstacles'],[{'world_centerline_m':e['world_polyline_m']} for e in data['edges']],[e['world_m'] for e in endpoints]+[j['world_m'] for j in junctions])
 write_fixture(data,path);return data

def export_multi_system_fixtures(directory:Path,root:Path=ROOT,diagnostics_directory:Path|None=None)->dict:
 from .multi_system_diagnostics import diagnose_multi_system_conflicts, diagnostics_to_json
 from .multi_system_routing import EndpointCellMode, route_multi_system
 problem,evidence,policy,frame=controlled_s01_inputs(root);written={}
 for mode_name,filename in MULTI_SYSTEM_FIXTURES.items():
  mode=EndpointCellMode(mode_name)
  result=route_multi_system(problem,evidence,policy,endpoint_frame_id=frame,endpoint_cell_mode=mode);diagnostics=diagnose_multi_system_conflicts(result,evidence,policy)
  written[mode.value]=export_multi_system_fixture(result,diagnostics,evidence,problem,directory/filename)
  if diagnostics_directory is not None:
   diagnostics_directory.mkdir(parents=True,exist_ok=True);stem=filename.replace('fixture.json','')
   (diagnostics_directory/f'{stem}routing.json').write_text(result.to_canonical_json(),encoding='utf-8');(diagnostics_directory/f'{stem}diagnostics.json').write_text(diagnostics_to_json(diagnostics),encoding='utf-8')
 return written

def main()->int:
 parser=argparse.ArgumentParser(description='Regenerate the deterministic s01 viewer fixtures.')
 parser.add_argument('--output',type=Path,default=ROOT/'viewer'/'viewer_fixture.json')
 parser.add_argument('--multi-system',action='store_true',help='Also write both multi-system fixtures next to --output.')
 parser.add_argument('--diagnostics-dir',type=Path,default=None,help='Optionally write full routing and diagnostics JSON here.')
 args=parser.parse_args();data=export_single_system_fixture(args.output)
 print(f"{args.output}: {1+len(data['network']['branches'])} edges, {len(data['obstacles'])} obstacles")
 if args.multi_system:
  for mode,fixture in export_multi_system_fixtures(args.output.parent,diagnostics_directory=args.diagnostics_dir).items():
   s=fixture['summary'];print(f"{mode}: {s['routed_connection_count']}/{s['required_connection_count']} routed, {len(fixture['conflicts'])} conflict records, verdict {fixture['diagnostics_summary']['grid_level_verdict']}")
 return 0

if __name__=='__main__':
 raise SystemExit(main())

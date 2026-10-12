"""Canonical s01 grid-routing viewer fixture exporter."""
from __future__ import annotations
import argparse
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

def main()->int:
 parser=argparse.ArgumentParser(description='Regenerate the deterministic s01 single-system viewer fixture.')
 parser.add_argument('--output',type=Path,default=ROOT/'viewer'/'viewer_fixture.json')
 args=parser.parse_args();data=export_single_system_fixture(args.output)
 print(f"{args.output}: {1+len(data['network']['branches'])} edges, {len(data['obstacles'])} obstacles");return 0

if __name__=='__main__':
 raise SystemExit(main())

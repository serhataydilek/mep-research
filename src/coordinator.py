"""Phase 6C1 P-CORE hybrid seed and endpoint-flexible routing primitives.

This module creates no repair rounds or iterative coordination. Vertical
endpoint flexibility is for the current synthetic benchmark only; real IFC
connection points need explicit flexibility metadata.
"""
from __future__ import annotations
import argparse, json
from copy import deepcopy
from pathlib import Path
from typing import Any
from .clash import evaluate_route_result_conflicts
from .constructability import _method_summary, load_constructability_config, verify_route_result
from .demand import load_service_definitions
from .gravity_routing import endpoint_candidates, run_gravity_benchmark
from .routing import astar_3d, route_metrics, run_b0_benchmark
from .voxel import build_occupancy_grids

def load_coordinator_config(path: Path) -> dict[str, Any]:
    config=json.loads(path.read_text(encoding='utf-8'))
    if config.get('method_id')!='P-CORE' or not isinstance(config.get('max_repair_rounds'),int) or isinstance(config.get('max_repair_rounds'),bool) or config['max_repair_rounds']<1:
        raise ValueError('coordinator.json requires P-CORE and positive max_repair_rounds')
    return config

def route_non_drainage_with_vertical_candidates(grid, start_anchor, end_anchor):
    starts, ends=endpoint_candidates(grid,start_anchor), endpoint_candidates(grid,end_anchor)
    if not starts or not ends: return None, 'NO_VALID_ENDPOINT_CANDIDATES'
    choices=[]
    for start in starts:
        for end in ends:
            result=astar_3d(grid,tuple(start['grid_index']),tuple(end['grid_index']))
            if result.path_found:
                metrics=route_metrics(result,grid.spec.voxel_size_m)
                choices.append((start['snap_distance_m']+end['snap_distance_m'],metrics['step_count'],metrics['bend_count'],metrics['vertical_step_count'],tuple(start['grid_index']),tuple(end['grid_index']),start,end,result,metrics))
    if not choices: return None, 'NO_PATH_FOR_ENDPOINT_CANDIDATES'
    return min(choices,key=lambda row:row[:6]), None

def standard_route_record(base: dict[str,Any], start:dict[str,Any], end:dict[str,Any], result, metrics:dict[str,Any], **extra):
    return {**base,'path_found':True,'start':deepcopy(start),'end':deepcopy(end),**metrics,
            'endpoint_snap_distance_total_m':start['snap_distance_m']+end['snap_distance_m'],
            'path_cells':[list(cell) for cell in result.path_cells],**extra}

def build_pcore_initial_layout(scenarios:Path,demands:Path,systems:Path,constraints:Path,*,grids=None):
    grids=grids if grids is not None else build_occupancy_grids(scenarios,systems)
    before={key:bytes(grid.cells) for key,grid in grids.items()}
    b0=run_b0_benchmark(scenarios,demands,systems,grids=grids)
    gravity=run_gravity_benchmark(scenarios,demands,systems,constraints)
    gravity_routes={(case['case_id'],route['connection_id']):route for case in gravity['cases'] for route in case['routes']}
    cases=[]
    for case in b0['cases']:
        routes=[]
        for route in case['routes']:
            if route['system']!='drainage':
                item=deepcopy(route); item['initial_source']='B0'
            else:
                source=gravity_routes[(case['case_id'],route['connection_id'])]
                if not source['path_found']: raise RuntimeError('gravity seed must preserve drainage connectivity')
                item=deepcopy(source); item['start']=item.pop('selected_start'); item['end']=item.pop('selected_end')
                item['initial_source']='GRAVITY_AWARE_DRAINAGE'
            routes.append(item)
        cases.append({'case_id':case['case_id'],'scenario_id':case['scenario_id'],'demand_profile_id':case['demand_profile_id'],
                      'connection_count':len(routes),'successful_route_count':sum(route['path_found'] for route in routes),
                      'failed_route_count':sum(not route['path_found'] for route in routes),'connection_success_rate':sum(route['path_found'] for route in routes)/len(routes),'routes':routes})
    if before!={key:bytes(grid.cells) for key,grid in grids.items()}: raise RuntimeError('P-CORE seed must not mutate base grids')
    return {'method':{'id':'P-CORE-SEED','name':'Constructability-Aware Coordination Initial Seed'},'cases':cases}

def run_pcore_seed_benchmark(scenarios:Path,demands:Path,systems:Path,constraints:Path,coordinator_config:Path):
    config=load_coordinator_config(coordinator_config); grids=build_occupancy_grids(scenarios,systems); definitions=load_service_definitions(systems)
    seed=build_pcore_initial_layout(scenarios,demands,systems,constraints,grids=grids)
    conflicts=evaluate_route_result_conflicts(seed,grids,definitions,'P-CORE-SEED','P-CORE Seed Conflict Evaluation','seed_summary',{})
    verification=verify_route_result(seed,grids,load_constructability_config(constraints),definitions)
    seed.update({'coordinator_config':config,'initial_conflict_metrics':conflicts['global_metrics'],'c0_summary':_method_summary(verification)})
    return seed

def main():
    parser=argparse.ArgumentParser(description='Build the non-iterative P-CORE hybrid seed.')
    for name in ('scenarios','demands','systems','constraints','coordinator-config','output'): parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args(); result=run_pcore_seed_benchmark(args.scenarios,args.demands,args.systems,args.constraints,args.coordinator_config)
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('case routes hard conflicts clearance violations drainage compliant C0 feasible')
    conflict_cases={case['case_id']:case for case in evaluate_route_result_conflicts(result,build_occupancy_grids(args.scenarios,args.systems),load_service_definitions(args.systems),'P-CORE-SEED','P-CORE Seed Conflict Evaluation','seed_summary',{})['cases']}
    for case in result['cases']:
        row=conflict_cases[case['case_id']]; print(case['case_id'],case['successful_route_count'],row['hard_conflicting_route_pair_count'],row['clearance_violating_route_pair_count'])
    print('global',result['initial_conflict_metrics']); print('C0',result['c0_summary']); return 0
if __name__=='__main__': raise SystemExit(main())

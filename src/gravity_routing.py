"""Deterministic C0 gravity-aware drainage routing primitive (terminal to egress)."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any
from .constructability import drainage_gravity_metrics, load_constructability_config
from .demand import generate_benchmark_cases
from .routing import astar_3d_with_offsets, route_metrics, run_b0_benchmark
from .voxel import FREE, build_occupancy_grids, snap_anchor_to_free_cell

GRAVITY_OFFSETS = ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,-1))
TOLERANCE=1e-9

def endpoint_candidates(grid, anchor):
    ref=snap_anchor_to_free_cell(grid,anchor); i,j,_=ref['grid_index']; original=(anchor['x_m'],anchor['y_m'],anchor['z_m']); out=[]
    for k in range(grid.spec.nz):
        if grid.state_at(i,j,k)!=FREE: continue
        center=grid.spec.cell_center(i,j,k); distance=sum((center[n]-original[n])**2 for n in range(3))**.5
        if distance<=2*grid.spec.voxel_size_m+TOLERANCE:
            out.append({'anchor_id':anchor['anchor_id'],'original_world':list(original),'grid_index':[i,j,k],'voxel_center':list(center),'snap_distance_m':distance,'original_world_z':original[2],'selected_voxel_center_z':center[2],'elevation_adjustment_m':center[2]-original[2]})
    return sorted(out,key=lambda x:(x['snap_distance_m'],x['grid_index']))

def gravity_route(grid,start_anchor,end_anchor,slope):
    starts,goals=endpoint_candidates(grid,start_anchor),endpoint_candidates(grid,end_anchor)
    if not starts or not goals: return None,'NO_VALID_GRAVITY_ENDPOINT_CANDIDATES'
    passed_filter=False; found_path=False; accepted=[]
    for start in starts:
      for goal in goals:
        si,gi=tuple(start['grid_index']),tuple(goal['grid_index'])
        drop=start['voxel_center'][2]-goal['voxel_center'][2]; minrun=(abs(si[0]-gi[0])+abs(si[1]-gi[1]))*grid.spec.voxel_size_m
        if drop < slope*minrun-TOLERANCE: continue
        passed_filter=True; result=astar_3d_with_offsets(grid,si,gi,GRAVITY_OFFSETS)
        if not result.path_found: continue
        found_path=True; route={'path_found':True,'path_cells':[list(x) for x in result.path_cells]}; gravity=drainage_gravity_metrics(route,grid.spec.voxel_size_m,slope)
        if gravity['drainage_gravity_pass']:
            metrics=route_metrics(result,grid.spec.voxel_size_m); accepted.append((start['snap_distance_m']+goal['snap_distance_m'],metrics['step_count'],metrics['bend_count'],metrics['vertical_step_count'],si,gi,start,goal,result,gravity,metrics))
    if not accepted: return None,('INSUFFICIENT_ENDPOINT_ELEVATION_DROP' if not passed_filter else 'NO_MONOTONIC_GRAVITY_PATH' if not found_path else 'GRAVITY_SLOPE_NOT_SATISFIED')
    return min(accepted,key=lambda x:x[:6]),None

def run_gravity_benchmark(scenarios:Path,demands:Path,systems:Path,constraints:Path):
    config=load_constructability_config(constraints); slope=config['drainage']['minimum_slope_ratio']; grids=build_occupancy_grids(scenarios,systems); cases=[]
    for case in generate_benchmark_cases(scenarios,demands,systems):
      grid=grids[(case['scenario_id'],'drainage')]; anchors={a['anchor_id']:a for a in [*case['egress_anchors'],*case['terminal_anchors']]}; routes=[]
      for req in sorted((r for r in case['connection_requests'] if r['system']=='drainage'),key=lambda r:(r['terminal_index'],r['connection_id'])):
        selected,reason=gravity_route(grid,anchors[req['start_anchor']],anchors[req['end_anchor']],slope)
        base={'case_id':case['case_id'],'scenario_id':case['scenario_id'],'demand_profile_id':case['demand_profile_id'],'connection_id':req['connection_id'],'system':'drainage'}
        if selected is None: routes.append({**base,'path_found':False,'failure_reason':reason,'path_cells':[],'gravity':None}); continue
        _,_,_,_,_,_,start,end,result,gravity,metrics=selected; routes.append({**base,'path_found':True,'failure_reason':None,'reference_start':snap_anchor_to_free_cell(grid,anchors[req['start_anchor']]),'reference_end':snap_anchor_to_free_cell(grid,anchors[req['end_anchor']]),'selected_start':start,'selected_end':end,**metrics,'endpoint_snap_distance_total_m':start['snap_distance_m']+end['snap_distance_m'],'gravity':gravity,'path_cells':[list(x) for x in result.path_cells]})
      cases.append({'case_id':case['case_id'],'scenario_id':case['scenario_id'],'demand_profile_id':case['demand_profile_id'],'routes':routes})
    routes=[r for c in cases for r in c['routes']]; success=[r for r in routes if r['path_found']]; failures={x:sum(r['failure_reason']==x for r in routes) for x in sorted({r['failure_reason'] for r in routes if not r['path_found']})}
    return {'method':{'id':'D-GRAVITY-C0','name':'C0 Gravity-Aware Drainage Routing Primitive'},'constraint':config['drainage'],'global_summary':{'total_drainage_requests':len(routes),'successful_drainage_routes':len(success),'failed_drainage_routes':len(routes)-len(success),'gravity_compliant_successful_routes':sum(r['gravity']['drainage_gravity_pass'] for r in success),'failure_reason_counts':failures},'cases':cases}

def main():
 p=argparse.ArgumentParser(); [p.add_argument('--'+n,required=True,type=Path) for n in ('scenarios','demands','systems','constraints','output')]; a=p.parse_args(); r=run_gravity_benchmark(a.scenarios,a.demands,a.systems,a.constraints); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n'); print(r['global_summary'])
if __name__=='__main__': main()

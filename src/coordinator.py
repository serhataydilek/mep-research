"""P-CORE seed plus Phase 6C2A's single authoritative repair round.

The round globally reevaluates one complete trial and accepts it only for a
strict global conflict-objective improvement; otherwise it returns the full
pre-round case unchanged.  Iterative coordination is intentionally deferred
to Phase 6C2B.  Vertical endpoint flexibility is benchmark-only.
"""
from __future__ import annotations
import argparse, json
from copy import deepcopy
from pathlib import Path
from typing import Any
from .clash import evaluate_route_result_conflicts
from .constructability import _method_summary, load_constructability_config, verify_route_result
from .demand import load_service_definitions
from .demand import generate_benchmark_cases
from .gravity_routing import endpoint_candidates, run_gravity_benchmark
from .gravity_routing import gravity_route
from .routing import astar_3d, route_metrics, run_b0_benchmark
from .voxel import build_occupancy_grids
from .sequential import clone_grid, block_prior_routes
from .conflict_diagnosis import diagnose_route_conflicts

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

def benchmark_anchor_map(scenarios, demands, systems):
    """Map immutable benchmark request identities to their original anchors."""
    result={}
    for case in generate_benchmark_cases(scenarios,demands,systems):
        anchors={a['anchor_id']:a for a in [*case['egress_anchors'],*case['terminal_anchors']]}
        for request in case['connection_requests']:
            result[(case['case_id'],request['system'],request['connection_id'])]=(request,anchors[request['start_anchor']],anchors[request['end_anchor']])
    return result

def ensure_coordination_metadata(route):
    result=deepcopy(route)
    for key,value in {'coordination_repair_attempt_count':0,'coordination_successful_repair_count':0,'coordination_failed_repair_count':0,'coordination_path_change_count':0,'last_repair_round':None,'last_repair_failure_reason':None}.items(): result.setdefault(key,value)
    return result

def _case_result(case): return {'cases':[case]}
def _c0_state(case,grids,constraints,definitions):
    row=verify_route_result(_case_result(case),grids,constraints,definitions)['cases'][0]
    return {'routing_complete':row['all_required_connections_routed'],'hard_conflict_free':not row['inter_system_hard_conflict_count'],'clearance_compliant':not row['inter_system_clearance_violation_count'],'drainage_gravity_compliant':row['all_drainage_routes_gravity_compliant'],'benchmark_hard_feasible_C0':row['benchmark_hard_feasible_C0'],'failure_reasons':row['failure_reasons']}
def _metrics(routes): return (sum(r['grid_route_length_m'] for r in routes),sum(r['bend_count'] for r in routes),sum(r['vertical_travel_m'] for r in routes))

def _conflict_objective(conflicts):
    """Return the ordered, authoritative global conflict objective."""
    return {'hard_conflicting_route_pair_count':conflicts['hard_conflicting_route_pair_count'],
            'clearance_violating_route_pair_count':conflicts['clearance_violating_route_pair_count']}

def _objective_key(objective):
    return (objective['hard_conflicting_route_pair_count'],objective['clearance_violating_route_pair_count'])

def _round_metrics(case, c0):
    length,bends,vertical=_metrics(case['routes'])
    return {'route_completion':c0['routing_complete'],'total_routed_length_m':length,
            'total_bend_count':bends,'total_vertical_travel_m':vertical}

def _base_grid_snapshot(grids): return {key:bytes(grid.cells) for key,grid in grids.items()}

def attempt_coordination_round(current_case, case_request_map, base_grids, definitions, constraints, round_index):
    """Attempt exactly one globally accepted-or-rolled-back P-CORE repair round."""
    base_before=_base_grid_snapshot(base_grids)
    original=deepcopy(current_case)
    before=evaluate_route_result_conflicts(_case_result(original),base_grids,definitions,'P-CORE','current','summary',{})
    before_case=before['cases'][0]; diagnosis=diagnose_route_conflicts(_case_result(original),before)['cases'][0]
    candidates=sorted(diagnosis['selected_repair_candidates'],key=lambda r:(r['component_id'],r['selection_rank_in_component']))
    c0_before=_c0_state(original,base_grids,constraints,definitions)
    objective_before=_conflict_objective(before_case)
    state={'round_index':round_index,'round_attempted':True,'candidate_routes_selected':bool(candidates),
           'trial_repair_produced':False,'trial_accepted':False,'round_rolled_back':False,
           'round_status':None,'final_round_reason':None,'conflict_objective_before':objective_before,
           'conflict_objective_after_trial':None,'final_conflict_objective':objective_before,
           'selected_repair_candidate_count':len(candidates),'successful_rerouted_candidate_count':0,
           'repair_attempt_count':0,'successful_repair_count':0,'failed_repair_count':0,'changed_path_count':0,
           'attempts':[],'c0_before':c0_before,**{key+'_before':value for key,value in _round_metrics(original,c0_before).items()}}
    if not any(_objective_key(objective_before)):
        state.update({'round_status':'NO_CONFLICTS','final_round_reason':'NO_CONFLICTS'})
        if base_before!=_base_grid_snapshot(base_grids): raise RuntimeError('P-CORE round must not mutate base grids')
        return original,state
    if not candidates:
        state.update({'round_status':'NO_REPAIR_CANDIDATES','final_round_reason':'NO_REPAIR_CANDIDATES'})
        if base_before!=_base_grid_snapshot(base_grids): raise RuntimeError('P-CORE round must not mutate base grids')
        return original,state
    selected={(r['system'],r['connection_id']) for r in candidates}; trial={ (r['system'],r['connection_id']):ensure_coordination_metadata(r) for r in original['routes']}; frozen=[r for k,r in trial.items() if k not in selected]
    for candidate in candidates:
        key=(candidate['system'],candidate['connection_id']); old=trial[key]; _,start,end=case_request_map[(original['case_id'],*key)]; grid=clone_grid(base_grids[(original['scenario_id'],old['system'])]); block_prior_routes(grid,[r for r in frozen if r['system']!=old['system']],definitions[old['system']],definitions)
        try:
            if old['system']=='drainage': chosen,reason=gravity_route(grid,start,end,float(constraints['drainage']['minimum_slope_ratio']))
            else: chosen,reason=route_non_drainage_with_vertical_candidates(grid,start,end)
        except ValueError:
            chosen,reason=None,'NO_VALID_ENDPOINT_CANDIDATES'
        new=ensure_coordination_metadata(old); new['coordination_repair_attempt_count']+=1; new['last_repair_round']=round_index
        if chosen is None: new['coordination_failed_repair_count']+=1; new['last_repair_failure_reason']=reason; success=False; changed=False
        else:
            if old['system']=='drainage': _,_,_,_,_,_,s,e,res,gravity,metrics=chosen; new=standard_route_record(old,s,e,res,metrics,reference_start=old.get('reference_start'),reference_end=old.get('reference_end'),gravity=gravity,initial_source=old['initial_source']); new=ensure_coordination_metadata(new)
            else: _,_,_,_,_,_,s,e,res,metrics=chosen; new=standard_route_record(old,s,e,res,metrics,initial_source=old['initial_source']); new=ensure_coordination_metadata(new)
            new['coordination_repair_attempt_count']=old.get('coordination_repair_attempt_count',0)+1; new['coordination_successful_repair_count']=old.get('coordination_successful_repair_count',0)+1; new['last_repair_round']=round_index; new['last_repair_failure_reason']=None; changed=new['path_cells']!=old['path_cells']; new['coordination_path_change_count']=old.get('coordination_path_change_count',0)+changed; success=True; reason=None
        trial[key]=new; frozen.append(new); state['attempts'].append({'system':key[0],'connection_id':key[1],'success':success,'failure_reason':reason,'path_changed':changed})
    trial_case={**original,'routes':[trial[(r['system'],r['connection_id'])] for r in original['routes']]}
    after=evaluate_route_result_conflicts(_case_result(trial_case),base_grids,definitions,'P-CORE','trial','summary',{})['cases'][0]
    c0_after_trial=_c0_state(trial_case,base_grids,constraints,definitions); objective_after_trial=_conflict_objective(after)
    state.update({'trial_repair_produced':True,'conflict_objective_after_trial':objective_after_trial,
                  'repair_attempt_count':len(candidates),'successful_repair_count':sum(x['success'] for x in state['attempts']),
                  'successful_rerouted_candidate_count':sum(x['success'] for x in state['attempts']),
                  'failed_repair_count':sum(not x['success'] for x in state['attempts']),
                  'changed_path_count':sum(x['path_changed'] for x in state['attempts']),'c0_after_trial':c0_after_trial,
                  **{key+'_after_trial':value for key,value in _round_metrics(trial_case,c0_after_trial).items()}})
    state['trial_accepted']=_objective_key(objective_after_trial) < _objective_key(objective_before)
    final_case=trial_case if state['trial_accepted'] else original
    c0_final=c0_after_trial if state['trial_accepted'] else c0_before
    state.update({'round_rolled_back':not state['trial_accepted'],
                  'round_status':'ACCEPTED' if state['trial_accepted'] else 'ROLLED_BACK',
                  'final_round_reason':'ACCEPTED_STRICT_GLOBAL_CONFLICT_IMPROVEMENT' if state['trial_accepted'] else 'ROLLED_BACK_NO_STRICT_GLOBAL_CONFLICT_IMPROVEMENT',
                  'final_conflict_objective':objective_after_trial if state['trial_accepted'] else objective_before,
                  'c0_final':c0_final,**{key+'_after':value for key,value in _round_metrics(final_case,c0_final).items()}})
    if base_before!=_base_grid_snapshot(base_grids): raise RuntimeError('P-CORE round must not mutate base grids')
    return final_case,state

def run_pcore_single_round_benchmark(scenarios:Path,demands:Path,systems:Path,constraints:Path,coordinator_config:Path):
    """Run exactly one P-CORE 6C2A round for each deterministic benchmark case."""
    config=load_coordinator_config(coordinator_config); grids=build_occupancy_grids(scenarios,systems)
    base_before=_base_grid_snapshot(grids); definitions=load_service_definitions(systems); c0_constraints=load_constructability_config(constraints)
    seed=build_pcore_initial_layout(scenarios,demands,systems,constraints,grids=grids); anchors=benchmark_anchor_map(scenarios,demands,systems)
    cases=[]; states=[]
    for case in seed['cases']:
        final_case,state=attempt_coordination_round(case,anchors,grids,definitions,c0_constraints,1)
        cases.append(final_case); states.append(state)
    if base_before!=_base_grid_snapshot(grids): raise RuntimeError('P-CORE benchmark must not mutate base grids')
    def total(key, suffix=''):
        return sum(state.get(key+suffix,0) or 0 for state in states)
    summary={'cases_evaluated':len(cases),'completed_routes':sum(case['successful_route_count'] for case in cases),
             'initial_global_conflict_objective':{'hard_conflicting_route_pair_count':sum(s['conflict_objective_before']['hard_conflicting_route_pair_count'] for s in states),'clearance_violating_route_pair_count':sum(s['conflict_objective_before']['clearance_violating_route_pair_count'] for s in states)},
             'trial_global_conflict_objective':{'hard_conflicting_route_pair_count':sum((s['conflict_objective_after_trial'] or s['conflict_objective_before'])['hard_conflicting_route_pair_count'] for s in states),'clearance_violating_route_pair_count':sum((s['conflict_objective_after_trial'] or s['conflict_objective_before'])['clearance_violating_route_pair_count'] for s in states)},
             'final_global_conflict_objective':{'hard_conflicting_route_pair_count':sum(s['final_conflict_objective']['hard_conflicting_route_pair_count'] for s in states),'clearance_violating_route_pair_count':sum(s['final_conflict_objective']['clearance_violating_route_pair_count'] for s in states)},
             'accepted_rounds':sum(s['trial_accepted'] for s in states),'rolled_back_rounds':sum(s['round_rolled_back'] for s in states),
             'selected_repair_candidates':total('selected_repair_candidate_count'),'successful_reroutes':total('successful_rerouted_candidate_count'),
             'route_length_delta_m':sum(s['total_routed_length_m_after']-s['total_routed_length_m_before'] for s in states),
             'bend_count_delta':sum(s['total_bend_count_after']-s['total_bend_count_before'] for s in states),
             'vertical_travel_delta_m':sum(s['total_vertical_travel_m_after']-s['total_vertical_travel_m_before'] for s in states)}
    return {'method':{'id':'P-CORE-6C2A','name':'P-CORE Single-Round Repair'},'coordinator_config':config,'cases':cases,'round_results':states,'global_summary':summary}

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

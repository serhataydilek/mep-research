from pathlib import Path
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import src.coordinator as coordinator
from src.coordinator import attempt_coordination_round, build_pcore_initial_layout, coordinate_pcore_multi_round, load_coordinator_config, route_non_drainage_with_vertical_candidates, run_pcore_seed_benchmark
from src.demand import generate_benchmark_cases
from src.routing import AStarResult, route_metrics
from src.voxel import build_occupancy_grids

ROOT=Path(__file__).resolve().parents[1]; SCENARIOS=ROOT/'experiments/scenarios'; DEMANDS=ROOT/'experiments/demands'; SYSTEMS=ROOT/'config/mep_systems.json'; CONSTRAINTS=ROOT/'config/constructability.json'; CONFIG=ROOT/'config/coordinator.json'
class CoordinatorTests(unittest.TestCase):
 def test_config_and_seed(self):
  self.assertEqual('P-CORE',load_coordinator_config(CONFIG)['method_id'])
  result=run_pcore_seed_benchmark(SCENARIOS,DEMANDS,SYSTEMS,CONSTRAINTS,CONFIG)
  self.assertEqual(292,sum(c['successful_route_count'] for c in result['cases']))
  self.assertEqual(12,result['c0_summary']['drainage_gravity_compliant_case_count'])
 def test_seed_sources_and_base_immutability(self):
  grids=build_occupancy_grids(SCENARIOS,SYSTEMS); before={k:bytes(v.cells) for k,v in grids.items()}
  seed=build_pcore_initial_layout(SCENARIOS,DEMANDS,SYSTEMS,CONSTRAINTS,grids=grids)
  self.assertEqual(before,{k:bytes(v.cells) for k,v in grids.items()})
  for route in [r for c in seed['cases'] for r in c['routes']]: self.assertEqual('GRAVITY_AWARE_DRAINAGE' if route['system']=='drainage' else 'B0',route['initial_source'])
 def test_non_drainage_candidate_router(self):
  grids=build_occupancy_grids(SCENARIOS,SYSTEMS); case=generate_benchmark_cases(SCENARIOS,DEMANDS,SYSTEMS)[0]; request=next(r for r in case['connection_requests'] if r['system']=='hvac'); anchors={a['anchor_id']:a for a in [*case['egress_anchors'],*case['terminal_anchors']]}
  selected,reason=route_non_drainage_with_vertical_candidates(grids[(case['scenario_id'],'hvac')],anchors[request['start_anchor']],anchors[request['end_anchor']])
  self.assertIsNone(reason); self.assertIsNotNone(selected)

 def _round_fixture(self, before, after):
  route={'system':'hvac','connection_id':'r1','path_found':True,'path_cells':[[0,0,0],[0,1,0]],'grid_route_length_m':1.0,'bend_count':0,'vertical_travel_m':0.0,'initial_source':'B0','last_repair_failure_reason':'STALE_FAILURE'}
  case={'case_id':'c1','scenario_id':'s1','demand_profile_id':'d1','connection_count':1,'successful_route_count':1,'failed_route_count':0,'connection_success_rate':1.0,'routes':[route]}
  grid=SimpleNamespace(cells=bytearray(b'base'))
  result=AStarResult(True,((0,0,0),(1,0,0)),1,2); metrics=route_metrics(result,1.0); endpoint={'snap_distance_m':0.0,'grid_index':[0,0,0]}
  conflict_rows=iter([before,after])
  def evaluate(*_args,**_kwargs):
   hard,clearance=next(conflict_rows)
   return {'cases':[{'hard_conflicting_route_pair_count':hard,'clearance_violating_route_pair_count':clearance}]}
  c0={'routing_complete':True,'hard_conflict_free':False,'clearance_compliant':False,'drainage_gravity_compliant':True,'benchmark_hard_feasible_C0':False,'failure_reasons':[]}
  patches=[patch.object(coordinator,'evaluate_route_result_conflicts',side_effect=evaluate),patch.object(coordinator,'diagnose_route_conflicts',return_value={'cases':[{'selected_repair_candidates':[{'system':'hvac','connection_id':'r1','component_id':0,'selection_rank_in_component':0}]}]}),patch.object(coordinator,'_c0_state',return_value=c0),patch.object(coordinator,'clone_grid',return_value=grid),patch.object(coordinator,'block_prior_routes'),patch.object(coordinator,'route_non_drainage_with_vertical_candidates',return_value=((0,1,0,0,(0,0,0),(1,0,0),endpoint,endpoint,result,metrics),None))]
  return case,grid,patches

 def _run_stub_round(self, before, after):
  case,grid,patches=self._round_fixture(before,after)
  with patches[0],patches[1],patches[2],patches[3],patches[4],patches[5]:
   return case,attempt_coordination_round(case,{('c1','hvac','r1'):({}, {}, {})},{('s1','hvac'):grid},{'hvac':{}}, {},1),grid

 def test_single_round_accepts_strict_global_improvement_and_clears_stale_reason(self):
  case,(final,state),grid=self._run_stub_round((2,3),(1,2))
  self.assertTrue(state['trial_accepted']); self.assertEqual('ACCEPTED',state['round_status'])
  self.assertEqual({'hard_conflicting_route_pair_count':2,'clearance_violating_route_pair_count':3},state['conflict_objective_before'])
  self.assertEqual({'hard_conflicting_route_pair_count':1,'clearance_violating_route_pair_count':2},state['final_conflict_objective'])
  self.assertEqual([[0,0,0],[1,0,0]],final['routes'][0]['path_cells']); self.assertIsNone(final['routes'][0]['last_repair_failure_reason'])
  self.assertEqual(bytearray(b'base'),grid.cells); self.assertEqual(1,state['selected_repair_candidate_count']); self.assertEqual(1,state['successful_rerouted_candidate_count'])

 def test_single_round_rolls_back_equal_global_objective_exactly(self):
  case,(final,state),_grid=self._run_stub_round((2,3),(2,3))
  self.assertFalse(state['trial_accepted']); self.assertTrue(state['round_rolled_back'])
  self.assertEqual('ROLLED_BACK_NO_STRICT_GLOBAL_CONFLICT_IMPROVEMENT',state['final_round_reason'])
  self.assertEqual(case,final); self.assertEqual(state['conflict_objective_before'],state['final_conflict_objective'])

 def test_single_round_rejects_local_change_when_global_objective_worsens(self):
  case,(final,state),_grid=self._run_stub_round((2,3),(3,1))
  self.assertEqual([[0,0,0],[0,1,0]],case['routes'][0]['path_cells'])
  self.assertFalse(state['trial_accepted']); self.assertEqual(case,final)

 def test_single_round_is_deterministic_and_preserves_base_state(self):
  grids=build_occupancy_grids(SCENARIOS,SYSTEMS); before={key:bytes(grid.cells) for key,grid in grids.items()}
  seed=build_pcore_initial_layout(SCENARIOS,DEMANDS,SYSTEMS,CONSTRAINTS,grids=grids); anchors=coordinator.benchmark_anchor_map(SCENARIOS,DEMANDS,SYSTEMS)
  definitions=coordinator.load_service_definitions(SYSTEMS); c0_constraints=coordinator.load_constructability_config(CONSTRAINTS)
  first,state_one=attempt_coordination_round(seed['cases'][0],anchors,grids,definitions,c0_constraints,1)
  second,state_two=attempt_coordination_round(seed['cases'][0],anchors,grids,definitions,c0_constraints,1)
  self.assertEqual(before,{key:bytes(grid.cells) for key,grid in grids.items()})
  self.assertEqual((state_one['selected_repair_candidate_count'],state_one['round_status'],state_one['final_conflict_objective'],first['routes']),(state_two['selected_repair_candidate_count'],state_two['round_status'],state_two['final_conflict_objective'],second['routes']))

 def _multi_stub(self, states, *, initial=(3,3), max_rounds=6):
  case={'case_id':'c1','scenario_id':'s1','demand_profile_id':'d1','connection_count':1,'successful_route_count':1,'failed_route_count':0,'connection_success_rate':1.0,'routes':[{'system':'hvac','connection_id':'r1','path_found':True,'path_cells':[[0,0,0]],'grid_route_length_m':1.0,'bend_count':0,'vertical_travel_m':0.0}]}
  grid=SimpleNamespace(cells=bytearray(b'base')); seen=[]; iterator=iter(states)
  def evaluate(*_args,**_kwargs): return {'cases':[{'hard_conflicting_route_pair_count':initial[0],'clearance_violating_route_pair_count':initial[1]}]}
  c0={'routing_complete':True,'hard_conflict_free':False,'clearance_compliant':False,'drainage_gravity_compliant':True,'benchmark_hard_feasible_C0':False,'failure_reasons':[]}
  def attempt(current,*_args):
   seen.append(deepcopy(current)); entry=next(iterator); final=deepcopy(current); final['routes'][0]['path_cells']=[[entry.get('geometry',len(seen)),0,0]]
   return final,entry
  with patch.object(coordinator,'evaluate_route_result_conflicts',side_effect=evaluate),patch.object(coordinator,'_c0_state',return_value=c0),patch.object(coordinator,'attempt_coordination_round',side_effect=attempt):
   final,result=coordinate_pcore_multi_round(case,{}, {('s1','hvac'):grid},{}, {},max_rounds)
  return case,final,result,seen,grid

 def _round_state(self, objective, *, accepted=True, candidates=True, successful=1, rolled_back=False):
  return {'trial_accepted':accepted,'round_rolled_back':rolled_back,'candidate_routes_selected':candidates,'successful_rerouted_candidate_count':successful,'final_conflict_objective':{'hard_conflicting_route_pair_count':objective[0],'clearance_violating_route_pair_count':objective[1]},'selected_repair_candidate_count':int(candidates),'selected_candidate_identities':[],'conflict_objective_before':None,'conflict_objective_after_trial':None,'round_status':'ACCEPTED' if accepted else 'ROLLED_BACK','final_round_reason':'TEST'}

 def test_multi_round_commits_multiple_strict_improvements_and_history(self):
  first=self._round_state((2,2))
  first['geometry']=1; second=self._round_state((1,1)); second['geometry']=2; rejected=self._round_state((1,1),accepted=False,rolled_back=True); rejected['geometry']=3
  _case,final,result,seen,grid=self._multi_stub([first,second,rejected])
  summary=result['summary']; self.assertEqual(3,summary['rounds_attempted']); self.assertEqual(2,summary['rounds_accepted']); self.assertEqual('NO_STRICT_GLOBAL_IMPROVEMENT',summary['final_stop_reason'])
  self.assertEqual([{'hard_conflicting_route_pair_count':3,'clearance_violating_route_pair_count':3},{'hard_conflicting_route_pair_count':2,'clearance_violating_route_pair_count':2},{'hard_conflicting_route_pair_count':1,'clearance_violating_route_pair_count':1}],summary['objective_history'])
  self.assertEqual([[2,0,0]],final['routes'][0]['path_cells']); self.assertEqual([[1,0,0]],seen[1]['routes'][0]['path_cells']); self.assertEqual(bytearray(b'base'),grid.cells)

 def test_multi_round_equal_objective_rolls_back_and_stops(self):
  rejected=self._round_state((3,3),accepted=False,rolled_back=True)
  case,final,result,seen,_grid=self._multi_stub([rejected])
  self.assertEqual('NO_STRICT_GLOBAL_IMPROVEMENT',result['summary']['final_stop_reason']); self.assertEqual(case,final); self.assertEqual(1,len(seen)); self.assertEqual(1,result['summary']['rounds_rolled_back'])

 def test_multi_round_stops_for_no_candidates_or_successful_reroutes(self):
  no_candidates=self._round_state((3,3),accepted=False,candidates=False,successful=0,rolled_back=False)
  _case,_final,result,_seen,_grid=self._multi_stub([no_candidates]); self.assertEqual('NO_SELECTED_CANDIDATES',result['summary']['final_stop_reason'])
  no_reroutes=self._round_state((3,3),accepted=False,successful=0,rolled_back=True)
  _case,_final,result,_seen,_grid=self._multi_stub([no_reroutes]); self.assertEqual('NO_SUCCESSFUL_REROUTES',result['summary']['final_stop_reason'])

 def test_multi_round_honors_cap_and_zero_conflict_termination(self):
  first=self._round_state((2,2)); first['geometry']=1; second=self._round_state((1,1)); second['geometry']=2
  _case,_final,result,seen,_grid=self._multi_stub([first,second],max_rounds=2)
  self.assertEqual('MAX_REPAIR_ROUNDS_REACHED',result['summary']['final_stop_reason']); self.assertEqual(2,len(seen))
  _case,_final,result,seen,_grid=self._multi_stub([],initial=(0,0))
  self.assertEqual('ZERO_AUTHORITATIVE_CONFLICTS',result['summary']['final_stop_reason']); self.assertEqual([],seen)

 def test_multi_round_stub_history_is_deterministic(self):
  states=[]
  for objective,geometry in [((2,2),1),((1,1),2),((1,1),3)]:
   row=self._round_state(objective,accepted=geometry<3,rolled_back=geometry==3); row['geometry']=geometry; states.append(row)
  _case,first,first_result,_seen,_grid=self._multi_stub(states)
  _case,second,second_result,_seen,_grid=self._multi_stub(states)
  self.assertEqual((first,first_result['summary'],first_result['rounds']),(second,second_result['summary'],second_result['rounds']))

 def test_multi_round_real_case_preserves_drainage_and_completion(self):
  grids=build_occupancy_grids(SCENARIOS,SYSTEMS); before={key:bytes(grid.cells) for key,grid in grids.items()}; seed=build_pcore_initial_layout(SCENARIOS,DEMANDS,SYSTEMS,CONSTRAINTS,grids=grids)
  final,result=coordinate_pcore_multi_round(seed['cases'][0],coordinator.benchmark_anchor_map(SCENARIOS,DEMANDS,SYSTEMS),grids,coordinator.load_service_definitions(SYSTEMS),coordinator.load_constructability_config(CONSTRAINTS),2)
  self.assertTrue(result['c0_final']['routing_complete']); self.assertTrue(result['c0_final']['drainage_gravity_compliant']); self.assertEqual(before,{key:bytes(grid.cells) for key,grid in grids.items()}); self.assertEqual(seed['cases'][0]['connection_count'],len(final['routes']))

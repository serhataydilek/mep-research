"""Route one selected SHARED_MAIN group as one main plus explicit branches."""
from __future__ import annotations
from dataclasses import dataclass
import json
from typing import Any
from .clearance_policy import ClearancePolicy
from .junction_placement import CandidateJunctionPlacement, JunctionPlacementProposal
from .physical_edge_routing import PhysicalEdgeRoute, PhysicalEdgeRouteStatus, route_physical_edge
from .spatial_evidence import SpatialEvidence
from .topology_planner import TopologyCandidate

@dataclass(frozen=True, slots=True)
class PhysicalNetworkRoute:
    scenario_id:str; candidate_id:str; junction_node_id:str; selected_proposal:JunctionPlacementProposal
    main:PhysicalEdgeRoute; branches:tuple[PhysicalEdgeRoute,...]; connection_paths:tuple[tuple[str,tuple[tuple[int,int,int],...]],...]
    complete:bool; collision_free_claimed:bool=False
    def __post_init__(self):
        if self.collision_free_claimed: raise ValueError('Network routing cannot claim collision freedom.')
    def to_canonical_json(self)->str:
        return json.dumps({'scenario_id':self.scenario_id,'candidate_id':self.candidate_id,'junction_node_id':self.junction_node_id,'selected_junction_world_m':list(self.selected_proposal.world_position_m),'main':json.loads(self.main.to_canonical_json()),'branches':[json.loads(x.to_canonical_json()) for x in self.branches],'connection_paths':{k:[list(x) for x in v] for k,v in self.connection_paths},'complete':self.complete,'collision_free_claimed':False,'unresolved_physical_requirements':['FITTING_GEOMETRY_UNEVALUATED','CONNECTOR_ENGAGEMENT_UNEVALUATED','EXACT_COLLISION_CERTIFICATION_UNAVAILABLE']},sort_keys=True,separators=(',',':'))

def route_shared_main_group(candidate:TopologyCandidate,evidence:SpatialEvidence,policy:ClearancePolicy,placement:CandidateJunctionPlacement,*,junction_node_id:str,proposal_rank:int=0,endpoint_frame_id:str='',max_expansions:int=50000)->PhysicalNetworkRoute:
    junction=next((x for x in placement.junctions if x.junction_node_id==junction_node_id),None)
    if junction is None or proposal_rank>=len(junction.proposals): raise ValueError('Selected junction proposal is unavailable.')
    proposal=junction.proposals[proposal_rank]
    main=route_physical_edge(candidate,evidence,policy,junction.main_edge_id,endpoint_frame_id=endpoint_frame_id,junction_proposals=(proposal,),max_expansions=max_expansions)
    branches=tuple(route_physical_edge(candidate,evidence,policy,eid,endpoint_frame_id=endpoint_frame_id,junction_proposals=(proposal,),max_expansions=max_expansions) for eid in junction.branch_edge_ids)
    by_edge={x.edge_id:x for x in (main,*branches)}; paths=[]
    for assignment in candidate.demand.assignments:
        if junction.main_edge_id not in assignment.edge_ids: continue
        edge_routes=[by_edge.get(x) for x in assignment.edge_ids]
        if any(x is None or x.status is not PhysicalEdgeRouteStatus.ROUTED for x in edge_routes): paths.append((assignment.connection_id,())) ;continue
        cells=[]
        for route in edge_routes:
            segment=list(route.grid_cells)
            if cells and cells[-1]==segment[0]: segment=segment[1:]
            cells.extend(segment)
        paths.append((assignment.connection_id,tuple(cells)))
    complete=main.status is PhysicalEdgeRouteStatus.ROUTED and len(paths)==len(junction.connection_ids) and all(path for _,path in paths)
    return PhysicalNetworkRoute(evidence.provenance.scenario_id,candidate.candidate_id,junction_node_id,proposal,main,branches,tuple(sorted(paths)),complete)

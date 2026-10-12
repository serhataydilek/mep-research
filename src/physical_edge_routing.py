"""Bounded deterministic routing of one topology edge on an existing occupancy grid."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import heapq, json
from typing import Any, Mapping
from .clearance_policy import ClearancePolicy
from .junction_placement import JunctionPlacementProposal
from .routing import manhattan_heuristic
from .spatial_evidence import SpatialEvidence, read_only_occupancy_view
from .topology_planner import TopologyCandidate
from .voxel import FREE

class PhysicalEdgeRouteStatus(str, Enum):
    ROUTED="ROUTED"; NOT_FOUND="NOT_FOUND"; SEARCH_LIMIT_REACHED="SEARCH_LIMIT_REACHED"; INVALID_INPUT="INVALID_INPUT"; UNSUPPORTED="UNSUPPORTED"

@dataclass(frozen=True, slots=True)
class PhysicalEdgeRoute:
    status: PhysicalEdgeRouteStatus; edge_id: str; system: str; start_node_id: str; end_node_id: str
    grid_cells: tuple[tuple[int,int,int],...]; world_centerline_m: tuple[tuple[float,float,float],...]
    grid_path_length_m: float | None; expanded_node_count: int; policy_id: str; policy_digest_sha256: str; grid_cells_sha256: str
    reason: str | None = None
    def to_canonical_json(self)->str:
        return json.dumps({"edge_id":self.edge_id,"expanded_node_count":self.expanded_node_count,"grid_cells":[list(x) for x in self.grid_cells],"grid_cells_sha256":self.grid_cells_sha256,"grid_path_length_m":self.grid_path_length_m,"policy_digest_sha256":self.policy_digest_sha256,"policy_id":self.policy_id,"reason":self.reason,"start_node_id":self.start_node_id,"status":self.status.value,"system":self.system,"world_centerline_m":[list(x) for x in self.world_centerline_m],"end_node_id":self.end_node_id,"unresolved_physical_requirements":["FITTING_GEOMETRY_UNEVALUATED","EXACT_COLLISION_CERTIFICATION_UNAVAILABLE"]},sort_keys=True,separators=(",",":"))

def route_physical_edge(candidate: TopologyCandidate, evidence: SpatialEvidence, policy: ClearancePolicy, edge_id: str, *, endpoint_frame_id: str, junction_proposals: tuple[JunctionPlacementProposal,...]=(), max_expansions:int=50000, endpoint_cells: Mapping[str,tuple[int,int,int]] | None=None)->PhysicalEdgeRoute:
    """``endpoint_cells`` optionally replaces an endpoint's containing cell (e.g. an evidence snap cell); it must still be FREE."""
    edge=next((x for x in candidate.demand.topology.edges if x.edge_id==edge_id),None)
    if edge is None: return PhysicalEdgeRoute(PhysicalEdgeRouteStatus.INVALID_INPUT,edge_id,"","","",(),(),None,0,policy.policy_id,policy.digest_sha256,"","UNKNOWN_EDGE")
    grid=evidence.grid_for(edge.system.value)
    base=lambda status,reason: PhysicalEdgeRoute(status,edge.edge_id,edge.system.value,edge.start_node_id,edge.end_node_id,(),(),None,0,policy.policy_id,policy.digest_sha256,grid.cells_sha256 if grid else "",reason)
    if endpoint_frame_id!=evidence.coordinate_reference.frame_id: return base(PhysicalEdgeRouteStatus.INVALID_INPUT,"COORDINATE_FRAME_MISMATCH")
    if grid is None or grid.clearance_policy_digest_sha256!=policy.digest_sha256: return base(PhysicalEdgeRouteStatus.INVALID_INPUT,"GRID_OR_POLICY_MISMATCH")
    nodes={x.node_id:x for x in candidate.demand.topology.nodes}; endpoints={x.endpoint_id:x for x in candidate.demand.problem.endpoints}
    selected={x.junction_node_id:x for x in junction_proposals}
    def resolve(node_id):
        node=nodes[node_id]
        if node.endpoint_id:
            if endpoint_cells and node.endpoint_id in endpoint_cells: return tuple(endpoint_cells[node.endpoint_id])
            ep=endpoints[node.endpoint_id]; return grid.spec.world_to_containing_cell(ep.position.x,ep.position.y,ep.position.z)
        proposal=selected.get(node_id)
        return proposal.grid_index if proposal and proposal.system==edge.system.value and proposal.evidence.cells_sha256==grid.cells_sha256 else None
    try: start,end=resolve(edge.start_node_id),resolve(edge.end_node_id)
    except ValueError: return base(PhysicalEdgeRouteStatus.INVALID_INPUT,"ENDPOINT_OUTSIDE_GRID")
    if start is None or end is None: return base(PhysicalEdgeRouteStatus.INVALID_INPUT,"JUNCTION_PROPOSAL_MISSING")
    view=read_only_occupancy_view(grid); spec=view.spec
    if view.state_at(*start)!=FREE or view.state_at(*end)!=FREE:return base(PhysicalEdgeRouteStatus.INVALID_INPUT,"ENDPOINT_CELL_NOT_FREE")
    heap=[(manhattan_heuristic(start,end),0,start[2],start[1],start[0],start)]; parents={}; costs={start:0}; expanded=0
    while heap:
        _,g,k,j,i,current=heapq.heappop(heap)
        if costs.get(current)!=g:continue
        expanded+=1
        if expanded>max_expansions:return PhysicalEdgeRoute(PhysicalEdgeRouteStatus.SEARCH_LIMIT_REACHED,edge.edge_id,edge.system.value,edge.start_node_id,edge.end_node_id,(),(),None,expanded,policy.policy_id,policy.digest_sha256,grid.cells_sha256,"SEARCH_LIMIT_REACHED")
        if current==end:
            path=[current]
            while current!=start: current=parents[current];path.append(current)
            path=tuple(reversed(path)); world=tuple(spec.cell_center(*x) for x in path)
            return PhysicalEdgeRoute(PhysicalEdgeRouteStatus.ROUTED,edge.edge_id,edge.system.value,edge.start_node_id,edge.end_node_id,path,world,(len(path)-1)*spec.voxel_size_m,expanded,policy.policy_id,policy.digest_sha256,grid.cells_sha256)
        for di,dj,dk in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
            nxt=(i+di,j+dj,k+dk)
            try: free=view.state_at(*nxt)==FREE
            except ValueError: free=False
            ng=g+1
            if free and ng<costs.get(nxt,10**18): costs[nxt]=ng;parents[nxt]=current;heapq.heappush(heap,(ng+manhattan_heuristic(nxt,end),ng,nxt[2],nxt[1],nxt[0],nxt))
    return PhysicalEdgeRoute(PhysicalEdgeRouteStatus.NOT_FOUND,edge.edge_id,edge.system.value,edge.start_node_id,edge.end_node_id,(),(),None,expanded,policy.policy_id,policy.digest_sha256,grid.cells_sha256,"NO_FREE_GRID_PATH")

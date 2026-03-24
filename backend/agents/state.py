import operator
from typing import Annotated, Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    user_id: int
    campaign_id: int
    approved: bool
    campaign_data: Dict[str, Any]
    strategy_plan: Dict[str, Any]
    posts: List[Dict[str, Any]]
    step_log: Annotated[List[str], operator.add]

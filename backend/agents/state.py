import operator
from typing import Annotated, Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    user_id: int
    campaign_id: int
    approved: bool
    num_posts: int          # Derived from posting frequency; drives how many posts are generated
    campaign_data: Dict[str, Any]
    strategy_plan: Dict[str, Any]
    posts: List[Dict[str, Any]]
    step_log: Annotated[List[str], operator.add]
    # Platform research: populated by research_node, consumed by content_node
    research_insights: Dict[str, Any]  # e.g. {insights: [{platform, trending_formats, ...}], overall_content_direction}

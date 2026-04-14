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
    # Lead capture: populated by wizard Step 5 data, consumed by lead_capture_node
    lead_form_config: Dict[str, Any]      # e.g. {enabled, form_type, fields, cta_text}
    automation_config: Dict[str, Any]     # e.g. {enabled, trigger_keyword, reply_dm, platforms}

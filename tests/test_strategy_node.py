"""Regression: strategy prompt must tolerate `{` / `}` in user-supplied campaign fields."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agents.nodes import strategy_node


def test_strategy_node_braces_in_user_fields_do_not_crash() -> None:
    """Previously `.format(location=...)` on the full string treated `{x}` in goal as placeholders."""
    fake_plan = MagicMock()
    fake_plan.days = [MagicMock() for _ in range(7)]
    fake_plan.model_dump.return_value = {"days": []}

    state = {
        "campaign_id": 1,
        "campaign_data": {
            "ai_text_enabled": True,
            "goal": "Promote open house {this_weekend}",
            "location": "Neighborhood {North}",
            "audience": "Buyers {first_time}",
            "business_type": "Team {brand_name}",
        },
    }

    with patch("backend.agents.nodes._openai_api_key", return_value="sk-test-key-not-a-placeholder"):
        with patch("backend.agents.nodes._llm") as llm_fn:
            chain = MagicMock()
            chain.invoke.return_value = fake_plan
            llm_fn.return_value.with_structured_output.return_value = chain

            out = strategy_node(state)

    assert "strategy_plan" in out
    assert chain.invoke.called
    human = chain.invoke.call_args[0][0][1]
    assert "{this_weekend}" in human.content or "this_weekend" in human.content

import json
import os
from typing import Any, Dict, List

from openai import AsyncOpenAI

from backend.agents.errors import OpenAINotConfiguredError
from backend.schemas import GenerateCampaignRequest

DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


async def generate_campaign_posts(req: GenerateCampaignRequest) -> List[Dict[str, Any]]:
    """
    Legacy/async path — not used by LangGraph. Requires OpenAI; no sample posts.
    """
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise OpenAINotConfiguredError("OPENAI_API_KEY is required.")

    client = AsyncOpenAI(api_key=key)
    system = (
        "You are a social media copywriter for businesses and brands. "
        "Return ONLY valid JSON with a top-level key 'posts' containing exactly 7 objects. "
        "Each object must have: day (one of Monday..Sunday in order), caption (string), "
        "hashtags (array of strings relevant to the business type and goal), status (always 'pending'). "
        "Captions must be substantive and local to the user's location when provided."
    )
    user = json.dumps(req.model_dump(), default=str)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Campaign request JSON:\n{user}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    raw = (resp.choices[0].message.content or "").strip()
    data = json.loads(raw)
    posts = data.get("posts")
    if not isinstance(posts, list) or len(posts) < 7:
        raise RuntimeError("OpenAI returned invalid posts payload.")
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(posts[:7]):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "day": str(item.get("day") or DAYS[i % 7]),
                "caption": str(item.get("caption") or ""),
                "hashtags": item.get("hashtags")
                if isinstance(item.get("hashtags"), list)
                else [],
                "status": "pending",
            }
        )
    if len(out) != 7:
        raise RuntimeError("Could not normalize 7 posts from OpenAI response.")
    return out

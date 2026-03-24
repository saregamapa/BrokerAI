import json
import os
from typing import Any, Dict, List

from openai import AsyncOpenAI

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


def _sample_posts(req: GenerateCampaignRequest) -> List[Dict[str, Any]]:
    loc = req.location
    goal = req.goal
    out: List[Dict[str, Any]] = []
    for i, day in enumerate(DAYS):
        out.append(
            {
                "day": day,
                "caption": (
                    f"Looking for {goal.lower()} in {loc}? "
                    f"Let's connect — local expertise, no pressure. "
                    f"DM or comment for a free consult. #{loc.replace(' ', '')}"
                ),
                "hashtags": ["#realestate", f"#{loc.replace(' ', '')}", "#home"],
                "status": "pending",
            }
        )
    return out


async def generate_campaign_posts(req: GenerateCampaignRequest) -> List[Dict[str, Any]]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or key == "your_key_here":
        return _sample_posts(req)

    client = AsyncOpenAI(api_key=key)
    system = (
        "You are a social media copywriter for real estate professionals. "
        "Return ONLY valid JSON with a top-level key 'posts' containing exactly 7 objects. "
        "Each object must have: day (one of Monday..Sunday in order), caption (string), "
        "hashtags (array of strings, include #realestate-style tags), status (always 'pending'). "
        "Keep captions professional, inclusive, and Fair Housing compliant (no steering or discrimination)."
    )
    user = json.dumps(
        {
            "business_type": req.business_type,
            "goal": req.goal,
            "location": req.location,
            "platforms": req.platforms,
            "frequency": req.frequency,
        }
    )
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        raw = completion.choices[0].message.content or "{}"
        data = json.loads(raw)
        posts = data.get("posts") or data.get("campaign") or []
        if not isinstance(posts, list) or len(posts) < 1:
            return _sample_posts(req)
        normalized: List[Dict[str, Any]] = []
        for i, p in enumerate(posts[:7]):
            day = str(p.get("day") or DAYS[i % 7])
            cap = str(p.get("caption") or "").strip() or f"Post for {req.location}"
            tags = p.get("hashtags") or []
            if not isinstance(tags, list):
                tags = []
            tags = [str(t) for t in tags]
            normalized.append(
                {
                    "day": day,
                    "caption": cap,
                    "hashtags": tags,
                    "status": "pending",
                }
            )
        while len(normalized) < 7:
            idx = len(normalized)
            normalized.append(
                {
                    "day": DAYS[idx],
                    "caption": f"Tips for buyers in {req.location} — ask me anything.",
                    "hashtags": ["#realestate", "#tips"],
                    "status": "pending",
                }
            )
        return normalized[:7]
    except Exception:
        return _sample_posts(req)

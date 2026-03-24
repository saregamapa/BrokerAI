import json
import os
from typing import List

from openai import AsyncOpenAI

from backend.schemas import CheckComplianceResponse


async def check_caption_compliance(caption: str) -> CheckComplianceResponse:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or key == "your_key_here":
        return _heuristic_check(caption)

    client = AsyncOpenAI(api_key=key)
    system = (
        "You are a Fair Housing and marketing compliance reviewer for US real estate social posts. "
        "Check for: discriminatory or steering language (race, religion, national origin, familial status, "
        "disability, sex), promises that sound like guaranteed returns, unlicensed legal/tax advice, "
        "missing basic disclaimers when needed (e.g. 'not legal advice'). "
        "Return ONLY JSON: "
        '{"passed": boolean, "issues": string[], "suggested_fix": string}. '
        "If minor issues only, passed can still be true with issues listing suggestions."
    )
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": caption},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = completion.choices[0].message.content or "{}"
        data = json.loads(raw)
        passed = bool(data.get("passed", True))
        issues = data.get("issues") or []
        if not isinstance(issues, list):
            issues = []
        issues = [str(x) for x in issues]
        fix = str(data.get("suggested_fix") or "")
        return CheckComplianceResponse(passed=passed, issues=issues, suggested_fix=fix)
    except Exception:
        return _heuristic_check(caption)


def _heuristic_check(caption: str) -> CheckComplianceResponse:
    lower = caption.lower()
    red_flags: List[str] = []
    banned = [
        ("families only", "Avoid familial-status discrimination."),
        ("no children", "Avoid familial-status discrimination."),
        ("christian", "Avoid religious preference in housing ads."),
        ("muslim", "Avoid religious preference in housing ads."),
        ("white neighborhood", "Avoid racial steering."),
        ("exclusive", "Review for potentially exclusionary language."),
    ]
    for phrase, msg in banned:
        if phrase in lower:
            red_flags.append(msg)
    passed = len(red_flags) == 0
    fix = (
        "Use inclusive language; describe the property and services, not preferred types of people. "
        "Add 'Equal Housing Opportunity' where appropriate."
        if not passed
        else ""
    )
    return CheckComplianceResponse(passed=passed, issues=red_flags, suggested_fix=fix)

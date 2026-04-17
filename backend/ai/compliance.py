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
        "You are a marketing compliance reviewer for social media posts. "
        "Check for: discriminatory language (race, religion, national origin, disability, sex, age), "
        "promises that sound like guaranteed results or returns, unlicensed legal/financial/medical advice, "
        "false claims, or missing basic disclaimers when needed (e.g. 'not financial advice'). "
        "Return ONLY JSON: "
        '{"passed": boolean, "issues": string[], "suggested_fix": string}. '
        "CRITICAL: suggested_fix must be ONLY the extra disclaimer or disclosure text to APPEND to the "
        "existing caption (one or two short sentences, no hashtags unless essential). "
        "Never rewrite or replace the full caption in suggested_fix. "
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
        ("families only", "Avoid language that excludes protected groups."),
        ("no children", "Avoid language that discriminates by familial status."),
        ("whites only", "Avoid racially discriminatory language."),
        ("guaranteed returns", "Avoid making promises of guaranteed financial results."),
        ("guaranteed results", "Avoid making promises of guaranteed results."),
        ("100% guaranteed", "Avoid absolute guarantees that could mislead consumers."),
    ]
    for phrase, msg in banned:
        if phrase in lower:
            red_flags.append(msg)
    passed = len(red_flags) == 0
    fix = (
        "Results may vary. This post is not financial, legal, or medical advice."
        if not passed
        else ""
    )
    return CheckComplianceResponse(passed=passed, issues=red_flags, suggested_fix=fix)

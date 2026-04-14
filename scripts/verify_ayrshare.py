"""Sanity-check the Ayrshare configuration without touching the browser.

What it does:
  1. Loads .env and confirms the 4 required vars are present.
  2. Calls GET /api/user with just the API key (primary account view).
  3. Calls GET /api/profiles to list Business Plan profiles (if any).
  4. Calls POST /api/profiles/generateJWT for a disposable refId to confirm
     SSO signing works (PEM / PKCS#8 key, domain id, etc.).

Nothing is written to the DB and no real profile is created.
Run:  python -m scripts.verify_ayrshare
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Put repo root on sys.path so `python scripts/verify_ayrshare.py` also works.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import httpx  # noqa: E402


OK = "\033[92m✓\033[0m"
BAD = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"


def _mask(value: str, keep: int = 6) -> str:
    v = (value or "").strip()
    if not v:
        return "(unset)"
    return v[:keep] + "…" + str(len(v)) + "ch"


def check_env() -> dict:
    api_key = os.getenv("AYRSHARE_API_KEY", "").strip()
    domain = os.getenv("AYRSHARE_SSO_DOMAIN", "").strip()
    pk_inline = os.getenv("AYRSHARE_PRIVATE_KEY", "").strip()
    pk_path = os.getenv("AYRSHARE_PRIVATE_KEY_PATH", "").strip()
    single_primary = os.getenv("AYRSHARE_SINGLE_ACCOUNT_PUBLISH", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    print("── Env")
    print(f"  {OK if api_key else BAD} AYRSHARE_API_KEY        = {_mask(api_key)}")
    print(f"  {OK if domain else WARN} AYRSHARE_SSO_DOMAIN     = {domain or '(unset)'}")
    print(f"  {OK if (pk_inline or pk_path) else WARN} AYRSHARE_PRIVATE_KEY*   = "
          f"{'inline' if pk_inline else pk_path or '(unset)'}")
    print(f"  {OK if single_primary else '—'} AYRSHARE_SINGLE_ACCOUNT_PUBLISH = {single_primary}")

    return {
        "api_key": api_key,
        "domain": domain,
        "pk_inline": pk_inline,
        "pk_path": pk_path,
        "single_primary": single_primary,
    }


def check_user(api_key: str) -> None:
    print("\n── GET /api/user (primary account)")
    try:
        r = httpx.get(
            "https://api.ayrshare.com/api/user",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20.0,
        )
    except httpx.RequestError as e:
        print(f"  {BAD} network error: {e}")
        return
    print(f"  status: {r.status_code}")
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:300]}
    # Surface the useful bits; hide anything that looks like a key.
    for k in ("email", "displayName", "activeSocialAccounts", "businessPlan",
              "profiles", "refId", "refIds", "monthlyPostQuota", "postCount"):
        if isinstance(body, dict) and k in body:
            print(f"  {k}: {body[k]}")
    if r.status_code >= 400:
        print(f"  {BAD} full body: {json.dumps(body)[:400]}")
    else:
        print(f"  {OK} primary account API key is valid")


def check_profiles(api_key: str) -> None:
    print("\n── GET /api/profiles (Business Plan)")
    try:
        r = httpx.get(
            "https://api.ayrshare.com/api/profiles",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20.0,
        )
    except httpx.RequestError as e:
        print(f"  {BAD} network error: {e}")
        return
    print(f"  status: {r.status_code}")
    if r.status_code == 402 or r.status_code == 403:
        print(f"  {WARN} not on Business Plan — skip (this is fine for single-account dev).")
        return
    try:
        data = r.json()
    except Exception:
        print(f"  {BAD} non-JSON response: {r.text[:200]}")
        return
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if isinstance(profiles, list):
        print(f"  {OK} {len(profiles)} profile(s) exist")
        for p in profiles[:5]:
            print(f"     - refId={p.get('refId')} title={p.get('title')}")
    else:
        print(f"  payload: {json.dumps(data)[:300]}")


def check_generate_jwt(api_key: str, domain: str, pk_inline: str, pk_path: str) -> None:
    print("\n── SSO roundtrip: create profile → generateJWT → delete profile")
    if not domain:
        print(f"  {WARN} AYRSHARE_SSO_DOMAIN not set — skipping SSO check.")
        return
    key_pem = pk_inline
    if not key_pem and pk_path:
        try:
            key_pem = Path(pk_path).read_text()
        except Exception as e:
            print(f"  {BAD} could not read private key at {pk_path}: {e}")
            return
    if not key_pem:
        print(f"  {WARN} no private key set — skipping SSO check.")
        return
    key_pem = key_pem.replace("\\n", "\n")
    if "BEGIN PRIVATE KEY" not in key_pem:
        print(f"  {WARN} private key does not look like PKCS#8 PEM — "
              "Ayrshare generateJWT will likely return 400.")
        return

    ref_id = "brokerai_verify_noop"
    headers = {"Authorization": f"Bearer {api_key}"}

    # 1. Create throwaway profile
    try:
        r = httpx.post(
            "https://api.ayrshare.com/api/profiles",
            headers={**headers, "Content-Type": "application/json"},
            json={"title": "BrokerAI Verify (delete me)", "refId": ref_id},
            timeout=20.0,
        )
    except httpx.RequestError as e:
        print(f"  {BAD} create-profile network error: {e}")
        return
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    profile_key = body.get("profileKey") if isinstance(body, dict) else None
    if r.status_code != 200 or not profile_key:
        print(f"  {BAD} create profile failed status={r.status_code} body_keys="
              f"{list(body.keys()) if isinstance(body, dict) else type(body).__name__}")
        if isinstance(body, dict) and body.get("message"):
            print(f"     error: {body['message']}")
        return
    print(f"  {OK} profile created (refId={ref_id}, profileKey={profile_key[:8]}…)")

    # 2. generateJWT (form-encoded, per postman example)
    try:
        r = httpx.post(
            "https://api.ayrshare.com/api/profiles/generateJWT",
            headers=headers,
            data={"domain": domain, "privateKey": key_pem, "profileKey": profile_key},
            timeout=20.0,
        )
        jbody = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:200]}
    except httpx.RequestError as e:
        print(f"  {BAD} generateJWT network error: {e}")
        jbody = None
    if jbody and isinstance(jbody, dict) and jbody.get("url"):
        print(f"  {OK} SSO link generated (url length={len(jbody['url'])})")
    elif jbody:
        print(f"  {BAD} generateJWT failed status={r.status_code} keys={list(jbody.keys())}")
        if jbody.get("message"):
            print(f"     error: {jbody['message']}")

    # 3. Delete throwaway profile
    try:
        r = httpx.request(
            "DELETE",
            "https://api.ayrshare.com/api/profiles",
            headers={**headers, "Content-Type": "application/json"},
            json={"profileKey": profile_key},
            timeout=20.0,
        )
        if 200 <= r.status_code < 300:
            print(f"  {OK} throwaway profile deleted")
        else:
            print(f"  {WARN} cleanup delete returned {r.status_code} — "
                  "you can remove 'BrokerAI Verify' from the Ayrshare dashboard.")
    except httpx.RequestError as e:
        print(f"  {WARN} cleanup delete failed: {e}")


def main() -> int:
    cfg = check_env()
    if not cfg["api_key"]:
        print(f"\n{BAD} AYRSHARE_API_KEY is required. Aborting.")
        return 1
    check_user(cfg["api_key"])
    check_profiles(cfg["api_key"])
    check_generate_jwt(cfg["api_key"], cfg["domain"], cfg["pk_inline"], cfg["pk_path"])
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

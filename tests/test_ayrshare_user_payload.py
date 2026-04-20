"""Unit tests for Ayrshare GET /api/user payload parsing (no live API)."""

import pytest

from backend.integrations.ayrshare import ayrshare_connect_env_snapshot, normalize_ayrshare_api_key
from backend.services import ayrshare_service as ayrshare_service_mod
from backend.services.ayrshare_service import (
    _api_key,
    _parse_active_social_accounts_from_user_payload,
    fetch_linked_platforms_via_ref_id,
    format_ayrshare_operator_hint,
    parse_profile_linked_platforms,
)


def test_parse_prefers_active_social_accounts_list() -> None:
    assert _parse_active_social_accounts_from_user_payload(
        {"activeSocialAccounts": ["Instagram", "Facebook"]}
    ) == ["instagram", "facebook"]


def test_parse_active_social_accounts_object_entries() -> None:
    body = {
        "activeSocialAccounts": [
            {"platform": "Facebook"},
            {"platform": "instagram"},
        ]
    }
    assert _parse_active_social_accounts_from_user_payload(body) == ["facebook", "instagram"]


def test_parse_falls_back_to_display_names_when_lists_empty() -> None:
    body = {
        "activeSocialAccounts": [],
        "displayNames": [
            {"platform": "linkedin", "displayName": "Acme"},
            {"platform": "instagram", "username": "acme"},
        ],
    }
    assert _parse_active_social_accounts_from_user_payload(body) == ["linkedin", "instagram"]


def test_parse_falls_back_to_active_social_networks() -> None:
    assert _parse_active_social_accounts_from_user_payload(
        {"activeSocialNetworks": ["linkedin"]}
    ) == ["linkedin"]


def test_parse_empty_list_then_per_platform_blocks() -> None:
    body = {
        "activeSocialAccounts": [],
        "facebook": {"active": True},
        "instagram": {"id": "123", "username": "acme"},
    }
    assert _parse_active_social_accounts_from_user_payload(body) == ["facebook", "instagram"]


def test_parse_per_platform_linked_flag() -> None:
    body = {"linkedin": {"linked": True}}
    assert _parse_active_social_accounts_from_user_payload(body) == ["linkedin"]


def test_parse_ignores_empty_platform_objects() -> None:
    body = {"facebook": {}, "instagram": {"active": False}}
    assert _parse_active_social_accounts_from_user_payload(body) == []


def test_parse_profile_social_health_linked() -> None:
    prof = {
        "refId": "x",
        "socialHealth": {
            "facebook": {"linked": True},
            "instagram": {"linked": False},
        },
    }
    assert parse_profile_linked_platforms(prof) == ["facebook"]


def test_parse_profile_prefers_active_social_accounts() -> None:
    prof = {
        "activeSocialAccounts": ["twitter"],
        "socialHealth": {"facebook": {"linked": True}},
    }
    assert parse_profile_linked_platforms(prof) == ["twitter"]


def test_fetch_linked_via_ref_delegates_to_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(ref_id: str, *, include=None, **kwargs):
        assert include == "socialHealth"
        return [
            {
                "refId": ref_id,
                "socialHealth": {"linkedin": {"linked": True}},
            }
        ]

    monkeypatch.setattr(ayrshare_service_mod, "fetch_profiles_by_ref_id", fake)
    assert fetch_linked_platforms_via_ref_id("brokerai_user_99") == ["linkedin"]


def test_format_ayrshare_operator_hint_invalid_api_key_message() -> None:
    msg = (
        "API Key not valid. Please be sure to send a Header Authorization containing "
        "'Bearer API_KEY'. https://www.ayrshare.com/docs/apis/overview"
    )
    hint = format_ayrshare_operator_hint(msg)
    assert "AYRSHARE_API_KEY" in hint
    assert "Render" in hint


def test_format_ayrshare_operator_hint_profile_key_as_api_key() -> None:
    msg = (
        "You cannot use a Profile Key as the API Key. Please the API Key and the Profile Key: "
        "https://www.ayrshare.com/docs/apis/overview#profile-key-format"
    )
    hint = format_ayrshare_operator_hint(msg)
    assert "Primary" in hint
    assert "Profile Key" in hint
    assert "profile-key-format" in hint


def test_api_key_env_strips_outer_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AYRSHARE_API_KEY", '"secret-key-value"')
    assert _api_key() == "secret-key-value"


def test_api_key_env_strips_single_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AYRSHARE_API_KEY", "'abc'")
    assert _api_key() == "abc"


def test_normalize_ayrshare_api_key_strips_bearer_prefix() -> None:
    assert normalize_ayrshare_api_key("Bearer abc-123") == "abc-123"
    assert normalize_ayrshare_api_key("BEARER xyz") == "xyz"


def test_normalize_collapses_embedded_whitespace() -> None:
    assert normalize_ayrshare_api_key("aa\nbb\t cc") == "aabbcc"


def test_ayrshare_connect_env_snapshot_whitespace_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AYRSHARE_API_KEY", "aa\nbb")
    monkeypatch.delenv("AYRSHARE_SSO_DOMAIN", raising=False)
    snap = ayrshare_connect_env_snapshot()
    assert snap["api_key_configured"] is True
    assert snap["api_key_length"] == 4
    assert snap["api_key_had_whitespace_removed"] is True


def test_ayrshare_connect_env_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AYRSHARE_API_KEY", "Bearer kkk")
    monkeypatch.setenv("AYRSHARE_SSO_DOMAIN", "id-test")
    monkeypatch.setenv("AYRSHARE_PRIVATE_KEY", "-----BEGIN")
    monkeypatch.delenv("AYRSHARE_PRIVATE_KEY_PATH", raising=False)
    snap = ayrshare_connect_env_snapshot()
    assert snap["api_key_configured"] is True
    assert snap["api_key_length"] == 3
    assert snap["api_key_had_whitespace_removed"] is False
    assert snap["sso_domain_configured"] is True
    assert snap["private_key_inline_configured"] is True
    assert snap["private_key_path_configured"] is False

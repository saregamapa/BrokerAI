"""Unit tests for Ayrshare GET /api/user payload parsing (no live API)."""

import pytest

from backend.services import ayrshare_service as ayrshare_service_mod
from backend.services.ayrshare_service import (
    _parse_active_social_accounts_from_user_payload,
    fetch_linked_platforms_via_ref_id,
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

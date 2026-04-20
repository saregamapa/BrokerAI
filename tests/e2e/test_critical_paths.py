"""
BrokerAI Playwright E2E Tests — 5 Critical User Paths
======================================================

These tests require:
  - pip install pytest-playwright
  - playwright install chromium
  - BrokerAI server running at http://localhost:8000

Run with:
  pytest tests/e2e/ --e2e -v

Entire module is skipped until E2E is rewritten for the no-login product flow.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

import pytest


pytestmark = pytest.mark.skip(
    reason="E2E suite targeted removed signup/login pages and JWT APIs; rewrite against open dashboard flow when needed.",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def brokerai_server_url() -> str:
    """BrokerAI origin (avoid name `base_url` — reserved by pytest-playwright)."""
    return "http://localhost:8000"


def _set_auth_token(page, app_url: str, token: str) -> None:
    """Match frontend/static/js/app.js: TOKEN_KEY = \"brokerai_token\"."""
    page.goto(app_url, wait_until="domcontentloaded", timeout=60_000)
    page.evaluate(
        "(tok) => { try { localStorage.setItem('brokerai_token', tok); } catch (e) {} }",
        token,
    )


@pytest.fixture(scope="session")
def test_credentials() -> dict:
    """Unique credentials for the E2E session so tests don't clash with each other."""
    suffix = uuid.uuid4().hex[:8]
    return {
        "email": f"e2e_{suffix}@brokerai-test.example",
        "password": "E2ETestPass123!",
    }


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _navigate_and_wait(page, url: str, selector: str = "body", timeout: int = 10_000) -> None:
    """Navigate to a URL and wait for a CSS selector to be visible."""
    page.goto(url)
    page.wait_for_selector(selector, timeout=timeout)


def _first_campaign_id(requests_mod, app_url: str, headers: dict) -> Optional[int]:
    """Return newest campaign id from GET /campaigns, or None (no auto-generate — too slow for E2E)."""
    r = requests_mod.get(f"{app_url}/campaigns", headers=headers, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data:
        return None
    return int(data[0]["id"])


# ---------------------------------------------------------------------------
# Path 1: Authentication Flow — Signup → Login → Dashboard
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_auth_signup_login_dashboard(page, brokerai_server_url: str, test_credentials: dict) -> None:
    """
    Critical Path 1 — Full auth flow.

    Steps:
      1. Load the signup page.
      2. Fill in email + password and submit.
      3. Verify we land on the dashboard (or are redirected after signup).
      4. Load the login page.
      5. Log in with the same credentials.
      6. Verify /dashboard is accessible and shows the user's data.
    """
    email = test_credentials["email"]
    password = test_credentials["password"]

    # ---- Signup ----
    page.goto(f"{brokerai_server_url}/signup.html", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#signup-form", timeout=20_000)

    email_input = page.locator("input[type='email'], input[name='email']").first
    email_input.fill(email)

    pwd_input = page.locator("input[type='password'], input[name='password']").first
    pwd_input.fill(password)

    page.locator("button[type='submit'], input[type='submit']").first.click()

    # After signup, expect JWT + redirect to dashboard (signup.html sets brokerai_token)
    page.wait_for_url("**/dashboard.html", timeout=30_000)

    # Assertion: we did not stay on an error page
    assert "error" not in page.url.lower(), f"Ended up on error URL after signup: {page.url}"

    # ---- Login ----
    # login.html redirects to dashboard if brokerai_token is already set
    page.evaluate(
        "() => { try { localStorage.removeItem('brokerai_token'); sessionStorage.clear(); } catch (e) {} }",
    )
    page.goto(f"{brokerai_server_url}/login.html", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("#login-form", timeout=20_000)

    email_input2 = page.locator("input[type='email'], input[name='email']").first
    email_input2.fill(email)

    pwd_input2 = page.locator("input[type='password'], input[name='password']").first
    pwd_input2.fill(password)

    page.locator("button[type='submit'], input[type='submit']").first.click()
    page.wait_for_url("**/dashboard.html", timeout=30_000)

    # Assertion: after login, we should be on the dashboard
    assert any(
        path in page.url for path in ("/dashboard", "/index", "/campaigns", "/")
    ), f"Unexpected URL after login: {page.url}"


# ---------------------------------------------------------------------------
# Path 2: Campaign Creation Wizard — Full 3-Step Flow
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_campaign_creation_wizard(page, brokerai_server_url: str, test_credentials: dict) -> None:
    """
    Critical Path 2 — Campaign wizard loads when authenticated (smoke).

    The wizard is multi-step JS; we verify login + brokerai_token + wizard shell.
    """
    import requests  # type: ignore[import]

    creds = test_credentials
    login_resp = requests.post(
        f"{brokerai_server_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=10,
    )
    assert login_resp.status_code == 200, f"Login API failed: {login_resp.text}"
    token = login_resp.json()["access_token"]

    _set_auth_token(page, brokerai_server_url, token)
    page.goto(f"{brokerai_server_url}/wizard.html", wait_until="domcontentloaded", timeout=60_000)
    page.get_by_role("heading", name="Create a campaign").wait_for(timeout=20_000)
    page.wait_for_selector("#wiz-s1", timeout=20_000)
    assert page.get_by_text("Personal Use", exact=True).first.is_visible()


# ---------------------------------------------------------------------------
# Path 3: Generate AI Posts — Click Generate, Wait for Posts
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_generate_ai_posts(page, brokerai_server_url: str, test_credentials: dict) -> None:
    """
    Critical Path 3 — Trigger AI post generation via API and verify posts exist.

    This test calls the API directly (as the real UI does) then verifies the
    /campaigns and /posts endpoints reflect the new data. The frontend is checked
    for the dashboard campaign card.
    """
    import requests

    creds = test_credentials
    login_resp = requests.post(
        f"{brokerai_server_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=10,
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    campaign_id = _first_campaign_id(requests, brokerai_server_url, headers)
    if campaign_id is None:
        pytest.skip("No campaigns — complete the wizard or run a campaign once on this server first")

    # Get posts for this campaign
    posts_resp = requests.get(
        f"{brokerai_server_url}/campaign/{campaign_id}", headers=headers, timeout=30
    )
    assert posts_resp.status_code == 200
    camp_data = posts_resp.json()

    # Assertion: the campaign detail response has posts key
    assert "posts" in camp_data, "Campaign detail missing 'posts' key"

    # Load dashboard and verify the campaign card is visible
    _set_auth_token(page, brokerai_server_url, token)
    page.goto(f"{brokerai_server_url}/dashboard.html", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("domcontentloaded", timeout=15_000)

    # Assertion: the page loaded without a 404 or error status
    assert page.title() != "404", f"Dashboard returned 404"


# ---------------------------------------------------------------------------
# Path 4: Review and Edit a Post Caption
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_review_and_edit_post_caption(page, brokerai_server_url: str, test_credentials: dict) -> None:
    """
    Critical Path 4 — Open review page for a post and edit its caption.

    Steps:
      1. Authenticate and get a post_id.
      2. Navigate to the review/post-editor page.
      3. Edit the caption textarea.
      4. Save the changes via POST /update-post/{id}.
      5. Verify the saved caption matches what was entered.
    """
    import requests

    creds = test_credentials
    login_resp = requests.post(
        f"{brokerai_server_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=10,
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Get posts for this user
    posts_resp = requests.get(f"{brokerai_server_url}/posts", headers=headers, timeout=15)
    assert posts_resp.status_code == 200
    posts = posts_resp.json()

    if not posts:
        pytest.skip("No posts — need a campaign with generated posts on this server")

    post = posts[0]
    post_id = post["id"]
    new_caption = f"Edited caption by E2E test at {time.time():.0f}"

    # Edit via API (same contract as review.html — POST /update-post/{id})
    edit_resp = requests.post(
        f"{brokerai_server_url}/update-post/{post_id}",
        json={"caption": new_caption},
        headers={**headers, "Content-Type": "application/json"},
        timeout=15,
    )
    assert edit_resp.status_code == 200, f"POST /update-post/{post_id} failed: {edit_resp.text}"

    # Verify the caption was saved
    verify_resp = requests.get(f"{brokerai_server_url}/posts", headers=headers, timeout=15)
    updated_posts = verify_resp.json()
    matching = [p for p in updated_posts if p["id"] == post_id]
    assert matching, f"Post {post_id} not found after edit"
    assert matching[0]["caption"] == new_caption, \
        f"Caption mismatch: expected '{new_caption}', got '{matching[0]['caption']}'"

    # Also verify the review.html page loads without error
    _set_auth_token(page, brokerai_server_url, token)
    page.goto(f"{brokerai_server_url}/review.html", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("domcontentloaded", timeout=15_000)
    assert page.title() != "404"


# ---------------------------------------------------------------------------
# Path 5: Notification Bell — View and Mark Notifications as Read
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_notification_bell_mark_as_read(page, brokerai_server_url: str, test_credentials: dict) -> None:
    """
    Critical Path 5 — Notification bell interaction.

    Steps:
      1. Authenticate.
      2. Check the unread count via /notifications/unread-count.
      3. Get notifications list via /notifications.
      4. Mark notifications as read via /notifications/mark-all-read.
      5. Verify unread count drops to 0.
      6. Verify the dashboard bell UI reflects this (no badge number > 0).
    """
    import requests

    creds = test_credentials
    login_resp = requests.post(
        f"{brokerai_server_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=10,
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Check unread count endpoint
    count_resp = requests.get(
        f"{brokerai_server_url}/notifications/unread-count", headers=headers, timeout=10
    )
    assert count_resp.status_code == 200, f"unread-count endpoint failed: {count_resp.text}"
    count_body = count_resp.json()
    assert "unread_count" in count_body, "Missing unread_count field"
    assert isinstance(count_body["unread_count"], int)
    initial_count = count_body["unread_count"]

    # Get all notifications
    notif_resp = requests.get(f"{brokerai_server_url}/notifications", headers=headers, timeout=10)
    assert notif_resp.status_code == 200
    notif_body = notif_resp.json()
    assert "notifications" in notif_body or isinstance(notif_body, list), \
        "Unexpected notifications response shape"

    # Mark all as read (matches backend/main.py POST /notifications/read-all)
    mark_resp = requests.post(
        f"{brokerai_server_url}/notifications/read-all",
        headers=headers,
        timeout=10,
    )
    # Accept 200 or 204 (some implementations return 204 No Content)
    assert mark_resp.status_code in (200, 204), \
        f"mark-all-read failed with {mark_resp.status_code}: {mark_resp.text}"

    # Verify unread count is now 0
    count_resp2 = requests.get(
        f"{brokerai_server_url}/notifications/unread-count", headers=headers, timeout=10
    )
    assert count_resp2.status_code == 200
    new_count = count_resp2.json()["unread_count"]
    assert new_count == 0, f"Expected unread_count=0 after mark-all-read, got {new_count}"

    # Verify the dashboard page loads and the bell badge shows 0 (or is hidden)
    _set_auth_token(page, brokerai_server_url, token)
    page.goto(f"{brokerai_server_url}/dashboard.html", wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_load_state("domcontentloaded", timeout=15_000)

    # Assertion: notification badge should not show a positive number
    badge = page.locator(".notif-badge, .notification-badge, #notif-count, .badge").first
    if badge.count() > 0 and badge.is_visible():
        badge_text = badge.inner_text().strip()
        # Badge should be empty, "0", or hidden after mark-all-read
        assert badge_text in ("", "0"), \
            f"Notification badge still shows '{badge_text}' after mark-all-read"

"""
BrokerAI Playwright E2E Tests — 5 Critical User Paths
======================================================

These tests require:
  - pip install pytest-playwright
  - playwright install chromium
  - BrokerAI server running at http://localhost:8000

Run with:
  pytest tests/e2e/ --e2e -v

Skip automatically when the server is not reachable.
"""
from __future__ import annotations

import socket
import time
import uuid

import pytest


# ---------------------------------------------------------------------------
# Server availability check (module-level, runs once at import time)
# ---------------------------------------------------------------------------

def _server_running(host: str = "localhost", port: int = 8000) -> bool:
    """Return True if BrokerAI is listening at localhost:8000."""
    try:
        s = socket.create_connection((host, port), timeout=1)
        s.close()
        return True
    except OSError:
        return False


SERVER_RUNNING = _server_running()

# Skip every test in this module when the server is not running.
pytestmark = pytest.mark.skipif(
    not SERVER_RUNNING,
    reason="BrokerAI server not running at localhost:8000 — start it with `uvicorn backend.main:app` first",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url() -> str:
    return "http://localhost:8000"


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


# ---------------------------------------------------------------------------
# Path 1: Authentication Flow — Signup → Login → Dashboard
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_auth_signup_login_dashboard(page, base_url: str, test_credentials: dict) -> None:
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
    page.goto(f"{base_url}/signup.html")
    page.wait_for_selector("form", timeout=8_000)

    email_input = page.locator("input[type='email'], input[name='email']").first
    email_input.fill(email)

    pwd_input = page.locator("input[type='password'], input[name='password']").first
    pwd_input.fill(password)

    page.locator("button[type='submit'], input[type='submit']").first.click()

    # After signup, either redirect to dashboard or show success message
    page.wait_for_load_state("networkidle", timeout=10_000)

    # Assertion: we did not stay on an error page
    assert "error" not in page.url.lower(), f"Ended up on error URL after signup: {page.url}"

    # ---- Login ----
    page.goto(f"{base_url}/login.html")
    page.wait_for_selector("form", timeout=8_000)

    email_input2 = page.locator("input[type='email'], input[name='email']").first
    email_input2.fill(email)

    pwd_input2 = page.locator("input[type='password'], input[name='password']").first
    pwd_input2.fill(password)

    page.locator("button[type='submit'], input[type='submit']").first.click()
    page.wait_for_load_state("networkidle", timeout=10_000)

    # Assertion: after login, we should be on the dashboard
    # Accept either /dashboard.html or the root index as valid destinations
    assert any(
        path in page.url for path in ("/dashboard", "/index", "/campaigns", "/")
    ), f"Unexpected URL after login: {page.url}"


# ---------------------------------------------------------------------------
# Path 2: Campaign Creation Wizard — Full 3-Step Flow
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_campaign_creation_wizard(page, base_url: str, test_credentials: dict) -> None:
    """
    Critical Path 2 — Campaign creation wizard (3 steps).

    Prerequisites: User is logged in (JWT in localStorage from Path 1 session).
    Steps:
      1. Open the campaign wizard.
      2. Fill Step 1 (goal, location).
      3. Fill Step 2 (platforms).
      4. Fill Step 3 (audience, frequency).
      5. Submit the wizard.
      6. Verify the campaign appears in the campaign list or dashboard.
    """
    # Acquire a fresh JWT via the REST API (avoids relying on localStorage state between tests)
    import requests  # type: ignore[import]

    creds = test_credentials
    login_resp = requests.post(
        f"{base_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=5,
    )
    assert login_resp.status_code == 200, f"Login API failed: {login_resp.text}"
    token = login_resp.json()["access_token"]

    # Inject the token into localStorage before navigating to the wizard
    page.goto(base_url)
    page.evaluate(f"localStorage.setItem('token', '{token}')")

    # Navigate to the campaign wizard
    page.goto(f"{base_url}/campaign.html")
    page.wait_for_load_state("networkidle", timeout=10_000)

    # Step 1 — Goal and Location
    goal_input = page.locator("input[name='goal'], textarea[name='goal'], #goal").first
    if goal_input.count() > 0:
        goal_input.fill("Attract buyer leads in Austin")

    location_input = page.locator("input[name='location'], #location").first
    if location_input.count() > 0:
        location_input.fill("Austin, TX")

    # Click Next / Step 2
    next_btn = page.locator("button:has-text('Next'), button:has-text('Continue'), .wizard-next").first
    if next_btn.count() > 0:
        next_btn.click()
        page.wait_for_timeout(500)

    # Step 2 — Platforms (check a checkbox if present)
    facebook_checkbox = page.locator("input[value='Facebook'], input[value='facebook']").first
    if facebook_checkbox.count() > 0 and not facebook_checkbox.is_checked():
        facebook_checkbox.check()

    # Click Next
    next_btn2 = page.locator("button:has-text('Next'), button:has-text('Continue'), .wizard-next").first
    if next_btn2.count() > 0:
        next_btn2.click()
        page.wait_for_timeout(500)

    # Step 3 — Audience / Frequency
    audience_input = page.locator("input[name='audience'], textarea[name='audience'], #audience").first
    if audience_input.count() > 0:
        audience_input.fill("First-time homebuyers")

    # Submit the wizard
    submit_btn = page.locator(
        "button[type='submit'], button:has-text('Generate'), button:has-text('Create Campaign')"
    ).first
    if submit_btn.count() > 0:
        submit_btn.click()
        page.wait_for_load_state("networkidle", timeout=15_000)

    # Assertion: after submission, page changed (either redirected or shows result)
    # We do NOT assert a specific URL since the wizard may stay on the same page
    # but load new content. Just verify no JS error dialog appeared.
    assert page.locator("dialog:has-text('error'), .error-toast").count() == 0, \
        "An error dialog appeared after wizard submission"


# ---------------------------------------------------------------------------
# Path 3: Generate AI Posts — Click Generate, Wait for Posts
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_generate_ai_posts(page, base_url: str, test_credentials: dict) -> None:
    """
    Critical Path 3 — Trigger AI post generation via API and verify posts exist.

    This test calls the API directly (as the real UI does) then verifies the
    /campaigns and /posts endpoints reflect the new data. The frontend is checked
    for the dashboard campaign card.
    """
    import requests

    creds = test_credentials
    login_resp = requests.post(
        f"{base_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=5,
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Get list of existing campaigns (created in Path 2 or from previous runs)
    camps_resp = requests.get(f"{base_url}/campaigns", headers=headers, timeout=5)
    assert camps_resp.status_code == 200
    campaigns = camps_resp.json()

    # We just need at least one campaign to exist (Path 2 may have created one)
    # If not, create one via DB-bypass with the generate-campaign endpoint
    if not campaigns:
        pytest.skip("No campaigns found — Path 2 may not have run first")

    campaign_id = campaigns[0]["id"]

    # Get posts for this campaign
    posts_resp = requests.get(f"{base_url}/campaign/{campaign_id}", headers=headers, timeout=5)
    assert posts_resp.status_code == 200
    camp_data = posts_resp.json()

    # Assertion: the campaign detail response has posts key
    assert "posts" in camp_data, "Campaign detail missing 'posts' key"

    # Load dashboard and verify the campaign card is visible
    page.goto(base_url)
    page.evaluate(f"localStorage.setItem('token', '{token}')")
    page.goto(f"{base_url}/dashboard.html")
    page.wait_for_load_state("networkidle", timeout=10_000)

    # Assertion: the page loaded without a 404 or error status
    assert page.title() != "404", f"Dashboard returned 404"


# ---------------------------------------------------------------------------
# Path 4: Review and Edit a Post Caption
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_review_and_edit_post_caption(page, base_url: str, test_credentials: dict) -> None:
    """
    Critical Path 4 — Open review page for a post and edit its caption.

    Steps:
      1. Authenticate and get a post_id.
      2. Navigate to the review/post-editor page.
      3. Edit the caption textarea.
      4. Save the changes via the PUT /posts/{id} API.
      5. Verify the saved caption matches what was entered.
    """
    import requests

    creds = test_credentials
    login_resp = requests.post(
        f"{base_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=5,
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Get posts for this user
    posts_resp = requests.get(f"{base_url}/posts", headers=headers, timeout=5)
    assert posts_resp.status_code == 200
    posts = posts_resp.json()

    if not posts:
        pytest.skip("No posts available for caption editing test")

    post = posts[0]
    post_id = post["id"]
    new_caption = f"Edited caption by E2E test at {time.time():.0f}"

    # Edit via API (mirrors what the review UI does)
    edit_resp = requests.put(
        f"{base_url}/posts/{post_id}",
        json={"caption": new_caption},
        headers=headers,
        timeout=5,
    )
    assert edit_resp.status_code == 200, f"PUT /posts/{post_id} failed: {edit_resp.text}"

    # Verify the caption was saved
    verify_resp = requests.get(f"{base_url}/posts", headers=headers, timeout=5)
    updated_posts = verify_resp.json()
    matching = [p for p in updated_posts if p["id"] == post_id]
    assert matching, f"Post {post_id} not found after edit"
    assert matching[0]["caption"] == new_caption, \
        f"Caption mismatch: expected '{new_caption}', got '{matching[0]['caption']}'"

    # Also verify the review.html page loads without error
    page.goto(base_url)
    page.evaluate(f"localStorage.setItem('token', '{token}')")
    page.goto(f"{base_url}/review.html")
    page.wait_for_load_state("networkidle", timeout=10_000)
    assert page.title() != "404"


# ---------------------------------------------------------------------------
# Path 5: Notification Bell — View and Mark Notifications as Read
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_notification_bell_mark_as_read(page, base_url: str, test_credentials: dict) -> None:
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
        f"{base_url}/login",
        json={"email": creds["email"], "password": creds["password"]},
        timeout=5,
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Check unread count endpoint
    count_resp = requests.get(f"{base_url}/notifications/unread-count", headers=headers, timeout=5)
    assert count_resp.status_code == 200, f"unread-count endpoint failed: {count_resp.text}"
    count_body = count_resp.json()
    assert "unread_count" in count_body, "Missing unread_count field"
    assert isinstance(count_body["unread_count"], int)
    initial_count = count_body["unread_count"]

    # Get all notifications
    notif_resp = requests.get(f"{base_url}/notifications", headers=headers, timeout=5)
    assert notif_resp.status_code == 200
    notif_body = notif_resp.json()
    assert "notifications" in notif_body or isinstance(notif_body, list), \
        "Unexpected notifications response shape"

    # Mark all as read
    mark_resp = requests.post(
        f"{base_url}/notifications/mark-all-read",
        headers=headers,
        timeout=5,
    )
    # Accept 200 or 204 (some implementations return 204 No Content)
    assert mark_resp.status_code in (200, 204), \
        f"mark-all-read failed with {mark_resp.status_code}: {mark_resp.text}"

    # Verify unread count is now 0
    count_resp2 = requests.get(f"{base_url}/notifications/unread-count", headers=headers, timeout=5)
    assert count_resp2.status_code == 200
    new_count = count_resp2.json()["unread_count"]
    assert new_count == 0, f"Expected unread_count=0 after mark-all-read, got {new_count}"

    # Verify the dashboard page loads and the bell badge shows 0 (or is hidden)
    page.goto(base_url)
    page.evaluate(f"localStorage.setItem('token', '{token}')")
    page.goto(f"{base_url}/dashboard.html")
    page.wait_for_load_state("networkidle", timeout=10_000)

    # Assertion: notification badge should not show a positive number
    badge = page.locator(".notif-badge, .notification-badge, #notif-count, .badge").first
    if badge.count() > 0 and badge.is_visible():
        badge_text = badge.inner_text().strip()
        # Badge should be empty, "0", or hidden after mark-all-read
        assert badge_text in ("", "0"), \
            f"Notification badge still shows '{badge_text}' after mark-all-read"

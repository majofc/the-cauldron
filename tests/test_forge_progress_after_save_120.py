"""After the day's save the Forge lands on Progress (#120) — a Playwright test.

Today → Save → the anvil → its unlocks (Claim it / Not yet) → the Progress tab at
/cauldron/forge/progress/, with the session just saved marked in the history and
on the chart for ~2s. However the anvil is closed (button, Escape, backdrop) the
unlocks still come first. A past unfinished session saved from the blocking
sheet keeps the user on Today, and a failed save shows no anvil and goes nowhere.

Run inside the web container, where Chromium is installed:
    docker compose exec web python -m pytest the_cauldron/tests/test_forge_progress_after_save_120.py
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from the_cauldron.models import PrescribedExercise, SetLog, WorkoutSession

# The live-server / logged-in browser fixtures of the overlay test (#57).
from the_cauldron.tests.test_overlays_visual import (  # noqa: F401
    PHONE,
    browser,
    forge_user,
    open_forge,
    session_cookie,
)

pytest.importorskip("playwright.sync_api")

FAKE_UNLOCK_PRESCRIPTION = "00000000-0000-4000-8000-000000000120"


def _fill_today(page):
    """Log every set at its target — the inputs autosave as they're typed."""
    page.wait_for_selector("#forge-today-list .forge-actual")
    for inp in page.locator("#forge-today-list .forge-actual").all():
        inp.fill(inp.get_attribute("placeholder") or "5")
    page.wait_for_selector("#forge-log-session", state="visible")


def _inject_unlock(page, accepted):
    """Add one earned unlock to the save's response, and answer its Claim it /
    Not yet locally, so the unlock step is exercised without grinding sessions."""

    def log(route):
        response = route.fetch()
        body = response.json()
        body["progression"] = list(body.get("progression") or []) + [{
            "unlock": {
                "prescription": FAKE_UNLOCK_PRESCRIPTION,
                "from_name": "Knee Push-up",
                "to_name": "Push-up",
            }
        }]
        route.fulfill(response=response, body=json.dumps(body))

    def decide(route):
        accepted.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/cauldron/api/sessions/*/log/", log)
    page.route(f"**/cauldron/api/progression/{FAKE_UNLOCK_PRESCRIPTION}/*/", decide)


def _assert_on_progress_with_highlight(page, session_uuid):
    page.wait_for_url("**/cauldron/forge/progress/")
    assert page.locator('.forge-panel[data-panel="progress"]').is_visible()
    assert not page.locator('.forge-panel[data-panel="today"]').is_visible()
    page.wait_for_selector(f'.forge-history-card.is-just-saved[data-session="{session_uuid}"]')
    page.wait_for_selector("#forge-chart .forge-chart-dot.is-just-saved")
    # …and the mark clears on its own after ~2s.
    page.wait_for_function(
        "() => !document.querySelector('.is-just-saved')", timeout=5000
    )


def _saved_uuid(user):
    return str(
        WorkoutSession.objects.filter(user=user, status="completed")
        .latest("performed_at").uuid
    )


@pytest.mark.parametrize("close_with", ["escape", "backdrop"])
def test_closing_the_anvil_any_way_still_offers_the_unlocks_then_lands_on_progress(
    open_forge, forge_user, close_with
):
    page = open_forge(PHONE, "today")
    accepted = []
    _inject_unlock(page, accepted)
    _fill_today(page)
    page.click("#forge-log-session")
    page.wait_for_selector(".forge-anvil-overlay")
    if close_with == "escape":
        page.keyboard.press("Escape")
    else:
        page.mouse.click(4, 4)  # the backdrop, outside the card
    page.wait_for_selector(".forge-anvil-overlay", state="detached")

    # The unlock is not skipped: Claim it / Not yet comes before Progress.
    page.wait_for_selector("#forge-reveal:not([hidden]) .forge-unlock-title")
    assert "/forge/today/" in page.url
    page.locator('#forge-reveal-actions button[data-act="deny"]').click()
    page.wait_for_selector("#forge-reveal", state="hidden")
    assert accepted and accepted[0].endswith("/deny/")

    _assert_on_progress_with_highlight(page, _saved_uuid(forge_user))

    # Back returns to Today.
    page.go_back()
    page.wait_for_url("**/cauldron/forge/today/")
    assert page.locator('.forge-panel[data-panel="today"]').is_visible()


def test_bank_the_day_lands_on_progress(open_forge, forge_user):
    page = open_forge(PHONE, "today")
    _fill_today(page)
    page.click("#forge-log-session")
    page.locator(".forge-anvil-actions button").click()
    _assert_on_progress_with_highlight(page, _saved_uuid(forge_user))


def test_a_failed_save_shows_no_anvil_and_stays_on_today(open_forge, forge_user):
    page = open_forge(PHONE, "today")
    _fill_today(page)
    page.route(
        "**/cauldron/api/sessions/*/log/",
        lambda route: route.fulfill(status=500, content_type="application/json", body="{}"),
    )
    page.click("#forge-log-session")
    page.wait_for_selector("#forge-loader", state="hidden")
    page.wait_for_timeout(600)
    assert page.locator(".forge-anvil-overlay").count() == 0
    assert "/forge/today/" in page.url
    assert page.locator('.forge-panel[data-panel="today"]').is_visible()


@pytest.fixture
def unfinished_yesterday(forge_user):
    """A planned session from yesterday with values logged — it gates Today."""
    presc = PrescribedExercise.objects.filter(
        day__program__user=forge_user, day__program__is_active=True
    ).order_by("day__day_index", "order").first()
    session = WorkoutSession.objects.create(
        user=forge_user, program_day=presc.day, status=WorkoutSession.Status.PLANNED,
        scheduled_for=timezone.localdate() - timedelta(days=1),
    )
    for i in range(2):
        SetLog.objects.create(
            session=session, prescribed_exercise=presc, exercise=presc.exercise,
            set_index=i, expected_reps=presc.target_reps_min,
            expected_load=presc.target_load, actual_reps=presc.target_reps_min,
        )
    return session


def test_saving_a_past_unfinished_session_stays_on_today(open_forge, unfinished_yesterday):
    page = open_forge(PHONE, "today")
    page.wait_for_selector("#forge-unfinished-modal:not([hidden])")
    page.click("#forge-unfinished-save")
    page.wait_for_selector(".forge-anvil-overlay")
    page.locator(".forge-anvil-actions button").click()
    page.wait_for_selector(".forge-anvil-overlay", state="detached")
    page.wait_for_timeout(800)
    assert "/forge/today/" in page.url
    assert page.locator('.forge-panel[data-panel="today"]').is_visible()

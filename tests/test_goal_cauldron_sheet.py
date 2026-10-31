"""The goal cauldron's dialog (#119): centred on desktop, a bottom sheet on phones.

A Playwright test, like ``test_overlays_visual.py``: it opens the Journal, taps a
gauge and measures the ``<dialog>``. It asserts geometry rather than pixels — the
pot is inline SVG fed by the user's data, so a baseline would only re-test #107.

Run inside the web container, where Chromium is installed:
    docker compose exec web python -m pytest the_cauldron/tests/test_goal_cauldron_sheet.py
"""

import os

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from the_cauldron.models import CauldronProfile, IntakeEntry
from the_cauldron.services import legal, nutrition

playwright_sync = pytest.importorskip("playwright.sync_api")

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

DESKTOP = {"width": 1280, "height": 800}
SHORT_DESKTOP = {"width": 1280, "height": 420}
PHONE = {"width": 390, "height": 844}
MIN_TARGET = 44
NUTRIENT = "sodium_mg"

STABILISE_CSS = """
*, *::before, *::after {
  animation: none !important; transition: none !important; caret-color: transparent !important;
}
#arcano-stars { display: none !important; }
"""

User = get_user_model()


@pytest.fixture
def eater(transactional_db):
    """An adult with consent and a day of quick entries: half know their sodium (the
    pot), half don't (the fog, each with a fill form — enough to overflow a phone)."""
    user = User.objects.create_user(username="sheet-user", email="sheet@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
    CauldronProfile.objects.create(user=user, birth_year=1990, sex="female")
    tz = nutrition.user_tz(user)
    now = timezone.now()
    for i in range(10):
        when = now - timezone.timedelta(hours=i + 1)
        values = {"kcal": 300 + i, NUTRIENT: 200 + 10 * i} if i % 2 else {"kcal": 250 + i}
        nutrients, missing = nutrition.clean_quick_values(values)
        IntakeEntry.objects.create(
            user=user, eaten_at=when, local_date=when.astimezone(tz).date(), source="quick",
            label=f"Meal {i}", quantity=1, unit="portion", nutrients=nutrients, missing=missing,
        )
    return user


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as pw:
        try:
            chromium = pw.chromium.launch()
        except Exception as exc:  # pragma: no cover — only without the image's Chromium
            pytest.skip(f"Chromium unavailable: {exc}")
        yield chromium
        chromium.close()


@pytest.fixture
def open_cauldron(browser, live_server, eater, settings):
    """``open_cauldron(viewport, reduced_motion="reduce")`` → the Journal with the
    sodium cauldron open and filled."""
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
    client = Client()
    client.force_login(eater)
    cookie = client.cookies["sessionid"].value
    contexts = []

    def _open(viewport, reduced_motion="reduce", stabilise=True):
        context = browser.new_context(viewport=viewport, reduced_motion=reduced_motion, has_touch=True)
        contexts.append(context)
        context.add_cookies([{"name": "sessionid", "value": cookie, "url": live_server.url}])
        page = context.new_page()
        page.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith(live_server.url)
            else route.abort(),
        )
        page.goto(f"{live_server.url}/cauldron/journal/")
        if stabilise:
            page.add_style_tag(content=STABILISE_CSS)
        gauge = page.locator(f'.nut-gauge[data-nutrient="{NUTRIENT}"]')
        gauge.wait_for()
        gauge.click()
        page.wait_for_selector("#nut-cauldron[open]")
        page.wait_for_selector("#nut-cauldron-body .nut-cauldron-fog")
        return page

    yield _open
    for context in contexts:
        context.close()


def _box(page, selector):
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} is not rendered"
    return box


def _assert_inside(box, viewport, what):
    assert box["x"] >= 0 and box["y"] >= 0, f"{what} starts off-screen: {box}"
    assert box["x"] + box["width"] <= viewport["width"] + 0.5, f"{what} overflows right: {box}"
    assert box["y"] + box["height"] <= viewport["height"] + 0.5, f"{what} overflows bottom: {box}"


def _is_open(page):
    return page.evaluate("document.getElementById('nut-cauldron').open")


# ── Desktop: centred ─────────────────────────────────────────────────────────


def test_desktop_opens_centred(open_cauldron):
    page = open_cauldron(DESKTOP)
    box = _box(page, "#nut-cauldron")
    left, right = box["x"], DESKTOP["width"] - box["x"] - box["width"]
    top, bottom = box["y"], DESKTOP["height"] - box["y"] - box["height"]
    assert abs(left - right) <= 2, f"not centred horizontally: {left} vs {right}"
    assert abs(top - bottom) <= 2, f"not centred vertically: {top} vs {bottom}"
    assert box["width"] <= 560
    # Desktop has no drag handle.
    assert not page.locator(".nut-cauldron-handle").is_visible()


def test_desktop_tall_content_scrolls_inside_the_viewport(open_cauldron):
    page = open_cauldron(SHORT_DESKTOP)
    _assert_inside(_box(page, "#nut-cauldron"), SHORT_DESKTOP, "the dialog")
    assert page.evaluate(
        "(() => { const d = document.getElementById('nut-cauldron'); return d.scrollHeight > d.clientHeight; })()"
    )


def test_all_four_corner_ornaments_on_desktop(open_cauldron):
    page = open_cauldron(DESKTOP)
    for corner in ("tl", "tr", "bl", "br"):
        assert page.locator(f"#nut-cauldron .corner-{corner}").is_visible(), corner


# ── Phone: a bottom sheet ────────────────────────────────────────────────────


def test_phone_opens_as_a_bottom_sheet(open_cauldron):
    page = open_cauldron(PHONE)
    box = _box(page, "#nut-cauldron")
    assert box["x"] == 0 and box["width"] == PHONE["width"]
    assert abs(box["y"] + box["height"] - PHONE["height"]) <= 1, f"not docked to the bottom: {box}"
    assert box["height"] <= PHONE["height"] * 0.92 + 1
    assert page.locator(".nut-cauldron-handle").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]


def test_phone_head_stays_while_the_content_scrolls(open_cauldron):
    page = open_cauldron(PHONE)
    before = _box(page, ".nut-cauldron-head")
    scrolled = page.evaluate(
        "(() => { const b = document.querySelector('.nut-cauldron-scroll');"
        " b.scrollTop = b.scrollHeight; return b.scrollTop; })()"
    )
    assert scrolled > 0, "the content should overflow and scroll on a phone"
    assert _box(page, ".nut-cauldron-head") == before
    close = _box(page, "#nut-cauldron-close")
    _assert_inside(close, PHONE, "the close button")
    assert close["width"] >= MIN_TARGET and close["height"] >= MIN_TARGET


def test_phone_shows_only_the_top_corner_ornaments(open_cauldron):
    page = open_cauldron(PHONE)
    assert page.locator("#nut-cauldron .corner-tl").is_visible()
    assert page.locator("#nut-cauldron .corner-tr").is_visible()
    assert not page.locator("#nut-cauldron .corner-bl").is_visible()
    assert not page.locator("#nut-cauldron .corner-br").is_visible()


def _drag_head(page, distance):
    handle = _box(page, ".nut-cauldron-handle")
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + distance / 3, steps=3)
    page.mouse.move(x, y + distance, steps=3)
    page.mouse.up()


def test_phone_swiping_the_head_down_closes_it(open_cauldron):
    page = open_cauldron(PHONE)
    _drag_head(page, 200)
    page.wait_for_selector("#nut-cauldron", state="hidden")


def test_phone_a_short_drag_snaps_back(open_cauldron):
    page = open_cauldron(PHONE)
    _drag_head(page, 20)
    assert _is_open(page)
    assert page.evaluate("document.getElementById('nut-cauldron').style.transform") == ""


# ── Closing, at both sizes ───────────────────────────────────────────────────


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_close_button_closes(open_cauldron, viewport):
    page = open_cauldron(viewport)
    page.locator("#nut-cauldron-close").click()
    page.wait_for_selector("#nut-cauldron", state="hidden")


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_escape_closes(open_cauldron, viewport):
    page = open_cauldron(viewport)
    page.keyboard.press("Escape")
    page.wait_for_selector("#nut-cauldron", state="hidden")


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["desktop", "phone"])
def test_backdrop_click_closes(open_cauldron, viewport):
    page = open_cauldron(viewport)
    page.mouse.click(viewport["width"] / 2, 4)  # above the dialog / the sheet
    page.wait_for_selector("#nut-cauldron", state="hidden")


# ── Motion ───────────────────────────────────────────────────────────────────


def test_phone_slides_in_unless_motion_is_reduced(open_cauldron):
    animation = "getComputedStyle(document.getElementById('nut-cauldron')).animationName"
    moving = open_cauldron(PHONE, reduced_motion="no-preference", stabilise=False)
    assert moving.evaluate(animation) == "nut-sheet-in"
    still = open_cauldron(PHONE, reduced_motion="reduce", stabilise=False)
    assert still.evaluate(animation) == "none"

"""Forge overlays on phones and desktop (#57) — a Playwright visual test.

Each overlay is opened at 390×844 (phone) and 900×900 (desktop control) and
compared against the committed baselines in ``visual_baselines/``. Beyond the
pixel diff it asserts what the ticket is about: a close control is always inside
the viewport with a ≥44px hit area, nothing scrolls the page sideways at the
smallest supported widths, the dialogs close on Escape / backdrop / swipe, and the
results reveal stays non-dismissable.

Baselines are written when missing (or with ``FORGE_UPDATE_BASELINES=1``) and the
test then fails, so a fresh screenshot is always looked at before it is trusted.

Run inside the web container, where Chromium is installed:
    docker compose exec -e DJANGO_SETTINGS_MODULE=arcano.settings web \
        python -m pytest the_cauldron/tests/test_overlays_visual.py
"""

import os
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    Equipment,
    Exercise,
    MovementPattern,
)
from the_cauldron.services import forge

playwright_sync = pytest.importorskip("playwright.sync_api")

# Playwright's sync API runs an event loop on this thread; Django would otherwise
# refuse the ORM calls the live server fixtures make around it.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

BASELINES = Path(__file__).parent / "visual_baselines"
UPDATE = os.environ.get("FORGE_UPDATE_BASELINES") == "1"
# Share of pixels allowed to differ — absorbs anti-aliasing, not layout changes.
MAX_DIFF_RATIO = 0.005
MIN_TARGET = 44

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 900, "height": 900}
FIT_WIDTHS = [(320, 640), (360, 640), (390, 844)]

# Freeze everything that moves or loads late, so two runs render identically.
STABILISE_CSS = """
*, *::before, *::after {
  animation: none !important; transition: none !important; caret-color: transparent !important;
}
#arcano-stars { display: none !important; }
"""

User = get_user_model()


# ── Data ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def forge_user(transactional_db):
    """A user with a program forged from a Trial — deterministic: no grip-split
    rungs (no pull-up bar), so nothing on the day is picked at random. Bands and
    dumbbells open extra chains, so ⇄ Change has a list to show."""
    call_command("seed_forge")
    user = User.objects.create_user(
        username="phone-user", email="phone@example.com", password="pw12345!"
    )
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=["bodyweight", "bands", "dumbbells"]))
    profile.band_levels = ["light", "medium", "heavy"]
    profile.dumbbell_weights = [5, 10, 15]
    profile.configured_at = timezone.now()  # else the Forge bounces to Equipment
    profile.save()
    trial = AssessmentSession.objects.create(user=user, completed_at=timezone.now())
    owned = forge.owned_equipment_keys(profile)
    for pattern in MovementPattern.objects.all():
        # Grip carries two anchors (#61) — take the one this user would be shown.
        anchor = forge._anchor_for(pattern.pk, owned)
        AssessmentResult.objects.create(
            session=trial, pattern=pattern, tested_exercise=anchor,
            reps_or_seconds=anchor.placement_threshold, placed_exercise=anchor,
        )
    forge.generate_program(user, trial)
    return user


@pytest.fixture
def session_cookie(forge_user):
    client = Client()
    client.force_login(forge_user)
    return client.cookies["sessionid"].value


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
def open_forge(browser, live_server, session_cookie, settings):
    """``open_forge(viewport, section)`` → a page on that Forge section."""
    # The manifest storage would point templates at hashed names from whatever
    # collectstatic last ran — serve the source files being tested instead.
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
    contexts = []

    def _open(viewport, section="today"):
        context = browser.new_context(viewport=viewport, reduced_motion="reduce", has_touch=True)
        contexts.append(context)
        context.add_cookies(
            [{"name": "sessionid", "value": session_cookie, "url": live_server.url}]
        )
        page = context.new_page()
        # Fonts and analytics come from the network; keep the render local.
        page.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith(live_server.url)
            else route.abort(),
        )
        page.goto(f"{live_server.url}/cauldron/forge/{section}/")
        page.add_style_tag(content=STABILISE_CSS)
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#forge-loader", state="hidden")
        return page

    yield _open
    for context in contexts:
        context.close()


# ── Opening each overlay ─────────────────────────────────────────────────────


def _open_ex_modal(page):
    page.wait_for_selector(".ftree-node")
    page.locator('.ftree-node[aria-label^="Push-up —"]').click()
    page.wait_for_selector("#forge-ex-modal:not([hidden])")


def _open_swap_modal(page):
    page.wait_for_selector(".forge-swap-ex-btn:not([disabled])")
    page.locator(".forge-swap-ex-btn").first.click()
    page.wait_for_selector("#forge-swap-modal:not([hidden])")


def _open_voice(page):
    # Earphones mode needs speech synthesis to start for real; its layout is what
    # is under test, so show it as startVoice() would.
    page.evaluate(
        """() => {
          document.getElementById('forge-voice').hidden = false;
          document.getElementById('forge-voice-exercise').textContent = 'Knee Push-up';
          document.getElementById('forge-voice-instruction').textContent =
            'Set 1 of 3 — do 6 reps, then tap Log.';
          document.body.classList.add('forge-overlay-open');
        }"""
    )


def _open_reveal(page):
    # The reveal is driven by an assessment POST; render a long verdict directly
    # so the sizing is exercised without forging a new program.
    page.evaluate(
        """() => {
          const rows = Array.from({length: 6}, (_, i) =>
            `<div class="forge-score-row"><div class="forge-score-head">` +
            `<span class="forge-score-ex">Movement ${i + 1} · 12</span>` +
            `<span class="forge-score-decile">top 30% · decile 7/10</span></div>` +
            `<div class="forge-score-meta">7/10 vs peers your age &amp; sex</div></div>`
          ).join('');
          document.getElementById('forge-reveal-body').innerHTML =
            '<h3 class="forge-reveal-title">The verdict</h3>' + rows;
          const actions = document.getElementById('forge-reveal-actions');
          actions.innerHTML = '<button class="btn-cauldron btn-cauldron--primary">Continue</button>';
          actions.hidden = false;
          document.getElementById('forge-reveal').hidden = false;
          document.body.classList.add('forge-overlay-open');
        }"""
    )


OVERLAYS = {
    "ex-modal": ("exercises", _open_ex_modal, "#forge-ex-modal-close"),
    "swap-modal": ("today", _open_swap_modal, "#forge-swap-close"),
    "voice": ("today", _open_voice, "#forge-voice-exit"),
    "reveal": ("today", _open_reveal, None),
}


# ── Assertions ───────────────────────────────────────────────────────────────


def _assert_matches_baseline(page, name):
    from io import BytesIO

    from PIL import Image, ImageChops

    shot = Image.open(BytesIO(page.screenshot())).convert("RGB")
    baseline_path = BASELINES / f"{name}.png"
    if UPDATE or not baseline_path.exists():
        BASELINES.mkdir(exist_ok=True)
        shot.save(baseline_path)
        pytest.fail(f"Wrote baseline {baseline_path.name} — review it, then re-run.")
    baseline = Image.open(baseline_path).convert("RGB")
    assert shot.size == baseline.size, f"{name}: size {shot.size} != baseline {baseline.size}"
    diff = ImageChops.difference(shot, baseline).convert("L").point(lambda v: 255 if v > 24 else 0)
    changed = sum(diff.histogram()[255:])
    ratio = changed / (shot.size[0] * shot.size[1])
    if ratio > MAX_DIFF_RATIO:
        shot.save(BASELINES / f"{name}.actual.png")
    assert ratio <= MAX_DIFF_RATIO, f"{name}: {ratio:.2%} of pixels differ from the baseline"


def _assert_close_visible(page, selector, viewport):
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} is not rendered"
    assert box["x"] >= 0 and box["y"] >= 0, f"{selector} starts off-screen: {box}"
    assert box["x"] + box["width"] <= viewport["width"], f"{selector} overflows right: {box}"
    assert box["y"] + box["height"] <= viewport["height"], f"{selector} overflows bottom: {box}"


def _assert_hit_area(page, selector):
    """A 44px-wide target around the control's centre lands on it — measured by
    hit-testing, so an enlarged invisible target counts. The controls are round
    and hit-testing honours border-radius, so probe the four edge points of a
    44px circle rather than the corners of a square."""
    box = page.locator(selector).bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    half = MIN_TARGET / 2 - 1
    for dx, dy in [(-half, 0), (half, 0), (0, -half), (0, half)]:
        hit = page.evaluate(
            "([x, y, sel]) => { const el = document.elementFromPoint(x, y);"
            " return !!el && !!el.closest(sel); }",
            [cx + dx, cy + dy, selector],
        )
        assert hit, f"{selector}: ({dx:+.0f}, {dy:+.0f}) from centre misses the control"


@pytest.mark.parametrize("name", list(OVERLAYS))
@pytest.mark.parametrize("label,viewport", [("390x844", PHONE), ("900x900", DESKTOP)])
def test_overlay_renders_and_keeps_its_close_on_screen(open_forge, name, label, viewport):
    section, opener, close = OVERLAYS[name]
    page = open_forge(viewport, section)
    opener(page)
    _assert_matches_baseline(page, f"{name}-{label}")
    # Desktop is the unchanged control — only the pixel diff applies there.
    if close and viewport is PHONE:
        _assert_close_visible(page, close, viewport)
        _assert_hit_area(page, close)


@pytest.mark.parametrize("width,height", FIT_WIDTHS)
@pytest.mark.parametrize("name", list(OVERLAYS))
def test_overlay_fits_small_phones(open_forge, name, width, height):
    viewport = {"width": width, "height": height}
    section, opener, close = OVERLAYS[name]
    page = open_forge(viewport, section)
    opener(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    card = page.locator(f"#forge-{name} .cauldron-card")
    box = card.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    if close:
        _assert_close_visible(page, close, viewport)


def test_swap_close_survives_scrolling_to_the_bottom_of_the_list(open_forge):
    page = open_forge({"width": 320, "height": 640})
    _open_swap_modal(page)
    page.evaluate(
        "() => { const b = document.querySelector('#forge-swap-modal .forge-sheet-body');"
        " b.scrollTop = b.scrollHeight; }"
    )
    _assert_close_visible(page, "#forge-swap-close", {"width": 320, "height": 640})


# ── Dismissal ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["ex-modal", "swap-modal"])
def test_escape_closes_the_dialog(open_forge, name):
    section, opener, _ = OVERLAYS[name]
    page = open_forge(PHONE, section)
    opener(page)
    page.keyboard.press("Escape")
    page.wait_for_selector(f"#forge-{name}", state="hidden")
    assert not page.evaluate("document.body.classList.contains('forge-overlay-open')")


@pytest.mark.parametrize("name", ["ex-modal", "swap-modal"])
def test_backdrop_tap_closes_the_dialog(open_forge, name):
    section, opener, _ = OVERLAYS[name]
    page = open_forge(PHONE, section)
    opener(page)
    page.mouse.click(PHONE["width"] / 2, 8)  # above the sheet
    page.wait_for_selector(f"#forge-{name}", state="hidden")


@pytest.mark.parametrize("name", ["ex-modal", "swap-modal"])
def test_swiping_the_sheet_down_closes_it(open_forge, name):
    section, opener, _ = OVERLAYS[name]
    page = open_forge(PHONE, section)
    opener(page)
    handle = page.locator(f"#forge-{name} .forge-sheet-handle").bounding_box()
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 60, steps=4)
    page.mouse.move(x, y + 200, steps=4)
    page.mouse.up()
    page.wait_for_selector(f"#forge-{name}", state="hidden")


def test_a_short_drag_snaps_the_sheet_back(open_forge):
    page = open_forge(PHONE, "exercises")
    _open_ex_modal(page)
    handle = page.locator("#forge-ex-modal .forge-sheet-handle").bounding_box()
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 20, steps=3)
    page.mouse.up()
    assert page.locator("#forge-ex-modal").is_visible()


def test_the_reveal_cannot_be_dismissed(open_forge):
    page = open_forge({"width": 320, "height": 640})
    _open_reveal(page)
    page.keyboard.press("Escape")
    page.mouse.click(160, 4)
    assert page.locator("#forge-reveal").is_visible()
    assert page.locator("#forge-reveal .forge-sheet-handle").count() == 0
    # Readable and actionable at 320: its button can be scrolled to and is on screen.
    button = page.locator("#forge-reveal-actions button")
    button.scroll_into_view_if_needed()
    _assert_close_visible(page, "#forge-reveal-actions button", {"width": 320, "height": 640})

"""Journal gauges keep their content inside the card (#123) — a Playwright visual test.

The Spanish "near" badge ("fuera de tu meta · dentro de la recomendada") is
long. It must wrap inside a narrow gauge instead of widening it, so the
name, value, track, badge and targets all stay inside the card. Badges outside
the gauges keep their one-line pill.

The page is the real Journal. Only ``/rolling/`` is rewritten, so the gauges
show a fixed "near" saturated fat and a "met" protein. The gauge markup still
comes from nutrition.js.

Run inside the web container, where Chromium is installed:
    docker compose exec web python -m pytest the_cauldron/tests/test_journal_gauges_visual.py
"""

import json
import os

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from the_cauldron.services import legal

from .test_overlays_visual import STABILISE_CSS, _assert_matches_baseline

playwright_sync = pytest.importorskip("playwright.sync_api")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

User = get_user_model()

# Two 260px cards side by side (+ the 0.75rem gap): the width in the ticket.
TWO_NARROW_CARDS = ".nut-gauges { width: calc(520px + 0.75rem) !important; grid-template-columns: repeat(2, 260px) !important; }"


def _gauge_payload(real):
    """Keep the real response's shape and ticks; make saturated fat "near"
    (over my 15 g, inside the recommended 22 g) and protein "met"."""
    count = len(real["ticks"])
    every = lambda v: [v] * count  # noqa: E731

    def judged(statuses, lows, highs):
        return {
            n: every(s) for n, s in statuses.items()
        }, {n: every(v) for n, v in lows.items()}, {n: every(v) for n, v in highs.items()}

    tracked = ["protein_g", "sat_fat_g"]
    for n in tracked:
        real["values"][n] = every({"protein_g": 82, "sat_fat_g": 21}[n])
        real["missing"][n] = every(False)
    sets = {
        "mine": judged({"protein_g": "met", "sat_fat_g": "missed"},
                       {"protein_g": 60, "sat_fat_g": None}, {"protein_g": None, "sat_fat_g": 15}),
        "who": judged({"protein_g": "met", "sat_fat_g": "met"},
                      {"protein_g": 50, "sat_fat_g": None}, {"protein_g": None, "sat_fat_g": 22}),
    }
    for name, (status, low, high) in sets.items():
        s = real["sets"][name]
        s["status"], s["low"], s["high"] = status, low, high
        met = {n: status[n][0] == "met" for n in tracked}
        s["summary"] = [{
            "nutrient": n, "in_range": count if met[n] else 0, "out_of_range": 0 if met[n] else count,
            "uncertain": 0, "not_logged": 0, "judged": count, "pct": 100 if met[n] else 0,
        } for n in tracked]
        s["daily"] = {n: [] for n in tracked}
    real["tracked"] = tracked
    return real


@pytest.fixture
def journal_user(transactional_db):
    user = User.objects.create_user(username="gauge-user", email="gauge@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])  # the Journal needs consent
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
def open_journal(browser, live_server, journal_user, settings):
    """``open_journal(viewport, lang, css="")`` → the Journal with two gauges drawn."""
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
    client = Client()
    client.force_login(journal_user)
    cookie = client.cookies["sessionid"].value
    contexts = []

    def _route(route):
        url = route.request.url
        if not url.startswith(live_server.url):
            return route.abort()  # fonts and analytics: keep the render local
        if "/api/rolling/?" in url:
            response = route.fetch()
            return route.fulfill(response=response, body=json.dumps(_gauge_payload(response.json())))
        return route.continue_()

    def _open(viewport, lang, css=""):
        context = browser.new_context(viewport=viewport, reduced_motion="reduce", locale=lang)
        contexts.append(context)
        context.add_cookies([
            {"name": "sessionid", "value": cookie, "url": live_server.url},
            {"name": "django_language", "value": lang, "url": live_server.url},
        ])
        page = context.new_page()
        page.route("**/*", _route)
        page.goto(f"{live_server.url}/cauldron/journal/")
        page.add_style_tag(content=STABILISE_CSS + css)
        page.wait_for_selector('.nut-gauge[data-nutrient="sat_fat_g"]')
        page.wait_for_load_state("networkidle")
        return page

    yield _open
    for context in contexts:
        context.close()


def _box(page, selector):
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} is not rendered"
    return box


def _line_height(locator):
    """The rendered height of one text line ("normal" line-height has no number)."""
    return locator.evaluate(
        "el => { const r = document.createRange(); r.selectNodeContents(el);"
        " return r.getClientRects()[0].height; }"
    )


def _assert_inside(page, gauge):
    card = _box(page, gauge)
    for part in ("name", "value", "track", "foot", "targets"):
        sel = f"{gauge} .nut-gauge-{part}"
        box = _box(page, sel)
        assert box["x"] >= card["x"] - 0.5, f"{sel} starts left of its card"
        assert box["x"] + box["width"] <= card["x"] + card["width"] + 0.5, f"{sel} spills out of its card"
    badge = page.locator(f"{gauge} .nut-status")
    box = badge.bounding_box()
    assert box["x"] + box["width"] <= card["x"] + card["width"] + 0.5, "the badge spills out of its card"
    # Nothing in the card scrolls sideways either: the content really fits.
    assert page.locator(gauge).evaluate("el => el.scrollWidth <= el.clientWidth")


NEAR = '.nut-gauge[data-nutrient="sat_fat_g"]'
MET = '.nut-gauge[data-nutrient="protein_g"]'


def test_near_badge_in_spanish_stays_inside_a_260px_card(open_journal):
    page = open_journal({"width": 900, "height": 1000}, "es", TWO_NARROW_CARDS)
    assert round(_box(page, NEAR)["width"]) == 260
    _assert_inside(page, NEAR)
    _assert_inside(page, MET)

    # The value is whole: not clipped, and the neighbouring card doesn't cover it.
    value = page.locator(f"{NEAR} .nut-gauge-value")
    assert value.evaluate("el => el.scrollWidth <= el.clientWidth")
    vb = value.bounding_box()
    hit = page.evaluate(
        "([x, y]) => !!document.elementFromPoint(x, y).closest('[data-nutrient=\"sat_fat_g\"]')",
        [vb["x"] + vb["width"] - 2, vb["y"] + vb["height"] / 2],
    )
    assert hit, "the value's right edge is covered by another element"

    # The long badge keeps its full copy and wraps to two lines, no more.
    badge = page.locator(f"{NEAR} .nut-status")
    assert badge.inner_text().strip().lower() == "fuera de tu meta · dentro de la recomendada"
    # Lines measured against the short "met" badge, which is one line tall.
    one_line = page.locator(f"{MET} .nut-status").bounding_box()["height"]
    lines = (badge.bounding_box()["height"] - one_line) / _line_height(badge) + 1
    assert round(lines) == 2, f"the near badge takes {lines:.1f} line(s)"

    # A short badge is the same one-line pill it always was.
    assert one_line < 2 * _line_height(page.locator(f"{MET} .nut-status"))

    _assert_matches_baseline(page.locator("#nut-gauges"), "journal-gauges-near-es-260")


@pytest.mark.parametrize("lang", ["es", "en"])
def test_no_horizontal_scroll_at_320(open_journal, lang):
    page = open_journal({"width": 320, "height": 800}, lang)
    assert page.evaluate("document.documentElement.scrollWidth") <= 320
    _assert_inside(page, NEAR)
    _assert_inside(page, MET)


def test_badges_outside_the_gauges_keep_one_line(open_journal):
    page = open_journal({"width": 900, "height": 1000}, "es")
    white_space = page.evaluate(
        """() => {
          const el = document.createElement('span');
          el.className = 'nut-status nut-status--near';
          document.querySelector('.nut').appendChild(el);
          return getComputedStyle(el).whiteSpace;
        }"""
    )
    assert white_space == "nowrap"


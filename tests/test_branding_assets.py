"""Cauldron branding on the Forge pages: favicon, theme colour, page loader.

Guards the shared `_favicons.html` partial and the single-source
`cauldron-loader.js` — both are easy to regress by adding a template that
forgets to override Arcano's blocks.
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse

User = get_user_model()

CAULDRON_ICON_MARK = "stroke='%23B23A1F'"
CAULDRON_THEME_COLOR = '<meta name="theme-color" content="#B23A1F">'
ARCANO_ICON_MARK = "arcano/favicon"
LOADER_SRC = "the_cauldron/js/cauldron-loader.js"


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Render without the hashed-manifest storage (see test_forge_urls)."""
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="smith", password="pw12345!")


@pytest.fixture
def web(user):
    c = Client()
    c.force_login(user)
    return c


def _html(response):
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.parametrize("url_name", ["the_cauldron:landing", "the_cauldron:login"])
def test_public_pages_use_cauldron_favicon(client, url_name):
    html = _html(client.get(reverse(url_name)))
    assert CAULDRON_ICON_MARK in html
    assert CAULDRON_THEME_COLOR in html
    assert ARCANO_ICON_MARK not in html


def test_forge_uses_cauldron_favicon(web):
    html = _html(web.get(reverse("the_cauldron:forge"), follow=True))
    assert CAULDRON_ICON_MARK in html
    assert CAULDRON_THEME_COLOR in html
    assert ARCANO_ICON_MARK not in html


@pytest.mark.parametrize("url_name", ["the_cauldron:landing", "the_cauldron:login"])
def test_pages_use_shared_cauldron_loader(client, url_name):
    """The loader ships as one file — never re-inlined into a template."""
    html = _html(client.get(reverse(url_name)))
    assert LOADER_SRC in html
    # Catch a re-inlined copy regardless of how it is spaced or formatted.
    inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert not [s for s in inline_scripts if "buildCauldronLoader" in s]


def test_forge_does_not_emit_arcano_nav_loader(web):
    """The Forge overrides Arcano's nav wiring (it uses the Cauldron loader via
    app_scripts instead), and its own blocking #forge-loader overlay survives."""
    html = _html(web.get(reverse("the_cauldron:forge"), follow=True))
    # base.html's marker comment — present only if Arcano's block rendered.
    assert "Show ArcanoLoader on full-page navigation" not in html
    assert 'id="forge-loader"' in html


def _forge_css():
    path = finders.find("the_cauldron/css/forge.css")
    assert path, "forge.css not found by the staticfiles finders"
    return Path(path).read_text(encoding="utf-8")


def test_login_submit_button_is_full_width_and_centred():
    """The submit button fills the card and centres its label."""
    rules = re.findall(
        r"^\.forge-form\s+\.btn-cauldron\s*\{([^}]*)\}", _forge_css(), re.M
    )
    assert rules, "no `.forge-form .btn-cauldron` rule in forge.css"
    # A later rule wins, so assert on the last one rather than the first.
    declarations = rules[-1]
    assert "width: 100%" in declarations
    assert "justify-content: center" in declarations


def test_login_field_labels_stay_left_aligned():
    """The other half of the requirement: centring the button must not be
    achieved by centring the whole form."""
    rules = re.findall(r"^\.forge-form\s*\{([^}]*)\}", _forge_css(), re.M)
    assert rules, "no `.forge-form` rule in forge.css"
    assert "text-align: left" in rules[-1]

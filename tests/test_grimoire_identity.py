"""Grimoire identity (#66): the design assets every Grimoire ticket builds on.

Guards the acceptance rules that are easy to regress silently: Grimoire
CSS/JS derive every color from arcano/Cauldron tokens (no literals), the
loader honours prefers-reduced-motion, and the mark partial renders.
"""

import re
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string

TOKENS_SRC = "the_cauldron/css/grimoire-tokens.css"
COMPONENTS_SRC = "the_cauldron/css/grimoire.css"
LOADER_SRC = "the_cauldron/js/grimoire-loader.js"

# Hex, any color function, or a CSS named color used as a value. `transparent`,
# `currentColor` and `inherit` are keywords, not literals, and stay allowed.
COLOR_FUNCTIONS = r"(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\("
NAMED_COLORS = (
    "white|black|red|green|blue|gold|yellow|orange|purple|crimson|gray|grey|silver|"
    "maroon|navy|teal|olive|lime|aqua|fuchsia|brown|pink|beige|ivory|tan|wheat"
)
LITERAL_COLOR = re.compile(
    rf"#[0-9a-fA-F]{{3,8}}\b|\b{COLOR_FUNCTIONS}|(?<![\w-])(?:{NAMED_COLORS})(?![\w-])"
)


def _read(src):
    path = finders.find(src)
    assert path, f"{src} not found by staticfiles"
    return Path(path).read_text(encoding="utf-8")


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


@pytest.mark.parametrize("src", [TOKENS_SRC, COMPONENTS_SRC, LOADER_SRC])
def test_no_literal_colors(src):
    code = _strip_comments(_read(src))
    assert not LITERAL_COLOR.findall(code), f"{src} hardcodes a color — use a token"


def test_grimoire_tokens_only_derive_from_arcano_or_cauldron():
    code = _strip_comments(_read(TOKENS_SRC))
    referenced = set(re.findall(r"var\(--([a-z]+)-", code))
    assert referenced <= {"arcano", "cauldron", "grimoire"}
    # Never redefine a global or Cauldron token here.
    assert not re.search(r"--(?:arcano|cauldron)-[\w-]+\s*:", code)


def test_derived_tokens_are_redeclared_on_parchment():
    """A var() reference resolves where it is declared — derived tokens must
    sit in the shared `:root, .grimoire-parchment` block or parchment keeps
    the dark values (invisible stars on cream)."""
    code = _strip_comments(_read(TOKENS_SRC))
    shared = re.search(r":root,\s*\.grimoire-parchment\s*\{(.*?)\}", code, re.S)
    assert shared, "missing the shared derived-token block"
    for token in ("--grimoire-star-off", "--grimoire-nutrient-track", "--grimoire-border"):
        assert token in shared.group(1)


def test_literal_color_pattern_catches_common_forms():
    for bad in ("border: 1px solid white", "color-mix(in srgb, black 20%, transparent)",
                "color: crimson", "oklch(70% 0.1 80)", "#B23A1F"):
        assert LITERAL_COLOR.search(bad), bad
    assert not LITERAL_COLOR.search("color-mix(in srgb, var(--arcano-gold) 8%, transparent)")


def test_components_honour_reduced_motion():
    css = _read(COMPONENTS_SRC)
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_loader_honours_reduced_motion():
    js = _read(LOADER_SRC)
    block = js.split("prefers-reduced-motion: reduce", 1)
    assert len(block) == 2, "loader has no reduced-motion rule"
    assert "animation:none" in block[1].replace(" ", "")
    assert ".grim-loader-leaf{display:none;}" in block[1].replace(" ", "")


@pytest.mark.parametrize("mark_class", [None, "grimoire-empty-mark"])
def test_mark_partial_renders(mark_class):
    ctx = {"mark_class": mark_class} if mark_class else {}
    html = render_to_string("the_cauldron/grimoire/_mark.html", ctx)
    assert html.count("<svg") == 1
    assert f'class="{mark_class or "grimoire-nav-mark"}"' in html
    assert 'stroke="currentColor"' in html and 'aria-hidden="true"' in html

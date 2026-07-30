"""Per-section Forge URLs: routing, state-based redirects, deep links."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from the_cauldron.models import Program, UserEquipmentProfile
from the_cauldron.services import forge

User = get_user_model()


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Render the Forge without the hashed-manifest storage.

    Production serves static through WhiteNoise's manifest storage, which needs
    a `collectstatic` manifest that tests don't build — every `{% static %}` in
    the template would raise instead of rendering.
    """
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="wanderer", password="pw12345!")


@pytest.fixture
def web(user):
    c = Client()
    c.force_login(user)
    return c


def _configure_equipment(user):
    profile = forge.get_or_create_equipment_profile(user)
    profile.configured_at = timezone.now()
    profile.save(update_fields=["configured_at"])
    return profile


def _give_program(user):
    return Program.objects.create(user=user, is_active=True)


# ── Routing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "slug,tab",
    [
        ("equipment", "equipment"),
        ("the-trial", "trial"),
        ("today", "today"),
        ("exercises", "exercises"),
        ("progress", "progress"),
        ("about", "about"),
    ],
)
def test_each_section_has_its_own_url_and_opens_active(web, user, slug, tab):
    _configure_equipment(user)
    _give_program(user)
    resp = web.get(f"/cauldron/forge/{slug}/")
    assert resp.status_code == 200
    assert resp.context["initial_tab"] == tab
    # The section is rendered active server-side, not just switched to by JS.
    html = resp.content.decode()
    assert f'class="forge-panel is-active" data-panel="{tab}"' in html


def test_unknown_section_404s(web, user):
    _configure_equipment(user)
    _give_program(user)
    assert web.get("/cauldron/forge/sorcery/").status_code == 404


def test_section_url_reverses(web):
    assert reverse("the_cauldron:forge-section", kwargs={"section": "the-trial"}) == (
        "/cauldron/forge/the-trial/"
    )
    assert reverse("the_cauldron:forge") == "/cauldron/forge/"


# ── Bare /forge/ redirects by user state ─────────────────────────────────────


def test_bare_forge_sends_unconfigured_user_to_equipment(web):
    resp = web.get("/cauldron/forge/")
    assert resp.status_code == 302
    assert resp.url == "/cauldron/forge/equipment/"


def test_bare_forge_sends_configured_user_without_program_to_trial(web, user):
    _configure_equipment(user)
    resp = web.get("/cauldron/forge/")
    assert resp.url == "/cauldron/forge/the-trial/"


def test_bare_forge_sends_ready_user_to_today(web, user):
    _configure_equipment(user)
    _give_program(user)
    resp = web.get("/cauldron/forge/")
    assert resp.url == "/cauldron/forge/today/"


def test_auto_created_profile_is_not_treated_as_configured(web, user):
    # Merely loading the Forge creates the profile row via the API; that must
    # not count as having chosen equipment.
    forge.get_or_create_equipment_profile(user)
    resp = web.get("/cauldron/forge/")
    assert resp.url == "/cauldron/forge/equipment/"


# ── Unready deep links ───────────────────────────────────────────────────────


def test_deep_link_to_trial_without_equipment_redirects(web):
    resp = web.get("/cauldron/forge/the-trial/")
    assert resp.status_code == 302
    assert resp.url == "/cauldron/forge/equipment/"


def test_deep_link_to_today_without_program_redirects_to_trial(web, user):
    _configure_equipment(user)
    resp = web.get("/cauldron/forge/today/")
    assert resp.url == "/cauldron/forge/the-trial/"


def test_deep_link_to_today_without_equipment_redirects_to_equipment(web):
    resp = web.get("/cauldron/forge/today/")
    assert resp.url == "/cauldron/forge/equipment/"


@pytest.mark.parametrize("slug", ["equipment", "exercises", "progress", "about"])
def test_ungated_sections_open_for_a_brand_new_user(web, slug):
    assert web.get(f"/cauldron/forge/{slug}/").status_code == 200


# ── Auth ─────────────────────────────────────────────────────────────────────


def test_logged_out_deep_link_round_trips_to_the_requested_section(db, user):
    anon = Client()
    resp = anon.get("/cauldron/forge/progress/")
    assert resp.status_code == 302
    assert resp.url == "/cauldron/login/?next=/cauldron/forge/progress/"

    logged_in = anon.post(
        "/cauldron/login/?next=/cauldron/forge/progress/",
        {"username": "wanderer", "password": "pw12345!"},
    )
    assert logged_in.status_code == 302
    assert logged_in.url == "/cauldron/forge/progress/"


# ── API root wiring (the FORGE_API blocker) ──────────────────────────────────


def test_forge_api_root_resolves_from_every_section_url(web, user):
    _configure_equipment(user)
    _give_program(user)
    for slug in ("equipment", "the-trial", "today", "exercises", "progress", "about"):
        html = web.get(f"/cauldron/forge/{slug}/").content.decode()
        assert 'window.FORGE_API = "/cauldron/api/";' in html
        assert 'window.FORGE_ROOT = "/cauldron/forge/";' in html


# ── Equipment save marks the profile configured ──────────────────────────────


def test_saving_equipment_sets_configured_at(web, user, db):
    from django.core.management import call_command

    call_command("seed_forge")
    assert forge.get_or_create_equipment_profile(user).configured_at is None
    resp = web.put(
        "/cauldron/api/equipment/",
        data='{"equipment": ["bodyweight"]}',
        content_type="application/json",
    )
    assert resp.status_code == 200
    profile = UserEquipmentProfile.objects.get(user=user)
    assert profile.configured_at is not None

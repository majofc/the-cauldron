"""#87 — The Cauldron in Spanish: landing, login and the Forge.

Covers the language switcher's memory (cookie + ``CauldronProfile.language``),
the indexable Spanish public URLs, the translated templates and JS strings, the
catalog's ``_es`` fields (``name`` stays the English key), the translated day
name and API messages, and norms still keyed on English names.
"""

import json
import re
from datetime import datetime, timezone as dt_timezone

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import translation
from rest_framework.test import APIClient

from the_cauldron import forge_i18n
from the_cauldron.models import Exercise, MovementPattern, Program, ProgramDay
from the_cauldron.serializers import ExerciseSerializer, ProgramSerializer
from the_cauldron.services import forge, legal, norms
from the_cauldron.services.nutrition import get_profile

User = get_user_model()
COOKIE = settings.LANGUAGE_COOKIE_NAME


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Render pages without the hashed-manifest storage (no collectstatic in tests)."""
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


@pytest.fixture
def seeded(db):
    call_command("seed_forge", verbosity=0)


@pytest.fixture
def user(db):
    # Joined before open sign-up (#72): signs in without email verification.
    user = User.objects.create_user(
        username="bruja", password="pw12345!", date_joined=datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
    )
    legal.accept(user, legal.GATED)
    return user


def _forge_page(client):
    """Load a Forge section past the readiness redirects."""
    return client.get("/cauldron/forge/about/")


def _json_script(html, element_id):
    m = re.search(rf'<script id="{element_id}" type="application/json">(.*?)</script>', html, re.S)
    assert m, f"#{element_id} missing"
    return json.loads(m.group(1))


# ── Public pages ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPublicSpanishUrls:
    @pytest.mark.parametrize(
        "path,en_path",
        [("/cauldron/es/", "/cauldron/"), ("/cauldron/es/terms/", "/cauldron/terms/"),
         ("/cauldron/es/privacy/", "/cauldron/privacy/")],
    )
    def test_es_routes_render_spanish_with_alternates(self, path, en_path):
        client = Client()
        client.cookies[COOKIE] = "en"  # the route wins over the cookie
        html = client.get(path).content.decode()
        assert '<html lang="es">' in html
        assert f'<link rel="canonical" href="http://testserver{path}">' in html
        assert f'hreflang="en" href="http://testserver{en_path}"' in html
        assert f'hreflang="es" href="http://testserver{path}"' in html
        assert f'hreflang="x-default" href="http://testserver{en_path}"' in html
        # The switcher's EN button leads to the English page, not back here.
        assert f'name="next" type="hidden" value="{en_path}"' in html

    def test_landing_es_translates_title_meta_and_copy(self):
        html = Client().get("/cauldron/es/").content.decode()
        assert "<title>El Caldero — Recetas y la alquimia de la cocina</title>" in html
        assert 'content="Guarda tus recetas secretas.' in html
        assert "Templa el cuerpo como la hoja" in html
        assert "Temper the body like the blade" not in html
        tree = _json_script(html, "fshow-tree-data")
        assert tree["patterns"][0]["name"] == "Empuje horizontal"
        assert tree["strings"]["evolve"] == "Evolucionar"

    def test_english_landing_is_unchanged_and_canonical_to_itself(self):
        html = Client().get("/cauldron/").content.decode()
        assert '<html lang="en">' in html
        assert '<link rel="canonical" href="http://testserver/cauldron/">' in html
        assert "Temper the body like the blade" in html
        assert 'name="next" type="hidden" value="/cauldron/es/"' in html

    def test_login_page_in_spanish(self):
        client = Client()
        client.cookies[COOKIE] = "es"
        html = client.get("/cauldron/login/").content.decode()
        assert "Entra a El Caldero" in html
        assert "Enciende el fuego" in html


# ── The Forge page ───────────────────────────────────────────────────────────


class TestForgePage:
    def test_forge_renders_spanish_strings_and_catalog(self, seeded, user):
        client = Client()
        client.force_login(user)
        client.cookies[COOKIE] = "es"
        html = _forge_page(client).content.decode()
        assert "<title>La Forja — Calistenia adaptativa</title>" in html
        assert "¿Qué tienes?" in html
        assert "Cómo funciona la Forja — y por qué" in html
        strings = _json_script(html, "forge-i18n")
        assert strings["unlock_title"] == "¡Nuevo poder desbloqueado!"
        assert strings["n_reps_one"] == "%(n)s rep" and strings["n_reps_other"] == "%(n)s reps"
        catalog = _json_script(html, "forge-catalog-i18n")
        # Keyed by the English name; Spanish display text.
        assert catalog["exercises"]["Push-up"]["name"] == "Lagartija"
        assert catalog["patterns"]["vertical_pull"] == "Jalón vertical"
        assert catalog["muscles"]["glutes"] == "Glúteos"
        assert catalog["equipment"]["kettlebell"] == "Pesa rusa"

    def test_forge_in_english_is_unchanged(self, seeded, user):
        client = Client()
        client.force_login(user)
        html = _forge_page(client).content.decode()
        assert "What do you have?" in html
        catalog = _json_script(html, "forge-catalog-i18n")
        assert catalog["exercises"]["Push-up"]["name"] == "Push-up"

    def test_every_js_plural_ships_both_forms(self):
        with translation.override("es"):
            strings = forge_i18n.forge_strings()
        for key in [k[:-4] for k in strings if k.endswith("_one")]:
            assert f"{key}_other" in strings
        assert strings["n_sets_other"] == "%(n)s series"


# ── Catalog data ─────────────────────────────────────────────────────────────


class TestCatalogSpanish:
    def test_seed_fills_spanish_for_every_rung(self, seeded):
        assert not Exercise.objects.filter(name_es="").exists()
        assert not Exercise.objects.filter(cues_es="").exists()
        assert not MovementPattern.objects.filter(name_es="").exists()

    def test_api_returns_name_es_and_display_name_with_fallback(self, seeded, user):
        api = APIClient()
        api.force_authenticate(user)
        # One rung with no Spanish: falls back to English per field.
        Exercise.objects.filter(name="Plank").update(name_es="")
        resp = api.get("/cauldron/api/exercises/", HTTP_ACCEPT_LANGUAGE="es")
        rows = {r["name"]: r for r in resp.json()}
        assert rows["Push-up"]["name_es"] == "Lagartija"
        assert rows["Push-up"]["display_name"] == "Lagartija"
        assert rows["Push-up"]["display_cues"] == "Codos a ~45°; rango completo."
        assert rows["Plank"]["display_name"] == "Plank"  # fallback
        assert rows["Plank"]["display_cues"].startswith("Glúteos")  # cues_es still set

    def test_english_display_name_is_the_english_name(self, seeded):
        ex = Exercise.objects.get(name="Push-up")
        with translation.override("en"):
            data = ExerciseSerializer(ex).data
        assert data["name"] == data["display_name"] == "Push-up"

    def test_catalog_view_carries_display_names(self, seeded, user):
        api = APIClient()
        api.force_authenticate(user)
        groups = api.get("/cauldron/api/catalog/", HTTP_ACCEPT_LANGUAGE="es").json()["groups"]
        bw = next(g for g in groups if g["equipment_key"] == "bodyweight")
        assert bw["equipment_display_name"] == "Peso corporal"
        push = next(e for e in bw["exercises"] if e["name"] == "Push-up")
        assert push["display_name"] == "Lagartija"

    def test_norms_still_key_on_english_names_in_spanish(self, seeded):
        with translation.override("es"):
            score = norms.score("Push-up", 20, "male", 30)
        assert score.has_data
        assert score.label == "Lagartijas"


# ── Server strings ───────────────────────────────────────────────────────────


class TestServerStrings:
    def test_day_name_translates_without_a_data_migration(self, seeded, user):
        program = Program.objects.create(user=user, is_active=True)
        ProgramDay.objects.create(program=program, day_index=0, name=forge.THE_DAY_NAME)
        assert ProgramDay.objects.get(program=program).name == "Today's Forge"  # stored English
        with translation.override("es"):
            assert ProgramSerializer(program).data["days"][0]["name"] == "La Forja de hoy"
        with translation.override("en"):
            assert ProgramSerializer(program).data["days"][0]["name"] == "Today's Forge"

    def test_unfinished_gate_detail_translated_code_unchanged(self, user, monkeypatch):
        monkeypatch.setattr(forge, "active_day", lambda u: object())
        monkeypatch.setattr(forge, "has_unfinished_session", lambda u: True)
        api = APIClient()
        api.force_authenticate(user)
        body = api.get("/cauldron/api/today/", HTTP_ACCEPT_LANGUAGE="es").json()
        assert body["code"] == "unfinished_session"
        assert body["detail"] == "Primero termina o borra tu última sesión sin terminar."

    def test_validation_error_translated(self, user):
        api = APIClient()
        api.force_authenticate(user)
        resp = api.put("/cauldron/api/equipment/", {"bar_weight": -1}, format="json", HTTP_ACCEPT_LANGUAGE="es")
        assert resp.status_code == 400
        assert resp.json()["bar_weight"] == ["Peso de la barra: no puede ser negativo."]


# ── Language preference ──────────────────────────────────────────────────────


class TestLanguagePreference:
    def test_switcher_choice_is_saved_to_the_profile(self, seeded, user):
        client = Client()
        client.force_login(user)
        client.post("/i18n/set_language/", {"language": "es", "next": "/cauldron/forge/about/"})
        assert client.cookies[COOKIE].value == "es"
        _forge_page(client)
        assert get_profile(user).language == "es"

    def test_fresh_login_on_another_browser_applies_the_profile_language(self, seeded, user):
        profile = get_profile(user)
        profile.language = "es"
        profile.save()
        other = Client()  # a fresh browser: no language cookie
        other.post("/cauldron/login/", {"login": "bruja", "password": "pw12345!"})  # allauth form (#72)
        resp = _forge_page(other)
        assert "¿Qué tienes?" in resp.content.decode()
        assert resp.cookies[COOKIE].value == "es"
        assert get_profile(user).language == "es"

    def test_a_cookie_choice_in_this_browser_beats_the_profile(self, seeded, user):
        profile = get_profile(user)
        profile.language = "es"
        profile.save()
        client = Client()
        client.force_login(user)
        client.cookies[COOKIE] = "en"  # just switched to English here
        assert "What do you have?" in _forge_page(client).content.decode()
        assert get_profile(user).language == "en"

    def test_public_landing_does_not_create_a_profile(self, db):
        from the_cauldron.models import CauldronProfile

        outsider = User.objects.create_user(username="visitor", password="pw12345!")
        client = Client()
        client.force_login(outsider)
        assert client.get("/cauldron/").status_code == 200
        assert not CauldronProfile.objects.filter(user=outsider).exists()

    def test_no_choice_yet_leaves_the_profile_blank(self, seeded, user):
        client = Client()
        client.force_login(user)
        resp = _forge_page(client)
        assert resp.status_code == 200
        assert get_profile(user).language == ""
        assert COOKIE not in resp.cookies


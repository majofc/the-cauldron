"""#102 — the journal is The Cauldron's home for signed-in users.

/cauldron/ (and /cauldron/es/) send a signed-in user to the journal; anonymous
visitors keep the landing. Login lands on the journal by default, and the nav
calls the journal "The Cauldron" — "Journal" survives only in the URL.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from the_cauldron.services import legal

User = get_user_model()
JOURNAL = "/cauldron/journal/"


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Render pages without the hashed-manifest storage (no collectstatic in tests)."""
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


@pytest.fixture
def member(db):
    """Past the terms gate, with the sensitive-data consent given."""
    user = User.objects.create_user(username="caldera", password="pw12345!")
    legal.accept(user, legal.GATED + (legal.SENSITIVE_CONSENT,))
    return user


def _client(user):
    c = Client()
    c.force_login(user)
    return c


class TestEntry:
    @pytest.mark.parametrize("path", ["/cauldron/", "/cauldron/?utm_source=x", "/cauldron/es/"])
    def test_signed_in_goes_to_the_journal(self, member, path):
        resp = _client(member).get(path)
        assert resp.status_code == 302
        assert resp["Location"] == JOURNAL  # query string dropped

    def test_anonymous_keeps_the_landing(self, db):
        resp = Client().get("/cauldron/")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Temper the body like the blade" in html
        assert f'href="/cauldron/login/?next={JOURNAL}"' in html
        assert "Enter the Forge" not in html

    def test_pending_terms_end_on_the_accept_page(self, db):
        user = User.objects.create_user(username="newcomer", password="pw12345!")
        resp = _client(user).get("/cauldron/", follow=True)
        assert resp.redirect_chain[-1][0] == "/cauldron/accept/?next=%2Fcauldron%2Fjournal%2F"

    def test_no_sensitive_consent_ends_on_the_journal_gate(self, db):
        user = User.objects.create_user(username="cautious", password="pw12345!")
        legal.accept(user, legal.GATED)
        resp = _client(user).get("/cauldron/", follow=True)
        assert resp.redirect_chain[-1][0] == JOURNAL
        assert resp.context["page"] == "journal"
        assert resp.context["has_consent"] is False  # the consent gate shows

    def test_login_without_next_lands_on_the_journal(self, member):
        resp = Client().post("/cauldron/login/", {"username": "caldera", "password": "pw12345!"})
        assert resp.status_code == 302
        assert resp["Location"] == JOURNAL

    def test_login_with_next_still_honours_it(self, member):
        resp = Client().post(
            "/cauldron/login/", {"username": "caldera", "password": "pw12345!", "next": "/cauldron/grimoire/"}
        )
        assert resp["Location"] == "/cauldron/grimoire/"

    def test_login_with_an_off_site_next_falls_back_to_the_journal(self, member):
        resp = Client().post(
            "/cauldron/login/", {"username": "caldera", "password": "pw12345!", "next": "https://evil.example/"}
        )
        assert resp["Location"] == JOURNAL


class TestNaming:
    @pytest.mark.parametrize("path", [
        JOURNAL, "/cauldron/profile/", "/cauldron/grimoire/", "/cauldron/forge/about/", "/cauldron/mcp/",
    ])
    def test_app_pages_point_home_at_the_journal_and_drop_the_journal_item(self, member, path):
        html = _client(member).get(path, HTTP_ACCEPT="text/html").content.decode()
        assert ">The Journal</a>" not in html
        assert f'href="{JOURNAL}" class="cauldron-nav-brand' in html
        assert f'<a href="{JOURNAL}" class="nav-link cauldron-nav-tight' in html

    def test_public_mcp_page_sends_visitors_to_the_landing(self, db):
        html = Client().get("/cauldron/mcp/", HTTP_ACCEPT="text/html").content.decode()
        assert 'href="/cauldron/" class="cauldron-nav-brand' in html
        assert '<a href="/cauldron/" class="nav-link cauldron-nav-tight">The Cauldron</a>' in html
        assert f'href="{JOURNAL}"' not in html

    def test_accept_page_without_next_falls_back_to_the_journal(self, member):
        resp = _client(member).get("/cauldron/accept/")
        assert resp.status_code == 302 and resp["Location"] == JOURNAL

    @pytest.mark.parametrize("path", ["/cauldron/", "/cauldron/terms/", "/cauldron/login/", "/cauldron/mcp/"])
    def test_public_pages_have_no_journal_item(self, db, path):
        # Nav items only — the versioned legal text may still name the Journal.
        assert ">The Journal</a>" not in Client().get(path, HTTP_ACCEPT="text/html").content.decode()

    def test_journal_is_titled_the_cauldron_and_active(self, member):
        html = _client(member).get(JOURNAL).content.decode()
        assert "<title>The Cauldron</title>" in html
        assert '<h1 class="grim-title">The Cauldron</h1>' in html
        assert f'href="{JOURNAL}" class="nav-link cauldron-nav-tight active"' in html

    def test_journal_in_spanish(self, member):
        c = _client(member)
        c.cookies["django_language"] = "es"
        html = c.get(JOURNAL).content.decode()
        assert "<title>El Caldero</title>" in html
        assert '<h1 class="grim-title">El Caldero</h1>' in html

    @pytest.mark.parametrize("lang,button,help_end", [
        ("en", "Open The Cauldron", "live in The Cauldron."),
        ("es", "Abrir El Caldero", "están en El Caldero."),
    ])
    def test_profile_points_to_the_cauldron(self, member, lang, button, help_end):
        c = _client(member)
        c.cookies["django_language"] = lang
        html = c.get("/cauldron/profile/").content.decode()
        assert button in html and help_end in html

    def test_journal_route_name_is_unchanged(self):
        assert reverse("the_cauldron:journal") == JOURNAL

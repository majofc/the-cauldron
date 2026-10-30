"""The Cauldron is for adults only (#115): the birth year is required at sign-up
(or confirmed once, for older and Google accounts), and under 18 nothing opens —
pages, API or MCP tools. Runs with the real gate (``age_gate`` marker)."""

import secrets
from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model
from rest_framework.test import APIClient

from the_cauldron.models import CauldronProfile
from the_cauldron.services import age_gate, legal

pytestmark = [pytest.mark.age_gate, pytest.mark.django_db]

User = get_user_model()
THIS_YEAR = timezone.localdate().year
ADULT_YEAR = THIS_YEAR - 30
MINOR_YEAR = THIS_YEAR - 15


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    cache.clear()
    yield
    cache.clear()


def _user(name, birth_year=None):
    user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
    if birth_year:
        CauldronProfile.objects.update_or_create(user=user, defaults={"birth_year": birth_year})
    return user


def _browser(user):
    c = Client()
    c.force_login(user)
    return c


def _api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ── The rule ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("born,adult", [(2007, True), (2008, False), (1950, True), (2015, False)])
def test_adult_is_judged_by_the_youngest_age_the_year_allows(born, adult):
    # Born in 2008, someone may still be 17 until their birthday in 2026.
    assert age_gate.is_adult(born, today=date(2026, 10, 29)) is adult


def test_blocked_reasons():
    assert age_gate.blocked(_user("none")) == age_gate.AGE_REQUIRED
    assert age_gate.blocked(_user("kid", MINOR_YEAR)) == age_gate.UNDERAGE
    assert age_gate.blocked(_user("adult", ADULT_YEAR)) is None


# ── Sign-up ──────────────────────────────────────────────────────────────────


def _signup(**overrides):
    data = {
        "email": "ada@example.com", "first_name": "Ada", "password1": "s3cret-Cauldron!",
        "password2": "s3cret-Cauldron!", "accept_terms": "on", "website": "",
    }
    data.update(overrides)
    return Client().post("/cauldron/register/", data)


def test_sign_up_without_birth_year_is_refused():
    resp = _signup()
    assert resp.status_code == 200
    assert not User.objects.filter(email="ada@example.com").exists()
    assert "birth year is required" in resp.content.decode().lower()


def test_sign_up_under_18_creates_no_account():
    resp = _signup(birth_year=str(MINOR_YEAR))
    assert resp.status_code == 200
    assert not User.objects.filter(email="ada@example.com").exists()
    assert "18 and older" in resp.content.decode()


def test_sign_up_stores_the_birth_year():
    resp = _signup(birth_year=str(ADULT_YEAR))
    assert resp.status_code == 302
    user = User.objects.get(email="ada@example.com")
    assert user.cauldron_profile.birth_year == ADULT_YEAR


def test_future_birth_year_is_refused():
    _signup(birth_year=str(THIS_YEAR + 1))
    assert not User.objects.filter(email="ada@example.com").exists()


# ── Pages ────────────────────────────────────────────────────────────────────


def test_no_birth_year_goes_to_confirm_then_back():
    user = _user("legacy")
    c = _browser(user)
    resp = c.get("/cauldron/grimoire/")
    assert resp.status_code == 302
    assert resp["Location"] == "/cauldron/age/?next=%2Fcauldron%2Fgrimoire%2F"

    page = c.get(resp["Location"])
    assert page.status_code == 200
    assert 'id="nut-age-form"' in page.content.decode()

    assert c.post("/cauldron/api/age/", {"birth_year": ADULT_YEAR}, content_type="application/json").json() == {
        "code": None
    }
    # Confirmed: the confirm page moves on to ?next=, and the app opens.
    assert c.get("/cauldron/age/?next=/cauldron/grimoire/")["Location"] == "/cauldron/grimoire/"
    assert c.get("/cauldron/grimoire/").status_code == 200


def test_terms_come_before_the_birth_year():
    user = User.objects.create_user(username="fresh", password="pw12345!")
    resp = _browser(user).get("/cauldron/journal/")
    assert resp["Location"].startswith("/cauldron/accept/")


@pytest.mark.parametrize("path", ["/cauldron/journal/", "/cauldron/forge/", "/cauldron/grimoire/",
                                  "/cauldron/profile/", "/cauldron/age/"])
def test_under_18_sees_no_access_anywhere(path):
    resp = _browser(_user("kid", MINOR_YEAR)).get(path)
    assert resp.status_code == 403
    assert "Access not available" in resp.content.decode()


def test_adult_pages_open():
    assert _browser(_user("adult", ADULT_YEAR)).get("/cauldron/journal/").status_code == 200


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/cauldron/api/today/", "/cauldron/api/grimoire/spells/",
                                  "/cauldron/api/goals/", "/cauldron/api/body/", "/cauldron/api/equipment/"])
def test_api_is_closed_until_the_birth_year_is_given(path):
    resp = _api(_user("legacy")).get(path)
    assert resp.status_code == 403
    assert resp.json()["code"] == "age_required"
    assert resp.json()["accept_url"] == "/cauldron/age/"


@pytest.mark.parametrize("path", ["/cauldron/api/today/", "/cauldron/api/grimoire/spells/",
                                  "/cauldron/api/goals/", "/cauldron/api/rolling/"])
def test_api_is_closed_under_18(path):
    resp = _api(_user("kid", MINOR_YEAR)).get(path)
    assert resp.status_code == 403
    assert resp.json()["code"] == "underage"


def test_under_18_can_still_withdraw_consent():
    resp = _api(_user("kid", MINOR_YEAR)).post("/cauldron/api/legal/withdraw/", {"delete_data": True}, format="json")
    assert resp.status_code == 200


def test_age_endpoint_waits_for_the_terms():
    user = User.objects.create_user(username="fresh", password="pw12345!")
    resp = _api(user).post("/cauldron/api/age/", {"birth_year": ADULT_YEAR}, format="json")
    assert resp.status_code == 403
    assert resp.json()["code"] == "consent_required"
    assert not CauldronProfile.objects.filter(user=user, birth_year__isnull=False).exists()


def test_age_endpoint_sets_the_year_once():
    user = _user("legacy")
    c = _api(user)
    assert c.post("/cauldron/api/age/", {"birth_year": None}, format="json").status_code == 400
    assert c.post("/cauldron/api/age/", {"birth_year": 1850}, format="json").status_code == 400
    assert c.post("/cauldron/api/age/", {"birth_year": MINOR_YEAR}, format="json").json() == {"code": "underage"}
    # Set: it can't be changed here anymore, to get past the gate or otherwise.
    resp = c.post("/cauldron/api/age/", {"birth_year": ADULT_YEAR}, format="json")
    assert resp.status_code == 409
    assert age_gate.blocked(user) == age_gate.UNDERAGE


def test_birth_year_cannot_be_cleared_in_about_you():
    user = _user("adult", ADULT_YEAR)
    c = _api(user)
    for empty in (None, ""):
        assert c.patch("/cauldron/api/body/", {"birth_year": empty}, format="json").status_code == 400
    assert CauldronProfile.objects.get(user=user).birth_year == ADULT_YEAR


def test_changing_the_birth_year_to_under_18_blocks():
    user = _user("adult", ADULT_YEAR)
    c = _api(user)
    assert c.patch("/cauldron/api/body/", {"birth_year": MINOR_YEAR}, format="json").status_code == 200
    assert c.get("/cauldron/api/today/").json()["code"] == "underage"


# ── MCP ──────────────────────────────────────────────────────────────────────


def _token(user):
    app = get_application_model().objects.create(
        name="Assistant", client_type="public", authorization_grant_type="authorization-code",
        redirect_uris="https://claude.ai/api/mcp/auth_callback",
    )
    return get_access_token_model().objects.create(
        user=user, application=app, token=secrets.token_urlsafe(32), scope="cauldron:read cauldron:write",
        expires=timezone.now() + timedelta(hours=1),
    ).token


def _tool(user, name):
    resp = Client().post(
        "/cauldron/mcp/", data={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": name, "arguments": {}}},
        content_type="application/json", HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_AUTHORIZATION=f"Bearer {_token(user)}",
    )
    assert resp.status_code == 200, resp.content
    return resp.json()["result"]


@pytest.mark.parametrize("name", ["get_forge_today", "get_equipment", "get_today"])
def test_mcp_tools_need_the_birth_year(name):
    result = _tool(_user("legacy"), name)
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "age_required"
    assert result["structuredContent"]["accept_url"] == "http://testserver/cauldron/age/"


@pytest.mark.parametrize("name", ["get_forge_today", "get_equipment", "get_today"])
def test_mcp_tools_are_closed_under_18(name):
    result = _tool(_user("kid", MINOR_YEAR), name)
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "underage"


def test_mcp_tools_open_for_adults():
    assert _tool(_user("adult", ADULT_YEAR), "get_equipment")["isError"] is False

"""The journal says why a recommended goal is missing (#114): ``/rolling/``
carries the reasons the recommendations already know, and the page has the
place to show them with a link to "About you"."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework.test import APIClient

from the_cauldron.models import CauldronProfile
from the_cauldron.nutrition_i18n import nutrition_strings
from the_cauldron.services import legal, recommendations, rolling

User = get_user_model()
NOW = datetime(2026, 10, 15, 18, 30, tzinfo=ZoneInfo("America/Mexico_City"))


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


@pytest.fixture
def alice(db):
    user = User.objects.create_user(username="alice", email="alice@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
    return user


def test_no_weight_lists_the_weight_based_nutrients(alice):
    CauldronProfile.objects.create(user=alice, birth_year=1990, sex="female")
    who = rolling.rolling(alice, "24h", 7, now=NOW)["sets"]["who"]
    assert who["needs_weight"] == list(recommendations.WEIGHT_BASED)
    assert {"kcal", "sat_fat_g"} <= set(who["needs_weight"])
    assert who["assumed"] == ["activity"]
    assert who["unresolved"] == []


def test_with_weight_nothing_is_missing(alice):
    CauldronProfile.objects.create(user=alice, birth_year=1990, sex="female", weight_kg=60, activity_level="moderate")
    who = rolling.rolling(alice, "24h", 7, now=NOW)["sets"]["who"]
    assert who["needs_weight"] == [] and who["assumed"] == [] and who["unresolved"] == []
    assert "kcal" in who["status"]


def test_reasons_match_the_recommendations(alice):
    # No profile at all: sex and age are guessed, and whatever can't be agreed is unresolved.
    who = rolling.rolling(alice, "24h", 7, now=NOW)["sets"]["who"]
    rec = recommendations.recommended_for(alice)
    for reason in ("needs_weight", "assumed", "unresolved"):
        assert who[reason] == rec[reason]


def test_the_api_carries_the_reasons(alice):
    c = APIClient()
    c.force_authenticate(user=alice)
    who = c.get("/cauldron/api/rolling/?span=24h&days=7").json()["sets"]["who"]
    assert {"needs_weight", "assumed", "unresolved", "status_reason"} <= set(who)


def test_journal_has_the_notice_and_its_strings(alice):
    c = Client()
    c.force_login(alice)
    page = c.get("/cauldron/journal/").content.decode()
    assert 'id="nut-who-missing"' in page
    strings = nutrition_strings()
    for key in ("who_needs_weight", "who_needs_weight_link", "who_unresolved", "who_unresolved_link"):
        assert strings[key]

"""The goal cauldron (#107): a card's number broken into what was eaten, and
filling the values it doesn't know.

The user is in America/Mexico_City. "Now" is fixed at Oct 15 2026, 18:30 local.
"""

from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import IntakeEntry, Reagent, ReagentNutrients, Spell, SpellReagent
from the_cauldron.nutrition_writes import apply_entry
from the_cauldron.services import breakdown, legal, nutrition, rolling
from the_cauldron.tests.test_mcp_server import _ok, _token

User = get_user_model()
MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2026, 10, 15, 18, 30, tzinfo=MX)
API = "/cauldron/api/"


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=MX)


def _user(name="alice", consent=True):
    user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY] + ([legal.SENSITIVE_CONSENT] if consent else []))
    return user


@pytest.fixture
def alice(db):
    return _user()


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _reagent(name, owner=None, status=Reagent.Status.APPROVED, **nutrients):
    r = Reagent.objects.create(name=name, status=status, created_by=owner, source="user" if owner else "usda",
                               grams_per_unit={"piece": 50})
    ReagentNutrients.objects.create(reagent=r, **nutrients)
    return r


def _eat_reagent(user, reagent, grams, when):
    data = {"source": "reagent", "reagent": reagent.uuid, "quantity": grams, "unit": "g", "eaten_at": when}
    return apply_entry(user, data)


def _eat_spell(user, spell, grams, when):
    return apply_entry(user, {"source": "spell", "spell": spell.uuid, "quantity": grams, "unit": "g", "eaten_at": when})


def _quick(user, when, label="Tacos", **values):
    nutrients, missing = nutrition.clean_quick_values(values)
    return IntakeEntry.objects.create(user=user, eaten_at=when, local_date=when.date(), source="quick", label=label,
                                      quantity=1, unit="portion", nutrients=nutrients, missing=missing)


def _spell(owner, title, lines):
    spell = Spell.objects.create(owner=owner, title=title, servings=1)
    for order, (reagent, grams) in enumerate(lines):
        SpellReagent.objects.create(spell=spell, reagent=reagent, quantity=grams, unit="g", grams=grams, order=order)
    return spell


def _bd(user, nutrient="sodium_mg", span="24h", end=NOW):
    return breakdown.breakdown(user, span, end, nutrient)


class TestWindowAndGrouping:
    def test_window_bounds_match_rolling(self, alice):
        salt = _reagent("Salt", sodium_mg=1000)
        _eat_reagent(alice, salt, 10, NOW - timedelta(hours=24))  # exactly 24 h ago: outside
        _eat_reagent(alice, salt, 20, NOW - timedelta(hours=23, minutes=59))
        _eat_reagent(alice, salt, 30, NOW)  # at the end: inside
        d = _bd(alice)
        assert d["entries"] == 2 and d["total"] == pytest.approx(500)
        roll = rolling.rolling(alice, "24h", 7, now=NOW)
        assert roll["values"]["sodium_mg"][-1] == pytest.approx(d["total"])

    def test_entries_are_combined_and_top_5_plus_others_sum_to_the_card(self, alice):
        foods = [_reagent(f"Food {k}", sodium_mg=100 * (k + 1)) for k in range(7)]
        for k, food in enumerate(foods):
            _eat_reagent(alice, food, 100, at(15, 8 + k))
        _eat_reagent(alice, foods[0], 100, at(15, 16))  # a second entry of the smallest
        d = _bd(alice)
        assert [row["label"] for row in d["pie"][:5]] == ["Food 6", "Food 5", "Food 4", "Food 3", "Food 2"]
        others = d["pie"][5]
        assert others["kind"] == "others" and others["groups"] == 2 and others["entries"] == 3
        assert sum(row["amount"] for row in d["pie"]) == pytest.approx(d["total"])
        assert d["total"] == pytest.approx(sum(100 * (k + 1) for k in range(7)) + 100)

    def test_seven_days_is_a_daily_average(self, alice):
        salt = _reagent("Salt", sodium_mg=1000)
        _eat_reagent(alice, salt, 70, at(13, 12))
        d = _bd(alice, span="7d")
        assert d["per_day"] == 7 and d["total"] == pytest.approx(100) and d["pie"][0]["amount"] == pytest.approx(100)
        assert rolling.rolling(alice, "7d", 7, now=NOW)["values"]["sodium_mg"][-1] == pytest.approx(100)

    def test_deleted_spell_keeps_its_snapshot_title(self, alice):
        salt = _reagent("Salt", sodium_mg=1000)
        spell = _spell(alice, "Soup", [(salt, 10)])
        _eat_spell(alice, spell, 100, at(15, 12))
        spell.delete()
        row = _bd(alice)["pie"][0]
        assert row["label"] == "Soup" and row["spell"] is None


class TestFog:
    def test_unknowns_are_fog_never_sized(self, alice):
        mystery = _reagent("Mystery", kcal=100)  # sodium unknown
        salt = _reagent("Salt", sodium_mg=1000)
        _eat_reagent(alice, mystery, 100, at(15, 9))
        _eat_reagent(alice, salt, 10, at(15, 10))
        d = _bd(alice)
        assert d["lower_bound"] is True
        assert [row["label"] for row in d["pie"]] == ["Salt"]
        assert [f["label"] for f in d["fog"]] == ["Mystery"] and d["fog"][0]["amount"] == 0
        assert d["fog"][0]["fill"]["reagents"][0]["editable"] is False

    def test_partly_known_spell_is_in_pie_and_fog(self, alice):
        salt = _reagent("Salt", sodium_mg=1000)
        herb = _reagent("Herb", kcal=40)
        spell = _spell(alice, "Soup", [(salt, 10), (herb, 90)])
        _eat_spell(alice, spell, 100, at(15, 12))
        d = _bd(alice)
        assert d["pie"][0]["label"] == "Soup" and d["fog"][0]["label"] == "Soup"
        assert [r["name"] for r in d["fog"][0]["fill"]["reagents"]] == ["Herb"]

    def test_all_fog_and_empty(self, alice):
        assert _bd(alice)["entries"] == 0
        _quick(alice, at(15, 9), kcal=500)
        d = _bd(alice)
        assert d["pie"] == [] and d["fog"][0]["fill"]["quick"][0]["label"] == "Tacos"


class TestFillOptions:
    def test_someone_elses_spell_points_to_make_it_mine(self, alice):
        bob = _user("bob")
        oats = _reagent("Oats", kcal=389)
        public = _spell(bob, "Bob's porridge", [(oats, 50)])
        public.visibility = Spell.Visibility.PUBLIC
        public.save()
        _eat_spell(alice, public, 50, at(15, 9))
        assert _bd(alice)["fog"][0]["fill"] == {"not_owner": True}

    def test_a_carried_private_reagent_is_not_offered(self, alice):
        bob = _user("bob")
        secret = _reagent("Bob's mix", owner=bob, status=Reagent.Status.PRIVATE, kcal=1)
        mine = _spell(alice, "Fork", [(secret, 50)])
        _eat_spell(alice, mine, 50, at(15, 9))
        assert _bd(alice)["fog"][0]["fill"] == {"reagents": []}


class TestFill:
    def test_own_reagent_is_saved_and_marked_estimated(self, alice):
        mine = _reagent("My broth", owner=alice, status=Reagent.Status.PRIVATE, kcal=10)
        entry = _eat_reagent(alice, mine, 200, timezone.now() - timedelta(hours=2))
        r = _client(alice).post(f"{API}rolling/fill/", {"nutrient": "sodium_mg", "reagent": str(mine.uuid),
                                                       "value": 300}, format="json")
        assert r.status_code == 200, r.data
        mine.refresh_from_db()
        assert r.data["source"] == "reagent" and mine.nutrients.sodium_mg == 300 and mine.nutrients_estimated
        entry.refresh_from_db()
        assert entry.nutrients["sodium_mg"] == pytest.approx(600) and "sodium_mg" not in entry.missing
        assert entry.basis["estimated"] is True

    def test_catalog_reagent_gets_a_private_copy_reused_and_spells_switch(self, alice):
        bob = _user("bob")
        oats = _reagent("Oats", kcal=389)
        my_spell = _spell(alice, "Porridge", [(oats, 50)])
        bobs_spell = _spell(bob, "Bob's porridge", [(oats, 50)])
        c = _client(alice)
        r = c.post(f"{API}rolling/fill/", {"nutrient": "sodium_mg", "reagent": str(oats.uuid), "value": 2},
                   format="json")
        assert r.status_code == 200 and r.data["source"] == "reagent_copy"
        copy = Reagent.objects.get(copy_of=oats, created_by=alice)
        assert copy.status == "private" and copy.nutrients_estimated and copy.nutrients.sodium_mg == 2
        assert copy.nutrients.kcal == 389 and copy.grams_per_unit == oats.grams_per_unit
        oats.refresh_from_db()
        assert oats.nutrients.sodium_mg is None  # the original is unchanged
        assert my_spell.lines.get().reagent == copy
        assert bobs_spell.lines.get().reagent == oats  # someone else's spell untouched
        c.post(f"{API}rolling/fill/", {"nutrient": "iron_mg", "reagent": str(oats.uuid), "value": 4}, format="json")
        assert Reagent.objects.filter(copy_of=oats, created_by=alice).count() == 1  # reused

    def test_refresh_touches_only_missing_values_of_that_nutrient_in_the_last_7_days(self, alice):
        oats = _reagent("Oats", kcal=389)
        salt = _reagent("Salt", sodium_mg=1000, kcal=0)
        recent = _eat_reagent(alice, oats, 100, timezone.now() - timedelta(days=2))
        old = _eat_reagent(alice, oats, 100, timezone.now() - timedelta(days=8))
        soup = _spell(alice, "Soup", [(oats, 100), (salt, 1)])
        spell_entry = _eat_spell(alice, soup, 101, timezone.now() - timedelta(hours=3))
        known_kcal = recent.nutrients["kcal"]
        assert spell_entry.missing.get("sodium_mg") == 1
        breakdown.fill_reagent(alice, oats, "sodium_mg", 5)
        for e in (recent, old, spell_entry):
            e.refresh_from_db()
        assert recent.nutrients["sodium_mg"] == pytest.approx(5) and recent.nutrients["kcal"] == known_kcal
        assert "sodium_mg" not in old.nutrients and old.missing["sodium_mg"] == 1  # older: untouched
        # The spell now uses the copy (5 mg/100 g oats + 10 mg salt per 101 g cooked).
        assert spell_entry.nutrients["sodium_mg"] == pytest.approx(15, rel=1e-3)
        assert "sodium_mg" not in spell_entry.missing and spell_entry.basis["estimated"] is True

    def test_spell_without_the_reagent_any_more_is_skipped(self, alice):
        mine = _reagent("Herb", owner=alice, status=Reagent.Status.PRIVATE, kcal=40)
        salt = _reagent("Salt", sodium_mg=1000)
        soup = _spell(alice, "Soup", [(mine, 90), (salt, 10)])
        entry = _eat_spell(alice, soup, 100, timezone.now() - timedelta(hours=3))
        soup.lines.filter(reagent=mine).delete()
        breakdown.fill_reagent(alice, mine, "sodium_mg", 50)
        entry.refresh_from_db()
        assert entry.missing.get("sodium_mg") == 1

    def test_quick_entry_filled_in_place(self, alice):
        entry = _quick(alice, timezone.now() - timedelta(hours=1), kcal=500)
        r = _client(alice).post(f"{API}rolling/fill/", {"nutrient": "sodium_mg", "entry": str(entry.uuid),
                                                       "value": 900}, format="json")
        assert r.status_code == 200 and r.data["source"] == "quick"
        entry.refresh_from_db()
        assert entry.nutrients["sodium_mg"] == 900 and "sodium_mg" not in entry.missing and entry.basis["estimated"]

    def test_bad_input(self, alice):
        c = _client(alice)
        assert c.post(f"{API}rolling/fill/", [], format="json").status_code == 400
        assert c.get(f"{API}rolling/breakdown/?nutrient=kcal&end=0001-01-01T00:00").status_code == 400
        assert c.get(f"{API}rolling/breakdown/?nutrient=kcal&end=2026-13-01T00:00").status_code == 400
        oats = _reagent("Oats", kcal=389)
        for body in ({"nutrient": "nope", "reagent": str(oats.uuid), "value": 1},
                     {"nutrient": "sodium_mg", "reagent": str(oats.uuid), "value": -1},
                     {"nutrient": "sodium_mg", "reagent": str(oats.uuid), "value": "nan"},
                     {"nutrient": "sodium_mg", "value": 1}):
            assert c.post(f"{API}rolling/fill/", body, format="json").status_code == 400, body
        assert c.post(f"{API}rolling/fill/", {"nutrient": "sodium_mg", "reagent": "not-a-uuid", "value": 1},
                      format="json").status_code == 404

    def test_added_sugar_above_total_is_refused(self, alice):
        jam = _reagent("Jam", owner=alice, status=Reagent.Status.PRIVATE, sugar_g=10)
        r = _client(alice).post(f"{API}rolling/fill/", {"nutrient": "added_sugar_g", "reagent": str(jam.uuid),
                                                       "value": 20}, format="json")
        assert r.status_code == 400


class TestAccess:
    def test_consent_required(self, db):
        c = _client(_user(consent=False))
        assert c.get(f"{API}rolling/breakdown/?nutrient=kcal").status_code == 403
        assert c.post(f"{API}rolling/fill/", {}, format="json").status_code == 403

    def test_other_users_data_is_invisible_and_unfillable(self, alice):
        bob = _user("bob")
        _quick(bob, timezone.now() - timedelta(hours=1), label="Bob's lunch", kcal=500)
        bobs = IntakeEntry.objects.get(user=bob)
        private = _reagent("Bob's secret", owner=bob, status=Reagent.Status.PRIVATE, kcal=1)
        c = _client(alice)
        assert c.get(f"{API}rolling/breakdown/?nutrient=sodium_mg").data["entries"] == 0
        assert c.post(f"{API}rolling/fill/", {"nutrient": "sodium_mg", "entry": str(bobs.uuid), "value": 1},
                      format="json").status_code == 404
        assert c.post(f"{API}rolling/fill/", {"nutrient": "sodium_mg", "reagent": str(private.uuid), "value": 1},
                      format="json").status_code == 404

    def test_posthog_gets_only_enums(self, alice):
        entry = _quick(alice, timezone.now() - timedelta(hours=1), kcal=500)
        c = _client(alice)
        with mock.patch("the_cauldron.nutrition_api.posthog.capture") as capture:
            c.get(f"{API}rolling/breakdown/?nutrient=sodium_mg&span=7d")
            c.post(f"{API}rolling/fill/", {"nutrient": "sodium_mg", "entry": str(entry.uuid), "value": 9},
                   format="json")
        assert [call.args for call in capture.call_args_list] == [("goal_breakdown_opened",), ("missing_value_filled",)]
        assert [call.kwargs for call in capture.call_args_list] == [
            {"properties": {"span": "7d"}}, {"properties": {"source": "quick"}}]

    def test_mcp_tool_returns_the_endpoint_payload(self, alice):
        _quick(alice, timezone.now() - timedelta(hours=1), kcal=500, sodium_mg=800)
        api = _client(alice).get(f"{API}rolling/breakdown/?nutrient=sodium_mg").data
        tool = _ok(_token(alice), "get_goal_breakdown", {"nutrient": "sodium_mg"})
        assert tool["total"] == api["total"] == 800
        assert [r["label"] for r in tool["pie"]] == [r["label"] for r in api["pie"]] == ["Tacos"]


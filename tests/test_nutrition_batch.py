"""Nutrition batch: 12 more micronutrients (#125), the Journal list on the
gauges' window (#124), gauge eyes and goal autosave (#131), and the 12-hour
projection with planned meal times (#132).

The user is in America/Mexico_City (UTC−6); "now" is Oct 15 2026, 18:30 local.
"""

import importlib
import json
from datetime import date, datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    CauldronProfile,
    IntakeEntry,
    NutrientGoal,
    PlannedMeal,
    Reagent,
    ReagentNutrients,
    Spell,
    SpellReagent,
)
from the_cauldron.services import breakdown, legal, nutrition, recommendations, rolling
from the_cauldron.services.breakdown import ALL_TIME_NUTRIENTS
from the_cauldron.tests.test_mcp_server import _ok, _token  # noqa: F401

User = get_user_model()
MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2026, 10, 15, 18, 30, tzinfo=MX)
NEW = ["thiamin_mg", "riboflavin_mg", "niacin_mg", "pantothenic_mg", "vit_b6_mg", "folate_ug",
       "vit_e_mg", "vit_k_ug", "zinc_mg", "magnesium_mg", "selenium_ug", "phosphorus_mg"]


def at(day, hour, minute=0, tz=MX):
    return datetime(2026, 10, day, hour, minute, tzinfo=tz)


@pytest.fixture
def alice(db):
    user = User.objects.create_user(username="alice", email="alice@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
    NutrientGoal.objects.create(user=user, nutrient="protein_g", kind="at_least", min_value=100,
                                valid_from=date(2026, 9, 1))
    return user


@pytest.fixture
def client(alice):
    c = APIClient()
    c.force_authenticate(alice)
    return c


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: NOW.astimezone(ZoneInfo("UTC")))


def eat(user, when, **values):
    nutrients, missing = nutrition.clean_quick_values(values)
    return IntakeEntry.objects.create(
        user=user, eaten_at=when, local_date=when.astimezone(MX).date(), source="quick", label="x",
        quantity=1, unit="portion", nutrients=nutrients, missing=missing,
    )


def _reagent(name, **nutrients):
    r = Reagent.objects.create(name=name, status=Reagent.Status.APPROVED, grams_per_unit={"piece": 50})
    ReagentNutrients.objects.create(reagent=r, **nutrients)
    return r


@pytest.fixture
def stew(alice):
    beans = _reagent("Beans", kcal=100, protein_g=20)
    spell = Spell.objects.create(owner=alice, title="Stew", servings=2, cooked_yield_g=400)
    SpellReagent.objects.create(spell=spell, reagent=beans, quantity=400, unit="g", grams=400, order=0)
    return spell


def plan(user, spell, day, hour, slot="dinner", quantity=1):
    return PlannedMeal.objects.create(user=user, spell=spell, spell_title=spell.title if spell else "Gone",
                                      date=date(2026, 10, day), time=time(hour, 0), slot=slot,
                                      quantity=quantity, unit="serving")


# ── #125 twelve more micronutrients ──────────────────────────────────────────


class TestMicronutrients:
    def test_fields_and_recommendations(self, db):
        assert all(f in ReagentNutrients.FIELDS for f in NEW)
        assert len(ReagentNutrients.FIELDS) == 36
        dri = recommendations.DRI
        assert dri["folate_ug"] == [(18, None, 400, 400)]
        assert dri["magnesium_mg"][-1] == (31, None, 420, 320)
        assert dri["vit_k_ug"] == [(18, None, 120, 90)]

    def test_recommended_goals_have_no_food_maximum(self, alice):
        CauldronProfile.objects.update_or_create(user=alice, defaults={"birth_year": 1990, "sex": "female"})
        goals = recommendations.recommended_for(alice, today=date(2026, 10, 15))["goals"]
        for n in NEW:
            assert goals[n].kind == "at_least" and goals[n].max_value is None
        assert goals["magnesium_mg"].min_value == 320 and goals["zinc_mg"].min_value == 8

    def test_seed_fills_from_usda_once(self, db):
        call_command("seed_grimoire", stdout=StringIO())
        lentils = Reagent.objects.get(fdc_id=172420)
        assert lentils.nutrients.folate_ug == 479.0
        lentils.nutrients.folate_ug = None
        lentils.nutrients.zinc_mg = 9.99  # a staff edit is never overwritten
        lentils.nutrients.save()
        call_command("seed_grimoire", stdout=StringIO())
        lentils.nutrients.refresh_from_db()
        assert (lentils.nutrients.folate_ug, lentils.nutrients.zinc_mg) == (479.0, 9.99)

    def test_unknown_is_never_zero_in_totals(self, alice):
        eat(alice, at(15, 12), protein_g=10)
        eat(alice, at(15, 13), protein_g=10, zinc_mg=4)
        result = rolling.rolling(alice, "24h", 7, now=NOW)
        assert result["values"]["zinc_mg"][-1] == 4 and result["missing"]["zinc_mg"][-1] == 1

    def test_migration_marks_old_entries_missing(self, alice):
        old = IntakeEntry.objects.create(
            user=alice, eaten_at=at(10, 12), local_date=date(2026, 10, 10), source="reagent", label="old",
            quantity=100, unit="g", grams=100, nutrients={"kcal": 100}, missing={},
            basis={"per_100g": {"kcal": 100}, "missing": {}},
        )
        module = importlib.import_module("the_cauldron.migrations.0045_micronutrients_more")
        module.mark_missing(django_apps, None)
        old.refresh_from_db()
        assert all(old.missing.get(n) == 1 for n in NEW)
        assert all(old.basis["missing"].get(n) == 1 for n in NEW)
        module.mark_missing(django_apps, None)  # idempotent
        old.refresh_from_db()
        assert all(old.missing.get(n) == 1 for n in NEW)

    def test_backfill_fills_only_missing_all_time(self, alice):
        zinc = _reagent("Pumpkin seeds", kcal=500)
        long_ago = IntakeEntry.objects.create(
            user=alice, eaten_at=timezone.now() - timedelta(days=200), local_date=date(2026, 3, 1),
            source="reagent", reagent=zinc, label="seeds", quantity=50, unit="g", grams=50,
            nutrients={"kcal": 250, "magnesium_mg": 1.0}, missing={"zinc_mg": 1},
            basis={"per_100g": {"kcal": 500, "magnesium_mg": 2.0}, "missing": {"zinc_mg": 1}},
        )
        ReagentNutrients.objects.filter(reagent=zinc).update(zinc_mg=7.6, magnesium_mg=550)
        out = StringIO()
        call_command("backfill_micronutrients", stdout=out)
        long_ago.refresh_from_db()
        assert long_ago.nutrients["zinc_mg"] == 3.8 and "zinc_mg" not in long_ago.missing
        assert long_ago.nutrients["magnesium_mg"] == 1.0  # a known value never changes
        assert long_ago.basis["estimated"] is True
        call_command("backfill_micronutrients", stdout=StringIO())
        long_ago.refresh_from_db()
        assert long_ago.nutrients["zinc_mg"] == 3.8

    def test_refresh_reaches_all_time_only_for_the_new_twelve(self, alice):
        r = _reagent("Kale", kcal=50)
        old = IntakeEntry.objects.create(
            user=alice, eaten_at=timezone.now() - timedelta(days=60), local_date=date(2026, 8, 1),
            source="reagent", reagent=r, label="kale", quantity=100, unit="g", grams=100,
            nutrients={"kcal": 50}, missing={"vit_k_ug": 1, "iron_mg": 1},
            basis={"per_100g": {"kcal": 50}, "missing": {"vit_k_ug": 1, "iron_mg": 1}},
        )
        assert breakdown.refresh_entries(alice, [r], "vit_k_ug", 390) == 1
        assert breakdown.refresh_entries(alice, [r], "iron_mg", 1.5) == 0  # 7-day rule still applies
        old.refresh_from_db()
        assert old.nutrients["vit_k_ug"] == 390 and old.missing == {"iron_mg": 1}

    def test_fill_micronutrients_only_fills_nulls(self, alice, tmp_path):
        r = Reagent.objects.create(name="Mine", created_by=alice, source="user")
        ReagentNutrients.objects.create(reagent=r, selenium_ug=5)
        f = tmp_path / "values.json"
        f.write_text(json.dumps({str(r.uuid): {"selenium_ug": 99, "zinc_mg": 2.5, "kcal": 1}}), encoding="utf-8")
        call_command("fill_micronutrients", "--file", str(f), stdout=StringIO())
        r.refresh_from_db()
        assert (r.nutrients.selenium_ug, r.nutrients.zinc_mg, r.nutrients.kcal) == (5, 2.5, None)
        assert r.nutrients_estimated is True


# ── #131 gauge eyes + autosave ───────────────────────────────────────────────


class TestGaugeEyes:
    def test_toggle_persists_and_shows_everywhere(self, client, alice):
        r = client.patch("/cauldron/api/goals/gauges/", {"nutrient": "protein_g", "visible": False}, format="json")
        assert r.status_code == 200 and r.json()["hidden_gauges"] == ["protein_g"]
        assert client.get("/cauldron/api/goals/").json()["hidden_gauges"] == ["protein_g"]
        assert client.get("/cauldron/api/rolling/?span=24h&days=7").json()["hidden_gauges"] == ["protein_g"]
        client.patch("/cauldron/api/goals/gauges/", {"nutrient": "protein_g", "visible": True}, format="json")
        assert client.get("/cauldron/api/goals/").json()["hidden_gauges"] == []

    def test_unknown_nutrient_is_400(self, client):
        r = client.patch("/cauldron/api/goals/gauges/", {"nutrient": "unicorn_g", "visible": False}, format="json")
        assert r.status_code == 400

    def test_needs_consent(self, db):
        bob = User.objects.create_user(username="bob", email="bob@example.com", password="pw12345!")
        legal.accept(bob, [legal.TERMS, legal.PRIVACY])
        c = APIClient()
        c.force_authenticate(bob)
        r = c.patch("/cauldron/api/goals/gauges/", {"nutrient": "kcal", "visible": False}, format="json")
        assert r.status_code == 403 and r.json()["code"] == "consent_required"

    def test_hidden_changes_no_judgement(self, client, alice):
        client.patch("/cauldron/api/goals/gauges/", {"nutrient": "protein_g", "visible": False}, format="json")
        eat(alice, timezone.now() - timedelta(hours=1), protein_g=120)
        d = client.get("/cauldron/api/rolling/?span=24h&days=7").json()
        assert d["sets"]["mine"]["status"]["protein_g"][-1] == "met"
        assert "protein_g" in d["tracked"]

    def test_withdraw_with_delete_clears_hidden(self, client, alice):
        client.patch("/cauldron/api/goals/gauges/", {"nutrient": "kcal", "visible": False}, format="json")
        legal.withdraw_consent(alice, delete_data=True)
        assert CauldronProfile.objects.get(user=alice).journal_hidden_gauges == []

    def test_autosave_one_row_versions_it(self, client, alice):
        put = lambda goals: client.put("/cauldron/api/goals/", {"goals": goals}, format="json")  # noqa: E731
        protein = {"nutrient": "protein_g", "kind": "at_least", "min_value": 100, "max_value": None}
        assert put([protein, {"nutrient": "fiber_g", "kind": "at_least", "min_value": 25}]).status_code == 200
        assert put([protein]).status_code == 200  # fiber set to "No goal": removed
        active = set(NutrientGoal.objects.filter(user=alice, valid_to=None).values_list("nutrient", flat=True))
        assert active == {"protein_g"}

    def test_mcp_ignores_the_eyes(self, client, alice):
        client.patch("/cauldron/api/goals/gauges/", {"nutrient": "protein_g", "visible": False}, format="json")
        assert any(g["nutrient"] == "protein_g" for g in _ok(_token(alice), "get_goals")["mine"])


# ── #124 the list follows the window ─────────────────────────────────────────


class TestWindowList:
    def test_boundaries_and_cross_midnight(self, client, alice, frozen):
        eat(alice, at(14, 18, 30), protein_g=1)  # exactly 24 h before: out
        eat(alice, at(14, 23, 0), protein_g=2)   # yesterday, in
        eat(alice, at(15, 18, 30), protein_g=3)  # at the end: in
        d = client.get("/cauldron/api/intake/window/?span=24h").json()
        assert [e["nutrients"]["protein_g"] for e in d["entries"]] == [3, 2]  # newest first
        assert d["entries"][1]["local_date"] == "2026-10-14" and d["end_date"] == "2026-10-15"

    def test_past_end_and_seven_days(self, client, alice, frozen):
        eat(alice, at(10, 12), protein_g=5)
        eat(alice, at(14, 12), protein_g=6)
        d = client.get("/cauldron/api/intake/window/?span=24h&end=2026-10-14T13:00").json()
        assert [e["nutrients"]["protein_g"] for e in d["entries"]] == [6]
        d = client.get("/cauldron/api/intake/window/?span=7d").json()
        assert [e["nutrients"]["protein_g"] for e in d["entries"]] == [6, 5]

    def test_sum_matches_the_gauge(self, client, alice, frozen):
        for h in (9, 13, 17):
            eat(alice, at(15, h), protein_g=10 * h)
        roll = rolling.rolling(alice, "24h", 7)
        win = client.get("/cauldron/api/intake/window/?span=24h").json()
        assert sum(e["nutrients"]["protein_g"] for e in win["entries"]) == roll["values"]["protein_g"][-1]

    def test_dst_change_in_another_zone(self, client, alice, monkeypatch):
        # Europe/Madrid leaves summer time on Oct 25 2026 at 03:00 → 02:00.
        madrid = ZoneInfo("Europe/Madrid")
        CauldronProfile.objects.update_or_create(user=alice, defaults={"timezone": "Europe/Madrid"})
        now = datetime(2026, 10, 25, 12, 0, tzinfo=madrid)
        monkeypatch.setattr(timezone, "now", lambda: now.astimezone(ZoneInfo("UTC")))
        inside = IntakeEntry.objects.create(  # 24 real hours back is 13:00 the day before
            user=alice, eaten_at=datetime(2026, 10, 24, 13, 30, tzinfo=madrid), local_date=date(2026, 10, 24),
            source="quick", label="in", quantity=1, unit="portion", nutrients={"kcal": 1}, missing={})
        IntakeEntry.objects.create(
            user=alice, eaten_at=datetime(2026, 10, 24, 12, 30, tzinfo=madrid), local_date=date(2026, 10, 24),
            source="quick", label="out", quantity=1, unit="portion", nutrients={"kcal": 1}, missing={})
        d = client.get("/cauldron/api/intake/window/?span=24h").json()
        assert [e["uuid"] for e in d["entries"]] == [str(inside.uuid)]

    def test_uneaten_plans_are_listed_apart(self, client, alice, stew, frozen):
        plan(alice, stew, 15, 20)
        plan(alice, stew, 16, 8, slot="breakfast")
        plan(alice, stew, 13, 20)  # before the window's end date: not listed
        d = client.get("/cauldron/api/intake/window/?span=24h").json()
        assert [(p["date"], p["slot"]) for p in d["planned"]] == [("2026-10-15", "dinner"), ("2026-10-16", "breakfast")]

    def test_needs_consent(self, db):
        bob = User.objects.create_user(username="bob", email="bob@example.com", password="pw12345!")
        c = APIClient()
        c.force_authenticate(bob)
        assert c.get("/cauldron/api/intake/window/").status_code == 403

    def test_mcp_get_today_is_the_rolling_24h(self, alice, frozen):
        eat(alice, at(14, 23), protein_g=60)
        eat(alice, at(15, 12), protein_g=50)
        eat(alice, at(14, 10), protein_g=99)  # older than 24 h
        out = _ok(_token(alice), "get_today")
        assert [e["nutrients"]["protein_g"] for e in out["entries"]] == [50, 60]
        assert out["totals"]["values"]["protein_g"] == 110
        assert next(g for g in out["goals"] if g["nutrient"] == "protein_g")["status"] == "met"
        assert out["window"]["span"] == "24h"
        assert "projected_with_plan" not in out


# ── #132 projection + planned times ──────────────────────────────────────────


class TestProjection:
    def _protein(self, d, i):
        return d["values"]["protein_g"][i], d["sets"]["mine"]["status"]["protein_g"][i]

    def test_plans_and_scheduled_entries_count_only_in_projected_ticks(self, alice, stew):
        eat(alice, at(15, 12), protein_g=40)
        eat(alice, at(15, 21), protein_g=5)  # scheduled for later today
        plan(alice, stew, 15, 20)            # 1 serving = 200 g of stew = 40 g protein
        plan(alice, stew, 15, 10)            # already past and not marked: ignored
        d = rolling.rolling(alice, "24h", 7, now=NOW, ahead=12)
        assert d["projected"][-12:] == [True] * 12 and not any(d["projected"][:-12])
        now_i = len(d["ticks"]) - 13
        assert self._protein(d, now_i) == (40, "missed")
        at_2000 = d["ticks"].index(at(15, 20))
        assert self._protein(d, at_2000)[0] == 80
        at_2100 = d["ticks"].index(at(15, 21))
        assert self._protein(d, at_2100) == (85, "missed")

    def test_eaten_plan_is_not_counted_twice(self, alice, stew):
        meal = plan(alice, stew, 15, 20)
        entry = eat(alice, at(15, 20), protein_g=40)
        meal.intake_entry = entry
        meal.save()
        d = rolling.rolling(alice, "24h", 7, now=NOW, ahead=12)
        assert d["values"]["protein_g"][d["ticks"].index(at(15, 21))] == 40

    def test_gone_spell_makes_it_uncertain(self, alice, stew):
        NutrientGoal.objects.create(user=alice, nutrient="sodium_mg", kind="at_most", max_value=2000,
                                    valid_from=date(2026, 9, 1))
        eat(alice, at(15, 12), protein_g=120, sodium_mg=500)
        plan(alice, None, 15, 20)
        d = rolling.rolling(alice, "24h", 7, now=NOW, ahead=12)
        assert d["sets"]["mine"]["status"]["sodium_mg"][d["ticks"].index(at(15, 21))] == "uncertain"

    def test_after_midnight_uses_that_days_goals(self, alice):
        NutrientGoal.objects.filter(user=alice).update(valid_to=date(2026, 10, 16))
        NutrientGoal.objects.create(user=alice, nutrient="protein_g", kind="at_least", min_value=10,
                                    valid_from=date(2026, 10, 16))
        eat(alice, at(15, 12), protein_g=40)
        d = rolling.rolling(alice, "24h", 7, now=NOW, ahead=12)
        i = d["ticks"].index(datetime(2026, 10, 16, 1, 0, tzinfo=MX))
        assert d["sets"]["mine"]["low"]["protein_g"][i] == 10
        assert d["sets"]["mine"]["status"]["protein_g"][i] == "met"

    def test_projected_ticks_never_count_in_time_in_range(self, alice, stew):
        eat(alice, at(15, 12), protein_g=40)
        plan(alice, stew, 15, 20, quantity=3)
        base = rolling.rolling(alice, "24h", 7, now=NOW)
        ahead = rolling.rolling(alice, "24h", 7, now=NOW, ahead=12)
        assert base["sets"]["mine"]["summary"] == ahead["sets"]["mine"]["summary"]
        assert base["sets"]["mine"]["daily"] == ahead["sets"]["mine"]["daily"]

    def test_ahead_rejected_on_seven_days(self, client):
        assert client.get("/cauldron/api/rolling/?span=7d&days=7&ahead=12").status_code == 400
        assert client.get("/cauldron/api/rolling/?span=24h&days=7&ahead=13").status_code == 400
        assert client.get("/cauldron/api/rolling/?span=24h&days=7&ahead=12").status_code == 200

    def test_mcp_adherence_ahead(self, alice, stew):
        eat(alice, timezone.now() - timedelta(hours=2), protein_g=40)
        token = _token(alice)
        base = _ok(token, "get_adherence", {"days": 7})
        ahead = _ok(token, "get_adherence", {"days": 7, "ahead": 6})
        assert "projected" not in base
        assert len(ahead["projected"]) == 6 and all(t["projected"] for t in ahead["projected"])
        assert ahead["time_in_range"] == base["time_in_range"]


class TestPlannedTime:
    def test_migration_fills_by_slot(self, alice, stew):
        module = importlib.import_module("the_cauldron.migrations.0047_plannedmeal_time_fill")
        assert module.SLOT_TIMES["breakfast"] == time(8) and module.SLOT_TIMES["dinner"] == time(20)

    def test_api_default_and_custom_time(self, client, stew):
        r = client.post("/cauldron/api/plan/", {"date": "2026-10-20", "slot": "lunch", "spell": str(stew.uuid)},
                        format="json")
        assert r.status_code == 201 and r.json()["time"] == "14:00:00"
        r = client.post("/cauldron/api/plan/", {"date": "2026-10-20", "slot": "lunch", "spell": str(stew.uuid),
                                                "time": "13:15"}, format="json")
        assert r.json()["time"] == "13:15:00"

    def test_mcp_plan_meal_default_and_get_meal_plan(self, alice, stew):
        token = _token(alice)
        out = _ok(token, "plan_meal", {"spell_id": str(stew.uuid), "slot": "snack", "date": "2026-10-20"})
        assert out["planned"]["time"] == "17:00"
        out = _ok(token, "plan_meal", {"spell_id": str(stew.uuid), "slot": "snack", "date": "2026-10-20",
                                       "time": "16:45"})
        assert out["planned"]["time"] == "16:45"
        meals = _ok(token, "get_meal_plan", {"from": "2026-10-20"})["meals"]
        assert sorted(m["time"] for m in meals) == ["16:45", "17:00"]

    def test_eat_defaults_to_the_planned_time(self, client, alice, stew):
        meal = plan(alice, stew, 15, 20)
        r = client.post(f"/cauldron/api/plan/{meal.uuid}/eat/", {}, format="json")
        assert r.status_code == 201
        assert timezone.localtime(IntakeEntry.objects.get().eaten_at, MX) == at(15, 20)
        meal2 = plan(alice, stew, 16, 8, slot="breakfast")
        out = _ok(_token(alice), "mark_planned_meal_eaten", {"id": str(meal2.uuid)})
        assert out["logged"]["time"] == "08:00"

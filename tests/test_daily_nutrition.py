"""Daily nutrition (#85): legal acceptance (#84), goals (#80), meal log (#81),
adherence (#82) and the ritual planner (#83).

Every number below is computed by hand from the porridge fixture:
200 g oats (389 kcal, 16.9 g protein, 0 mg cholesterol per 100 g) + 100 g egg
(143 kcal, 12.6 g protein, 372 mg cholesterol), 2 servings, cooked weight 240 g.
Per 100 g cooked: 383.75 kcal, 19.333 g protein, 155 mg cholesterol.
One serving = 120 g: 460.5 kcal, 23.2 g protein, 186 mg cholesterol.
"""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from rest_framework.test import APIClient

from the_cauldron.models import (
    IntakeEntry,
    LegalAcceptance,
    NutrientGoal,
    PlannedMeal,
    Reagent,
    ReagentNutrients,
    Spell,
    SpellReagent,
)
from the_cauldron.services import adherence, legal, nutrition

User = get_user_model()
API = "/cauldron/api/"


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Render pages without the hashed-manifest storage (see test_forge_urls)."""
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pw12345!")


def _consent(user):
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
    return user


@pytest.fixture
def alice(db):
    return _consent(_user("alice"))


@pytest.fixture
def bob(db):
    return _consent(_user("bob"))


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _reagent(name, **nutrients):
    r = Reagent.objects.create(name=name, status=Reagent.Status.APPROVED, grams_per_unit={"piece": 50})
    if nutrients:
        ReagentNutrients.objects.create(reagent=r, **nutrients)
    return r


@pytest.fixture
def egg(db):
    return _reagent("Egg", kcal=143, protein_g=12.6, carbs_g=0.7, fat_g=9.5, cholesterol_mg=372)


@pytest.fixture
def porridge(alice, egg):
    oats = _reagent("Oats", kcal=389, protein_g=16.9, carbs_g=66.3, fat_g=6.9, cholesterol_mg=0)
    spell = Spell.objects.create(owner=alice, title="Porridge", servings=2, cooked_yield_g=240)
    SpellReagent.objects.create(spell=spell, reagent=oats, quantity=200, unit="g", grams=200, order=0)
    SpellReagent.objects.create(spell=spell, reagent=egg, quantity=2, unit="piece", grams=100, order=1)
    return spell


def _today(user):
    return nutrition.today_for(user)


def _log(client, **body):
    return client.post(f"{API}intake/", body, format="json")


def _quick(user, day, **values):
    """A quick entry straight in the DB, for adherence fixtures."""
    nutrients, missing = nutrition.clean_quick_values(values)
    return IntakeEntry.objects.create(
        user=user, eaten_at=datetime.combine(day, datetime.min.time(), tzinfo=dt_timezone.utc),
        local_date=day, source="quick", label="x", quantity=1, unit="portion",
        nutrients=nutrients, missing=missing,
    )


# ═════════════════════════════════════════════════════════════════════════
# #84 — Legal acceptance
# ═════════════════════════════════════════════════════════════════════════


class TestLegalAcceptance:
    def test_every_acceptance_is_stored_with_version_and_time(self, db):
        user = _user("carol")
        legal.accept(user, [legal.TERMS, legal.PRIVACY])
        rows = {r.document: r for r in LegalAcceptance.objects.filter(user=user)}
        assert rows[legal.TERMS].version == legal.CURRENT_VERSIONS[legal.TERMS]
        assert rows[legal.PRIVACY].version == legal.CURRENT_VERSIONS[legal.PRIVACY]
        assert rows[legal.TERMS].accepted_at is not None

    def test_rows_are_append_only(self, db, monkeypatch):
        user = _user("carol")
        monkeypatch.setitem(legal.CURRENT_VERSIONS, legal.TERMS, "1.0")
        legal.accept(user, [legal.TERMS])
        old = LegalAcceptance.objects.get(user=user)
        monkeypatch.setitem(legal.CURRENT_VERSIONS, legal.TERMS, "2.0")
        legal.accept(user, [legal.TERMS])
        versions = sorted(LegalAcceptance.objects.filter(user=user).values_list("version", flat=True))
        assert versions == ["1.0", "2.0"]
        old.refresh_from_db()
        assert old.version == "1.0"  # never rewritten
        old.version = "9.9"
        with pytest.raises(ValidationError):
            old.save()

    def test_accepting_the_same_version_twice_adds_nothing(self, db):
        user = _user("carol")
        legal.accept(user, [legal.TERMS])
        legal.accept(user, [legal.TERMS])
        assert LegalAcceptance.objects.filter(user=user).count() == 1

    def test_accept_api_records_the_current_versions(self, db):
        user = _user("carol")
        r = _client(user).post(f"{API}legal/accept/", {"documents": ["terms", "privacy"]}, format="json")
        assert r.status_code == 200, r.data
        assert r.data["pending"] == []
        assert r.data["accepted"]["terms"]["version"] == legal.CURRENT_VERSIONS[legal.TERMS]
        assert r.data["sensitive_consent"] is False

    def test_accept_api_rejects_unknown_documents(self, db):
        r = _client(_user("carol")).post(f"{API}legal/accept/", {"documents": ["cookies"]}, format="json")
        assert r.status_code == 400


class TestLegalGate:
    PAGES = ["/cauldron/forge/about/", "/cauldron/grimoire/", "/cauldron/profile/", "/cauldron/journal/"]

    @pytest.mark.parametrize("url", PAGES)
    def test_a_user_on_an_older_version_is_asked_to_accept_first(self, db, url, monkeypatch):
        user = _user("carol")
        monkeypatch.setitem(legal.CURRENT_VERSIONS, legal.TERMS, "1.0")
        legal.accept(user, [legal.TERMS, legal.PRIVACY])
        monkeypatch.setitem(legal.CURRENT_VERSIONS, legal.TERMS, "1.1")
        web = Client()
        web.force_login(user)
        r = web.get(url)
        assert r.status_code == 302
        assert r["Location"].startswith("/cauldron/accept/?next=")

    @pytest.mark.parametrize("url", PAGES)
    def test_a_user_on_the_current_version_goes_straight_in(self, alice, url):
        web = Client()
        web.force_login(alice)
        assert web.get(url).status_code == 200

    def test_accept_page_lists_only_what_is_pending(self, db):
        user = _user("carol")
        legal.accept(user, [legal.PRIVACY])
        web = Client()
        web.force_login(user)
        html = web.get("/cauldron/accept/?next=/cauldron/grimoire/").content.decode()
        assert 'value="terms"' in html
        assert 'value="privacy"' not in html
        assert 'name="documents" value="terms">' in html  # never pre-ticked

    def test_accept_page_moves_on_when_nothing_is_pending(self, alice):
        web = Client()
        web.force_login(alice)
        r = web.get("/cauldron/accept/?next=/cauldron/grimoire/")
        assert r.status_code == 302 and r["Location"] == "/cauldron/grimoire/"

    def test_accept_page_never_redirects_off_site(self, alice):
        web = Client()
        web.force_login(alice)
        r = web.get("/cauldron/accept/?next=https://evil.example/")
        assert r["Location"] == "/cauldron/journal/"

    def test_public_legal_pages_show_the_current_versions(self, db):
        web = Client()
        assert f"Version {legal.CURRENT_VERSIONS[legal.TERMS]}" in web.get("/cauldron/terms/").content.decode()
        assert f"Version {legal.CURRENT_VERSIONS[legal.PRIVACY]}" in web.get("/cauldron/privacy/").content.decode()


class TestSensitiveConsent:
    ENDPOINTS = ["goals/", "goals/history/", "intake/", "intake/day/", "adherence/", "plan/", "plan/day/"]

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_nothing_works_without_the_consent(self, db, path):
        user = _user("carol")
        legal.accept(user, [legal.TERMS, legal.PRIVACY])
        r = _client(user).get(f"{API}{path}")
        assert r.status_code == 403
        assert r.data["code"] == "consent_required"

    def test_no_entry_can_be_created_without_the_consent(self, db):
        user = _user("carol")
        legal.accept(user, [legal.TERMS, legal.PRIVACY])
        r = _log(_client(user), source="quick", label="Tacos", values={"kcal": 500})
        assert r.status_code == 403
        assert not IntakeEntry.objects.exists()

    def test_withdrawing_blocks_them_again(self, alice):
        c = _client(alice)
        assert c.get(f"{API}goals/").status_code == 200
        r = c.post(f"{API}legal/withdraw/", {}, format="json")
        assert r.status_code == 200 and r.data["sensitive_consent"] is False
        assert c.get(f"{API}goals/").status_code == 403
        # The withdrawn consent row is kept, closed — and consenting again adds a new row.
        row = LegalAcceptance.objects.get(user=alice, document=legal.SENSITIVE_CONSENT)
        assert row.withdrawn_at is not None
        c.post(f"{API}legal/accept/", {"documents": ["sensitive_consent"]}, format="json")
        assert LegalAcceptance.objects.filter(user=alice, document=legal.SENSITIVE_CONSENT).count() == 2
        assert c.get(f"{API}goals/").status_code == 200

    def test_withdrawing_with_delete_removes_the_data(self, alice, porridge):
        c = _client(alice)
        c.put(f"{API}goals/", {"goals": [{"nutrient": "kcal", "kind": "at_most", "max_value": 2000}]}, format="json")
        _log(c, source="quick", label="Tacos", values={"kcal": 500})
        c.post(f"{API}plan/", {"slot": "lunch", "spell": str(porridge.uuid)}, format="json")
        c.post(f"{API}legal/withdraw/", {"delete_data": True}, format="json")
        assert not NutrientGoal.objects.filter(user=alice).exists()
        assert not IntakeEntry.objects.filter(user=alice).exists()
        assert not PlannedMeal.objects.filter(user=alice).exists()

    def test_withdrawing_without_delete_keeps_the_data(self, alice):
        c = _client(alice)
        _log(c, source="quick", label="Tacos", values={"kcal": 500})
        c.post(f"{API}legal/withdraw/", {"delete_data": False}, format="json")
        assert IntakeEntry.objects.filter(user=alice).count() == 1

    def test_consent_panel_shows_instead_of_the_app_until_given(self, db):
        user = _user("carol")
        legal.accept(user, [legal.TERMS, legal.PRIVACY])
        web = Client()
        web.force_login(user)
        html = web.get("/cauldron/journal/").content.decode()
        assert 'id="nut-consent-check"' in html
        assert 'id="nut-app" hidden' in html

    def test_deleting_the_account_deletes_everything(self, alice, porridge):
        c = _client(alice)
        _log(c, source="quick", label="Tacos", values={"kcal": 500})
        c.post(f"{API}plan/", {"slot": "lunch", "spell": str(porridge.uuid)}, format="json")
        alice.delete()
        assert not IntakeEntry.objects.exists()
        assert not PlannedMeal.objects.exists()
        assert not LegalAcceptance.objects.exists()


# ═════════════════════════════════════════════════════════════════════════
# #80 — Daily goals
# ═════════════════════════════════════════════════════════════════════════


class TestGoals:
    def _put(self, c, goals):
        return c.put(f"{API}goals/", {"goals": goals}, format="json")

    def test_set_change_and_clear(self, alice):
        c = _client(alice)
        r = self._put(c, [
            {"nutrient": "protein_g", "kind": "at_least", "min_value": 100},
            {"nutrient": "sodium_mg", "kind": "at_most", "max_value": 2300},
            {"nutrient": "kcal", "kind": "between", "min_value": 1800, "max_value": 2200},
            {"nutrient": "fiber_g", "kind": "none"},
        ])
        assert r.status_code == 200, r.data
        assert {g["nutrient"]: g["kind"] for g in r.data["goals"]} == {
            "kcal": "between", "protein_g": "at_least", "sodium_mg": "at_most",
        }
        r = self._put(c, [{"nutrient": "protein_g", "kind": "at_least", "min_value": 120}])
        assert [(g["nutrient"], g["min_value"]) for g in r.data["goals"]] == [("protein_g", 120)]

    def test_changing_a_goal_keeps_the_previous_version(self, alice):
        today = _today(alice)
        NutrientGoal.objects.create(
            user=alice, nutrient="protein_g", kind="at_least", min_value=100, valid_from=today - timedelta(days=10),
        )
        self._put(_client(alice), [{"nutrient": "protein_g", "kind": "at_least", "min_value": 120}])
        rows = list(NutrientGoal.objects.filter(user=alice, nutrient="protein_g").order_by("valid_from"))
        assert [(g.min_value, g.valid_from, g.valid_to) for g in rows] == [
            (100, today - timedelta(days=10), today),
            (120, today, None),
        ]
        # A day before the change is judged against the old goal.
        old_day = today - timedelta(days=3)
        active = nutrition.goals_on(nutrition.goal_rows_between(alice, old_day, old_day), old_day)
        assert active["protein_g"].min_value == 100

    def test_an_unchanged_goal_keeps_its_row(self, alice):
        c = _client(alice)
        goals = [{"nutrient": "protein_g", "kind": "at_least", "min_value": 100}]
        self._put(c, goals)
        self._put(c, goals)
        assert NutrientGoal.objects.filter(user=alice).count() == 1

    def test_clearing_closes_the_goal(self, alice):
        c = _client(alice)
        NutrientGoal.objects.create(
            user=alice, nutrient="protein_g", kind="at_least", min_value=100,
            valid_from=_today(alice) - timedelta(days=5),
        )
        self._put(c, [])
        goal = NutrientGoal.objects.get(user=alice)
        assert goal.valid_to == _today(alice)
        assert c.get(f"{API}goals/").data["goals"] == []

    def test_history_lists_versions_but_not_same_day_replacements(self, alice):
        c = _client(alice)
        NutrientGoal.objects.create(
            user=alice, nutrient="protein_g", kind="at_least", min_value=90,
            valid_from=_today(alice) - timedelta(days=5),
        )
        self._put(c, [{"nutrient": "protein_g", "kind": "at_least", "min_value": 100}])
        self._put(c, [{"nutrient": "protein_g", "kind": "at_least", "min_value": 110}])  # same day
        history = c.get(f"{API}goals/history/").data["history"]
        assert [g["min_value"] for g in history] == [110, 90]

    @pytest.mark.parametrize("goal", [
        {"nutrient": "protein_g", "kind": "between", "min_value": 50},
        {"nutrient": "protein_g", "kind": "between", "min_value": 50, "max_value": 10},
        {"nutrient": "protein_g", "kind": "at_least"},
        {"nutrient": "protein_g", "kind": "at_most", "min_value": 5},
        {"nutrient": "protein_g", "kind": "at_least", "min_value": -1},
        {"nutrient": "protein_g", "kind": "at_least", "min_value": "lots"},
        {"nutrient": "unobtainium", "kind": "at_least", "min_value": 1},
        {"nutrient": "protein_g", "kind": "roughly", "min_value": 1},
    ])
    def test_bad_goals_are_400(self, alice, goal):
        assert self._put(_client(alice), [goal]).status_code == 400
        assert not NutrientGoal.objects.exists()

    def test_one_goal_per_nutrient(self, alice):
        r = self._put(_client(alice), [
            {"nutrient": "kcal", "kind": "at_most", "max_value": 2000},
            {"nutrient": "kcal", "kind": "at_least", "min_value": 1500},
        ])
        assert r.status_code == 400

    def test_another_users_goals_are_never_readable(self, alice, bob):
        self._put(_client(alice), [{"nutrient": "kcal", "kind": "at_most", "max_value": 2000}])
        assert _client(bob).get(f"{API}goals/").data["goals"] == []
        assert _client(bob).get(f"{API}goals/history/").data["history"] == []

    def test_timezone_setting(self, alice):
        c = _client(alice)
        assert c.get(f"{API}me/").data["timezone"] == "America/Mexico_City"
        assert c.patch(f"{API}me/", {"timezone": "Europe/Madrid"}, format="json").data["timezone"] == "Europe/Madrid"
        assert c.patch(f"{API}me/", {"timezone": "Mars/Olympus"}, format="json").status_code == 400


class TestPortionUsesProfileGoals:
    def _portion(self, user, spell, body=None):
        url = f"{API}grimoire/spells/{spell.uuid}/portion/"
        return _client(user).post(url, {} if body is None else body, format="json")

    def test_no_goals_hides_the_section(self, alice, porridge):
        assert self._portion(alice, porridge).data == {"has_goals": False}

    def test_no_consent_hides_the_section(self, db, porridge):
        user = _user("carol")
        legal.accept(user, [legal.TERMS, legal.PRIVACY])
        porridge.visibility = Spell.Visibility.PUBLIC
        porridge.save()
        assert self._portion(user, porridge).data == {"has_goals": False}

    def test_goals_in_the_request_body_are_ignored(self, alice, porridge):
        r = self._portion(alice, porridge, {"goals": [{"nutrient": "protein_g", "at_least": 29}]})
        assert r.data == {"has_goals": False}

    def test_range_comes_from_the_daily_goals_minus_what_is_eaten_and_planned(self, alice, porridge):
        c = _client(alice)
        c.put(f"{API}goals/", {"goals": [
            {"nutrient": "protein_g", "kind": "at_least", "min_value": 30},
            {"nutrient": "cholesterol_mg", "kind": "at_most", "max_value": 300},
        ]}, format="json")
        _log(c, source="quick", label="Shake", values={"protein_g": 20, "cholesterol_mg": 0})
        # Need 10 g more protein → 10 / 0.19333 = 51.7 g; 300 mg room → 300 / 1.55 = 193.5 g.
        r = self._portion(alice, porridge)
        assert r.data["has_goals"] and r.data["feasible"]
        assert r.data["min_g"] == pytest.approx(51.7, abs=0.1)
        assert r.data["max_g"] == pytest.approx(193.5, abs=0.1)
        # Plan one serving (23.2 g protein, 186 mg cholesterol): protein is covered,
        # 114 mg of cholesterol is left → 114 / 1.55 = 73.5 g.
        c.post(f"{API}plan/", {"slot": "dinner", "spell": str(porridge.uuid), "quantity": 1}, format="json")
        r = self._portion(alice, porridge)
        assert r.data["min_g"] == 0
        assert r.data["max_g"] == pytest.approx(73.5, abs=0.1)

    def test_a_used_up_maximum_names_the_goal(self, alice, porridge):
        c = _client(alice)
        c.put(f"{API}goals/", {"goals": [
            {"nutrient": "cholesterol_mg", "kind": "at_most", "max_value": 300},
        ]}, format="json")
        c.post(f"{API}plan/", {"slot": "dinner", "spell": str(porridge.uuid), "quantity": 2}, format="json")  # 372 mg
        r = self._portion(alice, porridge)
        assert not r.data["feasible"]
        assert r.data["conflicts"] == ["cholesterol_mg"] and r.data["over"] == ["cholesterol_mg"]

    def test_a_minimum_the_dish_cannot_bring_is_not_a_conflict(self, alice, porridge):
        ReagentNutrients.objects.update(vit_c_mg=0)  # a measured zero, not unknown
        c = _client(alice)
        c.put(f"{API}goals/", {"goals": [
            {"nutrient": "vit_c_mg", "kind": "at_least", "min_value": 60},
            {"nutrient": "kcal", "kind": "at_most", "max_value": 767.5},
        ]}, format="json")
        r = self._portion(alice, porridge)
        assert r.data["feasible"]
        assert r.data["not_covered"] == ["vit_c_mg"]
        assert r.data["max_g"] == pytest.approx(200, abs=0.1)  # 767.5 / 3.8375

    def test_conflicting_goals_are_named(self, alice, porridge):
        c = _client(alice)
        c.put(f"{API}goals/", {"goals": [
            {"nutrient": "protein_g", "kind": "at_least", "min_value": 100},
            {"nutrient": "cholesterol_mg", "kind": "at_most", "max_value": 100},
        ]}, format="json")
        r = self._portion(alice, porridge)
        assert not r.data["feasible"]
        assert r.data["conflicts"] == ["protein_g", "cholesterol_mg"]

    def test_private_spell_of_someone_else_is_404(self, bob, porridge):
        assert self._portion(bob, porridge).status_code == 404

    def test_unknown_data_is_not_reported_as_missing_from_the_dish(self, alice, porridge):
        c = _client(alice)
        c.put(f"{API}goals/", {"goals": [{"nutrient": "fiber_g", "kind": "at_least", "min_value": 25}]},
              format="json")
        r = self._portion(alice, porridge)  # neither reagent has fiber data
        assert r.data["not_covered"] == []
        assert "fiber_g" in r.data["incomplete"]

    def test_a_non_object_body_is_400(self, alice, porridge):
        _client(alice).put(f"{API}goals/", {"goals": [{"nutrient": "kcal", "kind": "at_most", "max_value": 900}]},
                           format="json")
        assert self._portion(alice, porridge, []).status_code == 400

    def test_spell_page_has_no_goal_inputs(self, alice, porridge):
        from pathlib import Path

        js = (Path(__file__).resolve().parent.parent / "static/the_cauldron/js/grimoire.js").read_text("utf-8")
        assert "grim-goal-add" not in js and "addGoalRow" not in js


# ═════════════════════════════════════════════════════════════════════════
# #81 — Meal log
# ═════════════════════════════════════════════════════════════════════════


class TestMealLog:
    def test_log_a_spell_a_reagent_and_a_quick_entry(self, alice, porridge, egg):
        c = _client(alice)
        day = _today(alice) - timedelta(days=1)
        r1 = _log(c, source="spell", spell=str(porridge.uuid), quantity=1, unit="serving", eaten_at=f"{day}T08:00")
        r2 = _log(c, source="reagent", reagent=str(egg.uuid), quantity=2, unit="piece", eaten_at=f"{day}T11:00")
        r3 = _log(c, source="quick", label="Tacos", values={"kcal": 500, "protein_g": 20}, eaten_at=f"{day}T14:30")
        assert (r1.status_code, r2.status_code, r3.status_code) == (201, 201, 201), (r1.data, r2.data, r3.data)
        assert r1.data["grams"] == pytest.approx(120)
        assert r1.data["nutrients"]["kcal"] == pytest.approx(460.5)
        assert r2.data["grams"] == pytest.approx(100)
        assert r2.data["missing"]["fiber_g"] == 1  # unknown, never 0
        assert r3.data["local_time"] == "14:30"
        assert r3.data["missing"]["carbs_g"] == 1

        d = c.get(f"{API}intake/day/?date={day}").data
        totals = {n["key"]: n for n in d["totals"]}
        assert totals["kcal"]["value"] == pytest.approx(460.5 + 143 + 500)
        assert totals["protein_g"]["value"] == pytest.approx(23.2 + 12.6 + 20)
        assert totals["kcal"]["missing"] == 0
        assert totals["carbs_g"]["missing"] == 1  # the quick entry
        assert [e["label"] for e in d["entries"]] == ["Porridge", "Egg", "Tacos"]

    def test_log_a_drink_by_the_glass(self, alice, egg):
        egg.grams_per_unit = {"glass": 260}  # any reagent with its own glass measure
        egg.save()
        r = _log(_client(alice), source="reagent", reagent=str(egg.uuid), quantity=1, unit="glass")
        assert r.status_code == 201, r.data
        assert r.data["grams"] == pytest.approx(260)

    def test_grams_of_the_cooked_dish(self, alice, porridge):
        r = _log(_client(alice), source="spell", spell=str(porridge.uuid), quantity=60, unit="g")
        assert r.data["nutrients"]["kcal"] == pytest.approx(230.25)
        assert r.data["nutrients"]["cholesterol_mg"] == pytest.approx(93)

    def test_editing_the_spell_afterwards_changes_nothing(self, alice, porridge):
        c = _client(alice)
        day = _today(alice) - timedelta(days=1)
        _log(c, source="spell", spell=str(porridge.uuid), quantity=1, unit="serving", eaten_at=f"{day}T08:00")
        line = porridge.lines.get(order=0)
        line.grams = 400
        line.save()
        porridge.servings = 4
        porridge.save()
        totals = {n["key"]: n for n in c.get(f"{API}intake/day/?date={day}").data["totals"]}
        assert totals["kcal"]["value"] == pytest.approx(460.5)

    def test_changing_the_amount_rescales_the_snapshot(self, alice, porridge):
        c = _client(alice)
        entry = _log(c, source="spell", spell=str(porridge.uuid), quantity=1, unit="serving").data
        porridge.lines.filter(order=0).update(grams=400)  # would change a fresh snapshot
        r = c.patch(f"{API}intake/{entry['uuid']}/", {"quantity": 2}, format="json")
        assert r.status_code == 200, r.data
        assert r.data["nutrients"]["kcal"] == pytest.approx(921)

    def test_edit_and_delete(self, alice):
        c = _client(alice)
        entry = _log(c, source="quick", label="Tacos", values={"kcal": 500}).data
        r = c.patch(f"{API}intake/{entry['uuid']}/", {"label": "Tacos al pastor", "values": {"kcal": 650}}, format="json")
        assert r.data["label"] == "Tacos al pastor" and r.data["nutrients"]["kcal"] == 650
        assert c.delete(f"{API}intake/{entry['uuid']}/").status_code == 204
        assert not IntakeEntry.objects.exists()

    def test_entries_near_midnight_land_on_the_right_day(self, alice):
        c = _client(alice)  # America/Mexico_City, UTC−6
        late = _log(c, source="quick", label="Late snack", values={"kcal": 100}, eaten_at="2026-10-15T23:50").data
        early = _log(c, source="quick", label="Early", values={"kcal": 100}, eaten_at="2026-10-16T00:10").data
        utc = _log(c, source="quick", label="UTC", values={"kcal": 100}, eaten_at="2026-10-16T05:30:00Z").data
        assert (late["local_date"], early["local_date"], utc["local_date"]) == ("2026-10-15", "2026-10-16", "2026-10-15")
        assert utc["local_time"] == "23:30"
        assert [e["label"] for e in c.get(f"{API}intake/day/?date=2026-10-15").data["entries"]] == ["UTC", "Late snack"]

    def test_the_users_timezone_moves_the_day(self, alice):
        c = _client(alice)
        c.patch(f"{API}me/", {"timezone": "Asia/Tokyo"}, format="json")
        r = _log(c, source="quick", label="UTC", values={"kcal": 100}, eaten_at="2026-10-15T20:00:00Z")
        assert r.data["local_date"] == "2026-10-16"

    def test_a_later_timezone_change_never_moves_an_entry(self, alice):
        c = _client(alice)
        entry = _log(c, source="quick", label="Dinner", values={"kcal": 600}, eaten_at="2026-10-14T23:30").data
        c.patch(f"{API}me/", {"timezone": "Europe/Madrid"}, format="json")
        r = c.patch(f"{API}intake/{entry['uuid']}/", {"values": {"kcal": 650}}, format="json")
        assert r.data["local_date"] == "2026-10-14"

    def test_editing_a_quick_entry_keeps_values_not_sent(self, alice):
        c = _client(alice)
        entry = _log(c, source="quick", label="Soup", values={"kcal": 300, "sodium_mg": 900}).data
        r = c.patch(f"{API}intake/{entry['uuid']}/", {"label": "Soup!"}, format="json")
        assert r.data["nutrients"] == {"kcal": 300, "sodium_mg": 900}

    def test_day_status_against_goals(self, alice):
        c = _client(alice)
        day = _today(alice) - timedelta(days=1)
        NutrientGoal.objects.create(user=alice, nutrient="kcal", kind="at_most", max_value=600,
                                    valid_from=day - timedelta(days=5))
        NutrientGoal.objects.create(user=alice, nutrient="protein_g", kind="at_least", min_value=50,
                                    valid_from=day - timedelta(days=5))
        NutrientGoal.objects.create(user=alice, nutrient="sodium_mg", kind="at_most", max_value=2000,
                                    valid_from=day - timedelta(days=5))
        _log(c, source="quick", label="Tacos", values={"kcal": 500, "protein_g": 20}, eaten_at=f"{day}T14:00")
        goals = {g["nutrient"]: g["status"] for g in c.get(f"{API}intake/day/?date={day}").data["goals"]}
        assert goals == {"kcal": "met", "protein_g": "missed", "sodium_mg": "uncertain"}
        empty = c.get(f"{API}intake/day/?date={day - timedelta(days=1)}").data
        assert {g["status"] for g in empty["goals"]} == {"not_logged"}

    def test_bad_input_is_400(self, alice, porridge):
        c = _client(alice)
        assert _log(c, source="spell", spell=str(porridge.uuid), quantity=1, unit="cup").status_code == 400
        assert _log(c, source="quick", label="", values={"kcal": 1}).status_code == 400
        assert _log(c, source="quick", label="Air", values={}).status_code == 400
        assert _log(c, source="reagent", quantity=1, unit="g").status_code == 400
        assert _log(c, source="spell", spell=str(porridge.uuid), quantity="nan", unit="g").status_code == 400
        assert c.post(f"{API}plan/", {"slot": "lunch", "spell": str(porridge.uuid), "quantity": "nan"},
                      format="json").status_code == 400

    def test_another_users_private_spell_cannot_be_logged(self, bob, porridge):
        r = _log(_client(bob), source="spell", spell=str(porridge.uuid), quantity=1, unit="serving")
        assert r.status_code == 400

    def test_another_users_entries_are_404(self, alice, bob):
        entry = _log(_client(alice), source="quick", label="Tacos", values={"kcal": 500}).data
        c = _client(bob)
        assert c.get(f"{API}intake/{entry['uuid']}/").status_code == 404
        assert c.patch(f"{API}intake/{entry['uuid']}/", {"label": "x"}, format="json").status_code == 404
        assert c.delete(f"{API}intake/{entry['uuid']}/").status_code == 404
        assert c.get(f"{API}intake/").data == []


# ═════════════════════════════════════════════════════════════════════════
# #82 — Adherence
# ═════════════════════════════════════════════════════════════════════════


TODAY = date(2026, 10, 15)


@pytest.fixture
def week(alice):
    """Protein ≥ 100 until Oct 12, then ≥ 50 (changed mid-period); sodium ≤ 2000.

    Day      protein   sodium    → protein   sodium
    Oct  9   120       1500        met       met
    Oct 10    80       2500        missed    missed
    Oct 11   —         —           not logged
    Oct 12    60       unknown     met (≥50) uncertain
    Oct 13    55       1000        met       met
    Oct 14    70       1800        met       met
    Oct 15    10        500        pending   pending   (today)
    """
    d = lambda day: date(2026, 10, day)  # noqa: E731
    NutrientGoal.objects.create(user=alice, nutrient="protein_g", kind="at_least", min_value=100,
                                valid_from=d(9), valid_to=d(12))
    NutrientGoal.objects.create(user=alice, nutrient="protein_g", kind="at_least", min_value=50, valid_from=d(12))
    NutrientGoal.objects.create(user=alice, nutrient="sodium_mg", kind="at_most", max_value=2000, valid_from=d(9))
    _quick(alice, d(9), protein_g=120, sodium_mg=1500)
    _quick(alice, d(10), protein_g=80, sodium_mg=2500)
    _quick(alice, d(12), protein_g=60)
    _quick(alice, d(13), protein_g=55, sodium_mg=1000)
    _quick(alice, d(14), protein_g=70, sodium_mg=1800)
    _quick(alice, d(15), protein_g=10, sodium_mg=500)
    return alice


class TestAdherence:
    def test_statuses_match_the_hand_built_week(self, week):
        result = adherence.adherence(week, 7, today=TODAY)
        by = {n["nutrient"]: n for n in result["nutrients"]}
        assert (by["protein_g"]["met"], by["protein_g"]["missed"], by["protein_g"]["uncertain"]) == (4, 1, 0)
        assert (by["sodium_mg"]["met"], by["sodium_mg"]["missed"], by["sodium_mg"]["uncertain"]) == (3, 1, 1)
        assert by["protein_g"]["not_logged"] == by["sodium_mg"]["not_logged"] == 1

    def test_days_without_entries_dont_lower_the_percentage(self, week):
        by = {n["nutrient"]: n for n in adherence.adherence(week, 7, today=TODAY)["nutrients"]}
        assert by["protein_g"]["judged"] == 5 and by["protein_g"]["pct"] == 80
        assert by["sodium_mg"]["judged"] == 5 and by["sodium_mg"]["pct"] == 60

    def test_streaks_reset_on_a_miss_and_skip_unlogged_days(self, week):
        by = {n["nutrient"]: n for n in adherence.adherence(week, 7, today=TODAY)["nutrients"]}
        # protein: met, MISSED, (not logged), met, met, met, (pending today) → 3 / best 3
        assert (by["protein_g"]["current_streak"], by["protein_g"]["best_streak"]) == (3, 3)
        # sodium: met, MISSED, (not logged), (uncertain), met, met, (pending) → 2 / best 2
        assert (by["sodium_mg"]["current_streak"], by["sodium_mg"]["best_streak"]) == (2, 2)

    def test_calendar_and_all_met_days(self, week):
        result = adherence.adherence(week, 7, today=TODAY)
        assert [c["status"] for c in result["calendar"]] == [
            "all_met", "none_met", "not_logged", "some_met", "all_met", "all_met", "pending",
        ]
        assert result["all_met_days"] == 3
        assert result["logged_days"] == 6
        assert result["calendar"][-1]["today"]

    def test_an_exceeded_maximum_today_is_already_missed(self, week):
        _quick(week, TODAY, sodium_mg=1600)  # 500 + 1600 > 2000
        by = {n["nutrient"]: n for n in adherence.adherence(week, 7, today=TODAY)["nutrients"]}
        assert by["sodium_mg"]["missed"] == 2
        assert by["sodium_mg"]["current_streak"] == 0

    def test_api_window_and_validation(self, alice):
        c = _client(alice)
        r = c.get(f"{API}adherence/?days=30")
        assert r.status_code == 200 and len(r.data["calendar"]) == 30
        assert c.get(f"{API}adherence/?days=12").status_code == 400


class TestJudge:
    @pytest.mark.parametrize("kind,low,high,value,missing,expected", [
        ("at_least", 50, None, 60, 0, "met"),
        ("at_least", 50, None, 60, 2, "met"),  # more data can only add
        ("at_least", 50, None, 40, 0, "missed"),
        ("at_least", 50, None, 40, 1, "uncertain"),
        ("at_most", None, 100, 120, 1, "missed"),  # already over, missing data can't rescue it
        ("at_most", None, 100, 80, 1, "uncertain"),
        ("at_most", None, 100, 80, 0, "met"),
        ("between", 50, 100, 70, 0, "met"),
        ("between", 50, 100, 70, 1, "uncertain"),
        ("between", 50, 100, 40, 0, "missed"),
    ])
    def test_verdicts(self, kind, low, high, value, missing, expected):
        goal = NutrientGoal(nutrient="protein_g", kind=kind, min_value=low, max_value=high)
        assert nutrition.judge(goal, nutrition.Total(value, missing)) == expected


# ═════════════════════════════════════════════════════════════════════════
# #83 — Ritual planner
# ═════════════════════════════════════════════════════════════════════════


class TestPlanner:
    def _plan(self, c, spell, **body):
        return c.post(f"{API}plan/", {"spell": str(spell.uuid), **body}, format="json")

    def test_plan_edit_and_remove(self, alice, porridge):
        c = _client(alice)
        day = _today(alice) + timedelta(days=2)
        r = self._plan(c, porridge, date=str(day), slot="breakfast", quantity=1, unit="serving")
        assert r.status_code == 201, r.data
        meal = r.data["uuid"]
        assert c.patch(f"{API}plan/{meal}/", {"slot": "lunch", "quantity": 2}, format="json").data["slot"] == "lunch"
        listed = c.get(f"{API}plan/?from={day}&to={day}").data
        assert [(m["slot"], m["quantity"]) for m in listed] == [("lunch", 2)]
        assert c.delete(f"{API}plan/{meal}/").status_code == 204
        assert c.get(f"{API}plan/?from={day}&to={day}").data == []

    def test_projected_totals_match_a_hand_computed_plan(self, alice, porridge):
        c = _client(alice)
        day = _today(alice) + timedelta(days=1)
        NutrientGoal.objects.create(user=alice, nutrient="kcal", kind="at_most", max_value=1000,
                                    valid_from=_today(alice))
        self._plan(c, porridge, date=str(day), slot="breakfast", quantity=1, unit="serving")
        self._plan(c, porridge, date=str(day), slot="dinner", quantity=60, unit="g")
        _log(c, source="quick", label="Snack", values={"kcal": 200}, eaten_at=f"{day}T16:00")
        d = c.get(f"{API}plan/day/?date={day}").data
        assert [m["slot"] for m in d["meals"]] == ["breakfast", "dinner"]
        assert d["meals"][0]["nutrients"]["kcal"] == pytest.approx(460.5)
        projected = {n["key"]: n["value"] for n in d["projected"]}
        assert projected["kcal"] == pytest.approx(460.5 + 230.25 + 200)
        assert {g["nutrient"]: g["status"] for g in d["goals"]} == {"kcal": "met"}  # 890.75 ≤ 1000

    def test_i_ate_this_creates_the_entry_and_marks_it_eaten(self, alice, porridge):
        c = _client(alice)
        day = _today(alice) - timedelta(days=1)
        meal = self._plan(c, porridge, date=str(day), slot="lunch", quantity=1, unit="serving").data
        r = c.post(f"{API}plan/{meal['uuid']}/eat/", {"quantity": 1.5}, format="json")
        assert r.status_code == 201, r.data
        assert r.data["meal"]["eaten"] is True
        assert r.data["entry"]["local_date"] == str(day)
        assert r.data["entry"]["local_time"] == "12:00"  # a past day defaults to noon
        assert r.data["entry"]["nutrients"]["kcal"] == pytest.approx(690.75)
        assert c.post(f"{API}plan/{meal['uuid']}/eat/", {}, format="json").status_code == 409
        # Eaten meals count once: as the entry, not again as planned.
        d = c.get(f"{API}plan/day/?date={day}").data
        assert {n["key"]: n["value"] for n in d["projected"]}["kcal"] == pytest.approx(690.75)
        # Deleting the entry makes the meal planned again.
        c.delete(f"{API}intake/{r.data['entry']['uuid']}/")
        assert c.get(f"{API}plan/day/?date={day}").data["meals"][0]["eaten"] is False

    def test_a_spell_made_private_by_its_author_cant_be_eaten_from_the_plan(self, alice, bob, porridge):
        porridge.visibility = Spell.Visibility.PUBLIC
        porridge.save()
        c = _client(bob)
        meal = self._plan(c, porridge, slot="lunch").data
        porridge.visibility = Spell.Visibility.PRIVATE
        porridge.save()
        assert c.post(f"{API}plan/{meal['uuid']}/eat/", {}, format="json").status_code == 409
        day = c.get(f"{API}plan/day/").data
        assert day["meals"][0]["available"] is False
        assert day["meals"][0]["spell_title"] == "Porridge"  # the planner's own snapshot

    def test_range_is_capped(self, alice):
        assert _client(alice).get(f"{API}plan/?from=2026-01-01&to=2026-06-01").status_code == 400

    def test_listing_follows_the_meal_order_not_the_alphabet(self, alice, porridge):
        c = _client(alice)
        for slot in ("snack", "dinner", "lunch", "breakfast"):
            self._plan(c, porridge, slot=slot)
        day = _today(alice)
        assert [m["slot"] for m in c.get(f"{API}plan/?from={day}&to={day}").data] == [
            "breakfast", "lunch", "dinner", "snack",
        ]

    def test_an_eaten_meal_cant_be_edited(self, alice, porridge):
        c = _client(alice)
        meal = self._plan(c, porridge, slot="lunch").data
        c.post(f"{API}plan/{meal['uuid']}/eat/", {}, format="json")
        assert c.patch(f"{API}plan/{meal['uuid']}/", {"quantity": 3}, format="json").status_code == 409

    def test_another_users_plan_is_404(self, alice, bob, porridge):
        meal = self._plan(_client(alice), porridge, slot="lunch").data
        c = _client(bob)
        assert c.delete(f"{API}plan/{meal['uuid']}/").status_code == 404
        assert c.post(f"{API}plan/{meal['uuid']}/eat/", {}, format="json").status_code == 404

    def test_journal_page_prefills_a_readable_spell_only(self, alice, bob, porridge):
        web = Client()
        web.force_login(alice)
        html = web.get(f"/cauldron/journal/?plan_spell={porridge.uuid}").content.decode()
        assert '"mode": "plan"' in html and "Porridge" in html
        web.force_login(bob)
        html = web.get(f"/cauldron/journal/?log_spell={porridge.uuid}").content.decode()
        assert "Porridge" not in html

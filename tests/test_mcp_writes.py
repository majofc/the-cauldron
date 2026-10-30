"""The Cauldron's MCP server (#89), phase 2: write tools.

Two-step confirmation (preview → single-use token bound to user, tool, target
and arguments, 5 minutes), ``client_request_id`` idempotency, ``channel="mcp"``
on everything created, AI-estimated nutrients, server-side duplicate checks,
and per tool: happy path, someone else's data, missing consent, bad input.
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    Casting,
    IntakeEntry,
    McpConfirmation,
    McpIdempotency,
    NutrientGoal,
    PlannedMeal,
    Reagent,
    Spell,
    Tag,
)
from the_cauldron.services import legal, nutrition
from the_cauldron.tests.test_mcp_server import (  # noqa: F401  (fixtures)
    _call,
    _err,
    _ok,
    _reagent,
    _rpc,
    _spell,
    _token,
    _user,
    alice,
    bob,
    plain_static_storage,
)


@pytest.fixture
def egg(db):
    return _reagent("Egg", kcal=143, protein_g=12.6)


@pytest.fixture
def porridge(alice):
    oats = _reagent("Oats", kcal=389, protein_g=16.9)
    return _spell(alice, "Porridge", [oats], cooked_yield_g=200)


def _confirmed(token, name, args):
    """Preview, then apply with the token; returns (preview, applied)."""
    preview = _ok(token, name, args)
    assert preview["status"] == "confirmation_required" and preview["expires_in_seconds"] == 300
    applied = _ok(token, name, {**args, "confirm_token": preview["confirm_token"]})
    return preview, applied


def _quick_entry(token, **extra):
    return _ok(token, "log_meal", {"label": "Tacos", "nutrients": {"kcal": 500}, **extra})["logged"]


# ═════════════════════════════════════════════════════════════════════════
# Two-step confirmation
# ═════════════════════════════════════════════════════════════════════════


class TestTwoStep:
    def test_preview_changes_nothing_then_the_token_applies(self, alice):
        token = _token(alice)
        entry = _quick_entry(token)
        preview = _ok(token, "update_meal", {"id": entry["id"], "nutrients": {"kcal": 700}})
        assert preview["preview"]["meal"]["nutrients"]["kcal"] == 500
        assert preview["preview"]["changes"] == {"nutrients": {"kcal": 700}}
        assert IntakeEntry.objects.get(uuid=entry["id"]).nutrients["kcal"] == 500
        applied = _ok(token, "update_meal", {"id": entry["id"], "nutrients": {"kcal": 700},
                                             "confirm_token": preview["confirm_token"]})
        assert applied["updated"]["nutrients"]["kcal"] == 700
        assert IntakeEntry.objects.get(uuid=entry["id"]).nutrients["kcal"] == 700

    def test_tokens_are_single_use(self, alice):
        token = _token(alice)
        entry = _quick_entry(token)
        args = {"id": entry["id"], "nutrients": {"kcal": 700}}
        preview, _applied = _confirmed(token, "update_meal", args)
        again = _err(token, "update_meal", {**args, "confirm_token": preview["confirm_token"]})
        assert again["code"] == "invalid_confirm_token"

    def test_tokens_are_bound_to_arguments_tool_and_target(self, alice):
        token = _token(alice)
        first, second = _quick_entry(token), _quick_entry(token)
        raw = _ok(token, "update_meal", {"id": first["id"], "nutrients": {"kcal": 700}})["confirm_token"]
        assert _err(token, "update_meal", {"id": first["id"], "nutrients": {"kcal": 9999}, "confirm_token": raw})["code"] == "invalid_confirm_token"
        assert _err(token, "update_meal", {"id": second["id"], "nutrients": {"kcal": 700}, "confirm_token": raw})["code"] == "invalid_confirm_token"
        assert _err(token, "delete_meal", {"id": first["id"], "confirm_token": raw})["code"] == "invalid_confirm_token"
        assert IntakeEntry.objects.get(uuid=first["id"]).nutrients["kcal"] == 500
        # Still unused: the right call works.
        assert _ok(token, "update_meal", {"id": first["id"], "nutrients": {"kcal": 700}, "confirm_token": raw})

    def test_tokens_expire(self, alice):
        token = _token(alice)
        entry = _quick_entry(token)
        raw = _ok(token, "delete_meal", {"id": entry["id"]})["confirm_token"]
        McpConfirmation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert _err(token, "delete_meal", {"id": entry["id"], "confirm_token": raw})["code"] == "invalid_confirm_token"
        assert IntakeEntry.objects.filter(uuid=entry["id"]).exists()

    def test_another_user_cant_use_the_token(self, alice, bob):
        entry = _quick_entry(_token(alice))
        raw = _ok(_token(alice), "delete_meal", {"id": entry["id"]})["confirm_token"]
        assert _err(_token(bob), "delete_meal", {"id": entry["id"], "confirm_token": raw})["code"] == "not_found"
        assert IntakeEntry.objects.filter(uuid=entry["id"]).exists()

    def test_only_the_hash_is_stored(self, alice):
        token = _token(alice)
        raw = _ok(token, "delete_meal", {"id": _quick_entry(token)["id"]})["confirm_token"]
        assert not McpConfirmation.objects.filter(token_hash=raw).exists()

    def test_a_failed_apply_keeps_the_token(self, alice, monkeypatch):
        token = _token(alice)
        entry = _quick_entry(token)
        raw = _ok(token, "delete_meal", {"id": entry["id"]})["confirm_token"]
        with monkeypatch.context() as m:
            m.setattr(IntakeEntry, "delete", lambda self: (_ for _ in ()).throw(RuntimeError("db down")))
            assert _err(token, "delete_meal", {"id": entry["id"], "confirm_token": raw})["code"] == "internal_error"
        assert _ok(token, "delete_meal", {"id": entry["id"], "confirm_token": raw})["deleted"] == entry["id"]

    def test_edit_tools_are_marked_and_take_a_token(self, alice):
        tools = {t["name"]: t for t in _rpc(_token(alice), "tools/list").json()["result"]["tools"]}
        two_step = {"update_meal", "delete_meal", "update_planned_meal", "delete_planned_meal",
                    "update_reagent", "update_spell", "delete_spell"}
        for name in two_step:
            assert "confirm_token" in tools[name]["inputSchema"]["properties"], name
            assert tools[name]["annotations"]["readOnlyHint"] is False
        for name in ("delete_meal", "delete_planned_meal", "delete_spell"):
            assert tools[name]["annotations"]["destructiveHint"] is True
        for name in ("log_meal", "plan_meal", "create_spell", "create_reagent", "set_goal"):
            assert "confirm_token" not in tools[name]["inputSchema"]["properties"]
            assert "client_request_id" in tools[name]["inputSchema"]["properties"]

    def test_write_tools_need_the_write_scope(self, alice):
        r = _rpc(_token(alice, scope="cauldron:read"), "tools/call",
                 {"name": "log_meal", "arguments": {"label": "x", "nutrients": {"kcal": 1}}})
        assert r.status_code == 403 and 'error="insufficient_scope"' in r["WWW-Authenticate"]
        assert not IntakeEntry.objects.exists()

    def test_analytics_marks_the_step(self, alice, monkeypatch):
        events = []
        monkeypatch.setattr("posthog.capture", lambda event, **kw: events.append((event, kw)))
        token = _token(alice)
        entry = _quick_entry(token)
        _confirmed(token, "delete_meal", {"id": entry["id"]})
        steps = [kw["properties"]["confirm_step"] for e, kw in events if e == "mcp_tool_called"]
        assert steps == [None, "preview", "applied"]
        assert "Tacos" not in json.dumps(events)


class TestIdempotency:
    def test_a_retry_returns_the_first_result(self, alice):
        token = _token(alice)
        args = {"label": "Tacos", "nutrients": {"kcal": 500}, "client_request_id": "r-1"}
        first = _ok(token, "log_meal", args)
        again = _ok(token, "log_meal", args)
        assert again == first
        assert IntakeEntry.objects.count() == 1

    def test_ids_are_per_tool_and_expire(self, alice, porridge):
        token = _token(alice)
        _ok(token, "log_meal", {"label": "Tacos", "nutrients": {"kcal": 500}, "client_request_id": "r-1"})
        _ok(token, "plan_meal", {"spell_id": str(porridge.uuid), "slot": "lunch", "client_request_id": "r-1"})
        assert PlannedMeal.objects.count() == 1
        McpIdempotency.objects.update(created_at=timezone.now() - timedelta(minutes=11))
        _ok(token, "log_meal", {"label": "Tacos", "nutrients": {"kcal": 500}, "client_request_id": "r-1"})
        assert IntakeEntry.objects.count() == 2

    def test_errors_are_not_remembered(self, alice):
        token = _token(alice)
        _err(token, "log_meal", {"label": "Tacos", "client_request_id": "r-2"})  # no values
        _ok(token, "log_meal", {"label": "Tacos", "nutrients": {"kcal": 1}, "client_request_id": "r-2"})
        assert IntakeEntry.objects.count() == 1

    def test_other_users_ids_dont_collide(self, alice, bob):
        args = {"label": "Tacos", "nutrients": {"kcal": 500}, "client_request_id": "same"}
        _ok(_token(alice), "log_meal", args)
        _ok(_token(bob), "log_meal", args)
        assert IntakeEntry.objects.filter(user=bob).count() == 1


# ═════════════════════════════════════════════════════════════════════════
# Meal log, plan, goals
# ═════════════════════════════════════════════════════════════════════════


class TestMealLog:
    def test_log_a_spell_an_ingredient_and_an_estimated_quick_entry(self, alice, porridge, egg):
        token = _token(alice)
        spell = _ok(token, "log_meal", {"spell_id": str(porridge.uuid), "quantity": 1, "unit": "serving"})
        # 100 g of oats cook into 200 g for 2 servings → 100 g, 194.5 kcal a serving.
        assert spell["logged"]["source"] == "spell" and spell["logged"]["grams"] == 100
        assert spell["day"]["totals"]["values"]["kcal"] == pytest.approx(194.5)
        reagent = _ok(token, "log_meal", {"reagent_id": str(egg.uuid), "quantity": 2, "unit": "piece",
                                          "eaten_at": f"{nutrition.today_for(alice)}T08:30"})
        assert reagent["logged"]["time"] == "08:30" and reagent["logged"]["grams"] == 100
        quick = _ok(token, "log_meal", {"label": "Pozole", "nutrients": {"kcal": 450}, "estimated": True})
        assert quick["logged"]["estimated"] is True
        assert set(IntakeEntry.objects.values_list("channel", flat=True)) == {"mcp"}
        web = APIClient()
        web.force_authenticate(alice)
        rows = web.get(f"/cauldron/api/intake/day/?date={nutrition.today_for(alice)}").json()["entries"]
        assert [r["estimated"] for r in rows if r["label"] == "Pozole"] == [True]

    def test_exactly_one_source(self, alice, egg):
        token = _token(alice)
        assert _err(token, "log_meal", {"quantity": 1})["code"] == "invalid_arguments"
        assert _err(token, "log_meal", {"label": "x", "reagent_id": str(egg.uuid), "quantity": 1})["code"] == "invalid_arguments"

    def test_someone_elses_private_spell_is_refused(self, alice, bob):
        secret = _spell(bob, "Secret", [_reagent("Salt", kcal=0)], cooked_yield_g=100)
        err = _err(_token(alice), "log_meal", {"spell_id": str(secret.uuid)})
        assert err["code"] == "invalid_input" and not IntakeEntry.objects.exists()

    def test_bad_values_point_at_the_field(self, alice):
        err = _err(_token(alice), "log_meal", {"label": "x", "nutrients": {"kcal": -5}})
        assert err["code"] == "invalid_arguments"

    def test_update_and_delete_only_own(self, alice, bob):
        entry = _quick_entry(_token(bob))
        assert _err(_token(alice), "update_meal", {"id": entry["id"], "nutrients": {"kcal": 1}})["code"] == "not_found"
        assert _err(_token(alice), "delete_meal", {"id": entry["id"]})["code"] == "not_found"
        _p, done = _confirmed(_token(bob), "delete_meal", {"id": entry["id"]})
        assert done["deleted"] == entry["id"] and not IntakeEntry.objects.exists()

    def test_estimated_survives_an_edit(self, alice):
        token = _token(alice)
        entry = _quick_entry(token, estimated=True)
        _confirmed(token, "update_meal", {"id": entry["id"], "quantity": 2})
        assert IntakeEntry.objects.get(uuid=entry["id"]).basis == {"estimated": True}

    @pytest.mark.parametrize("name,args", [
        ("log_meal", {"label": "x", "nutrients": {"kcal": 1}}),
        ("set_goal", {"nutrient": "kcal", "kind": "at_most", "max": 2000}),
    ])
    def test_need_consent(self, db, name, args):
        user = _user("noconsent", consent=False)
        legal.accept(user, legal.GATED)
        assert _err(_token(user), name, args)["code"] == "consent_required"
        assert not IntakeEntry.objects.exists() and not NutrientGoal.objects.exists()


class TestPlan:
    def test_plan_change_eat_and_remove(self, alice, porridge):
        token = _token(alice)
        today = nutrition.today_for(alice)
        planned = _ok(token, "plan_meal", {"spell_id": str(porridge.uuid), "slot": "breakfast"})["planned"]
        assert planned["date"] == str(today) and planned["title"] == "Porridge"
        assert PlannedMeal.objects.get().channel == "mcp"
        _p, moved = _confirmed(token, "update_planned_meal", {"id": planned["id"], "slot": "dinner"})
        assert moved["updated"]["slot"] == "dinner"
        eaten = _ok(token, "mark_planned_meal_eaten", {"id": planned["id"]})
        assert eaten["planned"]["eaten"] is True and eaten["logged"]["source"] == "spell"
        assert IntakeEntry.objects.get().channel == "mcp"
        assert _err(token, "mark_planned_meal_eaten", {"id": planned["id"]})["code"] == "conflict"
        assert _err(token, "update_planned_meal", {"id": planned["id"], "slot": "lunch"})["code"] == "conflict"
        _p, gone = _confirmed(token, "delete_planned_meal", {"id": planned["id"]})
        assert gone["removed"] == planned["id"] and not PlannedMeal.objects.exists()

    def test_plan_needs_a_readable_spell(self, alice, bob):
        secret = _spell(bob, "Secret", [_reagent("Salt", kcal=0)])
        assert _err(_token(alice), "plan_meal", {"spell_id": str(secret.uuid), "slot": "lunch"})["code"] == "invalid_input"

    def test_someone_elses_plan_is_not_found(self, alice, bob, porridge):
        meal = PlannedMeal.objects.create(user=alice, date=nutrition.today_for(alice), slot="lunch",
                                          spell=porridge, spell_title="Porridge", quantity=1)
        for name in ("update_planned_meal", "delete_planned_meal", "mark_planned_meal_eaten"):
            assert _err(_token(bob), name, {"id": str(meal.uuid)})["code"] == "not_found"


class TestGoals:
    def test_set_change_and_clear(self, alice):
        token = _token(alice)
        _ok(token, "set_goal", {"nutrient": "protein_g", "kind": "at_least", "min": 100})
        d = _ok(token, "set_goal", {"nutrient": "kcal", "kind": "between", "min": 1800, "max": 2200})
        assert {g["nutrient"] for g in d["goals"]} == {"kcal", "protein_g"}  # the other goal stays
        assert set(NutrientGoal.objects.values_list("channel", flat=True)) == {"mcp"}
        cleared = _ok(token, "set_goal", {"nutrient": "protein_g", "kind": "none"})
        assert [g["nutrient"] for g in cleared["goals"]] == ["kcal"]

    def test_invalid_goal(self, alice):
        assert _err(_token(alice), "set_goal", {"nutrient": "kcal", "kind": "between", "min": 2000})["code"] == "invalid_input"


# ═════════════════════════════════════════════════════════════════════════
# Grimoire
# ═════════════════════════════════════════════════════════════════════════


class TestReagents:
    def test_create_private_estimated(self, alice):
        d = _ok(_token(alice), "create_reagent", {
            "name": "Tamal de rajas", "category": "grain", "nutrients_per_100g": {"kcal": 220, "fat_g": 9},
            "grams_per_unit": {"piece": 150}, "estimated": True,
        })["created"]
        r = Reagent.objects.get(uuid=d["id"])
        assert (r.status, r.source, r.channel, r.nutrients_estimated) == ("private", "user", "mcp", True)
        assert d["estimated"] is True and d["per_100g"]["kcal"] == 220 and d["per_100g"]["protein_g"] is None

    def test_duplicates_are_returned_not_created(self, alice, bob, monkeypatch):
        events = []
        monkeypatch.setattr("posthog.capture", lambda event, **kw: events.append((event, kw)))
        _reagent("Egg", kcal=143)  # public catalog
        _reagent("Salsa macha", owner=alice, status=Reagent.Status.PRIVATE)
        _reagent("Bob's secret", owner=bob, status=Reagent.Status.PRIVATE)
        token = _token(alice)
        for name in ("egg", "Salsa Macha"):
            err = _err(token, "create_reagent", {"name": name})
            assert err["code"] == "duplicate" and len(err["matches"]) == 1
        assert _ok(token, "create_reagent", {"name": "Bob's secret"})  # not visible → not a duplicate
        assert _ok(token, "create_reagent", {"name": "Egg", "confirm_new": True})
        assert Reagent.objects.filter(name__iexact="egg").count() == 2
        dupes = [kw["properties"] for e, kw in events if e == "mcp_duplicate_prevented"]
        assert dupes == [{"kind": "reagent", "overridden": False}] * 2 + [{"kind": "reagent", "overridden": True}]

    def test_update_only_own_unapproved(self, alice, bob):
        mine = _reagent("Mine", owner=alice, status=Reagent.Status.PRIVATE, kcal=10)
        approved = _reagent("Catalog", kcal=10)
        theirs = _reagent("Theirs", owner=bob, status=Reagent.Status.PRIVATE, kcal=10)
        token = _token(alice)
        _p, done = _confirmed(token, "update_reagent", {"id": str(mine.uuid), "nutrients_per_100g": {"kcal": 20}})
        assert done["updated"]["per_100g"]["kcal"] == 20
        assert _err(token, "update_reagent", {"id": str(approved.uuid), "name": "x"})["code"] == "forbidden"
        assert _err(token, "update_reagent", {"id": str(theirs.uuid), "name": "x"})["code"] == "not_found"

    def test_estimated_flag_is_read_only_on_the_web(self, alice):
        mine = _reagent("Mine", owner=alice, status=Reagent.Status.PRIVATE, kcal=10)
        web = APIClient()
        web.force_authenticate(alice)
        web.patch(f"/cauldron/api/grimoire/reagents/{mine.uuid}/", {"nutrients_estimated": True}, format="json")
        mine.refresh_from_db()
        assert mine.nutrients_estimated is False


class TestSpells:
    def _lines(self, *reagents):
        return [{"reagent_id": str(r.uuid), "quantity": 100, "unit": "g"} for r in reagents]

    def test_create_private_with_lines(self, alice, egg):
        Tag.objects.create(slug="breakfast", name="Breakfast", name_es="Desayuno")
        d = _ok(_token(alice), "create_spell", {
            "title": "Huevos rancheros", "servings": 2, "lines": self._lines(egg), "steps": ["Fry", "Serve"],
            "tags": ["breakfast"],
        })["created"]
        spell = Spell.objects.get(uuid=d["id"])
        assert (spell.visibility, spell.channel, spell.owner_id) == ("private", "mcp", alice.pk)
        assert d["lines"][0]["reagent"] == "Egg" and d["text"]["steps"] == ["Fry", "Serve"]

    def test_duplicate_titles_are_returned(self, alice, bob, egg):
        _spell(bob, "Shakshuka", [egg], visibility=Spell.Visibility.PUBLIC)
        _spell(bob, "Hidden stew", [egg])
        token = _token(alice)
        err = _err(token, "create_spell", {"title": "shakshuka", "servings": 2, "lines": self._lines(egg)})
        assert err["code"] == "duplicate" and err["matches"][0]["untrusted_text"]["title"] == "Shakshuka"
        assert _ok(token, "create_spell", {"title": "Hidden stew", "servings": 1, "lines": self._lines(egg)})
        assert _ok(token, "create_spell", {"title": "Shakshuka", "servings": 2, "lines": self._lines(egg),
                                           "confirm_new": True})
        assert Spell.objects.filter(owner=alice).count() == 2

    def test_lines_are_validated_like_the_web(self, alice, bob):
        secret = _reagent("Secret", owner=bob, status=Reagent.Status.PRIVATE)
        err = _err(_token(alice), "create_spell", {"title": "X", "servings": 1, "lines": self._lines(secret)})
        assert err["code"] == "invalid_input" and "lines" in err["fields"]
        assert _err(_token(alice), "create_spell", {"title": "X", "servings": 1, "lines": [], "tags": ["nope"]})["code"] == "invalid_arguments"

    def test_update_delete_owner_only_fork_for_others(self, alice, bob, egg):
        public = _spell(bob, "Bob's soup", [egg], visibility=Spell.Visibility.PUBLIC)
        token = _token(alice)
        assert _err(token, "update_spell", {"id": str(public.uuid), "title": "Mine now"})["code"] == "forbidden"
        assert _err(token, "delete_spell", {"id": str(public.uuid)})["code"] == "forbidden"
        copy = _ok(token, "fork_spell", {"id": str(public.uuid)})["created"]
        forked = Spell.objects.get(uuid=copy["id"])
        assert (forked.owner_id, forked.visibility, forked.channel) == (alice.pk, "private", "mcp")
        _p, done = _confirmed(token, "update_spell", {"id": copy["id"], "title": "Alice's soup", "servings": 4})
        assert done["updated"]["text"]["title"] == "Alice's soup" and done["updated"]["servings"] == 4
        _p, gone = _confirmed(token, "delete_spell", {"id": copy["id"]})
        assert gone["deleted"] == copy["id"] and Spell.objects.filter(uuid=public.uuid).exists()

    def test_estimated_ingredients_flag_the_spell(self, alice):
        guess = _reagent("Guess", owner=alice, status=Reagent.Status.PRIVATE, kcal=100)
        Reagent.objects.filter(pk=guess.pk).update(nutrients_estimated=True)
        spell = _spell(alice, "Guesswork", [guess], cooked_yield_g=100)
        assert _ok(_token(alice), "get_spell", {"id": str(spell.uuid)})["estimated_nutrition"] is True
        web = APIClient()
        web.force_authenticate(alice)
        assert web.get(f"/cauldron/api/grimoire/spells/{spell.uuid}/").json()["has_estimated_nutrition"] is True
        logged = _ok(_token(alice), "log_meal", {"spell_id": str(spell.uuid)})["logged"]
        assert logged["estimated"] is True


class TestCastings:
    def test_log_casting(self, alice, porridge, bob):
        d = _ok(_token(alice), "log_casting", {"spell_id": str(porridge.uuid), "score": 4, "cooked_weight_g": 450,
                                               "notes": "less salt"})["logged"]
        casting = Casting.objects.get(uuid=d["id"])
        assert (casting.user_id, casting.channel, casting.score) == (alice.pk, "mcp", 4)
        assert d["cast_on"] == str(nutrition.today_for(alice))
        assert _err(_token(bob), "log_casting", {"spell_id": str(porridge.uuid)})["code"] == "not_found"
        assert _err(_token(alice), "log_casting", {"spell_id": str(porridge.uuid), "score": 9})["code"] == "invalid_arguments"

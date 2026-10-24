"""Added sugars as a tracked nutrient (#104).

``added_sugar_g`` sits right after ``sugar_g`` in ``ReagentNutrients.FIELDS``,
so totals, intake snapshots, rolling windows and goals carry it. Unknown stays
unknown (never 0), and it can never be more than the total sugars.
"""

import importlib
import json

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.management.commands import build_grimoire_reagents as builder
from the_cauldron.management.commands.seed_grimoire import SEED_DIR
from the_cauldron.models import IntakeEntry, Reagent, ReagentNutrients, Spell, SpellReagent
from the_cauldron.services import legal, nutrition
from the_cauldron.tests.test_mcp_server import _err, _ok, _rpc, _token

User = get_user_model()
API = "/cauldron/api/"
GRIMOIRE = f"{API}grimoire/"


@pytest.fixture
def alice(db):
    user = User.objects.create_user(username="alice", email="alice@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
    return user


@pytest.fixture
def client(alice):
    c = APIClient()
    c.force_authenticate(user=alice)
    return c


def _reagent(name, **nutrients):
    r = Reagent.objects.create(name=name, status=Reagent.Status.APPROVED, grams_per_unit={"piece": 50})
    ReagentNutrients.objects.create(reagent=r, **nutrients)
    return r


def _food(category, description, **numbers):
    """A minimal SR Legacy food record."""
    nutrients = [{"nutrient": {"number": n}, "amount": v} for n, v in {"269": 4.0, **numbers}.items()]
    return {"fdcId": 1, "description": description, "foodCategory": {"description": category},
            "foodNutrients": nutrients, "foodPortions": []}


def _added(food):
    return builder.build_entry(food, "x", "x", "other", None)["nutrients"]["added_sugar_g"]


class TestUsdaBuilder:
    def test_unprocessed_categories_default_to_zero(self):
        assert _added(_food("Vegetables and Vegetable Products", "Carrots, raw")) == 0
        assert _added(_food("Fruits and Fruit Juices", "Apricots, canned, juice pack, unsweetened")) == 0

    def test_sweetened_items_in_those_categories_stay_unknown(self):
        for description in ("Cranberries, dried, sweetened", "Peaches, canned, heavy syrup",
                            "Nuts, almonds, honey roasted", "Yogurt, fruit variety, nonfat",
                            "Salad dressing, french dressing", "Peanut butter, smooth style",
                            "Beans, baked, canned, with pork and sweet sauce", "Apricot nectar, canned"):
            assert _added(_food("Fruits and Fruit Juices", description)) is None, description

    def test_other_categories_stay_unknown(self):
        assert _added(_food("Sweets", "Sugars, granulated")) is None
        assert _added(_food("Breakfast Cereals", "Cereals, oats, dry")) is None

    def test_nutrient_539_wins_where_usda_lists_it(self):
        assert _added(_food("Sweets", "Candies, fudge", **{"539": 60.5})) == 60.5
        assert _added(_food("Dairy and Egg Products", "Milk, chocolate", **{"539": 4.2})) == 4.2

    def test_the_committed_seed_file_carries_it(self):
        rows = {r["name"]: r["nutrients"] for r in
                json.loads((SEED_DIR / "grimoire_reagents.json").read_text(encoding="utf-8"))["reagents"]}
        assert all("added_sugar_g" in n for n in rows.values())
        assert rows["Lentils"]["added_sugar_g"] == 0 and rows["Apple"]["added_sugar_g"] == 0
        # Outside the categories but plainly unsweetened (NO_ADDED_SUGAR).
        assert rows["Water"]["added_sugar_g"] == 0 and rows["Rolled oats"]["added_sugar_g"] == 0
        assert rows["Peanut butter"]["added_sugar_g"] is None
        # All of sugar's and honey's sugars count as added (FDA, WHO free sugars).
        for name in ("Sugar", "Honey"):
            assert rows[name]["added_sugar_g"] == rows[name]["sugar_g"] > 0


class TestSeedBackfill:
    def test_seed_fills_it_and_backfills_existing_rows_without_refresh(self, db):
        call_command("seed_grimoire")
        lentils = ReagentNutrients.objects.get(reagent__name="Lentils")
        assert lentils.added_sugar_g == 0
        assert ReagentNutrients.objects.get(reagent__name="Vanilla extract").added_sugar_g is None
        assert ReagentNutrients.objects.get(reagent__name="Mexican Coca-Cola").added_sugar_g == pytest.approx(10.19)

        # Rows seeded before #104 have it unknown; a staff value is never overwritten.
        ReagentNutrients.objects.update(added_sugar_g=None)
        ReagentNutrients.objects.filter(reagent__name="Apple").update(added_sugar_g=1.0)
        call_command("seed_grimoire")
        assert ReagentNutrients.objects.get(reagent__name="Lentils").added_sugar_g == 0
        assert ReagentNutrients.objects.get(reagent__name="Apple").added_sugar_g == 1.0
        assert ReagentNutrients.objects.get(reagent__name="Peanut butter").added_sugar_g is None

    def test_backfill_never_stores_more_than_the_edited_sugars(self, db):
        call_command("seed_grimoire")
        ReagentNutrients.objects.filter(reagent__name="Mexican Coca-Cola").update(sugar_g=9, added_sugar_g=None)
        call_command("seed_grimoire")
        coke = ReagentNutrients.objects.get(reagent__name="Mexican Coca-Cola")
        assert coke.sugar_g == 9 and coke.added_sugar_g is None


class TestReagents:
    def _post(self, client, **nutrients):
        return client.post(f"{GRIMOIRE}reagents/", {"name": "Jam", "nutrients": nutrients}, format="json")

    def test_stored_and_shown_unknown_is_null(self, client):
        r = self._post(client, sugar_g=48, added_sugar_g=40)
        assert r.status_code == 201, r.data
        assert r.data["nutrients"]["added_sugar_g"] == 40
        r = client.post(f"{GRIMOIRE}reagents/", {"name": "Plum", "nutrients": {"sugar_g": 10}}, format="json")
        assert r.data["nutrients"]["added_sugar_g"] is None

    def test_more_than_the_total_sugars_is_rejected(self, client):
        r = self._post(client, sugar_g=10, added_sugar_g=12)
        assert r.status_code == 400
        assert "added_sugar_g" in r.data["nutrients"]
        assert not Reagent.objects.exists()

    def test_patch_is_checked_against_the_stored_sugars(self, client):
        uuid = self._post(client, sugar_g=10, added_sugar_g=5).data["uuid"]
        r = client.patch(f"{GRIMOIRE}reagents/{uuid}/", {"nutrients": {"added_sugar_g": 11}}, format="json")
        assert r.status_code == 400
        r = client.patch(f"{GRIMOIRE}reagents/{uuid}/", {"nutrients": {"added_sugar_g": 9}}, format="json")
        assert r.status_code == 200, r.data
        assert client.get(f"{GRIMOIRE}reagents/{uuid}/").data["nutrients"]["added_sugar_g"] == 9

    def test_only_added_sugars_known_is_fine(self, client):
        assert self._post(client, added_sugar_g=12).status_code == 201

    def test_the_admin_form_rejects_it_too(self, db):
        profile = ReagentNutrients(reagent=Reagent.objects.create(name="Jam"), sugar_g=1, added_sugar_g=2)
        with pytest.raises(ValidationError):
            profile.full_clean()


class TestTotalsAndGoals:
    def test_spell_totals_count_unknown_as_missing(self, client, alice):
        jam = _reagent("Jam", kcal=250, sugar_g=48, added_sugar_g=40)
        plum = _reagent("Plum", kcal=46, sugar_g=10)
        spell = Spell.objects.create(owner=alice, title="Plum jam", servings=2)
        SpellReagent.objects.create(spell=spell, reagent=jam, quantity=100, unit="g", grams=100, order=0)
        SpellReagent.objects.create(spell=spell, reagent=plum, quantity=100, unit="g", grams=100, order=1)
        data = client.get(f"{GRIMOIRE}spells/{spell.uuid}/nutrition/").data
        added = {n["key"]: n for n in data["nutrients"]}["added_sugar_g"]
        assert added["batch"] == pytest.approx(40) and added["missing"] == 1

    def test_journal_totals_and_old_entries_have_it_missing(self, client, alice):
        day = nutrition.today_for(alice)
        jam = _reagent("Jam", kcal=250, sugar_g=48, added_sugar_g=40)
        r = client.post(f"{API}intake/", {"source": "reagent", "reagent": str(jam.uuid),
                                          "quantity": 50, "unit": "g"}, format="json")
        assert r.status_code == 201, r.data
        assert r.data["nutrients"]["added_sugar_g"] == pytest.approx(20)
        # Entries logged before #104: their snapshots never had the field.
        IntakeEntry.objects.create(
            user=alice, eaten_at=r.data["eaten_at"], local_date=day, source="quick", label="Old",
            quantity=1, unit="portion", nutrients={"kcal": 100, "sugar_g": 5}, missing={"protein_g": 1},
        )
        old_jam = IntakeEntry.objects.create(
            user=alice, eaten_at=r.data["eaten_at"], local_date=day, source="reagent", reagent=jam,
            label="Old jam", quantity=10, unit="g", grams=10,
            basis={"per_100g": {"kcal": 250}, "missing": {}}, nutrients={"kcal": 25}, missing={},
        )
        importlib.import_module("the_cauldron.migrations.0033_intake_added_sugar_missing").mark_missing(apps, None)
        old_jam.refresh_from_db()
        assert old_jam.basis["missing"] == {"added_sugar_g": 1}  # rescaling keeps it unknown
        totals = {n["key"]: n for n in client.get(f"{API}intake/day/?date={day}").data["totals"]}
        assert totals["added_sugar_g"]["value"] == pytest.approx(20)
        assert totals["added_sugar_g"]["missing"] == 2  # the old entries: unknown, not 0
        new = IntakeEntry.objects.get(uuid=r.data["uuid"])
        assert "added_sugar_g" not in new.missing  # a known value is left alone

    def test_quick_entry_above_total_sugars_is_rejected(self, client):
        r = client.post(f"{API}intake/", {"source": "quick", "label": "Soda", "quantity": 1, "unit": "portion",
                                          "values": {"sugar_g": 10, "added_sugar_g": 30}}, format="json")
        assert r.status_code == 400
        assert not IntakeEntry.objects.exists()

    def test_a_goal_can_be_set_and_the_rolling_window_tracks_it(self, client, alice):
        r = client.put(f"{API}goals/", {"goals": [
            {"nutrient": "added_sugar_g", "kind": "at_most", "max_value": 25}]}, format="json")
        assert r.status_code == 200, r.data
        assert [(g["nutrient"], g["max_value"]) for g in r.data["goals"]] == [("added_sugar_g", 25)]
        client.post(f"{API}intake/", {"source": "quick", "label": "Soda", "quantity": 1, "unit": "portion",
                                      "values": {"sugar_g": 35, "added_sugar_g": 35}}, format="json")
        roll = client.get(f"{API}rolling/?span=24h&days=7").data
        assert roll["values"]["added_sugar_g"][-1] == pytest.approx(35)
        assert roll["sets"]["mine"]["status"]["added_sugar_g"][-1] == "missed"


class TestMcp:
    def test_schemas_list_it(self, alice):
        tools = {t["name"]: t for t in _rpc(_token(alice), "tools/list").json()["result"]["tools"]}
        per_100g = tools["create_reagent"]["inputSchema"]["properties"]["nutrients_per_100g"]
        assert "added_sugar_g" in per_100g["properties"] and "added_sugar_g" in per_100g["description"]
        assert "added_sugar_g" in tools["update_reagent"]["inputSchema"]["properties"]["nutrients_per_100g"]["properties"]
        assert "added_sugar_g" in tools["log_meal"]["inputSchema"]["properties"]["nutrients"]["properties"]
        assert "added_sugar_g" in tools["set_goal"]["inputSchema"]["properties"]["nutrient"]["enum"]

    def test_create_reagent_round_trips_and_rejects_more_than_sugars(self, alice):
        token = _token(alice)
        created = _ok(token, "create_reagent", {"name": "Jam", "nutrients_per_100g": {"sugar_g": 48, "added_sugar_g": 40}})
        got = _ok(token, "get_reagent", {"id": created["created"]["id"]})
        assert got["per_100g"]["added_sugar_g"] == 40 and got["per_100g"]["fiber_g"] is None
        err = _err(token, "create_reagent", {"name": "Cola", "nutrients_per_100g": {"sugar_g": 5, "added_sugar_g": 9}})
        assert err["code"] == "invalid_input"

    def test_log_meal_rejects_more_than_sugars(self, alice):
        err = _err(_token(alice), "log_meal", {"label": "Soda", "nutrients": {"sugar_g": 5, "added_sugar_g": 9}})
        assert err["code"] == "invalid_input"
        assert not IntakeEntry.objects.exists()


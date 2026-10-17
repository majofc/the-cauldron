"""Grimoire spell nutrition (#70): totals, cooked yield, portion recommender."""

import json
from datetime import date
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.models import Casting, Reagent, ReagentNutrients, Spell, SpellReagent
from the_cauldron.services import grimoire_nutrition as nu
from the_cauldron.services.grimoire_nutrition import Goal, Ingredient, Total

User = get_user_model()
API = "/cauldron/api/grimoire/"
SEED = Path(__file__).resolve().parent.parent / "seed_data"


def _per(**values):
    return {name: values.get(name) for name in nu.FIELDS}


# ── Service: totals ──────────────────────────────────────────────────────────


class TestBatchTotals:
    def test_sums_grams_times_per_100g(self):
        totals = nu.batch_totals([
            Ingredient(key="oats", grams=200, per_100g=_per(kcal=389, protein_g=16.9)),
            Ingredient(key="egg", grams=100, per_100g=_per(kcal=143, protein_g=12.6)),
        ])
        assert totals["kcal"].value == pytest.approx(921)
        assert totals["protein_g"].value == pytest.approx(46.4)
        assert totals["kcal"].complete

    def test_unknown_is_never_zero_it_makes_the_total_incomplete(self):
        totals = nu.batch_totals([
            Ingredient(key="oats", grams=200, per_100g=_per(kcal=389, fiber_g=10.6)),
            Ingredient(key="mystery", grams=50, per_100g=_per(kcal=100)),  # fiber unknown
            Ingredient(key="paste", grams=30, per_100g=None),  # no profile at all
        ])
        assert totals["fiber_g"].value == pytest.approx(21.2)
        assert totals["fiber_g"].missing == 2
        assert not totals["fiber_g"].complete
        assert totals["kcal"].missing == 1  # only the reagent with no profile

    def test_a_reagent_on_two_lines_counts_once_as_missing(self):
        totals = nu.batch_totals([
            Ingredient(key="salt", grams=5, per_100g=None),
            Ingredient(key="salt", grams=2, per_100g=None),
        ])
        assert totals["sodium_mg"].missing == 1

    def test_a_zero_gram_line_is_not_missing(self):
        totals = nu.batch_totals([Ingredient(key="garnish", grams=0, per_100g=None)])
        assert totals["kcal"].complete


# ── Service: yield ───────────────────────────────────────────────────────────


class TestResolveYield:
    def test_order_casting_then_weight_then_pct_then_unknown(self):
        assert nu.resolve_yield(1000, casting_ratio=0.8, cooked_yield_g=900, yield_pct=70).source == "casting"
        assert nu.resolve_yield(1000, cooked_yield_g=900, yield_pct=70).ratio == pytest.approx(0.9)
        assert nu.resolve_yield(1000, yield_pct=70).ratio == pytest.approx(0.7)
        unknown = nu.resolve_yield(1000)
        assert (unknown.source, unknown.ratio, unknown.known) == ("unknown", 1.0, False)


class _Line:
    def __init__(self, reagent, quantity, unit, grams):
        self.reagent, self.quantity, self.unit, self.grams = reagent, quantity, unit, grams


class _Reagent:
    def __init__(self, uuid, grams_per_unit=None, density=None, name="r"):
        self.uuid, self.grams_per_unit, self.density_g_per_ml, self.name = uuid, grams_per_unit or {}, density, name


class TestCastingRawGrams:
    def setup_method(self):
        self.rice = _Reagent("rice", {"cup": 185})
        self.chicken = _Reagent("chicken")
        self.lines = [_Line(self.chicken, 500, "g", 500), _Line(self.rice, 1, "cup", 185)]

    def test_no_adjustments_is_the_spell(self):
        assert nu.casting_raw_grams(self.lines, []) == 685

    def test_same_unit_scales_the_lines_weight(self):
        adj = [{"line": 1, "reagent": "rice", "used_qty": 2, "unit": "cup"}]
        assert nu.casting_raw_grams(self.lines, adj) == 870

    def test_other_unit_goes_through_to_grams(self):
        adj = [{"line": 0, "reagent": "chicken", "used_qty": 1, "unit": "kg"}]
        assert nu.casting_raw_grams(self.lines, adj) == 1185

    def test_unconvertible_amount_keeps_the_spells_weight(self):
        adj = [{"line": 0, "reagent": "chicken", "used_qty": 2, "unit": "cup"}]
        assert nu.casting_raw_grams(self.lines, adj) == 685

    def test_matches_by_reagent_when_the_line_moved(self):
        adj = [{"line": 7, "reagent": "rice", "used_qty": 0.5, "unit": "cup"}]
        assert nu.casting_raw_grams(self.lines, adj) == pytest.approx(592.5)


# ── Service: portion recommender ─────────────────────────────────────────────

# Per 100 g cooked: 20 g protein, 50 mg cholesterol, 300 kcal, no fiber data from one reagent.
PER_100 = {name: Total(0.0) for name in nu.FIELDS}
PER_100.update(protein_g=Total(20.0), cholesterol_mg=Total(50.0), kcal=Total(300.0), fiber_g=Total(2.0, missing=1))


class TestRecommendPortion:
    def test_at_least_gives_a_minimum_and_no_maximum(self):
        p = nu.recommend_portion(PER_100, [Goal("protein_g", at_least=40)])
        assert (p.feasible, p.min_g, p.max_g) == (True, 200, None)

    def test_feasible_set_returns_a_valid_range(self):
        p = nu.recommend_portion(PER_100, [Goal("protein_g", at_least=40), Goal("cholesterol_mg", at_most=200)])
        assert (p.min_g, p.max_g) == (200, 400)
        assert p.conflicts == []

    def test_between_is_both_bounds(self):
        p = nu.recommend_portion(PER_100, [Goal("kcal", at_least=450, at_most=600)])
        assert (p.min_g, p.max_g) == (150, 200)

    def test_infeasible_names_the_conflicting_goals(self):
        p = nu.recommend_portion(PER_100, [
            Goal("protein_g", at_least=60),  # needs 300 g
            Goal("cholesterol_mg", at_most=100),  # allows 200 g
            Goal("kcal", at_most=2000),  # not part of the clash
        ])
        assert not p.feasible
        assert p.conflicts == ["protein_g", "cholesterol_mg"]

    def test_a_minimum_of_a_nutrient_the_dish_lacks_is_impossible(self):
        p = nu.recommend_portion(PER_100, [Goal("vit_c_mg", at_least=10)])
        assert not p.feasible
        assert p.conflicts == ["vit_c_mg"]

    def test_goals_on_incomplete_data_are_flagged(self):
        p = nu.recommend_portion(PER_100, [Goal("fiber_g", at_least=4)])
        assert p.incomplete == ["fiber_g"]
        assert p.min_g == 200


# ── API ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def alice(db):
    return User.objects.create_user(username="alice", email="alice@example.com", password="pw12345!")


@pytest.fixture
def bob(db):
    return User.objects.create_user(username="bob", email="bob@example.com", password="pw12345!")


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
def porridge(alice, db):
    """200 g oats + 2 eggs (100 g), 2 servings — every number below is by hand."""
    oats = _reagent("Oats", kcal=389, protein_g=16.9, carbs_g=66.3, fat_g=6.9, cholesterol_mg=0)
    egg = _reagent("Egg", kcal=143, protein_g=12.6, carbs_g=0.7, fat_g=9.5, cholesterol_mg=372)
    spell = Spell.objects.create(owner=alice, title="Porridge", servings=2)
    SpellReagent.objects.create(spell=spell, reagent=oats, quantity=200, unit="g", grams=200, order=0)
    SpellReagent.objects.create(spell=spell, reagent=egg, quantity=2, unit="piece", grams=100, order=1)
    return spell


def _nutrition(client, spell, query=""):
    return client.get(f"{API}spells/{spell.uuid}/nutrition/{query}")


def _by_key(data):
    return {n["key"]: n for n in data["nutrients"]}


class TestNutritionApi:
    def test_batch_serving_and_per_100g_by_hand(self, alice, porridge):
        porridge.yield_pct = 80  # 300 g raw → 240 g cooked
        porridge.save()
        r = _nutrition(_client(alice), porridge)
        assert r.status_code == 200, r.data
        kcal = _by_key(r.data)["kcal"]
        assert kcal["batch"] == pytest.approx(921)  # 778 + 143
        assert kcal["per_serving"] == pytest.approx(460.5)
        assert kcal["per_100g"] == pytest.approx(383.75)  # 921 / 240 g × 100
        assert r.data["cooked_g"] == 240
        assert r.data["yield"]["source"] == "spell_pct"

    def test_missing_yield_is_flagged_and_uses_raw_weight(self, alice, porridge):
        r = _nutrition(_client(alice), porridge)
        assert r.data["yield"]["source"] == "unknown"
        assert _by_key(r.data)["kcal"]["per_100g"] == pytest.approx(307)  # 921 / 300 g

    def test_a_weighed_casting_changes_per_100g(self, alice, porridge):
        porridge.cooked_yield_g = 280
        porridge.save()
        Casting.objects.create(spell=porridge, user=alice, cast_on=date(2026, 10, 1), cooked_weight_g=250)
        Casting.objects.create(spell=porridge, user=alice, cast_on=date(2026, 10, 9), cooked_weight_g=200)
        r = _nutrition(_client(alice), porridge)
        assert r.data["yield"]["source"] == "casting"
        assert r.data["yield"]["cast_on"] == date(2026, 10, 9)  # the latest weighed one
        assert _by_key(r.data)["kcal"]["per_100g"] == pytest.approx(460.5)  # 921 / 200 g

    def test_a_casting_with_changed_amounts_gives_a_yield_ratio(self, alice, porridge):
        # Cast at double (400 g oats, 4 eggs = 600 g raw) and weighed 480 g → 80 % yield.
        Casting.objects.create(spell=porridge, user=alice, cast_on=date(2026, 10, 9), cooked_weight_g=480, adjustments=[
            {"line": 0, "reagent": str(porridge.lines.all()[0].reagent.uuid), "used_qty": 400, "unit": "g"},
            {"line": 1, "reagent": str(porridge.lines.all()[1].reagent.uuid), "used_qty": 4, "unit": "piece"},
        ])
        r = _nutrition(_client(alice), porridge)
        assert r.data["yield"]["ratio"] == pytest.approx(0.8)
        assert r.data["cooked_g"] == 240

    def test_someone_elses_weighed_casting_is_ignored(self, alice, bob, porridge):
        porridge.visibility = Spell.Visibility.PUBLIC
        porridge.save()
        Casting.objects.create(spell=porridge, user=bob, cast_on=date(2026, 10, 9), cooked_weight_g=100)
        assert _nutrition(_client(alice), porridge).data["yield"]["source"] == "unknown"
        assert _nutrition(_client(bob), porridge).data["yield"]["source"] == "casting"

    def test_missing_data_marks_totals_incomplete(self, alice, porridge):
        # Oats and egg have no sugar data: incomplete, but not named (they have the basics).
        mystery = _reagent("Mystery paste")  # no nutrient profile
        SpellReagent.objects.create(spell=porridge, reagent=mystery, quantity=1, unit="piece", grams=50, order=2)
        r = _nutrition(_client(alice), porridge)
        kcal = _by_key(r.data)["kcal"]
        assert kcal["missing"] == 1
        assert kcal["batch"] == pytest.approx(921)  # unknown is not 0 — it's left out and flagged
        assert [m["name"] for m in r.data["reagents_missing_data"]] == ["Mystery paste"]

    def test_scale_and_servings_change_the_batch_only(self, alice, porridge):
        by_scale = _nutrition(_client(alice), porridge, "?scale=2").data
        by_servings = _nutrition(_client(alice), porridge, "?servings=4").data
        for data in (by_scale, by_servings):
            kcal = _by_key(data)["kcal"]
            assert kcal["batch"] == pytest.approx(1842)
            assert kcal["per_serving"] == pytest.approx(460.5)
            assert kcal["per_100g"] == pytest.approx(307)
            assert data["cooked_g"] == 600

    @pytest.mark.parametrize("query", ["?scale=0", "?scale=-1", "?servings=0", "?scale=abc"])
    def test_bad_scale_is_400(self, alice, porridge, query):
        assert _nutrition(_client(alice), porridge, query).status_code == 400

    def test_private_spell_of_someone_else_is_404(self, bob, porridge):
        assert _nutrition(_client(bob), porridge).status_code == 404

    def test_matches_a_hand_computed_seed_spell(self, alice):
        """The Strawberry Banana Smoothie, computed straight from the seed files."""
        call_command("seed_grimoire")
        reagents = {r["fdc_id"]: r for r in json.loads((SEED / "grimoire_reagents.json").read_text("utf-8"))["reagents"]}
        seed = next(s for s in json.loads((SEED / "grimoire_spells.json").read_text("utf-8"))["spells"]
                    if s["title"] == "Strawberry Banana Smoothie")
        expected = {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0}
        for fdc_id, qty, unit, *_notes in seed["lines"]:
            reagent = reagents[fdc_id]
            per_unit = (reagent.get("grams_per_unit") or {}).get(unit)
            ml = {"tsp": 4.92892, "tbsp": 14.7868, "cup": 236.588, "ml": 1}.get(unit)
            grams = qty if unit == "g" else qty * per_unit if per_unit else qty * ml * reagent["density_g_per_ml"]
            for name in expected:
                expected[name] += grams * reagent["nutrients"][name] / 100
        spell = Spell.objects.get(title="Strawberry Banana Smoothie")
        data = _by_key(_nutrition(_client(alice), spell).data)
        for name, value in expected.items():
            assert data[name]["batch"] == pytest.approx(value, abs=0.01)
            assert data[name]["per_serving"] == pytest.approx(value / seed["servings"], abs=0.01)


class TestYieldFields:
    def test_owner_sets_the_yield_on_the_spell(self, alice, porridge):
        r = _client(alice).patch(f"{API}spells/{porridge.uuid}/", {"cooked_yield_g": 250}, format="json")
        assert r.status_code == 200, r.data
        assert r.data["cooked_yield_g"] == 250

    @pytest.mark.parametrize("body", [{"cooked_yield_g": 0}, {"yield_pct": 0}, {"yield_pct": 5000}])
    def test_out_of_range_yield_is_400(self, alice, porridge, body):
        assert _client(alice).patch(f"{API}spells/{porridge.uuid}/", body, format="json").status_code == 400

    def test_a_casting_records_the_cooked_weight(self, alice, porridge):
        r = _client(alice).post(f"{API}spells/{porridge.uuid}/castings/",
                                {"cast_on": "2026-10-10", "cooked_weight_g": 260}, format="json")
        assert r.status_code == 201, r.data
        assert Casting.objects.get().cooked_weight_g == 260

    def test_a_fork_keeps_the_yield(self, alice, bob, porridge):
        porridge.visibility = Spell.Visibility.PUBLIC
        porridge.yield_pct = 85
        porridge.save()
        r = _client(bob).post(f"{API}spells/{porridge.uuid}/fork/")
        assert r.status_code == 201
        assert r.data["yield_pct"] == 85

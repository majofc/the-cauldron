"""Grimoire scaling (#69): the pure service and POST spells/{uuid}/scale/."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from the_cauldron.models import Reagent, Spell, SpellReagent
from the_cauldron.services import grimoire_scaling as sc
from the_cauldron.services.grimoire_scaling import Line, ScalingError

User = get_user_model()
API = "/cauldron/api/grimoire/"

CHICKEN, RICE, EGG = "chicken", "rice", "egg"
LINES = [
    Line(key=CHICKEN, quantity=500, unit="g", grams=500),
    Line(key=RICE, quantity=1, unit="cup", grams=185),
    Line(key=EGG, quantity=2, unit="piece", grams=100),
]


# ── Service: modes ───────────────────────────────────────────────────────────


class TestScaleByFactor:
    def test_every_line_and_its_grams_are_multiplied(self):
        result = sc.scale_by_factor(LINES, 1.5)
        assert result.factor == 1.5
        assert [line.quantity for line in result.lines] == [750, 1.5, 3]
        assert [line.grams for line in result.lines] == [750, 277.5, 150]
        assert result.limiting is None

    def test_keys_and_units_are_kept(self):
        result = sc.scale_by_factor(LINES, 2)
        assert [(line.key, line.unit) for line in result.lines] == [(CHICKEN, "g"), (RICE, "cup"), (EGG, "piece")]

    @pytest.mark.parametrize("bad", [0, -1, "", None, "abc", float("nan"), float("inf")])
    def test_zero_negative_and_non_numbers_are_rejected(self, bad):
        with pytest.raises(ScalingError):
            sc.scale_by_factor(LINES, bad)

    def test_an_absurd_factor_is_rejected(self):
        with pytest.raises(ScalingError):
            sc.scale_by_factor(LINES, sc.MAX_FACTOR + 1)


class TestScaleToServings:
    def test_multiplies_every_line_by_target_over_servings(self):
        result = sc.scale_to_servings(LINES, 4, 6)
        assert result.factor == 1.5
        assert [line.quantity for line in result.lines] == [750, 1.5, 3]

    def test_scaling_down(self):
        assert sc.scale_to_servings(LINES, 4, 1).factor == 0.25

    @pytest.mark.parametrize("target", [0, -2])
    def test_zero_or_negative_servings_rejected(self, target):
        with pytest.raises(ScalingError):
            sc.scale_to_servings(LINES, 4, target)


class TestScaleToTotalGrams:
    def test_lines_weigh_the_target_together(self):
        result = sc.scale_to_total_grams(LINES, 1570)  # spell weighs 785 g
        assert result.factor == 2
        assert sum(line.grams for line in result.lines) == pytest.approx(1570)

    def test_a_spell_with_no_weight_cannot_be_scaled_by_weight(self):
        with pytest.raises(ScalingError):
            sc.scale_to_total_grams([Line(key="salt", quantity=0, unit="g", grams=0)], 100)

    def test_zero_target_rejected(self):
        with pytest.raises(ScalingError):
            sc.scale_to_total_grams(LINES, 0)


class TestScaleByAvailable:
    def test_one_amount_sets_the_factor(self):
        result = sc.scale_by_available(LINES, {CHICKEN: 300})
        assert result.factor == pytest.approx(0.6)
        assert result.limiting == CHICKEN
        assert result.lines[1].grams == pytest.approx(111)

    def test_the_smallest_ratio_wins(self):
        # chicken 1000/500 = 2, rice 185/185 = 1, egg 150/100 = 1.5 → rice limits.
        result = sc.scale_by_available(LINES, {CHICKEN: 1000, RICE: 185, EGG: 150})
        assert result.factor == pytest.approx(1)
        assert result.limiting == RICE

    def test_a_reagent_on_two_lines_counts_both(self):
        lines = [Line(key="butter", quantity=50, unit="g", grams=50), Line(key="butter", quantity=50, unit="g", grams=50)]
        result = sc.scale_by_available(lines, {"butter": 50})
        assert result.factor == pytest.approx(0.5)

    def test_a_zero_gram_line_cannot_limit(self):
        lines = LINES + [Line(key="salt", quantity=0, unit="g", grams=0)]
        result = sc.scale_by_available(lines, {"salt": 5, CHICKEN: 250})
        assert result.limiting == CHICKEN

    def test_only_zero_gram_reagents_is_an_error(self):
        lines = LINES + [Line(key="salt", quantity=0, unit="g", grams=0)]
        with pytest.raises(ScalingError):
            sc.scale_by_available(lines, {"salt": 5})

    @pytest.mark.parametrize("bad", [0, -100])
    def test_zero_or_negative_amount_rejected(self, bad):
        with pytest.raises(ScalingError):
            sc.scale_by_available(LINES, {CHICKEN: bad})

    def test_nothing_entered_is_an_error(self):
        with pytest.raises(ScalingError):
            sc.scale_by_available(LINES, {})

    def test_a_reagent_not_in_the_spell_is_rejected(self):
        with pytest.raises(ScalingError):
            sc.scale_by_available(LINES, {"saffron": 1})

    def test_plenty_of_everything_is_capped(self):
        assert sc.scale_by_available(LINES, {CHICKEN: 10**9}).factor == sc.MAX_FACTOR


# ── Service: display rounding ────────────────────────────────────────────────


class TestDisplayQuantity:
    @pytest.mark.parametrize("qty, shown", [(0.37, 0.5), (1.2, 1), (1.3, 1.5), (2.74, 2.5), (0.1, 0.5)])
    def test_pieces_round_to_halves_never_below_half(self, qty, shown):
        assert sc.display_quantity(qty, "piece") == shown

    @pytest.mark.parametrize("qty, shown", [(0.6, 0.5), (0.05, 0.25), (1.13, 1.25)])
    def test_spoons_and_cups_round_to_quarters(self, qty, shown):
        assert sc.display_quantity(qty, "cup") == shown

    @pytest.mark.parametrize("qty, shown", [(277.5, 280), (103, 105), (42.4, 42), (3.14, 3.1), (0.01, 0.1)])
    def test_grams_round_to_five_above_100(self, qty, shown):
        assert sc.display_quantity(qty, "g") == shown

    def test_bulk_units_keep_two_decimals(self):
        assert sc.display_quantity(1.23456, "kg") == 1.23

    def test_scaled_lines_keep_exact_grams_behind_rounded_display(self):
        line = sc.scale_by_factor([Line(key=EGG, quantity=1, unit="piece", grams=50)], 0.37).lines[0]
        assert line.display_quantity == 0.5
        assert line.quantity == pytest.approx(0.37)
        assert line.grams == pytest.approx(18.5)


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


@pytest.fixture
def reagents(db):
    chicken = Reagent.objects.create(name="Chicken", status=Reagent.Status.APPROVED)
    rice = Reagent.objects.create(name="Rice", status=Reagent.Status.APPROVED, grams_per_unit={"cup": 185})
    egg = Reagent.objects.create(name="Egg", status=Reagent.Status.APPROVED, grams_per_unit={"piece": 50})
    return chicken, rice, egg


@pytest.fixture
def spell(alice, reagents):
    chicken, rice, egg = reagents
    s = Spell.objects.create(owner=alice, title="Rice bowl", servings=4)
    SpellReagent.objects.create(spell=s, reagent=chicken, quantity=500, unit="g", grams=500, order=0)
    SpellReagent.objects.create(spell=s, reagent=rice, quantity=1, unit="cup", grams=185, order=1)
    SpellReagent.objects.create(spell=s, reagent=egg, quantity=2, unit="piece", grams=100, order=2)
    return s


def _scale(client, spell, body):
    return client.post(f"{API}spells/{spell.uuid}/scale/", body, format="json")


class TestScaleApi:
    def test_servings(self, alice, spell):
        r = _scale(_client(alice), spell, {"mode": "servings", "servings": 6})
        assert r.status_code == 200, r.data
        assert r.data["factor"] == 1.5
        assert r.data["servings"] == 6
        assert [line["display_quantity"] for line in r.data["lines"]] == [750, 1.5, 3]
        assert [line["line"] for line in r.data["lines"]] == [0, 1, 2]

    def test_factor_and_total_grams(self, alice, spell):
        assert _scale(_client(alice), spell, {"mode": "factor", "factor": 2}).data["total_grams"] == 1570
        assert _scale(_client(alice), spell, {"mode": "total_grams", "grams": 392.5}).data["factor"] == 0.5

    def test_nothing_is_written(self, alice, spell):
        _scale(_client(alice), spell, {"mode": "factor", "factor": 3})
        spell.refresh_from_db()
        assert spell.servings == 4
        assert sorted(spell.lines.values_list("quantity", flat=True)) == [1, 2, 500]

    def test_available_reports_the_limiting_reagent(self, alice, spell, reagents):
        chicken, rice, _egg = reagents
        r = _scale(_client(alice), spell, {"mode": "available", "available": [
            {"reagent": str(chicken.uuid), "quantity": 300, "unit": "g"},
            {"reagent": str(rice.uuid), "quantity": 2, "unit": "cup"},
        ]})
        assert r.status_code == 200, r.data
        assert r.data["limiting"] == str(chicken.uuid)
        assert r.data["factor"] == pytest.approx(0.6)

    def test_available_in_the_lines_own_unit_uses_its_weight(self, alice, spell, reagents):
        _chicken, _rice, egg = reagents
        r = _scale(_client(alice), spell, {"mode": "available", "available": [
            {"reagent": str(egg.uuid), "quantity": 1, "unit": "piece"}]})
        assert r.data["factor"] == pytest.approx(0.5)
        assert r.data["lines"][2]["display_quantity"] == 1

    def test_an_unconvertible_amount_is_flagged_not_guessed(self, alice, spell, reagents):
        chicken, _rice, _egg = reagents  # chicken has no cup weight and no density
        r = _scale(_client(alice), spell, {"mode": "available", "available": [
            {"reagent": str(chicken.uuid), "quantity": 2, "unit": "cup"}]})
        assert r.status_code == 400
        assert r.data["unconvertible"] == [str(chicken.uuid)]

    def test_unconvertible_beside_a_usable_amount_still_scales(self, alice, spell, reagents):
        chicken, rice, _egg = reagents
        r = _scale(_client(alice), spell, {"mode": "available", "available": [
            {"reagent": str(chicken.uuid), "quantity": 2, "unit": "cup"},
            {"reagent": str(rice.uuid), "quantity": 370, "unit": "g"}]})
        assert r.status_code == 200
        assert r.data["unconvertible"] == [str(chicken.uuid)]
        assert r.data["limiting"] == str(rice.uuid)
        assert r.data["factor"] == pytest.approx(2)

    @pytest.mark.parametrize("body", [
        {"mode": "servings", "servings": 0},
        {"mode": "factor", "factor": -1},
        {"mode": "factor"},
        {"mode": "total_grams", "grams": 0},
        {"mode": "warp"},
        {"mode": "available", "available": []},
    ])
    def test_bad_input_is_400(self, alice, spell, body):
        assert _scale(_client(alice), spell, body).status_code == 400

    def test_zero_available_amount_is_400(self, alice, spell, reagents):
        r = _scale(_client(alice), spell, {"mode": "available", "available": [
            {"reagent": str(reagents[0].uuid), "quantity": 0, "unit": "g"}]})
        assert r.status_code == 400

    def test_a_reagent_not_on_the_spell_is_400(self, alice, spell):
        other = Reagent.objects.create(name="Saffron", status=Reagent.Status.APPROVED)
        r = _scale(_client(alice), spell, {"mode": "available", "available": [
            {"reagent": str(other.uuid), "quantity": 1, "unit": "g"}]})
        assert r.status_code == 400

    def test_anyone_can_scale_a_public_spell(self, bob, spell):
        spell.visibility = Spell.Visibility.PUBLIC
        spell.save()
        assert _scale(_client(bob), spell, {"mode": "factor", "factor": 2}).status_code == 200

    def test_someone_elses_private_spell_is_404(self, bob, spell):
        assert _scale(_client(bob), spell, {"mode": "factor", "factor": 2}).status_code == 404

    def test_login_required(self, spell):
        assert _scale(APIClient(), spell, {"mode": "factor", "factor": 2}).status_code in (401, 403)

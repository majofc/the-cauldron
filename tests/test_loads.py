"""Pure unit tests for the buildable-load engine. No DB required."""

from types import SimpleNamespace as NS

import pytest

from the_cauldron.services import loads
from the_cauldron.services.progression import available_loads, next_load_up


def dumbbell_plates_profile(**over):
    base = dict(
        equipment=["dumbbells"],
        dumbbell_mode="plates",
        dumbbell_plates=[{"weight": 2.5, "count": 8}, {"weight": 1.25, "count": 4}],
        dumbbell_handle_weight=2.0,
        dumbbell_weights=[],
    )
    base.update(over)
    return NS(**base)


def barbell_profile(**over):
    base = dict(
        equipment=["barbell"],
        barbell_plates=[{"weight": 20, "count": 4}, {"weight": 5, "count": 2}],
        bar_weight=20.0,
        barbell_min_increment=2.5,
    )
    base.update(over)
    return NS(**base)


def kettlebell_profile(**over):
    base = dict(
        equipment=["kettlebell"],
        kettlebell_mode="plates",
        kettlebell_plates=[{"weight": 2, "count": 6}],
        kettlebell_handle_weight=6.0,
        kettlebell_weights=[],
    )
    base.update(over)
    return NS(**base)


def ex(*required):
    return NS(required_equipment=list(required), progression_mode="load")


# ── Dumbbells: a matched pair ────────────────────────────────────────────────


def test_dumbbell_step_needs_four_plates_and_prescribes_one_bell():
    # 8 × 2.5 → 2 usable steps; 4 × 1.25 → 1 step. One bell = handle + 2 × per side.
    totals = [l.total for l in loads.dumbbell_loads(dumbbell_plates_profile())]
    assert totals == [2.0, 4.5, 7.0, 9.5, 12.0, 14.5]


def test_dumbbell_denomination_below_four_contributes_nothing():
    prof = dumbbell_plates_profile(dumbbell_plates=[{"weight": 5, "count": 3}])
    # Three plates cannot make a matched pair — only the bare handles remain.
    assert [l.total for l in loads.dumbbell_loads(prof)] == [2.0]


def test_empty_handle_is_the_lightest_load():
    assert loads.dumbbell_loads(dumbbell_plates_profile())[0].total == 2.0


def test_fixed_dumbbells_are_untouched_by_the_plate_engine():
    prof = dumbbell_plates_profile(dumbbell_mode="fixed", dumbbell_weights=[10, 5, 15, 10])
    assert [l.total for l in loads.dumbbell_loads(prof)] == [5.0, 10.0, 15.0]


# ── Barbell: plates in pairs ─────────────────────────────────────────────────


def test_barbell_consumes_plates_in_pairs():
    # bar 20; 4 × 20 → 2 steps/side; 2 × 5 → 1 step/side.
    totals = [l.total for l in loads.barbell_loads(barbell_profile())]
    assert totals == [20.0, 30.0, 60.0, 70.0, 100.0, 110.0]


def test_bare_bar_is_offered():
    assert loads.barbell_loads(barbell_profile())[0].total == 20.0


def test_barbell_falls_back_to_min_increment_without_plates():
    prof = barbell_profile(barbell_plates=[])
    totals = [l.total for l in loads.barbell_loads(prof)]
    assert totals[:3] == [2.5, 5.0, 7.5]
    assert len(totals) == 40


# ── Adjustable kettlebell: plates stack singly ───────────────────────────────


def test_kettlebell_consumes_plates_singly():
    totals = [l.total for l in loads.kettlebell_loads(kettlebell_profile())]
    assert totals == [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0]


def test_kettlebell_recipe_is_marked_stacked_not_per_side():
    recipe = loads.recipe_for(kettlebell_profile(), ex("kettlebell"), 10.0)
    assert recipe.stacked is True
    assert recipe.per_side == [{"weight": 2.0, "count": 2}]


def test_fixed_kettlebells_supported_alongside_adjustable():
    prof = kettlebell_profile(kettlebell_mode="fixed", kettlebell_weights=[16, 12, 24])
    assert [l.total for l in loads.kettlebell_loads(prof)] == [12.0, 16.0, 24.0]


# ── Recipes ──────────────────────────────────────────────────────────────────


def test_recipe_uses_fewest_plates():
    # 2 × 2.5 per side (12.0) must not be rendered as 1 × 2.5 + 2 × 1.25.
    recipe = loads.recipe_for(dumbbell_plates_profile(), ex("dumbbells"), 12.0)
    assert recipe.per_side == [{"weight": 2.5, "count": 2}]


def test_recipe_reports_leftover_plates():
    recipe = loads.recipe_for(dumbbell_plates_profile(), ex("dumbbells"), 7.0)
    # One 2.5 per side per bell = 4 consumed, leaving 4 of them and all the 1.25s.
    assert recipe.leftover_plates == [
        {"weight": 2.5, "count": 4}, {"weight": 1.25, "count": 4},
    ]


def test_recipe_for_unbuildable_load_is_none():
    assert loads.recipe_for(dumbbell_plates_profile(), ex("dumbbells"), 13.3) is None


def test_orphan_plates_reported_for_odd_counts():
    assert loads.orphan_plates([{"weight": 2, "count": 6}], 4) == [{"weight": 2.0, "count": 2}]
    assert loads.orphan_plates([{"weight": 2, "count": 8}], 4) == []


# ── Implement awareness (the live bug this ticket fixes) ─────────────────────


def test_barbell_move_never_gets_a_dumbbell_load():
    prof = NS(
        equipment=["dumbbells", "barbell"],
        dumbbell_mode="fixed", dumbbell_weights=[5, 10, 15], dumbbell_plates=[],
        dumbbell_handle_weight=2.0,
        barbell_plates=[{"weight": 20, "count": 2}], bar_weight=20.0,
        barbell_min_increment=2.5,
        kettlebell_mode="fixed", kettlebell_weights=[], kettlebell_plates=[],
        kettlebell_handle_weight=6.0,
        band_levels=[],
    )
    assert available_loads(prof, ex("barbell")) == [20.0, 60.0]
    assert available_loads(prof, ex("dumbbells")) == [5.0, 10.0, 15.0]


def test_kettlebell_move_never_gets_a_barbell_increment():
    prof = NS(
        equipment=["barbell", "kettlebell"],
        kettlebell_mode="fixed", kettlebell_weights=[16, 24], kettlebell_plates=[],
        kettlebell_handle_weight=6.0,
        barbell_plates=[], bar_weight=20.0, barbell_min_increment=2.5,
        dumbbell_mode="fixed", dumbbell_weights=[], dumbbell_plates=[],
        dumbbell_handle_weight=2.0,
        band_levels=[],
    )
    assert available_loads(prof, ex("kettlebell")) == [16.0, 24.0]


def test_exercise_naming_no_gear_falls_back_to_stocked_inventory():
    prof = NS(
        equipment=["dumbbells", "barbell"],
        dumbbell_mode="fixed", dumbbell_weights=[5], dumbbell_plates=[],
        dumbbell_handle_weight=2.0,
        barbell_plates=[{"weight": 10, "count": 2}], bar_weight=20.0,
        barbell_min_increment=2.5,
        kettlebell_mode="fixed", kettlebell_weights=[], kettlebell_plates=[],
        kettlebell_handle_weight=6.0,
        band_levels=[],
    )
    # With nothing to match on we keep the legacy waterfall — dumbbells first —
    # so profiles predating this feature prescribe what they always did.
    assert loads.implement_for(prof, ex()) == "dumbbells"


def test_min_increment_never_outranks_real_plates():
    prof = NS(
        equipment=["barbell"],
        barbell_plates=[{"weight": 10, "count": 2}], bar_weight=20.0,
        barbell_min_increment=2.5,
        dumbbell_mode="fixed", dumbbell_weights=[], dumbbell_plates=[],
        dumbbell_handle_weight=2.0,
        kettlebell_mode="fixed", kettlebell_weights=[], kettlebell_plates=[],
        kettlebell_handle_weight=6.0,
        band_levels=[],
    )
    assert available_loads(prof, ex("barbell")) == [20.0, 40.0]


# ── Progression integration ──────────────────────────────────────────────────


def test_next_load_up_walks_the_buildable_set():
    prof = dumbbell_plates_profile()
    e = ex("dumbbells")
    assert next_load_up(prof, e, None) == 2.0
    assert next_load_up(prof, e, 2.0) == 4.5
    assert next_load_up(prof, e, 14.5) == 14.5  # already at the top


def test_inventory_too_small_yields_only_the_handle():
    # One step short of anything: the engine must still return a load so the
    # "max load reached, +1 target rep" path fires instead of an empty set.
    prof = dumbbell_plates_profile(dumbbell_plates=[])
    assert available_loads(prof, ex("dumbbells")) == [2.0]


def test_nearest_buildable_resnaps_and_prefers_the_lighter_tie():
    prof = dumbbell_plates_profile()
    assert loads.nearest_buildable(prof, ex("dumbbells"), 12.4) == 12.0
    # 6.75 is equidistant from 4.5 and 9.0... use an exact midpoint of two loads:
    assert loads.nearest_buildable(prof, ex("dumbbells"), 5.75) == 4.5


def test_bands_remain_discrete_levels():
    prof = NS(
        equipment=["bands"], band_levels=["light", "med", "heavy"],
        dumbbell_mode="fixed", dumbbell_weights=[], dumbbell_plates=[],
        dumbbell_handle_weight=2.0,
        barbell_plates=[], bar_weight=20.0, barbell_min_increment=2.5,
        kettlebell_mode="fixed", kettlebell_weights=[], kettlebell_plates=[],
        kettlebell_handle_weight=6.0,
    )
    assert available_loads(prof, ex("bands")) == [0.0, 1.0, 2.0]


# ── Robustness ───────────────────────────────────────────────────────────────


def test_junk_inventory_degrades_to_no_plates_rather_than_raising():
    prof = dumbbell_plates_profile(dumbbell_plates="nonsense")
    assert [l.total for l in loads.dumbbell_loads(prof)] == [2.0]


def test_denomination_cap_truncates_instead_of_hanging():
    plates = [{"weight": float(w), "count": 4} for w in range(1, 20)]
    prof = dumbbell_plates_profile(dumbbell_plates=plates)
    assert len(loads.normalize_plates(plates)) == loads.MAX_DENOMINATIONS
    assert len(loads.dumbbell_loads(prof)) <= loads.MAX_LOADS


@pytest.mark.parametrize("total", [None, "", "abc"])
def test_recipe_for_bad_total_is_none(total):
    assert loads.recipe_for(dumbbell_plates_profile(), ex("dumbbells"), total) is None

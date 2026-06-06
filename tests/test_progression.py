"""Pure unit tests for the Forge adaptive engine. No DB required."""

from types import SimpleNamespace

from the_cauldron.services.progression import (
    SESSIONS_TO_ADVANCE,
    available_loads,
    find_substitute,
    next_load_up,
    next_prescription,
    place_from_assessment,
)


def make_exercise(rank, mode="difficulty", rmin=5, rmax=12, threshold=0,
                  name=None, progression=None, regression=None):
    return SimpleNamespace(
        difficulty_rank=rank,
        progression_mode=mode,
        rep_range_min=rmin,
        rep_range_max=rmax,
        placement_threshold=threshold,
        name=name or f"ex{rank}",
        progression=progression,
        regression=regression,
    )


def make_prescription(exercise, sets=3, rmin=5, rmax=12, load=None, at_top=0):
    return SimpleNamespace(
        exercise=exercise,
        target_sets=sets,
        target_reps_min=rmin,
        target_reps_max=rmax,
        target_load=load,
        sessions_at_top=at_top,
    )


def dumbbell_profile(weights):
    return SimpleNamespace(
        dumbbell_weights=weights, band_levels=[], barbell_min_increment=2.5,
        load_unit="kg",
    )


# ── Assessment placement ─────────────────────────────────────────────────────


def test_place_picks_hardest_rung_met():
    ladder = [
        make_exercise(1, threshold=0),
        make_exercise(2, threshold=5),
        make_exercise(3, threshold=12),
    ]
    assert place_from_assessment(ladder, 8).difficulty_rank == 2
    assert place_from_assessment(ladder, 12).difficulty_rank == 3
    assert place_from_assessment(ladder, 0).difficulty_rank == 1


def test_place_falls_back_to_easiest_when_below_all():
    ladder = [make_exercise(1, threshold=3), make_exercise(2, threshold=8)]
    assert place_from_assessment(ladder, 1).difficulty_rank == 1


def test_place_empty_ladder_returns_none():
    assert place_from_assessment([], 10) is None


# ── Substitution (blocked exercises) ─────────────────────────────────────────


def test_substitute_prefers_nearest_equal_or_easier():
    blocked = make_exercise(4, name="push-up")
    candidates = [
        make_exercise(1, name="wall"),
        make_exercise(3, name="knee"),
        make_exercise(5, name="diamond"),
    ]
    # rank 3 is the nearest that is <= 4, so it wins over the harder rank-5.
    assert find_substitute(blocked, candidates).name == "knee"


def test_substitute_takes_equal_rank_when_present():
    blocked = make_exercise(3, name="rower")
    candidates = [make_exercise(3, name="australian-row"), make_exercise(1, name="band-row")]
    assert find_substitute(blocked, candidates).name == "australian-row"


def test_substitute_falls_back_to_nearest_harder_when_nothing_easier():
    blocked = make_exercise(2, name="x")
    candidates = [make_exercise(4, name="hard"), make_exercise(6, name="harder")]
    assert find_substitute(blocked, candidates).name == "hard"


def test_substitute_excludes_the_blocked_exercise_itself():
    blocked = make_exercise(3, name="self")
    assert find_substitute(blocked, [blocked]) is None


def test_substitute_none_when_no_candidates():
    assert find_substitute(make_exercise(3), []) is None


# ── Load helpers ─────────────────────────────────────────────────────────────


def test_available_loads_uses_dumbbells_sorted_unique():
    prof = dumbbell_profile([10, 5, 15, 10])
    assert available_loads(prof, make_exercise(1, mode="load")) == [5.0, 10.0, 15.0]


def test_next_load_up_picks_smallest_increment():
    prof = dumbbell_profile([5, 10, 15])
    ex = make_exercise(1, mode="load")
    assert next_load_up(prof, ex, None) == 5.0
    assert next_load_up(prof, ex, 5.0) == 10.0
    assert next_load_up(prof, ex, 15.0) == 15.0  # already at top


def test_bands_are_discrete_levels():
    prof = SimpleNamespace(dumbbell_weights=[], band_levels=["light", "med", "heavy"],
                           barbell_min_increment=2.5, load_unit="band_level")
    assert available_loads(prof, make_exercise(1, mode="load")) == [0.0, 1.0, 2.0]


# ── Difficulty-mode progression ──────────────────────────────────────────────


def test_difficulty_advances_only_after_sustained_top():
    harder = make_exercise(3, name="archer", rmin=5, rmax=10)
    ex = make_exercise(2, name="standard", rmax=12, progression=harder)
    presc = make_prescription(ex, rmax=12, at_top=0)
    # First top session: holds, increments counter.
    r1 = next_prescription(presc, amrap_reps=14, equipment_profile=dumbbell_profile([]))
    assert r1.exercise.name == "standard"
    assert r1.sessions_at_top == 1
    # Second top session: advances.
    presc2 = make_prescription(ex, rmax=12, at_top=SESSIONS_TO_ADVANCE - 1)
    r2 = next_prescription(presc2, amrap_reps=14, equipment_profile=dumbbell_profile([]))
    assert r2.exercise.name == "archer"
    assert r2.sessions_at_top == 0


def test_advance_sets_advanced_flag_for_gating():
    harder = make_exercise(3, name="archer", rmin=5, rmax=10)
    ex = make_exercise(2, name="standard", rmax=12, progression=harder)
    # Already at threshold-1 sustained top sessions → this one earns the advance.
    presc = make_prescription(ex, rmax=12, at_top=SESSIONS_TO_ADVANCE - 1)
    r = next_prescription(presc, amrap_reps=14, equipment_profile=dumbbell_profile([]))
    assert r.advanced is True
    assert r.exercise.name == "archer"


def test_hold_does_not_set_advanced_flag():
    harder = make_exercise(3, name="archer")
    ex = make_exercise(2, name="standard", rmax=12, progression=harder)
    presc = make_prescription(ex, rmax=12, at_top=0)
    r = next_prescription(presc, amrap_reps=14, equipment_profile=dumbbell_profile([]))
    assert r.advanced is False


def test_difficulty_regresses_when_below_range():
    easier = make_exercise(1, name="incline")
    ex = make_exercise(2, name="standard", rmin=5, regression=easier)
    presc = make_prescription(ex, rmin=5)
    r = next_prescription(presc, amrap_reps=3, equipment_profile=dumbbell_profile([]))
    assert r.exercise.name == "incline"


def test_within_range_holds():
    ex = make_exercise(2, rmin=5, rmax=12)
    presc = make_prescription(ex, rmin=5, rmax=12, at_top=1)
    r = next_prescription(presc, amrap_reps=8, equipment_profile=dumbbell_profile([]))
    assert r.exercise is ex
    assert r.sessions_at_top == 0


# ── Load-mode progression (the lower-body fix) ───────────────────────────────


def test_load_mode_adds_smallest_increment_at_top():
    ex = make_exercise(1, mode="load", name="goblet_squat", rmin=6, rmax=10)
    prof = dumbbell_profile([5, 10, 15])
    presc = make_prescription(ex, rmin=6, rmax=10, load=5.0)
    r = next_prescription(presc, amrap_reps=10, equipment_profile=prof)
    assert r.target_load == 10.0
    assert r.target_reps_min == 6  # reps reset to bottom


def test_load_mode_extends_reps_when_no_heavier_load():
    ex = make_exercise(1, mode="load", rmin=6, rmax=10)
    prof = dumbbell_profile([15])
    presc = make_prescription(ex, rmin=6, rmax=10, load=15.0)
    r = next_prescription(presc, amrap_reps=10, equipment_profile=prof)
    assert r.target_load == 15.0
    assert r.target_reps_max == 11


def test_load_mode_deloads_below_range():
    ex = make_exercise(1, mode="load", rmin=6, rmax=10)
    prof = dumbbell_profile([5, 10, 15])
    presc = make_prescription(ex, rmin=6, rmax=10, load=15.0)
    r = next_prescription(presc, amrap_reps=4, equipment_profile=prof)
    assert r.target_load == 10.0

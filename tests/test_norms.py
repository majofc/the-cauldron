"""Pure unit tests for the peer-comparison (flames) norms. No DB required."""

from the_cauldron.services import norms


# ── Scoreability gates ───────────────────────────────────────────────────────


def test_unnormed_movement_has_no_data():
    s = norms.score("Pike Push-up", 10, "male", 25)
    assert s.has_data is False
    assert s.confidence == "none"


def test_missing_sex_blocks_scoring():
    assert norms.score("Push-up", 25, "undisclosed", 25).has_data is False


def test_missing_age_blocks_scoring():
    assert norms.score("Push-up", 25, "male", None).has_data is False


def test_plank_rejects_out_of_range_age():
    # Plank norms only cover young athletes (≤29).
    assert norms.score("Plank", 100, "male", 45).has_data is False
    assert norms.score("Plank", 100, "male", 24).has_data is True


def test_female_pullup_excluded_floor_effect():
    assert norms.score("Pull-up", 3, "female", 25).has_data is False


# ── Push-ups: real percentile mapping ────────────────────────────────────────


def test_pushup_median_is_mid_decile():
    # Male 20-29 median (P50) is 25 reps → ~5th-6th decile.
    s = norms.score("Push-up", 25, "male", 25)
    assert s.has_data and s.confidence == "good"
    assert 5 <= s.decile <= 6
    assert 1 <= s.flames <= 10


def test_pushup_elite_scores_high():
    s = norms.score("Push-up", 45, "male", 25)  # above P95 (40)
    assert s.decile >= 9


def test_pushup_low_scores_low():
    s = norms.score("Push-up", 5, "male", 25)  # near P5 (8)
    assert s.decile <= 2


def test_pushup_more_reps_never_lowers_decile():
    prev = 0
    for reps in range(0, 60, 3):
        d = norms.score("Push-up", reps, "male", 25).decile
        assert d >= prev
        prev = d


def test_female_pushup_carries_modified_position_note():
    s = norms.score("Push-up", 18, "female", 25)
    assert s.has_data
    assert "modified" in s.note.lower()


# ── Decile cutoffs for charting ──────────────────────────────────────────────


def test_decile_cutoffs_present_and_monotonic_for_pushup():
    cuts = norms.decile_cutoffs("Push-up", "male", 25)
    assert cuts is not None
    vals = [cuts[d] for d in sorted(cuts)]
    assert vals == sorted(vals)  # higher decile → higher rep cutoff


def test_decile_cutoffs_none_for_unnormed():
    assert norms.decile_cutoffs("Nordic Curl", "male", 25) is None

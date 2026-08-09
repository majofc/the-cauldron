"""Pure unit tests for the peer-comparison (flames) norms. No DB required."""

from the_cauldron.services import norms


# ── Scoreability gates ───────────────────────────────────────────────────────


def test_unnormed_movement_has_no_data():
    # A non-anchor ladder rung is intentionally not scored (rep counts aren't
    # comparable to a norm measured at a different difficulty).
    s = norms.score("Wall Push-up", 10, "male", 25)
    assert s.has_data is False
    assert s.confidence == "none"


def test_missing_sex_blocks_scoring():
    assert norms.score("Push-up", 25, "undisclosed", 25).has_data is False


def test_missing_age_blocks_scoring():
    assert norms.score("Push-up", 25, "male", None).has_data is False


def test_plank_young_bracket_is_real_older_is_estimated():
    young = norms.score("Plank", 100, "male", 24)
    assert young.has_data is True
    assert young.estimated is False  # 18-29 bracket is published data
    older = norms.score("Plank", 100, "male", 45)
    assert older.has_data is True  # now covered via estimated extension
    assert older.estimated is True
    assert "estimated" in older.note.lower()


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


# ── Estimated norms: every Trial anchor is scoreable, flagged estimated ───────


def test_normed_movements_are_scoreable():
    normed = ["Push-up", "Australian Row", "Pike Push-up", "Split Squat",
              "Plank", "Glute Bridge"]
    for name in normed:
        for sex in ("male", "female"):
            s = norms.score(name, 12, sex, 35)
            assert s.has_data is True, f"{name}/{sex} should score"
            assert 1 <= s.flames <= 10


def test_asymmetry_anchors_are_deliberately_unscored():
    """The three unilateral Trial anchors have no published normative data.

    Reps on an Incline Archer Push-up are NOT comparable to a standard push-up
    norm — this module only scores a movement at the difficulty its norm was
    measured at. Mapping them onto the nearest table would invent a rating, so
    they return has_data=False and the verdict simply omits their flames.
    Delete this test the day real norms are sourced for them.
    """
    for name in ("Incline Archer Push-up", "Single-Arm Australian Row",
                 "Single-Leg Glute Bridge"):
        assert norms.score(name, 12, "male", 35).has_data is False


def test_crowdsourced_norms_real_in_prime_estimated_when_old():
    # Pike/row/glute use Strength Level data for 20-39 (real, "thin"), and a
    # flagged estimated decline for 40+.
    for name in ["Australian Row", "Pike Push-up", "Glute Bridge"]:
        prime = norms.score(name, 12, "male", 35)
        assert prime.has_data and prime.confidence == "thin"
        assert prime.estimated is False, f"{name} should be real in prime age"
        old = norms.score(name, 12, "male", 55)
        assert old.estimated is True, f"{name} 40+ should be estimated"
        assert "estimated" in old.note.lower()


def test_split_squat_is_fully_estimated():
    # Derived from bodyweight-squat data; the whole table is flagged.
    for age in (25, 55):
        s = norms.score("Split Squat", 12, "male", age)
        assert s.estimated is True
        assert s.confidence == "estimated"
        assert "estimated" in s.note.lower()


def test_real_pushup_not_flagged_estimated():
    s = norms.score("Push-up", 25, "male", 25)
    assert s.estimated is False


def test_estimated_norm_monotonic():
    prev = 0
    for reps in range(0, 70, 5):
        d = norms.score("Glute Bridge", reps, "female", 35).decile
        assert d >= prev
        prev = d


# ── Decile cutoffs for charting ──────────────────────────────────────────────


def test_decile_cutoffs_present_and_monotonic_for_pushup():
    cuts = norms.decile_cutoffs("Push-up", "male", 25)
    assert cuts is not None
    vals = [cuts[d] for d in sorted(cuts)]
    assert vals == sorted(vals)  # higher decile → higher rep cutoff


def test_decile_cutoffs_none_for_unnormed():
    assert norms.decile_cutoffs("Nordic Curl", "male", 25) is None

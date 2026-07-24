"""Bar pull-up rungs split into overhand/underhand grips.

Covers the seed (two variants per bar-pull-up rung sharing a ladder position),
linear traversal, the daily weaker-grip selection, the session snapshot, and the
downstream guards so the split stays invisible to progression, peer norms, and
the progress chart.
"""

import pytest
from django.core.management import call_command
from django.utils import timezone

from datetime import timedelta

from django.contrib.auth import get_user_model

from the_cauldron.models import (
    BlockedExercise,
    Equipment,
    Exercise,
    Program,
    ProgramDay,
    PrescribedExercise,
    SetLog,
    WorkoutSession,
)
from the_cauldron.services import forge, norms

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="grip-athlete", password="pw12345!", email="grip@example.test"
    )


def _get(name):
    return Exercise.objects.get(name=name)


def _equip(user, keys=("bodyweight", "pullup_bar"), **profile_fields):
    """Give the user an equipment profile that owns ``keys`` (a bar by default, so
    bar pull-ups are eligible)."""
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    for field, value in profile_fields.items():
        setattr(profile, field, value)
    if profile_fields:
        profile.save()
    return profile


def _log_amrap(user, exercise, reps, when):
    """A completed session with one AMRAP set for ``exercise`` at ``when``."""
    session = WorkoutSession.objects.create(
        user=user, performed_at=when, status=WorkoutSession.Status.COMPLETED
    )
    SetLog.objects.create(
        session=session, exercise=exercise, set_index=0, actual_reps=reps, is_amrap=True
    )
    return session


# ── Seed ──────────────────────────────────────────────────────────────────────


class TestSeed:
    def test_bar_pullup_rungs_split_into_two_grips(self, seeded):
        for base, chin in (("Pull-up", "Chin-up"), ("Archer Pull-up", "Archer Chin-up")):
            over, under = _get(base), _get(chin)
            assert over.grip == Exercise.Grip.OVERHAND
            assert under.grip == Exercise.Grip.UNDERHAND
            assert over.difficulty_rank == under.difficulty_rank
            assert over.pattern_id == under.pattern_id

    def test_untouched_rungs_default_to_na(self, seeded):
        for name in ("Negative Pull-up", "Band-Assisted Pull-up", "Australian Row"):
            assert _get(name).grip == Exercise.Grip.NA

    def test_ladder_traversal_stays_linear(self, seeded):
        negative = _get("Negative Pull-up")
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        archer, archer_chin = _get("Archer Pull-up"), _get("Archer Chin-up")

        assert negative.progression == pullup  # linked to the overhand representative
        assert pullup.regression == negative and chinup.regression == negative
        assert pullup.progression == archer and chinup.progression == archer
        assert archer.regression == pullup and archer_chin.regression == pullup
        assert archer.progression is None and archer_chin.progression is None

    def test_chinup_demos_are_blank_not_wrong(self, seeded):
        # A copy-pasted overhand demo would mis-coach the grip; blank until sourced.
        assert _get("Chin-up").video_url == ""
        assert _get("Archer Chin-up").video_url == ""


# ── Grip selection helper ─────────────────────────────────────────────────────


class TestSelectGripVariant:
    def test_returns_unchanged_for_non_split_rung(self, seeded, user):
        _equip(user)
        negative = _get("Negative Pull-up")
        assert forge.select_grip_variant(user, negative) == negative

    def test_selects_weaker_grip_by_recent_reps(self, seeded, user):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        now = timezone.now()
        _log_amrap(user, pullup, 10, now)
        _log_amrap(user, chinup, 3, now)  # weaker grip
        assert forge.select_grip_variant(user, pullup) == chinup
        assert forge.select_grip_variant(user, chinup) == chinup  # either arg → same

    def test_uses_most_recent_not_best_set(self, seeded, user):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        now = timezone.now()
        _log_amrap(user, chinup, 12, now - timedelta(days=10))  # once strong
        _log_amrap(user, chinup, 2, now)  # now weak
        _log_amrap(user, pullup, 8, now)
        assert forge.select_grip_variant(user, pullup) == chinup

    def test_untrained_grip_is_preferred(self, seeded, user):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        _log_amrap(user, pullup, 3, timezone.now())
        assert forge.select_grip_variant(user, pullup) == chinup

    def test_history_outside_window_counts_as_untrained(self, seeded, user):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        now = timezone.now()
        _log_amrap(user, pullup, 8, now)
        _log_amrap(user, chinup, 20, now - timedelta(days=forge.GRIP_WINDOW_DAYS + 1))
        assert forge.select_grip_variant(user, pullup) == chinup

    def test_blocked_grip_forces_the_other(self, seeded, user):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        now = timezone.now()
        _log_amrap(user, pullup, 15, now)  # stronger, but underhand is blocked…
        _log_amrap(user, chinup, 1, now)
        BlockedExercise.objects.create(user=user, exercise=chinup)
        assert forge.select_grip_variant(user, pullup) == pullup

    def test_both_grips_blocked_falls_back_to_substitute(self, seeded, user):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        BlockedExercise.objects.create(user=user, exercise=pullup)
        BlockedExercise.objects.create(user=user, exercise=chinup)
        result = forge.select_grip_variant(user, pullup)
        # Never a forbidden movement; a real (easier, eligible) substitute instead.
        assert result not in (pullup, chinup)
        assert result == _get("Negative Pull-up")

    def test_ineligible_when_no_bar_owned(self, seeded, user):
        # No pull-up bar → neither grip eligible, and no eligible substitute exists,
        # so the caller's exercise is returned unchanged (nothing forbidden logged).
        forge.get_or_create_equipment_profile(user)  # empty profile (bodyweight only)
        pullup = _get("Pull-up")
        assert forge.select_grip_variant(user, pullup) == pullup

    def test_tie_chooses_randomly_between_both(self, seeded, user, monkeypatch):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        now = timezone.now()
        _log_amrap(user, pullup, 7, now)
        _log_amrap(user, chinup, 7, now)  # tie

        seen = {}
        monkeypatch.setattr(
            forge.random, "choice",
            lambda seq: (seen.__setitem__("ids", {e.pk for e in seq}), seq[0])[1],
        )
        result = forge.select_grip_variant(user, pullup)
        assert seen["ids"] == {pullup.pk, chinup.pk}
        assert result in (pullup, chinup)

    def test_both_untrained_chooses_randomly(self, seeded, user, monkeypatch):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        seen = {}
        monkeypatch.setattr(
            forge.random, "choice",
            lambda seq: (seen.__setitem__("ids", {e.pk for e in seq}), seq[0])[1],
        )
        forge.select_grip_variant(user, pullup)
        assert seen["ids"] == {pullup.pk, chinup.pk}


# ── Progression stays on the shared rung ──────────────────────────────────────


class TestEffectiveProgressionReps:
    def test_non_split_exercise_returns_logged_reps(self, seeded, user):
        negative = _get("Negative Pull-up")
        assert forge.effective_progression_reps(user, negative, 5) == 5

    def test_uses_stronger_grip_across_variants(self, seeded, user):
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        _log_amrap(user, pullup, 10, timezone.now())  # strong overhand on record
        # A weak chin-up day is lifted to the stronger grip's demonstrated reps.
        assert forge.effective_progression_reps(user, chinup, 3) == 10

    def test_falls_back_to_logged_when_no_history(self, seeded, user):
        chinup = _get("Chin-up")
        assert forge.effective_progression_reps(user, chinup, 3) == 3


def _pullup_program(user, day_index=1):
    """Active program with a single Pull-up (overhand) prescription."""
    pullup = _get("Pull-up")
    program = Program.objects.create(user=user, is_active=True, split=Program.Split.FULL_BODY_3X)
    day = ProgramDay.objects.create(program=program, day_index=day_index, name="Upper")
    PrescribedExercise.objects.create(
        day=day,
        pattern=pullup.pattern,
        exercise=pullup,
        target_sets=3,
        target_reps_min=pullup.rep_range_min,
        target_reps_max=pullup.rep_range_max,
        order=0,
    )
    return day


class TestStartSessionSnapshot:
    def test_session_snapshots_weaker_grip(self, seeded, user):
        _equip(user)
        pullup, chinup = _get("Pull-up"), _get("Chin-up")
        day = _pullup_program(user)
        _log_amrap(user, pullup, 9, timezone.now())  # overhand trained → chin weaker
        session = forge.start_session(user, day)
        assert {sl.exercise for sl in session.set_logs.all()} == {chinup}

    def test_prescription_grip_is_left_untouched(self, seeded, user):
        _equip(user)
        pullup = _get("Pull-up")
        day = _pullup_program(user)
        _log_amrap(user, pullup, 9, timezone.now())
        forge.start_session(user, day)
        assert day.prescriptions.get().exercise == pullup

    def test_weak_grip_day_does_not_demote_the_rung(self, seeded, user):
        """A weak chin-up AMRAP must not regress the shared Pull-up rung when the
        user's overhand pull-ups are strong (the core #1 correctness guard)."""
        _equip(user)
        pullup, negative = _get("Pull-up"), _get("Negative Pull-up")
        day = _pullup_program(user)
        # Strong overhand on record → Forge schedules the neglected chin-up today.
        _log_amrap(user, pullup, 10, timezone.now())
        session = forge.start_session(user, day)
        amrap = session.set_logs.get(is_amrap=True)
        assert amrap.exercise == _get("Chin-up")  # weaker grip was scheduled

        forge.apply_session_log(session, {str(amrap.uuid): {"actual_reps": 3}})
        presc = day.prescriptions.get()
        # Still on the pull-up rung — NOT demoted to Negative by one weak-grip day.
        assert presc.exercise == pullup
        assert presc.exercise != negative


# ── Downstream stays grip-blind (norms + progress chart) ──────────────────────


class TestRungLabel:
    def test_underhand_collapses_to_overhand_name(self, seeded):
        assert _get("Chin-up").rung_label == "Pull-up"
        assert _get("Archer Chin-up").rung_label == "Archer Pull-up"

    def test_other_exercises_keep_their_own_name(self, seeded):
        assert _get("Pull-up").rung_label == "Pull-up"
        assert _get("Negative Pull-up").rung_label == "Negative Pull-up"


class TestPeerNorms:
    def test_chinup_is_scored_against_pullup_norm(self, seeded):
        assert norms.EXERCISE_NORMS.get("Chin-up") == norms.EXERCISE_NORMS["Pull-up"]

    def test_chinup_amrap_earns_peer_flames(self, seeded, user):
        _equip(user, birth_year=1995, sex="male")
        _log_amrap(user, _get("Chin-up"), 10, timezone.now())
        best = forge.best_flames_by_exercise(user)
        assert "Chin-up" in best  # scored, not silently dropped

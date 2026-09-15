"""Load-mode prescriptions never drop to 0 and keep the user's working load (#52).

A weightless handle, bar or kettlebell shell is legitimate data, which makes the
bare implement a buildable load of 0. It must never be prescribed, and a load
assigned from scratch — a Trial retake or a ⇄ swap — starts from where the user
last lifted rather than the lightest load.
"""

from importlib import import_module

import pytest
from django.apps import apps as real_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    Equipment,
    Exercise,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    WorkoutSession,
)
from the_cauldron.services import forge, progression

User = get_user_model()

repair_zero_loads = import_module("the_cauldron.migrations.0017_repair_zero_loads").forwards

# Adjustable dumbbells with a weightless handle: one dumbbell builds 0, 2.5, 5,
# 7.5, 10, 12.5 (four plates per step, loaded on both sides).
PLATES = [{"weight": 1.25, "count": 4}, {"weight": 2.5, "count": 8}]


class _Apps:
    def get_model(self, app_label, model_name):
        return real_apps.get_model(app_label, model_name)


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="plates", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def press(seeded):
    return Exercise.objects.get(name="Dumbbell Shoulder Press")


def _zero_handle_profile(user):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=["bodyweight", "dumbbells"]))
    profile.dumbbell_mode = "plates"
    profile.dumbbell_plates = PLATES
    profile.dumbbell_handle_weight = 0.0
    profile.save()
    return profile


def _log_load(user, exercise, load, field="actual_load"):
    session = WorkoutSession.objects.create(
        user=user, performed_at=timezone.now(), status=WorkoutSession.Status.COMPLETED
    )
    SetLog.objects.create(session=session, exercise=exercise, set_index=0, actual_reps=8,
                          **{field: load})


def _retake_onto(user, *placed, score=10):
    """A completed Trial placing the user on ``placed`` — then the program is
    regenerated from it, exactly as the Trial POST does."""
    session = AssessmentSession.objects.create(user=user, completed_at=timezone.now())
    for ex in placed:
        AssessmentResult.objects.create(
            session=session, pattern=ex.pattern, tested_exercise=ex,
            placed_exercise=ex, reps_or_seconds=score,
        )
    return forge.generate_program(user, session)


def _load_prescriptions(user):
    return PrescribedExercise.objects.filter(
        day__program__user=user, day__program__is_active=True,
        exercise__progression_mode="load",
    )


# ── The equipment form's zero is kept ────────────────────────────────────────


def test_a_zero_weight_handle_saves_and_stays_zero(seeded, client):
    body = {"equipment": ["bodyweight", "dumbbells", "barbell", "kettlebell"],
            "dumbbell_mode": "plates", "dumbbell_plates": PLATES,
            "dumbbell_handle_weight": 0, "bar_weight": 0, "kettlebell_handle_weight": 0}
    assert client.put("/cauldron/api/equipment/", body, format="json").status_code == 200
    saved = client.get("/cauldron/api/equipment/").json()
    assert (saved["dumbbell_handle_weight"], saved["bar_weight"],
            saved["kettlebell_handle_weight"]) == (0, 0, 0)


# ── The prescribing path drops 0 ─────────────────────────────────────────────


def test_zero_is_buildable_but_never_prescribable(user, press):
    profile = _zero_handle_profile(user)
    assert progression.available_loads(profile, press)[0] == 2.5
    assert progression.next_load_up(profile, press, None) == 2.5
    assert progression.nearest_available_load(profile, press, 0) == 2.5


def test_band_level_zero_is_still_prescribable(seeded, user):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=["bodyweight", "bands"]))
    profile.band_levels = ["light", "medium"]
    profile.save()
    row = Exercise.objects.get(name="Band-Assisted Row")
    assert progression.available_loads(profile, row) == [0.0, 1.0]


def test_trial_with_a_weightless_handle_never_prescribes_zero(user, press):
    _zero_handle_profile(user)
    _retake_onto(user, press, Exercise.objects.get(name="Dumbbell Romanian Deadlift"))
    loads = list(_load_prescriptions(user).values_list("target_load", flat=True))
    # Press: 10 clears its threshold of 8 by 25% → one step. RDL: 10 < 15 → lightest.
    assert loads == [5.0, 2.5]


def test_de_loading_never_drops_to_zero(user, press):
    profile = _zero_handle_profile(user)
    presc = type("P", (), {})()
    presc.exercise, presc.target_sets = press, 3
    presc.target_reps_min, presc.target_reps_max = 6, 12
    presc.target_load, presc.sessions_at_top = 2.5, 0
    assert progression.next_prescription(presc, 1, profile).target_load == 2.5


# ── Loads carry over from history ────────────────────────────────────────────


def test_a_retake_seeds_the_load_from_history(user, press):
    _zero_handle_profile(user)
    _log_load(user, press, 9.8)
    _retake_onto(user, press)
    presc = _load_prescriptions(user).get(exercise=press)
    assert presc.target_load == 10.0  # 9.8 snapped to the nearest buildable load


def test_history_falls_back_to_the_expected_load(user, press):
    profile = _zero_handle_profile(user)
    _log_load(user, press, 7.4, field="expected_load")
    assert forge._initial_load(profile, press) == 7.5


def test_no_history_and_no_trial_starts_at_the_lightest_non_zero_load(user, press):
    profile = _zero_handle_profile(user)
    assert forge._initial_load(profile, press) == 2.5


# ── No load history: the Trial score sets the start ──────────────────────────
# Dumbbell Shoulder Press: threshold 8 Pike Push-ups; buildable 2.5 .. 12.5, so
# the midpoint cap is 7.5 (index 2 of 5).


@pytest.mark.parametrize(
    "score,expected",
    [
        (8, 2.5),     # clears the threshold by 0% → lightest
        (9, 2.5),     # 12.5% → not yet a full step
        (10, 5.0),    # 25% → one step
        (12, 7.5),    # 50% → two steps
        (40, 7.5),    # far above → capped at the middle of the range
        (3, 2.5),     # below the threshold → lightest
    ],
)
def test_a_retake_without_history_starts_from_the_trial_score(user, press, score, expected):
    _zero_handle_profile(user)
    _retake_onto(user, press, score=score)
    assert _load_prescriptions(user).get(exercise=press).target_load == expected


def test_load_history_beats_the_trial_score(user, press):
    _zero_handle_profile(user)
    _log_load(user, press, 2.4)
    _retake_onto(user, press, score=40)
    assert _load_prescriptions(user).get(exercise=press).target_load == 2.5


def test_an_open_trial_is_not_used(user, press):
    profile = _zero_handle_profile(user)
    open_session = AssessmentSession.objects.create(user=user)  # never completed
    AssessmentResult.objects.create(
        session=open_session, pattern=press.pattern, tested_exercise=press,
        placed_exercise=press, reps_or_seconds=40,
    )
    assert forge._initial_load(profile, press) == 2.5


def test_a_zero_threshold_rung_cannot_measure_a_margin(seeded, user):
    """Band-Assisted Row places at 0 — there is no margin to scale, so it starts
    on the lightest band whatever the score."""
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=["bodyweight", "bands"]))
    profile.band_levels = ["light", "medium", "heavy"]
    profile.save()
    row = Exercise.objects.get(name="Band-Assisted Row")
    assert row.placement_threshold == 0
    _retake_onto(user, row, score=30)
    assert _load_prescriptions(user).get(exercise=row).target_load == 0.0


def test_a_swap_uses_the_history_too(user, press):
    _zero_handle_profile(user)
    _log_load(user, press, 12.4)
    candidate = next(
        c for c in forge.swap_candidates(user)["candidates"]
        if c["exercise"] == str(press.uuid)
    )
    assert candidate["target_load"] == 12.5


def test_equipment_sync_repairs_a_stored_zero(user, press):
    profile = _zero_handle_profile(user)
    _log_load(user, press, 5.1)
    program = Program.objects.create(user=user, is_active=True)
    day = ProgramDay.objects.create(program=program, day_index=0)
    presc = PrescribedExercise.objects.create(
        day=day, pattern=press.pattern, exercise=press, target_load=0.0, order=0
    )
    forge.sync_program_equipment(user)
    presc.refresh_from_db()
    assert presc.target_load == 5.0
    assert profile.dumbbell_handle_weight == 0.0  # the handle itself is untouched


# ── Migration 0017: the production repair ────────────────────────────────────


def test_repair_migration_fixes_prescriptions_and_todays_plan(user, press):
    _zero_handle_profile(user)
    _log_load(user, press, 17.1)  # beyond the inventory: snaps to the heaviest
    rdl = Exercise.objects.get(name="Dumbbell Romanian Deadlift")  # no history, no Trial
    row_ex = Exercise.objects.get(name="Dumbbell Row")  # no history; Trial 15 vs threshold 12
    trial = AssessmentSession.objects.create(user=user, completed_at=timezone.now())
    AssessmentResult.objects.create(
        session=trial, pattern=row_ex.pattern, tested_exercise=row_ex,
        placed_exercise=row_ex, reps_or_seconds=15,
    )

    program = Program.objects.create(user=user, is_active=True)
    day = ProgramDay.objects.create(program=program, day_index=0)
    pressed = PrescribedExercise.objects.create(
        day=day, pattern=press.pattern, exercise=press, target_load=0.0, order=0
    )
    hinged = PrescribedExercise.objects.create(
        day=day, pattern=rdl.pattern, exercise=rdl, target_load=0.0, order=1
    )
    rowed = PrescribedExercise.objects.create(
        day=day, pattern=row_ex.pattern, exercise=row_ex, target_load=0.0, order=2
    )
    today = WorkoutSession.objects.create(
        user=user, program_day=day, scheduled_for=timezone.localdate(),
        status=WorkoutSession.Status.PLANNED,
    )
    planned = SetLog.objects.create(
        session=today, prescribed_exercise=pressed, exercise=press, set_index=0,
        expected_load=0.0,
    )

    repair_zero_loads(_Apps(), None)

    pressed.refresh_from_db()
    hinged.refresh_from_db()
    rowed.refresh_from_db()
    planned.refresh_from_db()
    assert pressed.target_load == 12.5  # from history
    assert hinged.target_load == 2.5   # nothing to go on: lightest
    assert rowed.target_load == 5.0    # from the Trial: 25% over → one step
    assert planned.expected_load == 12.5

"""Ab wheel equipment with its rollout ladder; the rowing machine is gone (#51)."""

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
    MovementPattern,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    WorkoutSession,
)
from the_cauldron.services import forge

User = get_user_model()

drop_rowing = import_module(
    "the_cauldron.migrations.0015_ab_wheel_and_drop_rowing_machine"
).forwards


class _Apps:
    def get_model(self, app_label, model_name):
        return real_apps.get_model(app_label, model_name)


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="roller", password="pw12345!")


def _own(user, *keys):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    return profile


def _program_on(user, exercise, **fields):
    program = Program.objects.create(user=user, is_active=True)
    day = ProgramDay.objects.create(program=program, day_index=0, name="Today's Forge")
    return PrescribedExercise.objects.create(
        day=day, pattern=exercise.pattern, exercise=exercise, target_sets=3,
        target_reps_min=exercise.rep_range_min, target_reps_max=exercise.rep_range_max,
        target_rest_seconds=exercise.rest_seconds, order=0, **fields,
    )


# ── Catalog ──────────────────────────────────────────────────────────────────


def test_ab_wheel_is_non_loadable_equipment(seeded):
    wheel = Equipment.objects.get(key="ab_wheel")
    assert (wheel.name, wheel.is_loadable, wheel.load_unit) == ("Ab Wheel", False, "none")


def test_rollout_rungs_require_the_wheel_and_chain_after_the_planks(seeded):
    knee = Exercise.objects.get(name="Knee Ab Wheel Rollout")
    standing = Exercise.objects.get(name="Standing Ab Wheel Rollout")
    for ex in (knee, standing):
        assert [e.key for e in ex.required_equipment.all()] == ["ab_wheel"]
        assert {m.key for m in ex.muscles.all()} == {"abs", "obliques", "lats"}
        assert ex.cues
    assert knee.progression == standing and standing.regression == knee
    assert standing.progression is None  # hardest rung of the pattern
    assert knee.regression.difficulty_rank == 4  # RKC Plank / Hollow Body Hold rank
    core = MovementPattern.objects.get(key="core_anti_extension")
    assert standing.difficulty_rank == max(core.exercises.values_list("difficulty_rank", flat=True))


def test_band_rollout_is_renamed_and_needs_only_bands(seeded):
    band = Exercise.objects.get(name="Band Rollout")
    assert [e.key for e in band.required_equipment.all()] == ["bands"]
    assert not Exercise.objects.filter(name="Ab Wheel / Band Rollout").exists()


def test_rowing_machine_is_gone_from_the_seed(seeded):
    assert not Equipment.objects.filter(key="rowing_machine").exists()
    assert not Exercise.objects.filter(name="Rowing Machine").exists()
    assert "rowing_machine" not in Equipment.Key.values


@pytest.mark.parametrize("owns_wheel", [True, False])
def test_only_wheel_owners_are_offered_rollouts(seeded, user, owns_wheel):
    keys = ["bodyweight", "ab_wheel"] if owns_wheel else ["bodyweight"]
    profile = _own(user, *keys)
    core = MovementPattern.objects.get(key="core_anti_extension")
    names = {e.name for e in forge.eligible_exercises(core, profile)}
    assert ("Knee Ab Wheel Rollout" in names) is owns_wheel
    assert ("Standing Ab Wheel Rollout" in names) is owns_wheel


def test_wheel_owner_can_be_placed_on_a_rollout(seeded, user):
    _own(user, "bodyweight", "ab_wheel")
    client = APIClient()
    client.force_authenticate(user=user)
    plank = Exercise.objects.get(name="Plank")
    resp = client.post(
        "/cauldron/api/assessment/",
        {"results": [{"pattern_key": "core_anti_extension",
                      "tested_exercise": str(plank.uuid), "reps_or_seconds": 120}]},
        format="json",
    )
    assert resp.status_code == 201
    presc = PrescribedExercise.objects.get(day__program__user=user, day__program__is_active=True)
    assert presc.exercise.name == "Standing Ab Wheel Rollout"


def test_profile_endpoint_persists_the_ab_wheel(seeded, user):
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.put("/cauldron/api/equipment/", {"equipment": ["bodyweight", "ab_wheel"]}, format="json")
    assert resp.status_code == 200
    assert set(client.get("/cauldron/api/equipment/").json()["equipment"]) == {"bodyweight", "ab_wheel"}


# ── Migration 0015: removing the rowing machine without orphaning anything ───


@pytest.fixture
def legacy_rowing(seeded):
    """Recreate the pre-#51 rowing rows the migration has to clear."""
    machine = Equipment.objects.create(key="rowing_machine", name="Rowing Machine")
    pull = MovementPattern.objects.get(key="vertical_pull")
    rowing = Exercise.objects.create(
        pattern=pull, name="Rowing Machine", difficulty_rank=2, rep_range_min=10,
        rep_range_max=20, placement_threshold=3,
    )
    rowing.required_equipment.set([machine])
    # The pre-#51 chain: Australian Row → Rowing Machine → Single-Arm Australian Row.
    australian = Exercise.objects.get(name="Australian Row")
    single_arm = Exercise.objects.get(name="Single-Arm Australian Row")
    Exercise.objects.filter(pk=australian.pk).update(progression=rowing)
    Exercise.objects.filter(pk=rowing.pk).update(regression=australian, progression=single_arm)
    rowing.refresh_from_db()
    return rowing


def test_migration_moves_everything_off_the_rowing_machine(legacy_rowing, user):
    rowing = legacy_rowing
    _own(user, "bodyweight", "pullup_bar", "rowing_machine")
    live = _program_on(user, rowing)
    # Earned the unlock from Australian Row up to the machine.
    parked = PrescribedExercise.objects.create(
        day=live.day, pattern=rowing.pattern, exercise=Exercise.objects.get(name="Australian Row"),
        pending_progression=rowing, order=1,
    )
    session = WorkoutSession.objects.create(
        user=user, program_day=live.day, performed_at=timezone.now(),
        status=WorkoutSession.Status.COMPLETED,
    )
    logged = SetLog.objects.create(session=session, exercise=rowing, set_index=0, actual_reps=15)
    trial = AssessmentSession.objects.create(user=user, completed_at=timezone.now())
    result = AssessmentResult.objects.create(
        session=trial, pattern=rowing.pattern, tested_exercise=rowing,
        placed_exercise=rowing, reps_or_seconds=10,
    )

    drop_rowing(_Apps(), None)

    australian = Exercise.objects.get(name="Australian Row")
    live.refresh_from_db()
    parked.refresh_from_db()
    logged.refresh_from_db()
    result.refresh_from_db()
    assert live.exercise == australian  # nearest rung the user can still do
    assert (live.target_reps_min, live.target_reps_max) == (8, 15)
    # Moved UP the old chain — never re-parked on the rung they already train.
    assert parked.pending_progression.name == "Single-Arm Australian Row"
    assert logged.exercise == australian and logged.actual_reps == 15
    assert (result.tested_exercise, result.placed_exercise) == (australian, australian)
    assert not Exercise.objects.filter(name="Rowing Machine").exists()
    assert not Equipment.objects.filter(key="rowing_machine").exists()
    assert set(forge.get_or_create_equipment_profile(user).equipment.values_list("key", flat=True)) == {
        "bodyweight", "pullup_bar"
    }


def test_migration_renames_the_old_rollout_in_place(seeded):
    band = Exercise.objects.get(name="Band Rollout")
    Exercise.objects.filter(pk=band.pk).update(name="Ab Wheel / Band Rollout")

    drop_rowing(_Apps(), None)

    band.refresh_from_db()
    assert band.name == "Band Rollout"  # same row, so its history stays attached

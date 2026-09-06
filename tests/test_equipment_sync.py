"""A live program must never prescribe a movement the user cannot perform.

Equipment can change after the program was generated — from the Forge's own
profile screen or from the Django admin. Reconciliation happens on that change;
the read paths hide whatever had no stand-in, and the write paths that move a
prescription along the ladder re-check eligibility before saving.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    Equipment,
    Exercise,
    MovementPattern,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    UserEquipmentProfile,
    WorkoutSession,
)
from the_cauldron.services import forge

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="rower", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def rowing(seeded):
    return Exercise.objects.get(name="Rowing Machine")


def _own(user, *keys):
    """Set the user's equipment — this fires the reconcile signal, exactly as the
    profile endpoint and the admin do."""
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    return profile


def _program_on(user, exercise) -> PrescribedExercise:
    """An active one-day program prescribing ``exercise``."""
    program = Program.objects.create(user=user, is_active=True)
    day = ProgramDay.objects.create(program=program, day_index=0, name="Full Body A")
    return PrescribedExercise.objects.create(
        day=day,
        pattern=exercise.pattern,
        exercise=exercise,
        target_sets=3,
        target_reps_min=exercise.rep_range_min,
        target_reps_max=exercise.rep_range_max,
        target_rest_seconds=exercise.rest_seconds,
        order=0,
    )


# ── Reconciling on an equipment change ───────────────────────────────────────


def test_changing_equipment_repoints_the_program(user, rowing):
    """The reported production case: bar + weights added, rowing machine removed.
    No read or session needed — the change itself repairs the program."""
    _own(user, "bodyweight", "rowing_machine")
    presc = _program_on(user, rowing)

    _own(user, "bodyweight", "pullup_bar", "dumbbells", "kettlebell")

    presc.refresh_from_db()
    assert presc.exercise != rowing
    assert "rowing_machine" not in set(
        presc.exercise.required_equipment.values_list("key", flat=True)
    )
    # The stand-in stays in the same pattern and resets the rung counter.
    assert presc.exercise.pattern == rowing.pattern
    assert presc.sessions_at_top == 0
    assert presc.target_reps_min == presc.exercise.rep_range_min


def test_sync_reports_what_it_swapped(user, rowing):
    _own(user, "bodyweight", "pullup_bar", "dumbbells", "kettlebell")
    presc = _program_on(user, rowing)  # created already-unperformable

    result = forge.sync_program_equipment(user)

    presc.refresh_from_db()
    assert result == {
        "swapped": [{"from": "Rowing Machine", "to": presc.exercise.name}],
        "hidden": set(),
    }


def test_sync_is_a_noop_when_the_user_still_owns_the_equipment(user, rowing):
    _own(user, "bodyweight", "rowing_machine")
    presc = _program_on(user, rowing)

    assert forge.sync_program_equipment(user) == {"swapped": [], "hidden": set()}
    presc.refresh_from_db()
    assert presc.exercise == rowing


def test_sync_is_idempotent(user, rowing):
    _own(user, "bodyweight", "pullup_bar")
    presc = _program_on(user, rowing)

    assert forge.sync_program_equipment(user)["swapped"]
    swapped_to = PrescribedExercise.objects.get(pk=presc.pk).exercise
    assert forge.sync_program_equipment(user) == {"swapped": [], "hidden": set()}
    assert PrescribedExercise.objects.get(pk=presc.pk).exercise == swapped_to


def test_sync_clears_a_pending_unlock_that_needs_missing_equipment(user, seeded):
    pattern = MovementPattern.objects.get(key="vertical_pull")
    bodyweight_rung = Exercise.objects.get(name="Australian Row")
    _own(user, "bodyweight", "pullup_bar")
    presc = _program_on(user, bodyweight_rung)
    presc.pending_progression = Exercise.objects.get(name="Rowing Machine")
    presc.save(update_fields=["pending_progression"])

    forge.sync_program_equipment(user)

    presc.refresh_from_db()
    assert presc.pending_progression is None
    assert presc.exercise == bodyweight_rung  # still doable, untouched
    assert presc.pattern == pattern


# ── Nothing reachable in the pattern: hide, never delete ─────────────────────


def test_unperformable_prescription_is_hidden_not_deleted(user, rowing):
    """Nothing in vertical_pull is bodyweight-only, so a user with no bar, bands
    or machine has no stand-in. The day loses the pattern rather than showing an
    impossible movement — but the row survives, so it returns if the gear does."""
    _own(user, "bodyweight")
    presc = _program_on(user, rowing)

    result = forge.sync_program_equipment(user)

    assert result == {
        "swapped": [{"from": "Rowing Machine", "to": None}],
        "hidden": {presc.pk},
    }
    assert PrescribedExercise.objects.filter(pk=presc.pk).exists()  # not destroyed
    assert forge.unperformable_prescription_ids(user) == {presc.pk}
    assert forge.select_day_prescriptions(user, presc.day) == []


def test_a_hidden_prescription_returns_when_the_equipment_comes_back(user, rowing):
    _own(user, "bodyweight")
    presc = _program_on(user, rowing)
    assert forge.select_day_prescriptions(user, presc.day) == []

    _own(user, "bodyweight", "rowing_machine")  # machine plugged back in

    assert forge.unperformable_prescription_ids(user) == set()
    assert [p.pk for p in forge.select_day_prescriptions(user, presc.day)] == [presc.pk]


def test_unperformable_prescription_is_hidden_from_both_read_paths(user, client, rowing):
    _own(user, "bodyweight")
    _program_on(user, rowing)

    today = client.get("/cauldron/api/today/")
    assert today.status_code == 200
    assert today.json()["set_logs"] == []

    program = client.get("/cauldron/api/program/")
    assert program.status_code == 200
    assert [p for d in program.json()["days"] for p in d["prescriptions"]] == []


def test_reading_the_program_writes_nothing(user, client, rowing):
    """GET /program/ is a plain read — it must not even create a profile row."""
    _program_on(user, rowing)
    assert not UserEquipmentProfile.objects.filter(user=user).exists()

    assert client.get("/cauldron/api/program/").status_code == 200

    assert not UserEquipmentProfile.objects.filter(user=user).exists()


# ── The write paths must not re-introduce an unusable movement ───────────────


def test_accepting_an_unlock_never_climbs_onto_unusable_equipment(user, seeded):
    """A stale client can accept an unlock earned before the gear went away."""
    _own(user, "bodyweight", "pullup_bar")
    presc = _program_on(user, Exercise.objects.get(name="Australian Row"))
    rowing = Exercise.objects.get(name="Rowing Machine")
    PrescribedExercise.objects.filter(pk=presc.pk).update(pending_progression=rowing)
    presc.refresh_from_db()

    new = forge.accept_progression(user, presc)

    assert new != rowing
    presc.refresh_from_db()
    assert presc.exercise != rowing
    assert presc.pending_progression is None


def test_logging_a_session_never_writes_back_unusable_equipment(user, seeded):
    """The engine walks the whole ladder; whatever rung it lands on is re-checked
    against the user's equipment before it is saved."""
    _own(user, "bodyweight", "pullup_bar")
    dumbbell_row = Exercise.objects.get(name="Dumbbell Row")
    presc = _program_on(user, dumbbell_row)  # needs dumbbells the user lacks

    session = WorkoutSession.objects.create(
        user=user,
        program_day=presc.day,
        scheduled_for=timezone.now().date(),
        status=WorkoutSession.Status.PLANNED,
    )
    amrap = SetLog.objects.create(
        session=session,
        prescribed_exercise=presc,
        exercise=presc.exercise,
        set_index=0,
        expected_reps=presc.target_reps_max,
        is_amrap=True,
    )

    forge.apply_session_log(session, {str(amrap.uuid): {"actual_reps": 12}})

    presc.refresh_from_db()
    assert presc.exercise != dumbbell_row
    assert presc.pending_progression != dumbbell_row


# ── Guard ────────────────────────────────────────────────────────────────────


def test_a_day_belonging_to_another_user_is_refused(user, rowing):
    """Defence in depth: both current callers already scope the day to the
    requesting user, so no future one can quietly read across accounts."""
    presc = _program_on(user, rowing)
    intruder = User.objects.create_user(
        username="intruder", password="pw12345!", email="intruder@example.test"
    )

    with pytest.raises(PermissionDenied):
        forge.select_day_prescriptions(intruder, presc.day)

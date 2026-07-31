"""Per-side (unilateral) exercises: even rep targets and per-side logging.

A per-side movement is worked one side at a time, so an odd rep target would
hand one side an extra rep. Every path that writes ``target_reps_*`` rounds both
ends UP to even via the single shared helper; timed holds (seconds) are exempt.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    Equipment,
    Exercise,
    PrescribedExercise,
    Program,
    ProgramDay,
)
from the_cauldron.serializers import ExerciseSerializer
from the_cauldron.services import forge, progression

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="archer", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


class _FakeExercise:
    """Minimal stand-in for the pure-logic helper (no DB)."""

    def __init__(self, is_per_side=False, is_timed=False):
        self.is_per_side = is_per_side
        self.is_timed = is_timed


# ── The shared parity helper ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected", [(0, 0), (1, 2), (2, 2), (3, 4), (4, 4), (5, 6), (11, 12)]
)
def test_even_up_rounds_up_to_even(raw, expected):
    assert progression.even_up(raw) == expected


def test_rep_targets_round_both_ends_for_per_side():
    # A Typewriter Push-up prescribed 3-6 becomes 4-6: 2 per side, both ways.
    assert progression.rep_targets_for(_FakeExercise(is_per_side=True), 3, 6) == (4, 6)
    assert progression.rep_targets_for(_FakeExercise(is_per_side=True), 3, 7) == (4, 8)


def test_rep_targets_untouched_for_two_sided_moves():
    assert progression.rep_targets_for(_FakeExercise(), 3, 7) == (3, 7)


def test_timed_holds_are_exempt_from_rounding():
    # Values are seconds, not reps — a 15s hold must stay 15s.
    timed = _FakeExercise(is_per_side=True, is_timed=True)
    assert progression.rep_targets_for(timed, 15, 45) == (15, 45)


# ── The seeded flag ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "Pistol Squat",
        "Bulgarian Split Squat",
        "Split Squat",
        "Archer Push-up",
        "Archer Pull-up",
        "Typewriter Push-up",
    ],
)
def test_seed_flags_per_side_exercises(seeded, name):
    assert Exercise.objects.get(name=name).is_per_side is True


@pytest.mark.parametrize("name", ["Goblet Squat", "Barbell Back Squat", "Push-up", "Plank"])
def test_seed_leaves_two_sided_exercises_unflagged(seeded, name):
    """Goblet and Back Squat sit on the ``lower_unilateral`` ladder but are
    two-legged lifts — the old pattern-derived check wrongly flagged them."""
    assert Exercise.objects.get(name=name).is_per_side is False


def test_serializer_exposes_the_flag_as_is_unilateral(seeded):
    """The client field name stays ``is_unilateral``; it now reads the flag, so
    upper-body archer/typewriter moves are finally covered."""
    typewriter = Exercise.objects.get(name="Typewriter Push-up")
    goblet = Exercise.objects.get(name="Goblet Squat")
    assert ExerciseSerializer(typewriter).data["is_unilateral"] is True
    assert ExerciseSerializer(goblet).data["is_unilateral"] is False


# ── Every prescription path produces even targets ────────────────────────────


def _own(user, *keys):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    return profile


def _program_on(user, exercise) -> PrescribedExercise:
    """An active one-day program prescribing ``exercise`` (raw odd targets)."""
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


def test_generation_prescribes_even_targets(seeded, user):
    """Program generation: a placement on a per-side rung with an odd catalog
    range (Pistol Squat 3-8) is prescribed 4-8."""
    _own(user, "bodyweight")
    pistol = Exercise.objects.get(name="Pistol Squat")
    assert pistol.rep_range_min % 2 == 1, "fixture expects an odd catalog min"

    assessment = AssessmentSession.objects.create(user=user, is_active=True)
    AssessmentResult.objects.create(
        session=assessment,
        pattern=pistol.pattern,
        tested_exercise=pistol,
        placed_exercise=pistol,
        reps_or_seconds=pistol.placement_threshold,
    )
    program = forge.generate_program(user, assessment)

    presc = PrescribedExercise.objects.filter(
        day__program=program, exercise=pistol
    ).first()
    assert presc is not None
    assert presc.target_reps_min == 4
    assert presc.target_reps_max == pistol.rep_range_max  # already even (8)


def test_substitution_repoints_onto_even_targets(seeded, user):
    """Blocking a move repoints the prescription — the stand-in's targets are
    evened too, not just the generated ones."""
    _own(user, "bodyweight")
    typewriter = Exercise.objects.get(name="Typewriter Push-up")  # 3-6
    presc = _program_on(user, Exercise.objects.get(name="Push-up"))
    profile = forge.get_or_create_equipment_profile(user)

    forge._repoint_prescription(presc, typewriter, profile)

    presc.refresh_from_db()
    assert (presc.target_reps_min, presc.target_reps_max) == (4, 6)


def test_progression_writes_back_even_targets(seeded, user):
    """The progression path (session log → next prescription) is the third
    writer of ``target_reps_*``. A de-load onto a per-side rung with an odd
    range must land on even targets too."""
    _own(user, "bodyweight")
    shrimp = Exercise.objects.get(name="Shrimp Squat")
    pistol = Exercise.objects.get(name="Pistol Squat")  # 3-8
    assert shrimp.regression == pistol

    presc = _program_on(user, shrimp)
    session = forge.start_session(user, presc.day)
    amrap = session.set_logs.get(is_amrap=True)
    # Well under the bottom of the range → regress one rung.
    forge.apply_session_log(session, {str(amrap.uuid): {"actual_reps": 1}})

    presc.refresh_from_db()
    assert presc.exercise == pistol
    assert (presc.target_reps_min, presc.target_reps_max) == (4, 8)


# ── Workout logging: one input per set ───────────────────────────────────────


def test_per_side_sets_log_a_single_reps_per_side_value(seeded, client, user):
    """Limb measurement lives in the Trial now. A per-side move is still flagged
    ``is_unilateral`` (the UI labels it "per side") but every set — including the
    to-failure one — records ONE value meaning reps per side."""
    _own(user, "bodyweight")
    archer = Exercise.objects.get(name="Archer Push-up")
    presc = _program_on(user, archer)

    today = client.get(f"/cauldron/api/today/?day={presc.day.day_index}")
    assert today.status_code == 201
    sets = today.json()["set_logs"]

    assert [s["is_unilateral"] for s in sets] == [True] * len(sets)
    # To-failure remains the last set only.
    assert [s["is_amrap"] for s in sets] == [False] * (len(sets) - 1) + [True]

    amrap = sets[-1]
    resp = client.post(
        f"/cauldron/api/sessions/{today.json()['uuid']}/log/",
        {"sets": {s["uuid"]: {"actual_reps": 7, "actual_load": None} for s in sets}},
        format="json",
    )
    assert resp.status_code == 200

    from the_cauldron.models import SetLog

    logged = SetLog.objects.get(uuid=amrap["uuid"])
    assert logged.actual_reps == 7
    # Nothing writes the per-side columns any more.
    assert (logged.left_reps, logged.right_reps) == (None, None)


def test_legacy_per_side_payload_is_still_accepted(seeded, client, user):
    """``left_reps``/``right_reps`` are read-only historical fields, but a client
    cached from before the change must not start erroring — the server still
    collapses them to the weaker side."""
    _own(user, "bodyweight")
    presc = _program_on(user, Exercise.objects.get(name="Archer Push-up"))
    today = client.get(f"/cauldron/api/today/?day={presc.day.day_index}")
    sets = today.json()["set_logs"]
    amrap = sets[-1]

    resp = client.post(
        f"/cauldron/api/sessions/{today.json()['uuid']}/log/",
        {"sets": {
            **{s["uuid"]: {"actual_reps": s["expected_reps"], "actual_load": None}
               for s in sets[:-1]},
            amrap["uuid"]: {"left_reps": 9, "right_reps": 5, "actual_load": None},
        }},
        format="json",
    )
    assert resp.status_code == 200

    from the_cauldron.models import SetLog

    logged = SetLog.objects.get(uuid=amrap["uuid"])
    assert (logged.left_reps, logged.right_reps) == (9, 5)
    assert logged.actual_reps == 5  # weaker side drives progression


def test_historical_per_side_rows_still_render(seeded, client, user):
    """Rows written before the change keep their values and must serialise
    without error — there was no backfill."""
    _own(user, "bodyweight")
    presc = _program_on(user, Exercise.objects.get(name="Archer Push-up"))
    today = client.get(f"/cauldron/api/today/?day={presc.day.day_index}")
    session_uuid = today.json()["uuid"]

    from the_cauldron.models import SetLog

    stale = SetLog.objects.filter(session__uuid=session_uuid, is_amrap=True).first()
    stale.left_reps, stale.right_reps, stale.actual_reps = 11, 6, 6
    stale.save(update_fields=["left_reps", "right_reps", "actual_reps"])

    resp = client.get(f"/cauldron/api/sessions/{session_uuid}/")
    assert resp.status_code == 200
    row = next(s for s in resp.json()["set_logs"] if s["is_amrap"])
    assert (row["left_reps"], row["right_reps"]) == (11, 6)

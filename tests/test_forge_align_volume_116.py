"""Programs left mixed by 0038 are aligned, and swaps follow the volume (#116).

0038 (#109) set every ``weekly_volume_target`` to 16 without regenerating
``target_sets``, so the first touched pattern jumped from 3 to 5 sets.
``align_forge_volume`` puts a misaligned program back on 3 sets everywhere with
the highest volume that gives 3; a ⇄ swap prescribes the program's sets.
"""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.models import Exercise, MovementPattern, PrescribedExercise, Program, ProgramDay
from the_cauldron.services import forge
from the_cauldron.tests.test_set_rung import _ex, _own

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="lifter", email="lifter@example.com", password="pw12345!")


def _program(user, volume=16, sets=3):
    """An active program with one performable movement per pattern (a Dumbbell
    Row for the horizontal pull), every one on ``sets``."""
    _own(user, "bodyweight", "dumbbells", dumbbell_weights=[5, 10, 15])
    owned = forge.owned_equipment_keys_for(user)
    row = _ex("Dumbbell Row")
    program = Program.objects.create(user=user, is_active=True, weekly_volume_target=volume)
    day = ProgramDay.objects.create(program=program, day_index=0, name="Today's Forge")
    for order, pattern in enumerate(MovementPattern.objects.order_by("key")):
        if pattern == row.pattern:
            ex = row
        else:
            ex = next((e for e in Exercise.objects.filter(pattern=pattern).order_by("name")
                       if forge.is_performable(e, owned)), None)
            if ex is None:
                continue
        PrescribedExercise.objects.create(
            day=day, pattern=pattern, exercise=ex, target_sets=sets, target_load=5 if ex == row else None,
            target_reps_min=ex.rep_range_min, target_reps_max=ex.rep_range_max,
            target_rest_seconds=ex.rest_seconds, order=order,
        )
    return program


def _sets(program):
    return sorted(PrescribedExercise.objects.filter(day__program=program).values_list("target_sets", flat=True))


def _align():
    out = StringIO()
    call_command("align_forge_volume", stdout=out)
    return out.getvalue()


def test_a_legacy_program_goes_to_3_sets_and_volume_7(seeded, user):
    program = _program(user)
    assert len(_sets(program)) == 7  # the seed's seven patterns, all performable here

    assert "aligned 1 program" in _align()

    program.refresh_from_db()
    assert program.weekly_volume_target == 7  # 7 / (3 × 5/7) = 3.27 → 3; 8 would give 4
    assert set(_sets(program)) == {3}


def test_a_mixed_program_goes_back_to_3(seeded, user):
    program = _program(user)
    for presc in PrescribedExercise.objects.filter(day__program=program)[:2]:
        presc.target_sets = 5
        presc.save()

    _align()

    program.refresh_from_db()
    assert set(_sets(program)) == {3}
    assert forge.sets_for_volume(program.weekly_volume_target,
                                 forge.weekly_frequency(program, 7)) == 3


def test_fewer_patterns_take_the_highest_volume_that_gives_3(seeded, user):
    program = _program(user)
    PrescribedExercise.objects.filter(day__program=program).exclude(
        pk__in=list(PrescribedExercise.objects.filter(day__program=program).values_list("pk", flat=True)[:5])
    ).delete()

    _align()

    program.refresh_from_db()
    assert program.weekly_volume_target == 10  # every pattern each of 3 sessions: 10 / 3 → 3


def test_aligned_programs_are_left_alone(seeded, user):
    on_five = _program(user, volume=16, sets=5)
    other = User.objects.create_user(username="other", email="other@example.com", password="pw12345!")
    hand_set = _program(other)
    forge.set_weekly_volume(other, hand_set, 6)  # 6 / 2.14 → 3 everywhere
    before = [(p.weekly_volume_target, _sets(p)) for p in (on_five, hand_set)]

    assert "aligned 0 program" in _align()

    for p in (on_five, hand_set):
        p.refresh_from_db()
    assert [(p.weekly_volume_target, _sets(p)) for p in (on_five, hand_set)] == before


def test_running_twice_changes_nothing_more(seeded, user):
    program = _program(user)
    assert "aligned 1 program" in _align()
    program.refresh_from_db()
    after_first = (program.weekly_volume_target, _sets(program))

    assert "aligned 0 program" in _align()
    program.refresh_from_db()
    assert (program.weekly_volume_target, _sets(program)) == after_first


def test_inactive_programs_are_not_touched(seeded, user):
    program = _program(user)
    Program.objects.filter(pk=program.pk).update(is_active=False)
    _align()
    program.refresh_from_db()
    assert program.weekly_volume_target == 16


def test_after_aligning_a_load_change_keeps_3_sets(seeded, user):
    program = _program(user)
    _align()
    row = PrescribedExercise.objects.get(day__program=program, exercise=_ex("Dumbbell Row"))
    client = APIClient()
    client.force_authenticate(user=user)

    resp = client.post(f"/cauldron/api/prescription/{row.uuid}/set-load/", {"load": 10}, format="json")

    assert resp.status_code == 200, resp.data
    row.refresh_from_db()
    assert row.target_load == 10 and row.target_sets == 3


def test_todays_logged_movement_keeps_its_sets(seeded, user):
    program = _program(user, sets=5)
    PrescribedExercise.objects.filter(day__program=program).update(target_sets=3)
    first = PrescribedExercise.objects.filter(day__program=program).order_by("order").first()
    PrescribedExercise.objects.filter(pk=first.pk).update(target_sets=5)  # mixed
    session = forge.start_session(user, first.day)
    # No training history: the opening serves the first five by display order.
    rows = list(session.set_logs.filter(prescribed_exercise=first).order_by("set_index"))
    assert rows
    rows[0].actual_reps = 8
    rows[0].save()

    _align()

    first.refresh_from_db()
    assert first.target_sets == 3
    assert session.set_logs.filter(prescribed_exercise=first).count() == len(rows) == 5


def test_untouched_sets_today_follow(seeded, user):
    program = _program(user, sets=5)
    PrescribedExercise.objects.filter(day__program=program).update(target_sets=3)
    first = PrescribedExercise.objects.filter(day__program=program).order_by("order").first()
    PrescribedExercise.objects.filter(pk=first.pk).update(target_sets=5)
    session = forge.start_session(user, first.day)
    assert session.set_logs.filter(prescribed_exercise=first).count() == 5

    _align()

    assert session.set_logs.filter(prescribed_exercise=first).count() == 3


# ── Swaps ⇄ ──────────────────────────────────────────────────────────────────


def _swap_pair(session, owned):
    on_session = set(session.set_logs.values_list("exercise_id", flat=True))
    for log in session.set_logs.select_related("exercise"):
        for ex in Exercise.objects.filter(pattern=log.exercise.pattern).exclude(pk__in=on_session):
            if forge.is_performable(ex, owned):
                return log.exercise, ex
    raise AssertionError("no swappable movement in this seed")


def test_a_swap_prescribes_the_programs_sets(seeded, user):
    program = _program(user, volume=16, sets=5)  # 16 / 2.14 → 5
    assert forge.program_sets(user) == 5
    session = forge.start_session(user, ProgramDay.objects.get(program=program))
    from_ex, to_ex = _swap_pair(session, forge.owned_equipment_keys_for(user))

    forge.swap_session_exercise(session, from_ex, to_ex)

    rows = list(session.set_logs.filter(exercise=to_ex).order_by("set_index"))
    assert len(rows) == 5 and rows[-1].is_amrap
    assert {c["target_sets"] for c in forge.swap_candidates(user)["candidates"]} == {5}


def test_without_a_program_a_swap_uses_3(seeded, user):
    assert forge.program_sets(user) == forge.DEFAULT_TARGET_SETS == 3

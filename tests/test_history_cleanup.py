"""The 0013 history-cleanup data migration.

The old Today flow wrote a session on every page load, so the history filled up
with sessions in which nothing was performed. The migration clears those out and
drops the extra program days that came with the multi-day model — without
touching a session that carries real work.

The migration function is exercised directly against the real models (the
historical models it receives are field-identical here), so the deletion rules
are tested rather than the migration machinery.
"""

from importlib import import_module

import pytest
from django.apps import apps as real_apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from the_cauldron.models import (
    Exercise,
    MovementPattern,
    Program,
    ProgramDay,
    SetLog,
    WorkoutSession,
)

User = get_user_model()

clean_history = import_module(
    "the_cauldron.migrations.0013_single_day_history_cleanup"
).clean_history


class _Apps:
    """Stands in for the migration's historical app registry."""

    def get_model(self, app_label, model_name):
        return real_apps.get_model(app_label, model_name)


def _run():
    clean_history(_Apps(), None)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="cleanup", password="pw12345!")


@pytest.fixture
def exercise(db):
    pattern = MovementPattern.objects.create(key="horizontal_push", name="Push")
    return Exercise.objects.create(
        pattern=pattern, name="Push-up", difficulty_rank=1,
        rep_range_min=5, rep_range_max=12,
    )


@pytest.fixture
def program(user):
    return Program.objects.create(user=user, is_active=True)


def _session(user, day, reps):
    """A completed session logging one set at ``reps`` (None = never performed)."""
    session = WorkoutSession.objects.create(
        user=user, program_day=day, performed_at=timezone.now(),
        status=WorkoutSession.Status.COMPLETED,
    )
    SetLog.objects.create(
        session=session,
        exercise=Exercise.objects.first(),
        set_index=0,
        actual_reps=reps,
    )
    return session


def test_deletes_sessions_where_nothing_was_performed(user, exercise, program):
    day = ProgramDay.objects.create(program=program, day_index=0, name="Day")
    empty = _session(user, day, None)
    zero = _session(user, day, 0)
    real = _session(user, day, 8)

    _run()

    assert not WorkoutSession.objects.filter(pk=empty.pk).exists()
    assert not WorkoutSession.objects.filter(pk=zero.pk).exists()
    assert WorkoutSession.objects.filter(pk=real.pk).exists()


def test_deleting_a_session_takes_its_set_logs_with_it(user, exercise, program):
    day = ProgramDay.objects.create(program=program, day_index=0, name="Day")
    empty = _session(user, day, None)

    _run()

    assert not SetLog.objects.filter(session_id=empty.pk).exists()


def test_a_session_with_no_sets_at_all_is_deleted(user, exercise, program):
    day = ProgramDay.objects.create(program=program, day_index=0, name="Day")
    bare = WorkoutSession.objects.create(user=user, program_day=day)

    _run()

    assert not WorkoutSession.objects.filter(pk=bare.pk).exists()


def test_one_real_set_saves_the_whole_session(user, exercise, program):
    """A session is kept if *any* set carries a rep, not only if all of them do."""
    day = ProgramDay.objects.create(program=program, day_index=0, name="Day")
    session = WorkoutSession.objects.create(
        user=user, program_day=day, performed_at=timezone.now(),
        status=WorkoutSession.Status.COMPLETED,
    )
    SetLog.objects.create(session=session, exercise=exercise, set_index=0, actual_reps=None)
    SetLog.objects.create(session=session, exercise=exercise, set_index=1, actual_reps=6)

    _run()

    assert WorkoutSession.objects.filter(pk=session.pk).exists()
    assert SetLog.objects.filter(session=session).count() == 2


def test_timed_holds_survive_on_seconds(user, exercise, program):
    """Holds store seconds in actual_reps, so a 30-second plank is real work."""
    day = ProgramDay.objects.create(program=program, day_index=0, name="Day")
    hold = _session(user, day, 30)

    _run()

    assert WorkoutSession.objects.filter(pk=hold.pk).exists()


def test_orphan_extra_days_are_deleted_and_day_zero_kept(user, exercise, program):
    day0 = ProgramDay.objects.create(program=program, day_index=0, name="Day 0")
    day1 = ProgramDay.objects.create(program=program, day_index=1, name="Day 1")
    day2 = ProgramDay.objects.create(program=program, day_index=2, name="Day 2")
    _session(user, day1, None)  # its only session is about to be deleted

    _run()

    assert ProgramDay.objects.filter(pk=day0.pk).exists()
    assert not ProgramDay.objects.filter(pk=day1.pk).exists()
    assert not ProgramDay.objects.filter(pk=day2.pk).exists()


def test_an_extra_day_a_surviving_session_points_at_is_retained(user, exercise, program):
    """History stays intact: the day is the only record of what that session was."""
    day1 = ProgramDay.objects.create(program=program, day_index=1, name="Day 1")
    kept = _session(user, day1, 10)

    _run()

    assert ProgramDay.objects.filter(pk=day1.pk).exists()
    assert WorkoutSession.objects.filter(pk=kept.pk).exists()


def test_running_twice_changes_nothing_more(user, exercise, program):
    day0 = ProgramDay.objects.create(program=program, day_index=0, name="Day 0")
    ProgramDay.objects.create(program=program, day_index=1, name="Day 1")
    _session(user, day0, 9)
    _session(user, day0, None)

    _run()
    after_first = (WorkoutSession.objects.count(), ProgramDay.objects.count())
    _run()

    assert (WorkoutSession.objects.count(), ProgramDay.objects.count()) == after_first

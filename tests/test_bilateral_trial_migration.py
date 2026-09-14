"""Migration 0014: Trial anchors move to their bilateral movements.

Runs the real migration graph — the per-side columns it reads no longer exist on
the current models — so the schema is rolled back to 0013, historical rows are
written, and the migration is applied (and reversed) for real.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

User = get_user_model()

BEFORE = [("the_cauldron", "0013_single_day_history_cleanup")]
AFTER = [("the_cauldron", "0014_bilateral_trial_anchors")]


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(target)
    return MigrationExecutor(connection).loader.project_state(target).apps


def _leaf():
    executor = MigrationExecutor(connection)
    return [n for n in executor.loader.graph.leaf_nodes() if n[0] == "the_cauldron"]


@pytest.fixture
def old_state(transactional_db):
    apps = _migrate(BEFORE)
    yield apps
    _migrate(_leaf())


def _rung(apps, pattern, name, rank, threshold, equipment, anchor=False):
    Exercise = apps.get_model("the_cauldron", "Exercise")
    ex = Exercise.objects.create(
        pattern=pattern, name=name, difficulty_rank=rank, placement_threshold=threshold,
        rep_range_min=5, rep_range_max=12, is_assessment_anchor=anchor,
    )
    ex.required_equipment.set([equipment])
    return ex


def _pre_migration_catalog(apps):
    """The relevant rungs as they stood before #50: unilateral anchors, #49 values."""
    Pattern = apps.get_model("the_cauldron", "MovementPattern")
    Equipment = apps.get_model("the_cauldron", "Equipment")
    bodyweight = Equipment.objects.create(key="bodyweight", name="Bodyweight")
    hinge = Pattern.objects.create(key="hinge", name="Hinge", is_lower_body=True)
    lower = Pattern.objects.create(key="lower_unilateral", name="Lower", is_lower_body=True)
    return {
        "hinge": hinge,
        "lower": lower,
        "Glute Bridge": _rung(apps, hinge, "Glute Bridge", 1, 0, bodyweight),
        "Single-Leg Glute Bridge": _rung(apps, hinge, "Single-Leg Glute Bridge", 2, 5, bodyweight, anchor=True),
        # A parallel rung sharing Single-Leg Glute Bridge's rank.
        "Weighted Hip Thrust": _rung(apps, hinge, "Weighted Hip Thrust", 2, 5, bodyweight),
        "Assisted Nordic Curl": _rung(apps, hinge, "Assisted Nordic Curl", 3, 12, bodyweight),
        "Nordic Curl": _rung(apps, hinge, "Nordic Curl", 4, 15, bodyweight),
        "Assisted Split Squat": _rung(apps, lower, "Assisted Split Squat", 2, 3, bodyweight),
        "Split Squat": _rung(apps, lower, "Split Squat", 4, 8, bodyweight, anchor=True),
        "Pistol Squat": _rung(apps, lower, "Pistol Squat", 9, 18, bodyweight),
    }


def test_anchor_swap_summing_and_one_rung_cap(old_state):
    apps = old_state
    catalog = _pre_migration_catalog(apps)
    Session = apps.get_model("the_cauldron", "AssessmentSession")
    Result = apps.get_model("the_cauldron", "AssessmentResult")
    user = User.objects.create_user(username="bilateral", password="pw12345!")
    session = Session.objects.create(user_id=user.pk, is_active=True, completed_at=timezone.now())

    # 40 + 40 single-leg bridges sum to 80 — uncapped that clears every hinge
    # threshold and lands on Nordic Curl, three rungs above Glute Bridge.
    hinge = Result.objects.create(
        session=session, pattern=catalog["hinge"],
        tested_exercise=catalog["Single-Leg Glute Bridge"],
        placed_exercise=catalog["Glute Bridge"],
        reps_or_seconds=40, left_reps=40, right_reps=40, asymmetry_pct=0,
    )
    # No sides to sum: 12 split squats per side re-read on the squat scale place
    # one rung down (Assisted Split Squat), not all the way to the bottom.
    lower = Result.objects.create(
        session=session, pattern=catalog["lower"],
        tested_exercise=catalog["Split Squat"], placed_exercise=catalog["Split Squat"],
        reps_or_seconds=12,
    )
    # Already on a parallel rank-2 rung and still scoring rank 2: it must stay
    # put, not drift sideways onto Single-Leg Glute Bridge.
    parallel = Result.objects.create(
        session=Session.objects.create(user_id=user.pk, is_active=False, completed_at=timezone.now()),
        pattern=catalog["hinge"], tested_exercise=catalog["Glute Bridge"],
        placed_exercise=catalog["Weighted Hip Thrust"], reps_or_seconds=20,
    )
    # A half-written Trial: an open session with one row.
    open_session = Session.objects.create(user_id=user.pk, is_active=True)
    open_row = Result.objects.create(
        session=open_session, pattern=catalog["hinge"],
        tested_exercise=catalog["Single-Leg Glute Bridge"],
        placed_exercise=catalog["Single-Leg Glute Bridge"],
        reps_or_seconds=6, left_reps=6, right_reps=9,
    )

    apps = _migrate(AFTER)
    Result = apps.get_model("the_cauldron", "AssessmentResult")
    Exercise = apps.get_model("the_cauldron", "Exercise")

    hinge = Result.objects.get(pk=hinge.pk)
    assert hinge.tested_exercise.name == "Glute Bridge"
    assert hinge.reps_or_seconds == 80
    assert hinge.placed_exercise.name == "Single-Leg Glute Bridge"  # capped at +1

    lower = Result.objects.get(pk=lower.pk)
    assert lower.tested_exercise.name == "Squat"  # created by the migration
    assert lower.reps_or_seconds == 12
    assert lower.placed_exercise.name == "Assisted Split Squat"  # one rung down

    assert Result.objects.get(pk=parallel.pk).placed_exercise.name == "Weighted Hip Thrust"

    open_row = Result.objects.get(pk=open_row.pk)
    assert (open_row.tested_exercise.name, open_row.reps_or_seconds) == ("Glute Bridge", 15)

    anchors = set(Exercise.objects.filter(is_assessment_anchor=True).values_list("name", flat=True))
    assert anchors == {"Glute Bridge", "Squat"}
    assert Exercise.objects.get(name="Nordic Curl").placement_threshold == 60

    # Reversible: the old anchors come back; summed reps cannot be split again.
    apps = _migrate(BEFORE)
    Result = apps.get_model("the_cauldron", "AssessmentResult")
    hinge = Result.objects.get(pk=hinge.pk)
    assert hinge.tested_exercise.name == "Single-Leg Glute Bridge"
    assert hinge.reps_or_seconds == 80
    assert Result.objects.get(pk=lower.pk).tested_exercise.name == "Split Squat"

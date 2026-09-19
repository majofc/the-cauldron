"""Data migrations 0019 (#46) and 0020 (#61).

0019 demotes anyone on a LIVE program off the floor One-Arm Push-up onto the new
Elevated One-Arm Push-up rung; 0020 gives every live program a grip prescription
at a rung the user can actually perform. Both are run for real against the
migration graph — the state before them has neither catalog row, so a fixture
could not stand in for it.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

User = get_user_model()

BEFORE = [("the_cauldron", "0018_exercise_alternative_equipment_and_more")]
AFTER = [("the_cauldron", "0020_grip_pattern_backfill")]


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


def _catalog(apps):
    """The horizontal-push tail and the equipment rows, as they stood pre-#46."""
    Pattern = apps.get_model("the_cauldron", "MovementPattern")
    Equipment = apps.get_model("the_cauldron", "Equipment")
    Exercise = apps.get_model("the_cauldron", "Exercise")

    bodyweight = Equipment.objects.create(key="bodyweight", name="Bodyweight")
    bar = Equipment.objects.create(key="pullup_bar", name="Pull-up Bar")
    push = Pattern.objects.create(key="horizontal_push", name="Horizontal Push")

    typewriter = Exercise.objects.create(
        pattern=push, name="Typewriter Push-up", difficulty_rank=8,
        placement_threshold=38, rep_range_min=3, rep_range_max=6,
    )
    one_arm = Exercise.objects.create(
        pattern=push, name="One-Arm Push-up", difficulty_rank=9,
        placement_threshold=45, rep_range_min=1, rep_range_max=5,
        regression=typewriter,
    )
    typewriter.progression = one_arm
    typewriter.save()
    for ex in (typewriter, one_arm):
        ex.required_equipment.set([bodyweight])
    return {"push": push, "bodyweight": bodyweight, "bar": bar,
            "typewriter": typewriter, "one_arm": one_arm}


def _program(apps, username, exercise, catalog, is_active=True, equipment=()):
    """A program with one day holding a single prescription for ``exercise``."""
    Program = apps.get_model("the_cauldron", "Program")
    ProgramDay = apps.get_model("the_cauldron", "ProgramDay")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
    Profile = apps.get_model("the_cauldron", "UserEquipmentProfile")

    user = User.objects.create_user(username=username, password="x")
    profile = Profile.objects.create(user_id=user.pk)
    if equipment:
        profile.equipment.set(equipment)
    program = Program.objects.create(user_id=user.pk, is_active=is_active)
    day = ProgramDay.objects.create(program=program, day_index=0, name="Day 1")
    presc = PrescribedExercise.objects.create(
        day=day, pattern=catalog["push"], exercise=exercise,
        target_sets=3, target_reps_min=1, target_reps_max=5,
        target_rest_seconds=105, order=0, sessions_at_top=2,
        pending_progression=None,
    )
    return {"user": user, "program": program, "day": day, "presc": presc}


class TestElevatedOneArmDemotion:
    def test_active_prescription_is_demoted_and_counters_reset(self, old_state):
        catalog = _catalog(old_state)
        live = _program(old_state, "live", catalog["one_arm"], catalog)
        live["presc"].pending_progression = catalog["one_arm"]
        live["presc"].save()

        apps = _migrate(AFTER)
        PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
        presc = PrescribedExercise.objects.get(pk=live["presc"].pk)

        assert presc.exercise.name == "Elevated One-Arm Push-up"
        assert presc.exercise.difficulty_rank == 9
        assert presc.sessions_at_top == 0
        assert presc.pending_progression is None

    def test_inactive_program_is_left_alone(self, old_state):
        catalog = _catalog(old_state)
        retired = _program(
            old_state, "retired", catalog["one_arm"], catalog, is_active=False
        )

        apps = _migrate(AFTER)
        PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
        presc = PrescribedExercise.objects.get(pk=retired["presc"].pk)

        assert presc.exercise.name == "One-Arm Push-up"
        assert presc.sessions_at_top == 2

    def test_history_is_never_rewritten(self, old_state):
        """AssessmentResult and SetLog are records of what happened."""
        catalog = _catalog(old_state)
        live = _program(old_state, "histuser", catalog["one_arm"], catalog)
        AssessmentSession = old_state.get_model("the_cauldron", "AssessmentSession")
        AssessmentResult = old_state.get_model("the_cauldron", "AssessmentResult")
        session = AssessmentSession.objects.create(
            user_id=live["user"].pk, is_active=False
        )
        result = AssessmentResult.objects.create(
            session=session, pattern=catalog["push"],
            tested_exercise=catalog["one_arm"], placed_exercise=catalog["one_arm"],
            reps_or_seconds=3,
        )

        apps = _migrate(AFTER)
        AssessmentResult = apps.get_model("the_cauldron", "AssessmentResult")
        after = AssessmentResult.objects.get(pk=result.pk)
        assert after.placed_exercise.name == "One-Arm Push-up"

    def test_one_arm_push_up_moves_to_rank_10(self, old_state):
        catalog = _catalog(old_state)
        _program(old_state, "ranker", catalog["one_arm"], catalog)

        apps = _migrate(AFTER)
        Exercise = apps.get_model("the_cauldron", "Exercise")
        assert Exercise.objects.get(name="One-Arm Push-up").difficulty_rank == 10
        assert Exercise.objects.get(name="Elevated One-Arm Push-up").is_per_side


class TestGripBackfill:
    def test_bar_owner_gets_the_assisted_hang(self, old_state):
        catalog = _catalog(old_state)
        owner = _program(
            old_state, "barowner", catalog["typewriter"], catalog,
            equipment=[catalog["bodyweight"], catalog["bar"]],
        )

        apps = _migrate(AFTER)
        PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
        grip = PrescribedExercise.objects.get(
            day__program_id=owner["program"].pk, pattern__key="grip"
        )
        assert grip.exercise.name == "Assisted Bar Hang"
        assert grip.day.day_index == 0
        assert grip.target_sets == 3
        assert grip.target_rest_seconds == 90
        assert grip.target_load is None
        # Ordered last on the day.
        assert grip.order == 1

    def test_user_without_a_bar_gets_the_towel_wring(self, old_state):
        catalog = _catalog(old_state)
        barefoot = _program(
            old_state, "nobar", catalog["typewriter"], catalog,
            equipment=[catalog["bodyweight"]],
        )

        apps = _migrate(AFTER)
        PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
        grip = PrescribedExercise.objects.get(
            day__program_id=barefoot["program"].pk, pattern__key="grip"
        )
        assert grip.exercise.name == "Towel Wring Hold"

    def test_inactive_program_gets_nothing(self, old_state):
        catalog = _catalog(old_state)
        retired = _program(
            old_state, "retired2", catalog["typewriter"], catalog, is_active=False,
            equipment=[catalog["bodyweight"], catalog["bar"]],
        )

        apps = _migrate(AFTER)
        PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
        assert not PrescribedExercise.objects.filter(
            day__program_id=retired["program"].pk, pattern__key="grip"
        ).exists()

    def test_no_assessment_result_is_written(self, old_state):
        catalog = _catalog(old_state)
        _program(
            old_state, "noresult", catalog["typewriter"], catalog,
            equipment=[catalog["bodyweight"], catalog["bar"]],
        )

        apps = _migrate(AFTER)
        AssessmentResult = apps.get_model("the_cauldron", "AssessmentResult")
        assert not AssessmentResult.objects.filter(pattern__key="grip").exists()

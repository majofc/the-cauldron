# Inserts the Elevated One-Arm Push-up rung (#46) between Typewriter Push-up
# and One-Arm Push-up, and demotes anyone sitting on the floor One-Arm Push-up
# in a LIVE program onto the new rung.
#
# The catalog rows are created here, not left to ``seed_forge``: the container
# runs ``migrate`` BEFORE ``seed_forge``, so a migration that only re-pointed
# prescriptions would find no rung to point them at on the first boot after
# deploy. Values mirror ``seed_forge.LADDERS["horizontal_push"]`` exactly and the
# seeder's ``update_or_create`` rewrites them idempotently right after, including
# the regression/progression links, which are deliberately left to it.
#
# Accepted trade-off, per the ticket: a user genuinely performing floor One-Arm
# Push-ups on an active program is demoted one rung.

from django.db import migrations

NEW_RUNG = "Elevated One-Arm Push-up"
TOP_RUNG = "One-Arm Push-up"
PATTERN = "horizontal_push"
MUSCLES = ["chest", "triceps", "front_delts", "obliques"]


def forwards(apps, schema_editor):
    MovementPattern = apps.get_model("the_cauldron", "MovementPattern")
    Exercise = apps.get_model("the_cauldron", "Exercise")
    Equipment = apps.get_model("the_cauldron", "Equipment")
    Muscle = apps.get_model("the_cauldron", "Muscle")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")

    pattern = MovementPattern.objects.filter(key=PATTERN).first()
    if pattern is None:
        return  # fresh database — seed_forge will lay the ladder down in order

    top = Exercise.objects.filter(pattern=pattern, name=TOP_RUNG).first()
    if top is None:
        return

    # One-Arm Push-up moves up to make room for the new rank 9.
    if top.difficulty_rank != 10:
        top.difficulty_rank = 10
        top.save(update_fields=["difficulty_rank"])

    elevated, created = Exercise.objects.get_or_create(
        pattern=pattern,
        name=NEW_RUNG,
        defaults={
            "difficulty_rank": 9,
            "progression_mode": "difficulty",
            "rep_range_min": 3,
            "rep_range_max": 6,
            "is_timed": False,
            "is_per_side": True,
            # See seed_forge: 41 keeps thresholds non-decreasing by rank.
            "placement_threshold": 41,
            "cues": (
                "One hand on a box or bench, other hand behind the back. Feet "
                "wide, hips square. Lower the surface as it gets easy."
            ),
            "rest_seconds": 105,
            "is_assessment_anchor": False,
        },
    )
    if created:
        bodyweight = Equipment.objects.filter(key="bodyweight").first()
        if bodyweight:
            elevated.required_equipment.set([bodyweight])
        elevated.muscles.set(Muscle.objects.filter(key__in=MUSCLES))

    # Live prescriptions only: history (AssessmentResult, SetLog) and programs
    # the user has already retired are records, not plans, and stay as they are.
    live = PrescribedExercise.objects.filter(
        exercise=top, day__program__is_active=True
    )
    live.update(exercise=elevated, sessions_at_top=0)
    PrescribedExercise.objects.filter(
        pending_progression=top, day__program__is_active=True
    ).update(pending_progression=None)


def backwards(apps, schema_editor):
    """Put live prescriptions back on One-Arm Push-up and restore its rank.

    The new rung itself is left in the catalog: re-seeding would recreate it and
    deleting it would cascade into SetLog history.
    """
    MovementPattern = apps.get_model("the_cauldron", "MovementPattern")
    Exercise = apps.get_model("the_cauldron", "Exercise")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")

    pattern = MovementPattern.objects.filter(key=PATTERN).first()
    if pattern is None:
        return
    elevated = Exercise.objects.filter(pattern=pattern, name=NEW_RUNG).first()
    top = Exercise.objects.filter(pattern=pattern, name=TOP_RUNG).first()
    if not (elevated and top):
        return
    PrescribedExercise.objects.filter(
        exercise=elevated, day__program__is_active=True
    ).update(exercise=top, sessions_at_top=0)
    if top.difficulty_rank != 9:
        top.difficulty_rank = 9
        top.save(update_fields=["difficulty_rank"])


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0018_exercise_alternative_equipment_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

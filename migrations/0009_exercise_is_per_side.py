"""Add ``Exercise.is_per_side`` and backfill it, then fix existing prescriptions.

Per-side (unilateral) movements are worked one side at a time, so their rep
targets must be even. Until now only the ``lower_unilateral`` *pattern* implied
per-side handling, which both missed the archer/typewriter upper-body moves and
wrongly included the two-legged Goblet/Back Squat rows sitting on that ladder.

⚠️ Production: the data migration below is what corrects existing users'
prescriptions (odd rep targets → rounded up to even). It must run against the
production database on deploy, not only locally.
"""

from django.db import migrations, models

# Keep in sync with seed_forge.PER_SIDE.
PER_SIDE = [
    "Assisted Split Squat", "Split Squat", "Bulgarian Split Squat",
    "Dumbbell Bulgarian Split Squat", "Assisted Pistol Squat", "Pistol Squat",
    "Shrimp Squat", "Dragon Squat",
    "Archer Push-up", "Typewriter Push-up", "One-Arm Push-up",
    "Archer Pull-up", "Archer Chin-up",
    "Single-Leg Glute Bridge",
]


def _even_up(n):
    """Round UP to the nearest even int — mirrors progression.even_up."""
    return n if n % 2 == 0 else n + 1


def backfill(apps, schema_editor):
    Exercise = apps.get_model("the_cauldron", "Exercise")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")

    Exercise.objects.filter(name__in=PER_SIDE).update(is_per_side=True)

    # Existing prescriptions on per-side movements may carry odd targets (e.g. a
    # Typewriter Push-up prescribed 3-6). Timed holds are seconds, not reps.
    stale = PrescribedExercise.objects.filter(
        exercise__is_per_side=True, exercise__is_timed=False
    )
    updated = []
    for presc in stale:
        reps_min = _even_up(presc.target_reps_min)
        reps_max = _even_up(presc.target_reps_max)
        if (reps_min, reps_max) != (presc.target_reps_min, presc.target_reps_max):
            presc.target_reps_min = reps_min
            presc.target_reps_max = reps_max
            updated.append(presc)
    if updated:
        PrescribedExercise.objects.bulk_update(
            updated, ["target_reps_min", "target_reps_max"]
        )


def unbackfill(apps, schema_editor):
    """Only the flag is reversible — the rounded rep targets are left as they
    are (the original odd values are not recoverable, and even targets remain
    valid prescriptions)."""
    Exercise = apps.get_model("the_cauldron", "Exercise")
    Exercise.objects.filter(name__in=PER_SIDE).update(is_per_side=False)


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0008_exercise_grip"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercise",
            name="is_per_side",
            field=models.BooleanField(
                default=False,
                help_text="True for movements performed one side at a time; rep "
                "targets are forced even and the to-failure set is logged per side.",
            ),
        ),
        migrations.RunPython(backfill, unbackfill),
    ]

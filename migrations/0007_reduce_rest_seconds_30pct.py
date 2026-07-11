from django.db import migrations


# The rest fields only ever hold one of seed_forge.rest_for's baselines, so a
# ~30%-cut lookup (rounded to the nearest 5s) covers every legitimate row and,
# unlike a blind multiply, is idempotent: values already at a new baseline are
# absent from the map and left untouched. This matters because startup runs
# `migrate` then `seed_forge` — seed re-asserts the new Exercise values, and a
# re-run of this migration must not reduce them a second time (40 → 30 → …).
REDUCED = {
    150: 105,
    120: 85,
    75: 55,
    60: 40,
}


def reduce_rests(apps, schema_editor):
    Exercise = apps.get_model("the_cauldron", "Exercise")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")

    for old, new in REDUCED.items():
        Exercise.objects.filter(rest_seconds=old).update(rest_seconds=new)
        PrescribedExercise.objects.filter(target_rest_seconds=old).update(
            target_rest_seconds=new
        )


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0006_setlog_left_reps_setlog_right_reps"),
    ]

    # The cut is lossy (75→55 and 60→40 both round off), so leave data in place
    # on reverse rather than guess the pre-reduction values.
    operations = [
        migrations.RunPython(reduce_rests, migrations.RunPython.noop),
    ]

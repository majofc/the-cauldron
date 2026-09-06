"""Clear out the debris the old Today flow left behind.

Opening Today used to write a WorkoutSession plus all its SetLog rows on every
page load, so the history is full of sessions in which nothing was ever
performed. The plan is ephemeral now (nothing is written until the user logs a
real value), which fixes it going forward — this clears what already accumulated.

Two passes:

1. Delete every session with no set carrying at least one rep. Timed holds store
   seconds in ``actual_reps``, so ``>= 1`` reads as "some work happened" for both.
   Their set logs go with them via cascade.
2. Delete the now-unreferenced ``day_index > 0`` program days. Programs are a
   single day from here on and days 1..N are never served again — but a day still
   referenced by a *surviving* session is kept, because ``WorkoutSession.
   program_day`` is the only record of what that session was, and history stays
   intact.

Irreversible: the deleted rows carry no information to restore.
"""

from django.db import migrations


def clean_history(apps, schema_editor):
    WorkoutSession = apps.get_model("the_cauldron", "WorkoutSession")
    ProgramDay = apps.get_model("the_cauldron", "ProgramDay")

    # 1. Sessions in which nothing was actually performed.
    WorkoutSession.objects.exclude(
        set_logs__actual_reps__gte=1
    ).delete()

    # 2. Extra days no surviving session points at.
    still_referenced = WorkoutSession.objects.filter(
        program_day__isnull=False
    ).values_list("program_day_id", flat=True)
    ProgramDay.objects.filter(day_index__gt=0).exclude(
        pk__in=still_referenced
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0012_merge_trial_asymmetry_and_loadables"),
    ]

    operations = [
        migrations.RunPython(clean_history, migrations.RunPython.noop),
    ]

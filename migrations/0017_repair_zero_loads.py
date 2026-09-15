"""Repair load-mode prescriptions that were reset to 0 kg.

With a weightless handle, bar or kettlebell shell, the bare implement is a
buildable load of 0, and program generation / ⇄ swaps prescribed it (#52). The
code no longer does; this fixes the rows already written, for every user:

1. Each active load-mode ``PrescribedExercise`` at ``target_load <= 0`` is
   re-seeded from the user's most recent non-zero load on that exercise
   (``actual_load``, else ``expected_load``), snapped to the nearest load they
   can build. With no history it follows the same rule as a fresh prescription:
   the latest Trial score for the pattern sets it (``trial_seeded_load``), else
   the lightest non-zero buildable load. With nothing buildable at all the load
   is cleared to none rather than left at 0.
2. Sets of an unfinished session scheduled for today that expect 0 on a
   load-mode exercise take the repaired prescription's load (or the same
   computation, for a swapped-in movement).

Band levels are indices where 0 is the lightest real band, so band-loaded
exercises are left alone. Irreversible: the zero loads carry nothing to restore.
"""

from django.db import migrations
from django.db.models import F
from django.utils import timezone

from the_cauldron.services import loads
from the_cauldron.services.progression import nearest_available_load, trial_seeded_load

KEEP = object()


def _history(SetLog, user_id, exercise_id):
    recent = SetLog.objects.filter(
        session__user_id=user_id, exercise_id=exercise_id
    ).order_by(
        F("session__performed_at").desc(nulls_last=True), "-session__created_at", "-set_index"
    )
    for field in ("actual_load", "expected_load"):
        value = recent.filter(**{f"{field}__gt": 0}).values_list(field, flat=True).first()
        if value is not None:
            return value
    return None


def _trial_score(AssessmentResult, user_id, pattern_id):
    return (
        AssessmentResult.objects.filter(
            session__user_id=user_id, session__completed_at__isnull=False, pattern_id=pattern_id
        )
        .order_by("-session__completed_at")
        .values_list("reps_or_seconds", flat=True)
        .first()
    )


def forwards(apps, schema_editor):
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
    SetLog = apps.get_model("the_cauldron", "SetLog")
    UserEquipmentProfile = apps.get_model("the_cauldron", "UserEquipmentProfile")
    AssessmentResult = apps.get_model("the_cauldron", "AssessmentResult")

    profiles = {}

    def profile_for(user_id):
        if user_id not in profiles:
            profiles[user_id] = UserEquipmentProfile.objects.filter(user_id=user_id).first()
        return profiles[user_id]

    def repaired_load(user_id, exercise):
        """The load to write, or ``KEEP`` to leave the row alone. ``None`` (no
        load) when the user can build nothing prescribable at all."""
        profile = profile_for(user_id)
        if profile is not None and loads.implement_for(profile, exercise) == "bands":
            return KEEP
        if profile is None:
            return None
        history = _history(SetLog, user_id, exercise.pk)
        if history is not None:
            return nearest_available_load(profile, exercise, history)
        return trial_seeded_load(
            profile, exercise, _trial_score(AssessmentResult, user_id, exercise.pattern_id)
        )

    for presc in PrescribedExercise.objects.filter(
        day__program__is_active=True,
        exercise__progression_mode="load",
        target_load__lte=0,
    ).select_related("exercise", "day__program"):
        user_id = presc.day.program.user_id
        load = repaired_load(user_id, presc.exercise)
        if load is KEEP:
            continue
        print(f"  PrescribedExercise {presc.pk} ({presc.exercise.name}): 0 -> {load}")
        presc.target_load = load
        presc.save(update_fields=["target_load"])

    for set_log in SetLog.objects.filter(
        session__status="planned",
        session__scheduled_for=timezone.localdate(),
        exercise__progression_mode="load",
        expected_load__lte=0,
    ).select_related("exercise", "session", "prescribed_exercise"):
        presc = set_log.prescribed_exercise
        if presc is not None and presc.target_load and presc.target_load > 0:
            load = presc.target_load
        else:
            load = repaired_load(set_log.session.user_id, set_log.exercise)
        if load is KEEP:
            continue
        set_log.expected_load = load
        set_log.save(update_fields=["expected_load"])


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0016_remove_asymmetry_and_rowing_key"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

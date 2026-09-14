"""Catalog: rename the band rollout and remove the rowing machine.

Runs before ``seed_forge``, which adds the ``ab_wheel`` equipment and its two
rollout rungs right after (seeding is additive and idempotent).

1. ``Ab Wheel / Band Rollout`` → ``Band Rollout``. The seed matches rows by
   (pattern, name), so without the rename it would create a second row and
   leave the old one behind.
2. The ``Rowing Machine`` exercise and ``rowing_machine`` equipment are deleted.
   Every row that references the exercise is moved off it first — several of
   those foreign keys are PROTECT:
   - live prescriptions go to the closest stand-in the user can perform
     (``progression.find_substitute``, the same rule ``sync_program_equipment``
     applies), falling back to Australian Row — the rung it shared a rank with;
   - a parked ``pending_progression`` moves up to the next harder rung on the
     old link chain the user can perform, or is cleared — a same-or-easier
     stand-in would re-park the unlock on the rung they are already on;
   - logged sets and Trial results are repointed to Australian Row.
   Profile ownership rows and the exercise's equipment links go with the
   deletes; a user's block on it cascades.

Reverse restores the rollout's old name only: the rowing machine's rows are not
recreated.
"""

from django.db import migrations

from the_cauldron.services.progression import (
    find_substitute,
    nearest_available_load,
    rep_targets_for,
)

OLD_ROLLOUT = "Ab Wheel / Band Rollout"
NEW_ROLLOUT = "Band Rollout"


def forwards(apps, schema_editor):
    Exercise = apps.get_model("the_cauldron", "Exercise")
    Equipment = apps.get_model("the_cauldron", "Equipment")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
    SetLog = apps.get_model("the_cauldron", "SetLog")
    AssessmentResult = apps.get_model("the_cauldron", "AssessmentResult")
    UserEquipmentProfile = apps.get_model("the_cauldron", "UserEquipmentProfile")
    BlockedExercise = apps.get_model("the_cauldron", "BlockedExercise")

    if not Exercise.objects.filter(name=NEW_ROLLOUT).exists():
        Exercise.objects.filter(name=OLD_ROLLOUT).update(name=NEW_ROLLOUT)

    rowing = Exercise.objects.filter(name="Rowing Machine").first()
    if rowing is not None:
        australian = Exercise.objects.filter(
            pattern_id=rowing.pattern_id, name="Australian Row"
        ).first()
        pool = [
            ex
            for ex in Exercise.objects.filter(pattern_id=rowing.pattern_id)
            .exclude(pk=rowing.pk)
            .prefetch_related("required_equipment")
        ]

        def profile_for(user_id):
            return UserEquipmentProfile.objects.filter(user_id=user_id).first()

        def performable_for(user_id):
            profile = profile_for(user_id)
            owned = {"bodyweight"}
            if profile is not None:
                owned |= set(profile.equipment.values_list("key", flat=True))
            owned.discard("rowing_machine")
            blocked = set(
                BlockedExercise.objects.filter(user_id=user_id).values_list(
                    "exercise_id", flat=True
                )
            )
            return [
                ex
                for ex in pool
                if ex.pk not in blocked
                and ({e.key for e in ex.required_equipment.all()} or {"bodyweight"}) <= owned
            ]

        def stand_in(user_id):
            return find_substitute(rowing, performable_for(user_id))

        def next_harder(user_id):
            """The first rung above the rowing machine on its old link chain
            that the user can perform — what the unlock was really pointing up to."""
            allowed = {ex.pk for ex in performable_for(user_id)}
            seen, rung = {rowing.pk}, rowing.progression
            while rung is not None and rung.pk not in seen:
                if rung.pk in allowed:
                    return rung
                seen.add(rung.pk)
                rung = rung.progression
            return None

        for presc in PrescribedExercise.objects.filter(exercise=rowing).select_related(
            "day__program"
        ):
            target = stand_in(presc.day.program.user_id) or australian
            if target is None:
                continue
            presc.exercise = target
            presc.target_reps_min, presc.target_reps_max = rep_targets_for(
                target, target.rep_range_min, target.rep_range_max
            )
            presc.target_rest_seconds = target.rest_seconds
            profile = profile_for(presc.day.program.user_id)
            presc.target_load = (
                nearest_available_load(profile, target, None)
                if target.progression_mode == "load" and profile is not None
                else None
            )
            presc.sessions_at_top = 0
            presc.save()

        for presc in PrescribedExercise.objects.filter(
            pending_progression=rowing
        ).select_related("day__program"):
            target = next_harder(presc.day.program.user_id)
            if target is not None and target.pk == presc.exercise_id:
                target = None
            presc.pending_progression = target
            presc.save(update_fields=["pending_progression"])

        if australian is not None:
            SetLog.objects.filter(exercise=rowing).update(exercise=australian)
            AssessmentResult.objects.filter(tested_exercise=rowing).update(
                tested_exercise=australian
            )
            AssessmentResult.objects.filter(placed_exercise=rowing).update(
                placed_exercise=australian
            )
            rowing.delete()

    Equipment.objects.filter(key="rowing_machine").delete()


def backwards(apps, schema_editor):
    Exercise = apps.get_model("the_cauldron", "Exercise")
    if not Exercise.objects.filter(name=OLD_ROLLOUT).exists():
        Exercise.objects.filter(name=NEW_ROLLOUT).update(name=OLD_ROLLOUT)


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0014_bilateral_trial_anchors"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

# Gives every live program a grip prescription (#61) without asking the user to
# retake the Trial.
#
# As in 0019, the rows this needs are created here rather than left to
# ``seed_forge``: the container runs ``migrate`` first. Only the pattern and the
# two entry rungs are laid down — the rest of the ladder (and the links between
# rungs) arrives with the seeder immediately after, which rewrites these rows
# idempotently with the same values.
#
# The rung a user lands on is the LOWEST-rank one they can perform: Assisted Bar
# Hang when they own a bar or rings, otherwise Towel Wring Hold. No
# AssessmentResult is written — the live prescription is the source of truth for
# the current rung, exactly as ``set_rung`` treats it.

from django.db import migrations

PATTERN_KEY = "grip"
WRING = "Towel Wring Hold"
ASSISTED = "Assisted Bar Hang"

# (name, rank, rmin, rmax, threshold, cues) — mirrors seed_forge.LADDERS["grip"].
ENTRY_RUNGS = [
    (
        WRING, 1, 15, 40, 0,
        "Twist a rolled towel as hard as you can, both directions. Squeeze, don't yank.",
    ),
    (
        ASSISTED, 2, 20, 45, 0,
        "Feet on the floor or a box taking part of your weight; shoulders packed down.",
    ),
]


def forwards(apps, schema_editor):
    MovementPattern = apps.get_model("the_cauldron", "MovementPattern")
    Exercise = apps.get_model("the_cauldron", "Exercise")
    Equipment = apps.get_model("the_cauldron", "Equipment")
    Muscle = apps.get_model("the_cauldron", "Muscle")
    Program = apps.get_model("the_cauldron", "Program")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")
    UserEquipmentProfile = apps.get_model("the_cauldron", "UserEquipmentProfile")

    if not Program.objects.filter(is_active=True).exists():
        return  # fresh database — nothing to backfill, seed_forge does the rest

    pattern, _ = MovementPattern.objects.get_or_create(
        key=PATTERN_KEY,
        defaults={
            "name": "Grip / Forearms",
            "primary_muscles": "Forearms, hands",
            "is_lower_body": False,
        },
    )
    bodyweight = Equipment.objects.filter(key="bodyweight").first()
    hang_options = list(Equipment.objects.filter(key__in=["pullup_bar", "rings"]))
    forearms = list(Muscle.objects.filter(key__in=["forearms", "lats", "traps"]))

    rungs = {}
    for name, rank, rmin, rmax, threshold, cues in ENTRY_RUNGS:
        ex, created = Exercise.objects.get_or_create(
            pattern=pattern,
            name=name,
            defaults={
                "difficulty_rank": rank,
                "progression_mode": "difficulty",
                "rep_range_min": rmin,
                "rep_range_max": rmax,
                "is_timed": True,
                "is_per_side": False,
                "placement_threshold": threshold,
                "cues": cues,
                # Grip holds rest 90s (seed_forge.rest_for), not the 40s of a plank.
                "rest_seconds": 90,
                "is_assessment_anchor": True,
            },
        )
        if created:
            if bodyweight:
                ex.required_equipment.set([bodyweight])
            if name == ASSISTED and hang_options:
                # "A bar OR rings" — any-of, so a rings-only user can do it.
                ex.alternative_equipment.set(hang_options)
            ex.muscles.set(forearms if name != WRING else [
                m for m in forearms if m.key == "forearms"
            ])
        rungs[name] = ex

    for program in Program.objects.filter(is_active=True).select_related("user"):
        day = (
            program.days.order_by("day_index").first()
            if hasattr(program, "days")
            else None
        )
        if day is None:
            continue
        if PrescribedExercise.objects.filter(day__program=program, pattern=pattern).exists():
            continue  # already has grip (re-run, or a program forged post-#61)

        profile = UserEquipmentProfile.objects.filter(user_id=program.user_id).first()
        owned = (
            set(profile.equipment.values_list("key", flat=True)) if profile else set()
        )
        can_hang = bool(owned & {"pullup_bar", "rings"})
        exercise = rungs[ASSISTED] if can_hang else rungs[WRING]

        last_order = (
            PrescribedExercise.objects.filter(day=day)
            .order_by("-order")
            .values_list("order", flat=True)
            .first()
        )
        PrescribedExercise.objects.create(
            day=day,
            pattern=pattern,
            exercise=exercise,
            target_sets=3,
            target_reps_min=exercise.rep_range_min,
            target_reps_max=exercise.rep_range_max,
            target_load=None,  # difficulty-mode rung: no load
            target_rest_seconds=exercise.rest_seconds,
            order=(last_order + 1) if last_order is not None else 0,
            sessions_at_top=0,
        )


def backwards(apps, schema_editor):
    """Drop the grip prescriptions again. The catalog rows stay: deleting them
    would cascade into any SetLog already written against them."""
    MovementPattern = apps.get_model("the_cauldron", "MovementPattern")
    PrescribedExercise = apps.get_model("the_cauldron", "PrescribedExercise")

    pattern = MovementPattern.objects.filter(key=PATTERN_KEY).first()
    if pattern is None:
        return
    PrescribedExercise.objects.filter(pattern=pattern).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0019_elevated_one_arm_pushup"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

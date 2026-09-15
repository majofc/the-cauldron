"""Move every Trial anchor to its bilateral movement and migrate past results.

Runs BEFORE ``seed_forge`` on deploy (the Dockerfile migrates first), so it must
not rely on the seed having landed the new catalog. It therefore:

1. Asserts the catalog values placement depends on: the bodyweight ``Squat``
   rung (created if missing), every rung's ``difficulty_rank`` and
   ``placement_threshold`` on the bilateral anchor scale, and the anchor flags.
   ``LADDERS`` is a snapshot of ``seed_forge.LADDERS`` at this migration — the
   seed re-asserts the same values (and everything else) right after.
2. For every ``AssessmentResult``:
   - repoints ``tested_exercise`` to the pattern's new anchor,
   - sums ``left_reps + right_reps`` into ``reps_or_seconds`` where both exist,
   - recomputes ``placed_exercise`` against the snapshot thresholds on the
     user's equipment-eligible, unblocked ladder, capped at one rung (rank step)
     either side of the current placement — an uncapped 40+40 single-leg bridge
     sums to 80 and would jump several rungs — and never moved sideways onto a
     parallel rung of the same rank (see ``_replace``),
   - prints each row whose placement moved.

Results are only ever written by one Trial POST, so an open retake has none and
a half-written session is migrated row by row like any other.

Reverse is best-effort: tested exercises go back to the old unilateral anchors;
summed reps cannot be split again and placements are left where they are. The
schema migration that follows re-adds the per-side columns empty.
"""

from django.db import migrations

OLD_ANCHORS = {
    "horizontal_push": "Incline Archer Push-up",
    "vertical_pull": "Single-Arm Australian Row",
    "vertical_push": "Pike Push-up",
    "lower_unilateral": "Split Squat",
    "core_anti_extension": "Plank",
    "hinge": "Single-Leg Glute Bridge",
}

NEW_ANCHORS = {
    "horizontal_push": "Push-up",
    "vertical_pull": "Australian Row",
    "vertical_push": "Pike Push-up",
    "lower_unilateral": "Squat",
    "core_anti_extension": "Plank",
    "hinge": "Glute Bridge",
}

# pattern -> {exercise name: (difficulty_rank, placement_threshold)}
LADDERS = {
    "horizontal_push": {
        "Wall Push-up": (1, 0), "Incline Push-up": (2, 2), "Knee Push-up": (3, 5),
        "Push-up": (4, 10), "Dumbbell Bench Press": (4, 10),
        "Incline Archer Push-up": (5, 20), "Barbell Bench Press": (5, 20),
        "Diamond Push-up": (6, 25), "Archer Push-up": (7, 30),
        "Typewriter Push-up": (8, 38), "One-Arm Push-up": (9, 45),
    },
    "vertical_pull": {
        "Band-Assisted Row": (1, 0), "Australian Row": (2, 3),
        "Single-Arm Australian Row": (3, 12), "Dumbbell Row": (3, 12),
        "Negative Pull-up": (4, 20), "Band-Assisted Pull-up": (4, 20),
        "Pull-up": (5, 28), "Chin-up": (5, 28),
        "Archer Pull-up": (6, 40), "Archer Chin-up": (6, 40),
    },
    "vertical_push": {
        "Incline Pike Push-up": (1, 0), "Pike Push-up": (2, 4),
        "Wall Handstand Hold": (3, 8), "Dumbbell Shoulder Press": (3, 8),
        "Assisted Handstand Push-up": (4, 12), "Barbell Overhead Press": (4, 12),
    },
    "lower_unilateral": {
        "Squat": (1, 0), "Assisted Split Squat": (2, 10), "Goblet Squat": (3, 20),
        "Split Squat": (4, 30), "Bulgarian Split Squat": (5, 40),
        "Barbell Back Squat": (6, 50), "Dumbbell Bulgarian Split Squat": (7, 60),
        "Assisted Pistol Squat": (8, 75), "Pistol Squat": (9, 95),
        "Shrimp Squat": (10, 120), "Dragon Squat": (11, 150),
    },
    "core_anti_extension": {
        "Knee Plank": (1, 0), "Plank": (2, 20), "Extended Plank": (3, 45),
        "RKC Plank": (4, 60), "Hollow Body Hold": (4, 60),
        # Renamed "Band Rollout" by 0015; either name carries the same rung.
        "Ab Wheel / Band Rollout": (5, 75), "Band Rollout": (5, 75),
    },
    "hinge": {
        "Glute Bridge": (1, 0), "Single-Leg Glute Bridge": (2, 15),
        "Dumbbell Romanian Deadlift": (2, 15), "Kettlebell Swing": (2, 15),
        "Assisted Nordic Curl": (3, 35), "Barbell Romanian Deadlift": (3, 35),
        "Nordic Curl": (4, 60),
    },
}

# Being removed from the catalog by 0015 — never a placement target.
RETIRED = {"Rowing Machine"}


def _assert_catalog(apps):
    MovementPattern = apps.get_model("the_cauldron", "MovementPattern")
    Exercise = apps.get_model("the_cauldron", "Exercise")
    Equipment = apps.get_model("the_cauldron", "Equipment")

    lower = MovementPattern.objects.filter(key="lower_unilateral").first()
    if lower is not None:
        squat, created = Exercise.objects.get_or_create(
            pattern=lower,
            name="Squat",
            defaults={
                "difficulty_rank": 1,
                "progression_mode": "difficulty",
                "rep_range_min": 10,
                "rep_range_max": 20,
                "rest_seconds": 55,
                "placement_threshold": 0,
                "cues": "Feet shoulder-width; sit hips back and down, chest tall.",
            },
        )
        bodyweight = Equipment.objects.filter(key="bodyweight").first()
        if created and bodyweight is not None:
            squat.required_equipment.set([bodyweight])

    anchors = set(NEW_ANCHORS.values())
    for pattern_key, rungs in LADDERS.items():
        for name, (rank, threshold) in rungs.items():
            Exercise.objects.filter(pattern__key=pattern_key, name=name).update(
                difficulty_rank=rank, placement_threshold=threshold
            )
        Exercise.objects.filter(pattern__key=pattern_key).update(is_assessment_anchor=False)
        Exercise.objects.filter(
            pattern__key=pattern_key, name__in=anchors
        ).update(is_assessment_anchor=True)


def _ladder_for(apps, pattern_id, owned, blocked):
    """The user's eligible rungs for a pattern, easiest first."""
    Exercise = apps.get_model("the_cauldron", "Exercise")
    rungs = []
    for ex in Exercise.objects.filter(pattern_id=pattern_id).prefetch_related(
        "required_equipment"
    ):
        if ex.name in RETIRED or ex.pk in blocked:
            continue
        required = {e.key for e in ex.required_equipment.all()} or {"bodyweight"}
        if required <= owned:
            rungs.append(ex)
    return sorted(rungs, key=_order)


def _order(ex):
    return (ex.difficulty_rank, ex.name)


def _placed_rank(ladder, score):
    """The rank ``place_from_assessment`` would reach: the last rung, in ladder
    order, whose threshold the score still clears."""
    rank = ladder[0].difficulty_rank
    for ex in ladder:
        if score >= ex.placement_threshold:
            rank = ex.difficulty_rank
        else:
            break
    return rank


def _replace(ladder, score, current):
    """Where a result lands after re-placement.

    Capping and choosing work on *ranks*, not rows, because parallel rungs share
    a rank (Push-up / Dumbbell Bench Press, Pull-up / Chin-up). The target rank
    is clamped to one rank step either side of the current placement; if that is
    the current rank the current rung is kept, so no result drifts sideways onto
    a different mode or grip. Otherwise the rung matching the current one's
    progression mode and grip wins, then the easiest name for determinism.
    """
    target = _placed_rank(ladder, score)
    if current is not None:
        ranks = sorted({ex.difficulty_rank for ex in ladder} | {current.difficulty_rank})
        at = ranks.index(current.difficulty_rank)
        low, high = ranks[max(at - 1, 0)], ranks[min(at + 1, len(ranks) - 1)]
        target = min(max(target, low), high)
        if target == current.difficulty_rank and current.pk in {ex.pk for ex in ladder}:
            return current
    at_rank = [ex for ex in ladder if ex.difficulty_rank == target]
    if not at_rank:
        # Only reachable when the target is the current, no-longer-eligible rank:
        # take the nearest eligible rank below it, else above.
        below = [ex for ex in ladder if ex.difficulty_rank < target]
        at_rank = [ex for ex in (below or ladder)
                   if ex.difficulty_rank == (below[-1] if below else ladder[0]).difficulty_rank]

    def preference(ex):
        if current is None:
            return (0, 0, ex.name)
        return (ex.progression_mode != current.progression_mode, ex.grip != current.grip, ex.name)

    return min(at_rank, key=preference)


def forwards(apps, schema_editor):
    AssessmentResult = apps.get_model("the_cauldron", "AssessmentResult")
    Exercise = apps.get_model("the_cauldron", "Exercise")
    UserEquipmentProfile = apps.get_model("the_cauldron", "UserEquipmentProfile")
    BlockedExercise = apps.get_model("the_cauldron", "BlockedExercise")

    _assert_catalog(apps)

    anchor_by_pattern = {
        ex.pattern_id: ex
        for ex in Exercise.objects.select_related("pattern").filter(
            name__in=NEW_ANCHORS.values()
        )
        if NEW_ANCHORS.get(ex.pattern.key) == ex.name
    }

    context = {}  # user_id -> (owned, blocked)
    moved = 0
    results = AssessmentResult.objects.select_related(
        "session", "pattern", "placed_exercise"
    ).order_by("session__created_at")
    for result in results:
        user_id = result.session.user_id
        if user_id not in context:
            profile = UserEquipmentProfile.objects.filter(user_id=user_id).first()
            owned = {"bodyweight"}
            if profile is not None:
                owned |= set(profile.equipment.values_list("key", flat=True))
            blocked = set(
                BlockedExercise.objects.filter(user_id=user_id).values_list(
                    "exercise_id", flat=True
                )
            )
            context[user_id] = (owned, blocked)
        owned, blocked = context[user_id]

        fields = []
        anchor = anchor_by_pattern.get(result.pattern_id)
        if anchor is not None and result.tested_exercise_id != anchor.pk:
            result.tested_exercise = anchor
            fields.append("tested_exercise")
        if result.left_reps is not None and result.right_reps is not None:
            result.reps_or_seconds = result.left_reps + result.right_reps
            fields.append("reps_or_seconds")

        ladder = _ladder_for(apps, result.pattern_id, owned, blocked)
        if ladder:
            current = result.placed_exercise
            placed = _replace(ladder, result.reps_or_seconds, current)
            if current is None or placed.pk != current.pk:
                old = f"{current.name} (rank {current.difficulty_rank})" if current else "none"
                print(
                    f"  AssessmentResult {result.pk} [{result.pattern.key}]: "
                    f"{old} -> {placed.name} (rank {placed.difficulty_rank}), "
                    f"score {result.reps_or_seconds}"
                )
                result.placed_exercise = placed
                fields.append("placed_exercise")
                moved += 1
        if fields:
            result.save(update_fields=fields)
    print(f"  Trial results re-placed: {moved}")


def backwards(apps, schema_editor):
    AssessmentResult = apps.get_model("the_cauldron", "AssessmentResult")
    Exercise = apps.get_model("the_cauldron", "Exercise")
    for pattern_key, old_name in OLD_ANCHORS.items():
        old = Exercise.objects.filter(pattern__key=pattern_key, name=old_name).first()
        new_name = NEW_ANCHORS[pattern_key]
        if old is None or old_name == new_name:
            continue
        AssessmentResult.objects.filter(
            pattern__key=pattern_key, tested_exercise__name=new_name
        ).update(tested_exercise=old)
        Exercise.objects.filter(pattern__key=pattern_key).update(is_assessment_anchor=False)
        Exercise.objects.filter(pk=old.pk).update(is_assessment_anchor=True)


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0013_single_day_history_cleanup"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

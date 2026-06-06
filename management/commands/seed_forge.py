"""Seed the Forge catalog: movement patterns, equipment, and exercise ladders.

Idempotent — safe to re-run. Ladders are ordered easiest→hardest within a
pattern; ``placement_threshold`` is the min AMRAP reps (or seconds for holds) to
be placed on that rung during the Trial. Load-mode rungs represent the same
movement loaded with equipment and progress by load rather than by climbing.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from the_cauldron.models import Equipment, Exercise, MovementPattern

PATTERNS = [
    ("horizontal_push", "Horizontal Push", "Chest, front delts, triceps", False),
    ("vertical_pull", "Vertical Pull", "Lats, biceps, mid-back", False),
    ("vertical_push", "Vertical Push", "Shoulders, triceps", False),
    ("lower_unilateral", "Lower (Unilateral)", "Quads, glutes", True),
    ("core_anti_extension", "Core (Anti-Extension)", "Abs, deep core", False),
    ("hinge", "Hinge / Posterior Chain", "Hamstrings, glutes, low back", True),
]

EQUIPMENT = [
    ("bodyweight", "Bodyweight", False, "none"),
    ("pullup_bar", "Pull-up Bar", False, "none"),
    ("dumbbells", "Dumbbells", True, "kg"),
    ("barbell", "Barbell + Plates", True, "kg"),
    ("bands", "Resistance Bands", True, "band_level"),
    ("rowing_machine", "Rowing Machine", False, "none"),
    ("bench", "Bench", False, "none"),
    ("kettlebell", "Kettlebell", True, "kg"),
    ("rings", "Gymnastic Rings", False, "none"),
]

# Each ladder: (pattern_key, [ (name, rank, mode, rmin, rmax, timed, threshold,
# [equipment_keys], cues) ... ]). Rungs are linked regression/progression in
# order. "threshold" is reps unless timed (seconds).
LADDERS = {
    "horizontal_push": [
        ("Wall Push-up", 1, "difficulty", 8, 15, False, 0, ["bodyweight"], "Body in a line; full lockout."),
        ("Incline Push-up", 2, "difficulty", 8, 15, False, 10, ["bodyweight"], "Hands elevated; brace core."),
        ("Knee Push-up", 3, "difficulty", 6, 12, False, 12, ["bodyweight"], "Hips down; chest to floor."),
        ("Push-up", 4, "difficulty", 5, 12, False, 8, ["bodyweight"], "Elbows ~45°; full range."),
        ("Diamond Push-up", 5, "difficulty", 5, 10, False, 15, ["bodyweight"], "Hands together; tuck elbows."),
        ("Archer Push-up", 6, "difficulty", 4, 8, False, 8, ["bodyweight"], "Shift to one arm; control."),
        ("Dumbbell Bench Press", 4, "load", 6, 12, False, 0, ["dumbbells", "bench"], "Drive through chest; full range."),
        ("Barbell Bench Press", 5, "load", 5, 10, False, 0, ["barbell", "bench"], "Bar to chest; tight back."),
    ],
    "vertical_pull": [
        ("Band-Assisted Row", 1, "load", 8, 15, False, 0, ["bands"], "Squeeze shoulder blades."),
        ("Australian Row", 2, "difficulty", 8, 15, False, 6, ["bodyweight", "pullup_bar"], "Body straight; pull chest to bar."),
        ("Rowing Machine", 2, "difficulty", 10, 20, False, 0, ["rowing_machine"], "Legs-hips-arms sequence."),
        ("Negative Pull-up", 3, "difficulty", 3, 6, False, 8, ["pullup_bar"], "5s lower; control the descent."),
        ("Band-Assisted Pull-up", 4, "load", 5, 10, False, 0, ["pullup_bar", "bands"], "Full hang to chin over bar."),
        ("Pull-up", 5, "difficulty", 4, 10, False, 4, ["pullup_bar"], "Dead hang; chin over bar."),
        ("Archer Pull-up", 6, "difficulty", 3, 6, False, 10, ["pullup_bar"], "Pull to one side; other arm straight."),
        ("Dumbbell Row", 3, "load", 6, 12, False, 0, ["dumbbells"], "Flat back; row to hip."),
    ],
    "vertical_push": [
        ("Incline Pike Push-up", 1, "difficulty", 6, 12, False, 0, ["bodyweight"], "Hips high; head between hands."),
        ("Pike Push-up", 2, "difficulty", 5, 12, False, 8, ["bodyweight"], "Pike position; crown to floor."),
        ("Wall Handstand Hold", 3, "difficulty", 15, 45, True, 0, ["bodyweight"], "Hollow body; push tall."),
        ("Assisted Handstand Push-up", 4, "difficulty", 3, 8, False, 30, ["bodyweight"], "Partial range; control."),
        ("Dumbbell Shoulder Press", 3, "load", 6, 12, False, 0, ["dumbbells"], "Press overhead; ribs down."),
        ("Barbell Overhead Press", 4, "load", 5, 10, False, 0, ["barbell"], "Bar to overhead; glutes tight."),
    ],
    "lower_unilateral": [
        ("Assisted Split Squat", 1, "difficulty", 8, 15, False, 0, ["bodyweight"], "Hold support; knee tracks toe."),
        ("Split Squat", 2, "difficulty", 8, 15, False, 10, ["bodyweight"], "Tall torso; back knee down."),
        ("Bulgarian Split Squat", 3, "difficulty", 6, 12, False, 12, ["bodyweight", "bench"], "Rear foot elevated; sink straight."),
        ("Assisted Pistol Squat", 4, "difficulty", 4, 8, False, 12, ["bodyweight"], "Hold support; full depth."),
        ("Pistol Squat", 5, "difficulty", 3, 8, False, 6, ["bodyweight"], "One leg; controlled descent."),
        ("Goblet Squat", 2, "load", 6, 12, False, 0, ["dumbbells", "kettlebell"], "Weight at chest; sit between hips."),
        ("Dumbbell Bulgarian Split Squat", 3, "load", 6, 12, False, 0, ["dumbbells", "bench"], "Loaded; rear foot elevated."),
        ("Barbell Back Squat", 4, "load", 5, 10, False, 0, ["barbell"], "Bar on traps; hit depth."),
    ],
    "core_anti_extension": [
        ("Knee Plank", 1, "difficulty", 15, 45, True, 0, ["bodyweight"], "Straight line knees to head."),
        ("Plank", 2, "difficulty", 20, 60, True, 20, ["bodyweight"], "Glutes + abs tight; no sag."),
        ("Extended Plank", 3, "difficulty", 15, 45, True, 45, ["bodyweight"], "Hands forward of shoulders."),
        ("RKC Plank", 4, "difficulty", 10, 30, True, 40, ["bodyweight"], "Max tension; posterior tilt."),
        ("Hollow Body Hold", 4, "difficulty", 15, 45, True, 30, ["bodyweight"], "Low back pressed to floor."),
        ("Ab Wheel / Band Rollout", 5, "load", 6, 12, False, 0, ["bands"], "Brace hard; don't arch."),
    ],
    "hinge": [
        ("Glute Bridge", 1, "difficulty", 12, 20, False, 0, ["bodyweight"], "Drive hips; squeeze glutes."),
        ("Single-Leg Glute Bridge", 2, "difficulty", 8, 15, False, 15, ["bodyweight"], "One leg; level hips."),
        ("Assisted Nordic Curl", 3, "difficulty", 5, 10, False, 12, ["bodyweight"], "Control the lower; use hands."),
        ("Nordic Curl", 4, "difficulty", 3, 8, False, 8, ["bodyweight"], "Hamstrings lower the body slowly."),
        ("Dumbbell Romanian Deadlift", 2, "load", 8, 12, False, 0, ["dumbbells"], "Soft knees; hinge from hips."),
        ("Barbell Romanian Deadlift", 3, "load", 6, 10, False, 0, ["barbell"], "Bar close; flat back."),
        ("Kettlebell Swing", 2, "load", 12, 20, False, 0, ["kettlebell"], "Hip snap; not a squat."),
    ],
}


# Curated YouTube tutorial per exercise (real, search-sourced watch URLs).
VIDEOS = {
    "Wall Push-up": "https://www.youtube.com/watch?v=YB0egDzsu18",
    "Incline Push-up": "https://www.youtube.com/watch?v=0JUrOH--Kdk",
    "Knee Push-up": "https://www.youtube.com/watch?v=utzhPQuXWcA",
    "Push-up": "https://www.youtube.com/watch?v=IODxDxX7oi4",
    "Diamond Push-up": "https://www.youtube.com/watch?v=_6AvEX9-k8E",
    "Archer Push-up": "https://www.youtube.com/watch?v=MxVbNel13Ek",
    "Dumbbell Bench Press": "https://www.youtube.com/watch?v=pKZMNVbfUzQ",
    "Barbell Bench Press": "https://www.youtube.com/watch?v=rT7DgCr-3pg",
    "Band-Assisted Row": "https://www.youtube.com/watch?v=eOKwM5nHzj4",
    "Australian Row": "https://www.youtube.com/watch?v=dnpDUwqMX04",
    "Rowing Machine": "https://www.youtube.com/watch?v=4zWu1yuJ0_g",
    "Negative Pull-up": "https://www.youtube.com/watch?v=bn76WhQQMlI",
    "Band-Assisted Pull-up": "https://www.youtube.com/watch?v=Dx6DNiOklZI",
    "Pull-up": "https://www.youtube.com/watch?v=EOgd2jRu4OU",
    "Archer Pull-up": "https://www.youtube.com/watch?v=_LGLKUiQH5k",
    "Dumbbell Row": "https://www.youtube.com/watch?v=gfUg6qWohTk",
    "Incline Pike Push-up": "https://www.youtube.com/watch?v=HLjASz4wexo",
    "Pike Push-up": "https://www.youtube.com/watch?v=2b5t0Cu2nQI",
    "Wall Handstand Hold": "https://www.youtube.com/watch?v=2v1YDTzMcO8",
    "Assisted Handstand Push-up": "https://www.youtube.com/watch?v=6MdyYIRS7FY",
    "Dumbbell Shoulder Press": "https://www.youtube.com/watch?v=1jYq9QQEWqE",
    "Barbell Overhead Press": "https://www.youtube.com/watch?v=bMksDb5a3P0",
    "Assisted Split Squat": "https://www.youtube.com/watch?v=GpNxsiouXC4",
    "Split Squat": "https://www.youtube.com/watch?v=KynErtGwD2M",
    "Bulgarian Split Squat": "https://www.youtube.com/watch?v=hiLF_pF3EJM",
    "Assisted Pistol Squat": "https://www.youtube.com/watch?v=88YkATr_7ZA",
    "Pistol Squat": "https://www.youtube.com/watch?v=hHxm3VbuS-w",
    "Goblet Squat": "https://www.youtube.com/watch?v=6mf0oa2GGUc",
    "Dumbbell Bulgarian Split Squat": "https://www.youtube.com/watch?v=vLuhN_glFZ8",
    "Barbell Back Squat": "https://www.youtube.com/watch?v=irA7MTz96ho",
    "Knee Plank": "https://www.youtube.com/watch?v=iDSHokfXqyA",
    "Plank": "https://www.youtube.com/watch?v=mwlp75MS6Rg",
    "Extended Plank": "https://www.youtube.com/watch?v=Rp3KMx7Z8dA",
    "RKC Plank": "https://www.youtube.com/watch?v=feE0RCgWAUs",
    "Hollow Body Hold": "https://www.youtube.com/watch?v=HAfUt2Cco74",
    "Ab Wheel / Band Rollout": "https://www.youtube.com/watch?v=j6lR4u193gE",
    "Glute Bridge": "https://www.youtube.com/watch?v=nuapk_-Q2BI",
    "Single-Leg Glute Bridge": "https://www.youtube.com/watch?v=VUl8R0kn6v4",
    "Assisted Nordic Curl": "https://www.youtube.com/watch?v=mYfTCoOoX74",
    "Nordic Curl": "https://www.youtube.com/watch?v=_e9vFU9-tkc",
    "Dumbbell Romanian Deadlift": "https://www.youtube.com/watch?v=aa57T45iFSE",
    "Barbell Romanian Deadlift": "https://www.youtube.com/watch?v=xgusDooVfKU",
    "Kettlebell Swing": "https://www.youtube.com/watch?v=PO343opm22o",
}

# Curated test movement per pattern: a stable, always-eligible bodyweight move
# so the Trial gives a sensible example regardless of owned equipment.
ASSESSMENT_ANCHORS = {
    "Push-up", "Australian Row", "Pike Push-up", "Split Squat", "Plank", "Glute Bridge",
}


def rest_for(mode, rmin, rmax, timed):
    """Evidence-based rest after a working set, in seconds.

    Longer rest (~2-3 min) favours strength and heavy compounds (Schoenfeld
    2016; Grgic 2017 review): hard/low-rep and loaded lifts get 150s. Accessory/
    hypertrophy ranges get ~120s, endurance (>12 reps) ~75s, isometric holds
    ~60s (a hold isn't a high-fatigue compound set).
    """
    if timed:
        return 60
    if rmax <= 8 or (mode == "load" and rmax <= 10):
        return 150
    if rmax <= 12:
        return 120
    return 75


class Command(BaseCommand):
    help = "Seed the Forge catalog (patterns, equipment, exercise ladders)."

    @transaction.atomic
    def handle(self, *args, **options):
        patterns = {}
        for key, name, muscles, lower in PATTERNS:
            obj, _ = MovementPattern.objects.update_or_create(
                key=key,
                defaults={"name": name, "primary_muscles": muscles, "is_lower_body": lower},
            )
            patterns[key] = obj

        equipment = {}
        for key, name, loadable, unit in EQUIPMENT:
            obj, _ = Equipment.objects.update_or_create(
                key=key,
                defaults={"name": name, "is_loadable": loadable, "load_unit": unit},
            )
            equipment[key] = obj

        n_ex = 0
        for pkey, rungs in LADDERS.items():
            pattern = patterns[pkey]
            # Build/refresh each rung.
            created = []
            for (name, rank, mode, rmin, rmax, timed, threshold, equips, cues) in rungs:
                ex, _ = Exercise.objects.update_or_create(
                    pattern=pattern,
                    name=name,
                    defaults={
                        "difficulty_rank": rank,
                        "progression_mode": mode,
                        "rep_range_min": rmin,
                        "rep_range_max": rmax,
                        "is_timed": timed,
                        "placement_threshold": threshold,
                        "cues": cues,
                        "video_url": VIDEOS.get(name, ""),
                        "rest_seconds": rest_for(mode, rmin, rmax, timed),
                        "is_assessment_anchor": name in ASSESSMENT_ANCHORS,
                    },
                )
                ex.required_equipment.set([equipment[e] for e in equips])
                created.append((mode, ex))
                n_ex += 1

            # Link regression/progression within each mode's ladder, ordered by rank.
            for mode in ("difficulty", "load"):
                chain = sorted(
                    [ex for m, ex in created if m == mode], key=lambda e: e.difficulty_rank
                )
                for i, ex in enumerate(chain):
                    ex.regression = chain[i - 1] if i > 0 else None
                    ex.progression = chain[i + 1] if i < len(chain) - 1 else None
                    ex.save(update_fields=["regression", "progression"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(patterns)} patterns, {len(equipment)} equipment, {n_ex} exercises."
            )
        )

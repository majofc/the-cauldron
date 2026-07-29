"""Seed the Forge catalog: movement patterns, equipment, and exercise ladders.

Idempotent — safe to re-run. Ladders are ordered easiest→hardest within a
pattern; ``placement_threshold`` is the min AMRAP reps (or seconds for holds) to
be placed on that rung during the Trial. Load-mode rungs represent the same
movement loaded with equipment and progress by load rather than by climbing.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from the_cauldron.models import Equipment, Exercise, MovementPattern, Muscle

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
        ("Typewriter Push-up", 7, "difficulty", 3, 6, False, 18, ["bodyweight"], "Stay low; glide side to side, elbows tight."),
        ("One-Arm Push-up", 8, "difficulty", 1, 5, False, 24, ["bodyweight"], "Widen the base; brace hard, no torso twist."),
        ("Dumbbell Bench Press", 4, "load", 6, 12, False, 0, ["dumbbells", "bench"], "Drive through chest; full range."),
        ("Barbell Bench Press", 5, "load", 5, 10, False, 0, ["barbell", "bench"], "Bar to chest; tight back."),
    ],
    "vertical_pull": [
        ("Band-Assisted Row", 1, "load", 8, 15, False, 0, ["bands"], "Squeeze shoulder blades."),
        ("Australian Row", 2, "difficulty", 8, 15, False, 6, ["bodyweight", "pullup_bar"], "Body straight; pull chest to bar."),
        ("Rowing Machine", 2, "difficulty", 10, 20, False, 0, ["rowing_machine"], "Legs-hips-arms sequence."),
        ("Negative Pull-up", 3, "difficulty", 3, 6, False, 8, ["pullup_bar"], "5s lower; control the descent."),
        ("Band-Assisted Pull-up", 4, "load", 5, 10, False, 0, ["pullup_bar", "bands"], "Full hang to chin over bar."),
        # Bar pull-up rungs split into two grips at the same rank; the Forge
        # prescribes the weaker grip daily. Overhand listed first = ladder-node
        # representative that adjacent rungs link to.
        ("Pull-up", 5, "difficulty", 4, 10, False, 4, ["pullup_bar"], "Dead hang; chin over bar.", "overhand"),
        ("Chin-up", 5, "difficulty", 4, 10, False, 4, ["pullup_bar"], "Underhand grip; pull chin over bar.", "underhand"),
        ("Archer Pull-up", 6, "difficulty", 3, 6, False, 10, ["pullup_bar"], "Pull to one side; other arm straight.", "overhand"),
        ("Archer Chin-up", 6, "difficulty", 3, 6, False, 10, ["pullup_bar"], "Underhand archer; pull to one side.", "underhand"),
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
        ("Shrimp Squat", 6, "difficulty", 3, 8, False, 16, ["bodyweight"], "Grab rear foot; sit straight down, chest tall."),
        ("Dragon Squat", 7, "difficulty", 1, 5, False, 22, ["bodyweight"], "Thread rear leg through; control the descent."),
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


# Muscle catalog: (key, display name, body-diagram region).
MUSCLES = [
    ("chest", "Chest", "front"),
    ("front_delts", "Front Deltoids", "front"),
    ("side_delts", "Side Deltoids", "front"),
    ("rear_delts", "Rear Deltoids", "back"),
    ("biceps", "Biceps", "front"),
    ("triceps", "Triceps", "back"),
    ("forearms", "Forearms", "front"),
    ("abs", "Abs", "front"),
    ("obliques", "Obliques", "front"),
    ("quads", "Quads", "front"),
    ("lats", "Lats", "back"),
    ("traps", "Trapezius", "back"),
    ("mid_back", "Mid-Back", "back"),
    ("lower_back", "Lower Back", "back"),
    ("glutes", "Glutes", "back"),
    ("hamstrings", "Hamstrings", "back"),
    ("calves", "Calves", "back"),
]

# Muscles trained by each exercise (by name). Primary movers first; the diagram
# treats all listed muscles equally. Keys must exist in MUSCLES above.
EXERCISE_MUSCLES = {
    # ── Horizontal Push ──
    "Wall Push-up": ["chest", "front_delts", "triceps"],
    "Incline Push-up": ["chest", "front_delts", "triceps"],
    "Knee Push-up": ["chest", "front_delts", "triceps"],
    "Push-up": ["chest", "front_delts", "triceps", "abs"],
    "Diamond Push-up": ["triceps", "chest", "front_delts"],
    "Archer Push-up": ["chest", "front_delts", "triceps", "abs"],
    "Typewriter Push-up": ["chest", "front_delts", "triceps"],
    "One-Arm Push-up": ["chest", "triceps", "front_delts", "obliques"],
    "Dumbbell Bench Press": ["chest", "front_delts", "triceps"],
    "Barbell Bench Press": ["chest", "front_delts", "triceps"],
    # ── Vertical Pull ──
    "Band-Assisted Row": ["lats", "mid_back", "biceps", "rear_delts"],
    "Australian Row": ["lats", "mid_back", "biceps", "rear_delts"],
    "Rowing Machine": ["lats", "mid_back", "biceps", "quads", "hamstrings"],
    "Negative Pull-up": ["lats", "biceps", "mid_back", "forearms"],
    "Band-Assisted Pull-up": ["lats", "biceps", "mid_back"],
    "Pull-up": ["lats", "biceps", "mid_back", "forearms"],
    "Chin-up": ["lats", "biceps", "mid_back", "forearms"],
    "Archer Pull-up": ["lats", "biceps", "mid_back", "forearms"],
    "Archer Chin-up": ["lats", "biceps", "mid_back", "forearms"],
    "Dumbbell Row": ["lats", "mid_back", "biceps", "rear_delts"],
    # ── Vertical Push ──
    "Incline Pike Push-up": ["front_delts", "side_delts", "triceps"],
    "Pike Push-up": ["front_delts", "side_delts", "triceps"],
    "Wall Handstand Hold": ["front_delts", "side_delts", "triceps", "traps"],
    "Assisted Handstand Push-up": ["front_delts", "side_delts", "triceps", "traps"],
    "Dumbbell Shoulder Press": ["front_delts", "side_delts", "triceps"],
    "Barbell Overhead Press": ["front_delts", "side_delts", "triceps", "traps"],
    # ── Lower (Unilateral) ──
    "Assisted Split Squat": ["quads", "glutes"],
    "Split Squat": ["quads", "glutes"],
    "Bulgarian Split Squat": ["quads", "glutes", "hamstrings"],
    "Assisted Pistol Squat": ["quads", "glutes"],
    "Pistol Squat": ["quads", "glutes", "hamstrings"],
    "Shrimp Squat": ["quads", "glutes"],
    "Dragon Squat": ["quads", "glutes", "hamstrings"],
    "Goblet Squat": ["quads", "glutes"],
    "Dumbbell Bulgarian Split Squat": ["quads", "glutes", "hamstrings"],
    "Barbell Back Squat": ["quads", "glutes", "hamstrings", "lower_back"],
    # ── Core (Anti-Extension) ──
    "Knee Plank": ["abs", "obliques"],
    "Plank": ["abs", "obliques"],
    "Extended Plank": ["abs", "obliques"],
    "RKC Plank": ["abs", "obliques"],
    "Hollow Body Hold": ["abs", "obliques", "quads"],
    "Ab Wheel / Band Rollout": ["abs", "obliques", "lats"],
    # ── Hinge / Posterior Chain ──
    "Glute Bridge": ["glutes", "hamstrings"],
    "Single-Leg Glute Bridge": ["glutes", "hamstrings"],
    "Assisted Nordic Curl": ["hamstrings", "glutes"],
    "Nordic Curl": ["hamstrings", "glutes", "calves"],
    "Dumbbell Romanian Deadlift": ["hamstrings", "glutes", "lower_back"],
    "Barbell Romanian Deadlift": ["hamstrings", "glutes", "lower_back"],
    "Kettlebell Swing": ["glutes", "hamstrings", "lower_back", "front_delts"],
}

# Curated YouTube tutorial per exercise (real, search-sourced watch URLs).
VIDEOS = {
    "Wall Push-up": "https://www.youtube.com/watch?v=YB0egDzsu18",
    "Incline Push-up": "https://www.youtube.com/watch?v=0JUrOH--Kdk",
    "Knee Push-up": "https://www.youtube.com/watch?v=utzhPQuXWcA",
    "Push-up": "https://www.youtube.com/watch?v=IODxDxX7oi4",
    "Diamond Push-up": "https://www.youtube.com/watch?v=_6AvEX9-k8E",
    "Archer Push-up": "https://www.youtube.com/watch?v=MxVbNel13Ek",
    "Typewriter Push-up": "https://www.youtube.com/watch?v=fw56EiZYvm8",
    "One-Arm Push-up": "https://www.youtube.com/watch?v=7rqykkPtg2M",
    "Dumbbell Bench Press": "https://www.youtube.com/watch?v=pKZMNVbfUzQ",
    "Barbell Bench Press": "https://www.youtube.com/watch?v=rT7DgCr-3pg",
    "Band-Assisted Row": "https://www.youtube.com/watch?v=eOKwM5nHzj4",
    "Australian Row": "https://www.youtube.com/watch?v=dnpDUwqMX04",
    "Rowing Machine": "https://www.youtube.com/watch?v=4zWu1yuJ0_g",
    "Negative Pull-up": "https://www.youtube.com/watch?v=bn76WhQQMlI",
    "Band-Assisted Pull-up": "https://www.youtube.com/watch?v=Dx6DNiOklZI",
    "Pull-up": "https://www.youtube.com/watch?v=EOgd2jRu4OU",
    "Archer Pull-up": "https://www.youtube.com/watch?v=_LGLKUiQH5k",
    # Chin-up / Archer Chin-up demos intentionally blank until a verified clip is
    # sourced — a wrong (overhand) demo would mis-coach the grip. TODO: add real URLs.
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
    "Shrimp Squat": "https://www.youtube.com/watch?v=xfl7SDj0Gzs",
    "Dragon Squat": "https://www.youtube.com/watch?v=Pic8epzj1N4",
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

# Movements performed one side at a time. Their rep targets are forced even (so
# both sides get equal work) and their to-failure set is logged Left/Right.
# Note the lower ladder is NOT wholly per-side: Goblet Squat and Barbell Back
# Squat sit on the unilateral pattern but are two-legged lifts.
# Keep in sync with migration 0009's PER_SIDE list.
PER_SIDE = {
    # Lower (unilateral)
    "Assisted Split Squat", "Split Squat", "Bulgarian Split Squat",
    "Dumbbell Bulgarian Split Squat", "Assisted Pistol Squat", "Pistol Squat",
    "Shrimp Squat", "Dragon Squat",
    # Horizontal push
    "Archer Push-up", "Typewriter Push-up", "One-Arm Push-up",
    # Vertical pull
    "Archer Pull-up", "Archer Chin-up",
    # Hinge
    "Single-Leg Glute Bridge",
}


def rest_for(mode, rmin, rmax, timed):
    """Evidence-based rest after a working set, in seconds.

    Longer rest (~2-3 min) favours strength and heavy compounds (Schoenfeld
    2016; Grgic 2017 review): hard/low-rep and loaded lifts get the most rest,
    accessory/hypertrophy ranges less, endurance (>12 reps) less still, and
    isometric holds the least (a hold isn't a high-fatigue compound set).

    Baselines are cut ~30% from the original prescriptions (150/120/75/60s) to
    keep sessions denser, rounded to the nearest 5s for clean timer values:
    heavy 150→105, hypertrophy 120→85, endurance 75→55, holds 60→40. Existing
    catalog and prescription rows are migrated to match in 0004.
    """
    if timed:
        return 40
    if rmax <= 8 or (mode == "load" and rmax <= 10):
        return 105
    if rmax <= 12:
        return 85
    return 55


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

        muscles = {}
        for key, name, region in MUSCLES:
            obj, _ = Muscle.objects.update_or_create(
                key=key, defaults={"name": name, "region": region}
            )
            muscles[key] = obj

        n_ex = 0
        for pkey, rungs in LADDERS.items():
            pattern = patterns[pkey]
            # Build/refresh each rung.
            created = []
            for rung in rungs:
                name, rank, mode, rmin, rmax, timed, threshold, equips, cues = rung[:9]
                # Optional trailing grip element; defaults to n/a for normal rungs.
                grip = rung[9] if len(rung) > 9 else Exercise.Grip.NA
                ex, _ = Exercise.objects.update_or_create(
                    pattern=pattern,
                    name=name,
                    defaults={
                        "difficulty_rank": rank,
                        "progression_mode": mode,
                        "rep_range_min": rmin,
                        "rep_range_max": rmax,
                        "is_timed": timed,
                        "is_per_side": name in PER_SIDE,
                        "placement_threshold": threshold,
                        "cues": cues,
                        "grip": grip,
                        "video_url": VIDEOS.get(name, ""),
                        "rest_seconds": rest_for(mode, rmin, rmax, timed),
                        "is_assessment_anchor": name in ASSESSMENT_ANCHORS,
                    },
                )
                ex.required_equipment.set([equipment[e] for e in equips])
                ex.muscles.set(
                    [muscles[m] for m in EXERCISE_MUSCLES.get(name, [])]
                )
                created.append((mode, ex))
                n_ex += 1

            # Link regression/progression within each mode's ladder, ordered by rank.
            # Grip variants sharing a rank (bar pull-ups) collapse into ONE ladder
            # node so traversal stays linear: both variants get the same adjacent
            # rungs, and adjacent rungs link to the node's first (overhand) variant.
            for mode in ("difficulty", "load"):
                chain = sorted(
                    [ex for m, ex in created if m == mode], key=lambda e: e.difficulty_rank
                )
                nodes = []
                grip_node_by_rank = {}
                for ex in chain:
                    if ex.grip != Exercise.Grip.NA:
                        # All grip variants of a rank share one node, regardless of
                        # their order in the sorted chain.
                        node = grip_node_by_rank.get(ex.difficulty_rank)
                        if node is None:
                            node = grip_node_by_rank[ex.difficulty_rank] = []
                            nodes.append(node)
                        node.append(ex)
                    else:
                        nodes.append([ex])
                # Overhand first in each grip node → the rung neighbours link to.
                for node in nodes:
                    node.sort(key=lambda e: e.grip != Exercise.Grip.OVERHAND)
                for i, node in enumerate(nodes):
                    regression = nodes[i - 1][0] if i > 0 else None
                    progression = nodes[i + 1][0] if i < len(nodes) - 1 else None
                    for ex in node:
                        ex.regression = regression
                        ex.progression = progression
                        ex.save(update_fields=["regression", "progression"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(patterns)} patterns, {len(equipment)} equipment, "
                f"{len(muscles)} muscles, {n_ex} exercises."
            )
        )

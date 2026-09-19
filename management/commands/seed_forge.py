"""Seed the Forge catalog: movement patterns, equipment, and exercise ladders.

Idempotent — safe to re-run. Ladders are ordered easiest→hardest within a
pattern; ``placement_threshold`` is the min AMRAP reps (or seconds for holds) to
be placed on that rung during the Trial. Load-mode rungs represent the same
movement loaded with equipment and progress by load rather than by climbing.

The Trial only ever scores a pattern's assessment anchor, so every threshold in
a pattern is on that anchor's scale and must be non-decreasing by rank —
``place_from_assessment`` stops at the first rung the score misses, so a dip
would make the rungs above it unreachable. Rungs sharing a rank share a
threshold; which of them a fully-equipped user lands on still follows row order
(only lower has unique ranks).
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
    ("grip", "Grip / Forearms", "Forearms, hands", False),
]

EQUIPMENT = [
    ("bodyweight", "Bodyweight", False, "none"),
    ("pullup_bar", "Pull-up Bar", False, "none"),
    ("dumbbells", "Dumbbells", True, "kg"),
    ("barbell", "Barbell + Plates", True, "kg"),
    ("bands", "Resistance Bands", True, "band_level"),
    ("bench", "Bench", False, "none"),
    ("kettlebell", "Kettlebell", True, "kg"),
    ("rings", "Gymnastic Rings", False, "none"),
    ("ab_wheel", "Ab Wheel", False, "none"),
    ("fat_grips", "Fat Grips", False, "none"),
]

# Each ladder: (pattern_key, [ (name, rank, mode, rmin, rmax, timed, threshold,
# [equipment_keys], cues) ... ]). Rungs are linked regression/progression in
# order. "threshold" is reps unless timed (seconds).
LADDERS = {
    # Thresholds: Push-up reps.
    "horizontal_push": [
        ("Wall Push-up", 1, "difficulty", 8, 15, False, 0, ["bodyweight"], "Body in a line; full lockout."),
        ("Incline Push-up", 2, "difficulty", 8, 15, False, 2, ["bodyweight"], "Hands elevated; brace core."),
        ("Knee Push-up", 3, "difficulty", 6, 12, False, 5, ["bodyweight"], "Hips down; chest to floor."),
        ("Push-up", 4, "difficulty", 5, 12, False, 10, ["bodyweight"], "Elbows ~45°; full range."),
        ("Incline Archer Push-up", 5, "difficulty", 4, 10, False, 20, ["bodyweight"], "Hands elevated; shift onto one arm, other arm straight."),
        ("Diamond Push-up", 6, "difficulty", 5, 10, False, 25, ["bodyweight"], "Hands together; tuck elbows."),
        ("Archer Push-up", 7, "difficulty", 4, 8, False, 30, ["bodyweight"], "Shift to one arm; control."),
        ("Typewriter Push-up", 8, "difficulty", 3, 6, False, 38, ["bodyweight"], "Stay low; glide side to side, elbows tight."),
        # Threshold 41, not the 21 the ticket quoted: thresholds must be
        # non-decreasing by rank (rank 8 is 38, rank 10 is 45) or the rungs above
        # the dip become unreachable — ``place_from_assessment`` stops at the
        # first rung the score misses.
        ("Elevated One-Arm Push-up", 9, "difficulty", 3, 6, False, 41, ["bodyweight"],
         "One hand on a box or bench, other hand behind the back. Feet wide, hips "
         "square. Lower the surface as it gets easy."),
        ("One-Arm Push-up", 10, "difficulty", 1, 5, False, 45, ["bodyweight"], "Widen the base; brace hard, no torso twist."),
        ("Dumbbell Bench Press", 4, "load", 6, 12, False, 10, ["dumbbells", "bench"], "Drive through chest; full range."),
        ("Barbell Bench Press", 5, "load", 5, 10, False, 20, ["barbell", "bench"], "Bar to chest; tight back."),
    ],
    # Thresholds: Australian Row reps.
    "vertical_pull": [
        ("Band-Assisted Row", 1, "load", 8, 15, False, 0, ["bands"], "Squeeze shoulder blades."),
        ("Australian Row", 2, "difficulty", 8, 15, False, 3, ["bodyweight", "pullup_bar"], "Body straight; pull chest to bar."),
        ("Single-Arm Australian Row", 3, "difficulty", 4, 10, False, 12, ["bodyweight", "pullup_bar"], "One hand on the bar; body straight, pull chest to that hand."),
        ("Negative Pull-up", 4, "difficulty", 3, 6, False, 20, ["pullup_bar"], "5s lower; control the descent."),
        ("Band-Assisted Pull-up", 4, "load", 5, 10, False, 20, ["pullup_bar", "bands"], "Full hang to chin over bar."),
        # Bar pull-up rungs split into two grips at the same rank; the Forge
        # prescribes the weaker grip daily. Overhand listed first = ladder-node
        # representative that adjacent rungs link to.
        ("Pull-up", 5, "difficulty", 4, 10, False, 28, ["pullup_bar"], "Dead hang; chin over bar.", "overhand", "bar_pullup"),
        ("Chin-up", 5, "difficulty", 4, 10, False, 28, ["pullup_bar"], "Underhand grip; pull chin over bar.", "underhand", "bar_pullup"),
        ("Archer Pull-up", 6, "difficulty", 3, 6, False, 40, ["pullup_bar"], "Pull to one side; other arm straight.", "overhand", "bar_archer_pullup"),
        ("Archer Chin-up", 6, "difficulty", 3, 6, False, 40, ["pullup_bar"], "Underhand archer; pull to one side.", "underhand", "bar_archer_pullup"),
        ("Dumbbell Row", 3, "load", 6, 12, False, 12, ["dumbbells"], "Flat back; row to hip."),
    ],
    # Thresholds: Pike Push-up reps.
    "vertical_push": [
        ("Incline Pike Push-up", 1, "difficulty", 6, 12, False, 0, ["bodyweight"], "Hips high; head between hands."),
        ("Pike Push-up", 2, "difficulty", 5, 12, False, 4, ["bodyweight"], "Pike position; crown to floor."),
        ("Wall Handstand Hold", 3, "difficulty", 15, 45, True, 8, ["bodyweight"], "Hollow body; push tall."),
        ("Assisted Handstand Push-up", 4, "difficulty", 3, 8, False, 12, ["bodyweight"], "Partial range; control."),
        ("Dumbbell Shoulder Press", 3, "load", 6, 12, False, 8, ["dumbbells"], "Press overhead; ribs down."),
        ("Barbell Overhead Press", 4, "load", 5, 10, False, 12, ["barbell"], "Bar to overhead; glutes tight."),
    ],
    # ONE ladder over every movement in the pattern (see SINGLE_LADDER_PATTERNS):
    # ranks are unique, and progression_mode only decides how a user advances
    # once on a rung. Loaded rungs sit at the difficulty of a working load, not
    # an empty bar. Thresholds: bodyweight Squat reps.
    "lower_unilateral": [
        ("Squat", 1, "difficulty", 10, 20, False, 0, ["bodyweight"], "Feet shoulder-width; sit hips back and down, chest tall."),
        ("Assisted Split Squat", 2, "difficulty", 8, 15, False, 10, ["bodyweight"], "Hold support; knee tracks toe."),
        ("Goblet Squat", 3, "load", 6, 12, False, 20, ["dumbbells", "kettlebell"], "Weight at chest; sit between hips."),
        ("Split Squat", 4, "difficulty", 8, 15, False, 30, ["bodyweight"], "Tall torso; back knee down."),
        ("Bulgarian Split Squat", 5, "difficulty", 6, 12, False, 40, ["bodyweight", "bench"], "Rear foot elevated; sink straight."),
        ("Barbell Back Squat", 6, "load", 5, 10, False, 50, ["barbell"], "Bar on traps; hit depth."),
        ("Dumbbell Bulgarian Split Squat", 7, "load", 6, 12, False, 60, ["dumbbells", "bench"], "Loaded; rear foot elevated."),
        ("Assisted Pistol Squat", 8, "difficulty", 4, 8, False, 75, ["bodyweight"], "Hold support; full depth."),
        ("Pistol Squat", 9, "difficulty", 3, 8, False, 95, ["bodyweight"], "One leg; controlled descent."),
        ("Shrimp Squat", 10, "difficulty", 3, 8, False, 120, ["bodyweight"], "Grab rear foot; sit straight down, chest tall."),
        ("Dragon Squat", 11, "difficulty", 1, 5, False, 150, ["bodyweight"], "Thread rear leg through; control the descent."),
    ],
    # Thresholds: Plank seconds.
    "core_anti_extension": [
        ("Knee Plank", 1, "difficulty", 15, 45, True, 0, ["bodyweight"], "Straight line knees to head."),
        ("Plank", 2, "difficulty", 20, 60, True, 20, ["bodyweight"], "Glutes + abs tight; no sag."),
        ("Extended Plank", 3, "difficulty", 15, 45, True, 45, ["bodyweight"], "Hands forward of shoulders."),
        ("RKC Plank", 4, "difficulty", 10, 30, True, 60, ["bodyweight"], "Max tension; posterior tilt."),
        ("Hollow Body Hold", 4, "difficulty", 15, 45, True, 60, ["bodyweight"], "Low back pressed to floor."),
        ("Band Rollout", 5, "load", 6, 12, False, 75, ["bands"], "Brace hard; don't arch."),
        ("Knee Ab Wheel Rollout", 5, "difficulty", 6, 12, False, 75, ["ab_wheel"], "Knees down; brace hard, roll out without arching."),
        ("Standing Ab Wheel Rollout", 6, "difficulty", 3, 8, False, 110, ["ab_wheel"], "From standing; hollow body, roll out and back without arching."),
    ],
    # Thresholds: Glute Bridge reps.
    "hinge": [
        ("Glute Bridge", 1, "difficulty", 12, 20, False, 0, ["bodyweight"], "Drive hips; squeeze glutes."),
        ("Single-Leg Glute Bridge", 2, "difficulty", 8, 15, False, 15, ["bodyweight"], "One leg; level hips."),
        ("Assisted Nordic Curl", 3, "difficulty", 5, 10, False, 35, ["bodyweight"], "Control the lower; use hands."),
        ("Nordic Curl", 4, "difficulty", 3, 8, False, 60, ["bodyweight"], "Hamstrings lower the body slowly."),
        ("Dumbbell Romanian Deadlift", 2, "load", 8, 12, False, 15, ["dumbbells"], "Soft knees; hinge from hips."),
        ("Barbell Romanian Deadlift", 3, "load", 6, 10, False, 35, ["barbell"], "Bar close; flat back."),
        ("Kettlebell Swing", 2, "load", 12, 20, False, 15, ["kettlebell"], "Hip snap; not a squat."),
    ],

    # Thresholds: Dead Hang seconds. Ranks 1-2 both sit at 0 (non-decreasing, so
    # the monotonicity rule holds): which of them a user lands on falls out of
    # equipment filtering, not a special case — someone with nowhere to hang gets
    # the towel wring, a bar/rings owner the assisted hang.
    # A rung reading "bar OR rings" carries them as ALTERNATIVE equipment (any-of)
    # rather than two rows, so a rings-only user climbs one uninterrupted ladder.
    "grip": [
        ("Towel Wring Hold", 1, "difficulty", 15, 40, True, 0, ["bodyweight"],
         "Twist a rolled towel as hard as you can, both directions. Squeeze, don't yank."),
        ("Assisted Bar Hang", 2, "difficulty", 20, 45, True, 0, ["bodyweight", ("pullup_bar", "rings")],
         "Feet on the floor or a box taking part of your weight; shoulders packed down."),
        ("Dead Hang", 3, "difficulty", 20, 60, True, 15, ["bodyweight", ("pullup_bar", "rings")],
         "Full hang, both hands, shoulders active. The Trial's grip test."),
        ("Towel Bar Hang", 4, "difficulty", 15, 45, True, 45, ["bodyweight", "pullup_bar"],
         "Hang from a towel over the bar, one end per hand. Thick and slippery on purpose."),
        ("Fat-Grip Hang", 5, "difficulty", 15, 40, True, 60, ["bodyweight", "pullup_bar", "fat_grips"],
         "Fat grips on the bar; crush them. Stop the set when the hands open, not the shoulders."),
        ("Plate Pinch Hold", 6, "difficulty", 10, 24, True, 70, ["bodyweight", "barbell"],
         "Pinch two plates smooth-side-out per hand, arms at your sides. Start at 5-10 kg per hand."),
        ("Assisted One-Arm Hang", 7, "difficulty", 10, 30, True, 80, ["bodyweight", ("pullup_bar", "rings")],
         "One hand hangs, the other holds the wrist or a band. Per side."),
        ("One-Arm Dead Hang", 8, "difficulty", 8, 24, True, 100, ["bodyweight", ("pullup_bar", "rings")],
         "Full hang on one hand, shoulder packed, no swinging. Per side."),
        ("One-Arm Towel Hang", 9, "difficulty", 6, 20, True, 130, ["bodyweight", "pullup_bar"],
         "One hand on a towel over the bar. Per side."),
        # Farmer's-carry family. Every rung is time under load — there is no
        # distance field — so the cues state the equivalence (~0.8-1.0 m/s
        # loaded: 20 m is about 20-25 s, 40 m about 40-50 s). Prescribed load is
        # what goes in ONE hand: 24 kg means 48 kg carried.
        #
        # Thresholds are the DIFFICULTY rung's value at the same rank (0/0/15/45),
        # not the 15/30/45/60 the ticket sketched: placement walks both chains of
        # a pattern together (``eligible_exercises`` returns every mode), so two
        # rungs sharing a rank must share a threshold or the walk stops early and
        # the rungs above become unreachable. Carries gate on LOAD anyway — the
        # weight in the hand, not the Trial score.
        ("Farmer Hold", 1, "load", 20, 45, True, 0, ["dumbbells"],
         "A dumbbell in each hand, stand tall and hold. Load shown is per hand."),
        ("Farmer Walk", 2, "load", 30, 60, True, 0, ["dumbbells"],
         "Walk with a dumbbell in each hand (~20 m per 20-25 s). Load shown is per hand."),
        ("Suitcase Carry", 3, "load", 20, 40, True, 15, ["kettlebell"],
         "One kettlebell, one side, ribs down and no lean (~20 m per 20-25 s). Per side."),
        ("Double-Overhand Barbell Hold", 4, "load", 15, 30, True, 45, ["barbell"],
         "Hold a loaded bar at the hips, thumbs over, no straps or hook grip."),
    ],
}

# Patterns whose rungs form ONE ranked ladder across both progression modes,
# linked into a single regression/progression chain. Every other pattern keeps
# a separate chain per mode (parallel bodyweight and loaded ladders).
SINGLE_LADDER_PATTERNS = {"lower_unilateral"}


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
    "Incline Archer Push-up": ["chest", "front_delts", "triceps", "obliques"],
    "Diamond Push-up": ["triceps", "chest", "front_delts"],
    "Archer Push-up": ["chest", "front_delts", "triceps", "abs"],
    "Typewriter Push-up": ["chest", "front_delts", "triceps"],
    "Elevated One-Arm Push-up": ["chest", "triceps", "front_delts", "obliques"],
    "One-Arm Push-up": ["chest", "triceps", "front_delts", "obliques"],
    "Dumbbell Bench Press": ["chest", "front_delts", "triceps"],
    "Barbell Bench Press": ["chest", "front_delts", "triceps"],
    # ── Vertical Pull ──
    "Band-Assisted Row": ["lats", "mid_back", "biceps", "rear_delts"],
    "Australian Row": ["lats", "mid_back", "biceps", "rear_delts"],
    "Single-Arm Australian Row": ["lats", "mid_back", "biceps", "rear_delts", "obliques"],
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
    "Squat": ["quads", "glutes"],
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
    "Band Rollout": ["abs", "obliques", "lats"],
    "Knee Ab Wheel Rollout": ["abs", "obliques", "lats"],
    "Standing Ab Wheel Rollout": ["abs", "obliques", "lats"],
    # ── Hinge / Posterior Chain ──
    "Glute Bridge": ["glutes", "hamstrings"],
    "Single-Leg Glute Bridge": ["glutes", "hamstrings"],
    "Assisted Nordic Curl": ["hamstrings", "glutes"],
    "Nordic Curl": ["hamstrings", "glutes", "calves"],
    "Dumbbell Romanian Deadlift": ["hamstrings", "glutes", "lower_back"],
    "Barbell Romanian Deadlift": ["hamstrings", "glutes", "lower_back"],
    "Kettlebell Swing": ["glutes", "hamstrings", "lower_back", "front_delts"],
    # ── Grip / Forearms ──
    "Towel Wring Hold": ["forearms"],
    "Assisted Bar Hang": ["forearms", "lats", "traps"],
    "Dead Hang": ["forearms", "lats", "traps"],
    "Towel Bar Hang": ["forearms", "lats", "traps"],
    "Fat-Grip Hang": ["forearms", "lats", "traps"],
    "Plate Pinch Hold": ["forearms"],
    "Assisted One-Arm Hang": ["forearms", "lats", "traps", "obliques"],
    "One-Arm Dead Hang": ["forearms", "lats", "traps", "obliques"],
    "One-Arm Towel Hang": ["forearms", "lats", "traps", "obliques"],
    "Farmer Hold": ["forearms", "traps"],
    "Farmer Walk": ["forearms", "traps", "abs"],
    "Suitcase Carry": ["forearms", "traps", "obliques"],
    "Double-Overhand Barbell Hold": ["forearms", "traps"],
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
    "Negative Pull-up": "https://www.youtube.com/watch?v=bn76WhQQMlI",
    "Band-Assisted Pull-up": "https://www.youtube.com/watch?v=Dx6DNiOklZI",
    "Pull-up": "https://www.youtube.com/watch?v=EOgd2jRu4OU",
    "Archer Pull-up": "https://www.youtube.com/watch?v=_LGLKUiQH5k",
    # Chin-up / Archer Chin-up demos intentionally blank until a verified clip is
    # sourced — a wrong (overhand) demo would mis-coach the grip. TODO: add real URLs.
    # Same for Incline Archer Push-up / Single-Arm Australian Row: a demo of the
    # wrong variant would mis-coach the single-arm setup. Left blank (the UI
    # simply hides the link). TODO: add real URLs.
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
    "Band Rollout": "https://www.youtube.com/watch?v=j6lR4u193gE",
    # Knee / Standing Ab Wheel Rollout demos blank until a verified clip is
    # sourced (the UI hides the link). TODO: add real URLs.
    "Glute Bridge": "https://www.youtube.com/watch?v=nuapk_-Q2BI",
    "Single-Leg Glute Bridge": "https://www.youtube.com/watch?v=VUl8R0kn6v4",
    "Assisted Nordic Curl": "https://www.youtube.com/watch?v=mYfTCoOoX74",
    "Nordic Curl": "https://www.youtube.com/watch?v=_e9vFU9-tkc",
    "Dumbbell Romanian Deadlift": "https://www.youtube.com/watch?v=aa57T45iFSE",
    "Barbell Romanian Deadlift": "https://www.youtube.com/watch?v=xgusDooVfKU",
    "Kettlebell Swing": "https://www.youtube.com/watch?v=PO343opm22o",
}

# Curated test movement per pattern: a stable, bilateral bodyweight move so the
# Trial takes one value per pattern regardless of owned equipment. Each one
# matches a key in ``services.norms.EXERCISE_NORMS`` so every Trial row gets a
# peer score. Keep in sync with migration 0014's NEW_ANCHORS.
# Grip carries TWO anchors: Dead Hang is the calibrated test, Towel Wring Hold
# the no-equipment fallback so a user with nowhere to hang still gets a grip row.
# Anchor lookup takes the HIGHEST-rank anchor the user can perform (see
# ``forge._anchor_for`` / ``forge.js:renderTrial``), so the fallback only shows
# when the hang is out of reach. The other six patterns keep exactly one anchor.
ASSESSMENT_ANCHORS = {
    "Push-up", "Australian Row", "Pike Push-up", "Squat", "Plank", "Glute Bridge",
    "Dead Hang", "Towel Wring Hold",
}

# Movements performed one side at a time. Their rep targets are forced even (so
# both sides get equal work).
# Note the lower ladder is NOT wholly per-side: Squat, Goblet Squat and Barbell
# Back Squat sit on the unilateral pattern but are two-legged lifts.
# Keep in sync with migration 0009's PER_SIDE list.
PER_SIDE = {
    # Lower (unilateral)
    "Assisted Split Squat", "Split Squat", "Bulgarian Split Squat",
    "Dumbbell Bulgarian Split Squat", "Assisted Pistol Squat", "Pistol Squat",
    "Shrimp Squat", "Dragon Squat",
    # Horizontal push
    "Incline Archer Push-up", "Archer Push-up", "Typewriter Push-up",
    "Elevated One-Arm Push-up", "One-Arm Push-up",
    # Vertical pull
    "Single-Arm Australian Row", "Archer Pull-up", "Archer Chin-up",
    # Hinge
    "Single-Leg Glute Bridge",
    # Grip (timed per-side holds keep their seconds; the value is per side)
    "Assisted One-Arm Hang", "One-Arm Dead Hang", "One-Arm Towel Hang",
    "Suitcase Carry",
}


def rest_for(mode, rmin, rmax, timed, pattern_key=None):
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
        # Grip holds are the exception: the forearms are the limiting tissue and
        # recover slower than a braced trunk, so a grip hold rests ~90s while
        # every other hold (plank, hollow) keeps 40s.
        return 90 if pattern_key == "grip" else 40
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
                # Optional trailing elements: grip label, then variant group.
                grip = rung[9] if len(rung) > 9 else Exercise.Grip.NA
                variant_group = rung[10] if len(rung) > 10 else ""
                # An equipment entry that is a tuple means "any one of these"
                # (a bar OR rings); a plain string is required outright.
                required = [e for e in equips if not isinstance(e, tuple)]
                alternatives = [a for e in equips if isinstance(e, tuple) for a in e]
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
                        "variant_group": variant_group,
                        "video_url": VIDEOS.get(name, ""),
                        "rest_seconds": rest_for(mode, rmin, rmax, timed, pkey),
                        "is_assessment_anchor": name in ASSESSMENT_ANCHORS,
                    },
                )
                ex.required_equipment.set([equipment[e] for e in required])
                ex.alternative_equipment.set([equipment[a] for a in alternatives])
                ex.muscles.set(
                    [muscles[m] for m in EXERCISE_MUSCLES.get(name, [])]
                )
                created.append((mode, ex))
                n_ex += 1

            # Link regression/progression within each ladder, ordered by rank —
            # one ladder per mode, or a single ladder for SINGLE_LADDER_PATTERNS.
            # Rungs sharing a ``variant_group`` (the bar pull-up grips) collapse
            # into ONE ladder node so traversal stays linear: every variant gets
            # the same adjacent rungs, and adjacent rungs link to the node's first
            # (overhand) variant.
            if pkey in SINGLE_LADDER_PATTERNS:
                ladders = [[ex for _, ex in created]]
            else:
                ladders = [
                    [ex for m, ex in created if m == mode] for mode in ("difficulty", "load")
                ]
            for ladder in ladders:
                chain = sorted(ladder, key=lambda e: e.difficulty_rank)
                nodes = []
                node_by_group = {}
                for ex in chain:
                    if ex.variant_group:
                        # Every variant of a group shares one node, regardless of
                        # its order in the sorted chain.
                        node = node_by_group.get(ex.variant_group)
                        if node is None:
                            node = node_by_group[ex.variant_group] = []
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

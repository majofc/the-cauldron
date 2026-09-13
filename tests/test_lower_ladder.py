"""Lower is ONE ranked ladder: every movement in the pattern, bodyweight and
loaded, on a single strict sequence linked into one chain. ``progression_mode``
only decides how a user advances once on a rung.

Also covers the catalog-wide rule that placement thresholds never dip as rank
climbs — ``place_from_assessment`` stops at the first rung a score misses, so a
dip would make the rungs above it unreachable.
"""

from itertools import groupby

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.models import (
    AssessmentResult,
    Equipment,
    Exercise,
    MovementPattern,
    PrescribedExercise,
    Program,
    ProgramDay,
)
from the_cauldron.services import forge

User = get_user_model()

LOWER_ORDER = [
    ("Squat", "difficulty"),
    ("Assisted Split Squat", "difficulty"),
    ("Goblet Squat", "load"),
    ("Split Squat", "difficulty"),
    ("Bulgarian Split Squat", "difficulty"),
    ("Barbell Back Squat", "load"),
    ("Dumbbell Bulgarian Split Squat", "load"),
    ("Assisted Pistol Squat", "difficulty"),
    ("Pistol Squat", "difficulty"),
    ("Shrimp Squat", "difficulty"),
    ("Dragon Squat", "difficulty"),
]


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="squatter", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _lower():
    return list(
        Exercise.objects.filter(pattern__key="lower_unilateral").order_by("difficulty_rank")
    )


def _own(user, *keys, **profile_fields):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    for field, value in profile_fields.items():
        setattr(profile, field, value)
    profile.save()
    return profile


def _program_on(user, exercise, **fields) -> PrescribedExercise:
    program = Program.objects.create(user=user, is_active=True)
    day = ProgramDay.objects.create(program=program, day_index=0, name="Today's Forge")
    return PrescribedExercise.objects.create(
        day=day,
        pattern=exercise.pattern,
        exercise=exercise,
        target_sets=3,
        target_reps_min=exercise.rep_range_min,
        target_reps_max=exercise.rep_range_max,
        target_rest_seconds=exercise.rest_seconds,
        order=0,
        **fields,
    )


# ── The catalog ──────────────────────────────────────────────────────────────


def test_bodyweight_squat_is_the_first_rung(seeded):
    squat = Exercise.objects.get(pattern__key="lower_unilateral", name="Squat")
    assert squat.difficulty_rank == 1
    assert squat.progression_mode == Exercise.ProgressionMode.DIFFICULTY
    assert squat.placement_threshold == 0
    assert squat.is_per_side is False
    assert squat.regression is None
    assert [e.key for e in squat.required_equipment.all()] == ["bodyweight"]
    assert {m.key for m in squat.muscles.all()} == {"quads", "glutes"}


def test_lower_ranks_are_unique_and_in_ladder_order(seeded):
    rungs = _lower()
    ranks = [e.difficulty_rank for e in rungs]
    assert len(ranks) == len(set(ranks))
    assert [(e.name, e.progression_mode) for e in rungs] == LOWER_ORDER


def test_lower_links_form_one_chain_in_rank_order(seeded):
    rungs = _lower()
    for i, ex in enumerate(rungs):
        assert ex.regression == (rungs[i - 1] if i > 0 else None), ex.name
        assert ex.progression == (rungs[i + 1] if i < len(rungs) - 1 else None), ex.name
    chain_of = forge.chain_map_for({rungs[0].pattern_id})
    assert len({chain_of[e.pk] for e in rungs}) == 1


def test_other_patterns_keep_a_chain_per_mode(seeded):
    """Only lower merges — horizontal push still has a separate loaded ladder."""
    bench = Exercise.objects.get(name="Dumbbell Bench Press")
    assert bench.regression is None
    assert bench.progression == Exercise.objects.get(name="Barbell Bench Press")


def test_placement_thresholds_never_dip_as_rank_climbs(seeded):
    for pattern in MovementPattern.objects.all():
        rungs = list(pattern.exercises.order_by("difficulty_rank"))
        by_rank = [
            (rank, {e.placement_threshold for e in group})
            for rank, group in groupby(rungs, key=lambda e: e.difficulty_rank)
        ]
        for rank, thresholds in by_rank:
            # Same-rank rungs share a threshold, so tie order can't decide placement.
            assert len(thresholds) == 1, f"{pattern.key} rank {rank}: {thresholds}"
        values = [t.pop() for _, t in by_rank]
        assert values == sorted(values), f"{pattern.key}: {values}"
        assert values[0] == 0, f"{pattern.key}: easiest rung must always place"


def test_bodyweight_users_eligible_lower_ladder_is_contiguous(seeded, user):
    profile = _own(user, "bodyweight")
    pattern = MovementPattern.objects.get(key="lower_unilateral")
    eligible = sorted(
        forge.eligible_exercises(pattern, profile), key=lambda e: e.difficulty_rank
    )
    assert [e.name for e in eligible] == [
        "Squat", "Assisted Split Squat", "Split Squat", "Assisted Pistol Squat",
        "Pistol Squat", "Shrimp Squat", "Dragon Squat",
    ]
    # Walking the links past what the user can't do visits exactly that list.
    owned = forge.owned_equipment_keys(profile)
    walked, ex = [], eligible[0]
    while ex is not None:
        walked.append(ex)
        ex = forge.first_performable_along(ex.progression, "progression", owned)
    assert walked == eligible


# ── Placement on the merged ladder ───────────────────────────────────────────


def _place(client, score):
    split = Exercise.objects.get(name="Split Squat")
    resp = client.post(
        "/cauldron/api/assessment/",
        {"results": [{"pattern_key": "lower_unilateral",
                      "tested_exercise": str(split.uuid), "reps_or_seconds": score}]},
        format="json",
    )
    assert resp.status_code == 201
    return AssessmentResult.objects.filter(pattern__key="lower_unilateral").latest(
        "created_at"
    ).placed_exercise.name


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "Squat"),
        (3, "Assisted Split Squat"),
        (6, "Assisted Split Squat"),  # Goblet needs weights
        (8, "Split Squat"),
        (12, "Split Squat"),  # Bulgarian needs a bench
        (17, "Assisted Pistol Squat"),
        (18, "Pistol Squat"),
        (40, "Dragon Squat"),
    ],
)
def test_bodyweight_placement(seeded, client, user, score, expected):
    _own(user, "bodyweight")
    assert _place(client, score) == expected


@pytest.mark.parametrize(
    "score,expected",
    [
        (5, "Assisted Split Squat"),
        (6, "Goblet Squat"),
        (10, "Bulgarian Split Squat"),
        (12, "Barbell Back Squat"),
        (14, "Dumbbell Bulgarian Split Squat"),
        (16, "Assisted Pistol Squat"),
    ],
)
def test_fully_equipped_placement(seeded, client, user, score, expected):
    _own(user, "bodyweight", "dumbbells", "kettlebell", "barbell", "bench")
    assert _place(client, score) == expected


# ── Progressing along the mixed chain ────────────────────────────────────────


def _log_amrap(user, presc, reps):
    session = forge.start_session(user, presc.day)
    amrap = session.set_logs.get(is_amrap=True, prescribed_exercise=presc)
    return forge.apply_session_log(session, {str(amrap.uuid): {"actual_reps": reps}})


def test_bodyweight_unlock_skips_a_loaded_rung_it_cannot_do(seeded, user):
    """Assisted Split Squat → Goblet Squat is the next link, but a bodyweight
    user can't hold a weight: the unlock must climb to Split Squat, not fall back
    to the rung they are already on."""
    _own(user, "bodyweight")
    assisted = Exercise.objects.get(name="Assisted Split Squat")
    presc = _program_on(user, assisted, sessions_at_top=1)

    _log_amrap(user, presc, assisted.rep_range_max)

    presc.refresh_from_db()
    assert presc.pending_progression.name == "Split Squat"


def test_equipped_unlock_lands_on_the_loaded_rung(seeded, user):
    _own(user, "bodyweight", "dumbbells", "kettlebell", kettlebell_weights=[8, 12, 16])
    assisted = Exercise.objects.get(name="Assisted Split Squat")
    presc = _program_on(user, assisted, sessions_at_top=1)

    _log_amrap(user, presc, assisted.rep_range_max)

    presc.refresh_from_db()
    assert presc.pending_progression.name == "Goblet Squat"


def test_accepting_an_unlock_walks_past_lost_equipment(seeded, user):
    """Unlock earned on Goblet Squat's position, dumbbells gone by accept time."""
    _own(user, "bodyweight")
    presc = _program_on(
        user,
        Exercise.objects.get(name="Assisted Split Squat"),
        pending_progression=Exercise.objects.get(name="Goblet Squat"),
    )

    landed = forge.accept_progression(user, presc)

    assert landed.name == "Split Squat"


def test_a_blocked_target_is_substituted_not_walked_past(seeded, user):
    """Blocked (not just unequipped) → nearest easier stand-in, never a harder
    rung: blocking Pull-up must offer Chin-up, not Archer Pull-up."""
    _own(user, "bodyweight", "pullup_bar")
    pullup = Exercise.objects.get(name="Pull-up")
    forge.block_exercise(user, pullup)
    presc = _program_on(
        user, Exercise.objects.get(name="Negative Pull-up"), pending_progression=pullup
    )

    landed = forge.accept_progression(user, presc)

    assert landed.name == "Chin-up"


def test_loaded_rung_still_progresses_by_weight(seeded, user):
    _own(user, "bodyweight", "dumbbells", "kettlebell", kettlebell_weights=[8, 12, 16])
    goblet = Exercise.objects.get(name="Goblet Squat")
    presc = _program_on(user, goblet, target_load=8)

    _log_amrap(user, presc, goblet.rep_range_max)

    presc.refresh_from_db()
    assert presc.exercise == goblet
    assert presc.pending_progression is None
    assert presc.target_load == 12


def test_regressing_onto_a_loaded_rung_gets_a_starting_load(seeded, user):
    _own(user, "bodyweight", "dumbbells", "kettlebell", kettlebell_weights=[8, 12, 16])
    split = Exercise.objects.get(name="Split Squat")
    presc = _program_on(user, split)

    _log_amrap(user, presc, 1)

    presc.refresh_from_db()
    assert presc.exercise.name == "Goblet Squat"
    assert presc.target_load == 8


# ── Existing users across the renumbering ────────────────────────────────────


OLD_LOWER = {
    # name: (rank, placement_threshold) as seeded before the merge
    "Assisted Split Squat": (1, 0),
    "Split Squat": (2, 10),
    "Bulgarian Split Squat": (3, 12),
    "Assisted Pistol Squat": (4, 12),
    "Pistol Squat": (5, 6),
    "Shrimp Squat": (6, 16),
    "Dragon Squat": (7, 22),
    "Goblet Squat": (2, 0),
    "Dumbbell Bulgarian Split Squat": (3, 0),
    "Barbell Back Squat": (4, 0),
}


def test_reseeding_keeps_existing_prescriptions_on_valid_rungs(seeded, user):
    """Rewind lower to the pre-merge catalog, prescribe every old rung, re-seed:
    each prescription (and a parked unlock) still points at the same, now
    uniquely-ranked exercise."""
    Exercise.objects.filter(pattern__key="lower_unilateral", name="Squat").delete()
    for name, (rank, threshold) in OLD_LOWER.items():
        Exercise.objects.filter(pattern__key="lower_unilateral", name=name).update(
            difficulty_rank=rank, placement_threshold=threshold
        )
    before = {}
    for ex in Exercise.objects.filter(pattern__key="lower_unilateral"):
        presc = _program_on(
            user, ex, pending_progression=ex.progression
        )
        before[presc.pk] = (ex.pk, ex.progression_id)

    call_command("seed_forge")

    lower_ids = {e.pk for e in _lower()}
    for presc in PrescribedExercise.objects.filter(pk__in=before):
        exercise_id, pending_id = before[presc.pk]
        assert presc.exercise_id == exercise_id
        assert presc.exercise_id in lower_ids
        assert presc.pending_progression_id == pending_id
    ranks = [e.difficulty_rank for e in _lower()]
    assert len(ranks) == len(set(ranks)) == len(LOWER_ORDER)

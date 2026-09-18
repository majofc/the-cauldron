"""Set my rung: move a prescription to any performable rung of its chain.

The Trial can place a user well above what they can train, and the engine only
regresses one rung after a failed session. ``POST prescription/<uuid>/set-rung/``
moves the whole chain in one step, up or down, warning (409) — never blocking —
when a climb outruns the latest Trial score.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    Equipment,
    Exercise,
    MovementPattern,
    PrescribedExercise,
    Program,
    ProgramDay,
    WorkoutSession,
)
from the_cauldron.services import forge, progression

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="climber", email="climber@example.com", password="pw12345!"
    )


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _ex(name):
    return Exercise.objects.get(name=name)


def _own(user, *keys, **profile_fields):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    for field, value in profile_fields.items():
        setattr(profile, field, value)
    profile.save()
    return profile


def _program(user, exercise, days=1, **fields):
    """An active program prescribing ``exercise`` once on each of ``days`` days."""
    program = Program.objects.create(user=user, is_active=True)
    prescriptions = []
    for index in range(days):
        day = ProgramDay.objects.create(program=program, day_index=index, name=f"Day {index}")
        prescriptions.append(
            PrescribedExercise.objects.create(
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
        )
    return prescriptions


def _trial(user, pattern_key, score, placed=None):
    """A completed Trial scoring ``score`` on ``pattern_key``."""
    pattern = MovementPattern.objects.get(key=pattern_key)
    anchor = Exercise.objects.get(pattern=pattern, is_assessment_anchor=True)
    session = AssessmentSession.objects.create(user=user, completed_at=timezone.now())
    AssessmentResult.objects.create(
        session=session,
        pattern=pattern,
        tested_exercise=anchor,
        reps_or_seconds=score,
        placed_exercise=placed,
    )
    return session


def _set_rung(client, presc, target, **extra):
    return client.post(
        f"/cauldron/api/prescription/{presc.uuid}/set-rung/",
        {"exercise": str(target.uuid), **extra},
        format="json",
    )


# ── Moving in one step, program-wide ─────────────────────────────────────────


def test_shrimp_squat_drops_several_rungs_on_every_program_day(seeded, client, user):
    """Regression: placed at Shrimp Squat, the user drops to Split Squat (six
    rungs) in one move, and the next session on every day prescribes it."""
    _own(user, "bodyweight")
    shrimp = _ex("Shrimp Squat")
    _trial(user, "lower_unilateral", 120, placed=shrimp)
    prescriptions = _program(user, shrimp, days=3)
    split = _ex("Split Squat")

    resp = _set_rung(client, prescriptions[0], split)

    assert resp.status_code == 200, resp.content
    assert resp.data["applied_to"] == 3
    assert resp.data["exercise"]["name"] == "Split Squat"
    for presc in prescriptions:
        presc.refresh_from_db()
        assert presc.exercise == split
        session = forge.start_session(user, presc.day)
        assert {s.exercise.name for s in session.set_logs.all()} == {"Split Squat"}


def test_a_climb_can_skip_rungs_too(seeded, client, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Squat"))

    resp = _set_rung(client, presc, _ex("Pistol Squat"))

    assert resp.status_code == 200
    presc.refresh_from_db()
    assert presc.exercise.name == "Pistol Squat"


def test_other_chains_are_untouched(seeded, client, user):
    _own(user, "bodyweight")
    [lower] = _program(user, _ex("Shrimp Squat"))
    push = PrescribedExercise.objects.create(
        day=lower.day, pattern=_ex("Push-up").pattern, exercise=_ex("Push-up"),
        target_sets=3, target_reps_min=5, target_reps_max=12, order=1,
    )

    resp = _set_rung(client, lower, _ex("Squat"))

    assert resp.data["applied_to"] == 1
    push.refresh_from_db()
    assert push.exercise.name == "Push-up"


# ── The not-ready warning ────────────────────────────────────────────────────


def test_descending_never_warns(seeded, client, user):
    _own(user, "bodyweight")
    _trial(user, "lower_unilateral", 5)  # far below even the current rung
    [presc] = _program(user, _ex("Shrimp Squat"))

    resp = _set_rung(client, presc, _ex("Assisted Split Squat"))

    assert resp.status_code == 200


def test_climbing_past_the_trial_warns_with_the_gap_then_proceeds_on_confirm(
    seeded, client, user
):
    _own(user, "bodyweight")
    _trial(user, "horizontal_push", 5)
    [presc] = _program(user, _ex("Knee Push-up"))
    diamond = _ex("Diamond Push-up")

    warned = _set_rung(client, presc, diamond)

    assert warned.status_code == 409
    assert warned.data["unready"] is True
    assert warned.data["required"] == 25
    assert warned.data["your_trial"] == 5
    assert warned.data["pattern"] == diamond.pattern.name
    assert warned.data["anchor"] == "Push-up"
    presc.refresh_from_db()
    assert presc.exercise.name == "Knee Push-up"  # nothing moved yet

    confirmed = _set_rung(client, presc, diamond, confirm_unready=True)

    assert confirmed.status_code == 200
    presc.refresh_from_db()
    assert presc.exercise == diamond


def test_a_truthy_string_does_not_confirm(seeded, client, user):
    """The warning is server-side: only a JSON ``true`` skips it."""
    _own(user, "bodyweight")
    _trial(user, "horizontal_push", 5)
    [presc] = _program(user, _ex("Knee Push-up"))

    resp = _set_rung(client, presc, _ex("Diamond Push-up"), confirm_unready="yes")

    assert resp.status_code == 409


def test_climbing_within_the_trial_does_not_warn(seeded, client, user):
    _own(user, "bodyweight")
    _trial(user, "horizontal_push", 12)
    [presc] = _program(user, _ex("Wall Push-up"))

    resp = _set_rung(client, presc, _ex("Push-up"))  # threshold 10

    assert resp.status_code == 200


def test_a_lateral_same_rank_move_does_not_warn(seeded, client, user):
    """RKC Plank and Hollow Body Hold share rank and threshold (60s) — a sideways
    step must not warn even when the Trial is well below it."""
    _own(user, "bodyweight")
    _trial(user, "core_anti_extension", 10)
    [presc] = _program(user, _ex("RKC Plank"))

    resp = _set_rung(client, presc, _ex("Hollow Body Hold"))

    assert resp.status_code == 200


def test_without_a_trial_the_change_applies_silently(seeded, client, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Squat"))

    resp = _set_rung(client, presc, _ex("Dragon Squat"))

    assert resp.status_code == 200


def test_options_flag_the_rungs_that_would_warn(seeded, client, user):
    _own(user, "bodyweight")
    _trial(user, "horizontal_push", 12)
    [presc] = _program(user, _ex("Knee Push-up"))

    resp = client.get(f"/cauldron/api/prescription/{presc.uuid}/set-rung/")

    assert resp.status_code == 200
    flags = {o["name"]: o["unready"] for o in resp.data["options"]}
    assert flags["Wall Push-up"] is False
    assert flags["Push-up"] is False  # threshold 10 ≤ 12
    assert flags["Incline Archer Push-up"] is True  # threshold 20 > 12
    assert resp.data["trial_score"] == 12
    assert [o["name"] for o in resp.data["options"] if o["is_current"]] == ["Knee Push-up"]


# ── Re-deriving the prescription ─────────────────────────────────────────────


def test_rung_fields_rederive_and_counters_reset(seeded, client, user):
    _own(user, "bodyweight")
    shrimp = _ex("Shrimp Squat")
    [presc] = _program(
        user, shrimp, sessions_at_top=1, pending_progression=_ex("Dragon Squat")
    )
    pistol = _ex("Assisted Pistol Squat")

    _set_rung(client, presc, pistol)

    presc.refresh_from_db()
    assert presc.sessions_at_top == 0
    assert presc.pending_progression is None
    assert presc.target_rest_seconds == pistol.rest_seconds
    assert (presc.target_reps_min, presc.target_reps_max) == progression.rep_targets_for(
        pistol, pistol.rep_range_min, pistol.rep_range_max
    )


def test_crossing_onto_a_per_side_rung_forces_even_targets(seeded, client, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Squat"))  # two-legged, 10–20
    split = _ex("Split Squat")  # per side, 8–15
    assert split.is_per_side

    _set_rung(client, presc, split)

    presc.refresh_from_db()
    assert (presc.target_reps_min, presc.target_reps_max) == (8, 16)


def test_crossing_off_a_per_side_rung_uses_the_plain_range(seeded, client, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Split Squat"))
    presc.target_reps_max = 16
    presc.save()

    _set_rung(client, presc, _ex("Squat"))

    presc.refresh_from_db()
    assert (presc.target_reps_min, presc.target_reps_max) == (10, 20)


def test_crossing_onto_a_loaded_rung_seeds_a_load(seeded, client, user):
    _own(user, "bodyweight", "dumbbells", "kettlebell", kettlebell_weights=[8, 12, 16])
    [presc] = _program(user, _ex("Split Squat"))

    resp = _set_rung(client, presc, _ex("Goblet Squat"))

    assert resp.status_code == 200
    presc.refresh_from_db()
    assert presc.exercise.progression_mode == Exercise.ProgressionMode.LOAD
    assert presc.target_load == 8


def test_crossing_off_a_loaded_rung_drops_the_load(seeded, client, user):
    _own(user, "bodyweight", "dumbbells", "kettlebell", kettlebell_weights=[8, 12, 16])
    [presc] = _program(user, _ex("Goblet Squat"), target_load=12)

    _set_rung(client, presc, _ex("Split Squat"))

    presc.refresh_from_db()
    assert presc.target_load is None


def test_normal_progression_resumes_one_rung_up_from_the_new_position(seeded, client, user):
    _own(user, "bodyweight", "bench")
    [presc] = _program(user, _ex("Shrimp Squat"))
    split = _ex("Split Squat")
    _set_rung(client, presc, split)

    for _ in range(progression.SESSIONS_TO_ADVANCE):
        presc.refresh_from_db()
        session = forge.start_session(user, presc.day)
        amrap = session.set_logs.get(is_amrap=True, prescribed_exercise=presc)
        forge.apply_session_log(
            session, {str(amrap.uuid): {"actual_reps": presc.target_reps_max}}
        )

    presc.refresh_from_db()
    assert presc.exercise == split
    assert presc.pending_progression.name == "Bulgarian Split Squat"


# ── What may be picked ───────────────────────────────────────────────────────


def test_options_cover_the_whole_single_ladder(seeded, client, user):
    """Lower is ONE ladder across both modes — the picker must not show a
    per-mode slice of it."""
    _own(
        user, "bodyweight", "dumbbells", "kettlebell", "barbell", "bench",
        kettlebell_weights=[8], dumbbell_weights=[10], barbell_plates=[[20, 2]],
    )
    [presc] = _program(user, _ex("Split Squat"))

    resp = client.get(f"/cauldron/api/prescription/{presc.uuid}/set-rung/")

    names = [o["name"] for o in resp.data["options"]]
    expected = list(
        Exercise.objects.filter(pattern__key="lower_unilateral")
        .order_by("difficulty_rank")
        .values_list("name", flat=True)
    )
    assert names == expected


def test_blocked_and_unequipped_rungs_are_absent_and_rejected(seeded, client, user):
    _own(user, "bodyweight")  # no bench → no Bulgarian Split Squat
    pistol = _ex("Pistol Squat")
    forge.block_exercise(user, pistol)
    [presc] = _program(user, _ex("Split Squat"))

    names = {
        o["name"]
        for o in client.get(f"/cauldron/api/prescription/{presc.uuid}/set-rung/").data["options"]
    }

    assert "Pistol Squat" not in names
    assert "Bulgarian Split Squat" not in names
    assert "Goblet Squat" not in names
    assert _set_rung(client, presc, pistol).status_code == 400
    assert _set_rung(client, presc, _ex("Bulgarian Split Squat")).status_code == 400


def test_a_rung_from_another_chain_is_rejected(seeded, client, user):
    _own(user, "bodyweight", "dumbbells", "bench", dumbbell_weights=[10])
    [presc] = _program(user, _ex("Push-up"))

    # Same pattern, but the loaded push chain is a separate ladder.
    assert _set_rung(client, presc, _ex("Dumbbell Bench Press")).status_code == 400
    assert _set_rung(client, presc, _ex("Squat")).status_code == 400


def test_malformed_exercise_is_a_bad_request(seeded, client, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Squat"))

    resp = client.post(
        f"/cauldron/api/prescription/{presc.uuid}/set-rung/",
        {"exercise": "not-a-uuid"},
        format="json",
    )

    assert resp.status_code == 400


def test_another_users_prescription_is_not_found(seeded, client, user):
    other = User.objects.create_user(username="someone", email="someone@example.com", password="pw12345!")
    _own(other, "bodyweight")
    [presc] = _program(other, _ex("Shrimp Squat"))

    assert _set_rung(client, presc, _ex("Squat")).status_code == 404
    assert client.get(f"/cauldron/api/prescription/{presc.uuid}/set-rung/").status_code == 404


def test_requires_authentication(seeded, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Shrimp Squat"))

    resp = _set_rung(APIClient(), presc, _ex("Squat"))

    assert resp.status_code in (401, 403)


# ── Today's open session ─────────────────────────────────────────────────────


def test_a_movement_with_logged_sets_today_is_refused(seeded, client, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Shrimp Squat"))
    session = forge.start_session(user, presc.day)
    first = session.set_logs.order_by("set_index").first()
    first.actual_reps = 3
    first.save()

    resp = _set_rung(client, presc, _ex("Squat"))

    assert resp.status_code == 409
    presc.refresh_from_db()
    assert presc.exercise.name == "Shrimp Squat"


def test_untouched_sets_on_todays_session_follow_the_move(seeded, client, user):
    _own(user, "bodyweight")
    [presc] = _program(user, _ex("Shrimp Squat"))
    session = forge.start_session(user, presc.day)

    assert _set_rung(client, presc, _ex("Split Squat")).status_code == 200

    rows = list(session.set_logs.order_by("set_index"))
    assert {r.exercise.name for r in rows} == {"Split Squat"}
    assert rows[-1].is_amrap and rows[-1].expected_reps == 16
    assert WorkoutSession.objects.filter(user=user).count() == 1


# ── Reading the change back ──────────────────────────────────────────────────


def test_swap_picker_reports_the_chosen_rung_not_the_trial_placement(seeded, client, user):
    _own(user, "bodyweight")
    shrimp = _ex("Shrimp Squat")
    _trial(user, "lower_unilateral", 120, placed=shrimp)
    [presc] = _program(user, shrimp)
    _set_rung(client, presc, _ex("Split Squat"))

    lower = [
        c for c in forge.swap_candidates(user)["candidates"]
        if c["pattern_key"] == "lower_unilateral"
    ]

    assert [c["exercise_name"] for c in lower] == ["Split Squat"]


def test_rung_overview_maps_every_offered_rung_to_its_prescription(seeded, client, user):
    _own(user, "bodyweight")
    prescriptions = _program(user, _ex("Shrimp Squat"), days=2)

    resp = client.get("/cauldron/api/rungs/")

    assert resp.status_code == 200
    [chain] = resp.data["chains"]
    assert set(chain["prescriptions"]) == {str(p.uuid) for p in prescriptions}
    assert resp.data["by_exercise"][str(_ex("Squat").uuid)] == chain["prescription"]
    assert str(_ex("Goblet Squat").uuid) not in resp.data["by_exercise"]


def test_the_evolution_chart_does_not_see_a_self_selected_move(seeded, client, user):
    """The Trial record is history — a manual drop writes nothing to it, so the
    chart can't read it as a setback."""
    _own(user, "bodyweight")
    shrimp = _ex("Shrimp Squat")
    _trial(user, "lower_unilateral", 120, placed=shrimp)
    [presc] = _program(user, shrimp)
    before = forge.trial_history(user)

    _set_rung(client, presc, _ex("Squat"))

    assert forge.trial_history(user) == before
    assert AssessmentResult.objects.filter(session__user=user).count() == 1


# ── No-op moves ──────────────────────────────────────────────────────────────


def test_choosing_the_current_rung_keeps_earned_progress(seeded, client, user):
    _own(user, "bodyweight")
    split = _ex("Split Squat")
    [presc] = _program(user, split, sessions_at_top=1, pending_progression=_ex("Bulgarian Split Squat"))

    resp = _set_rung(client, presc, split)

    assert resp.status_code == 200
    assert resp.data["applied_to"] == 0
    presc.refresh_from_db()
    assert presc.sessions_at_top == 1
    assert presc.pending_progression.name == "Bulgarian Split Squat"


def test_the_other_grip_is_the_same_rung(seeded, client, user):
    """Pull-up and Chin-up share a ladder position — picking the other grip must
    not wipe progress, and the picker reports both as current."""
    _own(user, "bodyweight", "pullup_bar")
    pullup = _ex("Pull-up")
    [presc] = _program(user, pullup, sessions_at_top=1)

    resp = _set_rung(client, presc, _ex("Chin-up"))

    assert resp.data["applied_to"] == 0
    presc.refresh_from_db()
    assert presc.exercise == pullup and presc.sessions_at_top == 1
    options = client.get(f"/cauldron/api/prescription/{presc.uuid}/set-rung/").data["options"]
    assert {o["name"] for o in options if o["is_current"]} == {"Pull-up", "Chin-up"}


def test_overview_measures_from_the_served_day(seeded, client, user):
    """An older multi-day program can drift apart per day; the overview takes the
    served day (index 0), the one Today posts from."""
    _own(user, "bodyweight")
    day0, day1 = _program(user, _ex("Push-up"), days=2)
    day1.exercise = _ex("Knee Push-up")
    day1.save()

    [chain] = [
        c for c in client.get("/cauldron/api/rungs/").data["chains"]
        if c["pattern_key"] == "horizontal_push"
    ]

    assert chain["prescription"] == str(day0.uuid)
    assert chain["current_name"] == "Push-up"

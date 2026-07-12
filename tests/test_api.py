"""API + orchestration tests for The Forge. Requires DB (seeded catalog)."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from datetime import timedelta

from django.utils import timezone

from the_cauldron.models import (
    Equipment,
    Exercise,
    MovementPattern,
    Program,
    SetLog,
    WorkoutSession,
)
from the_cauldron.services import forge

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="athlete", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _set_equipment(client, keys, **extra):
    return client.put("/cauldron/api/equipment/", {"equipment": keys, **extra}, format="json")


# ── Auth ─────────────────────────────────────────────────────────────────────


def test_endpoints_require_auth(db):
    anon = APIClient()
    assert anon.get("/cauldron/api/equipment/").status_code in (401, 403)
    assert anon.get("/cauldron/api/program/").status_code in (401, 403)


# ── Equipment filtering ──────────────────────────────────────────────────────


def test_exercise_catalog_filters_by_owned_equipment(seeded, client):
    # Bodyweight only → no barbell exercises appear.
    _set_equipment(client, ["bodyweight"])
    resp = client.get("/cauldron/api/exercises/?equipment=mine")
    assert resp.status_code == 200
    names = {e["name"] for e in resp.json()}
    assert "Push-up" in names
    assert "Barbell Bench Press" not in names

    # Add barbell + bench → barbell bench now eligible.
    _set_equipment(client, ["bodyweight", "barbell", "bench"])
    names2 = {e["name"] for e in client.get("/cauldron/api/exercises/?equipment=mine").json()}
    assert "Barbell Bench Press" in names2


# ── Assessment → program ─────────────────────────────────────────────────────


def _assessment_payload():
    results = []
    for pattern in MovementPattern.objects.all():
        ex = pattern.exercises.order_by("difficulty_rank").first()
        results.append(
            {"pattern_key": pattern.key, "tested_exercise": str(ex.uuid), "reps_or_seconds": 6}
        )
    return {"split": "full_body_3x", "results": results}


def test_assessment_creates_active_program(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    resp = client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    assert resp.status_code == 201
    assert Program.objects.filter(user=user, is_active=True).count() == 1
    program = resp.json()["program"]
    assert len(program["days"]) == 3
    assert all(len(d["prescriptions"]) >= 1 for d in program["days"])


# ── Today snapshot + log → next prescription ─────────────────────────────────


def test_today_snapshots_then_log_advances(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"], dumbbell_weights=[])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    today = client.get("/cauldron/api/today/?day=0")
    assert today.status_code == 201
    session = today.json()
    # Expected values were snapshotted.
    assert all(s["expected_reps"] is not None for s in session["set_logs"])

    # Log every set: AMRAP sets hit max+ to trigger progression.
    set_results = {}
    for s in session["set_logs"]:
        target = s["expected_reps"] + (3 if s["is_amrap"] else 0)
        set_results[s["uuid"]] = {"actual_reps": target, "actual_load": s["expected_load"]}

    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/log/", {"sets": set_results}, format="json"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["status"] == "completed"
    assert len(body["progression"]) >= 1  # at least one delta message

    # Session persisted as completed in history.
    assert WorkoutSession.objects.filter(user=user, status="completed").count() == 1


# ── Retake ───────────────────────────────────────────────────────────────────


def test_retake_deactivates_old_keeps_history(seeded, client, user):
    _set_equipment(client, ["bodyweight"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    assert Program.objects.filter(user=user, is_active=True).count() == 1

    retake = client.post("/cauldron/api/assessment/retake/")
    assert retake.status_code == 201
    # Old program deactivated; old assessment kept but inactive.
    assert Program.objects.filter(user=user, is_active=True).count() == 0
    assert user.forge_assessments.count() == 2  # history preserved


# ── Pure-ish placement via orchestrator ──────────────────────────────────────


def test_eligible_exercises_includes_bodyweight_implicitly(seeded, user):
    profile = forge.get_or_create_equipment_profile(user)
    pattern = MovementPattern.objects.get(key="horizontal_push")
    eligible = forge.eligible_exercises(pattern, profile)
    assert any(e.name == "Push-up" for e in eligible)


# ── Exercise blocking + substitution ─────────────────────────────────────────


def test_catalog_groups_by_equipment_and_flags_owned(seeded, client):
    _set_equipment(client, ["bodyweight", "dumbbells"])
    resp = client.get("/cauldron/api/catalog/")
    assert resp.status_code == 200
    groups = {g["equipment_key"]: g for g in resp.json()["groups"]}
    assert "bodyweight" in groups and groups["bodyweight"]["owned"] is True
    assert groups["dumbbells"]["owned"] is True
    # A group the user doesn't own is present but flagged not-owned.
    assert groups["barbell"]["owned"] is False
    # Bodyweight group lists a known bodyweight move.
    bw_names = {e["name"] for e in groups["bodyweight"]["exercises"]}
    assert "Push-up" in bw_names


def test_block_returns_substitute_and_excludes_from_eligible(seeded, client, user):
    _set_equipment(client, ["bodyweight"])
    pushup = Exercise.objects.get(name="Push-up")
    resp = client.post(f"/cauldron/api/exercises/{pushup.uuid}/block/", {}, format="json")
    assert resp.status_code == 200
    body = resp.json()
    # An equal-or-easier same-pattern stand-in is offered.
    assert body["substitute"] is not None
    assert body["substitute"]["difficulty_rank"] <= pushup.difficulty_rank

    # Blocked move drops out of the eligible ladder.
    profile = forge.get_or_create_equipment_profile(user)
    blocked = forge.blocked_exercise_ids(user)
    eligible = forge.eligible_exercises(pushup.pattern, profile, exclude_ids=blocked)
    assert all(e.name != "Push-up" for e in eligible)


def test_block_swaps_live_prescription_then_unblock(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    # Find an exercise actually prescribed in the active program and block it.
    from the_cauldron.models import PrescribedExercise

    presc = PrescribedExercise.objects.filter(
        day__program__user=user, day__program__is_active=True
    ).first()
    target = presc.exercise
    resp = client.post(f"/cauldron/api/exercises/{target.uuid}/block/", {}, format="json")
    assert resp.status_code == 200
    # No live prescription still points at the blocked move.
    assert not PrescribedExercise.objects.filter(
        day__program__user=user, day__program__is_active=True, exercise=target
    ).exists()

    # Unblock lifts the record.
    resp2 = client.post(f"/cauldron/api/exercises/{target.uuid}/unblock/", {}, format="json")
    assert resp2.status_code == 200
    assert target.pk not in forge.blocked_exercise_ids(user)


# ── Peer "flames" score + norms + L/R persistence ────────────────────────────


def _set_demographics(client, birth_year=1995, sex="male"):
    return client.put(
        "/cauldron/api/equipment/",
        {"equipment": ["bodyweight"], "birth_year": birth_year, "sex": sex},
        format="json",
    )


def test_assessment_returns_peer_flames_for_normed_move(seeded, client):
    _set_demographics(client, birth_year=1995, sex="male")
    pushup = Exercise.objects.get(name="Push-up")
    pattern = pushup.pattern
    payload = {
        "split": "full_body_3x",
        "results": [
            {"pattern_key": pattern.key, "tested_exercise": str(pushup.uuid), "reps_or_seconds": 25}
        ],
    }
    resp = client.post("/cauldron/api/assessment/", payload, format="json")
    assert resp.status_code == 201
    peer = {p["exercise"]: p for p in resp.json()["peer"]}
    assert "Push-up" in peer
    score = peer["Push-up"]["score"]
    assert score["has_data"] is True
    assert 1 <= score["flames"] <= 10


def test_catalog_reports_best_fires_ever(seeded, client):
    """The Exercises catalog annotates each move with the user's best peer
    "fires" ever earned; un-normed/never-scored moves report null."""
    _set_demographics(client, birth_year=1995, sex="male")
    pushup = Exercise.objects.get(name="Push-up")
    payload = {
        "split": "full_body_3x",
        "results": [
            {"pattern_key": pushup.pattern.key, "tested_exercise": str(pushup.uuid), "reps_or_seconds": 25}
        ],
    }
    assert client.post("/cauldron/api/assessment/", payload, format="json").status_code == 201

    catalog = client.get("/cauldron/api/catalog/").json()
    by_name = {e["name"]: e for g in catalog["groups"] for e in g["exercises"]}
    assert 1 <= by_name["Push-up"]["best_fires"] <= 10
    assert by_name["Push-up"]["best_fires_value"] == 25
    # A move without a published norm never has fires.
    assert by_name["Nordic Curl"]["best_fires"] is None


def test_catalog_best_fires_null_without_demographics(seeded, client):
    """Without age+sex there is no fair peer basis, so fires stay null even
    after a normed result is recorded."""
    _set_equipment(client, ["bodyweight"])
    pushup = Exercise.objects.get(name="Push-up")
    payload = {
        "split": "full_body_3x",
        "results": [
            {"pattern_key": pushup.pattern.key, "tested_exercise": str(pushup.uuid), "reps_or_seconds": 25}
        ],
    }
    client.post("/cauldron/api/assessment/", payload, format="json")
    catalog = client.get("/cauldron/api/catalog/").json()
    by_name = {e["name"]: e for g in catalog["groups"] for e in g["exercises"]}
    assert by_name["Push-up"]["best_fires"] is None


def test_peer_score_absent_without_demographics(seeded, client):
    pushup = Exercise.objects.get(name="Push-up")
    payload = {
        "split": "full_body_3x",
        "results": [
            {"pattern_key": pushup.pattern.key, "tested_exercise": str(pushup.uuid), "reps_or_seconds": 25}
        ],
    }
    resp = client.post("/cauldron/api/assessment/", payload, format="json")
    score = resp.json()["peer"][0]["score"]
    assert score["has_data"] is False


def test_norms_endpoint_returns_decile_cutoffs(seeded, client):
    _set_demographics(client, birth_year=1995, sex="male")
    resp = client.get("/cauldron/api/norms/?exercise=Push-up")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is True
    cuts = body["cutoffs"]
    vals = [cuts[k] for k in sorted(cuts, key=int)]
    assert vals == sorted(vals)  # monotonic


def test_norms_endpoint_no_data_for_unnormed(seeded, client):
    _set_demographics(client)
    assert client.get("/cauldron/api/norms/?exercise=Nordic Curl").json()["has_data"] is False


def test_unilateral_persists_left_right_and_places_from_weaker(seeded, client, user):
    _set_equipment(client, ["bodyweight"])
    pattern = MovementPattern.objects.get(key="lower_unilateral")
    anchor = pattern.exercises.order_by("difficulty_rank").first()
    payload = {
        "split": "full_body_3x",
        "results": [
            {"pattern_key": pattern.key, "tested_exercise": str(anchor.uuid),
             "left_reps": 12, "right_reps": 7}
        ],
    }
    resp = client.post("/cauldron/api/assessment/", payload, format="json")
    assert resp.status_code == 201
    from the_cauldron.models import AssessmentResult
    ar = AssessmentResult.objects.get(session__user=user, pattern=pattern)
    assert ar.left_reps == 12 and ar.right_reps == 7
    assert ar.reps_or_seconds == 7  # placed from the weaker side


def test_progress_filter_and_asymmetry(seeded, client, user):
    _set_equipment(client, ["bodyweight"])
    pattern = MovementPattern.objects.get(key="lower_unilateral")
    anchor = pattern.exercises.order_by("difficulty_rank").first()
    client.post(
        "/cauldron/api/assessment/",
        {"split": "full_body_3x",
         "results": [{"pattern_key": pattern.key, "tested_exercise": str(anchor.uuid),
                      "left_reps": 12, "right_reps": 7}]},
        format="json",
    )
    body = client.get("/cauldron/api/progress/").json()
    assert "exercises" in body
    assert any(a["left"] == 12 and a["right"] == 7 for a in body["asymmetry"])


def test_unilateral_amrap_logs_left_right_and_counts_weaker(seeded, client, user):
    """The final (AMRAP) set of a single-leg move is logged per side: both legs
    persist and actual_reps holds the weaker side. The serializer flags the set
    as unilateral so the UI can render the split."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    from the_cauldron.models import PrescribedExercise, SetLog

    # Find a day that prescribes a unilateral (lower_unilateral) exercise. Use a
    # non-"Full Body A" day (day_index != 0): Full Body A trains only the
    # least-trained subset of patterns, so its unilateral move may be dropped.
    presc = PrescribedExercise.objects.filter(
        day__program__user=user,
        day__program__is_active=True,
        day__day_index=1,
        pattern__key="lower_unilateral",
    ).select_related("day").first()
    assert presc is not None, "seed should place a lower_unilateral exercise"

    today = client.get(f"/cauldron/api/today/?day={presc.day.day_index}")
    assert today.status_code == 201
    session = today.json()

    # The unilateral exercise's AMRAP set must be flagged for the per-leg UI.
    uni_amrap = next(
        s for s in session["set_logs"]
        if s["is_unilateral"] and s["is_amrap"]
    )
    # Non-AMRAP sets of the same move stay single-input.
    assert any(
        s["is_unilateral"] and not s["is_amrap"] for s in session["set_logs"]
    )

    set_results = {}
    for s in session["set_logs"]:
        if s["uuid"] == uni_amrap["uuid"]:
            set_results[s["uuid"]] = {"left_reps": 12, "right_reps": 7,
                                      "actual_load": s["expected_load"]}
        else:
            set_results[s["uuid"]] = {"actual_reps": s["expected_reps"],
                                      "actual_load": s["expected_load"]}

    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/log/", {"sets": set_results}, format="json"
    )
    assert resp.status_code == 200

    sl = SetLog.objects.get(uuid=uni_amrap["uuid"])
    assert sl.left_reps == 12 and sl.right_reps == 7
    assert sl.actual_reps == 7  # weaker side drives progression/peer scoring


def test_accept_and_deny_progression(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    from the_cauldron.models import PrescribedExercise

    # Pick a prescription whose pattern has a harder rung to climb to.
    presc, harder = None, None
    for p in PrescribedExercise.objects.filter(
        day__program__user=user, day__program__is_active=True
    ).select_related("exercise", "pattern"):
        nxt = (
            p.pattern.exercises.filter(difficulty_rank__gt=p.exercise.difficulty_rank)
            .order_by("difficulty_rank").first()
        )
        if nxt:
            presc, harder = p, nxt
            break
    assert presc is not None and harder is not None
    presc.pending_progression = harder
    presc.save(update_fields=["pending_progression"])
    resp = client.post(f"/cauldron/api/progression/{presc.uuid}/accept/", {}, format="json")
    assert resp.status_code == 200 and resp.json()["accepted"] is True
    presc.refresh_from_db()
    assert presc.exercise_id == harder.pk
    assert presc.pending_progression_id is None

    # Park again and deny: stays put, pending cleared.
    presc.pending_progression = harder
    presc.save(update_fields=["pending_progression"])
    before = presc.exercise_id
    resp2 = client.post(f"/cauldron/api/progression/{presc.uuid}/deny/", {}, format="json")
    assert resp2.status_code == 200 and resp2.json()["denied"] is True
    presc.refresh_from_db()
    assert presc.exercise_id == before
    assert presc.pending_progression_id is None


def test_blocked_excluded_from_generated_program(seeded, client, user):
    _set_equipment(client, ["bodyweight"])
    pushup = Exercise.objects.get(name="Push-up")
    client.post(f"/cauldron/api/exercises/{pushup.uuid}/block/", {}, format="json")

    from the_cauldron.models import PrescribedExercise

    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    used = set(
        PrescribedExercise.objects.filter(
            day__program__user=user, day__program__is_active=True
        ).values_list("exercise__name", flat=True)
    )
    assert "Push-up" not in used


# ── Full Body A least-trained selection ──────────────────────────────────────


def _log_amrap(user, day, exercise, reps, when):
    """Record a completed session with a single AMRAP set for ``exercise``.

    ``performed_at`` is what the 30-day window filters on, so pass ``when`` to
    place the set inside or outside the window."""
    session = WorkoutSession.objects.create(
        user=user,
        program_day=day,
        performed_at=when,
        status=WorkoutSession.Status.COMPLETED,
    )
    SetLog.objects.create(
        session=session,
        exercise=exercise,
        set_index=0,
        actual_reps=reps,
        is_amrap=True,
    )
    return session


def _active_program(user):
    program = Program.objects.get(user=user, is_active=True)
    return program


def test_full_body_a_keeps_least_trained_and_drops_most_trained(seeded, client, user):
    """Full Body A trains the 5 patterns with the lowest recent max AMRAP reps.
    A never-trained pattern is kept; the single highest-rep pattern is dropped."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    prescs = list(day0.prescriptions.select_related("exercise", "pattern"))
    assert len(prescs) == 6  # full body trains all six patterns

    # Give five patterns increasing recent AMRAP reps; leave the sixth untouched.
    trained, never_trained = prescs[:5], prescs[5]
    for reps, presc in zip((10, 20, 30, 40, 50), trained):
        _log_amrap(user, day0, presc.exercise, reps, timezone.now())

    selected = forge.select_day_prescriptions(user, day0)
    keys = {p.pattern.key for p in selected}

    assert len(selected) == forge.FULL_BODY_A_PATTERN_COUNT == 5
    # The most-trained pattern (50 reps) is dropped …
    assert trained[-1].pattern.key not in keys
    # … while the never-trained pattern survives (ranks as least-trained).
    assert never_trained.pattern.key in keys
    # Selection preserves the day's display order (it filters, it doesn't reorder).
    assert [p.order for p in selected] == sorted(p.order for p in selected)


def test_full_body_a_filters_the_opened_session(seeded, client, user):
    """Opening Today for day 0 snapshots only the least-trained subset."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    hot = day0.prescriptions.select_related("exercise", "pattern").first()
    _log_amrap(user, day0, hot.exercise, 60, timezone.now())

    resp = client.get("/cauldron/api/today/?day=0")
    assert resp.status_code == 201
    session = resp.json()
    # One exercise per pattern on a day, so distinct exercises == distinct
    # patterns trained.
    exercises = {sl["exercise_name"] for sl in session["set_logs"]}
    assert len(exercises) == 5
    assert hot.exercise.name not in exercises


def test_other_days_train_every_pattern(seeded, client, user):
    """Days other than Full Body A are unaffected — all patterns are trained
    even when history would rank some as most-trained."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day1 = _active_program(user).days.get(day_index=1)
    any_presc = day1.prescriptions.first()
    _log_amrap(user, day1, any_presc.exercise, 99, timezone.now())

    selected = forge.select_day_prescriptions(user, day1)
    assert len(selected) == day1.prescriptions.count() == 6


def test_recent_max_reps_ignores_sets_older_than_window(seeded, client, user):
    """AMRAP sets outside the 30-day window don't count toward 'most trained'."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    presc = day0.prescriptions.select_related("exercise").first()

    stale = timezone.now() - timedelta(days=forge.LEAST_TRAINED_WINDOW_DAYS + 5)
    _log_amrap(user, day0, presc.exercise, 50, stale)

    recent = forge.recent_max_reps_by_pattern(user)
    assert presc.pattern_id not in recent  # too old to count

    fresh = timezone.now() - timedelta(days=1)
    _log_amrap(user, day0, presc.exercise, 33, fresh)
    recent = forge.recent_max_reps_by_pattern(user)
    assert recent[presc.pattern_id] == 33

"""The Trial: bilateral anchors, the 30-day retest nudge, and per-row history.

- every pattern is tested on one bilateral anchor that has a peer norm, with a
  single value per pattern (no left/right capture);
- the retest nudge is driven by *completed* assessments and is dismissible;
- Trial history is ladder-normalised, so climbing a rung never reads as a setback.
"""

from datetime import timedelta

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
)
from the_cauldron.services import forge, norms, progression

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="smith", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _own(user, *keys):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    return profile


# ── The anchors ──────────────────────────────────────────────────────────────


EXPECTED_ANCHORS = {
    "horizontal_push": "Push-up",
    "vertical_pull": "Australian Row",
    "vertical_push": "Pike Push-up",
    "lower_unilateral": "Squat",
    "core_anti_extension": "Plank",
    "hinge": "Glute Bridge",
}


def test_each_pattern_has_exactly_its_bilateral_anchor(seeded):
    anchors = Exercise.objects.filter(is_assessment_anchor=True).select_related("pattern")
    assert {e.pattern.key: e.name for e in anchors} == EXPECTED_ANCHORS
    assert anchors.count() == len(EXPECTED_ANCHORS)


def test_no_anchor_is_a_per_side_movement(seeded):
    for ex in Exercise.objects.filter(is_assessment_anchor=True):
        assert ex.is_per_side is False, f"{ex.name} must be tested with both sides at once"


@pytest.mark.parametrize("name", sorted(EXPECTED_ANCHORS.values()))
def test_every_anchor_has_a_peer_norm(name):
    assert norms.score(name, 10, "female", 32).has_data is True


def test_a_beginner_scoring_zero_still_places_on_every_pattern(seeded, user):
    """A first-timer must complete the Trial even with a 0 — every ladder needs
    a threshold-0 floor, otherwise placement returns nothing. (Every pull rung
    needs a bar or bands, so the beginner owns a bar.)"""
    profile = _own(user, "bodyweight", "pullup_bar")
    for pattern in MovementPattern.objects.all():
        ladder = forge.eligible_exercises(pattern, profile)
        assert progression.place_from_assessment(ladder, 0) is not None, pattern.key


def test_serializer_no_longer_exposes_asymmetry(seeded, client):
    by_name = {e["name"]: e for e in client.get("/cauldron/api/exercises/").json()}
    assert "measures_asymmetry" not in by_name["Push-up"]
    # is_unilateral still labels per-side rungs for workout logging.
    assert by_name["Split Squat"]["is_unilateral"] is True


def test_single_arm_rungs_stay_linked_into_their_ladders(seeded):
    """The old unilateral anchors remain ordinary rungs, reachable by the engine."""
    for name in ("Incline Archer Push-up", "Single-Arm Australian Row"):
        ex = Exercise.objects.get(name=name)
        assert ex.regression is not None, f"{name} has no easier rung"
        assert ex.progression is not None, f"{name} has no harder rung"


@pytest.mark.parametrize(
    "easier,inserted,harder",
    [
        ("Push-up", "Incline Archer Push-up", "Diamond Push-up"),
        ("Australian Row", "Single-Arm Australian Row", "Negative Pull-up"),
    ],
)
def test_single_arm_rungs_sit_between_their_neighbours(seeded, easier, inserted, harder):
    lo = Exercise.objects.get(name=easier)
    mid = Exercise.objects.get(name=inserted)
    hi = Exercise.objects.get(name=harder)
    assert lo.difficulty_rank < mid.difficulty_rank < hi.difficulty_rank


# ── Submitting a Trial ───────────────────────────────────────────────────────


def _trial_payload(user, overrides=None):
    """One result row per pattern, using each pattern's seeded anchor."""
    overrides = overrides or {}
    rows = []
    for pattern in MovementPattern.objects.all():
        anchor = pattern.exercises.filter(is_assessment_anchor=True).first()
        assert anchor is not None, f"{pattern.key} has no anchor"
        row = {
            "pattern_key": pattern.key,
            "tested_exercise": str(anchor.uuid),
            "reps_or_seconds": 8,
        }
        row.update(overrides.get(pattern.key, {}))
        rows.append(row)
    return {"results": rows}


def test_trial_takes_one_value_per_pattern_and_returns_no_asymmetry(seeded, client, user):
    _own(user, "bodyweight", "pullup_bar")
    payload = _trial_payload(user, {"hinge": {"reps_or_seconds": 22}})
    resp = client.post("/cauldron/api/assessment/", payload, format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert "asymmetry" not in body

    result = AssessmentResult.objects.get(session__user=user, pattern__key="hinge")
    assert result.tested_exercise.name == "Glute Bridge"
    assert result.reps_or_seconds == 22
    assert result.placed_exercise.name == "Single-Leg Glute Bridge"  # 22 >= 15, < 35


def test_every_trial_row_gets_a_peer_score(seeded, client, user):
    _own(user, "bodyweight", "pullup_bar")
    profile = forge.get_or_create_equipment_profile(user)
    profile.birth_year, profile.sex = timezone.now().year - 30, "female"
    profile.save()
    resp = client.post("/cauldron/api/assessment/", _trial_payload(user), format="json")
    peer = resp.json()["peer"]
    assert len(peer) == len(EXPECTED_ANCHORS)
    assert all(p["score"]["has_data"] for p in peer), [
        p["exercise"] for p in peer if not p["score"]["has_data"]
    ]


# ── The retest nudge ─────────────────────────────────────────────────────────


def _completed_trial(user, days_ago):
    when = timezone.now() - timedelta(days=days_ago)
    session = AssessmentSession.objects.create(user=user, is_active=True)
    AssessmentSession.objects.filter(pk=session.pk).update(completed_at=when)
    session.refresh_from_db()
    return session


def test_no_nudge_for_a_user_who_never_completed_a_trial(seeded, user):
    status = forge.retest_status(user)
    assert status["retest_due"] is False
    assert status["last_trial_at"] is None
    assert status["days_since_last_trial"] is None


def test_no_nudge_before_thirty_days(seeded, user):
    _completed_trial(user, days_ago=29)
    status = forge.retest_status(user)
    assert status["retest_due"] is False
    assert status["days_since_last_trial"] == 29


def test_nudge_at_thirty_days(seeded, user):
    _completed_trial(user, days_ago=30)
    status = forge.retest_status(user)
    assert status["retest_due"] is True
    assert status["days_since_last_trial"] == 30


def test_incomplete_session_does_not_reset_the_clock(seeded, user):
    """An abandoned retake leaves an active, uncompleted session — the nudge must
    keep firing off the last *completed* Trial."""
    _completed_trial(user, days_ago=45)
    AssessmentSession.objects.create(user=user, is_active=True)  # completed_at=None

    status = forge.retest_status(user)
    assert status["retest_due"] is True
    assert status["days_since_last_trial"] == 45


def test_dismissal_suppresses_for_three_days_then_returns(seeded, user):
    _completed_trial(user, days_ago=40)
    forge.dismiss_retest_prompt(user)
    assert forge.retest_status(user)["retest_due"] is False

    # Wind the dismissal back past the window — still due, so it returns.
    profile = forge.get_or_create_equipment_profile(user)
    profile.retest_prompt_dismissed_at = timezone.now() - timedelta(days=4)
    profile.save(update_fields=["retest_prompt_dismissed_at"])
    assert forge.retest_status(user)["retest_due"] is True


def test_starting_a_retake_suppresses_the_nudge(seeded, user):
    _completed_trial(user, days_ago=40)
    assert forge.retest_status(user)["retest_due"] is True

    forge.retake_assessment(user)
    assert forge.retest_status(user)["retest_due"] is False


def test_dismiss_endpoint(seeded, client, user):
    _completed_trial(user, days_ago=40)
    resp = client.post("/cauldron/api/assessment/reminder/dismiss/")
    assert resp.status_code == 200
    assert resp.json()["retest_due"] is False


def test_today_payload_carries_the_nudge(seeded, client, user):
    _own(user, "bodyweight", "pullup_bar")
    client.post("/cauldron/api/assessment/", _trial_payload(user), format="json")
    # Age the trial the program was just built from.
    AssessmentSession.objects.filter(user=user).update(
        completed_at=timezone.now() - timedelta(days=31)
    )
    # Submitting a Trial routes through retake_assessment, which stamps a
    # dismissal (per spec: starting a retake acts on the nudge). In production
    # that 3-day suppression has long expired by the time the 30-day clock is
    # up; here the clock is fast-forwarded, so clear it to match real timing.
    profile = forge.get_or_create_equipment_profile(user)
    profile.retest_prompt_dismissed_at = None
    profile.save(update_fields=["retest_prompt_dismissed_at"])

    resp = client.get("/cauldron/api/today/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["retest_due"] is True
    assert body["days_since_last_trial"] == 31
    assert "set_logs" in body  # the plan payload is still intact


def test_retake_rebuilds_from_scratch(seeded, client, user):
    """A retest is a full reassessment: previous session and program deactivate."""
    _own(user, "bodyweight", "pullup_bar")
    client.post("/cauldron/api/assessment/", _trial_payload(user), format="json")
    first = AssessmentSession.objects.get(user=user)

    resp = client.post("/cauldron/api/assessment/retake/")
    assert resp.status_code == 201

    first.refresh_from_db()
    assert first.is_active is False
    assert user.forge_programs.filter(is_active=True).count() == 0


# ── Trial history ────────────────────────────────────────────────────────────


def _history_point(user, pattern_key, tested, placed, reps, when):
    session = AssessmentSession.objects.create(user=user, is_active=False)
    AssessmentSession.objects.filter(pk=session.pk).update(completed_at=when)
    return AssessmentResult.objects.create(
        session=session,
        pattern=MovementPattern.objects.get(key=pattern_key),
        tested_exercise=tested,
        placed_exercise=placed,
        reps_or_seconds=reps,
    )


def test_history_is_empty_with_no_trials(seeded, client):
    resp = client.get("/cauldron/api/assessment/history/")
    assert resp.status_code == 200
    assert resp.json() == {"patterns": []}


def test_history_of_one_trial_reports_no_verdict(seeded, client, user):
    bridge = Exercise.objects.get(name="Single-Leg Glute Bridge")
    _history_point(
        user, "hinge", bridge, bridge, 8, timezone.now() - timedelta(days=10)
    )
    points = client.get("/cauldron/api/assessment/history/").json()["patterns"][0]["points"]
    assert len(points) == 1
    assert points[0]["delta_vs_prev"] is None
    assert points[0]["verdict"] == "none"
    assert "asymmetry_pct" not in points[0]


def test_a_rung_promotion_is_progress_not_a_setback(seeded, client, user):
    """The whole point of ladder_score: reps reset on promotion, but the user
    moved UP. Raw-rep comparison would call 12→4 a setback."""
    glute = Exercise.objects.get(name="Glute Bridge")            # rank 1
    bridge = Exercise.objects.get(name="Single-Leg Glute Bridge")  # rank 2
    assert bridge.difficulty_rank > glute.difficulty_rank

    _history_point(user, "hinge", glute, glute, 12, timezone.now() - timedelta(days=60))
    _history_point(user, "hinge", bridge, bridge, 4, timezone.now() - timedelta(days=10))

    points = client.get("/cauldron/api/assessment/history/").json()["patterns"][0]["points"]
    assert [p["reps_or_seconds"] for p in points] == [12, 4]  # raw reps went DOWN
    assert points[1]["verdict"] == "progress"
    assert points[1]["delta_vs_prev"] > 0


def test_fewer_reps_on_the_same_rung_is_a_setback(seeded, client, user):
    bridge = Exercise.objects.get(name="Single-Leg Glute Bridge")
    _history_point(user, "hinge", bridge, bridge, 10, timezone.now() - timedelta(days=60))
    _history_point(user, "hinge", bridge, bridge, 6, timezone.now() - timedelta(days=10))

    points = client.get("/cauldron/api/assessment/history/").json()["patterns"][0]["points"]
    assert points[1]["verdict"] == "setback"


def test_identical_results_report_no_change(seeded, client, user):
    bridge = Exercise.objects.get(name="Single-Leg Glute Bridge")
    _history_point(user, "hinge", bridge, bridge, 9, timezone.now() - timedelta(days=60))
    _history_point(user, "hinge", bridge, bridge, 9, timezone.now() - timedelta(days=10))

    points = client.get("/cauldron/api/assessment/history/").json()["patterns"][0]["points"]
    assert points[1]["verdict"] == "no change"
    assert points[1]["delta_vs_prev"] == 0


def test_timed_anchors_are_flagged_as_seconds(seeded, client, user):
    """The comparison view must never print 'reps' for a Plank."""
    plank = Exercise.objects.get(name="Plank")
    _history_point(
        user, "core_anti_extension", plank, plank, 45, timezone.now() - timedelta(days=5)
    )
    series = client.get("/cauldron/api/assessment/history/").json()["patterns"][0]
    assert series["points"][0]["is_timed"] is True


def test_incomplete_sessions_are_excluded_from_history(seeded, client, user):
    """An open retake has no results yet and must not plot as a phantom point."""
    bridge = Exercise.objects.get(name="Single-Leg Glute Bridge")
    _history_point(user, "hinge", bridge, bridge, 8, timezone.now() - timedelta(days=5))
    open_session = AssessmentSession.objects.create(user=user, is_active=True)
    AssessmentResult.objects.create(
        session=open_session,
        pattern=MovementPattern.objects.get(key="hinge"),
        tested_exercise=bridge,
        placed_exercise=bridge,
        reps_or_seconds=0,
    )

    points = client.get("/cauldron/api/assessment/history/").json()["patterns"][0]["points"]
    assert len(points) == 1


def test_history_is_scoped_to_the_requesting_user(seeded, client, user):
    # A distinct email is required: this project enforces UNIQUE(lower(email)),
    # so a second blank-email user collides with the fixture user.
    other = User.objects.create_user(
        username="intruder", password="pw12345!", email="intruder@example.test"
    )
    bridge = Exercise.objects.get(name="Single-Leg Glute Bridge")
    _history_point(other, "hinge", bridge, bridge, 8, timezone.now() - timedelta(days=5))

    assert client.get("/cauldron/api/assessment/history/").json() == {"patterns": []}


def test_history_requires_authentication(seeded):
    assert APIClient().get("/cauldron/api/assessment/history/").status_code in (401, 403)


def test_dismiss_requires_authentication(seeded):
    assert (
        APIClient().post("/cauldron/api/assessment/reminder/dismiss/").status_code
        in (401, 403)
    )

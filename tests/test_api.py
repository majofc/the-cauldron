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
    PrescribedExercise,
    Program,
    ProgramDay,
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


def _plan(client):
    """The computed plan for today. Reading it writes nothing."""
    resp = client.get("/cauldron/api/today/")
    assert resp.status_code == 200
    return resp.json()


def _open_today(client):
    """The plan, persisted — what the client does on the first logged value.

    Opening Today no longer creates a session, so any test that needs something
    to log against has to post the plan snapshot the way the browser does.
    """
    plan = _plan(client)
    created = client.post(
        "/cauldron/api/sessions/",
        {
            "sets": [
                {
                    "exercise": s["exercise"],
                    "prescription": s["prescription"],
                    "set_index": s["set_index"],
                }
                for s in plan["set_logs"]
            ]
        },
        format="json",
    )
    assert created.status_code == 201
    return created.json()


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
    # One day, carrying every pattern the Trial placed — whatever the split.
    assert len(program["days"]) == 1
    day = program["days"][0]
    assert day["day_index"] == 0
    assert len(day["prescriptions"]) == MovementPattern.objects.count() == 6


def test_program_is_one_day_whatever_split_is_requested(seeded, client, user):
    """The split no longer shapes the plan. An older client still sending one
    forges the same single all-patterns day."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    payload = dict(_assessment_payload(), split="upper_lower_4x")
    resp = client.post("/cauldron/api/assessment/", payload, format="json")
    assert resp.status_code == 201
    assert len(resp.json()["program"]["days"]) == 1
    # The field still resolves for historical rows; it just isn't taken from the
    # request any more.
    assert Program.objects.get(user=user, is_active=True).split == "full_body_3x"


# ── Today snapshot + log → next prescription ─────────────────────────────────


def test_today_snapshots_then_log_advances(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"], dumbbell_weights=[])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
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


# ── Ephemeral plan: nothing is written until a value is logged ───────────────


def test_opening_today_writes_nothing(seeded, client, user):
    """The whole point of the ephemeral plan: reading Today leaves no debris."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    before = WorkoutSession.objects.count()
    logs_before = SetLog.objects.count()

    for _ in range(3):
        resp = client.get("/cauldron/api/today/")
        assert resp.status_code == 200
        assert resp.json()["persisted"] is False
        assert resp.json()["uuid"] is None

    assert WorkoutSession.objects.count() == before
    assert SetLog.objects.count() == logs_before


def test_plan_sets_carry_synthetic_ids(seeded, client):
    """Planned sets are keyed ``<prescription-uuid>:<set-index>`` — there is no
    row behind them yet."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    plan = _plan(client)
    assert plan["set_logs"]
    for s in plan["set_logs"]:
        assert s["uuid"] == f"{s['prescription']}:{s['set_index']}"


def test_first_value_creates_the_session_and_persists_it(seeded, client, user):
    """Posting the plan snapshot writes the session; the value then patches it."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
    assert session["persisted"] is True
    assert WorkoutSession.objects.filter(user=user).count() == 1
    assert session["status"] == "planned"

    first = session["set_logs"][0]
    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/sets/",
        {"sets": {first["uuid"]: {"actual_reps": 7}}},
        format="json",
    )
    assert resp.status_code == 200
    stored = SetLog.objects.get(uuid=first["uuid"])
    assert stored.actual_reps == 7
    # An incremental save must not finish the session or run progression.
    stored.session.refresh_from_db()
    assert stored.session.status == "planned"
    assert stored.session.performed_at is None


def test_creating_a_session_twice_reuses_the_open_one(seeded, client, user):
    """Two inputs racing the first keystroke must not leave two sessions."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    first = _open_today(client)
    plan_again = client.post("/cauldron/api/sessions/", {"sets": []}, format="json")
    assert plan_again.status_code == 200
    assert plan_again.json()["uuid"] == first["uuid"]
    assert WorkoutSession.objects.filter(user=user).count() == 1


def test_targets_come_from_the_prescription_not_the_request(seeded, client, user):
    """The client chooses which movements are on the day, never what they cost."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    plan = _plan(client)
    row = plan["set_logs"][0]
    resp = client.post(
        "/cauldron/api/sessions/",
        {
            "sets": [
                {
                    "exercise": row["exercise"],
                    "prescription": row["prescription"],
                    "set_index": 0,
                    "expected_reps": 9999,
                    "expected_load": 9999,
                }
            ]
        },
        format="json",
    )
    assert resp.status_code == 201
    presc = PrescribedExercise.objects.get(uuid=row["prescription"])
    for s in resp.json()["set_logs"]:
        assert s["expected_reps"] in (presc.target_reps_min, presc.target_reps_max)
        assert s["expected_load"] == presc.target_load


def test_plan_cannot_pair_a_prescription_with_an_unrelated_movement(seeded, client, user):
    """A prescription may only be logged against its own rung (or a grip variant
    of it) — otherwise the client could take a hard movement's targets and put an
    easy movement's name on them."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    plan = _plan(client)
    row = plan["set_logs"][0]
    presc = PrescribedExercise.objects.get(uuid=row["prescription"])
    unrelated = Exercise.objects.exclude(pattern_id=presc.exercise.pattern_id).first()

    resp = client.post(
        "/cauldron/api/sessions/",
        {"sets": [{"exercise": str(unrelated.uuid),
                   "prescription": row["prescription"], "set_index": 0}]},
        format="json",
    )
    assert resp.status_code == 400
    assert WorkoutSession.objects.filter(user=user).count() == 0


def test_plan_cannot_swap_in_a_movement_the_user_cannot_perform(seeded, client, user):
    """A prescription-less block is a swap, and gets the swap endpoint's gate."""
    _set_equipment(client, ["bodyweight"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    barbell_move = Exercise.objects.filter(required_equipment__key="barbell").first()

    resp = client.post(
        "/cauldron/api/sessions/",
        {"sets": [{"exercise": str(barbell_move.uuid), "prescription": None, "set_index": 0}]},
        format="json",
    )
    assert resp.status_code == 400
    assert WorkoutSession.objects.filter(user=user).count() == 0


def test_a_grip_variant_of_the_prescribed_rung_is_accepted(seeded, client, user):
    """The plan legitimately carries whichever grip the Forge scheduled today."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    plan = _plan(client)
    swapped = None
    for s in plan["set_logs"]:
        presc = PrescribedExercise.objects.get(uuid=s["prescription"])
        sibling = (
            Exercise.objects.filter(
                pattern_id=presc.exercise.pattern_id,
                difficulty_rank=presc.exercise.difficulty_rank,
            )
            .exclude(pk=presc.exercise_id)
            .first()
        )
        if sibling is not None:
            swapped = (s, sibling)
            break
    if swapped is None:
        pytest.skip("seed catalogue has no grip-split rung on this day")

    row, sibling = swapped
    resp = client.post(
        "/cauldron/api/sessions/",
        {"sets": [{"exercise": str(sibling.uuid),
                   "prescription": row["prescription"], "set_index": 0}]},
        format="json",
    )
    assert resp.status_code == 201
    assert {s["exercise_name"] for s in resp.json()["set_logs"]} == {sibling.name}


def test_plan_cannot_borrow_another_users_prescription(seeded, client, user):
    """A prescription uuid from someone else's program is rejected outright."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    plan = _plan(client)

    intruder = User.objects.create_user(
        username="intruder", email="intruder@example.com", password="pw12345!"
    )
    other = APIClient()
    other.force_authenticate(user=intruder)
    _set_equipment(other, ["bodyweight", "pullup_bar"])
    other.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    resp = other.post(
        "/cauldron/api/sessions/",
        {"sets": [{"exercise": plan["set_logs"][0]["exercise"],
                   "prescription": plan["set_logs"][0]["prescription"],
                   "set_index": 0}]},
        format="json",
    )
    assert resp.status_code == 400
    assert WorkoutSession.objects.filter(user=intruder).count() == 0


def test_plan_with_junk_ids_is_a_bad_request_not_a_crash(seeded, client, user):
    """Malformed ids must not reach a uuid lookup."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    plan = _plan(client)
    row = plan["set_logs"][0]

    # Nothing resolvable at all → empty plan.
    resp = client.post(
        "/cauldron/api/sessions/",
        {"sets": [{"exercise": "not-a-uuid", "prescription": None, "set_index": 0}]},
        format="json",
    )
    assert resp.status_code == 400

    # A garbled prescription must not quietly become a free swap.
    resp2 = client.post(
        "/cauldron/api/sessions/",
        {"sets": [{"exercise": row["exercise"], "prescription": "nope", "set_index": 0}]},
        format="json",
    )
    assert resp2.status_code == 400
    assert WorkoutSession.objects.filter(user=user).count() == 0


def test_reopening_today_resumes_the_unfinished_session(seeded, client, user):
    """A reload must come back with the work in progress, not a fresh plan."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
    first = session["set_logs"][0]
    client.post(
        f"/cauldron/api/sessions/{session['uuid']}/sets/",
        {"sets": {first["uuid"]: {"actual_reps": 11}}},
        format="json",
    )

    resumed = client.get("/cauldron/api/today/")
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["persisted"] is True
    assert body["uuid"] == session["uuid"]
    assert next(s for s in body["set_logs"] if s["uuid"] == first["uuid"])["actual_reps"] == 11
    assert WorkoutSession.objects.filter(user=user).count() == 1


def test_resume_returns_swapped_exercises_and_their_values(seeded, client, user):
    """The resume payload has to carry both halves of the work in progress: the
    exercises a ⇄ swap put there, and the reps already logged against them."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
    body = _candidates(client)
    on_screen = {s["exercise"] for s in session["set_logs"]}
    taken = {body["chains"][e] for e in on_screen if e in body["chains"]}
    candidate = next(c for c in body["candidates"] if c["chain_key"] not in taken)
    victim = session["set_logs"][0]

    swapped = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/swap/",
        {"from_exercise": victim["exercise"], "to_exercise": candidate["exercise"]},
        format="json",
    ).json()
    target = next(
        s for s in swapped["set_logs"] if s["exercise"] == candidate["exercise"]
    )
    client.post(
        f"/cauldron/api/sessions/{session['uuid']}/sets/",
        {"sets": {target["uuid"]: {"actual_reps": 13}}},
        format="json",
    )

    resumed = client.get("/cauldron/api/today/").json()
    names = {s["exercise"] for s in resumed["set_logs"]}
    assert candidate["exercise"] in names
    assert victim["exercise"] not in names
    restored = next(s for s in resumed["set_logs"] if s["uuid"] == target["uuid"])
    assert restored["actual_reps"] == 13


def test_a_completed_session_does_not_block_a_new_plan(seeded, client, user):
    """Only *unfinished* sessions resume — after Save, Today plans again."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
    client.post(
        f"/cauldron/api/sessions/{session['uuid']}/log/",
        {"sets": {session["set_logs"][0]["uuid"]: {"actual_reps": 5}}},
        format="json",
    )
    assert client.get("/cauldron/api/today/").json()["persisted"] is False


def test_sets_endpoint_refuses_a_finalized_session(seeded, client):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _open_today(client)
    client.post(
        f"/cauldron/api/sessions/{session['uuid']}/log/",
        {"sets": {session["set_logs"][0]["uuid"]: {"actual_reps": 5}}},
        format="json",
    )
    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/sets/",
        {"sets": {session["set_logs"][0]["uuid"]: {"actual_reps": 9}}},
        format="json",
    )
    assert resp.status_code == 409


# ── ⇄ Change ────────────────────────────────────────────────────────────────


def _candidates(client):
    resp = client.get("/cauldron/api/today/swap-candidates/")
    assert resp.status_code == 200
    return resp.json()


def test_swap_candidates_are_ranked_least_trained_first(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    hot = day0.prescriptions.select_related("exercise").first()
    for n in range(2):
        _log_session(user, day0, [hot.exercise], timezone.now() - timedelta(hours=n + 1))

    body = _candidates(client)
    counts = [c["event_count"] for c in body["candidates"]]
    assert counts == sorted(counts)
    # The chain that was trained twice is reported as such, not as never-used.
    chain = body["chains"][str(hot.exercise.uuid)]
    assert next(c for c in body["candidates"] if c["chain_key"] == chain)["event_count"] == 2


def test_swap_candidates_exclude_unowned_and_blocked(seeded, client, user):
    """Equipment the user lacks, and movements they blocked, are never offered."""
    _set_equipment(client, ["bodyweight"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    pushup = Exercise.objects.get(name="Push-up")
    client.post(f"/cauldron/api/exercises/{pushup.uuid}/block/", {}, format="json")

    names = {c["exercise_name"] for c in _candidates(client)["candidates"]}
    assert "Push-up" not in names
    assert "Barbell Bench Press" not in names  # no barbell owned


def test_swap_candidate_carries_its_own_targets(seeded, client):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    for c in _candidates(client)["candidates"]:
        exercise = Exercise.objects.get(uuid=c["exercise"])
        assert c["target_sets"] == forge.DEFAULT_TARGET_SETS
        assert c["target_rest_seconds"] == exercise.rest_seconds
        assert c["pattern_key"] == exercise.pattern.key


def test_swap_replaces_the_movement_with_the_new_ones_targets(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
    on_screen = {s["exercise"] for s in session["set_logs"]}
    body = _candidates(client)
    taken = {body["chains"][e] for e in on_screen if e in body["chains"]}
    candidate = next(c for c in body["candidates"] if c["chain_key"] not in taken)
    victim = session["set_logs"][0]

    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/swap/",
        {"from_exercise": victim["exercise"], "to_exercise": candidate["exercise"]},
        format="json",
    )
    assert resp.status_code == 200
    swapped = [s for s in resp.json()["set_logs"] if s["exercise"] == candidate["exercise"]]
    assert len(swapped) == candidate["target_sets"]
    # Nothing is inherited from the movement it replaced.
    assert swapped[-1]["expected_reps"] == candidate["target_reps_max"]
    assert all(s["rest_seconds"] == candidate["target_rest_seconds"] for s in swapped)
    # No prescription behind it, so the progression engine will skip it.
    assert all(s["prescription"] is None for s in swapped)
    assert not SetLog.objects.filter(
        session__uuid=session["uuid"], exercise__uuid=victim["exercise"]
    ).exists()


def test_swapped_in_sets_earn_no_progression_but_still_count(seeded, client, user):
    """A swapped-in movement never advances a prescription, but its work shows up
    in the muscle map, the volume analytics and the chain counts."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
    body = _candidates(client)
    on_screen = {s["exercise"] for s in session["set_logs"]}
    taken = {body["chains"][e] for e in on_screen if e in body["chains"]}
    candidate = next(c for c in body["candidates"] if c["chain_key"] not in taken)
    victim = session["set_logs"][0]
    swapped_session = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/swap/",
        {"from_exercise": victim["exercise"], "to_exercise": candidate["exercise"]},
        format="json",
    ).json()

    prescriptions_before = {
        str(p.uuid): (p.exercise_id, p.sessions_at_top, p.pending_progression_id)
        for p in PrescribedExercise.objects.filter(day__program__user=user)
    }

    # Smash the AMRAP on the swapped-in move — enough to progress, if it could.
    sets = {}
    for s in swapped_session["set_logs"]:
        sets[s["uuid"]] = {"actual_reps": (s["expected_reps"] or 5) + 10}
    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/log/", {"sets": sets}, format="json"
    )
    assert resp.status_code == 200

    swapped_ids = [
        s["uuid"] for s in swapped_session["set_logs"]
        if s["exercise"] == candidate["exercise"]
    ]
    for uuid in swapped_ids:
        assert SetLog.objects.get(uuid=uuid).prescribed_exercise_id is None
    # No prescription moved *because of* the swapped-in movement: it has none.
    for p in PrescribedExercise.objects.filter(day__program__user=user):
        before = prescriptions_before[str(p.uuid)]
        if str(p.uuid) not in {
            s["prescription"] for s in swapped_session["set_logs"] if s["prescription"]
        }:
            assert (p.exercise_id, p.sessions_at_top, p.pending_progression_id) == before

    # …but the work is real: it counts toward its chain.
    swapped_exercise = Exercise.objects.get(uuid=candidate["exercise"])
    assert _counts_for(user, [swapped_exercise])[swapped_exercise] == 1
    # …and appears in the volume analytics.
    progress = client.get("/cauldron/api/progress/").json()
    assert swapped_exercise.rung_label in progress["exercises"]


def test_swap_refuses_when_the_movement_already_has_values(seeded, client, user):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    session = _open_today(client)
    victim = session["set_logs"][0]
    client.post(
        f"/cauldron/api/sessions/{session['uuid']}/sets/",
        {"sets": {victim["uuid"]: {"actual_reps": 6}}},
        format="json",
    )

    body = _candidates(client)
    on_screen = {s["exercise"] for s in session["set_logs"]}
    taken = {body["chains"][e] for e in on_screen if e in body["chains"]}
    candidate = next(c for c in body["candidates"] if c["chain_key"] not in taken)

    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/swap/",
        {"from_exercise": victim["exercise"], "to_exercise": candidate["exercise"]},
        format="json",
    )
    assert resp.status_code == 409
    assert SetLog.objects.get(uuid=victim["uuid"]).actual_reps == 6


def test_swap_rejects_a_movement_the_user_cannot_perform(seeded, client, user):
    _set_equipment(client, ["bodyweight"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _open_today(client)

    barbell_move = Exercise.objects.filter(
        required_equipment__key="barbell"
    ).first()
    assert barbell_move is not None
    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/swap/",
        {
            "from_exercise": session["set_logs"][0]["exercise"],
            "to_exercise": str(barbell_move.uuid),
        },
        format="json",
    )
    assert resp.status_code == 400


def test_swap_rejects_junk_uuids(seeded, client):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _open_today(client)
    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/swap/",
        {"from_exercise": "not-a-uuid", "to_exercise": "also-not"},
        format="json",
    )
    assert resp.status_code == 400


def test_swap_belongs_to_its_owner(seeded, client, user):
    """Another user's session is invisible, so its sets cannot be swapped."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _open_today(client)

    intruder = User.objects.create_user(
        username="thief", email="thief@example.com", password="pw12345!"
    )
    other = APIClient()
    other.force_authenticate(user=intruder)
    resp = other.post(
        f"/cauldron/api/sessions/{session['uuid']}/swap/",
        {
            "from_exercise": session["set_logs"][0]["exercise"],
            "to_exercise": session["set_logs"][0]["exercise"],
        },
        format="json",
    )
    assert resp.status_code == 404


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

    presc = PrescribedExercise.objects.filter(
        day__program__user=user,
        day__program__is_active=True,
        pattern__key="lower_unilateral",
    ).select_related("day").first()
    assert presc is not None, "seed should place a lower_unilateral exercise"

    # The day trains only the least-used chains, so give every *other* pattern an
    # event: the untouched unilateral chain is then guaranteed to be on the day.
    day0 = presc.day
    for other in day0.prescriptions.exclude(pk=presc.pk).select_related("exercise"):
        _log_session(user, day0, [other.exercise], timezone.now())

    session = _open_today(client)

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


# ── Least-used selection for the day ─────────────────────────────────────────


def _log_amrap(user, day, exercise, reps, when):
    """Record a completed session with a single AMRAP set for ``exercise``.

    ``performed_at`` is what the event window filters on, so pass ``when`` to
    place the session inside or outside the window."""
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


def _log_session(user, day, exercises, when, reps=8, sets=1):
    """One completed session on ``day`` logging ``sets`` sets of each exercise."""
    session = WorkoutSession.objects.create(
        user=user,
        program_day=day,
        performed_at=when,
        status=WorkoutSession.Status.COMPLETED,
    )
    for exercise in exercises:
        for index in range(sets):
            SetLog.objects.create(
                session=session,
                exercise=exercise,
                set_index=index,
                actual_reps=reps,
            )
    return session


def _counts_for(user, exercises):
    """``{exercise: event count}`` through the chain map, for readable asserts."""
    chain_of = forge.chain_map_for({e.pattern_id for e in exercises})
    counts = forge.chain_event_counts(user, chain_of)
    return {e: counts.get(chain_of.get(e.pk), 0) for e in exercises}


def _active_program(user):
    program = Program.objects.get(user=user, is_active=True)
    return program


def test_day_keeps_least_used_and_drops_most_used(seeded, client, user):
    """The day trains the 5 chains with the fewest recent usage events.
    A never-used chain is kept; the most-used one is dropped."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    prescs = list(day0.prescriptions.select_related("exercise", "pattern"))
    assert len(prescs) == 6  # full body trains all six patterns

    # Give five chains an increasing number of events; leave the sixth untouched.
    used, never_used = prescs[:5], prescs[5]
    now = timezone.now()
    for events, presc in enumerate(used, start=1):
        for n in range(events):
            _log_session(user, day0, [presc.exercise], now - timedelta(hours=n + 1))

    selected = forge.select_day_prescriptions(user, day0)
    keys = {p.pattern.key for p in selected}

    assert len(selected) == forge.DAILY_PATTERN_COUNT == 5
    # The most-used chain (5 events) is dropped …
    assert used[-1].pattern.key not in keys
    # … while the never-used chain survives (zero events).
    assert never_used.pattern.key in keys
    # Selection preserves the day's display order (it filters, it doesn't reorder).
    assert [p.order for p in selected] == sorted(p.order for p in selected)


def test_all_zero_counts_tie_break_on_order(seeded, client, user):
    """With no history every chain ties at zero, so the day's order decides."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    selected = forge.select_day_prescriptions(user, day0)

    by_order = sorted(day0.prescriptions.all(), key=lambda p: p.order)
    assert [p.pk for p in selected] == [p.pk for p in by_order[:5]]


def test_events_count_distinct_sessions_not_sets(seeded, client, user):
    """Four sets in one session are one event, not four."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    presc = day0.prescriptions.select_related("exercise").first()

    _log_session(user, day0, [presc.exercise], timezone.now(), sets=4)
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 1

    _log_session(user, day0, [presc.exercise], timezone.now() - timedelta(hours=2))
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 2


def test_events_follow_the_chain_across_a_rung_change(seeded, client, user):
    """Reps logged on one rung still count once the prescription advances."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    archer = Exercise.objects.get(name="Archer Push-up")
    typewriter = Exercise.objects.get(name="Typewriter Push-up")

    _log_session(user, day0, [archer], timezone.now())
    # The user climbs a rung; the earlier work still belongs to this chain.
    assert _counts_for(user, [typewriter])[typewriter] == 1


def test_grip_siblings_share_one_count(seeded, client, user):
    """Pull-up and Chin-up are the same ladder position → the same chain."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    pullup = Exercise.objects.get(name="Pull-up")
    chinup = Exercise.objects.get(name="Chin-up")

    chain_of = forge.chain_map_for({pullup.pattern_id})
    assert chain_of[pullup.pk] == chain_of[chinup.pk]

    # Alternating grips across two sessions must total 2, not 1 each.
    _log_session(user, day0, [pullup], timezone.now() - timedelta(hours=2))
    _log_session(user, day0, [chinup], timezone.now())
    assert _counts_for(user, [pullup])[pullup] == 2


def test_bodyweight_and_loaded_chains_are_counted_separately(seeded, client, user):
    """A pattern's two ladders are separate connected components."""
    _set_equipment(client, ["bodyweight", "pullup_bar", "dumbbells", "bench"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    pushup = Exercise.objects.get(name="Push-up")
    db_press = Exercise.objects.get(name="Dumbbell Bench Press")

    chain_of = forge.chain_map_for({pushup.pattern_id})
    assert chain_of[pushup.pk] != chain_of[db_press.pk]

    _log_session(user, day0, [db_press], timezone.now())
    counts = _counts_for(user, [pushup, db_press])
    assert counts[db_press] == 1
    assert counts[pushup] == 0


def test_least_used_filter_applies_to_the_served_plan(seeded, client, user):
    """Opening Today serves only the least-used subset."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    hot = day0.prescriptions.select_related("exercise", "pattern").first()
    now = timezone.now()
    for n in range(3):
        _log_session(user, day0, [hot.exercise], now - timedelta(hours=n + 1))

    session = _plan(client)
    # One exercise per pattern on a day, so distinct exercises == distinct
    # patterns trained.
    exercises = {sl["exercise_name"] for sl in session["set_logs"]}
    assert len(exercises) == 5
    assert hot.exercise.name not in exercises


def test_least_used_filter_applies_regardless_of_split(seeded, client, user):
    """The filter is split-agnostic — an upper/lower program written before the
    single-day change is filtered the same way."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    program = _active_program(user)
    program.split = Program.Split.UPPER_LOWER_4X
    program.save(update_fields=["split"])

    day0 = program.days.get(day_index=0)
    assert day0.prescriptions.count() == 6
    assert len(forge.select_day_prescriptions(user, day0)) == forge.DAILY_PATTERN_COUNT


def test_events_outside_the_window_do_not_count(seeded, client, user):
    """Sessions completed more than CHAIN_EVENT_WINDOW_DAYS ago are ignored."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    presc = day0.prescriptions.select_related("exercise").first()

    stale = timezone.now() - timedelta(days=forge.CHAIN_EVENT_WINDOW_DAYS + 1)
    _log_session(user, day0, [presc.exercise], stale)
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 0

    fresh = timezone.now() - timedelta(days=1)
    _log_session(user, day0, [presc.exercise], fresh)
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 1


def test_events_from_any_session_count(seeded, client, user):
    """Every completed session counts, whatever day it hung off.

    Counting used to be restricted to Full Body A. With one day there is no
    subset left to restrict to — and work done on a chain the user swapped in
    has to weigh the same as work done on a prescribed one.
    """
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    program = _active_program(user)
    day0 = program.days.get(day_index=0)
    presc = day0.prescriptions.select_related("exercise").first()

    # A legacy extra day, and a session with no program day at all (what a
    # swapped-in movement's history looks like once its day is gone).
    legacy = ProgramDay.objects.create(program=program, day_index=1, name="Legacy")
    _log_session(user, legacy, [presc.exercise], timezone.now())
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 1

    _log_session(user, None, [presc.exercise], timezone.now() - timedelta(hours=2))
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 2


def test_unlogged_and_zero_rep_sets_do_not_count(seeded, client, user):
    """An opened-but-unlogged session, and null/0 reps, are not events."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    presc = day0.prescriptions.select_related("exercise").first()

    # Opened session: still planned, no performed_at.
    opened = WorkoutSession.objects.create(user=user, program_day=day0)
    SetLog.objects.create(
        session=opened, exercise=presc.exercise, set_index=0, actual_reps=None
    )
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 0

    # Completed session, but nothing actually performed.
    _log_session(user, day0, [presc.exercise], timezone.now(), reps=0)
    assert _counts_for(user, [presc.exercise])[presc.exercise] == 0


def test_chain_resolution_is_one_query_each(seeded, client, user, django_assert_num_queries):
    """Chain resolution never grows with the number of prescriptions."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    prescs = list(day0.prescriptions.select_related("exercise"))
    _log_session(user, day0, [p.exercise for p in prescs], timezone.now())

    pattern_ids = {p.exercise.pattern_id for p in prescs}
    with django_assert_num_queries(1):
        chain_of = forge.chain_map_for(pattern_ids)
    with django_assert_num_queries(1):
        forge.chain_event_counts(user, chain_of)


def test_timed_holds_count_on_seconds(seeded, client, user):
    """Holds store seconds in actual_reps, so >= 1 second is an event."""
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    day0 = _active_program(user).days.get(day_index=0)
    plank = Exercise.objects.get(name="Plank")
    assert plank.is_timed

    _log_session(user, day0, [plank], timezone.now(), reps=30)
    assert _counts_for(user, [plank])[plank] == 1


# ── Plate inventory: validation, recipes, unit switching ─────────────────────


def _plate_equipment(client, **extra):
    return _set_equipment(
        client,
        ["bodyweight", "dumbbells"],
        dumbbell_mode="plates",
        dumbbell_plates=[{"weight": 2.5, "count": 8}, {"weight": 1.25, "count": 4}],
        dumbbell_handle_weight=2.0,
        **extra,
    )


def test_invalid_plate_inventory_is_rejected_not_stored(seeded, client):
    resp = _set_equipment(client, ["bodyweight", "dumbbells"],
                          dumbbell_plates=[{"weight": -5, "count": 4}])
    assert resp.status_code == 400
    assert "dumbbell_plates" in resp.json()

    resp = _set_equipment(client, ["bodyweight", "dumbbells"],
                          dumbbell_plates=[{"weight": 2.5, "count": 0}])
    assert resp.status_code == 400

    resp = _set_equipment(client, ["bodyweight", "dumbbells"], dumbbell_weights="hello")
    assert resp.status_code == 400

    resp = _set_equipment(client, ["bodyweight", "dumbbells"], dumbbell_weights=[-5])
    assert resp.status_code == 400


def test_negative_bar_weight_is_rejected(seeded, client):
    resp = _set_equipment(client, ["bodyweight", "barbell"], bar_weight=-1)
    assert resp.status_code == 400


def test_orphan_plates_are_reported_back(seeded, client):
    resp = _set_equipment(client, ["bodyweight", "dumbbells"],
                          dumbbell_mode="plates",
                          dumbbell_plates=[{"weight": 2, "count": 6}])
    assert resp.status_code == 200
    assert resp.json()["orphan_plates"]["dumbbells"] == [{"weight": 2.0, "count": 2}]


def test_switching_load_unit_clears_the_inventory(seeded, client, user):
    assert _plate_equipment(client).status_code == 200
    resp = _set_equipment(client, ["bodyweight", "dumbbells"], load_unit="lb")
    assert resp.status_code == 200
    body = resp.json()
    # Denominations belong to their unit — they are dropped, never converted.
    assert body["dumbbell_plates"] == []
    assert body["dumbbell_handle_weight"] == 0
    assert body["load_unit"] == "lb"


def test_switching_unit_keeps_an_inventory_supplied_in_the_same_request(seeded, client):
    assert _plate_equipment(client).status_code == 200
    resp = _set_equipment(client, ["bodyweight", "dumbbells"], load_unit="lb",
                          dumbbell_plates=[{"weight": 5, "count": 8}])
    assert resp.status_code == 200
    assert resp.json()["dumbbell_plates"] == [{"weight": 5.0, "count": 8}]


def test_prescribed_load_is_buildable_and_carries_its_recipe(seeded, client):
    _plate_equipment(client)
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _plan(client)

    loaded = [s for s in session["set_logs"] if s["expected_load"] is not None]
    for s in loaded:
        # 8 x 2.5 and 4 x 1.25 on a 2 kg handle -> one bell weighs one of these.
        assert s["expected_load"] in [2.0, 4.5, 7.0, 9.5, 12.0, 14.5]
        assert s["load_unit"] == "kg"
        recipe = s["expected_load_recipe"]
        assert recipe is not None
        assert recipe["total"] == s["expected_load"]
        assert recipe["stacked"] is False


def test_user_logged_load_persists_without_moving_progression(seeded, client):
    _plate_equipment(client)
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _open_today(client)

    target = next((s for s in session["set_logs"]
                   if s["expected_load"] is not None and s["is_amrap"]), None)
    if target is None:
        pytest.skip("seed catalogue produced no load-mode AMRAP set")
    presc_before = SetLog.objects.get(uuid=target["uuid"]).prescribed_exercise
    load_before = presc_before.target_load

    # Log a weight that is NOT the prescribed one. The AMRAP set lands just
    # inside the range (its expected_reps IS the top, which would legitimately
    # progress) so any load movement could only have come from actual_load.
    set_results = {
        s["uuid"]: {"actual_reps": s["expected_reps"], "actual_load": s["expected_load"]}
        for s in session["set_logs"]
    }
    set_results[target["uuid"]] = {
        "actual_reps": max(1, target["expected_reps"] - 1), "actual_load": 99.5,
    }
    resp = client.post(f"/cauldron/api/sessions/{session['uuid']}/log/",
                       {"sets": set_results}, format="json")
    assert resp.status_code == 200

    stored = SetLog.objects.get(uuid=target["uuid"])
    assert stored.actual_load == 99.5          # recorded as history
    presc_before.refresh_from_db()
    assert presc_before.target_load == load_before  # engine unmoved


def test_negative_actual_load_is_not_persisted(seeded, client):
    _plate_equipment(client)
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _open_today(client)
    first = session["set_logs"][0]
    client.post(f"/cauldron/api/sessions/{session['uuid']}/log/",
                {"sets": {first["uuid"]: {"actual_reps": 5, "actual_load": -20}}},
                format="json")
    assert SetLog.objects.get(uuid=first["uuid"]).actual_load is None


def test_existing_fixed_dumbbell_users_keep_their_loads(seeded, client):
    # No mode sent at all - the migration default is `fixed`, so a pre-existing
    # weight list must behave exactly as before.
    _set_equipment(client, ["bodyweight", "dumbbells"], dumbbell_weights=[5, 10, 15])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    session = _plan(client)
    loaded = [s["expected_load"] for s in session["set_logs"] if s["expected_load"] is not None]
    assert loaded, "expected at least one load-mode set"
    assert all(l in (5.0, 10.0, 15.0) for l in loaded)


def test_shrinking_the_inventory_resnaps_a_stranded_load(seeded, client, user):
    _plate_equipment(client)
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    presc = PrescribedExercise.objects.filter(
        day__program__user=user, target_load__isnull=False
    ).first()
    if presc is None:
        pytest.skip("seed catalogue produced no load-mode prescription")
    presc.target_load = 14.5   # top of the old inventory
    presc.save(update_fields=["target_load"])

    # Sell the 1.25s and half the 2.5s: 14.5 is no longer assemblable.
    _set_equipment(client, ["bodyweight", "dumbbells"], dumbbell_mode="plates",
                   dumbbell_plates=[{"weight": 2.5, "count": 4}],
                   dumbbell_handle_weight=2.0)
    forge.sync_program_equipment(user)
    presc.refresh_from_db()
    assert presc.target_load == 7.0  # nearest load that can still be built

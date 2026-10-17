"""The Cauldron's MCP server (#89), phase 3: the Forge tools.

The key property: for the same inputs, the MCP path places the user, builds
the program and runs progression exactly like the website (both call
``services/forge.py``). Plus the unfinished-session rule, two-step edits,
``channel="mcp"`` and someone else's sessions → not found.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    AssessmentSession,
    MovementPattern,
    PrescribedExercise,
    SetLog,
    UserEquipmentProfile,
    WorkoutSession,
)
from the_cauldron.services import forge
from the_cauldron.tests.test_mcp_server import _err, _ok, _rpc, _token, _user  # noqa: F401

EQUIPMENT = ["bodyweight", "pullup_bar"]


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


def _athlete(name):
    user = _user(name, consent=False)
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(profile.equipment.model.objects.filter(key__in=EQUIPMENT))
    return user


@pytest.fixture
def ana(seeded):
    return _athlete("ana")


@pytest.fixture
def ben(seeded):
    return _athlete("ben")


def _web(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _trial_args(user, value=6):
    tests = _ok(_token(user), "get_trial")["tests"]
    return [{"pattern": t["pattern"], "value": value} for t in tests if t.get("exercise_id")]


def _web_trial(user, value=6):
    anchors = {p.key: a for p, a in forge.trial_anchors(user)}
    return _web(user).post("/cauldron/api/assessment/", {"results": [
        {"pattern_key": key, "tested_exercise": str(a.uuid), "reps_or_seconds": value}
        for key, a in anchors.items() if a is not None
    ]}, format="json")


def _placement(user):
    """Rung position per pattern — equal for same-rank rungs, which placement
    may order either way (a pre-existing tie, web and MCP alike)."""
    return sorted(
        (p.pattern.key, p.exercise.difficulty_rank, p.target_sets)
        for p in PrescribedExercise.objects.filter(day__program__user=user, day__program__is_active=True)
    )


def _program_signature(user):
    return sorted(
        (p.pattern.key, p.exercise.name, p.target_sets, p.target_reps_min, p.target_reps_max, p.target_load)
        for p in PrescribedExercise.objects.filter(day__program__user=user, day__program__is_active=True)
    )


def _confirmed(token, name, args=None):
    preview = _ok(token, name, args or {})
    assert preview["status"] == "confirmation_required"
    return _ok(token, name, {**(args or {}), "confirm_token": preview["confirm_token"]})


class TestTrial:
    def test_no_program_says_take_the_trial(self, ana):
        d = _ok(_token(ana), "get_forge_today")
        assert d["status"] == "no_program" and "get_trial" in d["next_step"]

    def test_get_trial_explains_each_test(self, ana):
        d = _ok(_token(ana), "get_trial")
        assert d["status"] == "none"
        assert {t["pattern"] for t in d["tests"]} == set(MovementPattern.objects.values_list("key", flat=True))
        push = next(t for t in d["tests"] if t["pattern"] == "horizontal_push")
        assert push["exercise"] and push["how_to_count"].startswith("Do as many reps")
        timed = [t for t in d["tests"] if t.get("timed")]
        assert timed and all("seconds" in t["how_to_count"] for t in timed)

    def test_same_placement_and_program_as_the_web(self, ana, ben):
        assert _web_trial(ana).status_code == 201
        d = _ok(_token(ben), "submit_trial_results", {"results": _trial_args(ben)})
        assert d["program_ready"] is True and len(d["placements"]) == MovementPattern.objects.count()
        assert _placement(ben) == _placement(ana)
        assert AssessmentSession.objects.get(user=ben, is_active=True).channel == "mcp"
        assert AssessmentSession.objects.get(user=ana, is_active=True).channel == "web"
        assert _ok(_token(ben), "get_trial")["status"] == "done"

    def test_bad_trial_input(self, ana):
        token = _token(ana)
        assert _err(token, "submit_trial_results", {"results": [{"pattern": "nope", "value": 3}]})["code"] == "invalid_arguments"
        assert _err(token, "submit_trial_results", {"results": [{"pattern": "hinge", "value": -1}]})["code"] == "invalid_arguments"
        assert not AssessmentSession.objects.filter(user=ana, completed_at__isnull=False).exists()

    def test_retake_is_two_step(self, ana):
        token = _token(ana)
        _ok(token, "submit_trial_results", {"results": _trial_args(ana)})
        preview = _ok(token, "retake_trial")
        assert "program stops" in preview["preview"]["effect"]
        assert forge.active_day(ana) is not None  # nothing changed yet
        done = _ok(token, "retake_trial", {"confirm_token": preview["confirm_token"]})
        assert done["trial"]["status"] == "in_progress" and forge.active_day(ana) is None


class TestEquipment:
    def test_read_and_two_step_change(self, ana):
        token = _token(ana)
        d = _ok(token, "get_equipment")
        assert set(d["profile"]["equipment"]) == set(EQUIPMENT)
        assert "dumbbells" in {e["key"] for e in d["available_equipment"]}
        preview = _ok(token, "set_equipment", {"equipment": EQUIPMENT + ["dumbbells"], "dumbbell_weights": [5, 10]})
        assert preview["preview"]["changes"]["dumbbell_weights"] == {"from": [], "to": [5, 10]}
        assert "dumbbells" not in set(UserEquipmentProfile.objects.get(user=ana).equipment.values_list("key", flat=True))
        _ok(token, "set_equipment", {"equipment": EQUIPMENT + ["dumbbells"], "dumbbell_weights": [5, 10],
                                     "confirm_token": preview["confirm_token"]})
        profile = UserEquipmentProfile.objects.get(user=ana)
        assert "dumbbells" in set(profile.equipment.values_list("key", flat=True))
        assert profile.configured_at is not None

    def test_invalid_equipment_is_refused(self, ana):
        err = _err(_token(ana), "set_equipment", {"equipment": ["teleporter"]})
        assert err["code"] == "invalid_input" and "equipment" in err["fields"]


def _trained(user):
    _ok(_token(user), "submit_trial_results", {"results": _trial_args(user)})


def _log_all(token, session, reps=12):
    for block in session["exercises"]:
        for s in block["sets"]:
            _ok(token, "log_forge_set", {"set_id": s["id"], "reps": reps})


class TestWorkout:
    def test_today_plan_writes_nothing(self, ana):
        _trained(ana)
        d = _ok(_token(ana), "get_forge_today")
        assert d["status"] == "not_started" and d["exercises"]
        assert all(s["id"] is None for b in d["exercises"] for s in b["sets"])
        assert not WorkoutSession.objects.filter(user=ana).exists()

    def test_start_log_finish_matches_the_web(self, ana, ben):
        for u in (ana, ben):
            _trained(u)
        # Web: ana logs the same values through the API.
        plan = _web(ana).get("/cauldron/api/today/").json()
        created = _web(ana).post("/cauldron/api/sessions/", {"sets": [
            {"exercise": s["exercise"], "prescription": s["prescription"], "set_index": s["set_index"]}
            for s in plan["set_logs"]
        ]}, format="json").json()
        web_log = _web(ana).post(f"/cauldron/api/sessions/{created['uuid']}/log/", {"sets": {
            s["uuid"]: {"actual_reps": 12} for s in created["set_logs"]
        }}, format="json").json()

        token = _token(ben)
        started = _ok(token, "start_forge_session")
        assert started["status"] == "in_progress"
        session = started["session"]
        assert WorkoutSession.objects.get(uuid=session["id"]).channel == "mcp"
        assert _ok(token, "start_forge_session")["session"]["id"] == session["id"]  # resumes, no duplicate
        _log_all(token, session)
        assert _ok(token, "get_forge_today")["status"] == "in_progress"
        finished = _ok(token, "finish_forge_session", {"session_id": session["id"]})
        assert finished["session"]["status"] == "completed"
        # Same movements, same progression messages, same resulting prescriptions.
        # (Same-rank rungs on an untrained ladder can be placed in either DB order —
        # a pre-existing tie in placement, web and MCP alike — so only the trained
        # movements are compared.)
        trained = {b["exercise"] for b in session["exercises"]}
        assert trained == {s["exercise_name"] for s in created["set_logs"]}
        assert sorted(map(str, finished["progression"])) == sorted(map(str, web_log["progression"]))

        def trained_rows(user):
            return [row for row in _program_signature(user) if row[1] in trained]

        assert trained_rows(ben) == trained_rows(ana)
        assert _ok(token, "get_forge_today")["status"] == "done"
        assert _err(token, "finish_forge_session", {"session_id": session["id"]})["code"] == "conflict"
        assert _err(token, "delete_forge_session", {"session_id": session["id"]})["code"] == "conflict"

    def test_logged_sets_change_only_with_confirmation(self, ana):
        _trained(ana)
        token = _token(ana)
        session = _ok(token, "start_forge_session")["session"]
        first = session["exercises"][0]["sets"][0]["id"]
        _ok(token, "log_forge_set", {"set_id": first, "reps": 8})
        err = _err(token, "log_forge_set", {"set_id": first, "reps": 9})
        assert err["code"] == "already_logged"
        _confirmed(token, "update_forge_set", {"set_id": first, "reps": 10, "load": 5})
        row = SetLog.objects.get(uuid=first)
        assert (row.actual_reps, row.actual_load) == (10, 5)

    def test_unfinished_past_session_gates_today(self, ana):
        _trained(ana)
        token = _token(ana)
        session = _ok(token, "start_forge_session")["session"]
        _ok(token, "log_forge_set", {"set_id": session["exercises"][0]["sets"][0]["id"], "reps": 8})
        WorkoutSession.objects.filter(uuid=session["id"]).update(scheduled_for=timezone.now().date() - timedelta(days=1))
        today = _ok(token, "get_forge_today")
        assert today["status"] == "unfinished_past_session" and today["session"]["id"] == session["id"]
        err = _err(token, "start_forge_session")
        assert err["code"] == "unfinished_session" and err["session"]["id"] == session["id"]
        assert _web(ana).get("/cauldron/api/today/").status_code == 409  # same rule on the web
        _confirmed(token, "delete_forge_session", {"session_id": session["id"]})
        assert not WorkoutSession.objects.filter(uuid=session["id"]).exists()
        assert _ok(token, "get_forge_today")["status"] == "not_started"

    def test_finishing_the_unfinished_session_keeps_its_date(self, ana):
        _trained(ana)
        token = _token(ana)
        session = _ok(token, "start_forge_session")["session"]
        _log_all(token, session)
        yesterday = timezone.now().date() - timedelta(days=1)
        WorkoutSession.objects.filter(uuid=session["id"]).update(scheduled_for=yesterday)
        _ok(token, "finish_forge_session", {"session_id": session["id"]})
        assert timezone.localtime(WorkoutSession.objects.get(uuid=session["id"]).performed_at).date() == yesterday

    def test_someone_elses_sessions(self, ana, ben):
        _trained(ana)
        session = _ok(_token(ana), "start_forge_session")["session"]
        set_id = session["exercises"][0]["sets"][0]["id"]
        bens = _token(ben)
        assert _err(bens, "log_forge_set", {"set_id": set_id, "reps": 5})["code"] == "not_found"
        assert _err(bens, "finish_forge_session", {"session_id": session["id"]})["code"] == "not_found"
        assert _err(bens, "delete_forge_session", {"session_id": session["id"]})["code"] == "not_found"
        today = timezone.now().date()
        assert _ok(bens, "get_forge_sessions", {"from": str(today)})["sessions"] == []


class TestReads:
    def test_program_progress_history(self, ana):
        _trained(ana)
        token = _token(ana)
        program = _ok(token, "get_forge_program")
        assert len(program["prescriptions"]) == PrescribedExercise.objects.filter(day__program__user=ana).count()
        session = _ok(token, "start_forge_session")["session"]
        _log_all(token, session)
        _ok(token, "finish_forge_session", {"session_id": session["id"]})
        progress = _ok(token, "get_forge_progress")
        assert len(progress["sessions"]) == 1 and progress["sessions"][0]["total_reps"] > 0
        today = timezone.now().date()
        history = _ok(token, "get_forge_sessions", {"from": str(today - timedelta(days=3)), "to": str(today)})
        assert [s["id"] for s in history["sessions"]] == [session["id"]]
        assert _err(token, "get_forge_sessions", {"from": str(today - timedelta(days=90)), "to": str(today)})["code"] == "invalid_arguments"

    def test_get_today_includes_the_workout(self, ana):
        from the_cauldron.services import legal

        legal.accept(ana, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
        _trained(ana)
        d = _ok(_token(ana), "get_today")
        assert d["forge"]["status"] == "not_started" and d["forge"]["exercises"]

    def test_forge_tools_dont_need_nutrition_consent(self, ana):
        assert _ok(_token(ana), "get_forge_today")["status"] == "no_program"

    def test_read_scope_cant_write(self, ana):
        r = _rpc(_token(ana, scope="cauldron:read"), "tools/call", {"name": "start_forge_session", "arguments": {}})
        assert r.status_code == 403

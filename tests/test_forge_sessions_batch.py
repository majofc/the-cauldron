"""Forge session paths: RIR removed (#27), no N+1 on session reads/writes
(#129), one planned session per day on the device's date (#56)."""

import importlib
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    CauldronProfile,
    Exercise,
    PrescribedExercise,
    SetLog,
    WorkoutSession,
)
from the_cauldron.serializers import SetLogSerializer
from the_cauldron.services import forge
from the_cauldron.tests.helpers import persist_today, plan_snapshot
from the_cauldron.tests.test_mcp_server import _err, _ok, _token, _user  # noqa: F401

EQUIPMENT = ["bodyweight", "pullup_bar"]


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def athlete(seeded):
    user = _user("athlete", consent=False)
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(profile.equipment.model.objects.filter(key__in=EQUIPMENT))
    anchors = {p.key: a for p, a in forge.trial_anchors(user)}
    results = [
        {"pattern_key": key, "tested_exercise": str(a.uuid), "reps_or_seconds": 6}
        for key, a in anchors.items() if a is not None
    ]
    forge.submit_assessment(user, results)  # noqa: the TrialSubmission is unused
    return user


def _web(user, **headers):
    c = APIClient(**headers)
    c.force_authenticate(user)
    return c


def _sets(user, n):
    PrescribedExercise.objects.filter(day__program__user=user, day__program__is_active=True).update(target_sets=n)


def _session(user, n):
    _sets(user, n)
    return persist_today(user, forge.active_day(user))


class _Rollback(Exception):
    pass


def _isolated(run, n):
    """``run(n)``'s query count, warmed once, with every write rolled back so
    progression state can't differ between the 3- and 12-set runs."""
    counts = []
    for _ in range(2):
        try:
            with transaction.atomic():
                counts.append(run(n))
                raise _Rollback
        except _Rollback:
            pass
    return counts[-1]


def _queries(fn):
    with CaptureQueriesContext(connection) as ctx:
        fn()
    return len(ctx.captured_queries)


# ── #27 RIR is gone ──────────────────────────────────────────────────────────


def test_setlog_has_no_rir(athlete):
    session = _session(athlete, 3)
    assert not hasattr(SetLog, "rir")
    assert "rir" not in SetLogSerializer(session.set_logs.first()).data


def test_log_ignores_an_old_clients_rir(athlete):
    session = _session(athlete, 3)
    row = session.set_logs.order_by("set_index").first()
    r = _web(athlete).post(
        f"/cauldron/api/sessions/{session.uuid}/log/",
        {"sets": {str(row.uuid): {"actual_reps": 7, "actual_load": None, "rir": 2}}},
        format="json",
    )
    assert r.status_code == 200, r.content
    row.refresh_from_db()
    assert row.actual_reps == 7


def test_mcp_set_tools_reject_rir(athlete):
    token = _token(athlete)
    _ok(token, "start_forge_session")
    set_id = str(WorkoutSession.objects.get(user=athlete).set_logs.first().uuid)
    assert _err(token, "log_forge_set", {"set_id": set_id, "reps": 5, "rir": 1})["code"] == "invalid_arguments"
    logged = _ok(token, "log_forge_set", {"set_id": set_id, "reps": 5})["logged"]
    assert "rir" not in logged
    today = date.today()
    sessions = _ok(token, "get_forge_sessions", {"from": (today - timedelta(days=2)).isoformat()})["sessions"]
    assert sessions and all("rir" not in s for b in sessions[0]["exercises"] for s in b["sets"])


# ── #129 constant queries ────────────────────────────────────────────────────


def _values(session, reps=5):
    return {str(sl.uuid): {"actual_reps": reps} for sl in session.set_logs.all()}


def test_sets_endpoint_queries_do_not_grow_with_sets(athlete):
    small = _session(athlete, 3)
    q3 = _queries(lambda: _web(athlete).post(f"/cauldron/api/sessions/{small.uuid}/sets/",
                                             {"sets": _values(small)}, format="json"))
    small.delete()
    big = _session(athlete, 12)
    q12 = _queries(lambda: _web(athlete).post(f"/cauldron/api/sessions/{big.uuid}/sets/",
                                              {"sets": _values(big)}, format="json"))
    assert q3 == q12


def test_saving_values_is_one_update(athlete):
    session = _session(athlete, 4)
    with CaptureQueriesContext(connection) as ctx:
        forge.save_session_values(session, _values(session, 6))
    updates = [q for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 1
    assert set(session.set_logs.values_list("actual_reps", flat=True)) == {6}


@pytest.mark.parametrize("path", ["today", "unfinished", "create", "log"])
def test_session_reads_constant(athlete, path, monkeypatch):
    def run(n):
        WorkoutSession.objects.filter(user=athlete).delete()
        _sets(athlete, n)
        client = _web(athlete)
        if path == "create":
            plan = client.get("/cauldron/api/today/").json()["set_logs"]
            body = {"sets": [{"exercise": s["exercise"], "prescription": s["prescription"],
                              "set_index": s["set_index"]} for s in plan]}
            return _queries(lambda: client.post("/cauldron/api/sessions/", body, format="json"))
        session = persist_today(athlete, forge.active_day(athlete))
        if path == "today":
            return _queries(lambda: client.get("/cauldron/api/today/"))
        if path == "unfinished":
            WorkoutSession.objects.filter(pk=session.pk).update(scheduled_for=forge.local_today(athlete) - timedelta(days=1))
            session.set_logs.update(actual_reps=3)
            return _queries(lambda: client.get("/cauldron/api/sessions/unfinished/"))
        return _queries(lambda: client.post(f"/cauldron/api/sessions/{session.uuid}/log/",
                                            {"sets": _values(session)}, format="json"))

    assert _isolated(run, 3) == _isolated(run, 12)


def test_plan_serialization_constant(athlete):
    from the_cauldron.serializers import PlannedSetLogSerializer

    def run(n):
        _sets(athlete, n)
        rows = forge.build_today_plan(athlete, forge.active_day(athlete))
        return _queries(lambda: PlannedSetLogSerializer(rows, many=True).data)

    assert _isolated(run, 3) == _isolated(run, 12)


def test_mcp_today_and_sessions_constant(athlete):
    token = _token(athlete)

    def run(n):
        WorkoutSession.objects.filter(user=athlete).delete()
        _sets(athlete, n)
        persist_today(athlete, forge.active_day(athlete))
        today = forge.local_today(athlete).isoformat()
        return (_queries(lambda: _ok(token, "get_forge_today")),
                _queries(lambda: _ok(token, "get_forge_sessions", {"from": today})))

    assert _isolated(run, 3) == _isolated(run, 12)


def test_swap_constant(athlete):
    def run(n):
        WorkoutSession.objects.filter(user=athlete).delete()
        _sets(athlete, n)
        session = persist_today(athlete, forge.active_day(athlete))
        owned = forge.owned_equipment_keys_for(athlete)
        on = set(session.set_logs.values_list("exercise_id", flat=True))
        for log in session.set_logs.select_related("exercise"):
            for ex in Exercise.objects.filter(pattern=log.exercise.pattern).exclude(pk__in=on):
                if forge.is_performable(ex, owned):
                    body = {"from_exercise": str(log.exercise.uuid), "to_exercise": str(ex.uuid)}
                    return _queries(lambda: _web(athlete).post(
                        f"/cauldron/api/sessions/{session.uuid}/swap/", body, format="json"))
        raise AssertionError("nothing to swap")

    assert _isolated(run, 3) == _isolated(run, 12)


# ── #56 one planned session per day, device date ─────────────────────────────


def test_existing_planned_session_is_returned_not_duplicated(athlete, monkeypatch):
    day = forge.active_day(athlete)
    first = persist_today(athlete, day)
    snapshot = plan_snapshot(forge.build_today_plan(athlete, day))
    session, created = forge.get_or_create_session_from_plan(athlete, day, snapshot)
    assert (session.pk, created) == (first.pk, False)
    assert WorkoutSession.objects.filter(user=athlete).count() == 1

    # The race: the resumable check misses, the insert hits the constraint.
    monkeypatch.setattr(forge, "resumable_session", lambda user: None)
    r = _web(athlete).post("/cauldron/api/sessions/", {"sets": snapshot}, format="json")
    assert r.status_code == 200 and r.json()["uuid"] == str(first.uuid)
    assert WorkoutSession.objects.filter(user=athlete).count() == 1


def test_mcp_start_returns_existing(athlete):
    first = persist_today(athlete, forge.active_day(athlete))
    out = _ok(_token(athlete), "start_forge_session")
    assert out["status"] == "in_progress" and out["session"]["id"] == str(first.uuid)


def test_delete_then_start_again_same_day(athlete):
    first = persist_today(athlete, forge.active_day(athlete))
    client = _web(athlete)
    assert client.delete(f"/cauldron/api/sessions/{first.uuid}/").status_code == 204
    plan = client.get("/cauldron/api/today/").json()["set_logs"]
    body = {"sets": [{"exercise": s["exercise"], "prescription": s["prescription"],
                      "set_index": s["set_index"]} for s in plan]}
    assert client.post("/cauldron/api/sessions/", body, format="json").status_code == 201


# 2026-11-10 20:00 in Mexico City = 2026-11-11 02:00 UTC.
FROZEN = datetime(2026, 11, 11, 2, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)


def _start(client):
    plan = client.get("/cauldron/api/today/").json()
    body = {"sets": [{"exercise": s["exercise"], "prescription": s["prescription"],
                      "set_index": s["set_index"]} for s in plan["set_logs"]]}
    return plan, client.post("/cauldron/api/sessions/", body, format="json")


def test_header_zone_sets_scheduled_for(athlete, frozen):
    CauldronProfile.objects.update_or_create(user=athlete, defaults={"timezone": "Asia/Tokyo"})
    plan, r = _start(_web(athlete, HTTP_X_CAULDRON_TIMEZONE="America/Mexico_City"))
    assert plan["scheduled_for"] == "2026-11-10"
    assert r.status_code == 201 and r.json()["scheduled_for"] == "2026-11-10"


@pytest.mark.parametrize("header", [None, "Not/AZone", "../../etc/passwd"])
def test_invalid_or_missing_header_falls_back_to_profile(athlete, frozen, header):
    CauldronProfile.objects.update_or_create(user=athlete, defaults={"timezone": "Asia/Tokyo"})
    headers = {"HTTP_X_CAULDRON_TIMEZONE": header} if header else {}
    _plan, r = _start(_web(athlete, **headers))
    assert r.json()["scheduled_for"] == "2026-11-11"


def test_mcp_uses_profile_zone(athlete, frozen):
    CauldronProfile.objects.update_or_create(user=athlete, defaults={"timezone": "America/Mexico_City"})
    out = _ok(_token(athlete), "start_forge_session")
    assert out["session"]["date"] == "2026-11-10"


def test_unfinished_gate_uses_device_today(athlete, frozen):
    session = persist_today(athlete, forge.active_day(athlete))
    WorkoutSession.objects.filter(pk=session.pk).update(scheduled_for=date(2026, 11, 10))
    session.set_logs.update(actual_reps=4)
    # Still the 10th on a Mexico City device: today's own session, no gate.
    assert _web(athlete, HTTP_X_CAULDRON_TIMEZONE="America/Mexico_City").get("/cauldron/api/today/").status_code == 200
    # Already the 11th in UTC: an unfinished past session.
    assert _web(athlete, HTTP_X_CAULDRON_TIMEZONE="UTC").get("/cauldron/api/today/").status_code == 409


def test_migration_collapses_duplicates(athlete):
    module = importlib.import_module("the_cauldron.migrations.0042_one_planned_session_per_day")
    constraint = next(c for c in WorkoutSession._meta.constraints if c.name == "one_planned_session_per_day")
    day = forge.active_day(athlete)
    with connection.schema_editor() as editor:
        editor.remove_constraint(WorkoutSession, constraint)
    try:
        today = forge.local_today(athlete)
        older_with_values = WorkoutSession.objects.create(user=athlete, program_day=day, scheduled_for=today)
        SetLog.objects.create(session=older_with_values, exercise=Exercise.objects.first(), set_index=0, actual_reps=5)
        newer_empty = WorkoutSession.objects.create(user=athlete, program_day=day, scheduled_for=today)
        other_day_a = WorkoutSession.objects.create(user=athlete, program_day=day, scheduled_for=today - timedelta(days=3))
        other_day_b = WorkoutSession.objects.create(user=athlete, program_day=day, scheduled_for=today - timedelta(days=3))
        module.collapse_duplicate_planned(django_apps, None)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with connection.schema_editor() as editor:
            editor.add_constraint(WorkoutSession, constraint)
    status = dict(WorkoutSession.objects.filter(user=athlete).values_list("pk", "status"))
    assert status[older_with_values.pk] == "planned"   # kept: has values
    assert status[newer_empty.pk] == "skipped"
    assert status[other_day_b.pk] == "planned"         # kept: newest
    assert status[other_day_a.pk] == "skipped"
    assert WorkoutSession.objects.filter(user=athlete).count() == 4  # nothing deleted

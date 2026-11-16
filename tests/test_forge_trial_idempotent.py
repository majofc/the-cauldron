"""Trial submission is idempotent and Programs are one per assessment (#26)."""

import importlib
from datetime import timedelta
from io import StringIO

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import AssessmentSession, Program, ProgramDay, WorkoutSession
from the_cauldron.services import forge
from the_cauldron.tests.test_mcp_server import _ok, _token, _user  # noqa: F401

EQUIPMENT = ["bodyweight", "pullup_bar"]
ASSESS = "/cauldron/api/assessment/"


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def athlete(seeded):
    user = _user("trialist", consent=False)
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(profile.equipment.model.objects.filter(key__in=EQUIPMENT))
    return user


def _web(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _results(user, value=6):
    return [
        {"pattern_key": p.key, "tested_exercise": str(a.uuid), "reps_or_seconds": value}
        for p, a in forge.trial_anchors(user) if a is not None
    ]


def test_first_trial_creates_program(athlete):
    r = _web(athlete).post(ASSESS, {"results": _results(athlete)}, format="json")
    assert r.status_code == 201 and r.json()["idempotent"] is False
    assert Program.objects.filter(user=athlete).count() == 1


def test_sequential_retry_writes_nothing(athlete):
    client = _web(athlete)
    first = client.post(ASSESS, {"results": _results(athlete)}, format="json").json()
    again = client.post(ASSESS, {"results": _results(athlete, 9)}, format="json")
    assert again.status_code == 200
    body = again.json()
    assert body["idempotent"] is True
    assert body["program"]["uuid"] == first["program"]["uuid"]
    assert body["assessment"]["uuid"] == first["assessment"]["uuid"]
    assert {p["value"] for p in body["peer"]} == {6}
    assert Program.objects.filter(user=athlete).count() == 1
    assert AssessmentSession.objects.filter(user=athlete).count() == 1


def test_retake_then_submit_creates_new(athlete):
    client = _web(athlete)
    client.post(ASSESS, {"results": _results(athlete)}, format="json")
    assert client.post(ASSESS + "retake/").status_code == 201
    r = client.post(ASSESS, {"results": _results(athlete, 8)}, format="json")
    assert r.status_code == 201 and r.json()["idempotent"] is False
    assert Program.objects.filter(user=athlete).count() == 2
    assert Program.objects.filter(user=athlete, is_active=True).count() == 1


def test_submit_locks_rows(athlete):
    """Two concurrent submits serialize on the profile row (and the open
    assessment), so the second sees the first's Program."""
    with CaptureQueriesContext(connection) as ctx:
        forge.submit_assessment(athlete, _results(athlete))
    locks = [q["sql"] for q in ctx.captured_queries if "FOR UPDATE" in q["sql"]]
    assert any("userequipmentprofile" in sql for sql in locks)


def test_mcp_retry_returns_current_program(athlete):
    token = _token(athlete)
    args = [{"pattern": p.key, "value": 6} for p, a in forge.trial_anchors(athlete) if a is not None]
    first = _ok(token, "submit_trial_results", {"results": args})
    assert "already_recorded" not in first
    again = _ok(token, "submit_trial_results", {"results": args})
    assert again["already_recorded"] is True and again["note"]
    assert Program.objects.filter(user=athlete).count() == 1


def test_db_rejects_second_program_for_one_assessment(athlete):
    sub = forge.submit_assessment(athlete, _results(athlete))
    with pytest.raises(IntegrityError), transaction.atomic():
        Program.objects.create(user=athlete, source_assessment=sub.assessment, is_active=False)


# ── cleanup of existing clones ───────────────────────────────────────────────


def _clone_history(user):
    """Three Trials in 2.5 minutes (racing clones), then a real one a day later
    whose Program has a logged session, then the active one."""
    base = timezone.now() - timedelta(days=3)
    made = []
    for minutes in (0, 1, 2.5):
        a = AssessmentSession.objects.create(user=user, is_active=False)
        AssessmentSession.objects.filter(pk=a.pk).update(completed_at=base + timedelta(minutes=minutes))
        p = Program.objects.create(user=user, is_active=False, source_assessment=a)
        Program.objects.filter(pk=p.pk).update(created_at=base + timedelta(minutes=minutes))
        made.append((a, p))
    real_a = AssessmentSession.objects.create(user=user, is_active=False)
    AssessmentSession.objects.filter(pk=real_a.pk).update(completed_at=base + timedelta(days=1))
    real_p = Program.objects.create(user=user, is_active=False, source_assessment=real_a)
    Program.objects.filter(pk=real_p.pk).update(created_at=base + timedelta(days=1))
    day = ProgramDay.objects.create(program=real_p, day_index=0)
    WorkoutSession.objects.create(user=user, program_day=day, scheduled_for=(base + timedelta(days=1)).date(),
                                  status="completed")
    # The active Program, created 5 min after the real one: never touched.
    active_a = AssessmentSession.objects.create(user=user, is_active=True)
    AssessmentSession.objects.filter(pk=active_a.pk).update(completed_at=base + timedelta(days=1, minutes=5))
    active_p = Program.objects.create(user=user, is_active=True, source_assessment=active_a)
    Program.objects.filter(pk=active_p.pk).update(created_at=base + timedelta(days=1, minutes=5))
    return made, (real_a, real_p), (active_a, active_p)


def test_dry_run_lists_without_deleting(athlete):
    _clone_history(athlete)
    out = StringIO()
    call_command("dedupe_programs", "--dry-run", stdout=out)
    assert f"user {athlete.pk}: 2 program(s), 2 assessment(s)" in out.getvalue()
    assert "would be deleted" in out.getvalue()
    assert "@" not in out.getvalue()  # no emails
    assert Program.objects.filter(user=athlete).count() == 5


def test_migration_cleans_clones_keeps_history(athlete):
    made, (real_a, real_p), (active_a, active_p) = _clone_history(athlete)
    module = importlib.import_module("the_cauldron.migrations.0043_uniq_program_per_assessment")
    module.dedupe(django_apps, None)
    programs = set(Program.objects.filter(user=athlete).values_list("pk", flat=True))
    # The two older clones go; the last of the burst stays (nothing within 10 min after it).
    assert programs == {made[2][1].pk, real_p.pk, active_p.pk}
    assessments = set(AssessmentSession.objects.filter(user=athlete).values_list("pk", flat=True))
    assert assessments == {made[2][0].pk, real_a.pk, active_a.pk}
    # Rerunning finds nothing.
    module.dedupe(django_apps, None)
    assert Program.objects.filter(user=athlete).count() == 3

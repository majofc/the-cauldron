"""Tests for exercise→muscle mapping, serializer exposure, and the daily
worked-muscle diagram data. Requires DB (seeded catalog)."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.models import Exercise, MovementPattern, Muscle

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


# ── Seeding ──────────────────────────────────────────────────────────────────


def test_seed_creates_muscle_catalog(seeded):
    # Every seeded muscle has a region on one of the two body figures.
    assert Muscle.objects.count() >= 15
    assert set(Muscle.objects.values_list("region", flat=True)) <= {"front", "back"}
    assert Muscle.objects.filter(key="chest", region="front").exists()
    assert Muscle.objects.filter(key="glutes", region="back").exists()


def test_seed_assigns_multiple_muscles_to_exercise(seeded):
    # A push-up trains more than one muscle group (the core requirement).
    pushup = Exercise.objects.get(name="Push-up")
    keys = set(pushup.muscles.values_list("key", flat=True))
    assert {"chest", "front_delts", "triceps"} <= keys
    assert len(keys) >= 2


def test_seed_is_idempotent_for_muscles(seeded):
    before = Muscle.objects.count()
    pushup_before = set(Exercise.objects.get(name="Push-up").muscles.values_list("key", flat=True))
    call_command("seed_forge")
    assert Muscle.objects.count() == before
    assert set(Exercise.objects.get(name="Push-up").muscles.values_list("key", flat=True)) == pushup_before


# ── Serializer exposure ──────────────────────────────────────────────────────


def test_exercise_endpoint_includes_muscles(seeded, client):
    resp = client.get("/cauldron/api/exercises/")
    assert resp.status_code == 200
    by_name = {e["name"]: e for e in resp.json()}
    pushup = by_name["Push-up"]
    assert "muscles" in pushup
    keys = {m["key"] for m in pushup["muscles"]}
    assert {"chest", "front_delts", "triceps"} <= keys
    # Each muscle carries the fields the diagram needs.
    sample = pushup["muscles"][0]
    assert {"key", "name", "region"} <= set(sample.keys())
    assert sample["region"] in {"front", "back"}


# ── Daily diagram data (set logs expose worked muscles) ──────────────────────


def _assessment_payload():
    results = []
    for pattern in MovementPattern.objects.all():
        ex = pattern.exercises.order_by("difficulty_rank").first()
        results.append(
            {"pattern_key": pattern.key, "tested_exercise": str(ex.uuid), "reps_or_seconds": 6}
        )
    return {"results": results}


def _open_today(client):
    """The plan, persisted. Opening Today writes nothing, so a test that needs a
    session to log against has to post the snapshot the way the browser does."""
    plan = client.get("/cauldron/api/today/")
    assert plan.status_code == 200
    created = client.post(
        "/cauldron/api/sessions/",
        {
            "sets": [
                {
                    "exercise": s["exercise"],
                    "prescription": s["prescription"],
                    "set_index": s["set_index"],
                }
                for s in plan.json()["set_logs"]
            ]
        },
        format="json",
    )
    assert created.status_code == 201
    return created.json()


def test_today_set_logs_expose_muscles_for_diagram(seeded, client):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")

    today = client.get("/cauldron/api/today/")
    assert today.status_code == 200
    set_logs = today.json()["set_logs"]
    assert set_logs, "expected at least one prescribed set on the day"
    # Every set log carries its exercise's muscle list, and at least one set in
    # the day reports trained muscles — enough to render the worked-muscle map.
    assert all("muscles" in s for s in set_logs)
    worked = {m["key"] for s in set_logs for m in s["muscles"]}
    assert worked, "no muscles surfaced across the day's set logs"


# ── Filters: Exercises catalog + Progress chart ──────────────────────────────


def test_catalog_exercises_include_muscles_for_filtering(seeded, client):
    _set_equipment(client, ["bodyweight"])
    resp = client.get("/cauldron/api/catalog/")
    assert resp.status_code == 200
    exercises = [e for g in resp.json()["groups"] for e in g["exercises"]]
    pushup = next(e for e in exercises if e["name"] == "Push-up")
    assert "muscles" in pushup
    keys = {m["key"] for m in pushup["muscles"]}
    assert {"chest", "front_delts", "triceps"} <= keys


def test_progress_lists_muscles_and_filters_by_muscle(seeded, client):
    _set_equipment(client, ["bodyweight", "pullup_bar"])
    client.post("/cauldron/api/assessment/", _assessment_payload(), format="json")
    # Complete the day so there is a session with set logs to chart.
    session = _open_today(client)
    trained = {m["key"] for s in session["set_logs"] for m in s["muscles"]}
    set_results = {
        s["uuid"]: {"actual_reps": (s["expected_reps"] or 5), "actual_load": s["expected_load"]}
        for s in session["set_logs"]
    }
    client.post(
        f"/cauldron/api/sessions/{session['uuid']}/log/", {"sets": set_results}, format="json"
    )

    # The Progress payload advertises the muscle catalog for the dropdown.
    base = client.get("/cauldron/api/progress/").json()
    assert {"key", "name"} <= set(base["muscles"][0].keys())
    assert base["points"], "expected at least one completed-session point"

    # Filtering by a muscle trained that day keeps points; an untrained muscle drops them.
    trained_key = next(iter(trained))
    all_keys = {m["key"] for m in base["muscles"]}
    untrained_key = next(k for k in all_keys if k not in trained)
    assert client.get(f"/cauldron/api/progress/?muscle={trained_key}").json()["points"]
    assert client.get(f"/cauldron/api/progress/?muscle={untrained_key}").json()["points"] == []

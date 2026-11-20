"""Forge engine batch: regression after swap-downs (#23), the 20-rep ceiling
(#24), band names for band loads (#55) and payload shapes / route (#10)."""

from types import SimpleNamespace as NS

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from the_cauldron.models import (
    Equipment,
    Exercise,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    WorkoutSession,
)
from the_cauldron.serializers import PrescribedExerciseSerializer, SetLogSerializer
from the_cauldron.services import forge
from the_cauldron.services.progression import (
    REP_EXTENSION_CEILING,
    SESSIONS_TO_ADVANCE,
    band_label,
    next_prescription,
)
from the_cauldron.tests.test_mcp_server import _err, _ok, _token  # noqa: F401
from the_cauldron.tests.test_progression_63 import bands, fixed_dumbbells, presc, rung

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="batcher", email="batcher@example.com", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _own(user, *keys, **fields):
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=keys))
    for k, v in fields.items():
        setattr(profile, k, v)
    profile.save()
    return profile


def _ex(name):
    return Exercise.objects.get(name=name)


def _prescribe(user, exercise, **fields):
    program = Program.objects.filter(user=user, is_active=True).first() or Program.objects.create(
        user=user, is_active=True
    )
    day, _ = ProgramDay.objects.get_or_create(program=program, day_index=0, defaults={"name": "Today"})
    defaults = dict(
        target_sets=3, target_reps_min=exercise.rep_range_min, target_reps_max=exercise.rep_range_max,
        target_rest_seconds=exercise.rest_seconds, order=0,
    )
    defaults.update(fields)
    return PrescribedExercise.objects.create(day=day, pattern=exercise.pattern, exercise=exercise, **defaults)


_day_offset = {"n": 0}


def _swap_session(user, p, performed, reps=6, log=True, days_ago=None):
    """A completed-to-be session where ``p`` was ⇄ swapped for ``performed``."""
    _day_offset["n"] += 1
    when = timezone.now().date() - timezone.timedelta(days=days_ago if days_ago is not None else 0)
    session = WorkoutSession.objects.create(user=user, program_day=p.day, scheduled_for=when, status="planned")
    sets = {}
    for i in range(3):
        sl = SetLog.objects.create(session=session, prescribed_exercise=None, swapped_from=p, exercise=performed,
                                   set_index=i, expected_reps=performed.rep_range_min, is_amrap=i == 2)
        if log:
            sets[str(sl.uuid)] = {"actual_reps": reps}
    deltas = forge.apply_session_log(session, sets)
    return session, deltas


def _as_prescribed(user, p, reps=6):
    session = WorkoutSession.objects.create(user=user, program_day=p.day, scheduled_for=timezone.now().date(),
                                            status="planned")
    sets = {}
    for i in range(3):
        sl = SetLog.objects.create(session=session, prescribed_exercise=p, exercise=p.exercise, set_index=i,
                                   expected_reps=p.target_reps_min, expected_load=p.target_load, is_amrap=i == 2)
        sets[str(sl.uuid)] = {"actual_reps": reps, "actual_load": p.target_load}
    return forge.apply_session_log(session, sets)


def _pullup(user):
    _own(user, "bodyweight", "pullup_bar")
    return _prescribe(user, _ex("Pull-up"), target_reps_min=4, target_reps_max=10)


# ── #23 regression after two swap-downs ──────────────────────────────────────


class TestSwapDown:
    def test_two_swap_downs_suggest_the_easier_rung(self, seeded, user):
        p = _pullup(user)
        _swap_session(user, p, _ex("Negative Pull-up"))
        p.refresh_from_db()
        assert (p.sessions_swapped_down, p.pending_regression) == (1, None)
        _, deltas = _swap_session(user, p, _ex("Negative Pull-up"))
        p.refresh_from_db()
        assert p.pending_regression.name == "Negative Pull-up"
        assert p.sessions_swapped_down == 0
        assert any(d.get("unlock", {}).get("direction") == "down" for d in deltas)

    def test_original_scenario_never_reads_on_track(self, seeded, user):
        p = _pullup(user)
        _, deltas = _swap_session(user, p, _ex("Negative Pull-up"), reps=6)
        assert not any("On track" in d["message"] for d in deltas)
        p.refresh_from_db()
        assert p.exercise.name == "Pull-up" and p.sessions_at_top == 0  # swapped reps never scored

    def test_session_as_prescribed_resets_the_count(self, seeded, user):
        p = _pullup(user)
        _swap_session(user, p, _ex("Negative Pull-up"))
        _as_prescribed(user, p)
        _swap_session(user, p, _ex("Negative Pull-up"))
        p.refresh_from_db()
        assert p.pending_regression is None and p.sessions_swapped_down == 1

    def test_latest_swap_two_rungs_down_wins(self, seeded, user):
        p = _pullup(user)
        _swap_session(user, p, _ex("Negative Pull-up"))
        _swap_session(user, p, _ex("Single-Arm Australian Row"))
        p.refresh_from_db()
        assert p.pending_regression.name == "Single-Arm Australian Row"

    @pytest.mark.parametrize("performed", ["Archer Pull-up", "Chin-up", "Push-up"])
    def test_harder_grip_variant_or_off_ladder_never_counts(self, seeded, user, performed):
        p = _pullup(user)
        _swap_session(user, p, _ex(performed))
        _swap_session(user, p, _ex(performed))
        p.refresh_from_db()
        assert p.pending_regression is None and p.sessions_swapped_down == 0

    def test_unlogged_swap_never_counts(self, seeded, user):
        p = _pullup(user)
        _swap_session(user, p, _ex("Negative Pull-up"), log=False)
        _swap_session(user, p, _ex("Negative Pull-up"), log=False)
        p.refresh_from_db()
        assert p.pending_regression is None and p.sessions_swapped_down == 0

    def test_accept_repoints_with_the_rungs_own_targets(self, seeded, user, client):
        p = _pullup(user)
        p.pending_regression = _ex("Negative Pull-up")
        p.save()
        r = client.post(f"/cauldron/api/regression/{p.uuid}/accept/")
        assert r.status_code == 200
        p.refresh_from_db()
        neg = _ex("Negative Pull-up")
        assert p.exercise == neg and (p.target_reps_min, p.target_reps_max) == (neg.rep_range_min, neg.rep_range_max)
        assert p.pending_regression is None
        plan = forge.build_today_plan(user, p.day)
        assert {row.exercise.name for row in plan} == {"Negative Pull-up"}

    def test_deny_clears_and_needs_two_more(self, seeded, user, client):
        p = _pullup(user)
        _swap_session(user, p, _ex("Negative Pull-up"))
        _swap_session(user, p, _ex("Negative Pull-up"))
        assert client.post(f"/cauldron/api/regression/{p.uuid}/deny/").status_code == 200
        p.refresh_from_db()
        assert p.pending_regression is None and p.sessions_swapped_down == 0
        _swap_session(user, p, _ex("Negative Pull-up"))
        p.refresh_from_db()
        assert p.pending_regression is None
        _swap_session(user, p, _ex("Negative Pull-up"))
        p.refresh_from_db()
        assert p.pending_regression.name == "Negative Pull-up"

    def test_a_new_regression_replaces_a_pending_unlock(self, seeded, user):
        p = _pullup(user)
        p.pending_progression = _ex("Archer Pull-up")
        p.save()
        _swap_session(user, p, _ex("Negative Pull-up"))
        _swap_session(user, p, _ex("Negative Pull-up"))
        p.refresh_from_db()
        assert p.pending_progression is None and p.pending_regression is not None

    def test_api_swap_records_what_it_replaced(self, seeded, user, client):
        p = _pullup(user)
        session = WorkoutSession.objects.create(user=user, program_day=p.day,
                                                scheduled_for=forge.local_today(user), status="planned")
        for i in range(3):
            SetLog.objects.create(session=session, prescribed_exercise=p, exercise=p.exercise, set_index=i,
                                  is_amrap=i == 2)
        r = client.post(f"/cauldron/api/sessions/{session.uuid}/swap/",
                        {"from_exercise": str(p.exercise.uuid), "to_exercise": str(_ex("Negative Pull-up").uuid)},
                        format="json")
        assert r.status_code == 200, r.content
        rows = session.set_logs.all()
        assert all(sl.swapped_from_id == p.pk and sl.prescribed_exercise_id is None for sl in rows)
        assert {s["swapped_from"] for s in r.json()["set_logs"]} == {str(p.uuid)}

    def test_pre_save_swap_snapshot_carries_swapped_from(self, seeded, user):
        p = _pullup(user)
        neg = _ex("Negative Pull-up")
        session = forge.create_session_from_plan(user, p.day, [
            {"exercise": str(neg.uuid), "prescription": None, "swapped_from": str(p.uuid), "set_index": 0},
        ])
        assert {sl.swapped_from_id for sl in session.set_logs.all()} == {p.pk}

    def test_mcp_program_and_answer(self, seeded, user):
        p = _pullup(user)
        p.pending_regression = _ex("Negative Pull-up")
        p.save()
        token = _token(user)
        row = next(r for r in _ok(token, "get_forge_program")["prescriptions"] if r["prescription_id"] == str(p.uuid))
        assert row["pending_regression"] == "Negative Pull-up"
        out = _ok(token, "answer_forge_suggestion", {"prescription_id": str(p.uuid), "direction": "down",
                                                     "accept": True})
        assert out["exercise"] == "Negative Pull-up"
        assert _err(token, "answer_forge_suggestion", {"prescription_id": str(p.uuid), "direction": "down",
                                                       "accept": False})["code"] == "not_found"

    def test_today_lists_the_suggestion(self, seeded, user, client):
        p = _pullup(user)
        p.pending_regression = _ex("Negative Pull-up")
        p.save()
        notices = client.get("/cauldron/api/today/").json()["notices"]
        assert notices["suggestions"] == [{
            "direction": "down", "prescription": str(p.uuid), "from_name": "Pull-up",
            "to_name": "Negative Pull-up", "to_uuid": str(_ex("Negative Pull-up").uuid),
        }]


# ── #24 the rep ceiling ──────────────────────────────────────────────────────

DB = rung("db", mode="load", rmin=6, rmax=12, required=["dumbbells"])


class TestRepCeiling:
    def test_load_mode_shifts_then_holds(self):
        p = presc(DB, load=12)
        ranges = []
        for _ in range(15):
            r = next_prescription(p, p.target_reps_max, fixed_dumbbells([10, 12]), next_rung=None)
            ranges.append((r.target_reps_min, r.target_reps_max))
            p = presc(DB, rmin=r.target_reps_min, rmax=r.target_reps_max, load=12, at_top=r.sessions_at_top)
        assert ranges[:8] == [(7 + i, 13 + i) for i in range(8)]  # … → 14–20
        assert set(ranges[8:]) == {(14, 20)}
        assert r.ceiling_notice == "heavier_load"

    def test_lightest_band_shifts_to_the_ceiling(self):
        ex = rung("Band-Assisted Row", mode="load", rmin=8, rmax=15, assisted=True, required=["bands"])
        r = next_prescription(presc(ex, rmin=14, rmax=20, load=0), 22, bands(), next_rung=None)
        assert (r.target_reps_max, r.ceiling_notice) == (20, "lighter_band")
        r = next_prescription(presc(ex, rmin=12, rmax=19, load=0), 22, bands(), next_rung=None)
        assert (r.target_reps_min, r.target_reps_max) == (13, 20)

    def test_top_of_ladder_shares_the_ceiling(self):
        top = rung("Dragon", rmin=8, rmax=12)
        r = next_prescription(presc(top, rmin=16, rmax=20, at_top=1), 22, None, next_rung=None)
        assert (r.target_reps_max, r.ceiling_notice) == (20, "top_of_ladder")

    def test_load_mode_at_ceiling_offers_a_harder_rung_after_two(self):
        harder = rung("Harder", rmin=5, rmax=10)
        r1 = next_prescription(presc(DB, rmin=14, rmax=20, load=12, at_top=0), 20, fixed_dumbbells([12]),
                               next_rung=harder)
        assert not r1.advanced and r1.sessions_at_top == 1
        r2 = next_prescription(presc(DB, rmin=14, rmax=20, load=12, at_top=r1.sessions_at_top), 20,
                               fixed_dumbbells([12]), next_rung=harder)
        assert r2.advanced and r2.exercise is harder and SESSIONS_TO_ADVANCE == 2

    def test_heavier_equipment_resumes_load_with_base_range(self):
        r = next_prescription(presc(DB, rmin=14, rmax=20, load=12), 20, fixed_dumbbells([12, 14]), next_rung=None)
        assert (r.target_load, r.target_reps_min, r.target_reps_max) == (14, 6, 12)

    def test_above_ceiling_is_clamped(self):
        r = next_prescription(presc(DB, rmin=8, rmax=17 + 10, load=12), 10, fixed_dumbbells([12]), next_rung=None)
        assert (r.target_reps_min, r.target_reps_max) == (REP_EXTENSION_CEILING - 6, REP_EXTENSION_CEILING)

    def test_timed_holds_unaffected(self):
        hold = rung("Plank", rmin=20, rmax=60, timed=True, progression=rung("next"))
        r = next_prescription(presc(hold, rmax=60), 90, None)
        assert r.target_reps_max == 81

    def test_notice_shown_once_until_it_no_longer_applies(self, seeded, user, client):
        _own(user, "dumbbells", dumbbell_weights=[10, 12])
        p = _prescribe(user, _ex("Dumbbell Romanian Deadlift"), target_load=12.0,
                       target_reps_min=14, target_reps_max=20)
        _as_prescribed(user, p, reps=20)
        p.refresh_from_db()
        assert p.ceiling_notice == "heavier_load"
        assert client.get("/cauldron/api/today/").json()["notices"]["ceiling"][0]["notice"] == "heavier_load"
        program = _ok(_token(user), "get_forge_program")["prescriptions"]
        assert program[0]["ceiling_notice"] == "heavier_load"
        assert client.post(f"/cauldron/api/prescription/{p.uuid}/ceiling-notice/dismiss/").status_code == 204
        _as_prescribed(user, p, reps=20)
        p.refresh_from_db()
        assert p.ceiling_notice == ""  # not repeated every session
        # Heavier gear: load moves, the notice memory resets.
        _own(user, "dumbbells", dumbbell_weights=[10, 12, 14])
        _as_prescribed(user, p, reps=20)
        p.refresh_from_db()
        assert (p.target_load, p.ceiling_notice, p.ceiling_notice_dismissed) == (14, "", "")


# ── #55 band names ───────────────────────────────────────────────────────────


class TestBandLoads:
    def test_band_label_falls_back(self):
        profile = NS(band_levels=["light", "15"])
        assert band_label(profile, 0) == "light band"
        assert band_label(profile, 1) == "15 band"
        assert band_label(profile, 5) == "Band 6"

    def test_resisted_and_assisted_messages_name_the_band(self):
        profile = NS(equipment=["bands"], band_levels=["light", "medium", "heavy"], load_unit="kg")
        rollout = rung("Band Rollout", mode="load", rmin=6, rmax=12, required=["bands"])
        r = next_prescription(presc(rollout, load=0), 12, profile)
        assert "medium band" in r.message and "kg" not in r.message
        row = rung("Band-Assisted Row", mode="load", rmin=8, rmax=15, assisted=True, required=["bands"])
        r = next_prescription(presc(row, load=1), 15, profile)
        assert "Less assistance → light band" in r.message

    def test_serializers(self, seeded, user):
        _own(user, "bands", "dumbbells", band_levels=["light", "medium", "heavy"], dumbbell_weights=[5, 10])
        band = _prescribe(user, _ex("Band Rollout"), target_load=1.0)
        db = _prescribe(user, _ex("Dumbbell Romanian Deadlift"), target_load=5.0, order=1)
        data = PrescribedExerciseSerializer(band).data
        assert (data["load_unit"], data["band_labels"], data["is_band_load"]) == ("band", ["light", "medium", "heavy"], True)
        data = PrescribedExerciseSerializer(db).data
        assert (data["load_unit"], data["band_labels"]) == ("kg", None)
        session = WorkoutSession.objects.create(user=user, program_day=band.day, scheduled_for=timezone.now().date())
        sl = SetLog.objects.create(session=session, prescribed_exercise=band, exercise=band.exercise, set_index=0)
        assert SetLogSerializer(sl).data["load_unit"] == "band"
        swap = forge.swap_candidates(user)["candidates"]
        rollout = [c for c in swap if c["exercise_name"] == "Band Rollout"]
        assert not rollout or rollout[0]["load_unit"] == "band"

    @pytest.mark.parametrize("sent, stored", [(1.6, 2.0), (9, 2.0), (-1, None), (0.4, 0.0)])
    def test_log_snaps_band_loads(self, seeded, user, client, sent, stored):
        _own(user, "bands", band_levels=["light", "medium", "heavy"])
        p = _prescribe(user, _ex("Band Rollout"), target_load=0.0)
        session = WorkoutSession.objects.create(user=user, program_day=p.day, scheduled_for=forge.local_today(user))
        sl = SetLog.objects.create(session=session, prescribed_exercise=p, exercise=p.exercise, set_index=0,
                                   is_amrap=True)
        r = client.post(f"/cauldron/api/sessions/{session.uuid}/log/",
                        {"sets": {str(sl.uuid): {"actual_reps": 8, "actual_load": sent}}}, format="json")
        if sent < 0:
            assert r.status_code == 400  # #10: a negative value is rejected outright
            return
        assert r.status_code == 200, r.content
        sl.refresh_from_db()
        assert sl.actual_load == stored

    def test_mcp_reports_and_snaps_bands(self, seeded, user):
        _own(user, "bands", band_levels=["light", "medium", "heavy"])
        p = _prescribe(user, _ex("Band Rollout"), target_load=1.0)
        token = _token(user)
        row = _ok(token, "get_forge_program")["prescriptions"][0]
        assert (row["load_unit"], row["band"]) == ("band", "medium")
        session = WorkoutSession.objects.create(user=user, program_day=p.day, scheduled_for=forge.local_today(user))
        sl = SetLog.objects.create(session=session, prescribed_exercise=p, exercise=p.exercise, set_index=0,
                                   expected_load=1.0, is_amrap=True)
        logged = _ok(token, "log_forge_set", {"set_id": str(sl.uuid), "reps": 8, "load": 7})["logged"]
        assert logged["load_done"] == 2.0
        today = forge.local_today(user).isoformat()
        block = _ok(token, "get_forge_sessions", {"from": today})["sessions"][0]["exercises"][0]
        assert block["load_unit"] == "band" and block["sets"][0]["band"] == "medium"
        assert block["sets"][0]["band_done"] == "heavy"

    def test_progress_payload_for_band_exercise(self, seeded, user):
        _own(user, "bands", band_levels=["light", "medium"])
        p = _prescribe(user, _ex("Band Rollout"), target_load=1.0)
        _as_prescribed(user, p, reps=8)
        data = forge.progress_points(user, exercise=p.exercise.rung_label)
        assert data["load_unit"] == "band" and data["band_labels"] == ["light", "medium"]
        assert data["points"][0]["top_load"] == 1.0
        assert data["points"][0]["total_volume"] == 8 * 3 * 2  # band steps, not kg


# ── #10 payload shapes and the progression route ─────────────────────────────


class TestPayloadShapes:
    @pytest.mark.parametrize("body", [
        {"results": "nope"},
        {"results": ["not-a-dict"]},
        {"results": [{"pattern_key": "hinge", "tested_exercise": "x", "reps_or_seconds": 3}]},
        {"results": [{"pattern_key": "hinge", "tested_exercise": "00000000-0000-0000-0000-000000000000",
                      "reps_or_seconds": -1}]},
    ])
    def test_assessment_bad_shapes_are_400(self, seeded, user, client, body):
        r = client.post("/cauldron/api/assessment/", body, format="json")
        assert r.status_code == 400 and "results" in r.json()

    def test_assessment_list_body_is_400(self, seeded, user, client):
        r = client.post("/cauldron/api/assessment/", [{"pattern_key": "hinge"}], format="json")
        assert r.status_code == 400

    @pytest.mark.parametrize("sets", [
        "nope", ["a"], {"00000000-0000-0000-0000-000000000000": {"actual_reps": 3}},
        "VALID:{\"actual_reps\": \"many\"}", "VALID:{\"actual_load\": -2}", "VALID:5",
    ])
    def test_log_bad_shapes_are_400_and_write_nothing(self, seeded, user, client, sets):
        import json

        p = _pullup(user)
        session = WorkoutSession.objects.create(user=user, program_day=p.day, scheduled_for=forge.local_today(user))
        sl = SetLog.objects.create(session=session, prescribed_exercise=p, exercise=p.exercise, set_index=0,
                                   is_amrap=True)
        if isinstance(sets, str) and sets.startswith("VALID:"):
            sets = {str(sl.uuid): json.loads(sets[6:])}
        r = client.post(f"/cauldron/api/sessions/{session.uuid}/log/", {"sets": sets}, format="json")
        assert r.status_code == 400
        session.refresh_from_db()
        sl.refresh_from_db()
        assert session.status == "planned" and sl.actual_reps is None

    def test_progression_route_only_accepts_accept_or_deny(self, seeded, user, client):
        p = _pullup(user)
        assert client.post(f"/cauldron/api/progression/{p.uuid}/foo/").status_code == 404
        assert client.post(f"/cauldron/api/progression/{p.uuid}/deny/").status_code == 200
        other = User.objects.create_user(username="other", email="o@example.com", password="pw12345!")
        theirs = _prescribe(other, _ex("Pull-up"))
        assert client.post(f"/cauldron/api/progression/{theirs.uuid}/accept/").status_code == 404

"""Set my load, and the weekly volume behind each session's sets (#109).

``set_load`` puts every live prescription of an exercise on a load the user can
build (snapping down); ``target_sets`` follows ``Program.weekly_volume_target``
through ``clamp(round(volume / frequency), 2, 5)`` and is regenerated per
pattern on rung, load and equipment changes. Rung, load and volume are also
MCP tools.
"""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.models import PrescribedExercise, Program, ProgramDay
from the_cauldron.services import forge
from the_cauldron.tests.test_mcp_server import _err, _ok, _token
from the_cauldron.tests.test_set_rung import _ex, _own, _program, _trial

User = get_user_model()


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="lifter", email="lifter@example.com", password="pw12345!")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _set_load(client, presc, load):
    return client.post(f"/cauldron/api/prescription/{presc.uuid}/set-load/", {"load": load}, format="json")


def _dumbbells(user, weights=(5, 10, 15)):
    return _own(user, "bodyweight", "dumbbells", dumbbell_weights=list(weights))


def _add_push_up(day):
    """A second pattern on the same served day, untouched by load changes."""
    ex = _ex("Push-up")
    return PrescribedExercise.objects.create(
        day=day, pattern=ex.pattern, exercise=ex, target_sets=3,
        target_reps_min=5, target_reps_max=12, target_rest_seconds=90, order=1,
    )


# ── Set my load ──────────────────────────────────────────────────────────────


class TestSetLoad:
    def test_snaps_down_silently_and_applies_program_wide(self, seeded, client, user):
        _dumbbells(user)
        prescs = _program(user, _ex("Dumbbell Row"), days=2, target_load=5, sessions_at_top=2)
        resp = _set_load(client, prescs[0], 12)
        assert resp.status_code == 200, resp.data
        assert resp.data == {"load": 10, "snapped_from": 12, "applied_to": 2}
        for p in prescs:
            p.refresh_from_db()
            assert p.target_load == 10 and p.sessions_at_top == 0 and p.pending_progression is None

    def test_exact_and_below_the_lightest(self, seeded, client, user):
        _dumbbells(user)
        [presc] = _program(user, _ex("Dumbbell Row"), target_load=5)
        assert _set_load(client, presc, 15).data == {"load": 15, "snapped_from": None, "applied_to": 1}
        assert _set_load(client, presc, 3).data["load"] == 5  # below the lightest → the lightest

    def test_the_same_load_keeps_earned_progress(self, seeded, client, user):
        _dumbbells(user)
        [presc] = _program(user, _ex("Dumbbell Row"), target_load=10, sessions_at_top=2)
        resp = _set_load(client, presc, 10)
        assert resp.status_code == 200 and resp.data["applied_to"] == 0
        presc.refresh_from_db()
        assert presc.sessions_at_top == 2

    def test_bands_take_an_owned_level_and_assisted_keeps_its_meaning(self, seeded, client, user):
        _own(user, "bodyweight", "bands", band_levels=["light", "medium", "heavy"])
        [presc] = _program(user, _ex("Band-Assisted Row"), target_load=2)
        with mock.patch("the_cauldron.api.posthog.capture") as capture:
            resp = _set_load(client, presc, 1)
        assert resp.status_code == 200 and resp.data["load"] == 1
        # Less help on an assisted rung is harder: "up".
        assert capture.call_args.kwargs["properties"] == {
            "pattern_key": "vertical_pull", "direction": "up", "source": "web"}
        assert _set_load(client, presc, 1.5).status_code == 400
        assert _set_load(client, presc, 7).status_code == 400

    @pytest.mark.parametrize("load", [0, -5, "abc", None])
    def test_non_positive_or_junk_load_is_400(self, seeded, client, user, load):
        _dumbbells(user)
        [presc] = _program(user, _ex("Dumbbell Row"), target_load=5)
        assert _set_load(client, presc, load).status_code == 400

    def test_difficulty_mode_is_400(self, seeded, client, user):
        _own(user, "bodyweight")
        [presc] = _program(user, _ex("Push-up"))
        assert _set_load(client, presc, 10).status_code == 400

    def test_logged_sets_today_are_409_and_untouched_sets_follow(self, seeded, client, user):
        _dumbbells(user)
        [presc] = _program(user, _ex("Dumbbell Row"), target_load=5)
        session = forge.start_session(user, presc.day)
        assert _set_load(client, presc, 10).status_code == 200
        rows = list(session.set_logs.order_by("set_index"))
        assert {r.expected_load for r in rows} == {10}
        presc.refresh_from_db()
        assert len(rows) == presc.target_sets and rows[-1].is_amrap
        first = rows[0]
        first.actual_reps = 8
        first.save()
        assert _set_load(client, presc, 15).status_code == 409

    def test_another_users_prescription_is_404(self, seeded, client, user):
        other = User.objects.create_user(username="other", email="other@example.com", password="pw12345!")
        _dumbbells(other)
        [presc] = _program(other, _ex("Dumbbell Row"), target_load=5)
        assert _set_load(client, presc, 10).status_code == 404


# ── Weekly volume ────────────────────────────────────────────────────────────


class TestVolume:
    def test_the_rule(self, db):
        program = Program(split=Program.Split.FULL_BODY_3X)
        freq = forge.weekly_frequency(program, 7)
        assert freq == pytest.approx(3 * 5 / 7)
        assert forge.sets_for_volume(16, freq) == 5  # 7.47 → capped at 5
        assert forge.sets_for_volume(6, freq) == 3  # 2.8 → 3
        assert forge.sets_for_volume(4, freq) == 2  # 1.87 → 2
        assert forge.sets_for_volume(1, freq) == 2  # floor at 2
        assert forge.weekly_frequency(Program(split=Program.Split.UPPER_LOWER_4X), 3) == 4  # all trained

    def test_default_is_16(self, db):
        assert Program._meta.get_field("weekly_volume_target").default == 16

    def test_new_programs_follow_the_volume(self, seeded, user):
        _own(user, "bodyweight")
        program = forge.generate_program(user, _trial(user, "horizontal_push", 10, placed=_ex("Push-up")))
        expected = forge.sets_for_volume(16, forge.weekly_frequency(program, 1))
        assert program.weekly_volume_target == 16
        assert {p.target_sets for p in ProgramDay.objects.get(program=program).prescriptions.all()} == {expected}

    def test_patch_regenerates_every_pattern_and_validates(self, seeded, client, user):
        _dumbbells(user)
        [row] = _program(user, _ex("Dumbbell Row"), target_load=5)
        push = _add_push_up(row.day)
        with mock.patch("the_cauldron.api.posthog.capture") as capture:
            resp = client.patch("/cauldron/api/program/", {"weekly_volume_target": 6}, format="json")
        assert resp.status_code == 200, resp.data
        assert capture.call_args.args[0] == "forge_volume_set"
        assert capture.call_args.kwargs["properties"]["source"] == "web"
        # Two patterns, both trained each of 3 sessions: 6 / 3 = 2 sets.
        for p in (row, push):
            p.refresh_from_db()
        assert (row.target_sets, push.target_sets) == (2, 2)
        for bad in (3, 41, "x", 16.9, True):
            assert client.patch("/cauldron/api/program/", {"weekly_volume_target": bad},
                                format="json").status_code == 400

    def test_only_the_touched_pattern_is_regenerated(self, seeded, client, user):
        _dumbbells(user)
        [row] = _program(user, _ex("Dumbbell Row"), target_load=5)
        push = _add_push_up(row.day)
        assert row.day.program.weekly_volume_target == 16
        assert _set_load(client, row, 10).status_code == 200
        for p in (row, push):
            p.refresh_from_db()
        assert row.target_sets == 5 and push.target_sets == 3  # 16 / 3 → 5 for the touched one only

    def test_unperformable_movements_do_not_count_towards_frequency(self, seeded, user):
        _dumbbells(user)
        [row] = _program(user, _ex("Dumbbell Row"), target_load=5)
        # Five trainable patterns plus a bench press the user can't do (no bench).
        for order, name in enumerate(["Push-up", "Pike Push-up", "Squat", "Plank", "Dumbbell Bench Press"], 1):
            ex = _ex(name)
            PrescribedExercise.objects.create(
                day=row.day, pattern=ex.pattern, exercise=ex, target_sets=3,
                target_reps_min=ex.rep_range_min, target_reps_max=ex.rep_range_max,
                target_rest_seconds=ex.rest_seconds, order=order,
            )
        program = row.day.program
        program.weekly_volume_target = 9
        program.save()
        forge.regenerate_volume(program)
        row.refresh_from_db()
        # 5 trainable → every pattern in each of 3 sessions: 9 / 3 = 3. Counting the
        # bench press would give 9 / (3 * 5/6) = 3.6 → 4.
        assert row.target_sets == 3

    def test_an_equipment_change_regenerates_the_changed_pattern(self, seeded, user):
        _dumbbells(user)
        [row] = _program(user, _ex("Dumbbell Row"), target_load=15)
        push = _add_push_up(row.day)
        _dumbbells(user, weights=(5, 10))  # the 15 is gone: the row's load re-snaps
        forge.sync_program_equipment(user)
        for p in (row, push):
            p.refresh_from_db()
        assert row.target_load == 10 and row.target_sets == 5
        assert push.target_sets == 3


# ── MCP ──────────────────────────────────────────────────────────────────────


class TestMcp:
    def test_set_forge_rung_climbs_past_the_trial_without_confirmation(self, seeded, user):
        _own(user, "bodyweight")
        _trial(user, "horizontal_push", 5)
        [presc] = _program(user, _ex("Knee Push-up"))
        diamond = _ex("Diamond Push-up")
        out = _ok(_token(user), "set_forge_rung", {"prescription_id": str(presc.uuid), "exercise_id": str(diamond.uuid)})
        assert out == {"exercise": "Diamond Push-up", "applied_to": 1}
        presc.refresh_from_db()
        assert presc.exercise == diamond

    def test_set_forge_load_matches_the_endpoint(self, seeded, user):
        _dumbbells(user)
        [presc] = _program(user, _ex("Dumbbell Row"), target_load=5)
        token = _token(user)
        assert _ok(token, "set_forge_load", {"prescription_id": str(presc.uuid), "load": 12}) == {
            "load": 10, "snapped_from": 12, "applied_to": 1}
        assert _err(token, "set_forge_load", {"prescription_id": str(presc.uuid), "load": 0})["code"] == "invalid_input"
        forge.start_session(user, presc.day).set_logs.update(actual_reps=8)
        assert _err(token, "set_forge_load", {"prescription_id": str(presc.uuid), "load": 15})["code"] == "conflict"

    def test_set_forge_weekly_volume(self, seeded, user):
        _dumbbells(user)
        [presc] = _program(user, _ex("Dumbbell Row"), target_load=5)
        token = _token(user)
        out = _ok(token, "set_forge_weekly_volume", {"sets_per_week": 9})
        assert out["weekly_volume_target"] == 9
        presc.refresh_from_db()
        assert presc.target_sets == 3
        assert _err(token, "set_forge_weekly_volume", {"sets_per_week": 41})["code"] == "invalid_arguments"

    def test_get_forge_rung_options(self, seeded, user):
        _own(user, "bodyweight")
        [presc] = _program(user, _ex("Knee Push-up"))
        chains = _ok(_token(user), "get_forge_rung_options")["chains"]
        assert chains[0]["prescription_id"] == str(presc.uuid)
        names = {o["exercise"]: o for o in chains[0]["options"]}
        assert names["Knee Push-up"]["is_current"] is True and "Push-up" in names

    def test_other_users_prescription_is_not_found(self, seeded, user):
        other = User.objects.create_user(username="other", email="other@example.com", password="pw12345!")
        _dumbbells(other)
        [presc] = _program(other, _ex("Dumbbell Row"), target_load=5)
        assert _err(_token(user), "set_forge_load", {"prescription_id": str(presc.uuid), "load": 10})["code"] == "not_found"

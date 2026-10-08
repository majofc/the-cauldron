"""#63 — the Forge kept stalling or running backwards in production.

Covers: the minimum load step, progressing from the heaviest logged load,
assisted bands running backwards, the top of the ladder (reps keep growing,
weighted rungs unlock once owned), timed holds growing to a 120 s cap, and
unfinished past sessions that used to drop their results silently.

The first half is pure engine logic (no DB); the rest drives the ORM and API.
"""

from datetime import timedelta
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
from the_cauldron.services import forge
from the_cauldron.services.progression import (
    SESSIONS_TO_ADVANCE,
    TIMED_CAP_SECONDS,
    available_loads,
    next_load_down,
    nearest_available_load,
    next_load_up,
    next_prescription,
    trial_seeded_load,
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Pure engine
# ─────────────────────────────────────────────────────────────────────────────


def rung(name="ex", mode="difficulty", rmin=5, rmax=12, timed=False, assisted=False,
         progression=None, regression=None, required=()):
    return NS(
        name=name, difficulty_rank=1, progression_mode=mode, rep_range_min=rmin,
        rep_range_max=rmax, is_timed=timed, is_per_side=False, is_assisted=assisted,
        placement_threshold=0, progression=progression, regression=regression,
        required_equipment=list(required),
    )


def presc(ex, rmin=None, rmax=None, load=None, at_top=0, sets=3):
    return NS(
        exercise=ex, target_sets=sets,
        target_reps_min=ex.rep_range_min if rmin is None else rmin,
        target_reps_max=ex.rep_range_max if rmax is None else rmax,
        target_load=load, sessions_at_top=at_top,
    )


def mixed_plate_bell(**over):
    """A plate-loaded kettlebell with the prod user's mixed denominations."""
    base = dict(
        equipment=["kettlebell"], kettlebell_mode="plates", kettlebell_weights=[],
        kettlebell_plates=[
            {"weight": 2.27, "count": 2}, {"weight": 2.0, "count": 2}, {"weight": 2.5, "count": 2},
        ],
        kettlebell_handle_weight=2.0, load_unit="kg",
    )
    base.update(over)
    return NS(**base)


def fixed_dumbbells(weights, unit="kg"):
    return NS(equipment=["dumbbells"], dumbbell_mode="fixed", dumbbell_weights=weights,
              dumbbell_plates=[], band_levels=[], load_unit=unit)


def bands(n=4):
    return NS(equipment=["bands"], band_levels=[f"b{i}" for i in range(n)], load_unit="kg")


LOADED = rung("bell", mode="load", rmin=6, rmax=10, required=["kettlebell"])
DB = rung("db", mode="load", rmin=6, rmax=10, required=["dumbbells"])
BAND = rung("band", mode="load", rmin=8, rmax=15, required=["bands"])


class TestLoadStep:
    def test_mixed_plates_skip_gram_steps(self):
        prof = mixed_plate_bell()
        options = available_loads(prof, LOADED)
        # The trap: something only grams heavier is buildable.
        assert any(8.54 < l < 8.54 + 1 for l in options)
        floor = 8.54 + max(0.025 * 8.54, 1.0)
        expected = min(l for l in options if l >= floor - 1e-9)
        assert next_load_up(prof, LOADED, 8.54) == expected == 10.54

    def test_lb_profile_uses_a_2_5_lb_floor(self):
        prof = fixed_dumbbells([20, 21, 22, 22.5, 25], unit="lb")
        assert next_load_up(prof, DB, 20) == 22.5

    def test_fixed_dumbbells_below_the_step_still_progress(self):
        assert next_load_up(fixed_dumbbells([10, 10.5]), DB, 10) == 10.5

    def test_top_of_inventory_holds(self):
        assert next_load_up(fixed_dumbbells([10, 12]), DB, 12) == 12

    def test_deload_mirrors_the_step(self):
        prof = fixed_dumbbells([5, 8, 8.5, 9])
        assert next_load_down(prof, DB, 9) == 8
        assert next_load_down(fixed_dumbbells([9.5, 10]), DB, 10) == 9.5
        assert next_load_down(prof, DB, 5) is None

    def test_bands_move_one_level(self):
        assert next_load_up(bands(), BAND, 1) == 2
        assert next_load_down(bands(), BAND, 2) == 1

    def test_engine_deload_uses_the_step(self):
        r = next_prescription(presc(DB, load=9), 3, fixed_dumbbells([5, 8, 8.5, 9]))
        assert r.target_load == 8


class TestAssistedBands:
    def test_progress_lowers_the_band(self):
        ex = rung("Band-Assisted Row", mode="load", rmin=8, rmax=15, assisted=True,
                  required=["bands"])
        r = next_prescription(presc(ex, load=2), 15, bands())
        assert r.target_load == 1
        assert not r.advanced

    def test_lightest_band_at_top_unlocks_next_rung(self):
        nxt = rung("Australian Row", rmin=8, rmax=15)
        ex = rung("Band-Assisted Row", mode="load", rmin=8, rmax=15, assisted=True,
                  required=["bands"])
        r = next_prescription(presc(ex, load=0), 25, bands(), next_rung=nxt)
        assert r.advanced and r.exercise is nxt

    def test_deload_adds_assistance(self):
        ex = rung("Band-Assisted Row", mode="load", rmin=8, rmax=15, assisted=True,
                  required=["bands"])
        r = next_prescription(presc(ex, load=0), 4, bands())
        assert r.target_load == 1

    def test_starting_load_is_the_most_assistance(self):
        ex = rung("Band-Assisted Row", mode="load", rmin=8, rmax=15, assisted=True,
                  required=["bands"])
        assert nearest_available_load(bands(), ex, None) == 3
        # A stronger Trial means less help, not more.
        weak = trial_seeded_load(bands(), NS(**{**vars(ex), "placement_threshold": 10}), 10)
        strong = trial_seeded_load(bands(), NS(**{**vars(ex), "placement_threshold": 10}), 30)
        assert weak == 3 and strong < weak

    def test_resisted_bands_still_climb(self):
        r = next_prescription(presc(BAND, load=1), 15, bands())
        assert r.target_load == 2


class TestTopOfLadder:
    def test_top_rung_extends_reps_after_sustained_top(self):
        dragon = rung("Dragon Squat", rmin=1, rmax=5)
        r = next_prescription(presc(dragon, at_top=SESSIONS_TO_ADVANCE - 1), 11, None, next_rung=None)
        assert (r.exercise, r.target_reps_max, r.sessions_at_top) == (dragon, 6, 0)

    def test_extension_has_no_cap(self):
        dragon = rung("Dragon Squat", rmin=1, rmax=5)
        r = next_prescription(presc(dragon, rmax=30, at_top=1), 31, None, next_rung=None)
        assert r.target_reps_max == 31

    def test_outgrown_rung_unlocks_as_soon_as_a_harder_one_is_performable(self):
        weighted = rung("Weighted Pistol Squat", mode="load", rmin=3, rmax=8)
        dragon = rung("Dragon Squat", rmin=1, rmax=5, progression=weighted)
        r = next_prescription(presc(dragon, rmax=25, at_top=0), 26, None, next_rung=weighted)
        assert r.advanced and r.exercise is weighted

    def test_unextended_rung_still_needs_sustained_top(self):
        nxt = rung("harder")
        ex = rung("easy", rmin=8, rmax=15, progression=nxt)
        r = next_prescription(presc(ex, at_top=0), 30, None)
        assert not r.advanced and r.sessions_at_top == 1

    def test_heaviest_load_extends_reps(self):
        r = next_prescription(presc(DB, load=12), 10, fixed_dumbbells([10, 12]))
        assert (r.target_load, r.target_reps_max) == (12, 11)


class TestTimedHolds:
    @pytest.mark.parametrize("rmax, achieved, expected", [(45, 135, 120), (30, 32, 35), (30, 60, 54)])
    def test_hold_target_grows(self, rmax, achieved, expected):
        hold = rung("Plank", rmin=20, rmax=rmax, timed=True, progression=rung("next"))
        r = next_prescription(presc(hold), achieved, None)
        assert r.target_reps_max == expected
        assert not r.advanced

    def test_capped_hold_advances_like_a_top_of_range(self):
        nxt = rung("Extended Plank")
        hold = rung("Plank", rmin=20, rmax=60, timed=True, progression=nxt)
        r = next_prescription(presc(hold, rmax=TIMED_CAP_SECONDS, at_top=1), 125, None)
        assert r.advanced and r.exercise is nxt

    def test_capped_hold_still_needs_sustained_top(self):
        # Seconds are not reps: 125 s must not trip the 25-rep fast track.
        hold = rung("Plank", rmin=20, rmax=60, timed=True, progression=rung("Extended Plank"))
        r = next_prescription(presc(hold, rmax=TIMED_CAP_SECONDS, at_top=0), 125, None)
        assert not r.advanced and r.sessions_at_top == 1

    def test_capped_hold_without_a_next_rung_holds_ready(self):
        hold = rung("One-Arm Towel Hang", rmin=6, rmax=20, timed=True)
        r = next_prescription(presc(hold, rmax=TIMED_CAP_SECONDS, at_top=1), 125, None, next_rung=None)
        assert r.target_reps_max == TIMED_CAP_SECONDS
        assert r.sessions_at_top == SESSIONS_TO_ADVANCE


# ─────────────────────────────────────────────────────────────────────────────
# ORM / API
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


@pytest.fixture
def user(db):
    return User.objects.create_user(username="forger63", email="forger63@example.com", password="pw12345!")


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
        target_sets=3, target_reps_min=exercise.rep_range_min,
        target_reps_max=exercise.rep_range_max, target_rest_seconds=exercise.rest_seconds, order=0,
    )
    defaults.update(fields)
    return PrescribedExercise.objects.create(day=day, pattern=exercise.pattern, exercise=exercise, **defaults)


def _session(user, p, scheduled_for=None, values=None):
    """A planned session with three sets of ``p``; ``values`` maps set index →
    (reps, load) already logged on it."""
    session = WorkoutSession.objects.create(
        user=user, program_day=p.day, status=WorkoutSession.Status.PLANNED,
        scheduled_for=scheduled_for or timezone.now().date(),
    )
    for i in range(3):
        reps, load = (values or {}).get(i, (None, None))
        SetLog.objects.create(
            session=session, prescribed_exercise=p, exercise=p.exercise, set_index=i,
            expected_reps=p.target_reps_min, expected_load=p.target_load, is_amrap=i == 2,
            actual_reps=reps, actual_load=load,
        )
    return session


def _log(session, reps, loads):
    sets = {
        str(s.uuid): {"actual_reps": reps, "actual_load": loads[s.set_index]}
        for s in session.set_logs.order_by("set_index")
    }
    return forge.apply_session_log(session, sets)


class TestCatalog:
    def test_assisted_flag_is_seeded(self, seeded):
        assert _ex("Band-Assisted Row").is_assisted
        assert _ex("Band-Assisted Pull-up").is_assisted
        assert not _ex("Band Rollout").is_assisted

    def test_weighted_rungs_sit_past_the_bodyweight_top(self, seeded):
        dragon = _ex("Dragon Squat")
        assert dragon.progression.name == "Weighted Pistol Squat"
        assert dragon.progression.progression.name == "Weighted Dragon Squat"
        assert dragon.progression.progression_mode == "load"


class TestHeaviestLoggedLoad:
    def test_in_range_session_holds_at_the_heavier_logged_load(self, seeded, user):
        _own(user, "dumbbells", dumbbell_weights=[5, 10, 20, 22.5])
        p = _prescribe(user, _ex("Dumbbell Romanian Deadlift"), target_load=5.0)
        _log(_session(user, p), 10, [5.0, 19.08, 5.0])
        p.refresh_from_db()
        assert p.target_load == 20  # 19.08 snapped to the nearest bell

    def test_top_of_range_steps_up_from_the_heavier_logged_load(self, seeded, user):
        _own(user, "dumbbells", dumbbell_weights=[5, 10, 20, 22.5])
        p = _prescribe(user, _ex("Dumbbell Romanian Deadlift"), target_load=5.0)
        _log(_session(user, p), 12, [20.0, 20.0, 20.0])
        p.refresh_from_db()
        assert p.target_load == 22.5

    def test_lighter_logged_load_does_not_lower_the_target(self, seeded, user):
        _own(user, "dumbbells", dumbbell_weights=[5, 10, 20])
        p = _prescribe(user, _ex("Dumbbell Romanian Deadlift"), target_load=10.0)
        _log(_session(user, p), 10, [5.0, 5.0, 5.0])
        p.refresh_from_db()
        assert p.target_load == 10


class TestAssistedSessions:
    def test_band_assisted_pullup_at_lightest_band_unlocks_the_pullup(self, seeded, user):
        _own(user, "bands", "pullup_bar", "bodyweight", band_levels=["light", "medium", "heavy"])
        p = _prescribe(user, _ex("Band-Assisted Pull-up"), target_load=0.0)
        deltas = _log(_session(user, p), p.target_reps_max, [0.0, 0.0, 0.0])
        p.refresh_from_db()
        assert p.pending_progression.name == "Pull-up"
        assert any("unlock" in d for d in deltas)

    def test_band_assisted_row_moves_to_a_lighter_band(self, seeded, user):
        _own(user, "bands", band_levels=["light", "medium", "heavy"])
        p = _prescribe(user, _ex("Band-Assisted Row"), target_load=2.0)
        _log(_session(user, p), p.target_reps_max, [2.0, 2.0, 2.0])
        p.refresh_from_db()
        assert p.target_load == 1


class TestTopOfLadderSessions:
    def test_dragon_squat_without_weights_keeps_extending(self, seeded, user):
        _own(user, "bodyweight")
        p = _prescribe(user, _ex("Dragon Squat"), target_reps_max=26, sessions_at_top=1)
        _log(_session(user, p), 30, [None, None, None])
        p.refresh_from_db()
        assert p.exercise.name == "Dragon Squat"
        assert p.target_reps_max == 28  # +1, rounded up to even (per side)
        assert p.pending_progression is None

    def test_adding_dumbbells_unlocks_the_weighted_rung(self, seeded, user):
        _own(user, "bodyweight", "dumbbells", dumbbell_weights=[4, 6, 8])
        p = _prescribe(user, _ex("Dragon Squat"), target_reps_max=26, sessions_at_top=0)
        _log(_session(user, p), 27, [None, None, None])
        p.refresh_from_db()
        assert p.pending_progression.name == "Weighted Pistol Squat"


class TestUnfinishedSessions:
    def _setup(self, user):
        _own(user, "bodyweight")
        return _prescribe(user, _ex("Pistol Squat"))

    def test_only_newest_is_offered_and_older_are_skipped(self, seeded, user, client):
        p = self._setup(user)
        today = timezone.now().date()
        old = _session(user, p, today - timedelta(days=5), {0: (5, None)})
        new = _session(user, p, today - timedelta(days=2), {0: (6, None)})
        resp = client.get("/cauldron/api/sessions/unfinished/")
        assert resp.status_code == 200
        assert resp.json()["session"]["uuid"] == str(new.uuid)
        old.refresh_from_db()
        assert old.status == WorkoutSession.Status.SKIPPED
        assert old.set_logs.filter(actual_reps=5).exists()  # kept as history

    def test_today_is_gated_until_resolved(self, seeded, user, client):
        p = self._setup(user)
        _session(user, p, timezone.now().date() - timedelta(days=1), {0: (5, None)})
        resp = client.get("/cauldron/api/today/")
        assert resp.status_code == 409
        assert resp.json()["code"] == "unfinished_session"
        assert client.post("/cauldron/api/sessions/", {"sets": []}, format="json").status_code == 409

    def test_save_completes_on_its_own_date_and_progresses(self, seeded, user, client):
        p = self._setup(user)
        day = timezone.now().date() - timedelta(days=3)
        s = _session(user, p, day, {0: (5, None)})
        sets = {
            str(x.uuid): {"actual_reps": p.target_reps_max, "actual_load": None}
            for x in s.set_logs.all()
        }
        resp = client.post(f"/cauldron/api/sessions/{s.uuid}/log/", {"sets": sets}, format="json")
        assert resp.status_code == 200
        s.refresh_from_db()
        assert s.status == WorkoutSession.Status.COMPLETED
        assert timezone.localtime(s.performed_at).date() == day
        assert s.set_logs.get(set_index=0).actual_reps == p.target_reps_max  # edit persisted
        p.refresh_from_db()
        assert p.sessions_at_top == 1
        assert client.get("/cauldron/api/today/").status_code == 200

    def test_delete_removes_session_and_sets_only(self, seeded, user, client):
        p = self._setup(user)
        s = _session(user, p, timezone.now().date() - timedelta(days=1), {2: (8, None)})
        before = PrescribedExercise.objects.filter(pk=p.pk).values().get()
        resp = client.delete(f"/cauldron/api/sessions/{s.uuid}/")
        assert resp.status_code == 204
        assert not WorkoutSession.objects.filter(pk=s.pk).exists()
        assert not SetLog.objects.filter(session_id=s.pk).exists()
        assert PrescribedExercise.objects.filter(pk=p.pk).values().get() == before
        assert client.get("/cauldron/api/sessions/unfinished/").json()["session"] is None

    def test_completed_sessions_cannot_be_deleted(self, seeded, user, client):
        p = self._setup(user)
        s = _session(user, p)
        s.status = WorkoutSession.Status.COMPLETED
        s.save()
        assert client.delete(f"/cauldron/api/sessions/{s.uuid}/").status_code == 409

    def test_other_users_session_is_not_reachable(self, seeded, user, client):
        other = User.objects.create_user(username="other63", email="other63@example.com", password="pw12345!")
        _own(other, "bodyweight")
        p = _prescribe(other, _ex("Pistol Squat"))
        s = _session(other, p, timezone.now().date() - timedelta(days=1), {0: (5, None)})
        assert client.delete(f"/cauldron/api/sessions/{s.uuid}/").status_code == 404
        assert client.get("/cauldron/api/sessions/unfinished/").json()["session"] is None

    def test_sessions_superseded_by_a_completed_one_are_closed(self, seeded, user, client):
        p = self._setup(user)
        today = timezone.now().date()
        stale = _session(user, p, today - timedelta(days=4), {0: (5, None)})
        done = _session(user, p, today - timedelta(days=2))
        done.status = WorkoutSession.Status.COMPLETED
        done.save()
        assert client.get("/cauldron/api/today/").status_code == 200
        assert client.get("/cauldron/api/sessions/unfinished/").json()["session"] is None
        stale.refresh_from_db()
        assert stale.status == WorkoutSession.Status.SKIPPED

    def test_sessions_from_a_retired_program_are_closed(self, seeded, user, client):
        p = self._setup(user)
        old = _session(user, p, timezone.now().date() - timedelta(days=1), {0: (5, None)})
        Program.objects.filter(user=user).update(is_active=False)
        _prescribe(user, _ex("Pistol Squat"))  # the replacement program
        assert client.get("/cauldron/api/today/").status_code == 200
        assert client.get("/cauldron/api/sessions/unfinished/").json()["session"] is None
        old.refresh_from_db()
        assert old.status == WorkoutSession.Status.SKIPPED

    def test_todays_session_and_empty_past_sessions_never_trigger(self, seeded, user, client):
        p = self._setup(user)
        _session(user, p, timezone.now().date(), {0: (5, None)})
        _session(user, p, timezone.now().date() - timedelta(days=1))
        assert client.get("/cauldron/api/sessions/unfinished/").json()["session"] is None
        assert client.get("/cauldron/api/today/").status_code == 200

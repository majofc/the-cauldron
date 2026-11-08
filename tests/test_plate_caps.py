"""Disc capacity of plate-loaded implements.

A barbell sleeve, a dumbbell handle or a kettlebell shell only takes so many
discs. ``barbell_max_plates_per_side`` / ``dumbbell_max_plates_per_side`` /
``kettlebell_max_plates`` (null = no limit) drop every load whose fewest-plates
recipe needs more discs on a side, and a load that only the cap blocks moves UP
to the next one that fits.
"""

from types import SimpleNamespace as NS

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.services import forge, loads, progression
from the_cauldron.tests.test_set_rung import _ex, _own, _program

User = get_user_model()

# Per side: 20 ×2, 10, 5, 2.5. Uncapped, 85 = 20 + 2 × (20 + 10 + 2.5) → 3 discs a side.
BAR_PLATES = [{"weight": 20, "count": 4}, {"weight": 10, "count": 2},
              {"weight": 5, "count": 2}, {"weight": 2.5, "count": 2}]
# At most 2 discs a side.
BAR_TOTALS_CAP_2 = [20, 25, 30, 35, 40, 45, 50, 60, 65, 70, 80, 100]


def barbell(**over):
    base = dict(equipment=["barbell"], barbell_plates=BAR_PLATES, bar_weight=20.0,
                barbell_min_increment=2.5, barbell_max_plates_per_side=None)
    base.update(over)
    return NS(**base)


def ex(*required):
    return NS(required_equipment=list(required), progression_mode="load", is_assisted=False,
              placement_threshold=0)


# ── Engine ───────────────────────────────────────────────────────────────────


class TestEngine:
    def test_no_cap_is_limitless(self):
        totals = [l.total for l in loads.barbell_loads(barbell())]
        assert 85 in totals and max(totals) == 20 + 2 * 57.5

    def test_cap_drops_loads_needing_more_discs_a_side(self):
        prof = barbell(barbell_max_plates_per_side=2)
        built = loads.barbell_loads(prof)
        assert [l.total for l in built] == BAR_TOTALS_CAP_2
        assert all(sum(p["count"] for p in l.per_side) <= 2 for l in built)

    def test_a_total_survives_when_some_recipe_fits(self):
        # 60 = bar + 2 × 20 (one disc) — fits a 1-disc cap even though 2 × (10 + 5 + 5)
        # style recipes would not.
        totals = [l.total for l in loads.barbell_loads(barbell(barbell_max_plates_per_side=1))]
        assert totals == [20, 25, 30, 40, 60]

    def test_ignore_cap(self):
        prof = barbell(barbell_max_plates_per_side=2)
        assert loads.recipe_for(prof, ex("barbell"), 85) is None
        assert loads.recipe_for(prof, ex("barbell"), 85, ignore_cap=True) is not None

    def test_dumbbell_cap_is_per_end(self):
        prof = NS(equipment=["dumbbells"], dumbbell_mode="plates", dumbbell_weights=[],
                  dumbbell_plates=[{"weight": 2.5, "count": 8}, {"weight": 1.25, "count": 4}],
                  dumbbell_handle_weight=2.0, dumbbell_max_plates_per_side=1)
        assert [l.total for l in loads.dumbbell_loads(prof)] == [2.0, 4.5, 7.0]

    def test_kettlebell_cap_counts_the_shell(self):
        prof = NS(equipment=["kettlebell"], kettlebell_mode="plates", kettlebell_weights=[],
                  kettlebell_plates=[{"weight": 2, "count": 6}], kettlebell_handle_weight=6.0,
                  kettlebell_max_plates=3)
        assert [l.total for l in loads.kettlebell_loads(prof)] == [6, 8, 10, 12]

    @pytest.mark.parametrize("raw", [None, 0, -1, "junk"])
    def test_unreadable_cap_means_no_limit(self, raw):
        assert loads.max_plates(barbell(barbell_max_plates_per_side=raw), "barbell") is None

    def test_cap_does_not_touch_fixed_weights(self):
        prof = NS(equipment=["dumbbells"], dumbbell_mode="fixed", dumbbell_weights=[5, 10],
                  dumbbell_max_plates_per_side=1)
        assert [l.total for l in loads.dumbbell_loads(prof)] == [5, 10]


# ── Snapping: blocked by the cap → up ────────────────────────────────────────


class TestSnapping:
    def test_cap_blocked_load_goes_up(self):
        prof = barbell(barbell_max_plates_per_side=2)
        assert progression.blocked_by_plate_cap(prof, ex("barbell"), 85)
        assert progression.nearest_available_load(prof, ex("barbell"), 85) == 100

    def test_load_the_plates_cant_make_keeps_nearest(self):
        # 86 isn't buildable even without the cap: the usual nearest (ties lighter).
        prof = barbell(barbell_max_plates_per_side=2)
        assert not progression.blocked_by_plate_cap(prof, ex("barbell"), 86)
        assert progression.nearest_available_load(prof, ex("barbell"), 86) == 80

    def test_beyond_the_cap_tops_out_at_the_heaviest_that_fits(self):
        prof = barbell(barbell_max_plates_per_side=2)
        assert progression.nearest_available_load(prof, ex("barbell"), 135) == 100

    def test_progression_steps_only_through_loads_that_fit(self):
        prof = barbell(barbell_max_plates_per_side=2)
        assert progression.next_load_up(prof, ex("barbell"), 80) == 100


# ── API, set my load, equipment resnap ───────────────────────────────────────


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


def _bar(user, cap=None):
    return _own(user, "bodyweight", "barbell", barbell_plates=BAR_PLATES, bar_weight=20.0,
                barbell_max_plates_per_side=cap)


class TestApi:
    def test_round_trip_and_null_is_no_limit(self, seeded, client, user):
        _bar(user)
        resp = client.put("/cauldron/api/equipment/", {"barbell_max_plates_per_side": 2,
                                                        "dumbbell_max_plates_per_side": 4,
                                                        "kettlebell_max_plates": 3}, format="json")
        assert resp.status_code == 200, resp.data
        assert (resp.data["barbell_max_plates_per_side"], resp.data["dumbbell_max_plates_per_side"],
                resp.data["kettlebell_max_plates"]) == (2, 4, 3)
        resp = client.put("/cauldron/api/equipment/", {"barbell_max_plates_per_side": None}, format="json")
        assert resp.status_code == 200 and resp.data["barbell_max_plates_per_side"] is None

    @pytest.mark.parametrize("value", [0, 101])
    def test_out_of_range_cap_is_400(self, seeded, client, user, value):
        _bar(user)
        resp = client.put("/cauldron/api/equipment/", {"barbell_max_plates_per_side": value}, format="json")
        assert resp.status_code == 400

    def test_set_load_blocked_by_the_cap_snaps_up(self, seeded, client, user):
        _bar(user, cap=2)
        [presc] = _program(user, _ex("Barbell Back Squat"), target_load=60)
        resp = client.post(f"/cauldron/api/prescription/{presc.uuid}/set-load/", {"load": 85}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["load"] == 100 and resp.data["snapped_from"] == 85

    def test_set_load_unbuildable_anyway_still_snaps_down(self, seeded, client, user):
        _bar(user, cap=2)
        [presc] = _program(user, _ex("Barbell Back Squat"), target_load=60)
        resp = client.post(f"/cauldron/api/prescription/{presc.uuid}/set-load/", {"load": 86}, format="json")
        assert resp.data["load"] == 80

    def test_adding_a_cap_moves_live_prescriptions_up(self, seeded, client, user):
        _bar(user)
        [presc] = _program(user, _ex("Barbell Back Squat"), target_load=85)
        resp = client.put("/cauldron/api/equipment/", {"barbell_max_plates_per_side": 2}, format="json")
        assert resp.status_code == 200, resp.data
        forge.sync_program_equipment(user)  # what the next today/ read runs
        presc.refresh_from_db()
        assert presc.target_load == 100

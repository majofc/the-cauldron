"""Unit tests for the 30%-reduced rest prescriptions. No DB required."""

import importlib

from the_cauldron.management.commands.seed_forge import rest_for

_migration = importlib.import_module(
    "the_cauldron.migrations.0007_reduce_rest_seconds_30pct"
)
REDUCED = _migration.REDUCED


class TestRestFor:
    def test_holds_get_shortest_rest(self):
        assert rest_for("difficulty", 20, 40, timed=True) == 40

    def test_heavy_low_rep_gets_most_rest(self):
        assert rest_for("difficulty", 3, 6, timed=False) == 105

    def test_loaded_up_to_ten_reps_counts_as_heavy(self):
        assert rest_for("load", 6, 10, timed=False) == 105

    def test_hypertrophy_range(self):
        assert rest_for("difficulty", 8, 12, timed=False) == 85

    def test_endurance_range(self):
        assert rest_for("difficulty", 12, 20, timed=False) == 55


class TestReducedMap:
    def test_maps_old_baselines_to_new_rest_for_values(self):
        # Every old baseline maps onto the exact value rest_for now emits.
        assert REDUCED == {150: 105, 120: 85, 75: 55, 60: 40}

    def test_each_entry_is_roughly_a_thirty_percent_cut(self):
        for old, new in REDUCED.items():
            assert abs(new - old * 0.7) <= 2.5  # within one half-step of 30%

    def test_new_values_round_to_nearest_five(self):
        for new in REDUCED.values():
            assert new % 5 == 0

    def test_idempotent_new_values_are_not_remapped(self):
        # Already-reduced values must be absent from the map so a re-run of the
        # migration leaves them untouched.
        for new in REDUCED.values():
            assert new not in REDUCED

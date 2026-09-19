"""The grip pattern (#61): its two ladders, the any-of equipment rule, the
second Trial anchor, grip-specific rest and the Dead Hang norm.

Placement and equipment behaviour are asserted through the real seeded catalog —
the point of the ticket is that a bar-less user and a rings-only user both get a
grip row they can actually perform.
"""

import pytest
from django.core.management import call_command

from the_cauldron.models import Equipment, Exercise, MovementPattern
from the_cauldron.services import forge, norms
from the_cauldron.services.progression import place_from_assessment


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


def _get(name):
    return Exercise.objects.get(name=name)


def _grip_ladder(owned, mode="difficulty"):
    """The grip rungs a user owning ``owned`` can perform, easiest first."""
    pattern = MovementPattern.objects.get(key="grip")
    return sorted(
        (
            ex
            for ex in pattern.exercises.filter(progression_mode=mode)
            if forge.is_performable(ex, set(owned))
        ),
        key=lambda e: e.difficulty_rank,
    )


class TestLadder:
    def test_seeds_both_chains(self, seeded):
        pattern = MovementPattern.objects.get(key="grip")
        difficulty = pattern.exercises.filter(progression_mode="difficulty")
        load = pattern.exercises.filter(progression_mode="load")
        assert difficulty.count() == 9
        assert load.count() == 4
        assert _get("Towel Wring Hold").difficulty_rank == 1
        assert _get("One-Arm Towel Hang").progression is None
        assert _get("Double-Overhand Barbell Hold").progression is None

    def test_every_rung_is_timed(self, seeded):
        # The whole pattern lives in seconds, on its anchor's scale.
        pattern = MovementPattern.objects.get(key="grip")
        assert all(ex.is_timed for ex in pattern.exercises.all())

    def test_seed_is_idempotent(self, seeded):
        pattern = MovementPattern.objects.get(key="grip")
        before = {
            (e.name, e.difficulty_rank, e.placement_threshold, e.rest_seconds)
            for e in pattern.exercises.all()
        }
        call_command("seed_forge")
        after = {
            (e.name, e.difficulty_rank, e.placement_threshold, e.rest_seconds)
            for e in MovementPattern.objects.get(key="grip").exercises.all()
        }
        assert before == after
        assert MovementPattern.objects.filter(key="grip").count() == 1


class TestPlacementReachability:
    """Every rung must be reachable by the Trial walk.

    ``test_lower_ladder.test_placement_thresholds_never_dip_as_rank_climbs``
    already enforces the threshold rule (non-decreasing by rank, equal for
    same-rank rungs) across every pattern — including the two grip chains, which
    is what the ticket asks for. What it cannot see is the consequence: because
    ``eligible_exercises`` hands placement BOTH chains of a pattern at once, a
    load rung whose threshold disagrees with the difficulty rung at its rank
    truncates the walk. This asserts the outcome directly.
    """

    def test_every_hang_rung_is_reachable_by_some_score(self, seeded):
        owned = {"bodyweight", "pullup_bar", "fat_grips", "barbell", "dumbbells"}
        pattern = MovementPattern.objects.get(key="grip")
        ladder = [
            ex for ex in pattern.exercises.all() if forge.is_performable(ex, owned)
        ]
        reached = {
            place_from_assessment(ladder, score).name for score in range(0, 200)
        }
        # Nothing in the difficulty chain is stranded above a truncated walk.
        for name in ("Towel Bar Hang", "Fat-Grip Hang", "Plate Pinch Hold",
                     "Assisted One-Arm Hang", "One-Arm Dead Hang",
                     "One-Arm Towel Hang"):
            assert name in reached, f"{name} is unreachable by any Trial score"

    def test_a_dumbbell_owner_still_climbs_past_rank_one(self, seeded):
        # The carries sit at ranks 1-3; owning dumbbells must not pin the user
        # to the bottom of the hang ladder.
        owned = {"bodyweight", "pullup_bar", "dumbbells"}
        pattern = MovementPattern.objects.get(key="grip")
        ladder = [
            ex for ex in pattern.exercises.all() if forge.is_performable(ex, owned)
        ]
        assert place_from_assessment(ladder, 50).difficulty_rank >= 4


class TestEquipmentAnyOf:
    def test_bar_only_user_can_climb_the_hangs(self, seeded):
        names = [e.name for e in _grip_ladder({"bodyweight", "pullup_bar"})]
        assert "Assisted Bar Hang" in names
        assert "Dead Hang" in names
        assert "One-Arm Dead Hang" in names

    def test_rings_only_user_can_climb_the_same_hangs(self, seeded):
        names = [e.name for e in _grip_ladder({"bodyweight", "rings"})]
        # Owning rings alone is enough for every bar-OR-rings rung: no step in
        # the ladder asks the user to switch implement.
        assert "Assisted Bar Hang" in names
        assert "Dead Hang" in names
        assert "Assisted One-Arm Hang" in names
        assert "One-Arm Dead Hang" in names
        # Towel rungs genuinely need a bar to throw the towel over.
        assert "Towel Bar Hang" not in names

    def test_user_with_nowhere_to_hang_gets_only_the_wring(self, seeded):
        names = [e.name for e in _grip_ladder({"bodyweight"})]
        assert names == ["Towel Wring Hold"]

    def test_required_equipment_is_still_all_of(self, seeded):
        # Fat-Grip Hang needs the bar AND the grips — any-of never loosens that.
        assert not forge.is_performable(
            _get("Fat-Grip Hang"), {"bodyweight", "pullup_bar"}
        )
        assert forge.is_performable(
            _get("Fat-Grip Hang"), {"bodyweight", "pullup_bar", "fat_grips"}
        )

    def test_fat_grips_equipment_exists(self, seeded):
        fat_grips = Equipment.objects.get(key="fat_grips")
        assert fat_grips.is_loadable is False
        assert not Equipment.objects.filter(key="towel").exists()


class TestPlacement:
    def test_bar_less_user_is_placed_on_the_wring(self, seeded):
        placed = place_from_assessment(_grip_ladder({"bodyweight"}), 40)
        assert placed.name == "Towel Wring Hold"

    def test_bar_owner_below_the_hang_threshold_lands_on_assisted(self, seeded):
        # Ranks 1 and 2 both sit at threshold 0; the bar owner takes the higher.
        placed = place_from_assessment(_grip_ladder({"bodyweight", "pullup_bar"}), 10)
        assert placed.name == "Assisted Bar Hang"

    def test_a_strong_hang_places_higher_up(self, seeded):
        placed = place_from_assessment(_grip_ladder({"bodyweight", "pullup_bar"}), 50)
        assert placed.name == "Towel Bar Hang"


class TestAnchors:
    def test_grip_has_two_anchors_and_takes_the_hardest_performable(self, seeded):
        grip = MovementPattern.objects.get(key="grip")
        anchors = {
            e.name for e in grip.exercises.filter(is_assessment_anchor=True)
        }
        assert anchors == {"Towel Wring Hold", "Dead Hang"}
        assert forge._anchor_for(grip.pk, {"bodyweight", "pullup_bar"}).name == "Dead Hang"
        assert forge._anchor_for(grip.pk, {"bodyweight", "rings"}).name == "Dead Hang"
        assert forge._anchor_for(grip.pk, {"bodyweight"}).name == "Towel Wring Hold"

    def test_other_patterns_still_resolve_exactly_one_anchor(self, seeded):
        for pattern in MovementPattern.objects.exclude(key="grip"):
            anchors = list(pattern.exercises.filter(is_assessment_anchor=True))
            assert len(anchors) == 1, f"{pattern.key}: {[a.name for a in anchors]}"
            assert forge._anchor_for(pattern.pk, {"bodyweight"}) == anchors[0]


class TestRest:
    def test_grip_holds_rest_longer_than_other_holds(self, seeded):
        for name in ("Dead Hang", "Towel Wring Hold", "Farmer Walk"):
            assert _get(name).rest_seconds == 90
        for name in ("Plank", "Hollow Body Hold", "Wall Handstand Hold"):
            assert _get(name).rest_seconds == 40


class TestNorms:
    def test_dead_hang_is_scored_and_flagged_estimated(self, seeded):
        score = norms.score("Dead Hang", 45, age=30, sex="male")
        assert score.has_data
        assert score.confidence == "estimated"
        assert score.estimated is True

    def test_towel_wring_has_no_peer_data(self, seeded):
        assert norms.score("Towel Wring Hold", 30, age=30, sex="male").has_data is False


class TestPerSide:
    def test_one_arm_hangs_are_per_side(self, seeded):
        for name in ("Assisted One-Arm Hang", "One-Arm Dead Hang",
                     "One-Arm Towel Hang", "Suitcase Carry"):
            assert _get(name).is_per_side is True
        for name in ("Dead Hang", "Farmer Hold", "Farmer Walk"):
            assert _get(name).is_per_side is False

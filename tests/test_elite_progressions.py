"""The elite bodyweight rungs added above Archer Push-up and Pistol Squat must
seed, link onto the end of their difficulty ladders, and carry metadata."""

import pytest
from django.core.management import call_command

from the_cauldron.models import Exercise


@pytest.fixture
def seeded(db):
    call_command("seed_forge")


def _get(name):
    return Exercise.objects.get(name=name)


class TestHorizontalPushTop:
    def test_chains_after_archer_pushup(self, seeded):
        archer = _get("Archer Push-up")
        typewriter = _get("Typewriter Push-up")
        one_arm = _get("One-Arm Push-up")
        # Ladder order by rank: Archer(7) → Typewriter(8) → One-Arm(9).
        assert archer.progression == typewriter
        assert typewriter.regression == archer
        assert typewriter.progression == one_arm
        assert one_arm.regression == typewriter
        assert one_arm.progression is None  # new top of the bodyweight ladder

    def test_are_bodyweight_difficulty_rungs(self, seeded):
        for name in ("Typewriter Push-up", "One-Arm Push-up"):
            ex = _get(name)
            assert ex.progression_mode == Exercise.ProgressionMode.DIFFICULTY
            assert [e.key for e in ex.required_equipment.all()] == ["bodyweight"]
            assert ex.difficulty_rank > _get("Archer Push-up").difficulty_rank


class TestLowerUnilateralTop:
    def test_chains_after_pistol_squat(self, seeded):
        pistol = _get("Pistol Squat")
        shrimp = _get("Shrimp Squat")
        dragon = _get("Dragon Squat")
        # Ladder order by rank: Pistol(5) → Shrimp(6) → Dragon(7).
        assert pistol.progression == shrimp
        assert shrimp.regression == pistol
        assert shrimp.progression == dragon
        assert dragon.regression == shrimp
        assert dragon.progression is None  # new top of the bodyweight ladder

    def test_are_bodyweight_difficulty_rungs(self, seeded):
        for name in ("Shrimp Squat", "Dragon Squat"):
            ex = _get(name)
            assert ex.progression_mode == Exercise.ProgressionMode.DIFFICULTY
            assert [e.key for e in ex.required_equipment.all()] == ["bodyweight"]
            assert ex.difficulty_rank > _get("Pistol Squat").difficulty_rank


class TestMetadata:
    @pytest.mark.parametrize(
        "name", ["Typewriter Push-up", "One-Arm Push-up", "Shrimp Squat", "Dragon Squat"]
    )
    def test_has_video_cue_and_muscles(self, seeded, name):
        ex = _get(name)
        assert ex.video_url.startswith("https://www.youtube.com/watch?v=")
        assert ex.cues
        assert ex.muscles.exists()

    def test_are_not_assessment_anchors(self, seeded):
        # Elite rungs are reached by progression, not placed onto by the trial.
        for name in ("Typewriter Push-up", "One-Arm Push-up", "Shrimp Squat", "Dragon Squat"):
            assert _get(name).is_assessment_anchor is False

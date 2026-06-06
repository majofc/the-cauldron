"""The Forge — adaptive calisthenics data model.

Design + evidence rationale live in the approved plan; the short version:
- Assessment is objective (AMRAP), not subjective RIR, because novices misjudge
  proximity to failure by ~4-5 reps.
- Progression follows an APRE-style autoregulated double progression, with two
  modes: difficulty (climb a movement ladder) for pure bodyweight, and load
  (smallest available increment) when the user owns loadable equipment.
"""

import uuid

from django.conf import settings
from django.db import models


class ForgeBaseModel(models.Model):
    """UUID pk + timestamps. Mirrors klingreen's BaseModel without coupling
    across submodules."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ─────────────────────────────────────────────────────────────────────────────
# Catalog (seeded, shared across users)
# ─────────────────────────────────────────────────────────────────────────────


class MovementPattern(ForgeBaseModel):
    """One of the six fundamental movement patterns the program is built around."""

    class Key(models.TextChoices):
        HORIZONTAL_PUSH = "horizontal_push", "Horizontal Push"
        VERTICAL_PULL = "vertical_pull", "Vertical Pull"
        VERTICAL_PUSH = "vertical_push", "Vertical Push"
        LOWER_UNILATERAL = "lower_unilateral", "Lower (Unilateral)"
        CORE_ANTI_EXTENSION = "core_anti_extension", "Core (Anti-Extension)"
        HINGE = "hinge", "Hinge / Posterior Chain"

    key = models.CharField(max_length=32, choices=Key.choices, unique=True)
    name = models.CharField(max_length=80)
    primary_muscles = models.CharField(max_length=200, blank=True, default="")
    is_lower_body = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Equipment(ForgeBaseModel):
    """A piece of equipment the user may own. ``is_loadable`` flags equipment
    that supports numeric load progression (dumbbells, barbell, bands)."""

    class Key(models.TextChoices):
        BODYWEIGHT = "bodyweight", "Bodyweight"
        PULLUP_BAR = "pullup_bar", "Pull-up Bar"
        DUMBBELLS = "dumbbells", "Dumbbells"
        BARBELL = "barbell", "Barbell + Plates"
        BANDS = "bands", "Resistance Bands"
        ROWING_MACHINE = "rowing_machine", "Rowing Machine"
        BENCH = "bench", "Bench"
        KETTLEBELL = "kettlebell", "Kettlebell"
        RINGS = "rings", "Gymnastic Rings"

    class LoadUnit(models.TextChoices):
        NONE = "none", "None"
        KG = "kg", "Kilograms"
        LB = "lb", "Pounds"
        BAND_LEVEL = "band_level", "Band Level"

    key = models.CharField(max_length=32, choices=Key.choices, unique=True)
    name = models.CharField(max_length=80)
    is_loadable = models.BooleanField(default=False)
    load_unit = models.CharField(
        max_length=16, choices=LoadUnit.choices, default=LoadUnit.NONE
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "equipment"

    def __str__(self):
        return self.name


class Exercise(ForgeBaseModel):
    """A single rung on a progression ladder for a pattern.

    A pattern has several parallel ladders depending on equipment (e.g. a
    bodyweight vertical-pull ladder vs. a dumbbell-row ladder). ``difficulty_rank``
    orders rungs within a (pattern, ladder); ``progression``/``regression`` link
    adjacent rungs of the same ladder.
    """

    class ProgressionMode(models.TextChoices):
        DIFFICULTY = "difficulty", "Difficulty (climb the ladder)"
        LOAD = "load", "Load (add weight/band level)"

    pattern = models.ForeignKey(
        MovementPattern, on_delete=models.CASCADE, related_name="exercises"
    )
    name = models.CharField(max_length=120)
    difficulty_rank = models.PositiveIntegerField(
        help_text="Order within the pattern's ladder; lower = easier."
    )
    progression_mode = models.CharField(
        max_length=16,
        choices=ProgressionMode.choices,
        default=ProgressionMode.DIFFICULTY,
    )
    rep_range_min = models.PositiveIntegerField(default=5)
    rep_range_max = models.PositiveIntegerField(default=12)
    is_timed = models.BooleanField(
        default=False, help_text="True for holds (planks); values are seconds."
    )
    required_equipment = models.ManyToManyField(
        Equipment, related_name="exercises", blank=True
    )
    video_url = models.URLField(blank=True, default="")
    cues = models.TextField(blank=True, default="")
    # Recommended rest after a working set, in seconds. Evidence-based: longer
    # rest (≥2 min) favours strength/heavy compounds; ~60-90s for endurance/
    # accessory; ~60s for isometric holds. See seed_forge.rest_for().
    rest_seconds = models.PositiveIntegerField(default=90)
    # The curated movement used to TEST this pattern in the Trial (one anchor
    # per pattern, bodyweight so it's always eligible). Gives a stable, sensible
    # test example regardless of owned equipment.
    is_assessment_anchor = models.BooleanField(default=False)
    # AMRAP score on this exercise at/above which the user is placed here during
    # the assessment (used by place_from_assessment).
    placement_threshold = models.PositiveIntegerField(
        default=0, help_text="Min AMRAP reps/seconds to be placed on this rung."
    )
    regression = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="harder_variants",
    )
    progression = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="easier_variants",
    )

    class Meta:
        ordering = ["pattern", "difficulty_rank"]
        unique_together = [("pattern", "name")]

    def __str__(self):
        return f"{self.name} ({self.pattern.name} · rank {self.difficulty_rank})"


# ─────────────────────────────────────────────────────────────────────────────
# Per-user data
# ─────────────────────────────────────────────────────────────────────────────


class UserEquipmentProfile(ForgeBaseModel):
    """What the user owns. Drives which exercises are eligible and the smallest
    load increment available for load-mode progression."""

    class Sex(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        UNDISCLOSED = "undisclosed", "Prefer not to say"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forge_equipment_profile",
    )
    equipment = models.ManyToManyField(Equipment, related_name="owners", blank=True)
    # Optional demographics — used only for the peer-comparison ("flames") score,
    # which needs age + sex to look up published normative percentiles.
    birth_year = models.PositiveIntegerField(null=True, blank=True)
    sex = models.CharField(
        max_length=12, choices=Sex.choices, default=Sex.UNDISCLOSED
    )
    # Concrete loadable details:
    dumbbell_weights = models.JSONField(
        default=list, blank=True, help_text="Available dumbbell weights, e.g. [5,10,15]."
    )
    band_levels = models.JSONField(
        default=list, blank=True, help_text="Ordered band tensions/labels, easiest first."
    )
    barbell_min_increment = models.FloatField(default=2.5)
    barbell_plates = models.JSONField(default=list, blank=True)
    load_unit = models.CharField(
        max_length=16,
        choices=Equipment.LoadUnit.choices,
        default=Equipment.LoadUnit.KG,
    )

    def __str__(self):
        return f"Equipment for {self.user}"


class AssessmentSession(ForgeBaseModel):
    """One run of the Trial. History is preserved; only the latest is active."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forge_assessments",
    )
    is_active = models.BooleanField(default=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Assessment {self.uuid} for {self.user}"


class AssessmentResult(ForgeBaseModel):
    """One AMRAP result for one pattern within an assessment, plus where it
    placed the user."""

    session = models.ForeignKey(
        AssessmentSession, on_delete=models.CASCADE, related_name="results"
    )
    pattern = models.ForeignKey(MovementPattern, on_delete=models.CASCADE)
    tested_exercise = models.ForeignKey(
        Exercise, on_delete=models.PROTECT, related_name="+"
    )
    # For unilateral moves we record each side; ``reps_or_seconds`` holds the
    # value used for placement (the weaker side). For bilateral moves the per-leg
    # fields stay null and ``reps_or_seconds`` is the single result.
    reps_or_seconds = models.PositiveIntegerField()
    left_reps = models.PositiveIntegerField(null=True, blank=True)
    right_reps = models.PositiveIntegerField(null=True, blank=True)
    placed_exercise = models.ForeignKey(
        Exercise, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )

    class Meta:
        unique_together = [("session", "pattern")]

    def __str__(self):
        return f"{self.pattern} → {self.reps_or_seconds}"


class Program(ForgeBaseModel):
    """A generated, adapting program. Only one active per user."""

    class Split(models.TextChoices):
        FULL_BODY_3X = "full_body_3x", "Full Body ×3/week"
        UPPER_LOWER_4X = "upper_lower_4x", "Upper/Lower ×4/week"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forge_programs",
    )
    is_active = models.BooleanField(default=True)
    source_assessment = models.ForeignKey(
        AssessmentSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="programs",
    )
    split = models.CharField(
        max_length=24, choices=Split.choices, default=Split.FULL_BODY_3X
    )
    weekly_volume_target = models.PositiveIntegerField(
        default=8, help_text="Target working sets per pattern per week."
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Program {self.uuid} for {self.user}"


class ProgramDay(ForgeBaseModel):
    program = models.ForeignKey(
        Program, on_delete=models.CASCADE, related_name="days"
    )
    day_index = models.PositiveIntegerField()
    name = models.CharField(max_length=80, default="")

    class Meta:
        ordering = ["program", "day_index"]
        unique_together = [("program", "day_index")]

    def __str__(self):
        return f"{self.name or 'Day'} {self.day_index}"


class PrescribedExercise(ForgeBaseModel):
    """The current prescription for one exercise on one day. Mutated by the
    adaptive engine after each session."""

    day = models.ForeignKey(
        ProgramDay, on_delete=models.CASCADE, related_name="prescriptions"
    )
    pattern = models.ForeignKey(MovementPattern, on_delete=models.PROTECT)
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT)
    target_sets = models.PositiveIntegerField(default=3)
    target_reps_min = models.PositiveIntegerField(default=5)
    target_reps_max = models.PositiveIntegerField(default=12)
    target_load = models.FloatField(
        null=True, blank=True, help_text="Load for load-mode exercises; null for bodyweight."
    )
    target_rest_seconds = models.PositiveIntegerField(default=90)
    order = models.PositiveIntegerField(default=0)
    # Counts consecutive sessions at top-of-range, for the difficulty-mode
    # "advance after >=2 sessions" rule.
    sessions_at_top = models.PositiveIntegerField(default=0)
    # When a difficulty advance is EARNED it is not applied automatically: the
    # harder rung is parked here and surfaced as a celebratory "unlock" the user
    # must Accept (climb) or Deny (hold). Null when nothing is pending.
    pending_progression = models.ForeignKey(
        Exercise,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["day", "order"]

    def __str__(self):
        return f"{self.exercise.name} — {self.target_sets}×{self.target_reps_min}-{self.target_reps_max}"


class WorkoutSession(ForgeBaseModel):
    """A saved exercise session ("meeting"): planned or completed, with the
    expected-vs-real set logs attached."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forge_sessions",
    )
    program_day = models.ForeignKey(
        ProgramDay, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
    )
    scheduled_for = models.DateField(null=True, blank=True)
    performed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PLANNED
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Session {self.uuid} ({self.status})"


class SetLog(ForgeBaseModel):
    """One set within a session. Expected values are snapshotted at session
    start so the planned-vs-real comparison survives later adaptation."""

    session = models.ForeignKey(
        WorkoutSession, on_delete=models.CASCADE, related_name="set_logs"
    )
    prescribed_exercise = models.ForeignKey(
        PrescribedExercise, on_delete=models.SET_NULL, null=True, related_name="set_logs"
    )
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT, related_name="+")
    set_index = models.PositiveIntegerField()
    expected_reps = models.PositiveIntegerField(null=True, blank=True)
    expected_load = models.FloatField(null=True, blank=True)
    actual_reps = models.PositiveIntegerField(null=True, blank=True)
    actual_load = models.FloatField(null=True, blank=True)
    is_amrap = models.BooleanField(default=False)
    rir = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["session", "prescribed_exercise", "set_index"]

    def __str__(self):
        return f"set {self.set_index}: {self.actual_reps}/{self.expected_reps} reps"


class BlockedExercise(ForgeBaseModel):
    """An exercise the user has forbidden (injury, limitation, dislike).

    Blocked exercises are excluded from the assessment ladder, from generated
    programs, and from progression/regression. When a live prescription lands on
    a blocked move it is swapped for the closest equal-or-easier substitute in
    the same pattern (see ``services.forge.substitute_for``)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forge_blocked_exercises",
    )
    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="blocked_by"
    )
    reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("user", "exercise")]

    def __str__(self):
        return f"{self.user} blocked {self.exercise.name}"

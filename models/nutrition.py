"""Daily nutrition (#85): legal acceptance, daily goals, meal log, meal plan.

- ``LegalAcceptance`` (#84) — append-only record of who accepted which version
  of the terms, the privacy notice and the sensitive-data consent, and when.
- ``CauldronProfile`` (#80) — per-user settings that aren't Forge or Grimoire
  specific; today only the timezone that sets where a day starts and ends.
- ``NutrientGoal`` (#80) — one daily (24 h) goal per nutrient, versioned: a
  change closes the old row and opens a new one, so a past day is judged
  against the goal active that day (#82).
- ``IntakeEntry`` (#81) — one thing eaten, with a nutrient snapshot taken when
  it was logged, so editing a spell later never rewrites the history.
- ``PlannedMeal`` (#83) — a spell planned for a day and slot.

Food logs and goals may reveal health information: every write needs the
sensitive-data consent (``services.legal``), and none of their values ever
reach PostHog.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .forge import ForgeBaseModel, channel_field
from .grimoire import Casting, Reagent, ReagentNutrients, Spell

DEFAULT_TIMEZONE = "America/Mexico_City"
NUTRIENT_CHOICES = [(name, name) for name in ReagentNutrients.FIELDS]


class LegalAcceptance(ForgeBaseModel):
    """One acceptance of one document version. Never updated or deleted
    through the app: a new version adds a row. The only change a row ever
    takes is ``withdrawn_at`` being set once on a sensitive-data consent
    (``services.legal.withdraw_consent``). No IP or user agent is stored."""

    class Document(models.TextChoices):
        TERMS = "terms", "Terms & Conditions"
        PRIVACY = "privacy", "Privacy notice"
        SENSITIVE_CONSENT = "sensitive_consent", "Sensitive-data consent"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cauldron_legal_acceptances"
    )
    document = models.CharField(max_length=20, choices=Document.choices)
    version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(default=timezone.now, editable=False)
    withdrawn_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-accepted_at"]
        indexes = [models.Index(fields=["user", "document", "-accepted_at"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Legal acceptances are append-only.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} accepted {self.document} v{self.version}"


class CauldronProfile(ForgeBaseModel):
    """Per-user Cauldron settings. Created lazily on first read."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cauldron_profile"
    )
    # IANA name; a day runs midnight to midnight here.
    timezone = models.CharField(max_length=64, default=DEFAULT_TIMEZONE)
    # A self-registered account waiting for its email verification (#72).
    # Only this flag lets the confirmation activate the account, so one an
    # admin switched off is never revived.
    pending_verification = models.BooleanField(default=False)

    def __str__(self):
        return f"Cauldron profile for {self.user}"


class NutrientGoal(ForgeBaseModel):
    """A daily goal for one nutrient, active on ``valid_from`` ≤ day <
    ``valid_to`` (``valid_to`` null = still active). No active row = no goal
    ("none": shown in totals, never judged)."""

    class Kind(models.TextChoices):
        AT_LEAST = "at_least", "At least"
        AT_MOST = "at_most", "At most"
        BETWEEN = "between", "Between"

    channel = channel_field()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cauldron_nutrient_goals"
    )
    nutrient = models.CharField(max_length=20, choices=NUTRIENT_CHOICES)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    min_value = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    max_value = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["nutrient", "-valid_from", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "nutrient"],
                condition=Q(valid_to__isnull=True),
                name="cauldron_one_active_goal_per_nutrient",
            ),
        ]
        indexes = [models.Index(fields=["user", "valid_from"])]

    def __str__(self):
        return f"{self.user} {self.nutrient} {self.kind} {self.min_value}–{self.max_value}"


class IntakeEntry(ForgeBaseModel):
    """One thing the user ate: a spell portion, a reagent amount or a quick
    entry with typed-in values.

    ``basis`` is what the snapshot was computed from — per 100 g values and
    their missing counts, plus grams per serving for a spell — so changing the
    amount later rescales the same basis instead of re-reading a spell that may
    have changed. ``nutrients`` holds the entry's totals (known values only) and
    ``missing`` how many of its ingredients lacked each one: an unknown value is
    never 0.
    """

    class Source(models.TextChoices):
        SPELL = "spell", "Spell"
        REAGENT = "reagent", "Reagent"
        QUICK = "quick", "Quick entry"

    channel = channel_field()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cauldron_intake"
    )
    eaten_at = models.DateTimeField()
    # The day it counts for, in the user's timezone when it was logged.
    local_date = models.DateField(db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices)
    spell = models.ForeignKey(Spell, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    casting = models.ForeignKey(Casting, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reagent = models.ForeignKey(Reagent, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    label = models.CharField(max_length=150)  # spell title / reagent name snapshot, or the quick label
    quantity = models.FloatField(validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=10)  # g / serving for spells, a SpellReagent unit for reagents
    grams = models.FloatField(null=True, blank=True)
    basis = models.JSONField(default=dict, blank=True)
    nutrients = models.JSONField(default=dict, blank=True)
    missing = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["eaten_at", "created_at"]
        indexes = [models.Index(fields=["user", "local_date"])]
        verbose_name_plural = "intake entries"

    def __str__(self):
        return f"{self.label} at {self.eaten_at}"


class PlannedMeal(ForgeBaseModel):
    """A spell planned for a day and slot (#83). ``intake_entry`` is set when
    the user marks it eaten; deleting that entry makes it planned again."""

    class Slot(models.TextChoices):
        BREAKFAST = "breakfast", "Breakfast"
        LUNCH = "lunch", "Lunch"
        DINNER = "dinner", "Dinner"
        SNACK = "snack", "Snack"

    channel = channel_field()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cauldron_planned_meals"
    )
    date = models.DateField()
    slot = models.CharField(max_length=10, choices=Slot.choices)
    spell = models.ForeignKey(Spell, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    spell_title = models.CharField(max_length=150, blank=True, default="")  # snapshot
    quantity = models.FloatField(validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=10, default="serving")  # g / serving
    order = models.PositiveSmallIntegerField(default=0)
    intake_entry = models.OneToOneField(
        IntakeEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="planned_meal"
    )

    class Meta:
        # Slot order (breakfast to snack) isn't alphabetical: listings sort it in
        # Python (services.nutrition.planned_on, PlanViewSet.list).
        ordering = ["date", "order", "created_at"]
        indexes = [models.Index(fields=["user", "date"])]

    def __str__(self):
        return f"{self.spell_title} on {self.date} ({self.slot})"

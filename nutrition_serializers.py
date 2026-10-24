"""Serializers for daily nutrition (#80–#84). Input serializers check shapes;
the rules (goal bounds, snapshots, grams) live in ``services.nutrition``."""

import math

from django.utils import timezone
from django.utils.translation import get_language
from rest_framework import serializers

from the_cauldron.models import CauldronProfile, IntakeEntry, LegalAcceptance, PlannedMeal, SpellReagent
from the_cauldron.services import grimoire, nutrition

MAX_QUANTITY = 100_000


class FiniteFloatField(serializers.FloatField):
    """A FloatField that refuses NaN and ±inf ("nan" parses as a float and slips
    past min/max, since every comparison with NaN is false)."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            self.fail("invalid")
        return value


class LegalAcceptSerializer(serializers.Serializer):
    documents = serializers.ListField(
        child=serializers.ChoiceField(choices=LegalAcceptance.Document.choices),
        allow_empty=False, max_length=3,
    )


class LegalWithdrawSerializer(serializers.Serializer):
    delete_data = serializers.BooleanField(default=False)


class MeSerializer(serializers.Serializer):
    timezone = serializers.CharField(max_length=64)

    def validate_timezone(self, value):
        if value not in nutrition.timezone_names():
            raise serializers.ValidationError("Unknown timezone.")
        return value


class BodySerializer(serializers.ModelSerializer):
    """Body data on the Cauldron profile (#105). Every field optional; null clears."""

    weight_kg = FiniteFloatField(required=False, allow_null=True, min_value=20, max_value=400)
    activity_level = serializers.ChoiceField(
        choices=CauldronProfile.Activity.choices, required=False, allow_blank=True, allow_null=True,
    )

    class Meta:
        model = CauldronProfile
        fields = ["birth_year", "sex", "weight_kg", "activity_level"]

    def validate_birth_year(self, value):
        if value is not None and not 1900 <= value <= timezone.localdate().year:
            raise serializers.ValidationError("Enter a year between 1900 and this year.")
        return value

    def validate_activity_level(self, value):
        return value or ""


class GoalSetSerializer(serializers.Serializer):
    goals = serializers.ListField(child=serializers.DictField(), max_length=len(nutrition.FIELDS))


class IntakeInputSerializer(serializers.Serializer):
    """Create / update an entry. Which fields matter depends on ``source``."""

    source = serializers.ChoiceField(choices=IntakeEntry.Source.choices, required=False)
    eaten_at = serializers.DateTimeField(required=False)
    spell = serializers.UUIDField(required=False)
    casting = serializers.UUIDField(required=False, allow_null=True)
    reagent = serializers.UUIDField(required=False)
    label = serializers.CharField(required=False, max_length=150, allow_blank=True)
    quantity = FiniteFloatField(required=False, min_value=0.01, max_value=MAX_QUANTITY)
    unit = serializers.ChoiceField(
        choices=list(nutrition.SPELL_UNITS) + [u for u in SpellReagent.Unit.values if u != "g"] + ["portion"],
        required=False,
    )
    values = serializers.DictField(required=False)


class IntakeEntrySerializer(serializers.ModelSerializer):
    spell = serializers.UUIDField(source="spell_id", read_only=True)
    reagent = serializers.UUIDField(source="reagent_id", read_only=True)
    casting = serializers.UUIDField(source="casting_id", read_only=True)
    local_time = serializers.SerializerMethodField()
    planned_meal = serializers.SerializerMethodField()
    # Nutrients estimated by the user's AI assistant (quick entry, or a spell /
    # ingredient whose values were estimated) — shown as a badge (#89).
    estimated = serializers.SerializerMethodField()

    class Meta:
        model = IntakeEntry
        fields = [
            "uuid", "eaten_at", "local_date", "local_time", "source", "spell", "casting", "reagent",
            "label", "quantity", "unit", "grams", "nutrients", "missing", "planned_meal", "estimated",
        ]
        read_only_fields = fields

    def get_local_time(self, obj):
        tz = self.context.get("tz")
        return timezone.localtime(obj.eaten_at, tz).strftime("%H:%M") if tz else None

    def get_estimated(self, obj):
        return bool((obj.basis or {}).get("estimated"))

    def get_planned_meal(self, obj):
        meal = getattr(obj, "planned_meal", None)
        return str(meal.uuid) if meal else None


class PlannedMealInputSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    slot = serializers.ChoiceField(choices=PlannedMeal.Slot.choices, required=False)
    spell = serializers.UUIDField(required=False)
    quantity = FiniteFloatField(required=False, min_value=0.01, max_value=MAX_QUANTITY)
    unit = serializers.ChoiceField(choices=nutrition.SPELL_UNITS, required=False)
    order = serializers.IntegerField(required=False, min_value=0, max_value=1000)


class PlanEatSerializer(serializers.Serializer):
    eaten_at = serializers.DateTimeField(required=False)
    quantity = FiniteFloatField(required=False, min_value=0.01, max_value=MAX_QUANTITY)
    unit = serializers.ChoiceField(choices=nutrition.SPELL_UNITS, required=False)


class PlannedMealSerializer(serializers.ModelSerializer):
    spell = serializers.UUIDField(source="spell_id", read_only=True)
    spell_title = serializers.SerializerMethodField()
    eaten = serializers.SerializerMethodField()
    intake_entry = serializers.UUIDField(source="intake_entry_id", read_only=True)

    class Meta:
        model = PlannedMeal
        fields = ["uuid", "date", "slot", "spell", "spell_title", "quantity", "unit", "order", "eaten", "intake_entry"]
        read_only_fields = fields

    def get_spell_title(self, obj):
        spell = obj.spell
        request = self.context.get("request")
        # A spell its author has since made private keeps the planner's snapshot.
        if spell is not None and request and grimoire.can_read(spell, request.user):
            return grimoire.localized_text(spell, get_language())[0]
        return obj.spell_title

    def get_eaten(self, obj):
        return obj.intake_entry_id is not None

"""Grimoire DRF serializers (#67)."""

import math

from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework import serializers

from the_cauldron.models import (
    ADDED_SUGAR_ERROR,
    Casting,
    GrimoirePreferences,
    Reagent,
    ReagentNutrients,
    Spell,
    SpellReagent,
    Tag,
    added_sugar_over_total,
)
from the_cauldron.services import grimoire

MAX_LINES = 60


def _finite_or_none(value):
    """Model validators let NaN/inf through (every comparison with NaN is false)."""
    if value is not None and not math.isfinite(value):
        raise serializers.ValidationError(_("Enter a number."))
    return value
MAX_STEPS = 40
MAX_STEP_LENGTH = 2000
MAX_DESCRIPTION_LENGTH = 5000
MAX_NOTES_LENGTH = 5000
MAX_SUBSTITUTIONS_LENGTH = 2000


def _finite(value, label):
    """DRF's FloatField accepts "nan" / "Infinity"; they'd save, then break
    every JSON response that includes the row (STRICT_JSON) — reject them."""
    if value is not None and not math.isfinite(value):
        raise serializers.ValidationError(_("%(label)s must be a real number.") % {"label": label})
    return value


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["slug", "name", "name_es"]


class ReagentNutrientsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReagentNutrients
        fields = list(ReagentNutrients.FIELDS)

    def validate(self, attrs):
        for field, value in attrs.items():
            _finite(value, field.replace("_", " "))
        return attrs


class ReagentSerializer(serializers.ModelSerializer):
    """Read + user create/update. A user-created reagent is always
    ``source=user`` and starts ``private`` (or ``pending`` when ``share`` asks
    staff to review it); only staff approve, in the admin. Updates are allowed
    to the creator until approval (enforced in the view)."""

    nutrients = ReagentNutrientsSerializer(required=False, allow_null=True)
    share = serializers.BooleanField(write_only=True, required=False, default=False)
    is_mine = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Reagent
        fields = [
            "uuid", "name", "name_es", "category", "source", "status", "fdc_id",
            "grams_per_unit", "density_g_per_ml", "nutrients", "share", "is_mine", "can_edit",
            "nutrients_estimated",
        ]
        # nutrients_estimated is set only by the MCP tools (an AI estimate, #89).
        read_only_fields = ["uuid", "source", "status", "fdc_id", "is_mine", "can_edit", "nutrients_estimated"]

    def get_can_edit(self, obj):
        request = self.context.get("request")
        return bool(request and grimoire.can_edit_reagent(request.user, obj))

    def get_is_mine(self, obj):
        request = self.context.get("request")
        return bool(request and obj.created_by_id == request.user.pk)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("An ingredient needs a name."))
        return value

    def validate_grams_per_unit(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("Expected an object like {\"cup\": 240}."))
        clean = {}
        for unit, grams in value.items():
            if unit not in grimoire.HOUSEHOLD_UNITS:
                raise serializers.ValidationError(_("Unknown unit '%(unit)s'.") % {"unit": unit})
            if grams in (None, ""):
                continue
            try:
                grams = float(grams)
            except (TypeError, ValueError):
                raise serializers.ValidationError(_("Weight per %(unit)s must be a number.") % {"unit": unit})
            if not math.isfinite(grams) or grams <= 0:
                raise serializers.ValidationError(_("Weight per %(unit)s must be above 0.") % {"unit": unit})
            clean[unit] = grams
        return clean

    def validate_density_g_per_ml(self, value):
        return _finite(value, _("Density"))

    def validate(self, attrs):
        nutrients = attrs.get("nutrients")
        if nutrients:
            # PATCH sends only what changes: check it against what's stored.
            stored = getattr(self.instance, "nutrients", None) if self.partial else None
            merged = {**{f: getattr(stored, f) for f in ReagentNutrients.FIELDS if stored}, **nutrients}
            if added_sugar_over_total(merged.get("sugar_g"), merged.get("added_sugar_g")):
                raise serializers.ValidationError({"nutrients": {"added_sugar_g": [ADDED_SUGAR_ERROR]}})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        nutrients = validated_data.pop("nutrients", None) or {}
        share = validated_data.pop("share", False)
        reagent = Reagent.objects.create(
            **validated_data,
            source=Reagent.Source.USER,
            status=Reagent.Status.PENDING if share else Reagent.Status.PRIVATE,
            created_by=self.context["request"].user,
        )
        ReagentNutrients.objects.create(reagent=reagent, **nutrients)
        return reagent

    @transaction.atomic
    def update(self, instance, validated_data):
        nutrients = validated_data.pop("nutrients", None)
        share = validated_data.pop("share", None)
        units_changed = any(
            key in validated_data and validated_data[key] != getattr(instance, key)
            for key in ("grams_per_unit", "density_g_per_ml")
        )
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if share is not None and "share" in self.initial_data:
            instance.status = Reagent.Status.PENDING if share else Reagent.Status.PRIVATE
        instance.save()
        if nutrients is not None:
            # PUT replaces the whole profile — a nutrient left out is unknown
            # (null), not "keep the old value"; PATCH changes only what it sends.
            if not self.partial:
                nutrients = {field: nutrients.get(field) for field in ReagentNutrients.FIELDS}
            # The viewset select_related()s the profile: put the saved one back
            # on the instance, or the response renders the old cached values (#86).
            instance.nutrients, _ = ReagentNutrients.objects.update_or_create(reagent=instance, defaults=nutrients)
        if units_changed:
            grimoire.regrams_lines(instance)
        return instance


class SpellLineSerializer(serializers.ModelSerializer):
    reagent = serializers.UUIDField(source="reagent.uuid")
    reagent_name = serializers.CharField(source="reagent.name", read_only=True)
    reagent_name_es = serializers.CharField(source="reagent.name_es", read_only=True)
    reagent_status = serializers.CharField(source="reagent.status", read_only=True)
    # Lets the client show "≈ volume" for weighed or counted lines.
    reagent_density = serializers.FloatField(source="reagent.density_g_per_ml", read_only=True)

    class Meta:
        model = SpellReagent
        fields = [
            "reagent", "reagent_name", "reagent_name_es", "reagent_status", "reagent_density",
            "quantity", "unit", "grams", "note", "note_es", "order",
        ]
        read_only_fields = ["grams", "note_es", "order"]


class SpellSerializer(serializers.ModelSerializer):
    """Spell with its reagent lines and tags written in one request.

    ``visibility`` is read-only here: it changes only through the publish /
    unpublish actions, so an edit can never publish by accident."""

    lines = SpellLineSerializer(many=True, required=False)
    tags = serializers.SlugRelatedField(
        slug_field="slug", queryset=Tag.objects.all(), many=True, required=False
    )
    owner_name = serializers.CharField(source="owner.username", read_only=True)
    is_mine = serializers.SerializerMethodField()
    forked_from = serializers.SerializerMethodField()
    has_unverified_reagents = serializers.SerializerMethodField()
    has_estimated_nutrition = serializers.SerializerMethodField()
    # The viewing user's own castings of this spell (#68); annotated by
    # services.grimoire.with_casting_stats.
    my_casting_count = serializers.SerializerMethodField()
    my_average_score = serializers.SerializerMethodField()
    my_last_cast_on = serializers.SerializerMethodField()

    class Meta:
        model = Spell
        fields = [
            "uuid", "title", "title_es", "description", "description_es", "servings",
            "prep_minutes", "cook_minutes", "steps", "steps_es", "visibility", "tags", "lines",
            "owner_name", "is_mine", "forked_from", "has_unverified_reagents", "has_estimated_nutrition",
            "my_casting_count", "my_average_score", "my_last_cast_on", "cooked_yield_g", "yield_pct",
            "created_at", "updated_at",
        ]
        # *_es is curated starter text (seed/admin only); users write the base fields.
        read_only_fields = [
            "uuid", "title_es", "description_es", "steps_es", "visibility", "created_at", "updated_at",
        ]

    def get_is_mine(self, obj):
        request = self.context.get("request")
        return bool(request and obj.owner_id == request.user.pk)

    def validate_cooked_yield_g(self, value):
        return _finite_or_none(value)

    def validate_yield_pct(self, value):
        return _finite_or_none(value)

    def get_forked_from(self, obj):
        src = obj.forked_from
        if src is None:
            return None
        # Only reveal the source's title if the viewer may still read it.
        request = self.context.get("request")
        readable = src.visibility == Spell.Visibility.PUBLIC or (
            request and src.owner_id == request.user.pk
        )
        if not readable:
            return {"uuid": None, "title": None, "title_es": None}
        return {"uuid": str(src.uuid), "title": src.title, "title_es": src.title_es}

    def get_my_casting_count(self, obj):
        return getattr(obj, "my_casting_count", 0) or 0

    def get_my_average_score(self, obj):
        avg = getattr(obj, "my_average_score", None)
        return round(float(avg), 2) if avg is not None else None

    def get_my_last_cast_on(self, obj):
        last = getattr(obj, "my_last_cast_on", None)
        return last.isoformat() if last else None

    def get_has_unverified_reagents(self, obj):
        return any(line.reagent.status != Reagent.Status.APPROVED for line in obj.lines.all())

    def get_has_estimated_nutrition(self, obj):
        return any(line.reagent.nutrients_estimated for line in obj.lines.all())

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("A recipe needs a name."))
        return value

    def validate_description(self, value):
        if len(value) > MAX_DESCRIPTION_LENGTH:
            raise serializers.ValidationError(
                _("Keep the description under %(n)s characters.") % {"n": MAX_DESCRIPTION_LENGTH}
            )
        return value

    def validate_steps(self, value):
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            raise serializers.ValidationError(_("Steps must be a list of text."))
        steps = [s.strip() for s in value if s.strip()]
        if len(steps) > MAX_STEPS:
            raise serializers.ValidationError(_("At most %(n)s steps.") % {"n": MAX_STEPS})
        if any(len(s) > MAX_STEP_LENGTH for s in steps):
            raise serializers.ValidationError(_("A step can be at most %(n)s characters.") % {"n": MAX_STEP_LENGTH})
        return steps

    def validate_lines(self, value):
        if len(value) > MAX_LINES:
            raise serializers.ValidationError(_("At most %(n)s ingredients.") % {"n": MAX_LINES})
        user = self.context["request"].user
        uuids = {line["reagent"]["uuid"] for line in value}
        allowed = {r.uuid: r for r in grimoire.visible_reagents(user).filter(uuid__in=uuids)}
        if self.instance is not None:
            # A fork may carry another user's private reagent; keeping it is fine.
            for line in self.instance.lines.select_related("reagent"):
                allowed.setdefault(line.reagent.uuid, line.reagent)
        resolved, errors = [], []
        for line in value:
            reagent = allowed.get(line["reagent"]["uuid"])
            if reagent is None:
                errors.append({"reagent": _("Unknown ingredient.")})
                resolved.append(None)
                continue
            qty = line["quantity"]
            if qty is None or not math.isfinite(qty) or qty <= 0:
                errors.append({"quantity": _("Enter an amount above 0.")})
                resolved.append(None)
                continue
            try:
                grams = grimoire.to_grams(reagent, line["quantity"], line["unit"])
            except grimoire.ConversionError as exc:
                errors.append({"unit": str(exc)})
                resolved.append(None)
                continue
            errors.append({})
            resolved.append({**line, "reagent": reagent, "grams": grams})
        if any(errors):
            raise serializers.ValidationError(errors)
        return resolved

    def _write_lines(self, spell, lines):
        spell.lines.all().delete()
        SpellReagent.objects.bulk_create(
            SpellReagent(
                spell=spell,
                reagent=line["reagent"],
                quantity=line["quantity"],
                unit=line["unit"],
                grams=line["grams"],
                note=line.get("note", ""),
                order=i,
            )
            for i, line in enumerate(lines)
        )

    def _reloaded(self, spell):
        """The saved spell, prefetched and annotated like the list/detail responses."""
        qs = grimoire.with_spell_relations(Spell.objects.all())
        qs = grimoire.with_casting_stats(qs, self.context["request"].user)
        return qs.get(pk=spell.pk)

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        tags = validated_data.pop("tags", [])
        spell = Spell.objects.create(owner=self.context["request"].user, **validated_data)
        spell.tags.set(tags)
        self._write_lines(spell, lines)
        return self._reloaded(spell)

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        tags = validated_data.pop("tags", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if tags is not None:
            instance.tags.set(tags)
        if lines is not None:
            self._write_lines(instance, lines)
        return self._reloaded(instance)


class GrimoirePreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = GrimoirePreferences
        fields = ["unit_system"]


class CastingSerializer(serializers.ModelSerializer):
    """One time the user made a spell. ``spell`` is set by the view (from the
    URL), never from the payload; ``spell_available`` is false once the spell
    was deleted or stopped being readable ("spell removed")."""

    spell = serializers.SerializerMethodField()
    spell_title_es = serializers.SerializerMethodField()
    spell_available = serializers.SerializerMethodField()

    class Meta:
        model = Casting
        fields = [
            "uuid", "spell", "spell_title", "spell_title_es", "spell_available", "cast_on",
            "score", "notes", "adjustments", "substitutions", "cooked_weight_g", "created_at", "updated_at",
        ]
        read_only_fields = ["uuid", "spell_title", "created_at", "updated_at"]

    def _readable_spell(self, obj):
        spell = obj.spell
        if spell is None:
            return None
        request = self.context.get("request")
        if spell.visibility == Spell.Visibility.PUBLIC or (request and spell.owner_id == request.user.pk):
            return spell
        return None  # the author made it private again: treat it as gone

    def get_spell(self, obj):
        spell = self._readable_spell(obj)
        return str(spell.uuid) if spell else None

    def get_spell_title_es(self, obj):
        spell = self._readable_spell(obj)
        return spell.title_es if spell else ""

    def get_spell_available(self, obj):
        return self._readable_spell(obj) is not None

    def to_representation(self, obj):
        data = super().to_representation(obj)
        spell = self._readable_spell(obj)
        if spell is not None:
            data["spell_title"] = spell.title  # live, like spell_title_es; the snapshot is for a removed spell
        return data

    def validate_notes(self, value):
        if len(value) > MAX_NOTES_LENGTH:
            raise serializers.ValidationError(_("Keep the notes under %(n)s characters.") % {"n": MAX_NOTES_LENGTH})
        return value

    def validate_substitutions(self, value):
        if len(value) > MAX_SUBSTITUTIONS_LENGTH:
            raise serializers.ValidationError(
                _("Keep the substitutions under %(n)s characters.") % {"n": MAX_SUBSTITUTIONS_LENGTH}
            )
        return value

    def validate_cooked_weight_g(self, value):
        return _finite_or_none(value)

    def validate_adjustments(self, value):
        """Only the documented shape is stored — nothing else from the client."""
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Adjustments must be a list."))
        if len(value) > MAX_LINES:
            raise serializers.ValidationError(_("At most %(n)s ingredients.") % {"n": MAX_LINES})
        units = set(SpellReagent.Unit.values)
        clean = []
        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError(_("Each adjustment must be an object."))
            name = str(item.get("reagent_name") or "").strip()[:120]
            unit = item.get("unit")
            original_unit = item.get("original_unit") or None
            if not name or unit not in units or (original_unit is not None and original_unit not in units):
                raise serializers.ValidationError(_("Each adjustment needs an ingredient and a unit."))
            line = item.get("line")
            if line is not None and (isinstance(line, bool) or not isinstance(line, int) or not 0 <= line < MAX_LINES):
                raise serializers.ValidationError(_("Each adjustment needs an ingredient and a unit."))
            numbers = {}
            for key in ("original_qty", "used_qty"):
                raw = item.get(key)
                if raw in (None, ""):
                    numbers[key] = None
                    continue
                if isinstance(raw, bool):
                    raise serializers.ValidationError(_("Amounts must be numbers."))
                try:
                    num = float(raw)
                except (TypeError, ValueError):
                    raise serializers.ValidationError(_("Amounts must be numbers."))
                if not math.isfinite(num) or num < 0:
                    raise serializers.ValidationError(_("Amounts must be 0 or more."))
                numbers[key] = num
            if numbers["used_qty"] is None:
                raise serializers.ValidationError(_("Enter the amount you used."))
            reagent = item.get("reagent")
            clean.append({
                "line": line,
                "reagent": str(reagent)[:36] if reagent else None,
                "reagent_name": name,
                "original_qty": numbers["original_qty"],
                "original_unit": original_unit,
                "used_qty": numbers["used_qty"],
                "unit": unit,
            })
        return clean


class OnHandSerializer(serializers.Serializer):
    """One "I have…" amount for the scaler (#69)."""

    reagent = serializers.UUIDField()
    quantity = serializers.FloatField()
    unit = serializers.ChoiceField(choices=SpellReagent.Unit.choices)

    def validate_quantity(self, value):
        if not math.isfinite(value) or value <= 0:
            raise serializers.ValidationError(_("Enter an amount above 0."))
        return value


class SpellScaleSerializer(serializers.Serializer):
    """``POST spells/{uuid}/scale/`` — one mode per request; the number it needs
    is checked by services.grimoire_scaling (zero/negative → 400)."""

    MODES = ("factor", "servings", "total_grams", "available")

    mode = serializers.ChoiceField(choices=MODES)
    factor = serializers.FloatField(required=False)
    servings = serializers.FloatField(required=False)
    grams = serializers.FloatField(required=False)
    available = OnHandSerializer(many=True, required=False, max_length=MAX_LINES)

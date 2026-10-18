"""Writes to the daily nutrition data — the meal log and the plan — shared by
the REST API (``nutrition_api.py``) and the MCP tools (``mcp_server/tools``).
Each function acts for the user it is given; anything that isn't theirs (or
readable by them) is refused. Errors are ``NutritionInputError`` (bad input)
or ``PlanConflict`` (the plan item's state forbids it).
"""

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from the_cauldron.models import Casting, IntakeEntry, PlannedMeal
from the_cauldron.services import grimoire, nutrition
from the_cauldron.services.nutrition import NutritionInputError


def apply_entry(user, data, instance=None, *, channel=None, estimated=None):
    """Build or update an ``IntakeEntry`` from validated input. A new source
    (another spell or reagent) takes a fresh snapshot; a changed amount
    rescales the stored one.

    ``channel`` marks where a new entry came from (web / MCP). ``estimated``
    flags a quick entry's values as an AI estimate (``basis["estimated"]``);
    ``None`` keeps whatever the entry had."""
    tz = nutrition.user_tz(user)
    entry = instance or IntakeEntry(user=user)
    source = data.get("source") or entry.source
    if not source:
        raise NutritionInputError(_("Choose what you ate: a spell, an ingredient or a quick entry."))
    same_source = instance is not None and instance.source == source
    if instance is None or data.get("eaten_at"):
        # local_date is fixed when the time is set: a later timezone change
        # never moves an entry to another day.
        entry.eaten_at = nutrition.localize(data.get("eaten_at") or timezone.now(), tz)
        entry.local_date = timezone.localtime(entry.eaten_at, tz).date()
    quantity = data.get("quantity", entry.quantity if same_source else None)
    unit = data.get("unit", entry.unit if same_source else None)

    if source == IntakeEntry.Source.SPELL:
        if "spell" in data:
            spell = grimoire.readable_spells(user).filter(uuid=data["spell"]).first()
            if spell is None:
                raise NutritionInputError(_("That spell is private or no longer exists."))
        elif same_source:
            spell = entry.spell
        else:
            raise NutritionInputError(_("Choose a spell."))
        fresh = not same_source or (spell is not None and spell.pk != entry.spell_id)
        unit = unit or "serving"
        quantity = quantity or 1
        if unit not in nutrition.SPELL_UNITS:
            raise NutritionInputError(_("Use grams or servings for a spell."))
        if fresh:
            entry.basis = nutrition.spell_basis_snapshot(spell, user)
            entry.spell = spell
            entry.label = grimoire.localized_text(spell, get_language())[0]
        if "casting" in data:
            entry.casting = (
                Casting.objects.filter(uuid=data["casting"], user=user, spell=entry.spell).first()
                if data["casting"] else None
            )
        elif fresh:
            entry.casting = None
        entry.reagent = None
        entry.grams = nutrition.spell_grams(entry.basis, quantity, unit)
        entry.nutrients, entry.missing = nutrition.totals_from_basis(entry.basis, entry.grams)
    elif source == IntakeEntry.Source.REAGENT:
        if "reagent" in data:
            reagent = (
                grimoire.visible_reagents(user).select_related("nutrients").filter(uuid=data["reagent"]).first()
            )
            if reagent is None:
                raise NutritionInputError(_("That ingredient isn't available."))
        elif same_source:
            reagent = entry.reagent
        else:
            raise NutritionInputError(_("Choose an ingredient."))
        fresh = not same_source or (reagent is not None and reagent.pk != entry.reagent_id)
        unit = unit or "g"
        if quantity is None:
            raise NutritionInputError(_("Enter how much you ate."))
        if unit in nutrition.SPELL_UNITS[1:] or unit == "portion":
            raise NutritionInputError(_("Use a weight, a volume or pieces for an ingredient."))
        if fresh:
            entry.basis = nutrition.reagent_basis_snapshot(reagent)
            entry.reagent = reagent
            entry.label = (get_language() or "").startswith("es") and reagent.name_es or reagent.name
        entry.spell = entry.casting = None
        entry.grams = nutrition.reagent_grams(entry.reagent, quantity, unit)
        entry.nutrients, entry.missing = nutrition.totals_from_basis(entry.basis, entry.grams)
    else:
        label = (data.get("label") or (entry.label if same_source else "")).strip()
        if not label:
            raise NutritionInputError(_("Name what you ate."))
        entry.label = label
        if "values" in data:
            entry.nutrients, entry.missing = nutrition.clean_quick_values(data["values"])
        elif not same_source:
            raise NutritionInputError(_("Enter at least one value, e.g. the calories."))
        quantity, unit = quantity or 1, "portion"
        entry.spell = entry.casting = entry.reagent = None
        entry.grams = None
        was_estimated = bool(same_source and (entry.basis or {}).get("estimated"))
        entry.basis = {"estimated": True} if (was_estimated if estimated is None else estimated) else {}
    entry.source = source
    entry.quantity = quantity
    entry.unit = unit
    if channel and entry._state.adding:
        entry.channel = channel
    entry.save()
    return entry


def apply_plan(user, meal, data, *, channel=None):
    """Create or change a ``PlannedMeal`` from validated input (the spell must
    be readable by ``user``)."""
    if "spell" in data:
        spell = grimoire.readable_spells(user).filter(uuid=data["spell"]).first()
        if spell is None:
            raise NutritionInputError(_("That spell is private or no longer exists."))
        meal.spell = spell
        meal.spell_title = spell.title
    elif meal.spell_id is None and meal._state.adding:
        raise NutritionInputError(_("Choose a spell."))
    for field in ("date", "slot", "quantity", "unit", "order"):
        if field in data:
            setattr(meal, field, data[field])
    if meal.date is None:
        meal.date = nutrition.today_for(user)
    if not meal.slot:
        raise NutritionInputError(_("Choose a meal: breakfast, lunch, dinner or snack."))
    if meal.quantity is None:
        meal.quantity = 1
    if channel and meal._state.adding:
        meal.channel = channel
    meal.save()
    return meal


class PlanConflict(Exception):
    """The planned meal can't be eaten/changed as asked (already eaten, spell gone)."""


def eat_plan(user, meal_pk, data, *, channel=None):
    """Log a planned meal as eaten: an ``IntakeEntry`` with the real time
    (default now, or noon of a past/future planned day) and amount (default
    the planned one), linked back to the plan item. Locked and re-checked, so
    a double tap can't log the meal twice."""
    today = nutrition.today_for(user)
    with transaction.atomic():
        meal = PlannedMeal.objects.select_for_update(of=("self",)).select_related("spell").get(pk=meal_pk, user=user)
        if meal.intake_entry_id:
            raise PlanConflict(_("Already marked as eaten."))
        if meal.spell is None or not grimoire.can_read(meal.spell, user):
            raise PlanConflict(_("That spell is private or no longer exists."))
        eaten_at = data.get("eaten_at") or (
            timezone.now() if meal.date == today else datetime.combine(meal.date, time(12, 0))
        )
        entry = apply_entry(user, {
            "source": IntakeEntry.Source.SPELL,
            "spell": meal.spell.uuid,
            "eaten_at": eaten_at,
            "quantity": data.get("quantity", meal.quantity),
            "unit": data.get("unit", meal.unit),
        }, channel=channel)
        meal.intake_entry = entry
        meal.save(update_fields=["intake_entry", "updated_at"])
    return meal, entry

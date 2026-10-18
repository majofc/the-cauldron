"""Read payloads of the daily nutrition data, shared by the REST API
(``nutrition_api.py``) and the MCP tools (``mcp_server/tools``) so both answer
from the same code. Every function takes the user explicitly and only ever
reads that user's rows.

``request`` is only used by the serializers to tell whose title a planned
spell may show (``request.user``); the MCP layer passes an object with ``user``.
"""

from datetime import timedelta

from django.utils.translation import gettext as _

from the_cauldron.models import IntakeEntry, NutrientGoal, PlannedMeal
from the_cauldron.nutrition_serializers import IntakeEntrySerializer, PlannedMealSerializer
from the_cauldron.services import nutrition
from the_cauldron.services.nutrition import NutritionInputError


def goals_payload(user):
    """The active goal set."""
    goals = nutrition.current_goals(user)
    return {
        "today": nutrition.today_for(user),
        "goals": [nutrition.goal_payload(goals[n]) for n in nutrition.FIELDS if n in goals],
    }


def goal_history(user):
    """Every goal version, newest first per nutrient. Rows replaced on the day
    they were set (never active on any day) are left out."""
    rows = [
        g for g in NutrientGoal.objects.filter(user=user)
        if g.valid_to is None or g.valid_to > g.valid_from
    ]
    return [nutrition.goal_payload(g) for g in rows]


def day_payload(user, day, tz, request):
    """One day of the log: entries, totals and each goal's status."""
    today = nutrition.today_for(user)
    entries = list(
        IntakeEntry.objects.filter(user=user, local_date=day).select_related("planned_meal")
    )
    totals = nutrition.entries_totals(entries)
    goals = nutrition.goals_on(nutrition.goal_rows_between(user, day, day), day)
    return {
        "date": day,
        "today": today,
        "logged": bool(entries),
        "entries": IntakeEntrySerializer(entries, many=True, context={"tz": tz, "request": request}).data,
        "totals": nutrition.totals_payload(totals),
        "goals": nutrition.goal_statuses(goals, totals, logged=bool(entries), final=day < today),
    }


def check_range(start, end):
    """A ``from``–``to`` range of at most ``MAX_RANGE_DAYS`` days, in order."""
    if end < start or (end - start).days >= nutrition.MAX_RANGE_DAYS:
        raise NutritionInputError(_("Ask for at most %(n)s days.") % {"n": nutrition.MAX_RANGE_DAYS})


def plan_range(user, start, end, request):
    """Planned meals from ``start`` to ``end``, in slot order within each day."""
    check_range(start, end)
    order = {slot: i for i, slot in enumerate(PlannedMeal.Slot.values)}
    meals = sorted(
        PlannedMeal.objects.filter(user=user, date__gte=start, date__lte=end).select_related("spell"),
        key=lambda m: (m.date, order.get(m.slot, 9), m.order, m.created_at),
    )
    return PlannedMealSerializer(meals, many=True, context={"request": request}).data


def plan_day_payload(user, day, request):
    """Planned meals of a day with their live totals, plus the projected day:
    what's eaten + what's still planned, against the goals."""
    cache = nutrition.SpellTotalsCache(user)
    meals = []
    for meal in nutrition.planned_on(user, day):
        live = None if meal.intake_entry_id else cache.meal_totals(meal)
        row = PlannedMealSerializer(meal, context={"request": request}).data
        row["grams"] = round(live[2], 1) if live else None
        row["nutrients"] = live[0] if live else None
        row["missing"] = live[1] if live else None
        row["available"] = bool(live) or bool(meal.intake_entry_id)
        meals.append(row)
    projected = nutrition.consumed_and_planned(user, day, cache)
    goals = nutrition.goals_on(nutrition.goal_rows_between(user, day, day), day)
    return {
        "date": day,
        "meals": meals,
        "projected": nutrition.totals_payload(projected),
        "goals": nutrition.goal_statuses(goals, projected, logged=True, final=True),
    }


def default_week(start):
    return start + timedelta(days=6)

"""Nutrition write tools: the meal log, the meal plan and daily goals.

Thin adapters over ``nutrition_writes`` / ``services.nutrition`` — the same
code the website uses — with input validated by the web's serializers. Every
record they create carries ``channel="mcp"``. Edits and deletions are
two-step (``confirm=True``): see ``registry``.
"""

from the_cauldron import nutrition_writes as writes
from the_cauldron.mcp_server.errors import ToolError
from the_cauldron.mcp_server.registry import WRITE, tool
from the_cauldron.mcp_server.tools import _format as fmt
from the_cauldron.mcp_server.tools.nutrition import DATE, _day
from the_cauldron.models import Channel, IntakeEntry, PlannedMeal, SpellReagent
from the_cauldron.nutrition_payloads import goals_payload
from the_cauldron.nutrition_serializers import (
    IntakeEntrySerializer,
    IntakeInputSerializer,
    PlanEatSerializer,
    PlannedMealInputSerializer,
    PlannedMealSerializer,
)
from the_cauldron.services import nutrition

MCP = Channel.MCP
ID = {"type": "string", "description": "The id returned by get_today / get_journal / get_meal_plan."}
NUTRIENTS = {
    "type": "object",
    "description": "Known totals for what was eaten (the whole portion, not per 100 g). Leave out what you "
                   "don't know — it's stored as unknown, never 0.",
    "properties": {name: {"type": "number", "minimum": 0} for name in nutrition.FIELDS},
    "additionalProperties": False,
}
ENTRY_FIELDS = {
    "spell_id": {"type": "string", "description": "A spell (recipe) from search_spells."},
    "reagent_id": {"type": "string", "description": "An ingredient from search_reagents."},
    "label": {"type": "string", "minLength": 1, "maxLength": 150,
              "description": "Quick entry: what it was, when there's no spell or ingredient for it."},
    "nutrients": NUTRIENTS,
    "estimated": {"type": "boolean",
                  "description": "Quick entry: true when YOU estimated the nutrients (photo, description)."},
    "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 100000},
    "unit": {
        "type": "string", "enum": ["serving", "g", *[u for u in SpellReagent.Unit.values if u != "g"]],
        "description": "Spell: serving (default) or g. Ingredient: g, ml, cup, tbsp, tsp, piece, glass…",
    },
    "eaten_at": {"type": "string", "description": "Local time, e.g. 2026-10-16T08:30 (default now)."},
}
SLOT = {"type": "string", "enum": PlannedMeal.Slot.values}


def _entry_out(ctx, entry):
    return fmt.entry(IntakeEntrySerializer(entry, context={"tz": ctx.tz, "request": ctx.request}).data)


def _meal_out(ctx, meal):
    return fmt.planned(PlannedMealSerializer(meal, context={"request": ctx.request}).data)


def _entry_input(args, *, partial):
    """Tool arguments → the web's IntakeInputSerializer data."""
    sources = [k for k in ("spell_id", "reagent_id", "label") if args.get(k)]
    if len(sources) > 1 or (not partial and not sources):
        raise ToolError("invalid_arguments", "Give exactly one of spell_id, reagent_id or label (quick entry).")
    data = {}
    if args.get("spell_id"):
        data.update(source=IntakeEntry.Source.SPELL, spell=args["spell_id"])
    elif args.get("reagent_id"):
        data.update(source=IntakeEntry.Source.REAGENT, reagent=args["reagent_id"])
    elif args.get("label"):
        data.update(source=IntakeEntry.Source.QUICK, label=args["label"])
    if "nutrients" in args:
        data["values"] = args["nutrients"]
    for key in ("quantity", "unit", "eaten_at"):
        if key in args:
            data[key] = args[key]
    req = IntakeInputSerializer(data=data, partial=partial)
    req.is_valid(raise_exception=True)
    return req.validated_data


@tool(
    "log_meal",
    title="Log a meal",
    scope=WRITE,
    consent=True,
    description=(
        "Log something the user ate or drank. Prefer a spell (spell_id, in servings or grams) or an ingredient "
        "(reagent_id, with quantity + unit) found with search_spells / search_reagents; otherwise a quick entry "
        "(label + nutrients you know, estimated=true if you estimated them from a photo or description). "
        "Returns the entry and the day's updated totals and goals."
    ),
    schema={"type": "object", "properties": ENTRY_FIELDS, "additionalProperties": False},
)
def log_meal(ctx, estimated=None, **args):
    data = _entry_input(args, partial=False)
    quick = data["source"] == IntakeEntry.Source.QUICK
    entry = writes.apply_entry(ctx.user, data, channel=MCP, estimated=bool(estimated) if quick else None)
    return {"logged": _entry_out(ctx, entry), "day": _day(ctx, entry.local_date)}


def _own_entry(ctx, id):
    return IntakeEntry.objects.select_related("planned_meal").get(user=ctx.user, uuid=id)


@tool(
    "update_meal",
    title="Change a logged meal",
    scope=WRITE,
    consent=True,
    confirm=True,
    description=(
        "Change a logged meal: amount, time, what it was, or a quick entry's nutrients. Two-step: the first call "
        "returns a preview and a confirm_token; apply only after the user agrees."
    ),
    schema={"type": "object", "properties": {"id": ID, **ENTRY_FIELDS}, "required": ["id"], "additionalProperties": False},
)
def update_meal(ctx, id, apply, estimated=None, **args):
    entry = _own_entry(ctx, id)
    data = _entry_input(args, partial=True)
    if not apply:
        changes = {k: v for k, v in args.items()}
        if estimated is not None:
            changes["estimated"] = estimated
        return {"target": entry.uuid, "preview": {"meal": _entry_out(ctx, entry), "changes": changes}}
    entry = writes.apply_entry(ctx.user, data, entry, estimated=estimated)
    return {"updated": _entry_out(ctx, entry), "day": _day(ctx, entry.local_date)}


@tool(
    "delete_meal",
    title="Delete a logged meal",
    scope=WRITE,
    consent=True,
    confirm=True,
    destructive=True,
    description="Delete a logged meal. Two-step: preview first, then confirm_token after the user agrees.",
    schema={"type": "object", "properties": {"id": ID}, "required": ["id"], "additionalProperties": False},
)
def delete_meal(ctx, id, apply):
    entry = _own_entry(ctx, id)
    if not apply:
        return {"target": entry.uuid, "preview": {"delete": _entry_out(ctx, entry)}}
    day = entry.local_date
    entry.delete()
    return {"deleted": id, "day": _day(ctx, day)}


def _plan_input(args, *, partial):
    data = {k: v for k, v in args.items() if k in ("date", "slot", "quantity", "unit")}
    if "spell_id" in args:
        data["spell"] = args["spell_id"]
    req = PlannedMealInputSerializer(data=data, partial=partial)
    req.is_valid(raise_exception=True)
    return req.validated_data


PLAN_FIELDS = {
    "spell_id": {"type": "string", "description": "A spell (recipe) from search_spells."},
    "date": {**DATE, "description": "Day to plan (default today)."},
    "slot": SLOT,
    "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
    "unit": {"type": "string", "enum": nutrition.SPELL_UNITS},
}


@tool(
    "plan_meal",
    title="Plan a meal",
    scope=WRITE,
    consent=True,
    description="Add a spell (recipe) to the user's meal plan for a day and slot (default 1 serving, today).",
    schema={"type": "object", "properties": PLAN_FIELDS, "required": ["spell_id", "slot"], "additionalProperties": False},
)
def plan_meal(ctx, **args):
    meal = writes.apply_plan(ctx.user, PlannedMeal(user=ctx.user, unit="serving"), _plan_input(args, partial=False),
                             channel=MCP)
    return {"planned": _meal_out(ctx, meal)}


def _own_meal(ctx, id):
    return PlannedMeal.objects.select_related("spell").get(user=ctx.user, uuid=id)


@tool(
    "update_planned_meal",
    title="Change a planned meal",
    scope=WRITE,
    consent=True,
    confirm=True,
    description="Move or change a planned meal (day, slot, amount, spell). Two-step: preview, then confirm_token.",
    schema={"type": "object", "properties": {"id": ID, **PLAN_FIELDS}, "required": ["id"], "additionalProperties": False},
)
def update_planned_meal(ctx, id, apply, **args):
    meal = _own_meal(ctx, id)
    if meal.intake_entry_id:
        raise ToolError("conflict", "This meal is already logged as eaten; change the logged meal instead.")
    data = _plan_input(args, partial=True)
    if not apply:
        return {"target": meal.uuid, "preview": {"planned": _meal_out(ctx, meal), "changes": args}}
    return {"updated": _meal_out(ctx, writes.apply_plan(ctx.user, meal, data))}


@tool(
    "delete_planned_meal",
    title="Remove a planned meal",
    scope=WRITE,
    consent=True,
    confirm=True,
    destructive=True,
    description="Remove a meal from the plan. Two-step: preview, then confirm_token after the user agrees.",
    schema={"type": "object", "properties": {"id": ID}, "required": ["id"], "additionalProperties": False},
)
def delete_planned_meal(ctx, id, apply):
    meal = _own_meal(ctx, id)
    if not apply:
        return {"target": meal.uuid, "preview": {"remove": _meal_out(ctx, meal)}}
    meal.delete()
    return {"removed": id}


@tool(
    "mark_planned_meal_eaten",
    title="Mark a planned meal as eaten",
    scope=WRITE,
    consent=True,
    description=(
        "Log a planned meal as eaten (default: now if it's today's, the planned amount). "
        "Optionally the real time and amount."
    ),
    schema={
        "type": "object",
        "properties": {
            "id": ID,
            "eaten_at": ENTRY_FIELDS["eaten_at"],
            "quantity": PLAN_FIELDS["quantity"],
            "unit": PLAN_FIELDS["unit"],
        },
        "required": ["id"],
        "additionalProperties": False,
    },
)
def mark_planned_meal_eaten(ctx, id, **args):
    meal = _own_meal(ctx, id)
    req = PlanEatSerializer(data=args)
    req.is_valid(raise_exception=True)
    meal, entry = writes.eat_plan(ctx.user, meal.pk, req.validated_data, channel=MCP)
    return {"planned": _meal_out(ctx, meal), "logged": _entry_out(ctx, entry), "day": _day(ctx, entry.local_date)}


@tool(
    "set_goal",
    title="Set a daily goal",
    scope=WRITE,
    consent=True,
    description=(
        "Set or clear one daily (24 h) nutrient goal: at_least (min), at_most (max), between (min and max), "
        "or none to stop tracking it. Applies from today; past days keep the goal they had."
    ),
    schema={
        "type": "object",
        "properties": {
            "nutrient": {"type": "string", "enum": list(nutrition.FIELDS)},
            "kind": {"type": "string", "enum": ["at_least", "at_most", "between", "none"]},
            "min": {"type": "number", "minimum": 0},
            "max": {"type": "number", "minimum": 0},
        },
        "required": ["nutrient", "kind"],
        "additionalProperties": False,
    },
)
def set_goal(ctx, nutrient, kind, min=None, max=None):
    goals = [
        {"nutrient": g.nutrient, "kind": g.kind, "min_value": g.min_value, "max_value": g.max_value}
        for g in nutrition.current_goals(ctx.user).values() if g.nutrient != nutrient
    ]
    goals.append({"nutrient": nutrient, "kind": kind, "min_value": min, "max_value": max})
    specs = nutrition.clean_goal_set(goals)
    changed = nutrition.save_goal_set(ctx.user, specs, nutrition.today_for(ctx.user), channel=MCP)
    current = goals_payload(ctx.user)
    return {"changed": bool(changed), "goals": [fmt.goal(g) for g in current["goals"]]}

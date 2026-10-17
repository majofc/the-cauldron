"""Nutrition read tools: today, the journal, adherence, goals, the meal plan.

All need the sensitive-data consent (``consent=True``) and read only the
caller's own rows, through ``nutrition_payloads`` — the same code the web uses.
"""

from datetime import timedelta

from the_cauldron import nutrition_payloads as payloads
from the_cauldron.mcp_server.errors import ToolError
from the_cauldron.mcp_server.registry import tool
from the_cauldron.mcp_server.tools import _format as fmt
from the_cauldron.services import adherence, nutrition, rolling

MAX_JOURNAL_DAYS = 31
DATE = {"type": "string", "format": "date", "description": "YYYY-MM-DD, in the user's timezone."}


def _range(ctx, start, end, default_days, max_days):
    first = nutrition.parse_day(start, ctx.user)
    last = nutrition.parse_day(end, ctx.user) if end else first + timedelta(days=default_days - 1)
    if last < first:
        raise ToolError("invalid_arguments", "'to' is before 'from'.")
    if (last - first).days >= max_days:
        raise ToolError("invalid_arguments", f"Ask for at most {max_days} days at a time.")
    return first, last


def _day(ctx, day):
    p = payloads.day_payload(ctx.user, day, ctx.tz, ctx.serializer_request)
    return {
        "date": p["date"],
        "logged": p["logged"],
        "entries": [fmt.entry(e) for e in p["entries"]],
        "totals": fmt.totals(p["totals"]),
        "goals": [fmt.goal(g) for g in p["goals"]],
    }


@tool(
    "get_today",
    title="Today",
    consent=True,
    description=(
        "The user's day so far, in their timezone: what they ate (entries with nutrients), the day's "
        "totals, each daily goal with its status (met / missed / uncertain / pending / not_logged), "
        "and the meals still planned for today with the projected end of day. Start here for "
        "\"how am I doing today?\" or before suggesting what to eat next."
    ),
)
def get_today(ctx):
    today = nutrition.today_for(ctx.user)
    plan = payloads.plan_day_payload(ctx.user, today, ctx.serializer_request)
    return {
        "now": fmt.local_now(ctx.tz),
        "timezone": str(ctx.tz),
        **_day(ctx, today),
        "planned_meals": [fmt.planned(m) for m in plan["meals"] if not m["eaten"]],
        "projected_with_plan": [fmt.goal(g) for g in plan["goals"]],
    }


@tool(
    "get_journal",
    title="Meal journal",
    consent=True,
    description=(
        "Logged meals day by day between two dates (inclusive, at most 31 days), with each day's totals "
        "and goal statuses. Days with nothing logged are left out. Entry ids are what update_meal and "
        "delete_meal take."
    ),
    schema={
        "type": "object",
        "properties": {"from": DATE, "to": {**DATE, "description": "Inclusive end; defaults to 'from'."}},
        "required": ["from"],
        "additionalProperties": False,
    },
)
def get_journal(ctx, **args):
    first, last = _range(ctx, args["from"], args.get("to"), 1, MAX_JOURNAL_DAYS)
    days = [_day(ctx, d) for d in nutrition.days_between(first, last)]
    return {"from": first, "to": last, "days": [d for d in days if d["logged"]]}


@tool(
    "get_adherence",
    title="Goal adherence",
    consent=True,
    description=(
        "How often the user meets their daily goals over the last 7, 30 or 90 days. "
        "time_in_range: share of hours the rolling 24-hour window was within each goal (hours with "
        "nothing logged don't count). For 7 and 30 days also by_day_goal: days met / missed / "
        "uncertain per nutrient, % met and current/best streaks."
    ),
    schema={
        "type": "object",
        "properties": {"days": {"type": "integer", "enum": [7, 30, 90], "default": 7}},
        "additionalProperties": False,
    },
)
def get_adherence(ctx, days=7):
    roll = rolling.rolling(ctx.user, "24h", days)
    out = {"days": days, "time_in_range": roll["summary"]}
    if days in adherence.WINDOWS:
        out["by_day_goal"] = adherence.adherence(ctx.user, days)["nutrients"]
    return out


@tool(
    "get_goals",
    title="Daily goals",
    consent=True,
    description=(
        "The user's active daily nutrient goals (at_least / at_most / between, per 24 h) and every past "
        "version with the dates it applied. No goal for a nutrient = not tracked."
    ),
)
def get_goals(ctx):
    current = payloads.goals_payload(ctx.user)
    return {
        "today": current["today"],
        "goals": [fmt.goal(g) for g in current["goals"]],
        "history": [fmt.goal(g) for g in payloads.goal_history(ctx.user)],
    }


@tool(
    "get_meal_plan",
    title="Meal plan",
    consent=True,
    description=(
        "Spells (recipes) the user planned per day and slot (breakfast, lunch, dinner, snack), "
        "from 'from' (default today) for 7 days or up to 'to' (at most 62 days). eaten = already logged."
    ),
    schema={
        "type": "object",
        "properties": {"from": DATE, "to": DATE},
        "additionalProperties": False,
    },
)
def get_meal_plan(ctx, **args):
    first, last = _range(ctx, args.get("from"), args.get("to"), 7, nutrition.MAX_RANGE_DAYS)
    meals = payloads.plan_range(ctx.user, first, last, ctx.serializer_request)
    return {"from": first, "to": last, "meals": [fmt.planned(m) for m in meals]}

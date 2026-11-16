"""Nutrition read tools: today, the journal, adherence, goals, the goal
breakdown (#107), the meal plan.

All need the sensitive-data consent (``consent=True``) and read only the
caller's own rows, through ``nutrition_payloads`` — the same code the web uses.
"""

from datetime import timedelta

from the_cauldron import nutrition_payloads as payloads
from the_cauldron.mcp_server.errors import ToolError
from the_cauldron.mcp_server.registry import tool
from the_cauldron.mcp_server.tools import _format as fmt
from the_cauldron.services import adherence, breakdown, nutrition, rolling
from the_cauldron.services.nutrition import NutritionInputError

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
        "Where the user stands now, over the last 24 hours (the same rolling window as their Journal, not "
        "the calendar day): every entry eaten in that window (newest first, with its date and nutrients), "
        "the window's totals (incomplete = some entry lacks that value), each daily goal judged final "
        "against today's goals (met / missed / uncertain / not_logged), the meals still planned from today "
        "on, and where today's Forge workout stands. Start here for \"how am I doing?\" or before "
        "suggesting what to eat next. For calendar days use get_journal."
    ),
)
def get_today(ctx):
    from django.utils import timezone

    from the_cauldron.mcp_server.tools.forge import forge_summary  # forge imports this module

    now = timezone.now()
    today = nutrition.today_for(ctx.user, now)
    window = payloads.window_payload(ctx.user, "24h", now, ctx.tz, ctx.serializer_request)
    entries = window["_entries"]
    totals = nutrition.entries_totals(entries)
    goals = nutrition.goals_on(nutrition.goal_rows_between(ctx.user, today, today), today)
    return {
        "now": fmt.local_now(ctx.tz),
        "timezone": str(ctx.tz),
        "window": {"span": "24h", "from": window["start"].strftime("%Y-%m-%dT%H:%M"),
                   "to": window["end"].strftime("%Y-%m-%dT%H:%M")},
        "logged": bool(entries),
        "entries": [fmt.entry(e) for e in window["entries"]],
        "totals": fmt.totals(nutrition.totals_payload(totals)),
        "goals": [fmt.goal(g) for g in nutrition.goal_statuses(goals, totals, logged=bool(entries), final=True)],
        "planned_meals": [fmt.planned(m) for m in window["planned"]],
        "forge": forge_summary(ctx.user),
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
        "nothing logged don't count); time_in_range_recommended: the same against the recommended goals "
        "(see get_goals). For 7 and 30 days also by_day_goal: days met / missed / "
        "uncertain per nutrient, % met and current/best streaks. With ahead (1–12) also projected: the "
        "next hours' 24-hour windows counting meals already scheduled and planned meals not eaten yet — "
        "estimates, each tick projected: true; they never change time_in_range."
    ),
    schema={
        "type": "object",
        "properties": {
            "days": {"type": "integer", "enum": [7, 30, 90], "default": 7},
            "ahead": {"type": "integer", "minimum": 0, "maximum": 12, "default": 0,
                      "description": "Hours to project past now (estimates)."},
        },
        "additionalProperties": False,
    },
)
def get_adherence(ctx, days=7, ahead=0):
    roll = rolling.rolling(ctx.user, "24h", days, ahead=ahead)
    out = {"days": days, "time_in_range": roll["sets"]["mine"]["summary"],
           "time_in_range_recommended": roll["sets"]["who"]["summary"]}
    if ahead:
        out["projected"] = _projected_ticks(roll)
    if days in adherence.WINDOWS:
        out["by_day_goal"] = adherence.adherence(ctx.user, days)["nutrients"]
    return out


def _projected_ticks(roll):
    """The projected ticks of a ``rolling`` payload (#132), one row each: the
    window's known values and each goal's status, flagged ``projected``."""
    rows = []
    for i, projected in enumerate(roll["projected"]):
        if not projected:
            continue
        rows.append({
            "at": roll["ticks"][i].strftime("%Y-%m-%dT%H:%M"),
            "projected": True,
            "values": {n: roll["values"][n][i] for n in roll["tracked"]},
            "incomplete": [n for n in roll["tracked"] if roll["missing"][n][i]],
            "status": {n: s[i] for n, s in roll["sets"]["mine"]["status"].items()},
            "status_recommended": {n: s[i] for n, s in roll["sets"]["who"]["status"].items()},
        })
    return rows


@tool(
    "get_goals",
    title="Daily goals",
    consent=True,
    description=(
        "Two goal sets, per 24 h (at_least / at_most / between). mine: the user's own active goals, plus every "
        "past version with the dates it applied (history); no goal for a nutrient = not tracked. recommended: "
        "WHO/FAO and NASEM DRI references from the user's weight, age, sex and activity level (each with its "
        "source), general references for healthy adults, not medical advice; status 'minor' = under 18, none "
        "given. assumed lists body data the user hasn't given (the most conservative value was used); "
        "needs_weight lists goals left out until a weight is set; unresolved, goals left out because the "
        "cautious values for an unknown sex or age contradict each other (ask for the missing data)."
    ),
)
def get_goals(ctx):
    current = payloads.goals_payload(ctx.user)
    rec = current["recommended"]
    return {
        "today": current["today"],
        "mine": [fmt.goal(g) for g in current["goals"]],
        "history": [fmt.goal(g) for g in payloads.goal_history(ctx.user)],
        "recommended": {
            "status": rec["status"],
            "assumed": rec["assumed"],
            "needs_weight": rec["needs_weight"],
            "unresolved": rec["unresolved"],
            "goals": [{"nutrient": g["nutrient"], "kind": g["kind"], "min": g["min_value"], "max": g["max_value"],
                       "source": g["source"], "note": g["note"]} for g in rec["goals"]],
            "no_recommendation": rec["none"],
        },
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


@tool(
    "get_goal_breakdown",
    title="What a goal's number is made of",
    consent=True,
    description=(
        "Break one nutrient's rolling-window total into what was eaten: the top 5 spells/reagents "
        "(entries combined) plus 'others' (pie, amounts add up to total), and the fog — everything lacking "
        "the value (never 0; while any remain the total is a lower bound). For span 7d amounts are daily "
        "averages. end defaults to now (local ISO, no offset). Each fog item says how it could be filled "
        "(reagents on the spell missing it, or quick entries); filling is done by the user on the website."
    ),
    schema={
        "type": "object",
        "properties": {
            "nutrient": {"type": "string", "enum": list(nutrition.FIELDS)},
            "span": {"type": "string", "enum": list(rolling.SPANS), "default": "24h"},
            "end": {"type": "string", "description": "Window end, local time, e.g. 2026-10-15T18:00."},
        },
        "required": ["nutrient"],
        "additionalProperties": False,
    },
)
def get_goal_breakdown(ctx, nutrient, span="24h", end=None):
    try:
        span, end_at, nutrient = breakdown.window_args(ctx.user, {"span": span, "end": end, "nutrient": nutrient})
    except NutritionInputError as exc:
        raise ToolError("invalid_arguments", str(exc)) from None
    return breakdown.breakdown(ctx.user, span, end_at, nutrient)

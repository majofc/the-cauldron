"""Goal adherence (#82): how often the daily nutrient goals are met.

Computed on read — nothing is stored. Each day × nutrient with a goal active
that day is ``met``, ``missed``, ``uncertain`` (missing data could flip it) or
``not_logged`` (no entries that day — never counted as a miss). Today isn't
over, so whatever more food could still change today is ``pending``: it neither
counts nor breaks a streak (an exceeded maximum today is already ``missed``).

- % met = met days / judged days (met + missed + uncertain) in the window.
- Streaks: consecutive met days; a missed day resets the run, while uncertain,
  pending, not-logged and no-goal days are skipped (they neither extend nor
  reset it). Streaks look back ``STREAK_LOOKBACK`` days, beyond the window.
"""

from collections import defaultdict
from datetime import timedelta

from the_cauldron.models import IntakeEntry
from the_cauldron.services import nutrition as nu

WINDOWS = (7, 30)
STREAK_LOOKBACK = 90
JUDGED = (nu.MET, nu.MISSED, nu.UNCERTAIN)


def day_statuses(goals, entries, *, final):
    """``{nutrient: status}`` for one day."""
    if not entries:
        return {nutrient: nu.NOT_LOGGED for nutrient in goals}
    totals = nu.entries_totals(entries)
    return {nutrient: nu.judge(goal, totals[nutrient], final=final) for nutrient, goal in goals.items()}


def calendar_status(statuses):
    if not statuses:
        return "no_goals"
    values = list(statuses.values())
    if all(v == nu.NOT_LOGGED for v in values):
        return nu.NOT_LOGGED
    if all(v == nu.MET for v in values):
        return "all_met"
    if all(v in (nu.MET, nu.PENDING) for v in values):
        return nu.PENDING  # today, nothing decided against yet
    if any(v == nu.MET for v in values):
        return "some_met"
    return "none_met"


def adherence(user, days, today=None):
    today = today or nu.today_for(user)
    window_start = today - timedelta(days=days - 1)
    lookback_start = today - timedelta(days=max(days, STREAK_LOOKBACK) - 1)

    by_day = defaultdict(list)
    for entry in IntakeEntry.objects.filter(user=user, local_date__gte=lookback_start, local_date__lte=today):
        by_day[entry.local_date].append(entry)
    rows = nu.goal_rows_between(user, lookback_start, today)

    stats = defaultdict(lambda: {
        "met": 0, "missed": 0, "uncertain": 0, "not_logged": 0, "run": 0, "best": 0,
    })
    calendar = []
    all_met_days = logged_days = 0
    for day in nu.days_between(lookback_start, today):
        goals = nu.goals_on(rows, day)
        statuses = day_statuses(goals, by_day.get(day, []), final=day < today)
        in_window = day >= window_start
        for nutrient, status in statuses.items():
            s = stats[nutrient]
            if in_window and status in JUDGED + (nu.NOT_LOGGED,):
                s[status] += 1
            if status == nu.MET:
                s["run"] += 1
                s["best"] = max(s["best"], s["run"])
            elif status == nu.MISSED:
                s["run"] = 0
        if in_window:
            logged = bool(by_day.get(day))
            logged_days += logged
            kind = calendar_status(statuses)
            all_met_days += kind == "all_met"
            calendar.append({
                "date": day,
                "logged": logged,
                "status": kind,
                "goals": len(statuses),
                "met": sum(1 for v in statuses.values() if v == nu.MET),
                "today": day == today,
            })

    current = nu.current_goals(user)
    tracked = [n for n in nu.FIELDS if n in stats or n in current]
    nutrients = []
    for nutrient in tracked:
        s = stats[nutrient]
        judged = s["met"] + s["missed"] + s["uncertain"]
        nutrients.append({
            "nutrient": nutrient,
            "active": nutrient in current,
            "met": s["met"],
            "missed": s["missed"],
            "uncertain": s["uncertain"],
            "not_logged": s["not_logged"],
            "judged": judged,
            "pct": round(s["met"] / judged * 100) if judged else None,
            "current_streak": s["run"],
            "best_streak": s["best"],
        })
    return {
        "days": days,
        "from": window_start,
        "to": today,
        "logged_days": logged_days,
        "all_met_days": all_met_days,
        "calendar": calendar,
        "nutrients": nutrients,
    }

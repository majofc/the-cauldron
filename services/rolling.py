"""Rolling windows and time in range (#85 follow-up).

"How am I doing right now" is a moving window, not a calendar day: the last
24 hours (or the last 7 days, as a daily average) up to a moment, judged against
the daily goals. The history samples that window every hour (every 3 hours past
30 days) and reports how much of the time each goal was in range — like a
glucose monitor's time in range.

- One tick = one window end. A tick's window holds the entries with
  ``end - span < eaten_at ≤ end``; the 7-day window is divided by 7, so it is
  compared with the same daily goal.
- Each tick is judged against two goal sets (#105): ``mine`` — the goals
  active on the tick's local date (versioned, see ``NutrientGoal``) — and
  ``who`` — today's recommendations (``services.recommendations``), which are
  never versioned, so the whole history is judged against the current ones.
- A window with no entries is ``not_logged``: it never counts as in or out of
  range. ``uncertain`` (missing data could flip it) is judged but not in range.
- A window is complete by definition, so it is judged as final: an unmet
  minimum over the last 24 hours is a miss now, whatever comes later.

Pure computation on one query of entries plus one of goals; nothing is stored.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from the_cauldron.models import IntakeEntry
from the_cauldron.services import nutrition as nu
from the_cauldron.services import recommendations
from the_cauldron.services.grimoire_nutrition import Total

SPANS = {"24h": 24, "7d": 7 * 24}
HISTORY_DAYS = (7, 30, 90)
JUDGED = (nu.MET, nu.MISSED, nu.UNCERTAIN)


def step_hours(days):
    return 1 if days <= 30 else 3


def ticks_for(now, days):
    """Window ends: on the hour, every ``step_hours`` back ``days`` days, plus
    ``now`` itself when it isn't on the hour."""
    step = timedelta(hours=step_hours(days))
    end = now.replace(minute=0, second=0, microsecond=0)
    count = int(timedelta(days=days) / step)
    ticks = [end - step * i for i in range(count - 1, -1, -1)]
    if now > end:
        ticks.append(now)
    return ticks


def _prefix(entries):
    """Per-nutrient running sums of values and of entries lacking each one."""
    values = {n: [0.0] for n in nu.FIELDS}
    missing = {n: [0] for n in nu.FIELDS}
    for entry in entries:
        for n in nu.FIELDS:
            values[n].append(values[n][-1] + (entry.nutrients.get(n) or 0.0))
            missing[n].append(missing[n][-1] + (1 if entry.missing.get(n) else 0))
    return values, missing


def rolling(user, span, days, now=None):
    """The window ``span`` evaluated at every tick of the last ``days`` days.
    ``values`` / ``missing`` are shared; each goal set in ``sets`` has its own
    ``status`` / ``low`` / ``high`` / ``summary`` / ``daily``."""
    hours = SPANS[span]
    per_day = hours / 24
    window = timedelta(hours=hours)
    now = now or timezone.now()
    tz = nu.user_tz(user)
    ticks = ticks_for(now, days)

    entries = list(
        IntakeEntry.objects.filter(user=user, eaten_at__gt=ticks[0] - window, eaten_at__lte=now)
        .order_by("eaten_at", "created_at")
    )
    times = [e.eaten_at for e in entries]
    pv, pm = _prefix(entries)
    local_days = [timezone.localtime(t, tz).date() for t in ticks]

    values = {n: [] for n in nu.FIELDS}
    missing = {n: [] for n in nu.FIELDS}
    logged = []
    for tick in ticks:
        lo, hi = bisect_right(times, tick - window), bisect_right(times, tick)
        logged.append(hi > lo)
        for n in nu.FIELDS:
            values[n].append(round((pv[n][hi] - pv[n][lo]) / per_day, 2))
            missing[n].append(pm[n][hi] - pm[n][lo])

    rows = nu.goal_rows_between(user, local_days[0], local_days[-1])
    goals_cache = {}

    def my_goals(day):
        if day not in goals_cache:
            goals_cache[day] = nu.goals_on(rows, day)
        return goals_cache[day]

    rec = recommendations.recommended_for(user, today=nu.today_for(user, now))
    mine = _judge_set([n for n in nu.FIELDS if any(g.nutrient == n for g in rows)],
                      my_goals, local_days, values, missing, logged)
    who = _judge_set([n for n in nu.FIELDS if n in rec["goals"]],
                     lambda day: rec["goals"], local_days, values, missing, logged)
    who["status_reason"] = rec["status"]

    return {
        "span": span,
        "days": days,
        "step_hours": step_hours(days),
        # On the user's clock ("…T19:00:00-06:00"): the page reads date and hour
        # straight off the string, so UTC here would show tomorrow's 01:00.
        "now": timezone.localtime(now, tz),
        "ticks": [timezone.localtime(t, tz) for t in ticks],
        "logged": logged,
        "values": values,
        "missing": missing,
        "sets": {"mine": mine, "who": who},
        "tracked": [n for n in nu.FIELDS if n in mine["status"] or n in who["status"]],
        "current_goals": [nu.goal_payload(g) for n, g in sorted(
            nu.current_goals(user).items(), key=lambda kv: nu.FIELDS.index(kv[0]))],
    }


def _judge_set(tracked, goals_for, local_days, values, missing, logged):
    """One goal set judged at every tick: ``goals_for(day)`` → ``{nutrient: goal}``."""
    status = {n: [] for n in tracked}
    low = {n: [] for n in tracked}
    high = {n: [] for n in tracked}
    for i, day in enumerate(local_days):
        goals = goals_for(day)
        for n in tracked:
            goal = goals.get(n)
            if goal is None:
                status[n].append(None)
                low[n].append(None)
                high[n].append(None)
                continue
            g_low, g_high = nu.bounds(goal)
            low[n].append(g_low)
            high[n].append(g_high)
            status[n].append(
                nu.judge(goal, Total(values[n][i], missing[n][i])) if logged[i] else nu.NOT_LOGGED
            )
    return {
        "status": status,
        "low": low,
        "high": high,
        "summary": [_summary(n, status[n]) for n in tracked],
        "daily": _daily(tracked, status, local_days),
    }


def _summary(nutrient, statuses):
    counts = defaultdict(int)
    for s in statuses:
        if s is not None:
            counts[s] += 1
    judged = sum(counts[s] for s in JUDGED)
    return {
        "nutrient": nutrient,
        "in_range": counts[nu.MET],
        "out_of_range": counts[nu.MISSED],
        "uncertain": counts[nu.UNCERTAIN],
        "not_logged": counts[nu.NOT_LOGGED],
        "judged": judged,
        "pct": round(counts[nu.MET] / judged * 100) if judged else None,
    }


def _daily(tracked, status, local_days):
    """Per local day and nutrient: share of that day's judged ticks in range."""
    out = {}
    for n in tracked:
        days = {}
        for s, day in zip(status[n], local_days):
            if s in JUDGED:
                met, judged = days.get(day, (0, 0))
                days[day] = (met + (s == nu.MET), judged + 1)
        out[n] = [
            {"date": day, "pct": round(met / judged * 100), "judged": judged}
            for day, (met, judged) in sorted(days.items())
        ]
    return out

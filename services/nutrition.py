"""Daily nutrition (#80, #81, #83): timezone, versioned goals, meal-log
snapshots, day totals, goal status and the remaining-budget portion.

Rules
- A day runs midnight to midnight in the user's timezone (``CauldronProfile``).
- Goals are per nutrient and per day; a change closes the active row and opens
  a new one dated today, so a past day keeps the goal it had.
- An unknown nutrient value is never 0. Totals carry how many entries lacked
  it (``missing``) and show as "≥ x"; a verdict that missing data could flip
  is ``uncertain``, never ``met`` or ``missed``.
- A logged entry keeps a snapshot (``IntakeEntry.basis``): changing its amount
  rescales that snapshot, so editing the spell afterwards changes nothing.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from the_cauldron.models import (
    ADDED_SUGAR_ERROR,
    CauldronProfile,
    IntakeEntry,
    NutrientGoal,
    PlannedMeal,
    ReagentNutrients,
    added_sugar_over_total,
)
from the_cauldron.models.nutrition import DEFAULT_TIMEZONE
from the_cauldron.services import grimoire, grimoire_nutrition
from the_cauldron.services.grimoire import ConversionError, to_grams
from the_cauldron.services.grimoire_nutrition import Goal, Portion, Total

FIELDS = ReagentNutrients.FIELDS
KINDS = tuple(NutrientGoal.Kind.values)
MAX_RANGE_DAYS = 62
SPELL_UNITS = ("g", "serving")

MET, MISSED, UNCERTAIN, PENDING, NOT_LOGGED = "met", "missed", "uncertain", "pending", "not_logged"


class NutritionInputError(ValueError):
    """Input the day maths can't use (bad goal, unconvertible amount…)."""


# ── Timezone ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def timezone_names():
    return frozenset(available_timezones())


def get_profile(user):
    profile, _created = CauldronProfile.objects.get_or_create(user=user)
    return profile


def user_tz(user):
    try:
        return ZoneInfo(get_profile(user).timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def today_for(user, now=None):
    return timezone.localtime(now or timezone.now(), user_tz(user)).date()


def parse_day(raw, user):
    """``YYYY-MM-DD`` → date; blank → today in the user's timezone."""
    if raw in (None, ""):
        return today_for(user)
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise NutritionInputError(_("Use a date like 2026-10-15.")) from None


def localize(dt, tz):
    """An aware datetime for ``dt``: a naive one is read as wall-clock time in ``tz``."""
    if timezone.is_naive(dt):
        return dt.replace(tzinfo=tz)
    return dt


def days_between(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


# ── Goals ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoalSpec:
    nutrient: str
    kind: str
    min_value: float = None
    max_value: float = None

    def same_as(self, goal):
        return (
            goal.kind == self.kind
            and goal.min_value == self.min_value
            and goal.max_value == self.max_value
        )


def _number(value, label):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise NutritionInputError(_("%(label)s must be a number.") % {"label": label}) from None
    if not math.isfinite(number) or number < 0:
        raise NutritionInputError(_("%(label)s must be 0 or more.") % {"label": label})
    return number


def clean_goal_set(raw_goals):
    """``[{nutrient, kind, min_value, max_value}]`` → ``[GoalSpec]``. ``kind``
    ``none`` (or a missing row) means no goal for that nutrient."""
    if not isinstance(raw_goals, list):
        raise NutritionInputError(_("Send a list of goals."))
    specs, seen = [], set()
    for raw in raw_goals:
        if not isinstance(raw, dict):
            raise NutritionInputError(_("Send a list of goals."))
        nutrient = raw.get("nutrient")
        if nutrient not in FIELDS:
            raise NutritionInputError(_("Unknown nutrient."))
        if nutrient in seen:
            raise NutritionInputError(_("Only one goal per nutrient."))
        seen.add(nutrient)
        kind = raw.get("kind") or "none"
        if kind == "none":
            continue
        if kind not in KINDS:
            raise NutritionInputError(_("Unknown goal type."))
        low = _number(raw.get("min_value"), _("The minimum"))
        high = _number(raw.get("max_value"), _("The maximum"))
        if kind == NutrientGoal.Kind.AT_LEAST:
            if low is None:
                raise NutritionInputError(_("An “at least” goal needs a minimum."))
            high = None
        elif kind == NutrientGoal.Kind.AT_MOST:
            if high is None:
                raise NutritionInputError(_("An “at most” goal needs a maximum."))
            low = None
        else:
            if low is None or high is None:
                raise NutritionInputError(_("A “between” goal needs a minimum and a maximum."))
            if low > high:
                raise NutritionInputError(_("A goal's minimum can't be above its maximum."))
        specs.append(GoalSpec(nutrient, kind, low, high))
    return specs


def current_goals(user):
    """The active set: ``{nutrient: NutrientGoal}``."""
    return {g.nutrient: g for g in NutrientGoal.objects.filter(user=user, valid_to__isnull=True)}


def save_goal_set(user, specs, today, *, channel=None):
    """Make ``specs`` the active set. Unchanged goals keep their row; a changed
    or cleared one is closed today (``valid_to``) and a changed one reopens as a
    new row from today. Returns how many goals changed. ``channel`` marks
    where the new rows came from (web / MCP)."""
    wanted = {spec.nutrient: spec for spec in specs}
    changed = 0
    get_profile(user)
    with transaction.atomic():
        # Lock the user's profile row so two saves (a double submit) run one after
        # the other: locking the goal rows alone locks nothing when there are none.
        CauldronProfile.objects.select_for_update().get(user=user)
        active = {
            g.nutrient: g
            for g in NutrientGoal.objects.select_for_update().filter(user=user, valid_to__isnull=True)
        }
        for nutrient, goal in active.items():
            spec = wanted.get(nutrient)
            if spec is not None and spec.same_as(goal):
                continue
            goal.valid_to = today
            goal.save(update_fields=["valid_to", "updated_at"])
            changed += 1
        for nutrient, spec in wanted.items():
            goal = active.get(nutrient)
            if goal is not None and spec.same_as(goal):
                continue
            NutrientGoal.objects.create(
                user=user, nutrient=nutrient, kind=spec.kind,
                min_value=spec.min_value, max_value=spec.max_value, valid_from=today,
                **({"channel": channel} if channel else {}),
            )
            if goal is None:
                changed += 1
    return changed


def goal_rows_between(user, start, end):
    """Every goal row active on at least one day of ``start``…``end``."""
    # A goal changed twice in one day leaves a row with valid_to == valid_from:
    # it was never active on any day.
    return list(
        NutrientGoal.objects.filter(user=user, valid_from__lte=end)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=start))
        .exclude(valid_to=F("valid_from"))
    )


def goals_on(rows, day):
    """``{nutrient: goal}`` active on ``day`` among ``rows``."""
    return {
        g.nutrient: g for g in rows
        if g.valid_from <= day and (g.valid_to is None or day < g.valid_to)
    }


def bounds(goal):
    low = goal.min_value if goal.kind in (NutrientGoal.Kind.AT_LEAST, NutrientGoal.Kind.BETWEEN) else None
    high = goal.max_value if goal.kind in (NutrientGoal.Kind.AT_MOST, NutrientGoal.Kind.BETWEEN) else None
    return low, high


def judge(goal, total, *, final=True):
    """``met`` / ``missed`` / ``uncertain`` for one day's ``Total`` of the
    goal's nutrient. Missing data can only push the real value up: it can't
    rescue an exceeded maximum, but it leaves an unmet minimum or a respected
    maximum undecided.

    With ``final=False`` (the day isn't over) only what more food can't change
    is decided: an exceeded maximum is ``missed`` and a reached minimum with no
    maximum is ``met``; anything else is ``pending``."""
    low, high = bounds(goal)
    if high is not None and total.value > high:
        return MISSED
    if not final:
        return MET if high is None and (low is None or total.value >= low) else PENDING
    if low is not None and total.value < low:
        return UNCERTAIN if total.missing else MISSED
    if high is not None and total.missing:
        return UNCERTAIN
    return MET


# ── Snapshots ───────────────────────────────────────────────────────────────


def _round(value):
    return round(value, 4)


def spell_basis_snapshot(spell, user):
    """Snapshot basis of a spell's cooked dish as the user sees it now."""
    basis = grimoire_nutrition.spell_basis(spell, user)
    if basis.cooked_g <= 0:
        raise NutritionInputError(_("This spell has no weighed ingredients."))
    per = basis.per_100g_cooked()
    snapshot = {
        "per_100g": {name: _round(total.value) for name, total in per.items()},
        "missing": {name: total.missing for name, total in per.items() if total.missing},
        "serving_g": _round(basis.cooked_g / spell.servings),
        "yield_source": basis.yld.source,
    }
    if any(line.reagent.nutrients_estimated for line in basis.lines):
        snapshot["estimated"] = True
    return snapshot


def reagent_basis_snapshot(reagent):
    profile = getattr(reagent, "nutrients", None)
    per, missing = {}, {}
    for name in FIELDS:
        value = getattr(profile, name) if profile else None
        if value is None:
            missing[name] = 1
        else:
            per[name] = value
    snapshot = {"per_100g": per, "missing": missing}
    if reagent.nutrients_estimated:
        snapshot["estimated"] = True
    return snapshot


def spell_grams(basis, quantity, unit):
    if unit == "g":
        return quantity
    if unit == "serving":
        return quantity * basis["serving_g"]
    raise NutritionInputError(_("Use grams or servings for a spell."))


MASS_G = {"g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}


def reagent_grams(reagent, quantity, unit):
    """Grams of a reagent amount. With the reagent gone, only mass units work."""
    if reagent is None:
        if unit in MASS_G:
            return quantity * MASS_G[unit]
        raise NutritionInputError(_("That ingredient is gone — use a weight."))
    try:
        return to_grams(reagent, quantity, unit)
    except ConversionError as exc:
        raise NutritionInputError(str(exc)) from None


def totals_from_basis(basis, grams):
    nutrients = {name: round(per * grams / 100.0, 3) for name, per in basis.get("per_100g", {}).items()}
    missing = dict(basis.get("missing", {})) if grams > 0 else {}
    return nutrients, missing


def clean_quick_values(raw):
    """Typed-in values of a quick entry. Left blank = unknown, not 0."""
    if not isinstance(raw, dict):
        raise NutritionInputError(_("Send the values as an object."))
    values = {}
    for name, value in raw.items():
        if name not in FIELDS:
            raise NutritionInputError(_("Unknown nutrient."))
        number = _number(value, name)
        if number is not None:
            values[name] = number
    if not values:
        raise NutritionInputError(_("Enter at least one value, e.g. the calories."))
    if added_sugar_over_total(values.get("sugar_g"), values.get("added_sugar_g")):
        raise NutritionInputError(str(ADDED_SUGAR_ERROR))
    return values, {name: 1 for name in FIELDS if name not in values}


# ── Day totals ──────────────────────────────────────────────────────────────


def empty_totals():
    return {name: Total(0.0, 0) for name in FIELDS}


def add_totals(totals, nutrients, missing):
    """Add one entry (or planned meal) to ``totals``. ``missing`` counts entries."""
    out = {}
    for name in FIELDS:
        total = totals[name]
        out[name] = Total(total.value + (nutrients.get(name) or 0.0), total.missing + (1 if missing.get(name) else 0))
    return out


def entries_totals(entries):
    totals = empty_totals()
    for entry in entries:
        totals = add_totals(totals, entry.nutrients, entry.missing)
    return totals


def totals_payload(totals):
    return [{"key": name, "value": round(t.value, 3), "missing": t.missing} for name, t in totals.items()]


def goal_payload(goal):
    return {
        "nutrient": goal.nutrient,
        "kind": goal.kind,
        "min_value": goal.min_value,
        "max_value": goal.max_value,
        "valid_from": goal.valid_from,
        "valid_to": goal.valid_to,
    }


def goal_statuses(goals, totals, *, logged, final):
    out = []
    for nutrient, goal in sorted(goals.items(), key=lambda kv: FIELDS.index(kv[0])):
        total = totals[nutrient]
        out.append({
            **goal_payload(goal),
            "value": round(total.value, 3),
            "missing": total.missing,
            "status": judge(goal, total, final=final) if logged else NOT_LOGGED,
        })
    return out


def entries_on(user, day):
    return list(IntakeEntry.objects.filter(user=user, local_date=day))


class SpellTotalsCache:
    """Live totals of planned meals, one ``spell_basis`` per spell per request."""

    def __init__(self, user):
        self.user = user
        self._bases = {}

    def basis(self, spell):
        if spell.pk not in self._bases:
            try:
                self._bases[spell.pk] = spell_basis_snapshot(spell, self.user)
            except NutritionInputError:
                self._bases[spell.pk] = None
        return self._bases[spell.pk]

    def meal_totals(self, meal):
        """``(nutrients, missing, grams)`` of a planned meal, or None when its
        spell is gone, no longer readable or has nothing weighed."""
        spell = meal.spell
        if spell is None or not grimoire.can_read(spell, self.user):
            return None
        basis = self.basis(spell)
        if basis is None:
            return None
        grams = spell_grams(basis, meal.quantity, meal.unit)
        nutrients, missing = totals_from_basis(basis, grams)
        return nutrients, missing, grams


def planned_on(user, day):
    meals = list(
        PlannedMeal.objects.filter(user=user, date=day).select_related("spell", "intake_entry")
    )
    order = {slot: i for i, slot in enumerate(PlannedMeal.Slot.values)}
    return sorted(meals, key=lambda m: (order.get(m.slot, 9), m.order, m.created_at))


def consumed_and_planned(user, day, cache=None):
    """Totals of what's eaten on ``day`` plus what's still planned for it."""
    cache = cache or SpellTotalsCache(user)
    totals = entries_totals(entries_on(user, day))
    for meal in planned_on(user, day):
        if meal.intake_entry_id:
            continue  # already counted as an entry
        live = cache.meal_totals(meal)
        if live:
            totals = add_totals(totals, live[0], live[1])
    return totals


# ── Remaining-budget portion (#80, #83) ─────────────────────────────────────


def recommend_within_budget(per_100g, goals, consumed):
    """How much of a cooked dish fits what's left of today's goals.

    Each goal's bounds minus what's already eaten or planned: an "at most"
    leaves ``max - consumed`` (0 once exceeded), an "at least" still needs
    ``min - consumed`` (nothing once met). A remaining minimum of a nutrient the
    dish doesn't have is listed in ``not_covered`` — other food has to bring it
    — instead of making every portion fail. Returns the ``Portion`` and the list
    of nutrients whose maximum is already used up (``over``).
    """
    left, not_covered, incomplete = [], [], []
    for nutrient, goal in goals.items():
        low, high = bounds(goal)
        used = consumed[nutrient]
        dish = per_100g[nutrient]
        if (used.missing or dish.missing) and nutrient not in incomplete:
            incomplete.append(nutrient)
        need = low - used.value if low is not None and low - used.value > 0 else None
        if need is not None and dish.value <= 0:
            # A known zero means other food must bring it. Unknown (missing data) is
            # never a zero: it is flagged incomplete above and sets no bound.
            if not dish.missing:
                not_covered.append(nutrient)
            need = None
        room = max(0.0, high - used.value) if high is not None else None
        if need is None and room is None:
            continue
        left.append(Goal(nutrient, need, room))
    if not left:
        return Portion(min_g=0.0, incomplete=incomplete), not_covered, []
    portion = grimoire_nutrition.recommend_portion(per_100g, left)
    portion.incomplete = incomplete
    over = [g.nutrient for g in left if g.at_most == 0 and per_100g[g.nutrient].value > 0]
    if portion.feasible and portion.max_g == 0 and over:
        portion = Portion(conflicts=over, incomplete=incomplete)
    return portion, not_covered, over

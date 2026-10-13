"""Grimoire spell nutrition (#70): totals, cooked yield, portion recommender.

No writes. Nutrient values are per 100 g of each reagent (``ReagentNutrients``),
and null means *unknown*: an unknown value is never counted as 0, it makes the
total *incomplete* ("≥ 12 g, 2 ingredients missing data").

Cooked weight: cooking loses (or gains) water, so "per 100 g" of the dish uses
the cooked weight, resolved in this order — the user's latest casting with the
pot weighed, the spell's ``cooked_yield_g``, the spell's ``yield_pct``, and
finally the raw weight itself, flagged ``unknown``.
"""

import math
from dataclasses import dataclass, field

from django.utils.translation import gettext as _

from the_cauldron.models import ReagentNutrients
from the_cauldron.services.grimoire import ConversionError, to_grams

FIELDS = ReagentNutrients.FIELDS
# A reagent missing any of these is named in the UI as "no basic data".
CORE = ("kcal", "protein_g", "carbs_g", "fat_g")
MAX_GOALS = 10


class NutritionError(ValueError):
    """A portion goal set that can't be evaluated (bad nutrient, bad bounds)."""


@dataclass(frozen=True)
class Ingredient:
    """One spell line: its grams and its reagent's per-100 g values (``None``
    for no profile at all). ``key`` identifies the reagent."""

    key: object
    grams: float
    per_100g: dict = None


@dataclass(frozen=True)
class Total:
    value: float  # sum over the ingredients whose value is known
    missing: int = 0  # ingredients (distinct reagents) with no value for it

    @property
    def complete(self):
        return self.missing == 0

    def times(self, factor):
        return Total(self.value * factor, self.missing)


# ── Totals ──────────────────────────────────────────────────────────────────


def batch_totals(ingredients):
    """``{field: Total}`` for the whole batch. A reagent with grams > 0 but no
    value counts as missing once, however many lines it's on."""
    totals = {}
    for name in FIELDS:
        value = 0.0
        missing = set()
        for ing in ingredients:
            per = (ing.per_100g or {}).get(name)
            if per is None:
                if ing.grams > 0:
                    missing.add(ing.key)
                continue
            value += ing.grams * per / 100.0
        totals[name] = Total(value, len(missing))
    return totals


def scaled(totals, factor):
    return {name: total.times(factor) for name, total in totals.items()}


# ── Cooked yield ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Yield:
    ratio: float  # cooked weight / raw weight
    source: str  # casting | spell_weight | spell_pct | unknown
    cast_on: object = None

    @property
    def known(self):
        return self.source != "unknown"


def casting_raw_grams(lines, adjustments):
    """Raw grams of the batch as it was actually cast: the spell's lines with
    the casting's changed amounts swapped in. An amount in the line's own unit
    scales the line's weight; another unit goes through ``to_grams``; one that
    can't convert keeps the spell's weight (the casting can't tell us more).
    """
    grams = [line.grams for line in lines]
    used = set()
    for adj in adjustments or []:
        index = adj.get("line")
        if not (isinstance(index, int) and 0 <= index < len(lines) and index not in used
                and str(lines[index].reagent.uuid) == str(adj.get("reagent"))):
            index = next((i for i, line in enumerate(lines)
                          if i not in used and str(line.reagent.uuid) == str(adj.get("reagent"))), None)
        if index is None:
            continue  # the line is gone from the spell
        used.add(index)
        line = lines[index]
        qty, unit = adj.get("used_qty"), adj.get("unit")
        if qty is None:
            continue
        if unit == line.unit and line.quantity > 0:
            grams[index] = line.grams * float(qty) / line.quantity
            continue
        try:
            grams[index] = to_grams(line.reagent, qty, unit)
        except (ConversionError, ValueError, TypeError):
            pass
    return sum(grams)


def resolve_yield(raw_g, *, casting_ratio=None, cast_on=None, cooked_yield_g=None, yield_pct=None):
    """Cooked/raw ratio from the best source available (see module docstring)."""
    if casting_ratio and casting_ratio > 0:
        return Yield(casting_ratio, "casting", cast_on)
    if cooked_yield_g and raw_g > 0:
        return Yield(cooked_yield_g / raw_g, "spell_weight")
    if yield_pct:
        return Yield(yield_pct / 100.0, "spell_pct")
    return Yield(1.0, "unknown")


# ── Portion recommender ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Goal:
    nutrient: str
    at_least: float = None
    at_most: float = None


@dataclass
class Portion:
    min_g: float = None  # None → no portion fits
    max_g: float = None  # None → no upper limit (with min_g set)
    conflicts: list = field(default_factory=list)  # nutrients whose goals can't all hold
    incomplete: list = field(default_factory=list)  # goal nutrients with missing data

    @property
    def feasible(self):
        return self.min_g is not None


def _finite_positive(value, label):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise NutritionError(_("%(label)s must be a number.") % {"label": label}) from None
    if not math.isfinite(number) or number < 0:
        raise NutritionError(_("%(label)s must be 0 or more.") % {"label": label})
    return number


def clean_goals(raw_goals):
    """Validate ``[{nutrient, at_least, at_most}]`` into ``Goal`` objects."""
    if not raw_goals:
        raise NutritionError(_("Add at least one goal."))
    if len(raw_goals) > MAX_GOALS:
        raise NutritionError(_("At most %(n)s goals.") % {"n": MAX_GOALS})
    goals = []
    for raw in raw_goals:
        nutrient = raw.get("nutrient")
        if nutrient not in FIELDS:
            raise NutritionError(_("Unknown nutrient."))
        low = _finite_positive(raw.get("at_least"), _("The minimum"))
        high = _finite_positive(raw.get("at_most"), _("The maximum"))
        if low is None and high is None:
            raise NutritionError(_("Each goal needs a minimum, a maximum or both."))
        if low is not None and high is not None and low > high:
            raise NutritionError(_("A goal's minimum can't be above its maximum."))
        goals.append(Goal(nutrient, low, high))
    return goals


def recommend_portion(per_100g, goals):
    """Range of grams of the cooked dish meeting every goal.

    ``per_100g`` is ``{field: Total}`` per 100 g cooked. Each goal turns into a
    lower and/or upper bound on grams (nutrient content is proportional to the
    portion). When the tightest lower bound passes the tightest upper bound,
    the goals setting those two bounds are the conflict. A goal on a nutrient
    with missing data still counts, and is listed in ``incomplete``: the real
    amount can only be higher, so an "at most" may be optimistic.
    """
    low, low_by = 0.0, []
    high, high_by = math.inf, []
    impossible = []
    incomplete = []
    for goal in goals:
        total = per_100g[goal.nutrient]
        if not total.complete and goal.nutrient not in incomplete:
            incomplete.append(goal.nutrient)
        per_g = total.value / 100.0
        if goal.at_least:
            if per_g <= 0:
                impossible.append(goal.nutrient)  # no amount of this dish gets there
            else:
                bound = goal.at_least / per_g
                if bound > low:
                    low, low_by = bound, [goal.nutrient]
                elif bound == low:
                    low_by.append(goal.nutrient)
        if goal.at_most is not None and per_g > 0:
            bound = goal.at_most / per_g
            if bound < high:
                high, high_by = bound, [goal.nutrient]
            elif bound == high:
                high_by.append(goal.nutrient)
    if impossible:
        return Portion(conflicts=impossible, incomplete=incomplete)
    if low > high:
        conflicts = list(dict.fromkeys(low_by + high_by))
        return Portion(conflicts=conflicts, incomplete=incomplete)
    return Portion(
        min_g=round(low, 1),
        max_g=None if high == math.inf else round(high, 1),
        incomplete=incomplete,
    )

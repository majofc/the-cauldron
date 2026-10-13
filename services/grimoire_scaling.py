"""Grimoire scaling (#69): pure maths, no queries and no writes.

Scale a spell's lines by a factor, to a number of servings, to a total weight,
or from what the user has on hand ("I only have 300 g of chicken"), where the
reagent that runs out first sets the factor for everything else.

Quantities and grams stay exact (the nutrition maths uses them); only
``display_quantity`` is rounded to something you can measure in a kitchen.
"""

import math
from dataclasses import dataclass

from django.utils.translation import gettext as _

# Beyond this the "recipe" is a different recipe — and it keeps the maths sane.
MAX_FACTOR = 100.0


class ScalingError(ValueError):
    """The scaling request can't be answered (zero/negative input, nothing to scale)."""


@dataclass(frozen=True)
class Line:
    """One spell line as the scaler sees it. ``key`` identifies the reagent
    (a pk or uuid); two lines may share it."""

    key: object
    quantity: float
    unit: str
    grams: float


@dataclass(frozen=True)
class ScaledLine:
    key: object
    unit: str
    quantity: float  # exact
    grams: float  # exact
    display_quantity: float  # rounded for the kitchen


@dataclass(frozen=True)
class Scaled:
    factor: float
    lines: list
    limiting: object = None  # key of the reagent that set the factor (scale_by_available)


def _positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ScalingError(_("%(label)s must be a number.") % {"label": label}) from None
    if not math.isfinite(number) or number <= 0:
        raise ScalingError(_("%(label)s must be more than 0.") % {"label": label})
    return number


def _round_to(value, step):
    return round(round(value / step) * step, 6)


def display_quantity(quantity, unit):
    """Practical rounding: pieces to ½ (never "0.37 eggs"), spoons and cups to ¼,
    grams/ml to 5 above 100, metric/imperial bulk units to two decimals.
    Anything above zero stays above zero, at the smallest step."""
    if quantity <= 0:
        return 0.0
    if unit == "piece":
        return max(_round_to(quantity, 0.5), 0.5)
    if unit in ("tsp", "tbsp", "cup"):
        return max(_round_to(quantity, 0.25), 0.25)
    if unit in ("g", "ml"):
        if quantity > 100:
            return _round_to(quantity, 5)
        if quantity >= 10:
            return float(round(quantity))
        return max(round(quantity, 1), 0.1)
    if unit == "oz":
        return max(round(quantity, 1 if quantity >= 1 else 2), 0.01)
    return max(round(quantity, 2), 0.01)  # kg, l, lb


def scale_by_factor(lines, factor):
    factor = _positive(factor, _("The factor"))
    if factor > MAX_FACTOR:
        raise ScalingError(_("Scale by at most %(n)s×.") % {"n": int(MAX_FACTOR)})
    scaled = [
        ScaledLine(
            key=line.key,
            unit=line.unit,
            quantity=line.quantity * factor,
            grams=line.grams * factor,
            display_quantity=display_quantity(line.quantity * factor, line.unit),
        )
        for line in lines
    ]
    return Scaled(factor=factor, lines=scaled)


def scale_to_servings(lines, servings, target_servings):
    """Every line × ``target_servings / servings``."""
    servings = _positive(servings, _("The spell's servings"))
    target = _positive(target_servings, _("Servings"))
    return scale_by_factor(lines, target / servings)


def scale_to_total_grams(lines, target_grams):
    """Scale so the lines weigh ``target_grams`` together."""
    target = _positive(target_grams, _("The total weight"))
    total = sum(line.grams for line in lines)
    if total <= 0:
        raise ScalingError(_("This spell has no weighed ingredients to scale by."))
    return scale_by_factor(lines, target / total)


def scale_by_available(lines, available):
    """Scale from what the user has: ``available`` maps a reagent key to the
    grams on hand. The smallest available/required ratio wins, and that
    reagent is reported as ``limiting``. A reagent on several lines counts
    all of them; one the spell needs 0 g of can't limit anything."""
    if not available:
        raise ScalingError(_("Enter how much you have of at least one ingredient."))
    required = {}
    for line in lines:
        required[line.key] = required.get(line.key, 0.0) + line.grams
    best = None
    for key, grams in available.items():
        if key not in required:
            raise ScalingError(_("That ingredient isn't in this spell."))
        grams = _positive(grams, _("The amount you have"))
        if required[key] <= 0:
            continue
        ratio = grams / required[key]
        if best is None or ratio < best[0]:
            best = (ratio, key)
    if best is None:
        raise ScalingError(_("None of those ingredients has an amount to scale by."))
    ratio, key = best
    scaled = scale_by_factor(lines, min(ratio, MAX_FACTOR))
    return Scaled(factor=scaled.factor, lines=scaled.lines, limiting=key)

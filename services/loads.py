"""Buildable-load engine — what the user can actually assemble, and how.

Pure logic: takes a profile-shaped object (anything with the equipment fields)
and an exercise-shaped object, returns loads that can be physically built from
the plates on hand. No Django/ORM imports, so it unit-tests without a DB.

Three loading geometries, because the plate arithmetic genuinely differs:

* **Dumbbells** — a *matched pair*. One step of a denomination means one plate on
  each side of each of the two handles, so it costs **4** plates. The figure we
  prescribe is the weight of *one* dumbbell.
* **Barbell** — one bar, symmetric. One step costs **2** plates and the total is
  ``bar + 2 × (per side)``.
* **Adjustable kettlebell** — plates stack inside one shell, no symmetry. One
  step costs **1** plate and the total is ``handle + Σ plates``.

The bare bar / empty handle is itself a valid load and is normally the lightest.
Recipes are minimal-plate greedy (heaviest denomination first) so the
instruction is deterministic and matches what a person actually does.
"""

import logging
from dataclasses import dataclass, field
from itertools import product
from typing import Optional

logger = logging.getLogger(__name__)

# Guard rails so an absurd inventory cannot hang a request. What gets dropped is
# logged rather than silently discarded.
MAX_DENOMINATIONS = 8
MAX_LOADS = 200

# Totals are compared and de-duplicated at this precision — plate weights are
# real numbers like 1.25, and float noise must not split one load into two.
_PRECISION = 2


@dataclass
class Load:
    """One assemblable load.

    ``per_side`` is what goes on ONE side of a barbell or ONE end of a dumbbell
    handle; for a kettlebell it is simply what goes in the shell. ``leftover``
    is what remains in the box after building this load.
    """

    total: float
    per_side: list = field(default_factory=list)
    leftover_plates: list = field(default_factory=list)
    # Plates consumed in total across the implement (4× per side for a dumbbell
    # pair, 2× for a barbell, 1× for a kettlebell). Used to pick the cheapest
    # recipe when two combinations land on the same total.
    plate_count: int = 0
    # True when plates stack in one place (adjustable kettlebell) rather than
    # being mirrored across two sides. The client needs it to know whether to
    # say "per side".
    stacked: bool = False

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "per_side": [dict(p) for p in self.per_side],
            "leftover_plates": [dict(p) for p in self.leftover_plates],
            "stacked": self.stacked,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Inventory normalisation
# ─────────────────────────────────────────────────────────────────────────────


def normalize_plates(raw) -> list:
    """Coerce a stored plate list into ``[{"weight": float, "count": int}, ...]``.

    Tolerant on read: legacy rows and hand-edited JSON may hold junk, and a
    profile that cannot be parsed should degrade to "no plates" rather than 500.
    Write-time rejection of bad input is the serializer's job.
    """
    out = {}
    for entry in raw or []:
        try:
            if isinstance(entry, dict):
                weight = float(entry.get("weight"))
                count = int(entry.get("count"))
            else:  # a bare denomination, count unknown → assume a usable pair set
                weight, count = float(entry), 4
        except (TypeError, ValueError):
            continue
        if weight <= 0 or count <= 0:
            continue
        weight = round(weight, _PRECISION)
        out[weight] = out.get(weight, 0) + count
    plates = [{"weight": w, "count": c} for w, c in out.items()]
    plates.sort(key=lambda p: p["weight"], reverse=True)
    if len(plates) > MAX_DENOMINATIONS:
        logger.warning(
            "loads: inventory truncated to %s denominations; dropped %s",
            MAX_DENOMINATIONS,
            [p["weight"] for p in plates[MAX_DENOMINATIONS:]],
        )
        plates = plates[:MAX_DENOMINATIONS]
    return plates


def normalize_weights(raw) -> list:
    """Coerce a fixed weight list into a sorted list of positive floats."""
    out = set()
    for value in raw or []:
        try:
            weight = float(value)
        except (TypeError, ValueError):
            continue
        if weight > 0:
            out.add(round(weight, _PRECISION))
    return sorted(out)


def orphan_plates(raw, per_step: int) -> list:
    """Plates that can never be used because the count doesn't divide evenly.

    A dumbbell pair consumes 4 at a time, so 6 × 2 kg leaves 2 unusable. Surfaced
    in the equipment form rather than hidden.
    """
    out = []
    for plate in normalize_plates(raw):
        remainder = plate["count"] % per_step
        if remainder:
            out.append({"weight": plate["weight"], "count": remainder})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Enumeration
# ─────────────────────────────────────────────────────────────────────────────


def _enumerate(plates: list, base: float, per_step: int, multiplier: int) -> list:
    """Every load assemblable from ``plates``.

    ``per_step`` is how many physical plates one "step" of a denomination costs
    (4 dumbbell / 2 barbell / 1 kettlebell); ``multiplier`` is how many times a
    step's weight counts toward the prescribed figure (2 for anything symmetric
    loaded per side, 1 for a kettlebell).

    Steps are enumerated heaviest-denomination-first so the first recipe reaching
    a given total is already the minimal-plate one — later ties are dropped.
    """
    usable = [
        {"weight": p["weight"], "steps": p["count"] // per_step}
        for p in plates
        if p["count"] // per_step > 0
    ]
    base = round(base, _PRECISION)
    # The bare bar / empty handle: always buildable, usually the lightest.
    stacked = multiplier == 1
    by_total = {
        base: Load(total=base, per_side=[], leftover_plates=_leftover(plates, {}, per_step),
                   plate_count=0, stacked=stacked)
    }
    if not usable:
        return list(by_total.values())

    truncated = False
    for combo in product(*[range(u["steps"] + 1) for u in usable]):
        if not any(combo):
            continue  # the empty combo is the bare bar, already recorded
        per_side = [
            {"weight": usable[i]["weight"], "count": n}
            for i, n in enumerate(combo)
            if n
        ]
        added = sum(p["weight"] * p["count"] for p in per_side)
        total = round(base + multiplier * added, _PRECISION)
        used = {p["weight"]: p["count"] for p in per_side}
        plate_count = sum(used.values()) * per_step
        existing = by_total.get(total)
        if existing is not None and existing.plate_count <= plate_count:
            continue  # keep the cheaper recipe for this total
        if existing is None and len(by_total) >= MAX_LOADS:
            truncated = True
            continue
        by_total[total] = Load(
            total=total,
            per_side=per_side,
            leftover_plates=_leftover(plates, used, per_step),
            plate_count=plate_count,
            stacked=stacked,
        )

    if truncated:
        logger.warning(
            "loads: enumeration capped at %s distinct loads; heavier combinations dropped",
            MAX_LOADS,
        )
    return sorted(by_total.values(), key=lambda l: l.total)


def _leftover(plates: list, used_per_side: dict, per_step: int) -> list:
    """What stays in the box after building a load."""
    out = []
    for plate in plates:
        remaining = plate["count"] - used_per_side.get(plate["weight"], 0) * per_step
        if remaining > 0:
            out.append({"weight": plate["weight"], "count": remaining})
    return out


def _fixed_loads(weights: list) -> list:
    """Fixed whole weights need no assembly — each is its own recipe."""
    return [Load(total=w, per_side=[], leftover_plates=[], plate_count=0)
            for w in normalize_weights(weights)]


# ─────────────────────────────────────────────────────────────────────────────
# Per-implement entry points
# ─────────────────────────────────────────────────────────────────────────────


def dumbbell_loads(profile) -> list:
    """Loads for a matched pair of dumbbells — the weight of ONE dumbbell."""
    if getattr(profile, "dumbbell_mode", "fixed") != "plates":
        return _fixed_loads(getattr(profile, "dumbbell_weights", None))
    return _enumerate(
        normalize_plates(getattr(profile, "dumbbell_plates", None)),
        base=float(getattr(profile, "dumbbell_handle_weight", 0) or 0),
        per_step=4,   # 2 handles × 2 sides
        multiplier=2,  # both sides of the one dumbbell we prescribe
    )


def barbell_loads(profile) -> list:
    """Loads for one bar. Falls back to the legacy fixed increment when the user
    has entered no plates, so existing profiles keep working."""
    plates = normalize_plates(getattr(profile, "barbell_plates", None))
    bar = float(getattr(profile, "bar_weight", 0) or 0)
    if not plates:
        inc = getattr(profile, "barbell_min_increment", None)
        if not inc:
            return []
        return [Load(total=round(inc * n, _PRECISION)) for n in range(1, 41)]
    return _enumerate(plates, base=bar, per_step=2, multiplier=2)


def kettlebell_loads(profile) -> list:
    """Loads for a kettlebell — fixed bells, or plates stacked in one shell."""
    if getattr(profile, "kettlebell_mode", "fixed") != "plates":
        return _fixed_loads(getattr(profile, "kettlebell_weights", None))
    return _enumerate(
        normalize_plates(getattr(profile, "kettlebell_plates", None)),
        base=float(getattr(profile, "kettlebell_handle_weight", 0) or 0),
        per_step=1,   # plates stack internally, no symmetry
        multiplier=1,
    )


def band_loads(profile) -> list:
    """Bands are discrete ordered levels; the index acts as the load value."""
    bands = list(getattr(profile, "band_levels", None) or [])
    return [Load(total=float(i)) for i in range(len(bands))]


# Deterministic priority when an exercise names more than one implement, or
# names none at all: heaviest/most specific first.
_IMPLEMENT_ORDER = (
    ("barbell", barbell_loads),
    ("kettlebell", kettlebell_loads),
    ("dumbbells", dumbbell_loads),
    ("bands", band_loads),
)


def _required_keys(exercise) -> set:
    """Equipment keys an exercise needs, tolerant of both model and stub shapes."""
    req = getattr(exercise, "required_equipment", None)
    if req is None:
        return set()
    if hasattr(req, "all"):  # Django M2M manager
        req = req.all()
    keys = set()
    for item in req:
        key = getattr(item, "key", item)
        if isinstance(key, str):
            keys.add(key)
    return keys


# When the exercise names no loadable gear there is nothing to match on, so we
# fall back to whatever the user actually stocks. This order is the legacy
# waterfall (dumbbells before bands before the bare barbell increment) — keeping
# it means profiles written before this feature prescribe exactly what they did.
_FALLBACK_ORDER = ("dumbbells", "kettlebell", "bands", "barbell")


def _has_inventory(profile, key) -> bool:
    """Whether the user has entered anything concrete for ``key``.

    The barbell's ``min_increment`` deliberately does not count: it is a
    last-resort ladder, not an inventory, and must not outrank real plates.
    """
    if key == "dumbbells":
        return bool(normalize_weights(getattr(profile, "dumbbell_weights", None))
                    or normalize_plates(getattr(profile, "dumbbell_plates", None)))
    if key == "kettlebell":
        return bool(normalize_weights(getattr(profile, "kettlebell_weights", None))
                    or normalize_plates(getattr(profile, "kettlebell_plates", None)))
    if key == "barbell":
        return bool(normalize_plates(getattr(profile, "barbell_plates", None)))
    if key == "bands":
        return bool(getattr(profile, "band_levels", None))
    return False


def implement_for(profile, exercise) -> Optional[str]:
    """Which implement this exercise's load should come from.

    The exercise's ``required_equipment`` decides, intersected with what the user
    owns — so a barbell movement never gets handed a dumbbell weight and a
    kettlebell swing never gets a barbell increment. Only when the exercise names
    no loadable gear at all do we fall back to their stock.

    Ownership gates the choice only when we can see it: a profile whose M2M is
    empty (or a plain stub) is treated as "unknown", not "owns nothing".
    """
    required = _required_keys(exercise)
    owned = _owned_keys(profile)

    def owns(key):
        return not owned or key in owned

    for key, _ in _IMPLEMENT_ORDER:
        if key in required and owns(key):
            return key
    for key in _FALLBACK_ORDER:
        if owns(key) and _has_inventory(profile, key):
            return key
    # Nothing concrete anywhere: the legacy fixed-increment ladder, if set.
    if owns("barbell") and getattr(profile, "barbell_min_increment", None):
        return "barbell"
    return None


def _owned_keys(profile) -> set:
    equipment = getattr(profile, "equipment", None)
    if equipment is None:
        return set()
    if hasattr(equipment, "all"):
        equipment = equipment.all()
    return {getattr(e, "key", e) for e in equipment}


def buildable_loads(profile, exercise) -> list:
    """Ascending loads the user can assemble for ``exercise``'s implement."""
    if profile is None:
        return []
    key = implement_for(profile, exercise)
    if key is None:
        return []
    builder = dict(_IMPLEMENT_ORDER)[key]
    return builder(profile)


def recipe_for(profile, exercise, total) -> Optional[Load]:
    """The recipe for an already-prescribed ``total``, or None if unbuildable.

    Matched on the rounded total so a stored float never misses its own recipe.
    """
    if total is None:
        return None
    try:
        target = round(float(total), _PRECISION)
    except (TypeError, ValueError):
        return None
    for load in buildable_loads(profile, exercise):
        if round(load.total, _PRECISION) == target:
            return load
    return None


def nearest_buildable(profile, exercise, total) -> Optional[float]:
    """Re-snap a load to the closest buildable one after an inventory change.

    Ties go to the lighter load — never silently prescribe more than before.
    """
    loads = buildable_loads(profile, exercise)
    if not loads:
        return None
    if total is None:
        return loads[0].total
    try:
        target = float(total)
    except (TypeError, ValueError):
        return loads[0].total
    return min(loads, key=lambda l: (abs(l.total - target), l.total)).total

"""The Forge adaptive engine — pure progression logic.

These functions contain NO Django/view/ORM logic: they take plain values or
attribute-bearing objects and return plain results, so they can be unit-tested
in isolation (no DB). The API layer is responsible for loading/saving models.

Evidence anchors (see plan): APRE-style autoregulated double progression — the
last working set is an AMRAP "test set" whose result dictates the next target.
Two modes: ``difficulty`` (climb the movement ladder) for pure bodyweight, and
``load`` (smallest available increment) when loadable equipment is owned, which
also fixes the well-documented difficulty of overloading the lower body with
bodyweight alone.
"""

from dataclasses import dataclass
from typing import Optional, Sequence

# How many consecutive top-of-range sessions before a difficulty-mode exercise
# advances to the next (harder) rung.
SESSIONS_TO_ADVANCE = 2


@dataclass
class Prescription:
    """Plain result of a progression decision."""

    exercise: object  # Exercise (or stand-in with .difficulty_rank etc.)
    target_sets: int
    target_reps_min: int
    target_reps_max: int
    target_load: Optional[float]
    sessions_at_top: int
    message: str
    # True only when this result represents an EARNED difficulty advance to a
    # harder rung. The orchestrator uses this to gate the climb behind a user
    # Accept/Deny instead of applying it automatically.
    advanced: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Assessment placement
# ─────────────────────────────────────────────────────────────────────────────


def place_from_assessment(ladder: Sequence, reps_or_seconds: int):
    """Place a user on a ladder from an objective AMRAP score.

    ``ladder`` is the equipment-eligible exercises for one pattern, each with
    ``.difficulty_rank`` and ``.placement_threshold``. Returns the hardest rung
    whose threshold the user met; falls back to the easiest rung.
    """
    ordered = sorted(ladder, key=lambda e: e.difficulty_rank)
    if not ordered:
        return None
    placed = ordered[0]
    for ex in ordered:
        if reps_or_seconds >= ex.placement_threshold:
            placed = ex
        else:
            break
    return placed


# ─────────────────────────────────────────────────────────────────────────────
# Substitution (blocked exercises)
# ─────────────────────────────────────────────────────────────────────────────


def find_substitute(blocked, candidates: Sequence):
    """Closest stand-in for a blocked exercise from an eligible candidate ladder.

    ``candidates`` is the set of exercises in the SAME pattern the user is allowed
    to do (equipment-eligible, not blocked). We prefer the nearest rung that is
    equal-or-easier than the blocked one (safer when the block is an injury/
    limitation); if nothing is easier, fall back to the nearest harder rung.
    Returns ``None`` when no candidate remains.
    """
    pool = [c for c in candidates if c.difficulty_rank is not None and c is not blocked]
    if not pool:
        return None
    rank = blocked.difficulty_rank
    easier = [c for c in pool if c.difficulty_rank <= rank]
    if easier:
        # nearest from below (largest rank that is still <= blocked)
        return max(easier, key=lambda c: (c.difficulty_rank, -abs(c.difficulty_rank - rank)))
    # nothing equal-or-easier: take the nearest harder rung
    return min(pool, key=lambda c: c.difficulty_rank)


# ─────────────────────────────────────────────────────────────────────────────
# Load helpers
# ─────────────────────────────────────────────────────────────────────────────


def available_loads(equipment_profile, exercise) -> list:
    """Ordered list of selectable loads for a load-mode exercise, given what the
    user owns. Returns [] when the exercise isn't load-progressable for this user.
    """
    weights = list(equipment_profile.dumbbell_weights or [])
    if weights:
        return sorted(set(float(w) for w in weights))
    bands = list(equipment_profile.band_levels or [])
    if bands:
        # Bands are discrete ordered levels; index acts as the load value.
        return [float(i) for i in range(len(bands))]
    inc = getattr(equipment_profile, "barbell_min_increment", None)
    if inc:
        return [round(inc * n, 2) for n in range(1, 41)]  # up to 40 increments
    return []


def next_load_up(equipment_profile, exercise, current_load: Optional[float]) -> Optional[float]:
    """Smallest available load strictly greater than ``current_load`` (or the
    lowest available load if current is None)."""
    loads = available_loads(equipment_profile, exercise)
    if not loads:
        return None
    if current_load is None:
        return loads[0]
    for load in loads:
        if load > current_load:
            return load
    return current_load  # already at the top available load


# ─────────────────────────────────────────────────────────────────────────────
# Per-exercise progression after a session (APRE-style double progression)
# ─────────────────────────────────────────────────────────────────────────────


def next_prescription(prescribed, amrap_reps: int, equipment_profile) -> Prescription:
    """Compute the next prescription for one exercise from its AMRAP test set.

    ``prescribed`` carries the current targets (.exercise, .target_sets,
    .target_reps_min/max, .target_load, .sessions_at_top) and ``amrap_reps`` is
    the actual reps achieved on the last (AMRAP) working set.
    """
    ex = prescribed.exercise
    rmin = prescribed.target_reps_min
    rmax = prescribed.target_reps_max
    sets = prescribed.target_sets
    load = prescribed.target_load
    at_top = prescribed.sessions_at_top
    is_load_mode = ex.progression_mode == "load"

    # ── Underperformed: below the bottom of the range → de-load. ──────────────
    if amrap_reps < rmin:
        if is_load_mode and load:
            loads = available_loads(equipment_profile, ex)
            lower = [l for l in loads if l < load]
            new_load = lower[-1] if lower else load
            msg = (
                f"De-load: reduced to {new_load}{_unit(equipment_profile)}"
                if lower
                else "Held load; dropped a set to recover."
            )
            return Prescription(ex, max(2, sets - (0 if lower else 1)), rmin, rmax, new_load, 0, msg)
        # difficulty mode: regress one rung if we can, else drop a set
        if ex.regression is not None:
            reg = ex.regression
            return Prescription(
                reg, sets, reg.rep_range_min, reg.rep_range_max, None, 0,
                f"De-load: regressed to {reg.name}.",
            )
        return Prescription(ex, max(2, sets - 1), rmin, rmax, load, 0, "De-load: dropped a set.")

    # ── Hit/exceeded top of range → progress. ─────────────────────────────────
    if amrap_reps >= rmax:
        if is_load_mode:
            new_load = next_load_up(equipment_profile, ex, load)
            if new_load is not None and (load is None or new_load > load):
                return Prescription(
                    ex, sets, rmin, rmax, new_load, 0,
                    f"+load → {new_load}{_unit(equipment_profile)} (reps reset to {rmin}).",
                )
            # No heavier load available: progress reps within an extended range.
            return Prescription(ex, sets, rmin, rmax + 1, load, 0, "Max load reached; +1 target rep.")
        # difficulty mode: advance only after sustained top performance
        new_at_top = at_top + 1
        if new_at_top >= SESSIONS_TO_ADVANCE and ex.progression is not None:
            prog = ex.progression
            return Prescription(
                prog, sets, prog.rep_range_min, prog.rep_range_max, None, 0,
                f"Advanced to {prog.name}!", advanced=True,
            )
        return Prescription(
            ex, sets, rmin, rmax, None, new_at_top,
            f"Top of range ({new_at_top}/{SESSIONS_TO_ADVANCE}); hold to advance.",
        )

    # ── Within range → hold, nudge volume toward the weekly target. ──────────
    return Prescription(ex, sets, rmin, rmax, load, 0, "On track — holding.")


def _unit(equipment_profile) -> str:
    unit = getattr(equipment_profile, "load_unit", "kg")
    return "" if unit in ("none", "band_level") else f" {unit}"

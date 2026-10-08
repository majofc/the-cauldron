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

from the_cauldron.services import loads

# How many consecutive top-of-range sessions before a difficulty-mode exercise
# advances to the next (harder) rung.
SESSIONS_TO_ADVANCE = 2

# Smallest load jump worth prescribing: 2.5% of the current load, never under a
# floor in the profile's unit. Mixed plates otherwise make the "next" buildable
# total a few grams heavier (5.44 → 5.50 kg) and the programme never moves.
LOAD_STEP_FRACTION = 0.025
MIN_LOAD_STEP = {"kg": 1.0, "lb": 2.5}

# A top-of-ladder rung (nothing harder the user can perform) keeps extending its
# rep target. Once the AMRAP reaches this, every top-of-range session re-checks
# for a performable harder rung — e.g. the weighted rung after dumbbells are added.
TOP_OF_LADDER_REPS = 25

# Timed holds grow their target instead of advancing at the seeded max: the new
# max is ``max(rmax + TIMED_STEP_SECONDS, round(TIMED_ACHIEVED_RATIO × achieved))``
# up to TIMED_CAP_SECONDS, and only a hold at the cap counts as topped out.
TIMED_STEP_SECONDS = 5
TIMED_ACHIEVED_RATIO = 0.9
TIMED_CAP_SECONDS = 120

# Sentinel: ``next_prescription`` was not told the next rung, so it follows the
# exercise's own ``progression`` link (the pure, no-equipment view).
_LINKED = object()


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
# Per-side rep parity
# ─────────────────────────────────────────────────────────────────────────────


def even_up(n: int) -> int:
    """Round ``n`` UP to the nearest even integer (3 → 4, 4 → 4)."""
    return n if n % 2 == 0 else n + 1


def rep_targets_for(exercise, rmin: int, rmax: int) -> tuple:
    """The rep targets to store for ``exercise``, as ``(min, max)``.

    Per-side movements are performed one side at a time, so an odd target would
    hand one side an extra rep — both ends are rounded UP to even. Timed holds
    are exempt: their values are seconds, not reps. Every path that writes
    ``PrescribedExercise.target_reps_*`` (generation, substitution, progression)
    goes through here so the parity rule lives in one place.
    """
    if not getattr(exercise, "is_per_side", False) or exercise.is_timed:
        return rmin, rmax
    return even_up(rmin), even_up(rmax)


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

    Every value here is a load the user can physically assemble from the plates
    they declared — ``services.loads`` owns that arithmetic, including which
    implement ``exercise`` calls for. This returns only the totals; the recipe
    for a chosen load is fetched separately via ``loads.recipe_for`` so the
    progression maths below stays plain float comparison.

    A total of 0 — the bare implement when the user's handle, bar or shell weighs
    nothing — stays a valid recipe but is never prescribable, so it is dropped
    here. Band levels are indices, where 0 is the lightest real band, so they
    keep it.
    """
    totals = [l.total for l in loads.buildable_loads(equipment_profile, exercise)]
    if loads.implement_for(equipment_profile, exercise) == "bands":
        return totals
    return [t for t in totals if t > 0]


def _easiest_first(exercise, options: list) -> list:
    """Loads ordered from easiest to hardest. Ascending, except for an assisted
    movement, where the heaviest band is the most help and so the easiest."""
    return list(reversed(options)) if getattr(exercise, "is_assisted", False) else options


def nearest_available_load(equipment_profile, exercise, target) -> Optional[float]:
    """The prescribable load closest to ``target`` (ties go lighter), or the
    easiest one when ``target`` is None — the lightest weight, or the heaviest
    band for an assisted movement. ``None`` when nothing is prescribable."""
    options = available_loads(equipment_profile, exercise)
    if not options:
        return None
    if target is None:
        return _easiest_first(exercise, options)[0]
    return min(options, key=lambda l: (abs(l - target), l))


# Trial-seeded starting load: one buildable step up from the lightest load for
# every this-much the Trial score clears the rung's placement threshold.
TRIAL_STEP_FRACTION = 0.2


def trial_seeded_load(equipment_profile, exercise, trial_score) -> Optional[float]:
    """Starting load for a user with no load history on ``exercise``, from the
    latest Trial score for its pattern.

    The score and ``placement_threshold`` share a scale (the pattern's anchor),
    so the margin by which the score clears the threshold says how comfortably
    the user placed here. Each ``TRIAL_STEP_FRACTION`` of margin moves one
    prescribable load up from the lightest, never past the middle of the user's
    range — a first session should not open near their heaviest load. With no
    usable score (none recorded, or a threshold of 0 to measure against) this is
    the lightest prescribable load. ``None`` when nothing is prescribable.
    """
    options = _easiest_first(exercise, available_loads(equipment_profile, exercise))
    if not options:
        return None
    threshold = exercise.placement_threshold or 0
    if trial_score is None or threshold <= 0 or trial_score <= threshold:
        return options[0]
    steps = int((trial_score - threshold) / threshold / TRIAL_STEP_FRACTION)
    return options[min(steps, (len(options) - 1) // 2)]


def load_step(equipment_profile, current_load: float) -> float:
    """The minimum jump from ``current_load``: ``LOAD_STEP_FRACTION`` of it, never
    under the unit floor (1 kg, or 2.5 lb on an ``lb`` profile)."""
    unit = getattr(equipment_profile, "load_unit", "kg")
    floor = MIN_LOAD_STEP.get(unit, MIN_LOAD_STEP["kg"])
    return max(LOAD_STEP_FRACTION * current_load, floor)


def _is_band(equipment_profile, exercise) -> bool:
    """Band loads are level indices, not weights — the step rule doesn't apply."""
    return loads.implement_for(equipment_profile, exercise) == "bands"


def next_load_up(equipment_profile, exercise, current_load: Optional[float]) -> Optional[float]:
    """The next prescribable load above ``current_load``.

    The first buildable total at least ``load_step`` heavier; when nothing clears
    the step (fixed dumbbells 10 → 10.5), the next heavier load anyway, so a
    sparse inventory never strands the user. Bands simply move one level. The
    lowest available load when ``current_load`` is None, and ``current_load``
    itself when it is already the heaviest.
    """
    options = available_loads(equipment_profile, exercise)
    if not options:
        return None
    if current_load is None:
        return options[0]
    heavier = [l for l in options if l > current_load]
    if not heavier:
        return current_load  # already at the top available load
    if _is_band(equipment_profile, exercise):
        return heavier[0]
    floor = round(current_load + load_step(equipment_profile, current_load), 2)
    for load in heavier:
        if round(load, 2) >= floor:
            return load
    return heavier[0]


def next_load_down(equipment_profile, exercise, current_load: Optional[float]) -> Optional[float]:
    """Mirror of ``next_load_up``: the heaviest buildable load at least
    ``load_step`` lighter, else the next lighter one. ``None`` when nothing is
    lighter (or ``current_load`` is None)."""
    if current_load is None:
        return None
    lighter = [l for l in available_loads(equipment_profile, exercise) if l < current_load]
    if not lighter:
        return None
    if _is_band(equipment_profile, exercise):
        return lighter[-1]
    ceiling = round(current_load - load_step(equipment_profile, current_load), 2)
    for load in reversed(lighter):
        if round(load, 2) <= ceiling:
            return load
    return lighter[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Per-exercise progression after a session (APRE-style double progression)
# ─────────────────────────────────────────────────────────────────────────────


def _seeded_rmax(exercise) -> int:
    """The rung's own rep-range max as a prescription stores it (per-side parity
    applied). A prescription above it has been extended at the top."""
    return rep_targets_for(exercise, exercise.rep_range_min, exercise.rep_range_max)[1]


def next_prescription(prescribed, amrap_reps: int, equipment_profile, next_rung=_LINKED) -> Prescription:
    """Compute the next prescription for one exercise from its AMRAP test set.

    ``prescribed`` carries the current targets (.exercise, .target_sets,
    .target_reps_min/max, .target_load, .sessions_at_top) and ``amrap_reps`` is
    the actual reps achieved on the last (AMRAP) working set.

    ``next_rung`` is the harder rung the user can move to, or ``None`` when there
    is nothing harder they can perform — the top of *their* ladder. The
    orchestrator resolves it against equipment and blocks; left out, the
    exercise's own ``progression`` link is used.

    Assisted exercises (``is_assisted``: band-assisted rows/pull-ups) run the band
    index backwards — a lighter band is less help, so progress lowers the index
    and the lightest band at the top of the range earns the next rung.
    """
    ex = prescribed.exercise
    rmin = prescribed.target_reps_min
    rmax = prescribed.target_reps_max
    sets = prescribed.target_sets
    load = prescribed.target_load
    at_top = prescribed.sessions_at_top
    is_load_mode = ex.progression_mode == "load"
    is_timed = getattr(ex, "is_timed", False)
    assisted = getattr(ex, "is_assisted", False)
    if next_rung is _LINKED:
        next_rung = ex.progression

    def advance_to(rung):
        return Prescription(
            rung, sets, rung.rep_range_min, rung.rep_range_max, None, 0,
            f"Advanced to {rung.name}!", advanced=True,
        )

    # ── Underperformed: below the bottom of the range → de-load. ──────────────
    # ``load is not None`` rather than truthiness: band level 0 is a real load.
    if amrap_reps < rmin:
        if is_load_mode and load is not None:
            # An assisted movement de-loads by adding help: a heavier band.
            if assisted:
                easier = next_load_up(equipment_profile, ex, load)
                if easier is not None and easier <= load:
                    easier = None
            else:
                easier = next_load_down(equipment_profile, ex, load)
            if easier is not None:
                return Prescription(
                    ex, max(2, sets), rmin, rmax, easier, 0,
                    f"De-load: reduced to {easier}{_unit(equipment_profile)}",
                )
            return Prescription(
                ex, max(2, sets - 1), rmin, rmax, load, 0, "Held load; dropped a set to recover."
            )
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
        if is_load_mode and assisted:
            lighter = next_load_down(equipment_profile, ex, load)
            if lighter is not None:
                return Prescription(
                    ex, sets, rmin, rmax, lighter, 0,
                    f"Less assistance → band {lighter:g} (reps reset to {rmin}).",
                )
            if load is not None and next_rung is not None:
                return advance_to(next_rung)
            return Prescription(ex, sets, rmin, rmax + 1, load, 0, "Lightest band reached; +1 target rep.")
        if is_load_mode:
            new_load = next_load_up(equipment_profile, ex, load)
            if new_load is not None and (load is None or new_load > load):
                return Prescription(
                    ex, sets, rmin, rmax, new_load, 0,
                    f"+load → {new_load}{_unit(equipment_profile)} (reps reset to {rmin}).",
                )
            # No heavier load available: progress reps within an extended range.
            return Prescription(ex, sets, rmin, rmax + 1, load, 0, "Max load reached; +1 target rep.")

        # Timed holds grow their target until the cap; only a capped hold is at
        # the top of its range for advancing.
        if is_timed and rmax < TIMED_CAP_SECONDS:
            new_rmax = min(
                TIMED_CAP_SECONDS,
                max(rmax + TIMED_STEP_SECONDS, round(TIMED_ACHIEVED_RATIO * amrap_reps)),
            )
            return Prescription(ex, sets, rmin, new_rmax, None, 0, f"Hold target → {new_rmax}s.")

        # difficulty mode: advance only after sustained top performance — or, on
        # a rung already extended past its range, as soon as the AMRAP shows the
        # user has long outgrown it and something harder has become performable.
        new_at_top = min(at_top + 1, SESSIONS_TO_ADVANCE)
        # Reps only: a capped hold's seconds would always clear 25.
        outgrown = not is_timed and rmax > _seeded_rmax(ex) and amrap_reps >= TOP_OF_LADDER_REPS
        if next_rung is not None and (new_at_top >= SESSIONS_TO_ADVANCE or outgrown):
            return advance_to(next_rung)
        if next_rung is None and new_at_top >= SESSIONS_TO_ADVANCE and not is_timed:
            # Top of the user's ladder: nowhere to climb, so the range grows. No cap.
            return Prescription(ex, sets, rmin, rmax + 1, None, 0, "Top of the ladder; +1 target rep.")
        # A capped hold with nowhere to go keeps its counter full, so the first
        # session after a harder rung becomes performable unlocks it.
        return Prescription(
            ex, sets, rmin, rmax, None, new_at_top,
            f"Top of range ({new_at_top}/{SESSIONS_TO_ADVANCE}); hold to advance.",
        )

    # ── Within range → hold, nudge volume toward the weekly target. ──────────
    return Prescription(ex, sets, rmin, rmax, load, 0, "On track — holding.")


def _unit(equipment_profile) -> str:
    unit = getattr(equipment_profile, "load_unit", "kg")
    return "" if unit in ("none", "band_level") else f" {unit}"

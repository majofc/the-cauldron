"""Forge orchestration — ties the pure progression engine to the ORM.

View/API code calls into here; the pure decision logic stays in
``progression.py``. This module owns DB reads/writes (program generation,
session snapshotting, applying a logged session).
"""

import random
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    BlockedExercise,
    Exercise,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    UserEquipmentProfile,
    WorkoutSession,
)
from the_cauldron.services import norms, progression

# Which patterns go on which day for each split.
SPLIT_LAYOUTS = {
    Program.Split.FULL_BODY_3X: {
        "days": 3,
        "day_patterns": lambda i, all_keys: all_keys,  # every pattern every day
        "names": ["Full Body A", "Full Body B", "Full Body C"],
    },
    Program.Split.UPPER_LOWER_4X: {
        "days": 4,
        "names": ["Upper A", "Lower A", "Upper B", "Lower B"],
    },
}

UPPER_PATTERNS = {"horizontal_push", "vertical_pull", "vertical_push", "core_anti_extension"}
LOWER_PATTERNS = {"lower_unilateral", "hinge", "core_anti_extension"}

# "Full Body A" (the first day of a Full Body ×3 split) biases toward the
# movements the user has seen least often lately: it trains only this many
# patterns — the ones whose progression chains have the fewest recent usage
# events — instead of every pattern. Days B/C are unchanged.
FULL_BODY_A_PATTERN_COUNT = 5
# Trailing window for counting Full Body A usage events per progression chain.
CHAIN_EVENT_WINDOW_DAYS = 14
# Window used when comparing recent AMRAP work (grip selection).
LEAST_TRAINED_WINDOW_DAYS = 30
# Trailing window for comparing the two grips of a split bar-pull-up rung: the
# weaker grip is the one with the lower most-recent AMRAP in this many days.
GRIP_WINDOW_DAYS = 30

# The Trial is a recurring measurement: nudge a retest once the last COMPLETED
# assessment is this old. Completed, not merely opened — an abandoned retake
# must not reset the clock.
RETEST_INTERVAL_DAYS = 30
# How long dismissing the nudge suppresses it before it returns (if still due).
RETEST_DISMISS_DAYS = 3


def get_or_create_equipment_profile(user) -> UserEquipmentProfile:
    profile, _ = UserEquipmentProfile.objects.get_or_create(user=user)
    return profile


def _peer_score_dict(score) -> dict:
    """Serialise a norms.PeerScore for an API response."""
    if not score.has_data:
        return {"has_data": False, "reason": score.reason, "confidence": score.confidence}
    return {
        "has_data": True,
        "flames": score.flames,
        "decile": score.decile,
        "percentile": score.percentile,
        "label": score.label,
        "confidence": score.confidence,
        "approximate": score.approximate,
        "estimated": score.estimated,
        "source": score.source,
        "url": score.url,
        "note": score.note,
    }


def peer_score(user, exercise_name, value) -> dict:
    """Peer "flames" score for a result, using the user's age+sex and published
    norms. Always returns a dict (``has_data`` False when not scoreable)."""
    profile = get_or_create_equipment_profile(user)
    age = (timezone.now().year - profile.birth_year) if profile.birth_year else None
    return _peer_score_dict(norms.score(exercise_name, value, profile.sex, age))


def best_flames_by_exercise(user) -> dict:
    """Highest "fires" (peer flames, 1-10) the user has ever earned on each
    exercise, keyed by exercise name.

    Flames rise monotonically with the AMRAP value, so the best value the user
    has ever logged for a movement yields the best flames. Sources are every
    AMRAP working set and every Trial result. Only movements with a published
    peer norm and at least one recorded result that scores appear in the result.
    """
    profile = get_or_create_equipment_profile(user)
    age = (timezone.now().year - profile.birth_year) if profile.birth_year else None

    best_value = {}  # exercise name -> best AMRAP reps/seconds ever

    def consider(name, value):
        if value is None or name not in norms.EXERCISE_NORMS:
            return
        if value > best_value.get(name, -1):
            best_value[name] = value

    for sl in SetLog.objects.filter(
        session__user=user, is_amrap=True, actual_reps__isnull=False
    ).select_related("exercise"):
        consider(sl.exercise.name, sl.actual_reps)

    for r in AssessmentResult.objects.filter(session__user=user).select_related(
        "tested_exercise"
    ):
        consider(r.tested_exercise.name, r.reps_or_seconds)

    result = {}
    for name, value in best_value.items():
        sc = norms.score(name, value, profile.sex, age)
        if sc.has_data:
            result[name] = {
                "flames": sc.flames,
                "value": value,
                "estimated": sc.estimated,
            }
    return result


def peer_decile_cutoffs(user, exercise_name) -> dict:
    """Decile reference values (P10..P90) for charting this exercise against
    peers. ``{has_data, cutoffs, label, source, url}``."""
    profile = get_or_create_equipment_profile(user)
    age = (timezone.now().year - profile.birth_year) if profile.birth_year else None
    cutoffs = norms.decile_cutoffs(exercise_name, profile.sex, age)
    if cutoffs is None:
        return {"has_data": False}
    meta = norms.NORMS[norms.EXERCISE_NORMS[exercise_name]]
    return {
        "has_data": True,
        "cutoffs": cutoffs,
        "label": meta["label"],
        "metric": meta["metric"],
        "source": meta["source"],
        "url": meta["url"],
        "approximate": True,
    }


def owned_equipment_keys(profile) -> set:
    """Equipment keys the user owns. Bodyweight is implicitly always available."""
    return set(profile.equipment.values_list("key", flat=True)) | {"bodyweight"}


def owned_equipment_keys_for(user) -> set:
    """``owned_equipment_keys`` for a user, without creating a profile row — read
    paths must not write. A user with no profile yet has bodyweight only."""
    profile = UserEquipmentProfile.objects.filter(user=user).first()
    return owned_equipment_keys(profile) if profile else {"bodyweight"}


def is_performable(exercise, owned, blocked=()) -> bool:
    """True when every piece of ``exercise``'s required equipment is in ``owned``
    and it is not among ``blocked``. Reads ``required_equipment`` through the
    prefetch cache, so callers should prefetch it when checking in bulk."""
    if exercise.pk in blocked:
        return False
    req = {e.key for e in exercise.required_equipment.all()} or {"bodyweight"}
    return req <= owned


def eligible_exercises(pattern, profile, exclude_ids=None, owned=None):
    """Exercises for a pattern whose required equipment the user owns.

    ``exclude_ids`` (e.g. the user's blocked exercises) are filtered out. Pass
    ``owned`` to reuse a set already computed for this user.
    """
    if owned is None:
        owned = owned_equipment_keys(profile)
    exclude_ids = set(exclude_ids or ())
    return [
        ex
        for ex in pattern.exercises.prefetch_related("required_equipment")
        if is_performable(ex, owned, exclude_ids)
    ]


def blocked_exercise_ids(user) -> set:
    """UUIDs of the exercises this user has blocked."""
    return set(
        BlockedExercise.objects.filter(user=user).values_list("exercise_id", flat=True)
    )


def substitute_for(user, exercise):
    """Closest equal-or-easier stand-in for ``exercise`` in the same pattern that
    is equipment-eligible for ``user`` and not itself blocked. ``None`` if none."""
    profile = get_or_create_equipment_profile(user)
    blocked = blocked_exercise_ids(user)
    candidates = eligible_exercises(exercise.pattern, profile, exclude_ids=blocked)
    return progression.find_substitute(exercise, candidates)


def grip_siblings(exercise):
    """The grip variants sharing ``exercise``'s ladder position (both overhand and
    underhand rows at the same pattern + rank), or ``[]`` if ``exercise`` is not a
    grip-split rung."""
    if exercise.grip == Exercise.Grip.NA:
        return []
    return list(
        Exercise.objects.filter(
            pattern_id=exercise.pattern_id,
            difficulty_rank=exercise.difficulty_rank,
        ).exclude(grip=Exercise.Grip.NA)
    )


def _recent_amrap_by_exercise(user, exercises, cutoff) -> dict:
    """``{exercise_id: most_recent actual_reps}`` for each of ``exercises`` within
    the window — a single query over the whole set (no per-variant N+1)."""
    if not exercises:
        return {}
    rows = (
        SetLog.objects.filter(
            session__user=user,
            exercise__in=exercises,
            is_amrap=True,
            actual_reps__isnull=False,
            session__performed_at__gte=cutoff,
        )
        .order_by("exercise_id", "-session__performed_at")
        .values_list("exercise_id", "actual_reps")
    )
    most_recent = {}
    for exercise_id, reps in rows:
        # Ordered newest-first per exercise → first row seen is the most recent.
        most_recent.setdefault(exercise_id, reps)
    return most_recent


def select_grip_variant(user, exercise):
    """The grip variant of ``exercise`` to train today — the *weaker* grip.

    Bar pull-up rungs exist as two ``Exercise`` rows at the same
    ``difficulty_rank`` (overhand + underhand). Given either variant, return the
    one whose most-recent AMRAP set in the trailing ``GRIP_WINDOW_DAYS`` has the
    lower ``actual_reps`` (bilateral, so ``actual_reps`` is the score). Rules:

    - A variant with no AMRAP set in the window is treated as the weaker (a
      neglected grip → prioritised).
    - Tie, or neither variant has history → choose randomly.
    - A blocked or equipment-ineligible variant is never returned; the other is
      used regardless of reps. If *both* are unavailable, fall back to a
      substitute (mirrors ``block_exercise``) rather than a forbidden movement.

    Returns ``exercise`` unchanged if it is not a grip-split rung.
    """
    variants = grip_siblings(exercise)
    if len(variants) < 2:
        return exercise  # not a grip-split rung, or missing a sibling

    profile = get_or_create_equipment_profile(user)
    eligible = {e.pk for e in eligible_exercises(exercise.pattern, profile)}
    blocked = blocked_exercise_ids(user)
    candidates = [v for v in variants if v.pk in eligible and v.pk not in blocked]
    if not candidates:
        # Every grip blocked or unusable — don't prescribe a forbidden movement.
        return substitute_for(user, exercise) or exercise
    if len(candidates) == 1:
        return candidates[0]  # the other grip is unavailable — forced, ignore reps

    cutoff = timezone.now() - timedelta(days=GRIP_WINDOW_DAYS)
    recent = _recent_amrap_by_exercise(user, candidates, cutoff)

    # No history in the window → weakest. If any variant is untrained, pick among
    # those (random when more than one is untrained).
    untrained = [v for v in candidates if v.pk not in recent]
    if untrained:
        return random.choice(untrained)
    lowest = min(recent[v.pk] for v in candidates)
    weakest = [v for v in candidates if recent[v.pk] == lowest]
    return random.choice(weakest)


def effective_progression_reps(user, trained_exercise, logged_reps):
    """Reps that should drive the shared rung's difficulty progression.

    A grip-split rung is one ladder position trained by two grips. Because the
    Forge deliberately schedules the *weaker* grip, a weak-grip AMRAP must not
    regress or stall the rung — the rung reflects the user's demonstrated pulling
    ability, i.e. the *stronger* grip. So return the best most-recent AMRAP across
    both grips in the window (``logged_reps`` is already persisted, so it is
    included). Non-split movements return ``logged_reps`` unchanged.
    """
    variants = grip_siblings(trained_exercise)
    if len(variants) < 2:
        return logged_reps
    cutoff = timezone.now() - timedelta(days=GRIP_WINDOW_DAYS)
    recent = _recent_amrap_by_exercise(user, variants, cutoff)
    return max([logged_reps, *recent.values()]) if recent else logged_reps


def _repoint_prescription(presc, exercise, profile) -> None:
    """Move ``presc`` onto ``exercise``, resetting the rung-dependent fields."""
    presc.exercise = exercise
    presc.target_reps_min, presc.target_reps_max = progression.rep_targets_for(
        exercise, exercise.rep_range_min, exercise.rep_range_max
    )
    presc.target_load = _initial_load(profile, exercise)
    presc.target_rest_seconds = exercise.rest_seconds
    presc.sessions_at_top = 0
    presc.save()


def _live_prescriptions(user):
    """Prescriptions on the user's active program, ready for a performability
    pass (equipment prefetched, so the scan costs a fixed number of queries)."""
    return PrescribedExercise.objects.filter(
        day__program__user=user, day__program__is_active=True
    ).select_related("exercise__pattern", "pending_progression__pattern").prefetch_related(
        "exercise__required_equipment", "pending_progression__required_equipment"
    )


def unperformable_prescription_ids(user) -> set:
    """PKs of active-program prescriptions the user cannot perform.

    Normally empty: ``sync_program_equipment`` swaps such a prescription for a
    stand-in as soon as the equipment changes. It stays non-empty only when *no*
    rung of that pattern is reachable at all, leaving nothing to swap in. Read
    paths hide those rather than delete them, so the day trains one fewer pattern
    and the movement returns on its own if the equipment does.
    """
    owned = owned_equipment_keys_for(user)
    blocked = blocked_exercise_ids(user)
    return {
        p.pk for p in _live_prescriptions(user) if not is_performable(p.exercise, owned, blocked)
    }


@transaction.atomic
def sync_program_equipment(user) -> dict:
    """Re-point live prescriptions the user can no longer perform.

    Equipment can change *after* a program was generated — the user edits their
    profile, or staff edits it in the admin — which would otherwise leave the
    program prescribing a movement whose gear is gone (e.g. a Rowing Machine row
    after the machine was removed). Each prescription that is no longer
    performable is swapped to the closest eligible stand-in, and a parked
    ``pending_progression`` that became ineligible is cleared. Where no rung of
    the pattern is reachable there is nothing to swap in, so the row is left for
    the read paths to hide.

    Idempotent — a program already consistent with the profile is untouched.
    Returns ``{"swapped": [{"from", "to"}, ...], "hidden": {prescription pk}}``,
    ``to`` being ``None`` for the rows that had no stand-in.
    """
    profile = get_or_create_equipment_profile(user)
    owned = owned_equipment_keys(profile)
    blocked = blocked_exercise_ids(user)

    candidates_by_pattern = {}

    def candidates(pattern):
        if pattern.pk not in candidates_by_pattern:
            candidates_by_pattern[pattern.pk] = eligible_exercises(
                pattern, profile, exclude_ids=blocked, owned=owned
            )
        return candidates_by_pattern[pattern.pk]

    swapped, hidden = [], set()
    for presc in _live_prescriptions(user):
        pending = presc.pending_progression
        clear_pending = pending is not None and not is_performable(pending, owned, blocked)
        if clear_pending:
            presc.pending_progression = None
        if is_performable(presc.exercise, owned, blocked):
            if clear_pending:
                presc.save(update_fields=["pending_progression"])
            continue
        substitute = progression.find_substitute(
            presc.exercise, candidates(presc.exercise.pattern)
        )
        swapped.append(
            {"from": presc.exercise.name, "to": substitute.name if substitute else None}
        )
        if substitute is None:
            hidden.add(presc.pk)
            if clear_pending:
                presc.save(update_fields=["pending_progression"])
        else:
            # The full save also persists a cleared pending_progression.
            _repoint_prescription(presc, substitute, profile)
    return {"swapped": swapped, "hidden": hidden}


@transaction.atomic
def block_exercise(user, exercise, reason="") -> dict:
    """Block ``exercise`` for ``user`` and swap any live prescription using it to
    a substitute. Returns ``{"blocked": <uuid>, "substitute": <Exercise|None>,
    "swapped": <int>}``."""
    BlockedExercise.objects.update_or_create(
        user=user, exercise=exercise, defaults={"reason": reason}
    )
    substitute = substitute_for(user, exercise)
    swapped = 0
    profile = get_or_create_equipment_profile(user)
    prescriptions = PrescribedExercise.objects.filter(
        day__program__user=user, day__program__is_active=True, exercise=exercise
    )
    for presc in prescriptions:
        if substitute is None:
            continue
        _repoint_prescription(presc, substitute, profile)
        swapped += 1
    return {"blocked": exercise.pk, "substitute": substitute, "swapped": swapped}


def unblock_exercise(user, exercise) -> None:
    """Remove a block. Live prescriptions are left as-is (the substitute stays;
    the user can retake the Trial or progress back naturally)."""
    BlockedExercise.objects.filter(user=user, exercise=exercise).delete()


def _initial_load(profile, exercise):
    if exercise.progression_mode != Exercise.ProgressionMode.LOAD:
        return None
    return progression.next_load_up(profile, exercise, None)


@transaction.atomic
def generate_program(user, assessment: AssessmentSession, split=None) -> Program:
    """Create a fresh active program from an assessment's placements."""
    profile = get_or_create_equipment_profile(user)
    split = split or Program.Split.FULL_BODY_3X

    # Deactivate any previous active program.
    Program.objects.filter(user=user, is_active=True).update(is_active=False)

    program = Program.objects.create(
        user=user, source_assessment=assessment, split=split, weekly_volume_target=8
    )

    # placement per pattern key -> placed Exercise
    placements = {
        r.pattern.key: (r.placed_exercise or r.tested_exercise)
        for r in assessment.results.select_related("pattern", "placed_exercise", "tested_exercise")
    }

    layout = SPLIT_LAYOUTS[split]
    for day_index in range(layout["days"]):
        day = ProgramDay.objects.create(
            program=program, day_index=day_index, name=layout["names"][day_index]
        )
        if split == Program.Split.FULL_BODY_3X:
            keys = list(placements.keys())
        else:  # upper/lower alternation
            keys = [k for k in placements if k in (UPPER_PATTERNS if day_index % 2 == 0 else LOWER_PATTERNS)]
        for order, key in enumerate(keys):
            ex = placements[key]
            reps_min, reps_max = progression.rep_targets_for(
                ex, ex.rep_range_min, ex.rep_range_max
            )
            PrescribedExercise.objects.create(
                day=day,
                pattern=ex.pattern,
                exercise=ex,
                target_sets=3,
                target_reps_min=reps_min,
                target_reps_max=reps_max,
                target_load=_initial_load(profile, ex),
                target_rest_seconds=ex.rest_seconds,
                order=order,
            )
    return program


def chain_map_for(pattern_ids) -> dict:
    """``{exercise_id: chain_key}`` for every exercise of ``pattern_ids``.

    A chain is a connected component of the ``progression``/``regression`` links,
    so a pattern's bodyweight ladder and its loaded ladder are separate chains.
    Grip variants sharing a ladder position (Pull-up / Chin-up) are unioned into
    the same chain: only the overhand row is guaranteed to be what neighbouring
    rungs link to, and ``select_grip_variant`` alternates grips day to day, so
    counting them apart would halve a chain's tally.

    One query plus in-memory union-find — never a walk per prescription.
    """
    if not pattern_ids:
        return {}
    rows = list(
        Exercise.objects.filter(pattern_id__in=pattern_ids).values(
            "uuid", "pattern_id", "progression_id", "regression_id", "difficulty_rank", "grip"
        )
    )
    parent = {r["uuid"]: r["uuid"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if b is None or a not in parent or b not in parent:
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for r in rows:
        union(r["uuid"], r["progression_id"])
        union(r["uuid"], r["regression_id"])
    # Grip siblings share a ladder position → same chain.
    by_position = {}
    for r in rows:
        if r["grip"] == Exercise.Grip.NA:
            continue
        key = (r["pattern_id"], r["difficulty_rank"])
        if key in by_position:
            union(by_position[key], r["uuid"])
        else:
            by_position[key] = r["uuid"]

    return {r["uuid"]: find(r["uuid"]) for r in rows}


def chain_event_counts(user, chain_of, since_days=CHAIN_EVENT_WINDOW_DAYS) -> dict:
    """``{chain_key: number of recent Full Body A usage events}``.

    One event is one distinct *completed* Full Body A session in which any
    exercise of that chain was actually worked (a ``SetLog`` with at least one
    rep — or, for holds, one second, since timed moves store seconds in
    ``actual_reps``). Counting distinct sessions rather than sets keeps a
    four-set movement from reading as four times as used as a one-set movement.

    Only Full Body A sessions count. B and C train every pattern equally, so
    including them would flatten every chain into a permanent tie and leave the
    tie-break deciding each day. Sessions that are open but unlogged have a null
    ``performed_at`` and are excluded by the window, matching today's behaviour.
    """
    if not chain_of:
        return {}
    cutoff = timezone.now() - timedelta(days=since_days)
    rows = (
        SetLog.objects.filter(
            session__user=user,
            session__status=WorkoutSession.Status.COMPLETED,
            session__performed_at__gte=cutoff,
            session__program_day__day_index=0,
            session__program_day__program__split=Program.Split.FULL_BODY_3X,
            actual_reps__gte=1,
        )
        .values_list("exercise_id", "session_id")
        .distinct()
    )
    sessions_by_chain = {}
    for exercise_id, session_id in rows:
        chain = chain_of.get(exercise_id)
        if chain is not None:
            sessions_by_chain.setdefault(chain, set()).add(session_id)
    return {chain: len(sessions) for chain, sessions in sessions_by_chain.items()}


def select_day_prescriptions(user, program_day: ProgramDay) -> list:
    """The prescriptions to actually train on ``program_day``, in display order.

    Every day trains its full prescription set, except **Full Body A** (the first
    day of a Full Body ×3 program). There we bias toward the movements the user
    has seen least often: keep only the ``FULL_BODY_A_PATTERN_COUNT`` whose
    progression chains have the fewest usage events in the trailing
    ``CHAIN_EVENT_WINDOW_DAYS``, and drop the rest. Counting per chain rather
    than per exercise means climbing a ladder carries the tally forward — reps
    logged on Archer Push-up still count once the prescription has advanced to
    Typewriter Push-up. A chain never used in the window counts zero, so it is
    always kept; ties break by the day's own display order for determinism.

    A prescription the user cannot perform at all is never returned, whatever the
    day — the day trains one fewer pattern instead.
    """
    if program_day.program.user_id != user.pk:
        raise PermissionDenied("program_day belongs to another user")
    owned = owned_equipment_keys_for(user)
    blocked = blocked_exercise_ids(user)
    prescriptions = [
        p
        for p in program_day.prescriptions.select_related("exercise", "pattern")
        .prefetch_related("exercise__required_equipment")
        if is_performable(p.exercise, owned, blocked)
    ]
    is_full_body_a = (
        program_day.program.split == Program.Split.FULL_BODY_3X
        and program_day.day_index == 0
    )
    if not is_full_body_a or len(prescriptions) <= FULL_BODY_A_PATTERN_COUNT:
        return prescriptions

    # Chains are keyed off each prescription's *current* exercise, which
    # progression/equipment changes repoint in place.
    chain_of = chain_map_for({p.exercise.pattern_id for p in prescriptions})
    counts = chain_event_counts(user, chain_of)
    least_used = sorted(
        prescriptions,
        key=lambda p: (counts.get(chain_of.get(p.exercise_id), 0), p.order),
    )[:FULL_BODY_A_PATTERN_COUNT]
    keep = {p.pk for p in least_used}
    # Preserve the day's natural order — this filters the day, it doesn't reorder.
    return [p for p in prescriptions if p.pk in keep]


@transaction.atomic
def start_session(user, program_day: ProgramDay) -> WorkoutSession:
    """Open a WorkoutSession and snapshot expected reps/load from the current
    prescriptions, so planned-vs-real survives later adaptation.

    Full Body A trains only the least-trained subset of patterns (see
    ``select_day_prescriptions``); all other days snapshot every prescription.

    Prescriptions are reconciled against the user's current equipment first, so
    a day never snapshots a movement they can no longer perform."""
    sync_program_equipment(user)
    session = WorkoutSession.objects.create(
        user=user,
        program_day=program_day,
        scheduled_for=timezone.now().date(),
        status=WorkoutSession.Status.PLANNED,
    )
    for presc in select_day_prescriptions(user, program_day):
        # Bar pull-ups re-pick the weaker grip each day they're scheduled; the
        # choice is snapshotted onto this session only (the prescription's
        # nominal grip is untouched). Non-split rungs return unchanged.
        session_exercise = select_grip_variant(user, presc.exercise)
        for set_index in range(presc.target_sets):
            is_amrap = set_index == presc.target_sets - 1
            SetLog.objects.create(
                session=session,
                prescribed_exercise=presc,
                exercise=session_exercise,
                set_index=set_index,
                expected_reps=presc.target_reps_max if is_amrap else presc.target_reps_min,
                expected_load=presc.target_load,
                is_amrap=is_amrap,
            )
    return session


@transaction.atomic
def apply_session_log(session: WorkoutSession, set_results: dict) -> list:
    """Record actual reps/load and advance prescriptions via the engine.

    ``set_results`` maps SetLog uuid -> {"actual_reps", "actual_load", "rir"}.
    Unilateral AMRAP sets may instead send {"left_reps", "right_reps", ...}; both
    sides are stored and ``actual_reps`` is set to the weaker side so progression
    and peer scoring never over-credit the strong leg (mirrors the assessment).
    Returns a list of human-readable progression deltas.
    """
    profile = get_or_create_equipment_profile(session.user)

    def _int_or_none(v):
        if v in (None, ""):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # Save actuals.
    for set_log in session.set_logs.all():
        res = set_results.get(str(set_log.uuid))
        if not res:
            continue
        left = _int_or_none(res.get("left_reps"))
        right = _int_or_none(res.get("right_reps"))
        sides = [v for v in (left, right) if v is not None]
        if sides:
            # Per-leg log: persist both sides; the weaker side drives everything.
            set_log.left_reps = left
            set_log.right_reps = right
            set_log.actual_reps = min(sides)
        else:
            set_log.actual_reps = _int_or_none(res.get("actual_reps"))
        set_log.actual_load = res.get("actual_load")
        set_log.rir = res.get("rir")
        set_log.save(update_fields=[
            "actual_reps", "actual_load", "rir", "left_reps", "right_reps",
        ])

    session.status = WorkoutSession.Status.COMPLETED
    session.performed_at = timezone.now()
    session.save(update_fields=["status", "performed_at"])

    # Advance each prescription from its AMRAP set. The engine moves along the
    # full ladder, so every rung it lands on is re-checked against what the user
    # can actually do — otherwise logging a session could write back a movement
    # whose equipment they no longer own.
    deltas = []
    blocked = blocked_exercise_ids(session.user)
    owned = owned_equipment_keys(profile)
    amrap_sets = session.set_logs.filter(is_amrap=True).select_related(
        "prescribed_exercise", "prescribed_exercise__exercise"
    )
    for amrap in amrap_sets:
        presc = amrap.prescribed_exercise
        if presc is None or amrap.actual_reps is None:
            continue
        # The AMRAP may have been logged on a grip variant (the weaker grip the
        # Forge scheduled today) while ``presc`` tracks the shared rung. Drive the
        # rung from the stronger grip so a weak-grip day never demotes/stalls it.
        reps = effective_progression_reps(session.user, amrap.exercise, amrap.actual_reps)
        new = progression.next_prescription(presc, reps, profile)

        # Earned a harder rung → don't climb automatically. Park it as a pending
        # "unlock" the user must Accept/Deny, and hold at the current rung.
        if new.advanced:
            target = new.exercise
            if not is_performable(target, owned, blocked):
                target = substitute_for(session.user, target) or target
            if presc.pending_progression_id is None:
                presc.pending_progression = target
                presc.save(update_fields=["pending_progression"])
            deltas.append(
                {
                    "exercise": presc.exercise.name,
                    "message": f"Unlocked: {target.name}! Accept to advance.",
                    "unlock": {
                        "prescription": str(presc.uuid),
                        "from_name": presc.exercise.name,
                        "to_name": target.name,
                        "to_uuid": str(target.pk),
                    },
                }
            )
            continue

        # If the engine advanced/regressed onto a move the user can't do — blocked
        # or needing equipment they lack — substitute it.
        if not is_performable(new.exercise, owned, blocked):
            reason = "is blocked" if new.exercise.pk in blocked else "needs equipment you don't have"
            sub = substitute_for(session.user, new.exercise)
            if sub is not None:
                new.exercise = sub
                new.message += f" (substituted {sub.name} — original {reason})"
        presc.exercise = new.exercise
        presc.target_sets = new.target_sets
        # Parity is re-evaluated against the rung we actually landed on — a
        # regression/advance can move onto (or off) a per-side movement.
        presc.target_reps_min, presc.target_reps_max = progression.rep_targets_for(
            new.exercise, new.target_reps_min, new.target_reps_max
        )
        presc.target_load = new.target_load
        presc.sessions_at_top = new.sessions_at_top
        presc.save()
        deltas.append({"exercise": new.exercise.name, "message": new.message})
    return deltas


@transaction.atomic
def accept_progression(user, prescription) -> "Exercise | None":
    """Apply a parked unlock: climb to the harder rung. Returns the new Exercise
    (or None if nothing was pending)."""
    prog = prescription.pending_progression
    if prog is None:
        return None
    profile = get_or_create_equipment_profile(user)
    # Respect a block — or an equipment change — since the unlock was earned.
    if not is_performable(prog, owned_equipment_keys(profile), blocked_exercise_ids(user)):
        prog = substitute_for(user, prog) or prog
    prescription.pending_progression = None
    _repoint_prescription(prescription, prog, profile)
    return prog


@transaction.atomic
def deny_progression(user, prescription) -> None:
    """Decline a parked unlock: stay on the current rung and reset the counter so
    the unlock must be re-earned before prompting again."""
    prescription.pending_progression = None
    prescription.sessions_at_top = 0
    prescription.save(update_fields=["pending_progression", "sessions_at_top"])


@transaction.atomic
def retake_assessment(user) -> AssessmentSession:
    """Deactivate the current assessment + program and open a fresh assessment.
    History is preserved (rows are kept, just flagged inactive)."""
    AssessmentSession.objects.filter(user=user, is_active=True).update(is_active=False)
    Program.objects.filter(user=user, is_active=True).update(is_active=False)
    # Starting a retake IS acting on the nudge — suppress it while the user works
    # through the Trial, otherwise the banner nags through the very flow it asked
    # for. The 30-day clock only clears once the retake is completed.
    dismiss_retest_prompt(user)
    return AssessmentSession.objects.create(user=user, is_active=True)


# ─────────────────────────────────────────────────────────────────────────────
# Retest nudge
# ─────────────────────────────────────────────────────────────────────────────


def dismiss_retest_prompt(user) -> None:
    """Suppress the retest banner for ``RETEST_DISMISS_DAYS``."""
    profile = get_or_create_equipment_profile(user)
    profile.retest_prompt_dismissed_at = timezone.now()
    profile.save(update_fields=["retest_prompt_dismissed_at", "updated_at"])


def retest_status(user) -> dict:
    """Whether to nudge the user to retake the Trial, and the age of the last one.

    Due when the last COMPLETED assessment is at least ``RETEST_INTERVAL_DAYS``
    old AND the nudge has not been dismissed within ``RETEST_DISMISS_DAYS``.
    Keying off completion means an abandoned retake never resets the clock.
    A user who has never completed a Trial is not nudged — they are already
    being sent to the Trial by the empty-program path.
    """
    last = (
        AssessmentSession.objects.filter(user=user, completed_at__isnull=False)
        .order_by("-completed_at")
        .first()
    )
    now = timezone.now()
    if last is None:
        return {
            "retest_due": False,
            "last_trial_at": None,
            "days_since_last_trial": None,
        }

    days_since = (now - last.completed_at).days
    due = days_since >= RETEST_INTERVAL_DAYS
    if due:
        dismissed_at = get_or_create_equipment_profile(user).retest_prompt_dismissed_at
        if dismissed_at is not None and dismissed_at > now - timedelta(
            days=RETEST_DISMISS_DAYS
        ):
            due = False
    return {
        "retest_due": due,
        "last_trial_at": last.completed_at.isoformat(),
        "days_since_last_trial": days_since,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Trial history
# ─────────────────────────────────────────────────────────────────────────────


def ladder_score(result) -> float:
    """Where a Trial result sits on the ladder, as a single comparable number.

    ``difficulty_rank`` of the rung the user was placed on, plus the fraction of
    that rung's rep range they achieved. Normalising this way means climbing a
    rung reads as progress even though the raw reps reset — comparing bare reps
    across rungs would report a promotion as a setback.
    """
    placed = result.placed_exercise
    if placed is None:
        return 0.0
    rep_max = placed.rep_range_max or 1
    return placed.difficulty_rank + min(1.0, (result.reps_or_seconds or 0) / rep_max)


def _verdict(delta) -> str:
    if delta is None:
        return "none"
    if delta > 0:
        return "progress"
    if delta < 0:
        return "setback"
    return "no change"


def trial_history(user) -> dict:
    """Per-pattern Trial series, oldest first, for the Evolution charts.

    Only completed Trials are included — an open retake has no results yet and
    would plot as a phantom zero. Each point carries its verdict against the
    immediately prior Trial *of the same pattern*, computed on ``ladder_score``.
    """
    results = (
        AssessmentResult.objects.filter(
            session__user=user, session__completed_at__isnull=False
        )
        .select_related("session", "pattern", "tested_exercise", "placed_exercise")
        .order_by("session__completed_at", "session__created_at")
    )

    by_pattern = {}
    for r in results:
        entry = by_pattern.setdefault(
            r.pattern.key,
            {
                "pattern_key": r.pattern.key,
                "pattern_name": r.pattern.name,
                "measures_asymmetry": False,
                "points": [],
            },
        )
        score = ladder_score(r)
        prev = entry["points"][-1] if entry["points"] else None
        delta = None if prev is None else round(score - prev["ladder_score"], 3)
        if r.tested_exercise.measures_asymmetry:
            entry["measures_asymmetry"] = True
        entry["points"].append(
            {
                "date": r.session.completed_at.isoformat(),
                "exercise": r.tested_exercise.name,
                "reps_or_seconds": r.reps_or_seconds,
                "is_timed": r.tested_exercise.is_timed,
                "left_reps": r.left_reps,
                "right_reps": r.right_reps,
                "asymmetry_pct": r.asymmetry_pct,
                "ladder_score": round(score, 3),
                "delta_vs_prev": delta,
                "verdict": _verdict(delta),
            }
        )
    return {"patterns": list(by_pattern.values())}

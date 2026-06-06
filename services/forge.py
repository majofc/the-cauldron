"""Forge orchestration — ties the pure progression engine to the ORM.

View/API code calls into here; the pure decision logic stays in
``progression.py``. This module owns DB reads/writes (program generation,
session snapshotting, applying a logged session).
"""

from django.db import transaction
from django.utils import timezone

from the_cauldron.models import (
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


def eligible_exercises(pattern, profile, exclude_ids=None):
    """Exercises for a pattern whose required equipment the user owns.

    Bodyweight is implicitly always available. An exercise is eligible if every
    piece of its required equipment is owned (bodyweight counts as owned).
    ``exclude_ids`` (e.g. the user's blocked exercises) are filtered out.
    """
    owned = set(profile.equipment.values_list("key", flat=True)) | {"bodyweight"}
    exclude_ids = set(exclude_ids or ())
    result = []
    for ex in pattern.exercises.prefetch_related("required_equipment"):
        if ex.pk in exclude_ids:
            continue
        req = set(ex.required_equipment.values_list("key", flat=True)) or {"bodyweight"}
        if req <= owned:
            result.append(ex)
    return result


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
    prescriptions = PrescribedExercise.objects.filter(
        day__program__user=user, day__program__is_active=True, exercise=exercise
    )
    for presc in prescriptions:
        if substitute is None:
            continue
        presc.exercise = substitute
        presc.target_reps_min = substitute.rep_range_min
        presc.target_reps_max = substitute.rep_range_max
        presc.target_load = _initial_load(get_or_create_equipment_profile(user), substitute)
        presc.target_rest_seconds = substitute.rest_seconds
        presc.sessions_at_top = 0
        presc.save()
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
            PrescribedExercise.objects.create(
                day=day,
                pattern=ex.pattern,
                exercise=ex,
                target_sets=3,
                target_reps_min=ex.rep_range_min,
                target_reps_max=ex.rep_range_max,
                target_load=_initial_load(profile, ex),
                target_rest_seconds=ex.rest_seconds,
                order=order,
            )
    return program


@transaction.atomic
def start_session(user, program_day: ProgramDay) -> WorkoutSession:
    """Open a WorkoutSession and snapshot expected reps/load from the current
    prescriptions, so planned-vs-real survives later adaptation."""
    session = WorkoutSession.objects.create(
        user=user,
        program_day=program_day,
        scheduled_for=timezone.now().date(),
        status=WorkoutSession.Status.PLANNED,
    )
    for presc in program_day.prescriptions.select_related("exercise"):
        for set_index in range(presc.target_sets):
            is_amrap = set_index == presc.target_sets - 1
            SetLog.objects.create(
                session=session,
                prescribed_exercise=presc,
                exercise=presc.exercise,
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
    Returns a list of human-readable progression deltas.
    """
    profile = get_or_create_equipment_profile(session.user)

    # Save actuals.
    for set_log in session.set_logs.all():
        res = set_results.get(str(set_log.uuid))
        if not res:
            continue
        set_log.actual_reps = res.get("actual_reps")
        set_log.actual_load = res.get("actual_load")
        set_log.rir = res.get("rir")
        set_log.save(update_fields=["actual_reps", "actual_load", "rir"])

    session.status = WorkoutSession.Status.COMPLETED
    session.performed_at = timezone.now()
    session.save(update_fields=["status", "performed_at"])

    # Advance each prescription from its AMRAP set.
    deltas = []
    blocked = blocked_exercise_ids(session.user)
    amrap_sets = session.set_logs.filter(is_amrap=True).select_related(
        "prescribed_exercise", "prescribed_exercise__exercise"
    )
    for amrap in amrap_sets:
        presc = amrap.prescribed_exercise
        if presc is None or amrap.actual_reps is None:
            continue
        new = progression.next_prescription(presc, amrap.actual_reps, profile)

        # Earned a harder rung → don't climb automatically. Park it as a pending
        # "unlock" the user must Accept/Deny, and hold at the current rung.
        if new.advanced:
            target = new.exercise
            if target.pk in blocked:
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

        # If the engine advanced/regressed onto a blocked move, substitute it.
        if new.exercise.pk in blocked:
            sub = substitute_for(session.user, new.exercise)
            if sub is not None:
                new.exercise = sub
                new.message += f" (substituted {sub.name} — original is blocked)"
        presc.exercise = new.exercise
        presc.target_sets = new.target_sets
        presc.target_reps_min = new.target_reps_min
        presc.target_reps_max = new.target_reps_max
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
    # Respect a block placed after the unlock was earned.
    if prog.pk in blocked_exercise_ids(user):
        prog = substitute_for(user, prog) or prog
    profile = get_or_create_equipment_profile(user)
    prescription.exercise = prog
    prescription.target_reps_min = prog.rep_range_min
    prescription.target_reps_max = prog.rep_range_max
    prescription.target_load = _initial_load(profile, prog)
    prescription.target_rest_seconds = prog.rest_seconds
    prescription.sessions_at_top = 0
    prescription.pending_progression = None
    prescription.save()
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
    return AssessmentSession.objects.create(user=user, is_active=True)

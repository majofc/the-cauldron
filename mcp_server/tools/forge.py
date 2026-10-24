"""The Forge read tools: today's workout, the program, progress, past
sessions, the equipment profile and the Trial.

Everything comes from ``services/forge.py`` (the web's own code) and is only
reshaped here. No consent is needed — training data isn't the sensitive
nutrition data — but every read is the caller's own.
"""

from django.utils import timezone

from the_cauldron.mcp_server.registry import tool
from the_cauldron.mcp_server.tools.nutrition import DATE, _range
from the_cauldron.models import Equipment, WorkoutSession
from the_cauldron.serializers import (
    PlannedSetLogSerializer,
    UserEquipmentProfileSerializer,
    WorkoutSessionSerializer,
)
from the_cauldron.services import forge

MAX_SESSION_DAYS = 62
SWAP_OPTIONS = 6


def _blocks(rows):
    """Serialized SetLogs → one block per movement, sets in order."""
    blocks, by_key = [], {}
    for r in rows:
        key = (r["prescription"], r["exercise"])
        block = by_key.get(key)
        if block is None:
            block = by_key[key] = {
                "exercise_id": r["exercise"],
                "exercise": r["exercise_name"],
                "prescription_id": r["prescription"],
                "timed": r["is_timed"],
                "per_side": r["is_unilateral"],
                "rest_seconds": r["rest_seconds"],
                "cues": r["cues"],
                "load_unit": r["load_unit"],
                "sets": [],
            }
            blocks.append(block)
        block["sets"].append({
            "id": r["uuid"],
            "set": r["set_index"] + 1,
            "to_failure": r["is_amrap"],
            "target": r["expected_reps"],
            "target_load": r["expected_load"],
            "done": r["actual_reps"],
            "load_done": r["actual_load"],
            "rir": r["rir"],
        })
    return blocks


def session_out(session):
    data = WorkoutSessionSerializer(session).data
    return {
        "id": data["uuid"],
        "date": data["scheduled_for"],
        "status": data["status"],
        "performed_at": data["performed_at"],
        "exercises": _blocks(data["set_logs"]),
    }


def _plan_out(user, day):
    rows = PlannedSetLogSerializer(forge.build_today_plan(user, day), many=True).data
    blocks = _blocks(rows)
    for block in blocks:
        for s in block["sets"]:
            s["id"] = None  # not a real set until start_forge_session writes the session
    return blocks


def _swap_options(user):
    return [
        {"exercise": c["exercise_name"], "pattern": c["pattern_name"], "sessions_recently": c["event_count"]}
        for c in forge.swap_candidates(user)["candidates"][:SWAP_OPTIONS]
    ]


def today_state(user):
    """Where today's workout stands: ``(status, session_or_none, day)``.
    status: no_program | unfinished_past_session | in_progress | done | not_started."""
    day = forge.active_day(user)
    if day is None:
        return "no_program", None, None
    unfinished = forge.pending_unfinished(user)
    if unfinished is not None:
        return "unfinished_past_session", unfinished, day
    existing = forge.resumable_session(user)
    if existing is not None:
        return "in_progress", existing, day
    done = (
        WorkoutSession.objects.filter(
            user=user, scheduled_for=timezone.now().date(), status=WorkoutSession.Status.COMPLETED
        ).order_by("-performed_at").first()
    )
    if done is not None:
        return "done", done, day
    return "not_started", None, day


NEXT_STEP = {
    "no_program": "No program yet: the user takes the Trial first (get_trial, then submit_trial_results).",
    "unfinished_past_session": "A past session has logged sets but was never finished: finish it "
                               "(finish_forge_session) or delete it (delete_forge_session) before today's workout.",
    "in_progress": "Log each set with log_forge_set, then finish_forge_session.",
    "done": "Today's workout is finished.",
    "not_started": "Call start_forge_session when the user begins; it returns the set ids to log against.",
}


def forge_summary(user):
    """Compact line for get_today: today's workout state and its movements."""
    status, session, day = today_state(user)
    out = {"status": status}
    if session is not None:
        out["exercises"] = sorted({sl.exercise.name for sl in session.set_logs.select_related("exercise")})
    elif day is not None:
        out["exercises"] = sorted({r.exercise.name for r in forge.build_today_plan(user, day)})
    return out


@tool(
    "get_forge_today",
    title="Today's workout",
    description=(
        "Today's Forge workout: each movement with its sets, rep (or seconds, when timed) and load targets, "
        "rest, cues, and which set is to failure. status tells where it stands — not_started (call "
        "start_forge_session to begin), in_progress (log sets), done, unfinished_past_session (must be finished "
        "or deleted first) or no_program (take the Trial). Also the Trial retest reminder and a few movements "
        "the user could swap in."
    ),
)
def get_forge_today(ctx):
    user = ctx.user
    status, session, day = today_state(user)
    out = {"status": status, "next_step": NEXT_STEP[status]}
    if status == "no_program":
        return out
    if session is not None:
        out["session"] = session_out(session)
    else:
        out["exercises"] = _plan_out(user, day)
    out["swap_options"] = _swap_options(user)
    out.update(forge.retest_status(user))
    return out


@tool(
    "get_forge_program",
    title="Training program",
    description=(
        "The user's current Forge program: every prescribed movement with its sets, rep range (seconds when "
        "timed), load, rest, and any progression earned but not yet applied. The daily workout picks the "
        "least-trained of these."
    ),
)
def get_forge_program(ctx):
    from the_cauldron.serializers import ProgramSerializer

    program = forge.active_program(ctx.user)
    if program is None:
        return {"program": None, "next_step": NEXT_STEP["no_program"]}
    data = ProgramSerializer(program).data
    rows = [p for day in data["days"] for p in day["prescriptions"]]
    return {
        "created_at": data["created_at"],
        "prescriptions": [{
            "prescription_id": p["uuid"],
            "pattern": p["pattern_key"],
            "exercise": p["exercise_name"],
            "timed": p["is_timed"],
            "sets": p["target_sets"],
            "reps_min": p["target_reps_min"],
            "reps_max": p["target_reps_max"],
            "load": p["target_load"],
            "load_unit": p["load_unit"],
            "rest_seconds": p["target_rest_seconds"],
            "pending_progression": p["pending_progression_name"],
        } for p in rows],
    }


@tool(
    "get_forge_progress",
    title="Training progress",
    description=(
        "Progress over the user's completed sessions (total reps, volume, best to-failure set per session; "
        "optionally narrowed to one exercise or one muscle), plus their best peer score (\"fires\", 1–10, "
        "vs people of their age and sex) per exercise."
    ),
    schema={
        "type": "object",
        "properties": {
            "exercise": {"type": "string", "maxLength": 80, "description": "Exercise name, as in get_forge_program."},
            "muscle": {"type": "string", "maxLength": 40, "description": "Muscle key."},
            "last": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30,
                     "description": "How many recent sessions to return."},
        },
        "additionalProperties": False,
    },
)
def get_forge_progress(ctx, exercise=None, muscle=None, last=30):
    data = forge.progress_points(ctx.user, exercise=exercise, muscle=muscle)
    fires = forge.best_flames_by_exercise(ctx.user)
    return {
        "sessions": data["points"][-last:],
        "exercises": data["exercises"],
        "muscles": [m["key"] for m in data["muscles"]],
        "best_fires": {name: {"fires": f["flames"], "value": f["value"], "estimated": f["estimated"]}
                       for name, f in fires.items()},
    }


@tool(
    "get_forge_sessions",
    title="Workout history",
    description="The user's workout sessions between two dates (at most 62 days), with every set as logged.",
    schema={
        "type": "object",
        "properties": {"from": DATE, "to": {**DATE, "description": "Inclusive end; defaults to 'from' + 6 days."}},
        "required": ["from"],
        "additionalProperties": False,
    },
)
def get_forge_sessions(ctx, **args):
    first, last = _range(ctx, args["from"], args.get("to"), 7, MAX_SESSION_DAYS)
    sessions = (
        WorkoutSession.objects.filter(user=ctx.user, scheduled_for__gte=first, scheduled_for__lte=last)
        .prefetch_related("set_logs__exercise", "set_logs__prescribed_exercise")
        .order_by("scheduled_for", "created_at")
    )
    return {"from": first, "to": last, "sessions": [session_out(s) for s in sessions]}


@tool(
    "get_equipment",
    title="Equipment profile",
    description=(
        "What the user trains with: owned equipment keys, load unit, dumbbell / kettlebell weights or plates, "
        "band levels, barbell plates, birth year and sex (used for peer scores). available_equipment lists "
        "every key set_equipment accepts."
    ),
)
def get_equipment(ctx):
    profile = forge.get_or_create_equipment_profile(ctx.user)
    return {
        "profile": UserEquipmentProfileSerializer(profile).data,
        "available_equipment": [
            {"key": e.key, "name": e.name, "loadable": e.is_loadable} for e in Equipment.objects.order_by("name")
        ],
    }


def _how_to_count(exercise):
    if exercise.is_timed:
        text = "Hold as long as you can with good form; count seconds."
    else:
        text = "Do as many reps as you can in one set with good form; count reps."
    return text + (" Count one side (per side)." if exercise.is_per_side else "")


@tool(
    "get_trial",
    title="The Trial (assessment)",
    description=(
        "The Trial places the user on each movement pattern's ladder from one all-out test per pattern. "
        "Returns its status (none, in_progress, done), how to perform each test (exercise, cues, what to count) "
        "with the result already recorded if any, the retest reminder, and the last results per pattern "
        "(trial_history). Submit with submit_trial_results."
    ),
)
def get_trial(ctx):
    from the_cauldron.models import AssessmentSession

    user = ctx.user
    active = AssessmentSession.objects.filter(user=user, is_active=True).prefetch_related(
        "results__pattern", "results__tested_exercise", "results__placed_exercise"
    ).first()
    status = "none" if active is None else ("done" if active.completed_at else "in_progress")
    recorded = {r.pattern.key: r for r in active.results.all()} if active else {}
    tests = []
    for pattern, anchor in forge.trial_anchors(user):
        row = {"pattern": pattern.key, "pattern_name": pattern.name}
        if anchor is not None:
            row.update(
                exercise_id=anchor.uuid, exercise=anchor.name, timed=anchor.is_timed, per_side=anchor.is_per_side,
                cues=anchor.cues, video_url=anchor.video_url, how_to_count=_how_to_count(anchor),
            )
        result = recorded.get(pattern.key)
        if result is not None:
            row["recorded"] = {"value": result.reps_or_seconds, "tested": result.tested_exercise.name,
                               "placed_on": result.placed_exercise.name if result.placed_exercise else None}
        tests.append(row)
    history = forge.trial_history(user)["patterns"]
    return {
        "status": status,
        "tests": tests,
        **forge.retest_status(user),
        "trial_history": [
            {"pattern": p["pattern_key"], "results": [
                {"date": pt["date"][:10], "exercise": pt["exercise"], "value": pt["reps_or_seconds"],
                 "verdict": pt["verdict"]} for pt in p["points"][-3:]
            ]} for p in history
        ],
    }


@tool(
    "get_forge_rung_options",
    title="Rungs the user can move to",
    description=(
        "Every movement chain of the active program with the rungs the user can perform on it: the current one "
        "(is_current) and the others, flagged above_trial when the latest Trial doesn't support them yet. Use the "
        "prescription_id and an exercise_id with set_forge_rung."
    ),
)
def get_forge_rung_options(ctx):
    overview = forge.rung_overview(ctx.user)
    return {"chains": [{
        "prescription_id": c["prescription"],
        "pattern": c["pattern_key"],
        "current": c["current_name"],
        "options": [{
            "exercise_id": o["exercise"],
            "exercise": o["name"],
            "rank": o["difficulty_rank"],
            "mode": o["progression_mode"],
            "is_current": o["is_current"],
            "above_trial": o["unready"],
        } for o in c["options"]],
    } for c in overview["chains"]]}

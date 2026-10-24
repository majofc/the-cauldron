"""The Forge write tools: equipment, the Trial, and logging a workout.

Each one calls the same ``services/forge.py`` function the website uses, so
placement, programs and progression come out exactly as on the web for the
same inputs. Sessions and Trials started here carry ``channel="mcp"``.

The unfinished-session rule holds: while a past session with logged sets was
never finished, today's workout can't start — ``finish_forge_session`` or
``delete_forge_session`` it first. Changing equipment, retaking the Trial,
editing a logged set and deleting a session are two-step (``confirm=True``).
Setting the rung, the load and the weekly volume (#109) are not: they are the
user's explicit choice and reversible the same way.
"""

import posthog

from the_cauldron.mcp_server.errors import ToolError
from the_cauldron.mcp_server.registry import WRITE, tool
from the_cauldron.mcp_server.tools.forge import NEXT_STEP, session_out, today_state
from the_cauldron.models import Channel, Exercise, PrescribedExercise, SetLog, WorkoutSession
from the_cauldron.serializers import AssessmentSessionSerializer, UserEquipmentProfileSerializer
from the_cauldron.services import forge

MCP = Channel.MCP
NUMBERS = {"type": "array", "items": {"type": "number", "minimum": 0}, "maxItems": 60}
EQUIPMENT_FIELDS = {
    "equipment": {"type": "array", "items": {"type": "string"}, "maxItems": 40,
                  "description": "Every equipment key the user owns (replaces the list); see get_equipment."},
    "load_unit": {"type": "string", "enum": ["kg", "lb"]},
    "dumbbell_mode": {"type": "string", "enum": ["fixed", "plates"]},
    "dumbbell_weights": {**NUMBERS, "description": "Fixed dumbbells owned, per hand."},
    "dumbbell_plates": NUMBERS,
    "dumbbell_handle_weight": {"type": "number", "minimum": 0},
    "band_levels": {"type": "array", "items": {"type": ["string", "number"]}, "maxItems": 20,
                    "description": "Band tensions or labels the user owns, easiest first."},
    "barbell_min_increment": {"type": "number", "minimum": 0},
    "barbell_plates": NUMBERS,
    "bar_weight": {"type": "number", "minimum": 0},
    "kettlebell_mode": {"type": "string", "enum": ["fixed", "plates"]},
    "kettlebell_weights": NUMBERS,
    "kettlebell_plates": NUMBERS,
    "kettlebell_handle_weight": {"type": "number", "minimum": 0},
}
SET_VALUES = {
    "set_id": {"type": "string", "description": "A set id from get_forge_today / start_forge_session."},
    "reps": {"type": "integer", "minimum": 0, "maximum": 1000,
             "description": "Reps done — or seconds held, for a timed movement; per side for per-side ones."},
    "load": {"type": "number", "minimum": 0, "maximum": 1000, "description": "Weight actually used, if any."},
    "rir": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Reps in reserve, if known."},
}
SESSION_ID = {"type": "string", "description": "A session id from get_forge_today or get_forge_sessions."}


@tool(
    "set_equipment",
    title="Change the equipment profile",
    scope=WRITE,
    confirm=True,
    description=(
        "Change what the user trains with (only the fields sent change; equipment replaces the owned list). "
        "This can change future prescriptions. Two-step: preview, then confirm_token after the user agrees. "
        "Birth year and sex (for the peer score) are set by the user on their Profile page, not here."
    ),
    schema={"type": "object", "properties": EQUIPMENT_FIELDS, "minProperties": 1, "additionalProperties": False},
)
def set_equipment(ctx, apply, **args):
    profile = forge.get_or_create_equipment_profile(ctx.user)
    serializer = UserEquipmentProfileSerializer(profile, data=args, partial=True)
    serializer.is_valid(raise_exception=True)
    if not apply:
        current = UserEquipmentProfileSerializer(profile).data
        return {"target": profile.uuid, "preview": {
            "changes": {k: {"from": current.get(k), "to": v} for k, v in args.items()},
        }}
    serializer.save()
    forge.mark_equipment_configured(profile)
    return {"profile": UserEquipmentProfileSerializer(profile).data}


@tool(
    "submit_trial_results",
    title="Submit Trial results",
    scope=WRITE,
    description=(
        "Record the Trial: one all-out result per movement pattern (reps, or seconds for timed tests), done on "
        "the exercise get_trial names. Places the user on each ladder and builds their program — exactly like "
        "the website. Send every pattern the user tested."
    ),
    schema={
        "type": "object",
        "properties": {
            "results": {
                "type": "array", "minItems": 1, "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Pattern key from get_trial."},
                        "value": {"type": "integer", "minimum": 0, "maximum": 10000,
                                  "description": "Reps, or seconds held."},
                        "exercise_id": {"type": "string",
                                        "description": "Only if they tested on another exercise than get_trial's."},
                    },
                    "required": ["pattern", "value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["results"],
        "additionalProperties": False,
    },
)
def submit_trial_results(ctx, results):
    anchors = {p.key: a for p, a in forge.trial_anchors(ctx.user)}
    payload = []
    for r in results:
        if r["pattern"] not in anchors:
            raise ToolError("invalid_arguments", f"Unknown pattern '{r['pattern']}'. Use the keys from get_trial.")
        exercise_id = r.get("exercise_id") or (anchors[r["pattern"]].uuid if anchors[r["pattern"]] else None)
        if exercise_id is None:
            raise ToolError("invalid_arguments", f"No test exercise for '{r['pattern']}'; send exercise_id.")
        payload.append({"pattern_key": r["pattern"], "tested_exercise": str(exercise_id),
                        "reps_or_seconds": r["value"]})
    try:
        session, program, peer = forge.submit_assessment(ctx.user, payload, channel=MCP)
    except forge.TrialInputError as exc:
        raise ToolError("invalid_input", str(exc)) from None
    data = AssessmentSessionSerializer(session).data
    fires = {p["pattern_key"]: p["score"] for p in peer}
    return {
        "placements": [
            {"pattern": r["pattern_key"], "value": r["reps_or_seconds"], "placed_on": r["placed_exercise_name"],
             "fires": (fires.get(r["pattern_key"]) or {}).get("flames")}
            for r in data["results"]
        ],
        "program_ready": program is not None,
        "next_step": "Call get_forge_today for the first workout.",
    }


@tool(
    "retake_trial",
    title="Retake the Trial",
    scope=WRITE,
    confirm=True,
    destructive=True,
    description=(
        "Start a new Trial: the current program and assessment are set aside (history is kept) until new "
        "results are submitted. Two-step: preview, then confirm_token after the user agrees."
    ),
)
def retake_trial(ctx, apply):
    if not apply:
        has_program = forge.active_day(ctx.user) is not None
        return {"target": ctx.user.pk, "preview": {
            "effect": "The current program stops until the new Trial results are submitted." if has_program
                      else "Opens a new Trial.",
        }}
    session = forge.retake_assessment(ctx.user, channel=MCP)
    return {"trial": {"id": session.uuid, "status": "in_progress"},
            "next_step": "Call get_trial for the tests, then submit_trial_results."}


@tool(
    "start_forge_session",
    title="Start today's workout",
    scope=WRITE,
    description=(
        "Start today's Forge workout from the plan get_forge_today shows; returns the session with real set ids "
        "to log against. If today's workout already started, returns it as it is."
    ),
)
def start_forge_session(ctx):
    status, session, day = today_state(ctx.user)
    if status == "no_program":
        raise ToolError("no_program", NEXT_STEP["no_program"])
    if status == "unfinished_past_session":
        raise ToolError("unfinished_session", NEXT_STEP["unfinished_past_session"], session=session_out(session))
    if status in ("in_progress", "done"):
        return {"status": status, "session": session_out(session)}
    plan = forge.build_today_plan(ctx.user, day)
    snapshot = [{
        "exercise": str(row.exercise.uuid),
        "prescription": str(row.prescribed_exercise.uuid) if row.prescribed_exercise else None,
        "set_index": row.set_index,
    } for row in plan]
    try:
        session = forge.create_session_from_plan(ctx.user, day, snapshot, channel=MCP)
    except ValueError as exc:
        raise ToolError("invalid_input", str(exc)) from None
    return {"status": "in_progress", "session": session_out(session),
            "next_step": NEXT_STEP["in_progress"]}


def _own_set(ctx, set_id):
    row = SetLog.objects.select_related("session", "exercise").get(uuid=set_id, session__user=ctx.user)
    if row.session.status == WorkoutSession.Status.COMPLETED:
        raise ToolError("conflict", "That session is finished; its sets can't change anymore.")
    return row


def _values(args):
    return {"actual_reps": args.get("reps"), "actual_load": args.get("load"), "rir": args.get("rir")}


def _set_out(row):
    row.refresh_from_db()
    return {"id": row.uuid, "exercise": row.exercise.name, "set": row.set_index + 1, "to_failure": row.is_amrap,
            "done": row.actual_reps, "load_done": row.actual_load, "rir": row.rir}


@tool(
    "log_forge_set",
    title="Log a set",
    scope=WRITE,
    description=(
        "Record one set of the workout in progress: reps (or seconds for timed movements, per side for "
        "per-side ones), and the load actually used if different. To change a set already logged, use "
        "update_forge_set."
    ),
    schema={"type": "object", "properties": SET_VALUES, "required": ["set_id", "reps"], "additionalProperties": False},
)
def log_forge_set(ctx, set_id, **args):
    row = _own_set(ctx, set_id)
    if row.actual_reps is not None or row.actual_load is not None:
        raise ToolError("already_logged", "That set is already logged; use update_forge_set to change it.",
                        set=_set_out(row))
    forge.save_session_values(row.session, {str(row.uuid): _values(args)})
    remaining = row.session.set_logs.filter(actual_reps__isnull=True).count()
    return {"logged": _set_out(row), "sets_left": remaining,
            "next_step": "Log the next set." if remaining else "All sets logged: call finish_forge_session."}


@tool(
    "update_forge_set",
    title="Change a logged set",
    scope=WRITE,
    confirm=True,
    description="Change a set already logged in an unfinished session. Two-step: preview, then confirm_token.",
    schema={"type": "object", "properties": SET_VALUES, "required": ["set_id"], "minProperties": 2,
            "additionalProperties": False},
)
def update_forge_set(ctx, set_id, apply, **args):
    row = _own_set(ctx, set_id)
    values = {"reps": row.actual_reps, "load": row.actual_load, "rir": row.rir, **args}
    if not apply:
        return {"target": row.uuid, "preview": {"set": _set_out(row), "changes": args}}
    forge.save_session_values(row.session, {str(row.uuid): _values(values)})
    return {"updated": _set_out(row)}


def _own_session(ctx, session_id):
    return WorkoutSession.objects.get(uuid=session_id, user=ctx.user)


@tool(
    "finish_forge_session",
    title="Finish a workout",
    scope=WRITE,
    description=(
        "Finish a workout with the sets logged so far: progression runs exactly like the website (targets, "
        "loads and new movements earned) and the to-failure sets get peer scores. Also how to close an "
        "unfinished past session."
    ),
    schema={"type": "object", "properties": {"session_id": SESSION_ID}, "required": ["session_id"],
            "additionalProperties": False},
)
def finish_forge_session(ctx, session_id):
    session = _own_session(ctx, session_id)
    if session.status != WorkoutSession.Status.PLANNED:
        raise ToolError("conflict", f"That session is already {session.status}.")
    deltas = forge.apply_session_log(session, {})
    peer = forge.session_peer_scores(ctx.user, session)
    return {
        "session": session_out(session),
        "progression": deltas,
        "fires": [{"exercise": p["exercise"], "value": p["value"], "fires": (p["score"] or {}).get("flames")}
                  for p in peer],
    }


@tool(
    "delete_forge_session",
    title="Delete an unfinished workout",
    scope=WRITE,
    confirm=True,
    destructive=True,
    description=(
        "Delete a workout that was never finished, with its logged sets (the program isn't touched). "
        "Finished workouts are history and can't be deleted. Two-step: preview, then confirm_token."
    ),
    schema={"type": "object", "properties": {"session_id": SESSION_ID}, "required": ["session_id"],
            "additionalProperties": False},
)
def delete_forge_session(ctx, session_id, apply):
    session = _own_session(ctx, session_id)
    if session.status == WorkoutSession.Status.COMPLETED:
        raise ToolError("conflict", "A finished workout is history and can't be deleted.")
    if not apply:
        return {"target": session.uuid, "preview": {"delete": session_out(session)}}
    forge.discard_session(session)
    return {"deleted": session_id}


PRESCRIPTION_ID = {"type": "string", "description": "A prescription_id from get_forge_program or get_forge_rung_options."}


def _prescription(ctx, prescription_id):
    return PrescribedExercise.objects.select_related("exercise__pattern", "day__program").get(
        uuid=prescription_id, day__program__user=ctx.user, day__program__is_active=True,
    )


def _capture(event, pattern_key, direction):
    posthog.capture(event, properties={"pattern_key": pattern_key, "direction": direction, "source": "mcp"})


@tool(
    "set_forge_rung",
    title="Set the current rung",
    scope=WRITE,
    description=(
        "Move a movement to any rung of its ladder the user can perform (get_forge_rung_options), up or down, "
        "program-wide. Rep targets, load and rest are re-derived and the climb restarts from there; today's "
        "untouched sets follow. Climbing past what the latest Trial supports is allowed (the user asked for it)."
    ),
    schema={"type": "object", "properties": {"prescription_id": PRESCRIPTION_ID,
            "exercise_id": {"type": "string", "description": "The rung's exercise_id."}},
            "required": ["prescription_id", "exercise_id"], "additionalProperties": False},
)
def set_forge_rung(ctx, prescription_id, exercise_id):
    presc = _prescription(ctx, prescription_id)
    target = Exercise.objects.get(uuid=exercise_id)
    try:
        result = forge.set_rung(ctx.user, presc, target, confirm_unready=True)
    except forge.SetsAlreadyLogged as exc:
        raise ToolError("conflict", str(exc)) from None
    except ValueError as exc:
        raise ToolError("invalid_input", str(exc)) from None
    _capture("forge_rung_set", presc.exercise.pattern.key, result["direction"])
    return {"exercise": result["exercise"].name, "applied_to": result["applied_to"]}


@tool(
    "set_forge_load",
    title="Set the training load",
    scope=WRITE,
    description=(
        "Set the load a load-mode movement trains with, program-wide. The value snaps DOWN to the nearest load "
        "the user can build (below the lightest → the lightest); snapped_from says when it moved. Bands take a "
        "band level index the user owns. Today's untouched sets follow."
    ),
    schema={"type": "object", "properties": {"prescription_id": PRESCRIPTION_ID,
            "load": {"type": "number", "description": "kg or lb per the profile's load_unit, or a band level."}},
            "required": ["prescription_id", "load"], "additionalProperties": False},
)
def set_forge_load(ctx, prescription_id, load):
    presc = _prescription(ctx, prescription_id)
    try:
        result = forge.set_load(ctx.user, presc, load)
    except forge.SetsAlreadyLogged as exc:
        raise ToolError("conflict", str(exc)) from None
    except ValueError as exc:
        raise ToolError("invalid_input", str(exc)) from None
    _capture("forge_load_set", presc.exercise.pattern.key, result["direction"])
    return {k: result[k] for k in ("load", "snapped_from", "applied_to")}


@tool(
    "set_forge_weekly_volume",
    title="Set the weekly volume",
    scope=WRITE,
    description=(
        f"Working sets per movement pattern per week ({forge.VOLUME_MIN}-{forge.VOLUME_MAX}, default "
        f"{forge.DEFAULT_WEEKLY_VOLUME}). Sets per session follow for every pattern "
        f"(clamped {forge.SETS_MIN}-{forge.SETS_MAX}); today's untouched sets too."
    ),
    schema={"type": "object", "properties": {"sets_per_week": {
            "type": "integer", "minimum": forge.VOLUME_MIN, "maximum": forge.VOLUME_MAX}},
            "required": ["sets_per_week"], "additionalProperties": False},
)
def set_forge_weekly_volume(ctx, sets_per_week):
    program = forge.active_program(ctx.user)
    if program is None:
        raise ToolError("not_found", "There is no active program yet: submit the Trial first.")
    try:
        result = forge.set_weekly_volume(ctx.user, program, sets_per_week)
    except ValueError as exc:
        raise ToolError("invalid_input", str(exc)) from None
    _capture("forge_volume_set", None, result["direction"])
    return {"weekly_volume_target": result["weekly_volume_target"], "prescriptions_changed": result["changed"]}

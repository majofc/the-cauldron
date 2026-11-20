"""Shared test helpers for The Forge."""

from the_cauldron.services import forge


def plan_snapshot(rows):
    """The client's snapshot of a computed plan (what forge.js posts)."""
    return [
        {
            "exercise": str(row.exercise.uuid),
            "prescription": str(row.prescribed_exercise.uuid) if row.prescribed_exercise else None,
            "set_index": row.set_index,
        }
        for row in rows
    ]


def persist_today(user, day):
    """Persist today's plan the way the app does (#56): build it, then write it
    as on the first logged value. Replaces the old up-front session writer."""
    return forge.create_session_from_plan(user, day, plan_snapshot(forge.build_today_plan(user, day)))

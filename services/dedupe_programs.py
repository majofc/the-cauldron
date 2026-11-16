"""Find the duplicate Programs / assessments that racing Trial submits left
behind (#26). Shared by migration 0043 and ``manage.py dedupe_programs``.

Takes the model classes as arguments so the migration can pass its historical
models. Pure planning: nothing here writes.
"""

from collections import defaultdict
from datetime import timedelta

CLONE_WINDOW = timedelta(minutes=10)


def plan(Program, AssessmentSession, WorkoutSession):
    """``{"programs": [pk…], "assessments": [pk…], "per_user": {user_id: (n_programs, n_assessments)}}``.

    A Program is a clone when it is inactive, has no WorkoutSession on any of
    its days, and another Program of the same user was created less than
    ``CLONE_WINDOW`` after it. Then an assessment is a clone when no Program
    is left pointing at it and it completed less than ``CLONE_WINDOW`` before
    another completed assessment of the same user. Active Programs and anything
    with logged sessions are never listed.
    """
    with_sessions = set(
        WorkoutSession.objects.exclude(program_day=None).values_list("program_day__program_id", flat=True)
    )
    by_user = defaultdict(list)
    for p in Program.objects.order_by("user_id", "created_at").values("pk", "user_id", "is_active", "created_at",
                                                                     "source_assessment_id"):
        by_user[p["user_id"]].append(p)

    doomed_programs = []
    surviving_links = set()
    per_user = defaultdict(lambda: [0, 0])
    for user_id, programs in by_user.items():
        for i, p in enumerate(programs):
            later = programs[i + 1] if i + 1 < len(programs) else None
            clone = (
                not p["is_active"]
                and p["pk"] not in with_sessions
                and later is not None
                and later["created_at"] - p["created_at"] < CLONE_WINDOW
            )
            if clone:
                doomed_programs.append(p["pk"])
                per_user[user_id][0] += 1
            elif p["source_assessment_id"]:
                surviving_links.add(p["source_assessment_id"])

    doomed_assessments = []
    completed = defaultdict(list)
    for a in (AssessmentSession.objects.exclude(completed_at=None)
              .order_by("user_id", "completed_at").values("pk", "user_id", "completed_at")):
        completed[a["user_id"]].append(a)
    for user_id, sessions in completed.items():
        for i, a in enumerate(sessions[:-1]):
            if a["pk"] in surviving_links:
                continue
            if sessions[i + 1]["completed_at"] - a["completed_at"] < CLONE_WINDOW:
                doomed_assessments.append(a["pk"])
                per_user[user_id][1] += 1

    return {
        "programs": doomed_programs,
        "assessments": doomed_assessments,
        "per_user": {k: tuple(v) for k, v in per_user.items()},
    }


def shared_links(Program):
    """``{assessment_id: [program pk…]}`` for assessments still linked from more
    than one Program, most-relevant first (active, then newest)."""
    links = defaultdict(list)
    for p in Program.objects.exclude(source_assessment=None).order_by("-is_active", "-created_at").values(
        "pk", "source_assessment_id"
    ):
        links[p["source_assessment_id"]].append(p["pk"])
    return {a: pks for a, pks in links.items() if len(pks) > 1}

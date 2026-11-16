"""The Forge DRF API. All endpoints require auth and are scoped to request.user."""

import uuid as uuid_module
from collections.abc import Mapping

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import posthog
from the_cauldron.forge_i18n import day_name, localized
from the_cauldron.models import (
    AssessmentSession,
    Equipment,
    Exercise,
    MovementPattern,
    PrescribedExercise,
    WorkoutSession,
)
from the_cauldron.permissions import AdultOnly
from the_cauldron.serializers import (
    AssessmentInputSerializer,
    AssessmentSessionSerializer,
    SetValuesInputSerializer,
    ExerciseSerializer,
    MovementPatternSerializer,
    PlannedSetLogSerializer,
    ProgramSerializer,
    UserEquipmentProfileSerializer,
    WorkoutSessionSerializer,
)
from the_cauldron.services import forge


class ForgeApiMixin:
    """Shared by every Forge view.

    - "Today" is read on the device in use (#56): the client's IANA zone from
      ``X-Cauldron-Timezone`` is scoped to the request for ``forge.local_today``.
      An invalid zone is ignored (profile zone, then the default).
    - A write whose body isn't a JSON object (a list, a string) is a 400 before
      the view runs (#10), never a 500 further down.
    """

    def dispatch(self, request, *args, **kwargs):
        with forge.device_timezone(request.headers.get("X-Cauldron-Timezone")):
            return super().dispatch(request, *args, **kwargs)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method in ("POST", "PUT", "PATCH") and not isinstance(request.data, Mapping):
            raise ParseError(_("The request body must be a JSON object."))


def _invalid(errors):
    return Response(errors, status=status.HTTP_400_BAD_REQUEST)


def _set_values_or_errors(session, data):
    """``data["sets"]`` checked against #10's shape: an object of this
    session's set uuids → ``{actual_reps, actual_load}`` (whole/finite numbers
    ≥ 0, or null). Returns ``(sets, None)`` or ``(None, errors)``. Other keys in
    a set (an old client's reps in reserve) are ignored."""
    sets = data.get("sets", {})
    if not isinstance(sets, Mapping):
        return None, {"sets": [_("Expected an object of set id → values.")]}
    known = {str(u) for u in session.set_logs.values_list("uuid", flat=True)}
    errors, clean = {}, {}
    for key, values in sets.items():
        if str(key) not in known:
            errors[str(key)] = [_("Not a set of this session.")]
            continue
        serializer = SetValuesInputSerializer(data=values)
        if not serializer.is_valid():
            errors[str(key)] = serializer.errors
            continue
        clean[str(key)] = serializer.validated_data
    if errors:
        return None, {"sets": errors}
    return clean, None


class PatternViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, AdultOnly]
    serializer_class = MovementPatternSerializer
    queryset = MovementPattern.objects.all()
    lookup_field = "uuid"


class ExerciseViewSet(ForgeApiMixin, viewsets.ReadOnlyModelViewSet):
    """Catalog of exercises, optionally filtered to the user's owned equipment
    via ?equipment=mine."""

    permission_classes = [IsAuthenticated, AdultOnly]
    serializer_class = ExerciseSerializer
    lookup_field = "uuid"

    def get_queryset(self):
        qs = Exercise.objects.select_related("pattern").prefetch_related(
            "required_equipment", "alternative_equipment", "muscles"
        )
        if self.request.query_params.get("equipment") == "mine":
            profile = forge.get_or_create_equipment_profile(self.request.user)
            owned = set(profile.equipment.values_list("key", flat=True)) | {"bodyweight"}
            # One rule for "can I do this?" — required AND, alternatives OR.
            keep = [e.pk for e in qs if forge.is_performable(e, owned)]
            qs = qs.filter(pk__in=keep)
        return qs

    @action(detail=True, methods=["post"])
    def block(self, request, uuid=None):
        """Forbid this exercise for the user; swap any live prescription using it
        to a substitute. Returns the chosen substitute (or null)."""
        exercise = self.get_object()
        result = forge.block_exercise(
            request.user, exercise, reason=request.data.get("reason", "")
        )
        sub = result["substitute"]
        posthog.capture(
            "exercise_blocked",
            properties={"has_substitute": sub is not None, "swapped_count": result["swapped"]},
        )
        return Response(
            {
                "blocked": str(exercise.uuid),
                "substitute": ExerciseSerializer(sub).data if sub else None,
                "swapped": result["swapped"],
            }
        )

    @action(detail=True, methods=["post"])
    def unblock(self, request, uuid=None):
        """Lift the block on this exercise."""
        exercise = self.get_object()
        forge.unblock_exercise(request.user, exercise)
        posthog.capture("exercise_unblocked")
        return Response({"unblocked": str(exercise.uuid)})


class CatalogView(ForgeApiMixin, APIView):
    """Full exercise catalog grouped by equipment, annotated with the user's
    blocks and (for blocked moves) the substitute that would be used.

    Drives the "Exercises" tab: browse every move, block/unblock, see the
    stand-in. Each exercise is filed under its primary required equipment
    (bodyweight if it needs none); ``owned`` flags groups the user can train.
    """

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        profile = forge.get_or_create_equipment_profile(request.user)
        owned = set(profile.equipment.values_list("key", flat=True)) | {"bodyweight"}
        blocked = forge.blocked_exercise_ids(request.user)
        # Best "fires" (peer flames) the user has ever earned per exercise.
        best_fires = forge.best_flames_by_exercise(request.user)

        # Stable group order: bodyweight first, then the rest by display name.
        equipment = list(Equipment.objects.all())
        equipment.sort(key=lambda e: (e.key != "bodyweight", e.name))
        groups = {
            e.key: {
                "equipment_key": e.key,
                "equipment_name": e.name,
                "equipment_display_name": localized(e, "name"),
                "owned": e.key in owned,
                "exercises": [],
            }
            for e in equipment
        }

        exercises = (
            Exercise.objects.select_related("pattern")
            .prefetch_related(
                "required_equipment", "alternative_equipment", "muscles"
            )
            .order_by("pattern__name", "difficulty_rank")
        )
        # Cache substitutes only for blocked exercises (small set).
        for ex in exercises:
            req = list(ex.required_equipment.all())
            req_keys = [e.key for e in req]
            alt_keys = [e.key for e in ex.alternative_equipment.all()]
            # Primary group: first non-bodyweight requirement, else the first
            # any-of option (a bar-or-rings rung files under the bar), else
            # bodyweight.
            primary = next(
                (k for k in req_keys if k != "bodyweight"),
                next(iter(alt_keys), "bodyweight"),
            )
            group = groups.get(primary) or groups["bodyweight"]
            is_blocked = ex.pk in blocked
            substitute = None
            if is_blocked:
                sub = forge.substitute_for(request.user, ex)
                substitute = (
                    {"uuid": str(sub.uuid), "name": sub.name} if sub else None
                )
            fires = best_fires.get(ex.name)
            group["exercises"].append(
                {
                    "uuid": str(ex.uuid),
                    "name": ex.name,
                    "display_name": localized(ex, "name"),
                    "pattern_key": ex.pattern.key,
                    "pattern_name": ex.pattern.name,
                    "difficulty_rank": ex.difficulty_rank,
                    "required_equipment": req_keys,
                    "alternative_equipment": alt_keys,
                    "is_timed": ex.is_timed,
                    "muscles": [
                        {"key": m.key, "name": m.name, "display_name": localized(m, "name")}
                        for m in ex.muscles.all()
                    ],
                    "eligible": forge.is_performable(ex, owned),
                    "is_blocked": is_blocked,
                    "substitute": substitute,
                    # Highest peer "fires" (1-10) ever earned on this move, with
                    # the AMRAP value behind it. Null when never scored.
                    # ``best_fires_estimated`` flags a placeholder benchmark.
                    "best_fires": fires["flames"] if fires else None,
                    "best_fires_value": fires["value"] if fires else None,
                    "best_fires_estimated": fires["estimated"] if fires else False,
                }
            )

        # Drop empty groups; keep order.
        result = [g for g in groups.values() if g["exercises"]]
        return Response({"groups": result})


class EquipmentProfileView(ForgeApiMixin, APIView):
    """GET/PUT the current user's equipment profile."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        profile = forge.get_or_create_equipment_profile(request.user)
        return Response(UserEquipmentProfileSerializer(profile).data)

    def put(self, request):
        profile = forge.get_or_create_equipment_profile(request.user)
        serializer = UserEquipmentProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if forge.mark_equipment_configured(profile):
            posthog.capture("equipment_profile_configured")
        return Response(serializer.data)


class AssessmentView(ForgeApiMixin, APIView):
    """POST AMRAP results → place the user and generate a program.

    Body: {"results": [
        {"pattern_key": "...", "tested_exercise": "<uuid>", "reps_or_seconds": 8}, ...]}

    A ``split`` key is accepted and ignored — every program is one all-patterns
    day now — so an older cached client still forges a program.
    """

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        active = AssessmentSession.objects.filter(user=request.user, is_active=True).first()
        if not active:
            return Response({"detail": _("No active assessment.")}, status=404)
        return Response(AssessmentSessionSerializer(active).data)

    def post(self, request):
        # Shape first (#10): a non-list ``results`` or a non-object item is a
        # 400 with field errors; domain rules stay in ``TrialInputError``.
        shape = AssessmentInputSerializer(data=request.data)
        if not shape.is_valid():
            return _invalid(shape.errors)
        results = [
            {**row, "tested_exercise": str(row["tested_exercise"])} for row in shape.validated_data["results"]
        ]
        try:
            sub = forge.submit_assessment(request.user, results)
        except forge.TrialInputError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not sub.idempotent:
            posthog.capture(
                "assessment_completed",
                properties={"result_count": len(request.data.get("results", []))},
            )
        # A retry of a Trial already submitted (#26) gets the current program
        # back with 200 and writes nothing.
        return Response(
            {
                "assessment": AssessmentSessionSerializer(sub.assessment).data,
                "program": ProgramSerializer(sub.program).data,
                "peer": sub.peer,
                "idempotent": sub.idempotent,
            },
            status=status.HTTP_200_OK if sub.idempotent else status.HTTP_201_CREATED,
        )


class RetakeAssessmentView(ForgeApiMixin, APIView):
    """POST → deactivate current assessment/program, open a fresh assessment."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def post(self, request):
        session = forge.retake_assessment(request.user)
        posthog.capture("assessment_retaken")
        return Response(AssessmentSessionSerializer(session).data, status=201)


def _live_prescription(request, presc_uuid):
    """One prescription of the user's active program, else 404 (also for
    another user's)."""
    return get_object_or_404(
        PrescribedExercise,
        uuid=presc_uuid,
        day__program__user=request.user,
        day__program__is_active=True,
    )


class ProgressionActionView(ForgeApiMixin, APIView):
    """Accept (climb) or deny (hold) a parked difficulty unlock for one
    prescription. POST /progression/<presc_uuid>/<accept|deny>/ — any other
    action is a 404 from routing (#10)."""

    permission_classes = [IsAuthenticated, AdultOnly]
    accept = staticmethod(forge.accept_progression)
    deny = staticmethod(forge.deny_progression)
    event = "progression"

    def post(self, request, presc_uuid, action):
        presc = _live_prescription(request, presc_uuid)
        if action == "accept":
            new = self.accept(request.user, presc)
            posthog.capture(f"{self.event}_accepted")
            return Response(
                {"accepted": True, "exercise": ExerciseSerializer(new).data if new else None}
            )
        self.deny(request.user, presc)
        posthog.capture(f"{self.event}_denied")
        return Response({"denied": True})


class RegressionActionView(ProgressionActionView):
    """The same for a parked move *down* (#23): POST
    /regression/<presc_uuid>/<accept|deny>/."""

    accept = staticmethod(forge.accept_regression)
    deny = staticmethod(forge.deny_regression)
    event = "regression"


class CeilingNoticeDismissView(ForgeApiMixin, APIView):
    """POST /prescription/<uuid>/ceiling-notice/dismiss/ — hide the #24
    equipment prompt until it applies anew."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def post(self, request, presc_uuid):
        forge.dismiss_ceiling_notice(_live_prescription(request, presc_uuid))
        return Response(status=status.HTTP_204_NO_CONTENT)


class RungOverviewView(ForgeApiMixin, APIView):
    """GET → every chain on the live program with the rungs the user may move to,
    plus ``by_exercise`` (rung uuid → prescription uuid) for the skill tree."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        return Response(forge.rung_overview(request.user))


class SetRungView(ForgeApiMixin, APIView):
    """Move a prescription to any performable rung of its chain, up or down.

    ``GET /prescription/<presc_uuid>/set-rung/`` — the picker's options.
    ``POST`` with ``{"exercise": "<uuid>", "confirm_unready": false}``:

    - 200 ``{"exercise": {...}, "applied_to": n}`` — every live prescription on
      the chain moved.
    - 409 ``{"unready": true, "required", "your_trial", "pattern", ...}`` — the
      rung is above what the latest Trial supports; re-POST with
      ``confirm_unready: true`` to proceed. Enforced here, not in the client.
    - 409 ``{"detail"}`` — the movement already has logged sets today.
    - 400 — not a uuid, not on this chain, blocked or needs missing gear.
    """

    permission_classes = [IsAuthenticated, AdultOnly]

    def _prescription(self, request, presc_uuid):
        return get_object_or_404(
            PrescribedExercise.objects.select_related("exercise__pattern"),
            uuid=presc_uuid,
            day__program__user=request.user,
            day__program__is_active=True,
        )

    def get(self, request, presc_uuid):
        presc = self._prescription(request, presc_uuid)
        return Response(forge.rung_options(request.user, presc))

    def post(self, request, presc_uuid):
        presc = self._prescription(request, presc_uuid)
        target = _exercise_or_400(request.data.get("exercise"))
        if target is None:
            return Response(
                {"detail": _("exercise must be an exercise uuid.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Strictly a JSON true — a stray truthy string must not skip the warning.
        confirm = request.data.get("confirm_unready") is True
        try:
            result = forge.set_rung(request.user, presc, target, confirm_unready=confirm)
        except forge.UnreadyForRung as exc:
            return Response({"unready": True, **exc.payload}, status=status.HTTP_409_CONFLICT)
        except forge.SetsAlreadyLogged as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        posthog.capture(
            "forge_rung_set",
            properties={"pattern_key": presc.exercise.pattern.key, "direction": result["direction"],
                        "source": "web"},
        )
        return Response(
            {
                "exercise": ExerciseSerializer(result["exercise"]).data,
                "applied_to": result["applied_to"],
            }
        )


class SetLoadView(SetRungView):
    """``POST /prescription/<presc_uuid>/set-load/ {"load": 22.5}`` (#109).

    - 200 ``{"load", "snapped_from", "applied_to"}`` — every live prescription of
      that exercise, snapped DOWN to a buildable load (``snapped_from`` = the
      asked value when it moved).
    - 400 — difficulty-mode movement, load ≤ 0, a band level not owned.
    - 409 ``{"detail"}`` — the movement already has logged sets today.
    """

    http_method_names = ["post", "options"]  # shares only _prescription with SetRungView

    def post(self, request, presc_uuid):
        presc = self._prescription(request, presc_uuid)
        try:
            result = forge.set_load(request.user, presc, request.data.get("load"))
        except forge.SetsAlreadyLogged as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        posthog.capture(
            "forge_load_set",
            properties={"pattern_key": presc.exercise.pattern.key, "direction": result["direction"],
                        "source": "web"},
        )
        return Response({k: result[k] for k in ("load", "snapped_from", "applied_to")})


class ProgramView(ForgeApiMixin, APIView):
    """GET the user's active program; ``PATCH {"weekly_volume_target": 16}``
    (4–40, #109) sets the weekly volume and regenerates every pattern's sets."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        # Prescriptions are reconciled when equipment changes, so this is a plain
        # read. Anything still unperformable had no stand-in to swap in — hide it
        # rather than show a movement the user cannot do.
        program = forge.active_program(request.user)
        if not program:
            return Response({"detail": _("No active program.")}, status=404)
        return Response(ProgramSerializer(program).data)

    def patch(self, request):
        program = forge.active_program(request.user)
        if not program:
            return Response({"detail": _("No active program.")}, status=404)
        if "weekly_volume_target" not in request.data:
            return Response({"detail": _("Send weekly_volume_target.")}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = forge.set_weekly_volume(request.user, program, request.data["weekly_volume_target"])
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        posthog.capture(
            "forge_volume_set",
            properties={"pattern_key": None, "direction": result["direction"], "source": "web"},
        )
        return Response(ProgramSerializer(forge.active_program(request.user)).data)


# Body of the 409 that gates Today (and session creation) while an unfinished
# past session awaits a Save/Delete. The client fetches it from
# ``GET sessions/unfinished/``. ``code`` is machine-readable and stays English;
# ``detail`` is translated per request (``unfinished_gate()``).
UNFINISHED_GATE = {
    "detail": gettext_noop("Finish or delete your last unfinished session first."),
    "code": "unfinished_session",
}


def unfinished_gate():
    return {**UNFINISHED_GATE, "detail": _(UNFINISHED_GATE["detail"])}


class TodayView(ForgeApiMixin, APIView):
    """GET → the plan for today. **Writes nothing.**

    There is one day, so there is no ``?day=`` to choose. The response is a
    computed plan (``persisted: false``, synthetic set ids) — the session is only
    written when the user logs their first real value, via ``POST /sessions/``.
    The one exception is resuming: an unfinished session already scheduled for
    today is returned as-is, so logged values and swapped-in exercises survive a
    reload.

    The payload also carries the retest nudge (``retest_due``, ``last_trial_at``,
    ``days_since_last_trial``) so the Today panel can render its banner without a
    second round-trip.
    """

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        day = forge.active_day(request.user)
        if day is None:
            return Response({"detail": _("No active program.")}, status=404)
        # A past session with logged values must be saved or deleted first, or
        # today's plan would be built from prescriptions it never progressed.
        if forge.has_unfinished_session(request.user):
            return Response(unfinished_gate(), status=status.HTTP_409_CONFLICT)
        existing = forge.resumable_session(request.user)
        if existing is not None:
            payload = WorkoutSessionSerializer(forge.session_for_display(existing)).data
        else:
            planned = forge.build_today_plan(request.user, day)
            payload = {
                "uuid": None,
                "day": str(day.uuid),
                "day_name": day_name(day.name),
                "scheduled_for": forge.local_today(request.user).isoformat(),
                "performed_at": None,
                "status": WorkoutSession.Status.PLANNED,
                "persisted": False,
                "set_logs": PlannedSetLogSerializer(planned, many=True).data,
            }
        payload.update(forge.retest_status(request.user))
        payload["notices"] = forge.today_notices(request.user)
        return Response(payload)


class SwapCandidatesView(ForgeApiMixin, APIView):
    """GET → every progression chain the user could swap onto, least-trained
    first.

    One entry per chain, each with its pattern, the rung the user is currently at
    on it, that rung's own targets, and its completed-session event count. The
    client drops the chains already on screen — including ones an earlier swap
    put there — and offers the rest.
    """

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        return Response(forge.swap_candidates(request.user))


class RetestReminderDismissView(ForgeApiMixin, APIView):
    """POST → suppress the retest nudge for a few days (it returns if still due)."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def post(self, request):
        forge.dismiss_retest_prompt(request.user)
        posthog.capture("retest_reminder_dismissed")
        return Response(forge.retest_status(request.user))


class AssessmentHistoryView(ForgeApiMixin, APIView):
    """GET → per-pattern Trial series for the Evolution charts.

    One entry per movement pattern, each with its points oldest-first: the raw
    result, the ladder-normalised score, and the verdict against the previous
    Trial.
    """

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        return Response(forge.trial_history(request.user))


class NormsView(ForgeApiMixin, APIView):
    """Decile reference cutoffs for an exercise, for the Progress chart's peer
    lines. GET ?exercise=<name>."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        name = request.query_params.get("exercise", "")
        return Response(forge.peer_decile_cutoffs(request.user, name))


class ProgressView(ForgeApiMixin, APIView):
    """Time-series of completed sessions for the progression chart. Returns one
    point per completed session; the client groups by day/week/month."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def get(self, request):
        # Optional ?exercise=<name> narrows every point to that one movement;
        # optional ?muscle=<key> narrows to sets that train that muscle;
        # omitted = totals across all exercises (the default view).
        return Response(forge.progress_points(
            request.user,
            exercise=request.query_params.get("exercise") or None,
            muscle=request.query_params.get("muscle") or None,
        ))


def _exercise_or_400(value):
    """Resolve a client-supplied exercise uuid, or ``None`` if it is not a uuid.

    ``get_object_or_404`` raises on a malformed uuid rather than 404ing, so the
    parse is done here and a bad value becomes a 400 instead of a 500.
    """
    try:
        return Exercise.objects.filter(uuid=uuid_module.UUID(str(value))).first()
    except (TypeError, ValueError, AttributeError):
        return None


class SessionViewSet(ForgeApiMixin, mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet):
    """Saved sessions.

    - ``POST /sessions/`` — persist the on-screen plan (first logged value)
    - ``POST /sessions/{uuid}/sets/`` — record values, session stays ``planned``
    - ``POST /sessions/{uuid}/swap/`` — replace an untouched movement
    - ``POST /sessions/{uuid}/log/`` — finalize: progression + ``completed``
    - ``GET /sessions/unfinished/`` — the newest unfinished past session (or null)
    - ``DELETE /sessions/{uuid}/`` — discard an unfinished session (409 if completed)
    """

    permission_classes = [IsAuthenticated, AdultOnly]
    serializer_class = WorkoutSessionSerializer
    lookup_field = "uuid"

    def get_queryset(self):
        return WorkoutSession.objects.filter(user=self.request.user).select_related(
            "program_day", "user"
        ).prefetch_related(*forge.SESSION_DISPLAY_PREFETCH)

    def create(self, request):
        """Write the plan the user is looking at, on their first logged value.

        Body: ``{"sets": [{"exercise", "prescription"|null, "set_index", ...}]}``
        — the on-screen snapshot. Targets are recomputed server-side; the client
        only chooses which movements are on the day. Returns the real session, so
        the client can re-key its inputs onto the persisted set uuids.
        """
        day = forge.active_day(request.user)
        if day is None:
            return Response({"detail": _("No active program.")}, status=404)
        if forge.has_unfinished_session(request.user):
            return Response(unfinished_gate(), status=status.HTTP_409_CONFLICT)
        # Two inputs racing the first keystroke must not create two sessions.
        # The DB holds one planned session per day (#56), so a race that slips
        # past this check gets the winner's session back, with 200.
        existing = forge.resumable_session(request.user)
        if existing is not None:
            return Response(WorkoutSessionSerializer(forge.session_for_display(existing)).data, status=200)
        try:
            session, created = forge.get_or_create_session_from_plan(
                request.user, day, request.data.get("sets", [])
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if created:
            posthog.capture("workout_session_started")
        return Response(
            WorkoutSessionSerializer(forge.session_for_display(session)).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"])
    def unfinished(self, request):
        """``{"session": <session>|null}`` — a past day with logged values that
        was never saved. Older ones are closed as ``skipped`` on the way."""
        session = forge.unfinished_session(request.user)
        return Response(
            {"session": WorkoutSessionSerializer(forge.session_for_display(session)).data if session else None}
        )

    def destroy(self, request, uuid=None):
        """Discard a session that was never completed, with its SetLogs.
        Prescriptions are not touched."""
        try:
            forge.discard_session(self.get_object())
        except forge.SessionAlreadyCompleted as exc:
            return Response({"detail": str(exc)}, status=409)
        posthog.capture("workout_session_discarded")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def sets(self, request, uuid=None):
        """Record values without finishing the session — the incremental save
        behind every edit after the first. Progression does not run here."""
        session = self.get_object()
        if session.status == WorkoutSession.Status.COMPLETED:
            return Response({"detail": _("Session already finalized.")}, status=409)
        sets, errors = _set_values_or_errors(session, request.data)
        if errors:
            return _invalid(errors)
        forge.save_session_values(session, sets)
        # Not refresh_from_db(): it drops the prefetch and costs a query per set (#129).
        return Response(WorkoutSessionSerializer(forge.session_for_display(session)).data)

    @action(detail=True, methods=["post"])
    def swap(self, request, uuid=None):
        """Replace an untouched movement with one from another chain.

        Body: ``{"from_exercise": "<uuid>", "to_exercise": "<uuid>"}``. 409 when
        the movement being replaced already has logged sets.
        """
        session = self.get_object()
        if session.status == WorkoutSession.Status.COMPLETED:
            return Response({"detail": _("Session already finalized.")}, status=409)
        from_exercise = _exercise_or_400(request.data.get("from_exercise"))
        to_exercise = _exercise_or_400(request.data.get("to_exercise"))
        if from_exercise is None or to_exercise is None:
            return Response(
                {"detail": _("from_exercise and to_exercise must be exercise uuids.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            forge.swap_session_exercise(session, from_exercise, to_exercise)
        except forge.SetsAlreadyLogged as exc:
            return Response({"detail": str(exc)}, status=409)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        posthog.capture("workout_exercise_swapped")
        return Response(WorkoutSessionSerializer(forge.session_for_display(session)).data)

    @action(detail=True, methods=["post"])
    def log(self, request, uuid=None):
        session = self.get_object()
        set_results, errors = _set_values_or_errors(session, request.data)
        if errors:
            return _invalid(errors)
        deltas = forge.apply_session_log(session, set_results)
        posthog.capture(
            "workout_session_completed",
            properties={"logged_set_count": len(set_results)},
        )
        # Peer "flames" score for each AMRAP test set performed this session.
        peer = forge.session_peer_scores(request.user, session)
        return Response(
            {
                "session": WorkoutSessionSerializer(forge.session_for_display(session)).data,
                "progression": deltas,
                "peer": peer,
            }
        )

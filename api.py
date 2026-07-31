"""The Forge DRF API. All endpoints require auth and are scoped to request.user."""

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    Equipment,
    Exercise,
    MovementPattern,
    Muscle,
    PrescribedExercise,
    Program,
    ProgramDay,
    WorkoutSession,
)
from the_cauldron.serializers import (
    AssessmentSessionSerializer,
    ExerciseSerializer,
    MovementPatternSerializer,
    ProgramSerializer,
    UserEquipmentProfileSerializer,
    WorkoutSessionSerializer,
)
from the_cauldron.services import forge, progression


class PatternViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MovementPatternSerializer
    queryset = MovementPattern.objects.all()
    lookup_field = "uuid"


class ExerciseViewSet(viewsets.ReadOnlyModelViewSet):
    """Catalog of exercises, optionally filtered to the user's owned equipment
    via ?equipment=mine."""

    permission_classes = [IsAuthenticated]
    serializer_class = ExerciseSerializer
    lookup_field = "uuid"

    def get_queryset(self):
        qs = Exercise.objects.select_related("pattern").prefetch_related(
            "required_equipment", "muscles"
        )
        if self.request.query_params.get("equipment") == "mine":
            profile = forge.get_or_create_equipment_profile(self.request.user)
            owned = set(profile.equipment.values_list("key", flat=True)) | {"bodyweight"}
            keep = [
                e.pk for e in qs
                if (set(e.required_equipment.values_list("key", flat=True)) or {"bodyweight"}) <= owned
            ]
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
        return Response({"unblocked": str(exercise.uuid)})


class CatalogView(APIView):
    """Full exercise catalog grouped by equipment, annotated with the user's
    blocks and (for blocked moves) the substitute that would be used.

    Drives the "Exercises" tab: browse every move, block/unblock, see the
    stand-in. Each exercise is filed under its primary required equipment
    (bodyweight if it needs none); ``owned`` flags groups the user can train.
    """

    permission_classes = [IsAuthenticated]

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
                "owned": e.key in owned,
                "exercises": [],
            }
            for e in equipment
        }

        exercises = (
            Exercise.objects.select_related("pattern")
            .prefetch_related("required_equipment", "muscles")
            .order_by("pattern__name", "difficulty_rank")
        )
        # Cache substitutes only for blocked exercises (small set).
        for ex in exercises:
            req = list(ex.required_equipment.all())
            req_keys = [e.key for e in req]
            # Primary group: first non-bodyweight requirement, else bodyweight.
            primary = next((k for k in req_keys if k != "bodyweight"), "bodyweight")
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
                    "pattern_key": ex.pattern.key,
                    "pattern_name": ex.pattern.name,
                    "difficulty_rank": ex.difficulty_rank,
                    "required_equipment": req_keys,
                    "is_timed": ex.is_timed,
                    "muscles": [
                        {"key": m.key, "name": m.name} for m in ex.muscles.all()
                    ],
                    "eligible": (set(req_keys) or {"bodyweight"}) <= owned,
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


class EquipmentProfileView(APIView):
    """GET/PUT the current user's equipment profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = forge.get_or_create_equipment_profile(request.user)
        return Response(UserEquipmentProfileSerializer(profile).data)

    def put(self, request):
        profile = forge.get_or_create_equipment_profile(request.user)
        serializer = UserEquipmentProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if profile.configured_at is None:
            profile.configured_at = timezone.now()
            profile.save(update_fields=["configured_at", "updated_at"])
        return Response(serializer.data)


class AssessmentView(APIView):
    """POST AMRAP results → place the user and generate a program.

    Body: {"split": "full_body_3x", "results": [
        {"pattern_key": "...", "tested_exercise": "<uuid>", "reps_or_seconds": 8}, ...]}
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        active = AssessmentSession.objects.filter(user=request.user, is_active=True).first()
        if not active:
            return Response({"detail": "No active assessment."}, status=404)
        return Response(AssessmentSessionSerializer(active).data)

    def post(self, request):
        profile = forge.get_or_create_equipment_profile(request.user)
        # Reuse an open (resultless) active assessment, else open one.
        session = AssessmentSession.objects.filter(
            user=request.user, is_active=True, completed_at__isnull=True
        ).first()
        if session is None:
            session = forge.retake_assessment(request.user)

        results = request.data.get("results", [])
        if not results:
            return Response({"detail": "results required."}, status=status.HTTP_400_BAD_REQUEST)

        blocked = forge.blocked_exercise_ids(request.user)
        peer = []
        for item in results:
            pattern = get_object_or_404(MovementPattern, key=item["pattern_key"])
            tested = get_object_or_404(Exercise, uuid=item["tested_exercise"])
            # Per-leg results for unilateral moves: place from the weaker side.
            left = item.get("left_reps")
            right = item.get("right_reps")
            left = int(left) if left not in (None, "") else None
            right = int(right) if right not in (None, "") else None
            if left is not None and right is not None:
                score = min(left, right)
            else:
                score = int(item["reps_or_seconds"])
            ladder = forge.eligible_exercises(pattern, profile, exclude_ids=blocked)
            placed = progression.place_from_assessment(ladder, score)
            session.results.update_or_create(
                pattern=pattern,
                defaults={
                    "tested_exercise": tested,
                    "reps_or_seconds": score,
                    "left_reps": left,
                    "right_reps": right,
                    "placed_exercise": placed,
                },
            )
            peer.append(
                {
                    "pattern_key": pattern.key,
                    "exercise": tested.name,
                    "value": score,
                    "score": forge.peer_score(request.user, tested.name, score),
                }
            )

        session.completed_at = timezone.now()
        session.save(update_fields=["completed_at"])

        split = request.data.get("split", Program.Split.FULL_BODY_3X)
        program = forge.generate_program(request.user, session, split=split)
        return Response(
            {
                "assessment": AssessmentSessionSerializer(session).data,
                "program": ProgramSerializer(program).data,
                "peer": peer,
            },
            status=status.HTTP_201_CREATED,
        )


class RetakeAssessmentView(APIView):
    """POST → deactivate current assessment/program, open a fresh assessment."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        session = forge.retake_assessment(request.user)
        return Response(AssessmentSessionSerializer(session).data, status=201)


class ProgressionActionView(APIView):
    """Accept (climb) or deny (hold) a parked difficulty unlock for one
    prescription. POST /progression/<presc_uuid>/<accept|deny>/."""

    permission_classes = [IsAuthenticated]

    def post(self, request, presc_uuid, action):
        presc = get_object_or_404(
            PrescribedExercise,
            uuid=presc_uuid,
            day__program__user=request.user,
            day__program__is_active=True,
        )
        if action == "accept":
            new = forge.accept_progression(request.user, presc)
            return Response(
                {"accepted": True, "exercise": ExerciseSerializer(new).data if new else None}
            )
        if action == "deny":
            forge.deny_progression(request.user, presc)
            return Response({"denied": True})
        return Response({"detail": "unknown action"}, status=status.HTTP_400_BAD_REQUEST)


class ProgramView(APIView):
    """GET the user's active program."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Prescriptions are reconciled when equipment changes, so this is a plain
        # read. Anything still unperformable had no stand-in to swap in — hide it
        # rather than show a movement the user cannot do.
        hidden = forge.unperformable_prescription_ids(request.user)
        program = (
            Program.objects.filter(user=request.user, is_active=True)
            .prefetch_related(
                Prefetch(
                    "days__prescriptions",
                    queryset=PrescribedExercise.objects.exclude(
                        pk__in=hidden
                    ).select_related("exercise", "pattern", "pending_progression"),
                )
            )
            .first()
        )
        if not program:
            return Response({"detail": "No active program."}, status=404)
        return Response(ProgramSerializer(program).data)


class TodayView(APIView):
    """GET ?day=<index> → open a WorkoutSession (snapshotting expected values)
    for that day of the active program and return it."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        program = Program.objects.filter(user=request.user, is_active=True).first()
        if not program:
            return Response({"detail": "No active program."}, status=404)
        day_index = int(request.query_params.get("day", 0))
        day = get_object_or_404(ProgramDay, program=program, day_index=day_index)
        session = forge.start_session(request.user, day)
        return Response(WorkoutSessionSerializer(session).data, status=201)


class NormsView(APIView):
    """Decile reference cutoffs for an exercise, for the Progress chart's peer
    lines. GET ?exercise=<name>."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        name = request.query_params.get("exercise", "")
        return Response(forge.peer_decile_cutoffs(request.user, name))


class ProgressView(APIView):
    """Time-series of completed sessions for the progression chart. Returns one
    point per completed session; the client groups by day/week/month."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Optional ?exercise=<name> narrows every point to that one movement;
        # optional ?muscle=<key> narrows to sets that train that muscle;
        # omitted = totals across all exercises (the default view).
        only = request.query_params.get("exercise") or None
        muscle = request.query_params.get("muscle") or None
        sessions = (
            WorkoutSession.objects.filter(user=request.user, status="completed")
            .prefetch_related("set_logs__exercise__muscles")
            .order_by("performed_at", "created_at")
        )
        points = []
        exercises = set()
        for s in sessions:
            total_reps = 0
            total_volume = 0.0
            top_set = 0
            counted = False
            for sl in s.set_logs.all():
                # Grip variants of a bar pull-up collapse to one rung label so the
                # daily grip choice stays invisible (a chin-up session still counts
                # toward the "Pull-up" series instead of a separate line).
                label = sl.exercise.rung_label
                exercises.add(label)
                if only and label != only:
                    continue
                if muscle and not any(m.key == muscle for m in sl.exercise.muscles.all()):
                    continue
                if sl.actual_reps is None:
                    continue
                counted = True
                total_reps += sl.actual_reps
                total_volume += sl.actual_reps * (sl.actual_load or 1)
                if sl.is_amrap:
                    top_set = max(top_set, sl.actual_reps)
            if (only or muscle) and not counted:
                continue  # nothing matching the filter was trained that session
            when = s.performed_at or s.created_at
            points.append(
                {
                    "date": when.isoformat(),
                    "total_reps": total_reps,
                    "total_volume": round(total_volume, 1),
                    "best_amrap": top_set,
                }
            )

        # Left/right asymmetry captured at each Trial (unilateral moves).
        asym = (
            AssessmentResult.objects.filter(
                session__user=request.user,
                left_reps__isnull=False,
                right_reps__isnull=False,
            )
            .select_related("session", "tested_exercise")
            .order_by("session__created_at")
        )
        asymmetry = [
            {
                "date": r.session.created_at.isoformat(),
                "exercise": r.tested_exercise.name,
                "left": r.left_reps,
                "right": r.right_reps,
            }
            for r in asym
        ]
        muscles = [
            {"key": m.key, "name": m.name}
            for m in Muscle.objects.all().order_by("name")
        ]
        return Response(
            {
                "points": points,
                "exercises": sorted(exercises),
                "muscles": muscles,
                "asymmetry": asymmetry,
            }
        )


class SessionViewSet(viewsets.ReadOnlyModelViewSet):
    """List/retrieve saved sessions; POST /sessions/{uuid}/log/ to record results."""

    permission_classes = [IsAuthenticated]
    serializer_class = WorkoutSessionSerializer
    lookup_field = "uuid"

    def get_queryset(self):
        return WorkoutSession.objects.filter(user=self.request.user).prefetch_related(
            "set_logs__exercise__pattern", "set_logs__exercise__muscles"
        )

    @action(detail=True, methods=["post"])
    def log(self, request, uuid=None):
        session = self.get_object()
        set_results = request.data.get("sets", {})
        deltas = forge.apply_session_log(session, set_results)
        # Peer "flames" score for each AMRAP test set performed this session.
        peer = [
            {
                "exercise": a.exercise.name,
                "value": a.actual_reps,
                "score": forge.peer_score(request.user, a.exercise.name, a.actual_reps),
            }
            for a in session.set_logs.filter(is_amrap=True).select_related("exercise")
            if a.actual_reps is not None
        ]
        return Response(
            {
                "session": WorkoutSessionSerializer(session).data,
                "progression": deltas,
                "peer": peer,
            }
        )

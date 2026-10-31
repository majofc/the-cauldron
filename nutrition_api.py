"""Daily nutrition API (#80–#84) — ``/cauldron/api/…``.

- ``legal/`` (+ ``accept/``, ``withdraw/``) — current document versions and
  what the user accepted (#84). Open to any signed-in user.
- ``me/`` — the user's timezone (a day runs midnight to midnight there).
- ``body/`` — birth year, sex, weight, activity level (#105). Birth year and sex
  (also the Forge's peer score) need only a sign-in; weight and activity level
  are sensitive data and need the consent.
- ``rolling/breakdown/`` + ``rolling/fill/`` — the goal cauldron (#107): what a
  card's number is made of, and filling the values it doesn't know.
- ``goals/`` (+ ``history/``), ``intake/`` (+ ``day/``), ``adherence/``,
  ``plan/`` (+ ``day/``, ``{uuid}/eat/``) — need the current terms and privacy
  notice **and** the sensitive-data consent; otherwise 403
  ``{"code": "consent_required"}``.

Everything is the user's own: anyone else's entry or plan item is a 404.
PostHog only ever gets counts and enums from here — never a value, a food or a
goal (CLAUDE.md "PostHog never receives PII").
"""

import posthog
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from the_cauldron.models import IntakeEntry, PlannedMeal
from the_cauldron import nutrition_payloads as payloads
from the_cauldron import nutrition_writes as writes
from the_cauldron.nutrition_serializers import (
    BodySerializer,
    GoalSetSerializer,
    IntakeEntrySerializer,
    IntakeInputSerializer,
    LegalAcceptSerializer,
    LegalWithdrawSerializer,
    MeSerializer,
    PlanEatSerializer,
    PlannedMealInputSerializer,
    PlannedMealSerializer,
)
from the_cauldron.permissions import AdultOnly
from the_cauldron.services import adherence, age_gate, breakdown, grimoire, legal, nutrition, rolling
from the_cauldron.services.nutrition import NutritionInputError


class NutritionConsent(BasePermission):
    """Current terms + privacy notice and a live sensitive-data consent."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if legal.pending_documents(user) or not legal.has_sensitive_consent(user):
            raise PermissionDenied({
                "code": "consent_required",
                "detail": _("Accept the sensitive-data consent to use goals and the meal log."),
            })
        return True


class UserTimezoneMixin:
    """Dates and times in and out of these views are in the user's timezone:
    a naive ``eaten_at`` is read as their wall-clock time, and every datetime
    is written back in it."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.tz = nutrition.user_tz(request.user)
        timezone.activate(self.tz)

    def finalize_response(self, request, response, *args, **kwargs):
        timezone.deactivate()
        return super().finalize_response(request, response, *args, **kwargs)

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "tz": getattr(self, "tz", None)}


def _bad(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _day(request):
    return nutrition.parse_day(request.query_params.get("date"), request.user)


# ── Legal (#84) ─────────────────────────────────────────────────────────────


class LegalView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(legal.status(request.user))


class LegalAcceptView(APIView):
    """``{"documents": ["terms", "privacy", "sensitive_consent"]}`` — records an
    acceptance of each document's current version."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        req = LegalAcceptSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        documents = list(dict.fromkeys(req.validated_data["documents"]))
        created = legal.accept(request.user, documents)
        for row in created:
            posthog.capture("legal_accepted", properties={"document": row.document, "version": row.version})
        return Response(legal.status(request.user))


class LegalWithdrawView(APIView):
    """Withdraw the sensitive-data consent; ``delete_data`` also deletes the
    goals, meal log and plan."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        req = LegalWithdrawSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        delete_data = req.validated_data["delete_data"]
        legal.withdraw_consent(request.user, delete_data=delete_data)
        posthog.capture("sensitive_consent_withdrawn", properties={"deleted_data": delete_data})
        return Response(legal.status(request.user))


# ── Settings ────────────────────────────────────────────────────────────────


class MeView(APIView):
    """``GET`` / ``PATCH`` ``{"timezone": "America/Mexico_City"}``."""

    permission_classes = [IsAuthenticated, AdultOnly]

    def _payload(self, user):
        return {"timezone": nutrition.get_profile(user).timezone, "today": nutrition.today_for(user)}

    def get(self, request):
        return Response(self._payload(request.user))

    def patch(self, request):
        req = MeSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        profile = nutrition.get_profile(request.user)
        profile.timezone = req.validated_data["timezone"]
        profile.save(update_fields=["timezone", "updated_at"])
        return Response(self._payload(request.user))


class BodyView(APIView):
    """``GET`` / ``PATCH`` ``{birth_year, sex, weight_kg, activity_level}`` (#105).
    Adding a weight or activity level needs the sensitive-data consent (403
    ``consent_required`` otherwise); clearing them, birth year and sex don't."""

    permission_classes = [IsAuthenticated, AdultOnly]
    SENSITIVE = ("weight_kg", "activity_level")

    def _payload(self, user):
        profile = nutrition.get_profile(user)
        return {**BodySerializer(profile).data, "age": profile.age(nutrition.today_for(user)),
                "has_consent": legal.has_sensitive_consent(user)}

    def get(self, request):
        return Response(self._payload(request.user))

    def patch(self, request):
        # Adding health data needs the consent; clearing it (null / "") never does.
        adding = any(request.data.get(f) not in (None, "") for f in self.SENSITIVE if f in request.data)
        if adding and not legal.has_sensitive_consent(request.user):
            return Response({"code": "consent_required", "detail": _("Give your consent first.")},
                            status=status.HTTP_403_FORBIDDEN)
        # The Cauldron is for adults (#115): the birth year that proves it stays.
        if "birth_year" in request.data and request.data.get("birth_year") in (None, ""):
            return Response({"birth_year": [_("Your birth year is required.")]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = BodySerializer(nutrition.get_profile(request.user), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        filled = [profile.birth_year, profile.sex != profile.Sex.UNDISCLOSED, profile.weight_kg,
                  profile.activity_level]
        posthog.capture("profile_body_data_saved", properties={"fields_count": sum(bool(f) for f in filled)})
        return Response(self._payload(request.user))


class AgeView(APIView):
    """``POST {"birth_year"}`` — the one-time birth year for an account that has
    none yet (#115: Google sign-ups and accounts from before the age rule).
    Never changes one already given: that goes through ``body/``, which a
    blocked account can't reach. Returns ``{"code"}``: null when they're in."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Terms and privacy (which say why the year is asked) come first.
        if legal.pending_documents(request.user):
            raise PermissionDenied({"code": "consent_required",
                                    "detail": _("Accept the terms and the privacy notice first.")})
        profile = nutrition.get_profile(request.user)
        if profile.birth_year:
            return Response({"code": "conflict", "detail": _("Your birth year is already set.")},
                            status=status.HTTP_409_CONFLICT)
        serializer = BodySerializer(profile, data={"birth_year": request.data.get("birth_year")}, partial=True)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("birth_year") is None:
            return Response({"birth_year": [_("Your birth year is required.")]}, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response({"code": age_gate.blocked(request.user)})


# ── Goals (#80) ─────────────────────────────────────────────────────────────


class GoalsView(APIView):
    """``GET`` the active goal set plus the recommended one (``recommended``,
    #105, read-only); ``PUT {"goals": [{nutrient, kind,
    min_value, max_value}]}`` replaces it (a nutrient left out, or with kind
    ``none``, has no goal). Changes are versioned, never overwritten."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]

    def get(self, request):
        return Response(payloads.goals_payload(request.user))

    def put(self, request):
        req = GoalSetSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        try:
            specs = nutrition.clean_goal_set(req.validated_data["goals"])
        except NutritionInputError as exc:
            return _bad(exc)
        changed = nutrition.save_goal_set(request.user, specs, nutrition.today_for(request.user))
        posthog.capture("nutrition_goals_saved", properties={"goal_count": len(specs), "changed_count": changed})
        return Response(payloads.goals_payload(request.user))


class GoalHistoryView(APIView):
    """Every goal version, newest first per nutrient. Rows replaced on the day
    they were set (never active on any day) are left out."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]

    def get(self, request):
        return Response({"history": payloads.goal_history(request.user)})


# ── Meal log (#81) ──────────────────────────────────────────────────────────


class IntakeViewSet(UserTimezoneMixin, viewsets.ModelViewSet):
    """The user's meal log. ``?date=YYYY-MM-DD`` (their timezone) filters the
    list; ``day/?date=`` adds totals and goal status."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]
    serializer_class = IntakeEntrySerializer
    lookup_field = "uuid"
    pagination_class = None
    LIST_LIMIT = 500

    def get_queryset(self):
        return IntakeEntry.objects.filter(user=self.request.user).select_related("planned_meal")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        if request.query_params.get("date"):
            try:
                qs = qs.filter(local_date=_day(request))
            except NutritionInputError as exc:
                return _bad(exc)
        return Response(self.get_serializer(qs.order_by("-eaten_at")[: self.LIST_LIMIT], many=True).data)

    def _write(self, request, instance=None, partial=False):
        req = IntakeInputSerializer(data=request.data, partial=partial)
        req.is_valid(raise_exception=True)
        try:
            entry = writes.apply_entry(request.user, req.validated_data, instance)
        except NutritionInputError as exc:
            return None, _bad(exc)
        return entry, None

    def create(self, request, *args, **kwargs):
        entry, error = self._write(request)
        if error:
            return error
        posthog.capture("intake_logged", properties={"source": entry.source})
        return Response(self.get_serializer(entry).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        entry, error = self._write(request, self.get_object(), partial=kwargs.get("partial", False))
        if error:
            return error
        posthog.capture("intake_updated", properties={"source": entry.source})
        return Response(self.get_serializer(entry).data)

    def perform_destroy(self, instance):
        instance.delete()
        posthog.capture("intake_deleted")

    @action(detail=False, methods=["get"])
    def day(self, request):
        try:
            day = _day(request)
        except NutritionInputError as exc:
            return _bad(exc)
        return Response(payloads.day_payload(request.user, day, self.tz, request))


# ── Adherence (#82) ─────────────────────────────────────────────────────────


class AdherenceView(UserTimezoneMixin, APIView):
    """``?days=7|30`` — met / missed / uncertain / not logged per nutrient,
    % met, streaks and a calendar strip."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 7))
        except ValueError:
            days = 0
        if days not in adherence.WINDOWS:
            return Response({"detail": _("Use 7 or 30 days.")}, status=status.HTTP_400_BAD_REQUEST)
        return Response(adherence.adherence(request.user, days))


class RollingView(UserTimezoneMixin, APIView):
    """``?span=24h|7d&days=7|30|90`` — the moving window up to now and at every
    hourly tick of the history: values of every nutrient, goal status, goal
    bands and time in range (``services.rolling``)."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]

    def get(self, request):
        span = request.query_params.get("span", "24h")
        try:
            days = int(request.query_params.get("days", 7))
        except ValueError:
            days = 0
        if span not in rolling.SPANS or days not in rolling.HISTORY_DAYS:
            return Response({"detail": _("Use span 24h or 7d and 7, 30 or 90 days.")},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(rolling.rolling(request.user, span, days))


class BreakdownView(UserTimezoneMixin, APIView):
    """``?span=24h|7d&end=<tick iso>&nutrient=<key>`` — the goal cauldron
    (``services.breakdown``): top spells/reagents, "others" and the fog."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]

    def get(self, request):
        try:
            span, end, nutrient = breakdown.window_args(request.user, request.query_params)
        except NutritionInputError as exc:
            return _bad(exc)
        posthog.capture("goal_breakdown_opened", properties={"span": span})
        return Response(breakdown.breakdown(request.user, span, end, nutrient))


class FillView(UserTimezoneMixin, APIView):
    """``POST {nutrient, value, reagent | entry}`` — fill one gap (#107).
    ``reagent``: per 100 g, saved on the user's own reagent or on their private
    copy of a catalog one; ``entry``: a quick entry's own total. Marked estimated;
    recent entries are gap-filled (``services.breakdown``)."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]

    def post(self, request):
        data = request.data
        if not isinstance(data, dict):
            return _bad(NutritionInputError(_("Send an object.")))
        nutrient = data.get("nutrient")
        try:
            if nutrient not in nutrition.FIELDS:
                raise NutritionInputError(_("Unknown nutrient."))
            value = breakdown.clean_value(data.get("value"))
            if data.get("entry"):
                entry = IntakeEntry.objects.filter(user=request.user, uuid=data["entry"]).first()
                if entry is None:
                    return Response({"detail": _("Not found.")}, status=status.HTTP_404_NOT_FOUND)
                breakdown.fill_quick(request.user, entry, nutrient, value)
                source, refreshed, reagent = "quick", 1, None
            elif data.get("reagent"):
                target = (grimoire.visible_reagents(request.user).select_related("nutrients")
                          .filter(uuid=data["reagent"]).first())
                if target is None:
                    return Response({"detail": _("Not found.")}, status=status.HTTP_404_NOT_FOUND)
                source, reagent, refreshed = breakdown.fill_reagent(request.user, target, nutrient, value)
            else:
                raise NutritionInputError(_("Choose an ingredient or an entry."))
        except NutritionInputError as exc:
            return _bad(exc)
        except ValidationError:  # a malformed uuid
            return Response({"detail": _("Not found.")}, status=status.HTTP_404_NOT_FOUND)
        posthog.capture("missing_value_filled", properties={"source": source})
        return Response({
            "source": source,
            "reagent": str(reagent.uuid) if reagent else None,
            "entries_refreshed": refreshed,
        })


# ── Ritual planner (#83) ────────────────────────────────────────────────────


class PlanViewSet(UserTimezoneMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                  mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Planned meals. ``?from=&to=`` (at most 62 days; default this week from
    today). ``day/?date=`` adds each meal's totals and the day's projected
    totals vs goals; ``{uuid}/eat/`` turns a planned meal into a log entry."""

    permission_classes = [IsAuthenticated, AdultOnly, NutritionConsent]
    serializer_class = PlannedMealSerializer
    lookup_field = "uuid"
    pagination_class = None

    def get_queryset(self):
        return PlannedMeal.objects.filter(user=self.request.user).select_related("spell")

    def list(self, request, *args, **kwargs):
        try:
            start = nutrition.parse_day(request.query_params.get("from"), request.user)
            end = (
                nutrition.parse_day(request.query_params.get("to"), request.user)
                if request.query_params.get("to") else payloads.default_week(start)
            )
            meals = payloads.plan_range(request.user, start, end, request)
        except NutritionInputError as exc:
            return _bad(exc)
        return Response(meals)

    def create(self, request, *args, **kwargs):
        req = PlannedMealInputSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        try:
            meal = writes.apply_plan(request.user, PlannedMeal(user=request.user, unit="serving"), req.validated_data)
        except NutritionInputError as exc:
            return _bad(exc)
        posthog.capture("plan_meal_added", properties={"slot": meal.slot})
        return Response(self.get_serializer(meal).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        meal = self.get_object()
        if meal.intake_entry_id:
            return Response(
                {"detail": _("This meal is already logged as eaten. Edit the log entry instead.")},
                status=status.HTTP_409_CONFLICT,
            )
        req = PlannedMealInputSerializer(data=request.data, partial=True)
        req.is_valid(raise_exception=True)
        try:
            meal = writes.apply_plan(request.user, meal, req.validated_data)
        except NutritionInputError as exc:
            return _bad(exc)
        return Response(self.get_serializer(meal).data)

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        instance.delete()
        posthog.capture("plan_meal_removed")

    @action(detail=True, methods=["post"])
    def eat(self, request, uuid=None):
        """Log the planned meal as eaten: an ``IntakeEntry`` with the real time
        (default now, or noon of a past/future planned day) and amount (default
        the planned one), linked back to the plan item."""
        target = self.get_object()
        req = PlanEatSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        try:
            meal, entry = writes.eat_plan(request.user, target.pk, req.validated_data)
        except writes.PlanConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except NutritionInputError as exc:
            return _bad(exc)
        posthog.capture("plan_meal_eaten", properties={"slot": meal.slot})
        return Response({
            "meal": self.get_serializer(meal).data,
            "entry": IntakeEntrySerializer(entry, context={"tz": self.tz, "request": request}).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def day(self, request):
        """Planned meals of a day with their live totals, plus the projected
        day: what's eaten + what's still planned, against the goals."""
        try:
            day = _day(request)
        except NutritionInputError as exc:
            return _bad(exc)
        return Response(payloads.plan_day_payload(request.user, day, request))

"""Daily nutrition API (#80–#84) — ``/cauldron/api/…``.

- ``legal/`` (+ ``accept/``, ``withdraw/``) — current document versions and
  what the user accepted (#84). Open to any signed-in user.
- ``me/`` — the user's timezone (a day runs midnight to midnight there).
- ``goals/`` (+ ``history/``), ``intake/`` (+ ``day/``), ``adherence/``,
  ``plan/`` (+ ``day/``, ``{uuid}/eat/``) — need the current terms and privacy
  notice **and** the sensitive-data consent; otherwise 403
  ``{"code": "consent_required"}``.

Everything is the user's own: anyone else's entry or plan item is a 404.
PostHog only ever gets counts and enums from here — never a value, a food or a
goal (CLAUDE.md "PostHog never receives PII").
"""

from datetime import datetime, time, timedelta

import posthog
from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from the_cauldron.models import Casting, IntakeEntry, NutrientGoal, PlannedMeal
from the_cauldron.nutrition_serializers import (
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
from the_cauldron.services import adherence, grimoire, legal, nutrition, rolling
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

    permission_classes = [IsAuthenticated]

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


# ── Goals (#80) ─────────────────────────────────────────────────────────────


class GoalsView(APIView):
    """``GET`` the active goal set; ``PUT {"goals": [{nutrient, kind,
    min_value, max_value}]}`` replaces it (a nutrient left out, or with kind
    ``none``, has no goal). Changes are versioned, never overwritten."""

    permission_classes = [IsAuthenticated, NutritionConsent]

    def _payload(self, user):
        goals = nutrition.current_goals(user)
        return {
            "today": nutrition.today_for(user),
            "goals": [nutrition.goal_payload(goals[n]) for n in nutrition.FIELDS if n in goals],
        }

    def get(self, request):
        return Response(self._payload(request.user))

    def put(self, request):
        req = GoalSetSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        try:
            specs = nutrition.clean_goal_set(req.validated_data["goals"])
        except NutritionInputError as exc:
            return _bad(exc)
        changed = nutrition.save_goal_set(request.user, specs, nutrition.today_for(request.user))
        posthog.capture("nutrition_goals_saved", properties={"goal_count": len(specs), "changed_count": changed})
        return Response(self._payload(request.user))


class GoalHistoryView(APIView):
    """Every goal version, newest first per nutrient. Rows replaced on the day
    they were set (never active on any day) are left out."""

    permission_classes = [IsAuthenticated, NutritionConsent]

    def get(self, request):
        rows = [
            g for g in NutrientGoal.objects.filter(user=request.user)
            if g.valid_to is None or g.valid_to > g.valid_from
        ]
        return Response({"history": [nutrition.goal_payload(g) for g in rows]})


# ── Meal log (#81) ──────────────────────────────────────────────────────────


def apply_entry(user, data, instance=None):
    """Build or update an ``IntakeEntry`` from validated input. A new source
    (another spell or reagent) takes a fresh snapshot; a changed amount
    rescales the stored one."""
    tz = nutrition.user_tz(user)
    entry = instance or IntakeEntry(user=user)
    source = data.get("source") or entry.source
    if not source:
        raise NutritionInputError(_("Choose what you ate: a spell, an ingredient or a quick entry."))
    same_source = instance is not None and instance.source == source
    if instance is None or data.get("eaten_at"):
        # local_date is fixed when the time is set: a later timezone change
        # never moves an entry to another day.
        entry.eaten_at = nutrition.localize(data.get("eaten_at") or timezone.now(), tz)
        entry.local_date = timezone.localtime(entry.eaten_at, tz).date()
    quantity = data.get("quantity", entry.quantity if same_source else None)
    unit = data.get("unit", entry.unit if same_source else None)

    if source == IntakeEntry.Source.SPELL:
        if "spell" in data:
            spell = grimoire.readable_spells(user).filter(uuid=data["spell"]).first()
            if spell is None:
                raise NutritionInputError(_("That spell is private or no longer exists."))
        elif same_source:
            spell = entry.spell
        else:
            raise NutritionInputError(_("Choose a spell."))
        fresh = not same_source or (spell is not None and spell.pk != entry.spell_id)
        unit = unit or "serving"
        quantity = quantity or 1
        if unit not in nutrition.SPELL_UNITS:
            raise NutritionInputError(_("Use grams or servings for a spell."))
        if fresh:
            entry.basis = nutrition.spell_basis_snapshot(spell, user)
            entry.spell = spell
            entry.label = grimoire.localized_text(spell, get_language())[0]
        if "casting" in data:
            entry.casting = (
                Casting.objects.filter(uuid=data["casting"], user=user, spell=entry.spell).first()
                if data["casting"] else None
            )
        elif fresh:
            entry.casting = None
        entry.reagent = None
        entry.grams = nutrition.spell_grams(entry.basis, quantity, unit)
        entry.nutrients, entry.missing = nutrition.totals_from_basis(entry.basis, entry.grams)
    elif source == IntakeEntry.Source.REAGENT:
        if "reagent" in data:
            reagent = (
                grimoire.visible_reagents(user).select_related("nutrients").filter(uuid=data["reagent"]).first()
            )
            if reagent is None:
                raise NutritionInputError(_("That ingredient isn't available."))
        elif same_source:
            reagent = entry.reagent
        else:
            raise NutritionInputError(_("Choose an ingredient."))
        fresh = not same_source or (reagent is not None and reagent.pk != entry.reagent_id)
        unit = unit or "g"
        if quantity is None:
            raise NutritionInputError(_("Enter how much you ate."))
        if unit in nutrition.SPELL_UNITS[1:] or unit == "portion":
            raise NutritionInputError(_("Use a weight, a volume or pieces for an ingredient."))
        if fresh:
            entry.basis = nutrition.reagent_basis_snapshot(reagent)
            entry.reagent = reagent
            entry.label = (get_language() or "").startswith("es") and reagent.name_es or reagent.name
        entry.spell = entry.casting = None
        entry.grams = nutrition.reagent_grams(entry.reagent, quantity, unit)
        entry.nutrients, entry.missing = nutrition.totals_from_basis(entry.basis, entry.grams)
    else:
        label = (data.get("label") or (entry.label if same_source else "")).strip()
        if not label:
            raise NutritionInputError(_("Name what you ate."))
        entry.label = label
        if "values" in data:
            entry.nutrients, entry.missing = nutrition.clean_quick_values(data["values"])
        elif not same_source:
            raise NutritionInputError(_("Enter at least one value, e.g. the calories."))
        quantity, unit = quantity or 1, "portion"
        entry.spell = entry.casting = entry.reagent = None
        entry.grams = None
        entry.basis = {}
    entry.source = source
    entry.quantity = quantity
    entry.unit = unit
    entry.save()
    return entry


def day_payload(user, day, tz, request):
    """One day of the log: entries, totals and each goal's status."""
    today = nutrition.today_for(user)
    entries = list(
        IntakeEntry.objects.filter(user=user, local_date=day).select_related("planned_meal")
    )
    totals = nutrition.entries_totals(entries)
    goals = nutrition.goals_on(nutrition.goal_rows_between(user, day, day), day)
    return {
        "date": day,
        "today": today,
        "logged": bool(entries),
        "entries": IntakeEntrySerializer(entries, many=True, context={"tz": tz, "request": request}).data,
        "totals": nutrition.totals_payload(totals),
        "goals": nutrition.goal_statuses(goals, totals, logged=bool(entries), final=day < today),
    }


class IntakeViewSet(UserTimezoneMixin, viewsets.ModelViewSet):
    """The user's meal log. ``?date=YYYY-MM-DD`` (their timezone) filters the
    list; ``day/?date=`` adds totals and goal status."""

    permission_classes = [IsAuthenticated, NutritionConsent]
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
            entry = apply_entry(request.user, req.validated_data, instance)
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
        return Response(day_payload(request.user, day, self.tz, request))


# ── Adherence (#82) ─────────────────────────────────────────────────────────


class AdherenceView(UserTimezoneMixin, APIView):
    """``?days=7|30`` — met / missed / uncertain / not logged per nutrient,
    % met, streaks and a calendar strip."""

    permission_classes = [IsAuthenticated, NutritionConsent]

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

    permission_classes = [IsAuthenticated, NutritionConsent]

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


# ── Ritual planner (#83) ────────────────────────────────────────────────────


class PlanViewSet(UserTimezoneMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                  mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Planned meals. ``?from=&to=`` (at most 62 days; default this week from
    today). ``day/?date=`` adds each meal's totals and the day's projected
    totals vs goals; ``{uuid}/eat/`` turns a planned meal into a log entry."""

    permission_classes = [IsAuthenticated, NutritionConsent]
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
                if request.query_params.get("to") else start + timedelta(days=6)
            )
        except NutritionInputError as exc:
            return _bad(exc)
        if end < start or (end - start).days >= nutrition.MAX_RANGE_DAYS:
            return Response(
                {"detail": _("Ask for at most %(n)s days.") % {"n": nutrition.MAX_RANGE_DAYS}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order = {slot: i for i, slot in enumerate(PlannedMeal.Slot.values)}
        meals = sorted(
            self.get_queryset().filter(date__gte=start, date__lte=end),
            key=lambda m: (m.date, order.get(m.slot, 9), m.order, m.created_at),
        )
        return Response(self.get_serializer(meals, many=True).data)

    def _apply(self, request, meal, data):
        if "spell" in data:
            spell = grimoire.readable_spells(request.user).filter(uuid=data["spell"]).first()
            if spell is None:
                raise NutritionInputError(_("That spell is private or no longer exists."))
            meal.spell = spell
            meal.spell_title = spell.title
        elif meal.spell_id is None and meal._state.adding:
            raise NutritionInputError(_("Choose a spell."))
        for field in ("date", "slot", "quantity", "unit", "order"):
            if field in data:
                setattr(meal, field, data[field])
        if meal.date is None:
            meal.date = nutrition.today_for(request.user)
        if not meal.slot:
            raise NutritionInputError(_("Choose a meal: breakfast, lunch, dinner or snack."))
        if meal.quantity is None:
            meal.quantity = 1
        meal.save()
        return meal

    def create(self, request, *args, **kwargs):
        req = PlannedMealInputSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        try:
            meal = self._apply(request, PlannedMeal(user=request.user, unit="serving"), req.validated_data)
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
            meal = self._apply(request, meal, req.validated_data)
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
        data = req.validated_data
        today = nutrition.today_for(request.user)
        with transaction.atomic():
            # Locked and re-checked, so a double tap can't log the meal twice.
            meal = PlannedMeal.objects.select_for_update(of=("self",)).select_related("spell").get(pk=target.pk)
            if meal.intake_entry_id:
                return Response({"detail": _("Already marked as eaten.")}, status=status.HTTP_409_CONFLICT)
            if meal.spell is None or not grimoire.can_read(meal.spell, request.user):
                return Response(
                    {"detail": _("That spell is private or no longer exists.")}, status=status.HTTP_409_CONFLICT
                )
            eaten_at = data.get("eaten_at") or (
                timezone.now() if meal.date == today else datetime.combine(meal.date, time(12, 0))
            )
            try:
                entry = apply_entry(request.user, {
                    "source": IntakeEntry.Source.SPELL,
                    "spell": meal.spell.uuid,
                    "eaten_at": eaten_at,
                    "quantity": data.get("quantity", meal.quantity),
                    "unit": data.get("unit", meal.unit),
                })
            except NutritionInputError as exc:
                transaction.set_rollback(True)
                return _bad(exc)
            meal.intake_entry = entry
            meal.save(update_fields=["intake_entry", "updated_at"])
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
        user = request.user
        cache = nutrition.SpellTotalsCache(user)
        meals = []
        for meal in nutrition.planned_on(user, day):
            live = None if meal.intake_entry_id else cache.meal_totals(meal)
            row = self.get_serializer(meal).data
            row["grams"] = round(live[2], 1) if live else None
            row["nutrients"] = live[0] if live else None
            row["missing"] = live[1] if live else None
            row["available"] = bool(live) or bool(meal.intake_entry_id)
            meals.append(row)
        projected = nutrition.consumed_and_planned(user, day, cache)
        goals = nutrition.goals_on(nutrition.goal_rows_between(user, day, day), day)
        return Response({
            "date": day,
            "meals": meals,
            "projected": nutrition.totals_payload(projected),
            "goals": nutrition.goal_statuses(goals, projected, logged=True, final=True),
        })

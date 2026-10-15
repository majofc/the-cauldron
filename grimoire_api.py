"""Grimoire DRF API (#67) — ``/cauldron/api/grimoire/…``.

Visibility rules live in ``services.grimoire``:
- A spell is readable by its owner, or by anyone once public. Anything else is
  a 404, so a private spell's existence never leaks.
- Only the owner may edit, delete, publish or unpublish (403 on a public spell
  someone else owns).
- A reagent is visible to everyone once approved, otherwise to its creator only.
"""

from django.db.models import Case, IntegerField, Value, When
from django.utils.translation import get_language
from django.utils.translation import gettext as _
import posthog
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from the_cauldron.grimoire_serializers import (
    CastingSerializer,
    GrimoirePreferencesSerializer,
    ReagentSerializer,
    SpellPortionSerializer,
    SpellScaleSerializer,
    SpellSerializer,
    TagSerializer,
)
from the_cauldron.models import Casting, GrimoirePreferences, Spell, SpellReagent, Tag, normalize_search
from the_cauldron.services import grimoire, grimoire_nutrition, grimoire_scaling


class TagViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = TagSerializer
    queryset = Tag.objects.all()
    lookup_field = "slug"
    pagination_class = None


class ReagentViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """``?q=`` searches (case/accent-insensitive, close matches first); without
    it, the visible catalog. ``?mine=1`` limits to the user's own reagents.

    POST refuses an exact duplicate of a visible reagent with 409 and the
    matches, unless ``confirm`` is true — the UI shows close matches first.

    PUT/PATCH: only the creator, only before approval (403 otherwise; a
    reagent the user can't see is a 404)."""

    permission_classes = [IsAuthenticated]
    serializer_class = ReagentSerializer
    lookup_field = "uuid"
    pagination_class = None
    LIST_LIMIT = 200

    def get_queryset(self):
        return grimoire.visible_reagents(self.request.user).select_related("nutrients")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        if request.query_params.get("mine") in ("1", "true"):
            qs = qs.filter(created_by=request.user)
        q = request.query_params.get("q", "")
        if q.strip():
            rows = grimoire.search_reagents(request.user, q, queryset=qs)
        else:
            # The user's own reagents first, so the cap never hides them.
            own = Case(
                When(created_by=request.user, then=Value(0)), default=Value(1),
                output_field=IntegerField(),
            )
            rows = qs.order_by(own, "name")[: self.LIST_LIMIT]
        return Response(self.get_serializer(rows, many=True).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        confirm = str(request.data.get("confirm", "")).lower() in ("1", "true")
        if not confirm:
            dupes = grimoire.exact_duplicates(request.user, serializer.validated_data["name"])
            if dupes:
                return Response(
                    {
                        "code": "duplicate",
                        "detail": _("An ingredient with this name already exists."),
                        "matches": ReagentSerializer(dupes, many=True, context={"request": request}).data,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        serializer.save()
        posthog.capture("reagent_created", properties={"confirmed_duplicate": confirm})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if not grimoire.can_edit_reagent(request.user, self.get_object()):
            raise PermissionDenied(_("Only your own ingredients can be edited, until they're approved."))
        return super().update(request, *args, **kwargs)


class SpellViewSet(viewsets.ModelViewSet):
    """CRUD + publish / unpublish / fork.

    Filters: ``?scope=all|mine|coven`` (default ``all``; ``coven`` = other
    users' public spells, ``public`` is accepted as an alias), ``?tag=<slug>``,
    ``?q=`` (English or Spanish title, case/accent-insensitive).
    Order: the user's own spells first, then the coven's; newest first within each."""

    permission_classes = [IsAuthenticated]
    serializer_class = SpellSerializer
    lookup_field = "uuid"
    pagination_class = None
    LIST_LIMIT = 200  # newest first; search/tags narrow the rest

    def _base_queryset(self):
        user = self.request.user
        return grimoire.with_casting_stats(
            grimoire.with_spell_relations(grimoire.readable_spells(user)), user
        )

    def get_queryset(self):
        user = self.request.user
        qs = self._base_queryset()
        if self.action != "list":
            # Filters narrow the list only — a detail/fork/publish lookup must
            # never 404 because a stray ?tag= or ?scope= rode along on the URL.
            return qs
        params = self.request.query_params
        scope = params.get("scope")
        if scope == "mine":
            qs = qs.filter(owner=user)
        elif scope in ("coven", "public"):
            qs = qs.filter(visibility=Spell.Visibility.PUBLIC).exclude(owner=user)
        if params.get("tag"):
            qs = qs.filter(tags__slug=params["tag"])
        q = normalize_search(params.get("q", ""))
        for word in q.split():
            qs = qs.filter(search_title__contains=word)
        own_first = Case(When(owner=user, then=Value(0)), default=Value(1), output_field=IntegerField())
        return qs.distinct().order_by(own_first, "-updated_at")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()[: self.LIST_LIMIT]
        return Response(self.get_serializer(qs, many=True).data)

    def _owned(self):
        spell = self.get_object()
        if spell.owner_id != self.request.user.pk:
            raise PermissionDenied(_("Only the recipe's owner can change it."))
        return spell

    def perform_create(self, serializer):
        spell = serializer.save()
        posthog.capture("spell_created", properties={"visibility": spell.visibility})

    def update(self, request, *args, **kwargs):
        self._owned()
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._owned()
        response = super().destroy(request, *args, **kwargs)
        posthog.capture("spell_deleted")
        return response

    def _set_visibility(self, visibility):
        spell = self._owned()
        spell.visibility = visibility
        spell.save(update_fields=["visibility", "updated_at"])
        posthog.capture(
            "spell_published" if visibility == Spell.Visibility.PUBLIC else "spell_unpublished"
        )
        return Response(self.get_serializer(spell).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, uuid=None):
        return self._set_visibility(Spell.Visibility.PUBLIC)

    @action(detail=True, methods=["post"])
    def unpublish(self, request, uuid=None):
        return self._set_visibility(Spell.Visibility.PRIVATE)

    @action(detail=True, methods=["post"])
    def fork(self, request, uuid=None):
        copy = grimoire.fork_spell(self.get_object(), request.user, lang=get_language())
        posthog.capture("spell_forked")
        # Not get_queryset(): its ?scope/?tag/?q filters could hide the new copy.
        copy = self._base_queryset().get(pk=copy.pk)
        return Response(self.get_serializer(copy).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"])
    def castings(self, request, uuid=None):
        """``GET``: the user's own castings of this spell, newest first.
        ``POST``: log one. Any readable spell (own or public) can be cast."""
        spell = self.get_object()
        if request.method == "GET":
            qs = Casting.objects.filter(spell=spell, user=request.user).select_related("spell")
            return Response(CastingSerializer(qs, many=True, context={"request": request}).data)
        serializer = CastingSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        casting = serializer.save(user=request.user, spell=spell, spell_title=spell.title)
        posthog.capture(
            "spell_cast",
            properties={
                "own_spell": spell.owner_id == request.user.pk,
                "weighed": casting.cooked_weight_g is not None,
            },
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def scale(self, request, uuid=None):
        """Scale a readable spell (#69) — nothing is written. One ``mode``:
        ``factor``, ``servings``, ``total_grams`` (``grams``) or ``available``
        (``[{reagent, quantity, unit}]``, the reagent that runs out first wins).
        An "I have" amount whose unit can't be turned into grams is listed in
        ``unconvertible`` and left out, never guessed."""
        spell = self.get_object()
        req = SpellScaleSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        data = req.validated_data
        lines = list(spell.lines.all())
        plain = [
            grimoire_scaling.Line(key=line.reagent.uuid, quantity=line.quantity, unit=line.unit, grams=line.grams)
            for line in lines
        ]
        unconvertible = []
        try:
            mode = data["mode"]
            if mode == "factor":
                result = grimoire_scaling.scale_by_factor(plain, data.get("factor"))
            elif mode == "servings":
                result = grimoire_scaling.scale_to_servings(plain, spell.servings, data.get("servings"))
            elif mode == "total_grams":
                result = grimoire_scaling.scale_to_total_grams(plain, data.get("grams"))
            else:
                on_hand = {}
                for item in data.get("available", []):
                    try:
                        grams = grimoire.grams_on_hand(lines, item["reagent"], item["quantity"], item["unit"])
                    except grimoire.ConversionError:
                        unconvertible.append(str(item["reagent"]))
                        continue
                    on_hand[item["reagent"]] = on_hand.get(item["reagent"], 0.0) + grams
                if not on_hand and unconvertible:
                    return Response(
                        {"detail": _("Those amounts can't be converted to grams — use g."),
                         "code": "unconvertible", "unconvertible": unconvertible},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                result = grimoire_scaling.scale_by_available(plain, on_hand)
        except grimoire_scaling.ScalingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        posthog.capture("spell_scaled", properties={"mode": mode})
        return Response({
            "factor": result.factor,
            "servings": spell.servings * result.factor,
            "total_grams": sum(line.grams for line in result.lines),
            "limiting": str(result.limiting) if result.limiting is not None else None,
            "unconvertible": unconvertible,
            "lines": [
                {
                    "line": i, "reagent": str(line.key), "unit": line.unit, "quantity": line.quantity,
                    "display_quantity": line.display_quantity, "grams": line.grams,
                }
                for i, line in enumerate(result.lines)
            ],
        })

    # ── Nutrition (#70) ──────────────────────────────────────────────────────

    def _nutrition_basis(self, spell):
        """(lines, per-batch totals, raw grams, Yield) for the requesting user."""
        lines = list(
            SpellReagent.objects.filter(spell=spell).select_related("reagent", "reagent__nutrients")
        )
        ingredients = []
        for line in lines:
            profile = getattr(line.reagent, "nutrients", None)
            per = {name: getattr(profile, name) for name in grimoire_nutrition.FIELDS} if profile else None
            ingredients.append(grimoire_nutrition.Ingredient(key=line.reagent_id, grams=line.grams, per_100g=per))
        raw_g = sum(line.grams for line in lines)
        # The user's own latest weighed casting — castings are private, so
        # nobody else's pot ever changes what this user sees.
        weighed = (
            Casting.objects.filter(spell=spell, user=self.request.user, cooked_weight_g__isnull=False)
            .order_by("-cast_on", "-created_at")
            .first()
        )
        ratio = None
        if weighed:
            cast_raw = grimoire_nutrition.casting_raw_grams(lines, weighed.adjustments)
            ratio = weighed.cooked_weight_g / cast_raw if cast_raw > 0 else None
        yld = grimoire_nutrition.resolve_yield(
            raw_g, casting_ratio=ratio, cast_on=weighed.cast_on if weighed else None,
            cooked_yield_g=spell.cooked_yield_g, yield_pct=spell.yield_pct,
        )
        return lines, ingredients, raw_g, yld

    @action(detail=True, methods=["get"])
    def nutrition(self, request, uuid=None):
        """Batch / per-serving / per-100 g-cooked nutrients of a readable spell.
        ``?servings=N`` or ``?scale=F`` scale the batch like the scaler (#69);
        per serving and per 100 g don't change with the scale."""
        spell = self.get_object()
        lines, ingredients, raw_g, yld = self._nutrition_basis(spell)
        plain = [
            grimoire_scaling.Line(key=line.reagent_id, quantity=line.quantity, unit=line.unit, grams=line.grams)
            for line in lines
        ]
        try:
            if request.query_params.get("servings") not in (None, ""):
                factor = grimoire_scaling.scale_to_servings(
                    plain, spell.servings, request.query_params["servings"]).factor
            elif request.query_params.get("scale") not in (None, ""):
                factor = grimoire_scaling.scale_by_factor(plain, request.query_params["scale"]).factor
            else:
                factor = 1.0
        except grimoire_scaling.ScalingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        batch = grimoire_nutrition.batch_totals(ingredients)
        cooked_g = raw_g * yld.ratio
        missing_names = {}
        for line in lines:
            profile = getattr(line.reagent, "nutrients", None)
            # Named only when the basics are unknown; any other gap shows as "≥" on its value.
            if line.grams > 0 and (profile is None or any(getattr(profile, n) is None for n in grimoire_nutrition.CORE)):
                missing_names[line.reagent_id] = line.reagent
        nutrients = []
        for name in grimoire_nutrition.FIELDS:
            total = batch[name]
            nutrients.append({
                "key": name,
                "batch": round(total.value * factor, 3),
                "per_serving": round(total.value / spell.servings, 3),
                "per_100g": round(total.value / cooked_g * 100, 3) if cooked_g > 0 else None,
                "missing": total.missing,
            })
        return Response({
            "factor": factor,
            "servings": spell.servings * factor,
            "raw_g": round(raw_g * factor, 1),
            "cooked_g": round(cooked_g * factor, 1),
            "yield": {
                "source": yld.source,
                "ratio": round(yld.ratio, 4),
                "cast_on": yld.cast_on,
            },
            "nutrients": nutrients,
            "reagents_missing_data": [
                {"reagent": str(r.uuid), "name": r.name, "name_es": r.name_es} for r in missing_names.values()
            ],
        })

    @action(detail=True, methods=["post"])
    def portion(self, request, uuid=None):
        """``{"goals": [{nutrient, at_least, at_most}]}`` → the range of grams of
        the cooked dish that meets every goal, or the conflicting goals."""
        spell = self.get_object()
        req = SpellPortionSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        try:
            goals = grimoire_nutrition.clean_goals(req.validated_data["goals"])
        except grimoire_nutrition.NutritionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        _lines, ingredients, raw_g, yld = self._nutrition_basis(spell)
        cooked_g = raw_g * yld.ratio
        if cooked_g <= 0:
            return Response({"detail": _("This spell has no weighed ingredients.")}, status=status.HTTP_400_BAD_REQUEST)
        per_100g = {
            name: grimoire_nutrition.Total(total.value / cooked_g * 100, total.missing)
            for name, total in grimoire_nutrition.batch_totals(ingredients).items()
        }
        result = grimoire_nutrition.recommend_portion(per_100g, goals)
        posthog.capture(
            "portion_calculated",
            properties={"goal_count": len(goals), "feasible": result.feasible},
        )
        return Response({
            "feasible": result.feasible,
            "min_g": result.min_g,
            "max_g": result.max_g,
            "conflicts": result.conflicts,
            "incomplete": result.incomplete,
            "cooked_g": round(cooked_g, 1),
            "yield_source": yld.source,
        })


class CastingViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """The user's castings across all spells (``castings/``), newest first —
    including ones whose spell was removed. Only ever the user's own: anyone
    else's casting is a 404. Created through ``spells/{uuid}/castings/``."""

    permission_classes = [IsAuthenticated]
    serializer_class = CastingSerializer
    lookup_field = "uuid"
    pagination_class = None
    LIST_LIMIT = 200

    def get_queryset(self):
        return Casting.objects.filter(user=self.request.user).select_related("spell")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()[: self.LIST_LIMIT]
        return Response(self.get_serializer(qs, many=True).data)


class PreferencesView(APIView):
    """``GET`` / ``PATCH`` the user's Grimoire preferences (``unit_system``)."""

    permission_classes = [IsAuthenticated]

    def _prefs(self, request):
        prefs, _created = GrimoirePreferences.objects.get_or_create(user=request.user)
        return prefs

    def get(self, request):
        return Response(GrimoirePreferencesSerializer(self._prefs(request)).data)

    def patch(self, request):
        serializer = GrimoirePreferencesSerializer(self._prefs(request), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        prefs = serializer.save()
        posthog.capture("grimoire_preferences_updated", properties={"unit_system": prefs.unit_system})
        return Response(serializer.data)

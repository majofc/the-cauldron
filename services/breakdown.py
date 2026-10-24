"""The goal cauldron (#107): what a goal card's number is made of, and filling
the values it doesn't know.

**Breakdown** — the entries in one rolling window (the same rule as
``services.rolling``: ``end - span < eaten_at <= end``) combined by spell /
reagent / quick-entry name. The top ``TOP`` groups by known amount plus an
"others" row form the pie; their amounts add up to the card's value at that
tick (``/ 7`` for the 7-day window, like the card). Every group with an entry
lacking the nutrient is in the fog: never sized, never 0. A spell entry that is
partly known sits in both.

**Fill** — a value the user types for one gap, always marked estimated:

- the user's own editable reagent → saved on it;
- a catalog reagent (approved / USDA / staff) → a private copy for the user
  (``Reagent.copy_of``; reused if one exists), and the user's own spells switch
  their lines to it; nobody else's spell is touched;
- a quick entry → its own value.

Then the user's entries of the last ``REFRESH_DAYS`` days that used the reagent
(directly or through a spell that still contains it) get **that nutrient only**
recomputed, when it was missing in them. Values already known are never
changed and older entries are never touched — a deliberate, narrow exception
to "editing a spell later never rewrites history".
"""

import math
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

from the_cauldron.models import (
    IntakeEntry,
    Reagent,
    ReagentNutrients,
    SpellReagent,
    added_sugar_over_total,
)
from the_cauldron.services import grimoire
from the_cauldron.services import nutrition as nu
from the_cauldron.services.nutrition import NutritionInputError
from the_cauldron.services.rolling import SPANS

TOP = 5
REFRESH_DAYS = 7
MAX_VALUE = 100_000  # per 100 g or per entry; a typo guard, not a nutrition rule
MAX_LOOKBACK_DAYS = 366


def window_args(user, params):
    """``(span, end, nutrient)`` from query/body params (API and MCP tool).
    ``end`` is an ISO time (naive = the user's clock); default now."""
    span = params.get("span", "24h")
    nutrient = params.get("nutrient")
    if span not in SPANS:
        raise NutritionInputError(_("Use span 24h or 7d."))
    if nutrient not in nu.FIELDS:
        raise NutritionInputError(_("Unknown nutrient."))
    raw = params.get("end")
    try:
        end = parse_datetime(raw) if raw else timezone.now()
    except ValueError:  # well-formed but impossible, e.g. month 13
        end = None
    if end is None:
        raise NutritionInputError(_("Use a date and time like 2026-10-15T18:00."))
    try:
        if timezone.is_naive(end):
            end = nu.localize(end, nu.user_tz(user))
        too_old = end < timezone.now() - timedelta(days=MAX_LOOKBACK_DAYS)
    except (ValueError, OverflowError):
        too_old = True
    if too_old:
        raise NutritionInputError(_("Pick a time within the last year."))
    return span, min(end, timezone.now()), nutrient


def _key(entry):
    if entry.source == IntakeEntry.Source.SPELL:
        return ("spell", entry.spell_id or f"gone:{entry.label}")
    if entry.source == IntakeEntry.Source.REAGENT:
        return ("reagent", entry.reagent_id or f"gone:{entry.label}")
    return ("quick", entry.label.strip().casefold())


def window_entries(user, span, end):
    """The user's entries in the window ending at ``end`` — the rolling rule."""
    start = end - timedelta(hours=SPANS[span])
    return list(
        IntakeEntry.objects.filter(user=user, eaten_at__gt=start, eaten_at__lte=end)
        .select_related("spell", "reagent").order_by("eaten_at", "created_at")
    )


def _missing_reagents(spell, nutrient, user):
    """The reagents on the user's own ``spell`` that lack ``nutrient`` (no
    profile or null) and that the user can see — a fork may carry someone
    else's private reagent, which can't be filled from here."""
    visible = set(grimoire.visible_reagents(user).filter(spell_lines__spell=spell).values_list("pk", flat=True))
    out, seen = [], set()
    for line in spell.lines.select_related("reagent__nutrients").order_by("order"):
        reagent = line.reagent
        profile = getattr(reagent, "nutrients", None)
        if reagent.pk in seen or line.grams <= 0 or reagent.pk not in visible:
            continue
        if profile is None or getattr(profile, nutrient) is None:
            seen.add(reagent.pk)
            out.append(_reagent_target(reagent, user))
    return out


def _reagent_target(reagent, user):
    return {
        "reagent": str(reagent.uuid),
        "name": reagent.name,
        "name_es": reagent.name_es,
        "editable": grimoire.can_edit_reagent(user, reagent),
    }


def breakdown(user, span, end, nutrient):
    """The payload of ``GET rolling/breakdown/`` and MCP ``get_goal_breakdown``."""
    per_day = SPANS[span] / 24
    tz = nu.user_tz(user)
    entries = window_entries(user, span, end)
    readable = set(grimoire.readable_spells(user).filter(
        pk__in={e.spell_id for e in entries if e.spell_id}).values_list("pk", flat=True))
    visible = set(grimoire.visible_reagents(user).filter(
        pk__in={e.reagent_id for e in entries if e.reagent_id}).values_list("pk", flat=True))

    groups = {}
    for entry in entries:
        key = _key(entry)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "kind": key[0], "label": entry.label, "spell": None, "reagent": None,
                "entries": 0, "amount": 0.0, "missing": False, "estimated": False,
                "_spell": None, "_reagent": None, "_quick": [],
            }
            if entry.spell_id and entry.spell_id in readable:
                g["spell"], g["_spell"] = str(entry.spell.uuid), entry.spell
            if entry.reagent_id and entry.reagent_id in visible:
                g["reagent"], g["_reagent"] = str(entry.reagent.uuid), entry.reagent
        g["label"] = entry.label  # the latest snapshot title
        g["entries"] += 1
        g["amount"] += entry.nutrients.get(nutrient) or 0.0
        g["estimated"] = g["estimated"] or bool((entry.basis or {}).get("estimated"))
        if entry.missing.get(nutrient):
            g["missing"] = True
            if entry.source == IntakeEntry.Source.QUICK:
                g["_quick"].append({"entry": str(entry.uuid), "label": entry.label,
                                    "eaten_at": timezone.localtime(entry.eaten_at, tz)})

    rows = sorted(groups.values(), key=lambda g: -g["amount"])
    sized = [g for g in rows if g["amount"] > 0]
    total = sum(g["amount"] for g in rows) / per_day

    def public(g):
        return {k: v for k, v in g.items() if not k.startswith("_")} | {
            "amount": round(g["amount"] / per_day, 3),
            "pct": round(g["amount"] / per_day / total * 100, 1) if total > 0 else None,
        }

    pie = [public(g) for g in sized[:TOP]]
    rest = sized[TOP:]
    if rest:
        amount = sum(g["amount"] for g in rest)
        pie.append({
            "kind": "others", "label": "", "spell": None, "reagent": None,
            "entries": sum(g["entries"] for g in rest), "groups": len(rest),
            "amount": round(amount / per_day, 3),
            "pct": round(amount / per_day / total * 100, 1) if total > 0 else None,
            "missing": any(g["missing"] for g in rest), "estimated": any(g["estimated"] for g in rest),
        })

    fog = []
    for g in rows:
        if not g["missing"]:
            continue
        item = public(g)
        if g["kind"] == "quick":
            item["fill"] = {"quick": g["_quick"]}
        elif g["_reagent"] is not None:
            item["fill"] = {"reagents": [_reagent_target(g["_reagent"], user)]}
        elif g["_spell"] is not None and g["_spell"].owner_id != user.pk:
            # Someone else's spell: a value filled here couldn't reach it (only the
            # user's own spells take the private copy) — "Make it mine" first.
            item["fill"] = {"not_owner": True}
        elif g["_spell"] is not None:
            item["fill"] = {"reagents": _missing_reagents(g["_spell"], nutrient, user)}
        else:
            item["fill"] = None  # the spell or ingredient is gone: nothing left to fill
        fog.append(item)

    return {
        "nutrient": nutrient,
        "span": span,
        "end": timezone.localtime(end, tz),
        "per_day": per_day,
        "entries": len(entries),
        "total": round(total, 2),
        "lower_bound": bool(fog),
        "pie": pie,
        "fog": fog,
    }


# ── Filling a gap ────────────────────────────────────────────────────────────


def clean_value(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise NutritionInputError(_("Enter a number.")) from None
    if not math.isfinite(value) or value < 0 or value > MAX_VALUE:
        raise NutritionInputError(_("Enter a number of 0 or more."))
    return value


def _check_sugars(values, nutrient, value):
    merged = {**values, nutrient: value}
    if added_sugar_over_total(merged.get("sugar_g"), merged.get("added_sugar_g")):
        raise NutritionInputError(_("Added sugars can't be more than the total sugars."))


def private_copy(user, original):
    """The user's private copy of a catalog reagent, made once and reused."""
    copy, created = Reagent.objects.get_or_create(
        copy_of=original, created_by=user,
        defaults={
            "name": original.name, "name_es": original.name_es, "category": original.category,
            "source": Reagent.Source.USER, "status": Reagent.Status.PRIVATE,
            "grams_per_unit": original.grams_per_unit, "density_g_per_ml": original.density_g_per_ml,
            "nutrients_estimated": True,
        },
    )
    if not created:
        return copy, False
    profile = getattr(original, "nutrients", None)
    ReagentNutrients.objects.create(
        reagent=copy, **{f: getattr(profile, f) if profile else None for f in ReagentNutrients.FIELDS}
    )
    return copy, True


@transaction.atomic
def fill_reagent(user, reagent, nutrient, value, now=None):
    """Fill ``nutrient`` (per 100 g) for ``reagent`` as the user sees it.
    Returns ``(source, reagent_written, entries_refreshed)``; ``source`` is
    ``reagent`` or ``reagent_copy`` (the PostHog enum)."""
    if grimoire.can_edit_reagent(user, reagent):
        target, source = reagent, "reagent"
        originals = [reagent]
    else:
        target, _created = private_copy(user, reagent)
        source = "reagent_copy"
        originals = [reagent, target]
        # The user's own spells use the copy from now on (same measures, same grams).
        SpellReagent.objects.filter(spell__owner=user, reagent=reagent).update(reagent=target)
    profile, _created = ReagentNutrients.objects.get_or_create(reagent=target)
    _check_sugars({f: getattr(profile, f) for f in ReagentNutrients.FIELDS}, nutrient, value)
    setattr(profile, nutrient, value)
    profile.save(update_fields=[nutrient, "updated_at"])
    if not target.nutrients_estimated:
        target.nutrients_estimated = True
        target.save(update_fields=["nutrients_estimated", "updated_at"])
    refreshed = refresh_entries(user, originals, nutrient, value, now=now)
    return source, target, refreshed


def refresh_entries(user, reagents, nutrient, value, now=None):
    """Gap-fill ``nutrient`` in the user's entries of the last ``REFRESH_DAYS``
    days that used one of ``reagents`` (``value`` = the reagent's new per-100 g
    amount): only where it was missing, only that nutrient. Returns how many
    entries changed."""
    now = now or timezone.now()
    ids = [r.pk for r in reagents]
    recent = IntakeEntry.objects.filter(
        Q(reagent_id__in=ids) | Q(spell__lines__reagent_id__in=ids),
        user=user, eaten_at__gt=now - timedelta(days=REFRESH_DAYS), eaten_at__lte=now,
    ).select_related("spell").distinct()
    readable = set(grimoire.readable_spells(user).values_list("pk", flat=True))
    changed = []
    spell_bases = {}
    for entry in recent:
        if not entry.missing.get(nutrient) or not entry.grams:
            continue
        if entry.source == IntakeEntry.Source.REAGENT and entry.reagent_id in ids:
            per_100g = value
            missing_now = 0
        elif entry.source == IntakeEntry.Source.SPELL and entry.spell_id in readable:
            if entry.spell_id not in spell_bases:
                try:
                    spell_bases[entry.spell_id] = nu.spell_basis_snapshot(entry.spell, user)
                except NutritionInputError:
                    spell_bases[entry.spell_id] = None
            fresh = spell_bases[entry.spell_id]
            if fresh is None or nutrient not in fresh["per_100g"]:
                continue
            per_100g = fresh["per_100g"][nutrient]
            missing_now = fresh["missing"].get(nutrient, 0)
            if missing_now >= entry.missing[nutrient]:
                continue  # nothing this fill could add
        else:
            continue
        basis = dict(entry.basis or {})
        basis["per_100g"] = {**basis.get("per_100g", {}), nutrient: per_100g}
        basis_missing = dict(basis.get("missing", {}))
        if missing_now:
            basis_missing[nutrient] = missing_now
        else:
            basis_missing.pop(nutrient, None)
        basis["missing"] = basis_missing
        basis["estimated"] = True
        entry.basis = basis
        entry.nutrients = {**entry.nutrients, nutrient: round(per_100g * entry.grams / 100.0, 3)}
        entry_missing = dict(entry.missing)
        if missing_now:
            entry_missing[nutrient] = missing_now
        else:
            entry_missing.pop(nutrient, None)
        entry.missing = entry_missing
        changed.append(entry)
    IntakeEntry.objects.bulk_update(changed, ["basis", "nutrients", "missing"])
    return len(changed)


@transaction.atomic
def fill_quick(user, entry, nutrient, value):
    """Fill a quick entry's own missing value (a total, not per 100 g)."""
    if entry.source != IntakeEntry.Source.QUICK:
        raise NutritionInputError(_("Only a typed-in entry takes a value directly."))
    if not entry.missing.get(nutrient):
        raise NutritionInputError(_("That value is already known."))
    _check_sugars(entry.nutrients, nutrient, value)
    entry.nutrients = {**entry.nutrients, nutrient: value}
    entry.missing = {k: v for k, v in entry.missing.items() if k != nutrient}
    entry.basis = {**(entry.basis or {}), "estimated": True}
    entry.save(update_fields=["nutrients", "missing", "basis", "updated_at"])
    return entry

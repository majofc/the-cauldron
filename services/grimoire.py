"""Grimoire domain rules (#67): unit → grams, reagent visibility/search, fork.

Kept out of the API layer so the seed command, admin and API share one
definition of "who can see this reagent" and "how many grams is a cup".
"""

from django.db import transaction
from django.db.models import Avg, Case, Count, IntegerField, Max, OuterRef, Prefetch, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _

from the_cauldron.models import Casting, Reagent, Spell, SpellReagent, normalize_search

# Fixed mass units, in grams.
MASS_UNITS = {"g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}
# Volume units, in millilitres (US customary for the spoon/cup measures).
VOLUME_UNITS = {"ml": 1.0, "l": 1000.0, "tsp": 4.92892, "tbsp": 14.7868, "cup": 236.588}
# Household units a reagent can define its own weight for (grams_per_unit keys).
HOUSEHOLD_UNITS = ("cup", "tbsp", "tsp", "piece", "glass")


class ConversionError(ValueError):
    """The quantity can't be turned into grams with what we know of the reagent."""


def to_grams(reagent, quantity, unit):
    """Grams for ``quantity`` ``unit`` of ``reagent``.

    Order of preference: a fixed mass unit; the reagent's own measured weight
    for a household unit (``grams_per_unit``, e.g. a cup of flour); a volume
    unit through the reagent's density. ``piece`` has no generic fallback.
    """
    quantity = float(quantity)
    if quantity < 0:
        raise ConversionError(_("Quantity can't be negative."))
    if unit in MASS_UNITS:
        return round(quantity * MASS_UNITS[unit], 2)
    per_unit = (reagent.grams_per_unit or {}).get(unit)
    if per_unit:
        return round(quantity * float(per_unit), 2)
    if unit in VOLUME_UNITS and reagent.density_g_per_ml:
        return round(quantity * VOLUME_UNITS[unit] * reagent.density_g_per_ml, 2)
    raise ConversionError(
        _("Can't convert %(unit)s of %(name)s to grams — use g, or add its weight per %(unit)s.")
        % {"unit": unit, "name": reagent.name}
    )


def grams_on_hand(lines, reagent_uuid, quantity, unit):
    """Grams of ``quantity unit`` of a reagent on a spell, for the scaler (#69).

    A unit one of its lines already uses converts through that line's own
    weight (what the author measured); any other unit goes through
    ``to_grams``, raising ``ConversionError`` rather than guessing. A reagent
    that isn't on the spell gets ``0`` and the scaler rejects it.
    """
    same = [line for line in lines if line.reagent.uuid == reagent_uuid]
    if not same:
        return 0.0
    for line in same:
        if line.unit == unit and line.quantity > 0:
            return quantity * line.grams / line.quantity
    return to_grams(same[0].reagent, quantity, unit)


def visible_reagents(user):
    """Approved reagents for everyone, plus the user's own unapproved ones."""
    return Reagent.objects.filter(
        Q(status=Reagent.Status.APPROVED) | Q(created_by=user)
    )


# Candidates ranked in Python per search; bounds the work for a 1-letter query.
SEARCH_CANDIDATES = 500


def _search_rank(reagent, needle):
    """0 exact, 1 prefix, 2 infix — on the English OR Spanish name alone, not
    the combined ``search_name`` (where a Spanish match always sits mid-string)."""
    names = [normalize_search(reagent.name), normalize_search(reagent.name_es)]
    if needle in names:
        return 0
    if any(n.startswith(needle) for n in names if n):
        return 1
    return 2


def can_edit_reagent(user, reagent):
    """Only the creator, and only until staff approve it — an approved reagent
    belongs to the shared catalog (edited by staff in the admin)."""
    return reagent.created_by_id == user.pk and reagent.status != Reagent.Status.APPROVED


def regrams_lines(reagent):
    """Recompute ``grams`` on every line using ``reagent`` after its weights or
    density changed. A line whose unit no longer converts keeps its old grams."""
    changed = []
    for line in SpellReagent.objects.filter(reagent=reagent):
        try:
            grams = to_grams(reagent, line.quantity, line.unit)
        except ConversionError:
            continue
        if grams != line.grams:
            line.grams = grams
            changed.append(line)
    SpellReagent.objects.bulk_update(changed, ["grams"])
    return len(changed)


def search_reagents(user, query, limit=20, queryset=None):
    """Case/accent-insensitive search; exact, then prefix, then infix matches.

    Every word of the query must appear (``"pimenton dulce"`` finds "Pimentón
    dulce"), so close matches surface before a user creates a duplicate.
    """
    qs = (queryset if queryset is not None else visible_reagents(user)).select_related("nutrients")
    needle = normalize_search(query)
    if not needle:
        return list(qs[:limit])
    for word in needle.split():
        qs = qs.filter(search_name__contains=word)
    hits = list(qs.order_by("name")[:SEARCH_CANDIDATES])
    hits.sort(key=lambda r: (_search_rank(r, needle), r.name.lower()))
    return hits[:limit]


def exact_duplicates(user, name):
    """Visible reagents whose normalized name matches ``name`` exactly."""
    needle = normalize_search(name)
    # search_name holds "<name> <name_es>", so a Spanish-name match sits mid-string.
    return [
        r for r in visible_reagents(user).filter(search_name__contains=needle)
        if normalize_search(r.name) == needle or normalize_search(r.name_es) == needle
    ]


def exact_spell_duplicates(user, title):
    """Readable spells (own + public) whose English or Spanish title matches
    ``title`` once normalized — what to reuse before creating another."""
    needle = normalize_search(title)
    if not needle:
        return []
    return [
        s for s in readable_spells(user).filter(search_title__contains=needle).select_related("owner")
        if needle in (normalize_search(s.title), normalize_search(s.title_es))
    ]


def readable_spells(user):
    """Spells the user may read: their own (any visibility) + every public one."""
    return Spell.objects.filter(Q(owner=user) | Q(visibility=Spell.Visibility.PUBLIC))


SPELL_SCOPES = ("all", "mine", "coven", "public")


def filter_spells(qs, user, *, scope=None, tag=None, q=""):
    """Narrow readable spells: ``scope`` all (default) | mine | coven (other
    users' public spells; ``public`` is an alias), a tag slug, and a title
    search (English or Spanish, case/accent-insensitive, every word must
    match). The user's own spells first, then newest."""
    if scope == "mine":
        qs = qs.filter(owner=user)
    elif scope in ("coven", "public"):
        qs = qs.filter(visibility=Spell.Visibility.PUBLIC).exclude(owner=user)
    if tag:
        qs = qs.filter(tags__slug=tag)
    for word in normalize_search(q or "").split():
        qs = qs.filter(search_title__contains=word)
    own_first = Case(When(owner=user, then=Value(0)), default=Value(1), output_field=IntegerField())
    return qs.distinct().order_by(own_first, "-updated_at")


# Spells weighed per "what can I cook" query before ranking in Python.
HAVE_CANDIDATES = 200


def spells_using(qs, reagent_ids, limit=10):
    """Spells from ``qs`` ranked by how many of ``reagent_ids`` (what the user
    has) they use: most matched first, then fewest missing. Each item is
    ``(spell, matched_lines, missing_lines)``; a reagent on two lines of one
    spell counts once. Pass ``qs`` through ``with_spell_relations`` to fetch
    the lines in one query."""
    have = set(reagent_ids)
    if not have:
        return []
    candidates = (
        qs.filter(lines__reagent__in=have)
        .annotate(matched_n=Count("lines__reagent", filter=Q(lines__reagent__in=have), distinct=True))
        .order_by("-matched_n", "-updated_at")[:HAVE_CANDIDATES]
    )
    ranked = []
    for spell in candidates:
        lines = list(spell.lines.all())
        matched = [line for line in lines if line.reagent_id in have]
        missing = [line for line in lines if line.reagent_id not in have]
        ranked.append((spell, matched, missing))
    ranked.sort(key=lambda r: (-len({line.reagent_id for line in r[1]}), len({line.reagent_id for line in r[2]})))
    return ranked[:limit]


def can_read(spell, user):
    """``readable_spells`` for one already-loaded spell, without a query."""
    return spell.owner_id == user.pk or spell.visibility == Spell.Visibility.PUBLIC


def with_casting_stats(qs, user):
    """Annotate the user's own casting count / average score / last date.

    Subqueries, not joins: the list may already join tags (``?tag=``), and a
    join here would multiply the counts.
    """
    mine = Casting.objects.filter(spell=OuterRef("pk"), user=user).order_by().values("spell")
    return qs.annotate(
        my_casting_count=Coalesce(
            Subquery(mine.annotate(n=Count("pk")).values("n")[:1], output_field=IntegerField()), 0
        ),
        my_average_score=Subquery(mine.annotate(a=Avg("score")).values("a")[:1]),
        my_last_cast_on=Subquery(mine.annotate(d=Max("cast_on")).values("d")[:1]),
    )


def with_spell_relations(qs):
    """Everything SpellSerializer touches, prefetched — one place, so the list,
    detail and write responses never fall back to a query per line."""
    return qs.select_related("owner", "forked_from").prefetch_related(
        "tags", Prefetch("lines", queryset=SpellReagent.objects.select_related("reagent"))
    )


def localized_text(spell, lang):
    """(title, description, steps) in ``lang`` — Spanish where the spell has it,
    else the base text. Each field falls back on its own."""
    if (lang or "").startswith("es"):
        return (
            spell.title_es or spell.title,
            spell.description_es or spell.description,
            list(spell.steps_es or spell.steps or []),
        )
    return spell.title, spell.description, list(spell.steps or [])


@transaction.atomic
def fork_spell(spell, user, lang="en"):
    """"Make it mine": an independent private deep copy owned by ``user``.

    The copy shares no rows with the original — later edits, unpublishing or
    deletion of the original never reach it (``forked_from`` is SET_NULL).
    It is written in ``lang`` (the forker's language) into the base fields:
    the fork is now the user's own spell, and users write in one language.
    """
    title, description, steps = localized_text(spell, lang)
    copy = Spell.objects.create(
        owner=user,
        title=title,
        description=description,
        servings=spell.servings,
        prep_minutes=spell.prep_minutes,
        cook_minutes=spell.cook_minutes,
        cooked_yield_g=spell.cooked_yield_g,
        yield_pct=spell.yield_pct,
        steps=steps,
        visibility=Spell.Visibility.PRIVATE,
        forked_from=spell,
    )
    copy.tags.set(spell.tags.all())
    spanish = (lang or "").startswith("es")
    SpellReagent.objects.bulk_create(
        SpellReagent(
            spell=copy,
            reagent_id=line.reagent_id,
            quantity=line.quantity,
            unit=line.unit,
            grams=line.grams,
            note=(line.note_es or line.note) if spanish else line.note,
            order=line.order,
        )
        for line in spell.lines.all()
    )
    return copy

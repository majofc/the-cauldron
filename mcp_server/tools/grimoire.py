"""Grimoire read tools: spells (recipes) and reagents (ingredients).

Visibility is the web's: ``readable_spells`` (own + public) and
``visible_reagents`` (approved + own); anything else is "not found".
Free text written by *other* users (a coven spell's title, description, steps
and line notes) is returned inside ``untrusted_text`` so the assistant reads it
as data, never as instructions (see ``instructions.py``).
"""

import uuid

from the_cauldron.management.commands.seed_grimoire import SYSTEM_USERNAME
from the_cauldron.mcp_server.errors import ToolError
from the_cauldron.mcp_server.registry import tool
from the_cauldron.mcp_server.tools import _format as fmt
from the_cauldron.models import Reagent, ReagentNutrients
from the_cauldron.services import grimoire, nutrition
from the_cauldron.services.nutrition import NutritionInputError

MAX_LIMIT = 25
LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 10}
ID = {"type": "string", "description": "The id (uuid) returned by a search."}


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ToolError("invalid_arguments", "That is not a valid id; use one returned by a search.") from None


def _author(spell, ctx):
    if spell.owner_id == ctx.user.pk:
        return "you"
    if spell.owner.username == SYSTEM_USERNAME:
        return "the_grimoire"
    return "another_user"


def _text(spell, ctx, author, **text):
    """Own and starter spells' text is plain; another user's goes in untrusted_text."""
    return {"untrusted_text": text} if author == "another_user" else {"text": text}


def reagent_name(reagent, ctx):
    return (ctx.is_es and reagent.name_es) or reagent.name


def _spell_row(spell, ctx):
    title, _desc, _steps = grimoire.localized_text(spell, ctx.language)
    author = _author(spell, ctx)
    return {
        "id": spell.uuid,
        "author": author,
        **_text(spell, ctx, author, title=title),
        "servings": spell.servings,
        "minutes": (spell.prep_minutes or 0) + (spell.cook_minutes or 0) or None,
        "tags": [t.slug for t in spell.tags.all()],
        "times_you_cooked_it": getattr(spell, "my_casting_count", None),
    }


def _readable(ctx):
    return grimoire.with_casting_stats(
        grimoire.with_spell_relations(grimoire.readable_spells(ctx.user)), ctx.user
    )


def _resolve_reagents(ctx, items):
    """Reagent ids for names or ids the user has; plus what didn't match."""
    visible = grimoire.visible_reagents(ctx.user)
    found, unresolved = {}, []
    for item in items:
        reagent = None
        try:
            reagent = visible.filter(uuid=uuid.UUID(str(item))).first()
        except ValueError:
            hits = grimoire.search_reagents(ctx.user, item, limit=1)
            reagent = hits[0] if hits else None
        if reagent is None:
            unresolved.append(item)
        else:
            found[reagent.pk] = reagent
    return found, unresolved


@tool(
    "search_spells",
    title="Search spells (recipes)",
    description=(
        "Find spells (recipes) the user can read: their own and the coven's public ones. "
        "q matches English or Spanish titles (accent-insensitive); tag is a tag slug (soup, salad, drink, "
        "breakfast, dessert, main, side, snack, sauce, bread, vegetarian, vegan); scope all | mine | coven. "
        "have_reagents: ingredient names or ids the user has (e.g. what's in the fridge) — results are then "
        "ranked by how many they use, with the missing ingredients listed. Always search before create_spell."
    ),
    schema={
        "type": "object",
        "properties": {
            "q": {"type": "string", "maxLength": 100},
            "tag": {"type": "string", "maxLength": 40},
            "scope": {"type": "string", "enum": ["all", "mine", "coven"], "default": "all"},
            "have_reagents": {
                "type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 100},
                "maxItems": 40,
            },
            "limit": LIMIT,
        },
        "additionalProperties": False,
    },
)
def search_spells(ctx, q="", tag=None, scope="all", have_reagents=None, limit=10):
    qs = grimoire.filter_spells(_readable(ctx), ctx.user, scope=scope, tag=tag, q=q)
    if not have_reagents:
        return {"spells": [_spell_row(s, ctx) for s in qs[:limit]]}
    have, unresolved = _resolve_reagents(ctx, have_reagents)
    ranked = grimoire.spells_using(qs, have.keys(), limit=limit)
    rows = []
    for spell, matched, missing in ranked:
        row = _spell_row(spell, ctx)
        row["uses_from_have"] = sorted({reagent_name(line.reagent, ctx) for line in matched})
        row["missing"] = sorted({reagent_name(line.reagent, ctx) for line in missing})
        rows.append(row)
    return {
        "have": sorted(reagent_name(r, ctx) for r in have.values()),
        "not_found": unresolved,
        "spells": rows,
    }


@tool(
    "get_spell",
    title="Spell (recipe) details",
    description=(
        "One spell (recipe): ingredients with amounts and grams, steps, servings, and nutrition per serving "
        "of the cooked dish (nutrients listed in incomplete have missing data, so they're at least that much)."
    ),
    schema={"type": "object", "properties": {"id": ID}, "required": ["id"], "additionalProperties": False},
)
def get_spell(ctx, id):
    spell = _readable(ctx).get(uuid=_uuid(id))
    title, description, steps = grimoire.localized_text(spell, ctx.language)
    author = _author(spell, ctx)
    lines = []
    notes = []
    for line in spell.lines.all():
        note = (ctx.is_es and line.note_es) or line.note
        lines.append({
            "reagent_id": line.reagent.uuid,
            "reagent": reagent_name(line.reagent, ctx),
            "quantity": line.quantity,
            "unit": line.unit,
            "grams": fmt.num(line.grams),
            **({"note": note} if note and author != "another_user" else {}),
        })
        if note and author == "another_user":
            notes.append({"reagent": reagent_name(line.reagent, ctx), "note": note})
    text = {"title": title, "description": description, "steps": steps}
    if notes:
        text["line_notes"] = notes
    out = {
        "id": spell.uuid,
        "author": author,
        "visibility": spell.visibility,
        **_text(spell, ctx, author, **text),
        "servings": spell.servings,
        "prep_minutes": spell.prep_minutes,
        "cook_minutes": spell.cook_minutes,
        "tags": [t.slug for t in spell.tags.all()],
        "lines": lines,
        "unverified_nutrition": any(l.reagent.status != Reagent.Status.APPROVED for l in spell.lines.all()),
        "estimated_nutrition": any(l.reagent.nutrients_estimated for l in spell.lines.all()),
        "times_you_cooked_it": spell.my_casting_count,
    }
    try:
        basis = nutrition.spell_basis_snapshot(spell, ctx.user)
    except NutritionInputError:
        out["nutrition_per_serving"] = None
    else:
        factor = basis["serving_g"] / 100
        out["nutrition_per_serving"] = {
            "serving_g": fmt.num(basis["serving_g"]),
            "yield_source": basis["yield_source"],
            **fmt.known({k: v * factor for k, v in basis["per_100g"].items()}, basis["missing"]),
        }
    return out


def _reagent_row(r, ctx):
    profile = getattr(r, "nutrients", None)
    return {
        "id": r.uuid,
        "name": reagent_name(r, ctx),
        "category": r.category,
        "status": r.status,
        "mine": r.created_by_id == ctx.user.pk,
        "estimated": r.nutrients_estimated,
        "per_100g": {k: getattr(profile, k) if profile else None for k in fmt.KEY},
        "measures_g": r.grams_per_unit or {},
    }


@tool(
    "search_reagents",
    title="Search reagents (ingredients)",
    description=(
        "Find ingredients the user can use: the shared catalog plus their own. English or Spanish names, "
        "accent-insensitive; exact matches first. Always search before create_reagent and reuse a match."
    ),
    schema={
        "type": "object",
        "properties": {"q": {"type": "string", "minLength": 1, "maxLength": 100}, "limit": LIMIT},
        "required": ["q"],
        "additionalProperties": False,
    },
)
def search_reagents(ctx, q, limit=10):
    return {"reagents": [_reagent_row(r, ctx) for r in grimoire.search_reagents(ctx.user, q, limit=limit)]}


@tool(
    "get_reagent",
    title="Reagent (ingredient) details",
    description=(
        "One ingredient: all 24 nutrients per 100 g (null = unknown, never assume 0), weights of household "
        "measures (cup, tbsp, piece, glass…), density, and whether the user may still edit it."
    ),
    schema={"type": "object", "properties": {"id": ID}, "required": ["id"], "additionalProperties": False},
)
def get_reagent(ctx, id):
    r = grimoire.visible_reagents(ctx.user).select_related("nutrients").get(uuid=_uuid(id))
    profile = getattr(r, "nutrients", None)
    row = _reagent_row(r, ctx)
    row["per_100g"] = {k: getattr(profile, k) if profile else None for k in ReagentNutrients.FIELDS}
    row["density_g_per_ml"] = r.density_g_per_ml
    row["can_edit"] = grimoire.can_edit_reagent(ctx.user, r)
    return row

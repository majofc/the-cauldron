"""Grimoire write tools: reagents (ingredients), spells (recipes), forks and
castings (attempts at a recipe).

Input goes through the web's serializers (same validation, unit conversion and
visibility rules). Everything created here is **private** and carries
``channel="mcp"`` — publishing to the coven stays web-only. ``create_*`` search
first, server-side: a name/title the user can already see comes back as
``duplicate`` with the matches, and nothing is created unless ``confirm_new``.
Edits and deletions are two-step (``confirm=True``): see ``registry``.
"""

import posthog

from the_cauldron.grimoire_serializers import CastingSerializer, ReagentSerializer, SpellSerializer
from the_cauldron.mcp_server.errors import ToolError
from the_cauldron.mcp_server.registry import WRITE, tool
from the_cauldron.mcp_server.tools import _format as fmt
from the_cauldron.mcp_server.tools.grimoire import (
    ID,
    _readable,
    _reagent_row,
    _spell_row,
    _uuid,
    get_spell,
)
from the_cauldron.models import Channel, Reagent, ReagentNutrients, Spell, SpellReagent, Tag
from the_cauldron.services import grimoire

MCP = Channel.MCP
UNITS = SpellReagent.Unit.values

PER_100G = {
    "type": "object",
    "description": "Nutrients per 100 g. Leave out what you don't know (unknown, never 0). "
                   "added_sugar_g = sugars added in processing (not those naturally in fruit, milk or "
                   "juice); never more than sugar_g.",
    "properties": {name: {"type": "number", "minimum": 0} for name in ReagentNutrients.FIELDS},
    "additionalProperties": False,
}
REAGENT_FIELDS = {
    "name": {"type": "string", "minLength": 1, "maxLength": 120, "description": "English name."},
    "name_es": {"type": "string", "maxLength": 120, "description": "Spanish name."},
    "category": {"type": "string", "enum": Reagent.Category.values},
    "nutrients_per_100g": PER_100G,
    "grams_per_unit": {
        "type": "object",
        "description": "Weight in grams of one household measure, e.g. {\"cup\": 240, \"piece\": 50, \"glass\": 250}.",
        "properties": {u: {"type": "number", "exclusiveMinimum": 0} for u in grimoire.HOUSEHOLD_UNITS},
        "additionalProperties": False,
    },
    "density_g_per_ml": {"type": "number", "exclusiveMinimum": 0, "maximum": 30},
    "estimated": {"type": "boolean", "description": "True when YOU estimated the nutrients (not from a label)."},
}
LINE = {
    "type": "object",
    "properties": {
        "reagent_id": {"type": "string", "description": "From search_reagents (create it first if missing)."},
        "quantity": {"type": "number", "exclusiveMinimum": 0},
        "unit": {"type": "string", "enum": UNITS},
        "note": {"type": "string", "maxLength": 120},
    },
    "required": ["reagent_id", "quantity", "unit"],
    "additionalProperties": False,
}
SPELL_FIELDS = {
    "title": {"type": "string", "minLength": 1, "maxLength": 150},
    "description": {"type": "string", "maxLength": 2000},
    "servings": {"type": "integer", "minimum": 1, "maximum": 100},
    "prep_minutes": {"type": "integer", "minimum": 0, "maximum": 2000},
    "cook_minutes": {"type": "integer", "minimum": 0, "maximum": 2000},
    "steps": {"type": "array", "items": {"type": "string", "maxLength": 1000}, "maxItems": 60},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 12,
             "description": "Tag slugs: soup, salad, drink, breakfast, dessert, main, side, snack, sauce, bread, vegetarian, vegan."},
    "lines": {"type": "array", "items": LINE, "maxItems": 60,
              "description": "Ingredients. On update, sending lines replaces them all."},
    "cooked_yield_g": {"type": "number", "exclusiveMinimum": 0,
                       "description": "Weight of the whole cooked dish, if known."},
}
CONFIRM_NEW = {"type": "boolean", "description": "Create even though a match exists (only if the user insists)."}


def _ctx(ctx):
    return {"request": ctx.request}


def _duplicate(kind, rows, overridden):
    posthog.capture("mcp_duplicate_prevented", properties={"kind": kind, "overridden": overridden})
    if not overridden:
        raise ToolError(
            "duplicate",
            f"A {kind} with this name already exists. Reuse it, or call again with confirm_new=true "
            "only if the user really wants a separate one.",
            matches=rows,
        )


def _reagent_payload(args):
    data = {k: args[k] for k in ("name", "name_es", "category", "grams_per_unit", "density_g_per_ml") if k in args}
    if "nutrients_per_100g" in args:
        data["nutrients"] = args["nutrients_per_100g"]
    return data


def _reagent_out(ctx, reagent):
    reagent = grimoire.visible_reagents(ctx.user).select_related("nutrients").get(pk=reagent.pk)
    return {**_reagent_row(reagent, ctx), "estimated": reagent.nutrients_estimated}


@tool(
    "create_reagent",
    title="Create an ingredient",
    scope=WRITE,
    description=(
        "Create a private ingredient with nutrients per 100 g. Call search_reagents first and reuse a match: "
        "if one with the same name exists you get code 'duplicate' with the matches and nothing is created. "
        "Set estimated=true when you estimated the nutrients."
    ),
    schema={"type": "object", "properties": {**REAGENT_FIELDS, "confirm_new": CONFIRM_NEW},
            "required": ["name"], "additionalProperties": False},
)
def create_reagent(ctx, confirm_new=False, estimated=False, **args):
    dupes = {r.pk: r for name in (args["name"], args.get("name_es")) if name
             for r in grimoire.exact_duplicates(ctx.user, name)}
    if dupes:
        _duplicate("reagent", [_reagent_row(r, ctx) for r in dupes.values()], overridden=confirm_new)
    serializer = ReagentSerializer(data={**_reagent_payload(args), "share": False}, context=_ctx(ctx))
    serializer.is_valid(raise_exception=True)
    reagent = serializer.save(channel=MCP, nutrients_estimated=estimated)
    return {"created": _reagent_out(ctx, reagent)}


@tool(
    "update_reagent",
    title="Change an ingredient",
    scope=WRITE,
    confirm=True,
    description=(
        "Change one of the user's own ingredients (only while not yet approved for the shared catalog). "
        "Nutrients sent replace just those values. Two-step: preview, then confirm_token."
    ),
    schema={"type": "object", "properties": {"id": ID, **REAGENT_FIELDS}, "required": ["id"],
            "additionalProperties": False},
)
def update_reagent(ctx, id, apply, estimated=None, **args):
    reagent = grimoire.visible_reagents(ctx.user).select_related("nutrients").get(uuid=_uuid(id))
    if not grimoire.can_edit_reagent(ctx.user, reagent):
        raise ToolError("forbidden", "Only your own ingredients can be changed, and only until they're approved.")
    serializer = ReagentSerializer(reagent, data=_reagent_payload(args), partial=True, context=_ctx(ctx))
    serializer.is_valid(raise_exception=True)
    if not apply:
        changes = dict(args, **({"estimated": estimated} if estimated is not None else {}))
        return {"target": reagent.uuid, "preview": {"ingredient": _reagent_out(ctx, reagent), "changes": changes}}
    reagent = serializer.save(**({"nutrients_estimated": estimated} if estimated is not None else {}))
    return {"updated": _reagent_out(ctx, reagent)}


def _spell_payload(args):
    data = {k: args[k] for k in SPELL_FIELDS if k in args and k != "lines"}
    if "lines" in args:
        data["lines"] = [
            {"reagent": line["reagent_id"], "quantity": line["quantity"], "unit": line["unit"],
             "note": line.get("note", "")}
            for line in args["lines"]
        ]
    if "tags" in data:
        known = set(Tag.objects.filter(slug__in=data["tags"]).values_list("slug", flat=True))
        unknown = sorted(set(data["tags"]) - known)
        if unknown:
            raise ToolError("invalid_arguments", f"Unknown tags: {', '.join(unknown)}.")
    return data


@tool(
    "create_spell",
    title="Create a spell (recipe)",
    scope=WRITE,
    description=(
        "Save a new private recipe with ingredient lines (reagent ids from search_reagents; create missing ones "
        "with create_reagent first), steps and servings. Call search_spells first: if the user can already see "
        "one with the same title you get code 'duplicate' with the matches and nothing is created."
    ),
    schema={"type": "object", "properties": {**SPELL_FIELDS, "confirm_new": CONFIRM_NEW},
            "required": ["title", "servings", "lines"], "additionalProperties": False},
)
def create_spell(ctx, confirm_new=False, **args):
    dupes = grimoire.exact_spell_duplicates(ctx.user, args["title"])
    if dupes:
        _duplicate("spell", [_spell_row(s, ctx) for s in _readable(ctx).filter(pk__in=[d.pk for d in dupes])],
                   overridden=confirm_new)
    serializer = SpellSerializer(data=_spell_payload(args), context=_ctx(ctx))
    serializer.is_valid(raise_exception=True)
    spell = serializer.save(channel=MCP)
    return {"created": get_spell(ctx, str(spell.uuid))}


def _own_spell(ctx, id):
    spell = _readable(ctx).get(uuid=_uuid(id))
    if spell.owner_id != ctx.user.pk:
        raise ToolError("forbidden", "Only the recipe's owner can change it. Use fork_spell to make your own copy.")
    return spell


@tool(
    "update_spell",
    title="Change a spell (recipe)",
    scope=WRITE,
    confirm=True,
    description=(
        "Change one of the user's own recipes. Fields left out stay as they are; sending lines replaces all "
        "ingredients. Two-step: preview, then confirm_token."
    ),
    schema={"type": "object", "properties": {"id": ID, **SPELL_FIELDS}, "required": ["id"],
            "additionalProperties": False},
)
def update_spell(ctx, id, apply, **args):
    spell = _own_spell(ctx, id)
    serializer = SpellSerializer(spell, data=_spell_payload(args), partial=True, context=_ctx(ctx))
    serializer.is_valid(raise_exception=True)
    if not apply:
        return {"target": spell.uuid, "preview": {"spell": _spell_row(spell, ctx), "changes": args}}
    serializer.save()
    return {"updated": get_spell(ctx, str(spell.uuid))}


@tool(
    "delete_spell",
    title="Delete a spell (recipe)",
    scope=WRITE,
    confirm=True,
    destructive=True,
    description=(
        "Delete one of the user's own recipes. Logged meals keep their numbers. "
        "Two-step: preview, then confirm_token after the user agrees."
    ),
    schema={"type": "object", "properties": {"id": ID}, "required": ["id"], "additionalProperties": False},
)
def delete_spell(ctx, id, apply):
    spell = _own_spell(ctx, id)
    if not apply:
        return {"target": spell.uuid, "preview": {"delete": _spell_row(spell, ctx)}}
    spell.delete()
    return {"deleted": id}


@tool(
    "fork_spell",
    title="Make a recipe mine",
    scope=WRITE,
    description=(
        "Copy a recipe the user can read (e.g. from the coven) into a private one of their own, in their "
        "language, that they can then change. Later changes to the original never reach the copy."
    ),
    schema={"type": "object", "properties": {"id": ID}, "required": ["id"], "additionalProperties": False},
)
def fork_spell(ctx, id):
    source = _readable(ctx).get(uuid=_uuid(id))
    copy = grimoire.fork_spell(source, ctx.user, lang=ctx.language)
    Spell.objects.filter(pk=copy.pk).update(channel=MCP)
    return {"created": get_spell(ctx, str(copy.uuid))}


@tool(
    "log_casting",
    title="Log a cooking attempt",
    scope=WRITE,
    description=(
        "Record that the user cooked a recipe: the date, a 1–5 score, notes, substitutions, amounts they "
        "changed, and the cooked weight (which makes the recipe's nutrition per serving more accurate)."
    ),
    schema={
        "type": "object",
        "properties": {
            "spell_id": ID,
            "cast_on": {"type": "string", "format": "date", "description": "YYYY-MM-DD (default today)."},
            "score": {"type": "integer", "minimum": 1, "maximum": 5},
            "notes": {"type": "string", "maxLength": 2000},
            "substitutions": {"type": "string", "maxLength": 500},
            "cooked_weight_g": {"type": "number", "exclusiveMinimum": 0},
            "adjustments": {
                "type": "array", "maxItems": 60,
                "items": {
                    "type": "object",
                    "properties": {
                        "reagent_name": {"type": "string", "minLength": 1, "maxLength": 120},
                        "original_qty": {"type": "number", "minimum": 0},
                        "used_qty": {"type": "number", "minimum": 0},
                        "unit": {"type": "string", "enum": UNITS},
                    },
                    "required": ["reagent_name", "used_qty", "unit"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["spell_id"],
        "additionalProperties": False,
    },
)
def log_casting(ctx, spell_id, **args):
    from the_cauldron.services import nutrition

    spell = _readable(ctx).get(uuid=_uuid(spell_id))
    data = {"cast_on": args.pop("cast_on", None) or nutrition.today_for(ctx.user), **args}
    serializer = CastingSerializer(data=data, context=_ctx(ctx))
    serializer.is_valid(raise_exception=True)
    casting = serializer.save(user=ctx.user, spell=spell, spell_title=spell.title, channel=MCP)
    return {
        "logged": {
            "id": casting.uuid,
            "spell_id": spell.uuid,
            "cast_on": casting.cast_on,
            "score": casting.score,
            "cooked_weight_g": fmt.num(casting.cooked_weight_g),
        }
    }

"""Seed the Grimoire: tags, the USDA reagent catalog and the public starter spells.

Idempotent — safe to re-run, and it runs on every container start. By default
it only *creates* what is missing (USDA reagents keyed by ``fdc_id``, staff
reagents by name, tags by slug,
spells by system user + title), so a restart never bumps the starter spells'
``updated_at`` (which would float them above users' spells) and never undoes
staff edits or moderation in the admin.

``--refresh`` pushes changed seed data onto existing rows (names, units,
nutrients, spell content). Even then a reagent's ``status`` and a starter
spell's ``visibility`` are never changed — moderation always wins.
User data is never touched.

Data lives in ``the_cauldron/seed_data/`` (committed, so no USDA API key is
needed at deploy). Rebuild the reagents with ``build_grimoire_reagents``.
``grimoire_staff_reagents.json`` holds staff-entered products USDA doesn't
cover (e.g. Mexican Coca-Cola), from their labels; they're approved catalog
rows owned by the system user.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from the_cauldron.models import Reagent, ReagentNutrients, Spell, SpellReagent, Tag
from the_cauldron.services import grimoire

SEED_DIR = Path(__file__).resolve().parents[2] / "seed_data"
SYSTEM_USERNAME = "the-grimoire"
# auth_user enforces a unique (case-insensitive) email, blank included, so the
# system user needs its own. ``.invalid`` is reserved (RFC 2606): never delivered.
SYSTEM_EMAIL = "the-grimoire@system.invalid"

TAGS = [
    ("soup", "Soup", "Sopa"),
    ("salad", "Salad", "Ensalada"),
    ("drink", "Drink", "Bebida"),
    ("breakfast", "Breakfast", "Desayuno"),
    ("dessert", "Dessert", "Postre"),
    ("main", "Main", "Plato fuerte"),
    ("side", "Side", "Guarnición"),
    ("snack", "Snack", "Botana"),
    ("sauce", "Sauce", "Salsa"),
    ("bread", "Bread", "Pan"),
    ("vegetarian", "Vegetarian", "Vegetariano"),
    ("vegan", "Vegan", "Vegano"),
]


def _load(name):
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


class Command(BaseCommand):
    help = "Seed Grimoire tags, USDA reagents and public starter spells (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--refresh", action="store_true",
            help="Update existing seeded reagents/spells from the seed files (status is kept).",
        )

    @transaction.atomic
    def handle(self, *args, refresh=False, **options):
        tags = {}
        for slug, name, name_es in TAGS:
            tags[slug], _ = Tag.objects.update_or_create(
                slug=slug, defaults={"name": name, "name_es": name_es}
            )

        reagents = {}
        for row in _load("grimoire_reagents.json")["reagents"]:
            fields = {
                "name": row["name"],
                "name_es": row["name_es"],
                "category": row["category"],
                "source": Reagent.Source.USDA,
                "grams_per_unit": row["grams_per_unit"],
                "density_g_per_ml": row["density_g_per_ml"],
            }
            reagent, created = Reagent.objects.get_or_create(
                fdc_id=row["fdc_id"], defaults={**fields, "status": Reagent.Status.APPROVED}
            )
            if refresh and not created:
                for key, value in fields.items():
                    setattr(reagent, key, value)
                reagent.save()
            if created or refresh or not hasattr(reagent, "nutrients"):
                ReagentNutrients.objects.update_or_create(reagent=reagent, defaults=row["nutrients"])
            reagents[row["fdc_id"]] = reagent

        owner = self._system_user()
        n_staff = 0
        for row in _load("grimoire_staff_reagents.json")["reagents"]:
            fields = {
                "name_es": row["name_es"],
                "category": row["category"],
                "grams_per_unit": row["grams_per_unit"],
                "density_g_per_ml": row["density_g_per_ml"],
            }
            reagent = (Reagent.objects.filter(source=Reagent.Source.STAFF, name=row["name"])
                       .order_by("created_at").first())
            created = reagent is None
            if created:
                reagent = Reagent.objects.create(
                    name=row["name"], source=Reagent.Source.STAFF, status=Reagent.Status.APPROVED,
                    created_by=owner, **fields,
                )
            elif refresh:
                for key, value in fields.items():
                    setattr(reagent, key, value)
                reagent.save()
            if created or refresh or not hasattr(reagent, "nutrients"):
                ReagentNutrients.objects.update_or_create(reagent=reagent, defaults=row["nutrients"])
            n_staff += 1
        n_spells = 0
        for data in _load("grimoire_spells.json")["spells"]:
            fields = {
                "title_es": data.get("title_es", ""),
                "description": data["description"],
                "description_es": data.get("description_es", ""),
                "steps_es": data.get("steps_es", []),
                "servings": data["servings"],
                "prep_minutes": data["prep_minutes"],
                "cook_minutes": data["cook_minutes"],
                "steps": data["steps"],
            }
            # Title isn't unique (staff may duplicate a spell in the admin): take
            # the oldest match instead of get_or_create's MultipleObjectsReturned.
            spell = Spell.objects.filter(owner=owner, title=data["title"]).order_by("created_at").first()
            if spell is None:
                spell = Spell.objects.create(
                    owner=owner, title=data["title"], visibility=Spell.Visibility.PUBLIC, **fields
                )
            elif not refresh:
                continue
            else:
                for key, value in fields.items():
                    setattr(spell, key, value)
                spell.save()
            n_spells += 1
            spell.tags.set([tags[slug] for slug in data["tags"]])
            spell.lines.all().delete()
            lines = []
            for order, (fdc_id, qty, unit, note, note_es) in enumerate(data["lines"]):
                reagent = reagents.get(fdc_id)
                if reagent is None:
                    raise CommandError(f"{data['title']}: unknown reagent fdc_id {fdc_id}")
                lines.append(
                    SpellReagent(
                        spell=spell, reagent=reagent, quantity=qty, unit=unit,
                        grams=grimoire.to_grams(reagent, qty, unit), note=note, note_es=note_es,
                        order=order,
                    )
                )
            SpellReagent.objects.bulk_create(lines)

        self.stdout.write(self.style.SUCCESS(
            f"Grimoire seeded: {len(tags)} tags, {len(reagents)} USDA + {n_staff} staff reagents, "
            f"{n_spells} starter spells {'written' if refresh else 'created'}."
        ))

    def _system_user(self):
        """Owner of the starter spells. Cannot log in."""
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=SYSTEM_USERNAME, defaults={"is_active": False, "email": SYSTEM_EMAIL}
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        return user

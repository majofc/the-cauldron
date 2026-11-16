"""Remove the Grimoire's starter spells the owner never used (#126).

    python manage.py prune_seed_spells --dry-run          # list keep/delete + why
    python manage.py prune_seed_spells                    # delete them
    python manage.py prune_seed_spells --dry-run --write-seed-file

"Used by the owner" = the owner's account (``--owner-email``, default
``SUPERADMIN_EMAIL``) cast it, logged it as an intake entry, planned it or
forked it. Other users' use doesn't count. History is safe: castings, intake
entries and plans of a deleted spell keep their title snapshot (SET_NULL), and
other users' forks are separate spells. Reagents stay.

``--write-seed-file`` rewrites ``seed_data/grimoire_spells.json`` to keep only
the spells listed as keep, so ``seed_grimoire`` never recreates the others.
Run the dry run in prod, review it, then delete there and commit the file.
Prints spell titles (public starter content) and reasons only — never who.
"""

import json
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from the_cauldron.management.commands.seed_grimoire import SEED_DIR, SYSTEM_USERNAME
from the_cauldron.models import Casting, IntakeEntry, PlannedMeal, Spell

SPELLS_FILE = "grimoire_spells.json"


def seed_spells():
    """The starter spells in the database: public spells of the system user."""
    return Spell.objects.filter(owner__username=SYSTEM_USERNAME).order_by("title")


def usage_by_owner(owner, spells):
    """``{spell pk: [reason, …]}`` for the spells ``owner`` used."""
    ids = [s.pk for s in spells]
    used = {pk: [] for pk in ids}
    checks = (
        ("cast", Casting.objects.filter(user=owner, spell_id__in=ids).values_list("spell_id", flat=True)),
        ("logged", IntakeEntry.objects.filter(user=owner, spell_id__in=ids).values_list("spell_id", flat=True)),
        ("planned", PlannedMeal.objects.filter(user=owner, spell_id__in=ids).values_list("spell_id", flat=True)),
        ("forked", Spell.objects.filter(owner=owner, forked_from_id__in=ids).values_list("forked_from_id", flat=True)),
    )
    for reason, rows in checks:
        for pk in set(rows):
            used[pk].append(reason)
    return {pk: reasons for pk, reasons in used.items() if reasons}


class Command(BaseCommand):
    help = "Delete the starter spells the owner never cast, logged, planned or forked (--dry-run lists them)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List keep/delete with the reason; write nothing.")
        parser.add_argument("--owner-email", default=os.environ.get("SUPERADMIN_EMAIL"),
                            help="The owner's account (default: SUPERADMIN_EMAIL).")
        parser.add_argument("--write-seed-file", action="store_true",
                            help=f"Rewrite seed_data/{SPELLS_FILE} to keep only the spells kept.")

    def handle(self, *args, dry_run=False, owner_email=None, write_seed_file=False, **options):
        if not owner_email:
            raise CommandError("Set SUPERADMIN_EMAIL or pass --owner-email.")
        owner = get_user_model().objects.filter(email__iexact=owner_email).first()
        if owner is None:
            raise CommandError("No account with that owner email.")
        spells = list(seed_spells())
        used = usage_by_owner(owner, spells)
        keep = [s for s in spells if s.pk in used]
        doomed = [s for s in spells if s.pk not in used]
        for s in keep:
            self.stdout.write(f"keep    {s.title}  ({', '.join(sorted(used[s.pk]))})")
        for s in doomed:
            self.stdout.write(f"delete  {s.title}  (never cast, logged, planned or forked by the owner)")
        self.stdout.write(f"{len(keep)} kept, {len(doomed)} {'to delete' if dry_run else 'deleted'}")
        if write_seed_file:
            self._write_seed_file({s.title for s in keep})
        if dry_run:
            return
        with transaction.atomic():
            Spell.objects.filter(pk__in=[s.pk for s in doomed]).delete()

    def _write_seed_file(self, kept_titles):
        path = SEED_DIR / SPELLS_FILE
        data = json.loads(path.read_text(encoding="utf-8"))
        before = len(data["spells"])
        data["spells"] = [s for s in data["spells"] if s["title"] in kept_titles]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(f"{path.name}: {before} → {len(data['spells'])} spells")

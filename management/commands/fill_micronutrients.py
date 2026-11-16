"""Apply researched micronutrient values (#125) to user-created reagents.

    python manage.py fill_micronutrients --file values.json [--dry-run]

``values.json``: ``{"<reagent uuid>": {"zinc_mg": 1.2, "folate_ug": 30, ...}}``
per 100 g. Only #125's 12 nutrients, only where the reagent's value is still
null (unknown); the reagent is flagged ``nutrients_estimated``. Unknown uuids
and other keys are reported and skipped. Prints counts only.
"""

import json
import math
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from the_cauldron.models import Reagent, ReagentNutrients
from the_cauldron.services.breakdown import ALL_TIME_NUTRIENTS


class Command(BaseCommand):
    help = "Fill researched values of the #125 micronutrients on reagents (nulls only)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, file, dry_run=False, **options):
        try:
            data = json.loads(Path(file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Can't read {file}: {exc}") from None
        if not isinstance(data, dict):
            raise CommandError("Expected an object of reagent uuid → values.")
        filled = skipped = 0
        with transaction.atomic():
            for uuid, values in data.items():
                reagent = Reagent.objects.filter(uuid=uuid).first() if _is_uuid(uuid) else None
                if reagent is None or not isinstance(values, dict):
                    self.stdout.write(f"skip {uuid}: no such reagent or bad values")
                    skipped += 1
                    continue
                profile, _created = ReagentNutrients.objects.get_or_create(reagent=reagent)
                changed = []
                for field, value in values.items():
                    if field not in ALL_TIME_NUTRIENTS:
                        self.stdout.write(f"skip {uuid}.{field}: not a #125 nutrient")
                        continue
                    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                        self.stdout.write(f"skip {uuid}.{field}: not a number ≥ 0")
                        continue
                    if getattr(profile, field) is None:
                        setattr(profile, field, float(value))
                        changed.append(field)
                if changed:
                    profile.save(update_fields=changed)
                    if not reagent.nutrients_estimated:
                        reagent.nutrients_estimated = True
                        reagent.save(update_fields=["nutrients_estimated", "updated_at"])
                    filled += 1
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{filled} reagent(s) filled, {skipped} skipped" + (" (dry run)" if dry_run else ""))


def _is_uuid(value):
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
    except ValueError:
        return False
    return True

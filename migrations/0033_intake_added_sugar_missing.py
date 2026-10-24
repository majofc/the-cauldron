"""Intake history logged before #104 never knew added sugars: mark them missing
(unknown), never 0. The basis gets it too, so rescaling an old entry keeps it.
For a spell, ``missing`` counts ingredients; how many lacked it is unknowable now,
so 1 stands for "at least one"."""

from django.db import migrations

FIELD = "added_sugar_g"


def mark_missing(apps, schema_editor):
    IntakeEntry = apps.get_model("the_cauldron", "IntakeEntry")
    updated = []
    for entry in IntakeEntry.objects.iterator():
        changed = False
        basis = entry.basis or {}
        if "per_100g" in basis and FIELD not in basis["per_100g"] and FIELD not in basis.get("missing", {}):
            basis.setdefault("missing", {})[FIELD] = 1
            changed = True
        # A 0 g spell/reagent entry has no missing values (totals_from_basis).
        weighed = entry.source != "quick"
        if FIELD not in (entry.nutrients or {}) and FIELD not in (entry.missing or {}) and not (weighed and not entry.grams):
            entry.missing = {**(entry.missing or {}), FIELD: 1}
            changed = True
        if changed:
            entry.basis = basis
            updated.append(entry)
    IntakeEntry.objects.bulk_update(updated, ["basis", "missing"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0032_reagent_added_sugar"),
    ]

    operations = [
        migrations.RunPython(mark_missing, migrations.RunPython.noop),
    ]

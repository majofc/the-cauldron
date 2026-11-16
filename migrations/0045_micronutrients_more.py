# #125: twelve more micronutrients. Older intake entries never knew them: they
# are marked missing (unknown), never 0 — like 0033 did for added sugars.

import django.core.validators
from django.db import migrations, models

NEW = ['thiamin_mg', 'riboflavin_mg', 'niacin_mg', 'pantothenic_mg', 'vit_b6_mg', 'folate_ug', 'vit_e_mg', 'vit_k_ug', 'zinc_mg', 'magnesium_mg', 'selenium_ug', 'phosphorus_mg']


def mark_missing(apps, schema_editor):
    IntakeEntry = apps.get_model("the_cauldron", "IntakeEntry")
    updated = []
    for entry in IntakeEntry.objects.iterator():
        changed = False
        basis = entry.basis or {}
        weighed = entry.source != "quick"
        for field in NEW:
            if "per_100g" in basis and field not in basis["per_100g"] and field not in basis.get("missing", {}):
                basis.setdefault("missing", {})[field] = 1
                changed = True
            # A 0 g spell/reagent entry has no missing values (totals_from_basis).
            if field not in (entry.nutrients or {}) and field not in (entry.missing or {})                     and not (weighed and not entry.grams):
                entry.missing = {**(entry.missing or {}), field: 1}
                changed = True
        if changed:
            entry.basis = basis
            updated.append(entry)
    IntakeEntry.objects.bulk_update(updated, ["basis", "missing"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0044_swap_down_and_rep_ceiling"),
    ]

    operations = [
        migrations.AddField(
            model_name="reagentnutrients",
            name="thiamin_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="riboflavin_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="niacin_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="pantothenic_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="vit_b6_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="folate_ug",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="vit_e_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="vit_k_ug",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="zinc_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="magnesium_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="selenium_ug",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="reagentnutrients",
            name="phosphorus_mg",
            field=models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.RunPython(mark_missing, migrations.RunPython.noop),
    ]

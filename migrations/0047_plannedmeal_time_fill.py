# #132: existing planned meals get their slot's suggested time, then the time is required.

from datetime import time

from django.db import migrations, models

SLOT_TIMES = {"breakfast": time(8, 0), "lunch": time(14, 0), "snack": time(17, 0), "dinner": time(20, 0)}


def fill(apps, schema_editor):
    PlannedMeal = apps.get_model("the_cauldron", "PlannedMeal")
    for slot, at in SLOT_TIMES.items():
        PlannedMeal.objects.filter(time__isnull=True, slot=slot).update(time=at)
    PlannedMeal.objects.filter(time__isnull=True).update(time=time(12, 0))  # unknown slot


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0046_hidden_gauges_planned_time"),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="plannedmeal",
            name="time",
            field=models.TimeField(),
        ),
    ]

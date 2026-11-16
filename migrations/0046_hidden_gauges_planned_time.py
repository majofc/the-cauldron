# #131 hidden Journal gauges; #132 a time on planned meals (nullable until 0047 fills it).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0045_micronutrients_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="cauldronprofile",
            name="journal_hidden_gauges",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="plannedmeal",
            name="time",
            field=models.TimeField(null=True),
        ),
    ]

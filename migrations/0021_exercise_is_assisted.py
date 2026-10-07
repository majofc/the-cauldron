# Adds Exercise.is_assisted (#63). The flag itself is set by ``seed_forge``,
# which runs after ``migrate`` on every boot; no prescription is rewritten —
# existing ones correct themselves on the next completed session.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0020_grip_pattern_backfill"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercise",
            name="is_assisted",
            field=models.BooleanField(
                default=False,
                help_text="True when the band assists the movement; progression moves to "
                "lighter bands.",
            ),
        ),
    ]

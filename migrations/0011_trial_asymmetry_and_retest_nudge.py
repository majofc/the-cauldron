"""Trial asymmetry measurement + the 30-day retest nudge.

Schema:
- ``Exercise.measures_asymmetry`` — the three Trial anchors whose Left/Right
  sides are captured separately.
- ``AssessmentResult.asymmetry_pct`` — signed, right-stronger positive, stored
  so history queries stay a plain read.
- ``UserEquipmentProfile.retest_prompt_dismissed_at`` — banner suppression.

Data:
- Backfill ``asymmetry_pct`` for existing rows that already carry both sides
  (the old Split Squat per-leg capture), so the new Evolution charts show real
  history instead of starting empty for established users.

The catalog itself (new rungs, moved anchors, re-ranked ladders) is owned by
``seed_forge``, which is idempotent and update_or_creates by (pattern, name) —
it is NOT duplicated here.
"""

from django.db import migrations, models


def backfill_asymmetry(apps, schema_editor):
    AssessmentResult = apps.get_model("the_cauldron", "AssessmentResult")
    rows = AssessmentResult.objects.filter(
        left_reps__isnull=False, right_reps__isnull=False
    )
    updates = []
    for row in rows.iterator():
        strongest = max(row.left_reps, row.right_reps)
        if strongest == 0:
            continue  # both zero — an absence of data, not an imbalance
        row.asymmetry_pct = round((row.right_reps - row.left_reps) / strongest * 100)
        updates.append(row)
    if updates:
        AssessmentResult.objects.bulk_update(updates, ["asymmetry_pct"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0010_userequipmentprofile_configured_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercise",
            name="measures_asymmetry",
            field=models.BooleanField(
                default=False,
                help_text="Trial captures Left/Right separately and tracks "
                "signed asymmetry across Trials.",
            ),
        ),
        migrations.AddField(
            model_name="assessmentresult",
            name="asymmetry_pct",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userequipmentprofile",
            name="retest_prompt_dismissed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_asymmetry, migrations.RunPython.noop),
    ]

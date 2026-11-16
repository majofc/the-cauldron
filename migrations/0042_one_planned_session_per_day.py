# #56: one planned WorkoutSession per (user, scheduled_for).

from django.conf import settings
from django.db import migrations, models
from django.db.models import Count, Q


def collapse_duplicate_planned(apps, schema_editor):
    """Keep one planned session per (user, day) before the constraint lands:
    the one with logged values, else the newest. The others become
    ``skipped``; nothing is deleted."""
    WorkoutSession = apps.get_model("the_cauldron", "WorkoutSession")
    SetLog = apps.get_model("the_cauldron", "SetLog")
    groups = (
        WorkoutSession.objects.filter(status="planned")
        .values("user_id", "scheduled_for")
        .annotate(n=Count("uuid"))
        .filter(n__gt=1)
    )
    for group in groups:
        sessions = list(
            WorkoutSession.objects.filter(
                status="planned", user_id=group["user_id"], scheduled_for=group["scheduled_for"]
            ).order_by("-created_at")
        )
        with_values = set(
            SetLog.objects.filter(session__in=sessions)
            .filter(Q(actual_reps__isnull=False) | Q(actual_load__isnull=False))
            .values_list("session_id", flat=True)
        )
        keep = next((s for s in sessions if s.pk in with_values), sessions[0])
        WorkoutSession.objects.filter(pk__in=[s.pk for s in sessions if s.pk != keep.pk]).update(
            status="skipped"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('the_cauldron', '0041_drop_setlog_rir'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(collapse_duplicate_planned, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='workoutsession',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'planned')), fields=('user', 'scheduled_for'), name='one_planned_session_per_day'),
        ),
    ]

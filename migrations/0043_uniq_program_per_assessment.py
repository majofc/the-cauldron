# #26: clean the Program/assessment clones of racing Trial submits, then one
# Program per assessment.

from django.db import migrations, models


def dedupe(apps, schema_editor):
    from the_cauldron.services import dedupe_programs

    Program = apps.get_model("the_cauldron", "Program")
    AssessmentSession = apps.get_model("the_cauldron", "AssessmentSession")
    WorkoutSession = apps.get_model("the_cauldron", "WorkoutSession")
    found = dedupe_programs.plan(Program, AssessmentSession, WorkoutSession)
    Program.objects.filter(pk__in=found["programs"]).delete()
    AssessmentSession.objects.filter(pk__in=found["assessments"]).delete()
    # Pairs that survive (both have sessions, or one is active) keep their data:
    # the active/newest Program keeps the link, the others are unlinked.
    shared = dedupe_programs.shared_links(Program)
    for pks in shared.values():
        Program.objects.filter(pk__in=pks[1:]).update(source_assessment=None)
    if found["programs"] or found["assessments"] or shared:
        print(
            f"\n  dedupe_programs: deleted {len(found['programs'])} program(s), "
            f"{len(found['assessments'])} assessment(s); unlinked {sum(len(p) - 1 for p in shared.values())} "
            "program(s) sharing an assessment"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('the_cauldron', '0042_one_planned_session_per_day'),
    ]

    operations = [
        migrations.RunPython(dedupe, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='program',
            constraint=models.UniqueConstraint(fields=('source_assessment',), name='uniq_program_per_assessment'),
        ),
    ]

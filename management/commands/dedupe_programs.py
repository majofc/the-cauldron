"""Show (or delete) the duplicate Programs and assessments left by racing Trial
submits (#26). Migration 0043 runs the same cleanup; run ``--dry-run`` against
prod before deploying it and review the counts.

Prints counts per user id only (no emails, no names).
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from the_cauldron.models import AssessmentSession, Program, WorkoutSession
from the_cauldron.services import dedupe_programs


class Command(BaseCommand):
    help = "List (--dry-run) or delete duplicate Forge Programs/assessments from racing Trial submits."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only print what would be deleted.")

    def handle(self, *args, dry_run=False, **options):
        found = dedupe_programs.plan(Program, AssessmentSession, WorkoutSession)
        for user_id, (programs, assessments) in sorted(found["per_user"].items()):
            self.stdout.write(f"user {user_id}: {programs} program(s), {assessments} assessment(s)")
        self.stdout.write(
            f"total: {len(found['programs'])} program(s), {len(found['assessments'])} assessment(s)"
            + (" would be deleted" if dry_run else " deleted")
        )
        shared = dedupe_programs.shared_links(Program)
        if shared:
            self.stdout.write(f"assessments still linked from several programs: {len(shared)} "
                              "(the migration keeps the active/newest link and unlinks the rest)")
        if dry_run:
            return
        with transaction.atomic():
            Program.objects.filter(pk__in=found["programs"]).delete()
            AssessmentSession.objects.filter(pk__in=found["assessments"]).delete()

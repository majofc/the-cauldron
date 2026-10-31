"""Align Forge programs whose sets disagree with their weekly volume (#116).

Migration 0038 (#109) set every program's ``weekly_volume_target`` to 16 without
regenerating ``target_sets``, so the first pattern touched afterwards jumped
from 3 to 5 sets and the program ended up mixed. This puts each such active
program back on 3 sets everywhere, with the highest volume that gives 3
(``forge.align_volume``). Aligned programs are left alone.

Idempotent — safe to re-run, and it runs on every container start.
"""

from django.core.management.base import BaseCommand

from the_cauldron.models import Program
from the_cauldron.services import forge


class Command(BaseCommand):
    help = "Align active Forge programs whose sets disagree with their weekly volume."

    def handle(self, *args, **options):
        aligned = sum(
            forge.align_volume(program)
            for program in Program.objects.filter(is_active=True).select_related("user")
        )
        self.stdout.write(f"align_forge_volume: aligned {aligned} program(s).")

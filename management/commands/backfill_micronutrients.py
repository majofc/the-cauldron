"""Fill #125's 12 micronutrients into intake history (all time) wherever an
entry still misses them and its reagent / spell now knows them. Only missing
values are filled, flagged estimated; known values never change. Idempotent.

Run after ``seed_grimoire`` (USDA values) and ``fill_micronutrients``.
Prints counts per user id only.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from the_cauldron.models import IntakeEntry
from the_cauldron.services import breakdown


class Command(BaseCommand):
    help = "Backfill the #125 micronutrients into every user's intake history (missing values only)."

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(
            pk__in=IntakeEntry.objects.values_list("user_id", flat=True).distinct()
        )
        total = 0
        for user in users.iterator():
            n = breakdown.backfill_entries(user)
            if n:
                self.stdout.write(f"user {user.pk}: {n} entr{'y' if n == 1 else 'ies'}")
            total += n
        self.stdout.write(f"total: {total} entries filled")

"""The Cauldron is for adults only (#115).

Every page, API view and MCP tool asks ``blocked(user)`` first. The age comes
from the birth year alone, so it is judged by the youngest the user could be
(this year's birthday may still be ahead) — the rule the recommended goals use.
A blocked account is never deleted; it just can't get in.
"""

from django.utils import timezone

from the_cauldron.models import CauldronProfile
from the_cauldron.services.recommendations import ADULT_AGE

AGE_REQUIRED = "age_required"
UNDERAGE = "underage"


def is_adult(birth_year, today=None):
    """True when someone born in ``birth_year`` is 18 even if their birthday
    hasn't come yet this year."""
    today = today or timezone.localdate()
    return today.year - birth_year - 1 >= ADULT_AGE


def blocked(user, today=None):
    """None when ``user`` may use The Cauldron; else ``AGE_REQUIRED`` (no birth
    year yet) or ``UNDERAGE``."""
    birth_year = CauldronProfile.objects.filter(user=user).values_list("birth_year", flat=True).first()
    if not birth_year:
        return AGE_REQUIRED
    return None if is_adult(birth_year, today) else UNDERAGE

"""DRF permissions shared by The Cauldron's API views."""

from django.urls import reverse
from django.utils.translation import gettext as _
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .services import age_gate


def age_error(code):
    """The ``{"code", "detail", "accept_url"}`` body for a blocked account (#115)."""
    if code == age_gate.AGE_REQUIRED:
        detail = _("Confirm your birth year to keep using The Cauldron.")
    else:
        detail = _("The Cauldron is only for people 18 and older.")
    return {"code": code, "detail": detail, "accept_url": reverse("the_cauldron:age-confirm")}


class AdultOnly(BasePermission):
    """The Cauldron is for adults (#115): 403 ``age_required`` until the birth
    year is given, ``underage`` under 18."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        code = age_gate.blocked(user)
        if code:
            raise PermissionDenied(age_error(code))
        return True

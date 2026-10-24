"""The Cauldron's legal documents and who accepted them (#84).

``CURRENT_VERSIONS`` is the single place the live versions are set. Bump a
version when its page (``templates/the_cauldron/legal/``) changes in a way users
must accept again — the page states the same version and date.

- Terms and privacy notice gate every Cauldron page: a signed-in user whose
  latest accepted version isn't the current one is sent to the acceptance page
  first (``views.cauldron_page``).
- The sensitive-data consent is asked separately, the first time the user opens
  the goals or the meal log. Goals, meal log, plan and adherence need it, and
  withdrawing it blocks them again (``nutrition_api.NutritionConsent``).

Acceptances are append-only (``LegalAcceptance``): accepting again adds a row.
"""

from django.db import transaction
from django.utils import timezone

from the_cauldron.models import CauldronProfile, IntakeEntry, LegalAcceptance, NutrientGoal, PlannedMeal

TERMS = LegalAcceptance.Document.TERMS
PRIVACY = LegalAcceptance.Document.PRIVACY
SENSITIVE_CONSENT = LegalAcceptance.Document.SENSITIVE_CONSENT

CURRENT_VERSIONS = {
    TERMS: "1.3",
    PRIVACY: "1.5",
    SENSITIVE_CONSENT: "1.1",
}
# Accepting these is required to use any part of The Cauldron.
GATED = (TERMS, PRIVACY)


def latest(user, document):
    """The user's most recent acceptance row of ``document``, or None."""
    return (
        LegalAcceptance.objects.filter(user=user, document=document)
        .order_by("-accepted_at", "-created_at")
        .first()
    )


def pending_documents(user):
    """Gated documents the user still has to accept at their current version."""
    pending = []
    for document in GATED:
        row = latest(user, document)
        if row is None or row.version != CURRENT_VERSIONS[document]:
            pending.append(document)
    return pending


def has_sensitive_consent(user):
    """True while the user's latest consent is the current version and not withdrawn."""
    row = latest(user, SENSITIVE_CONSENT)
    return bool(row and row.withdrawn_at is None and row.version == CURRENT_VERSIONS[SENSITIVE_CONSENT])


def accept(user, documents):
    """Record an acceptance of the current version of each document. A
    document already accepted at its current version (and, for the consent,
    not withdrawn) is left alone — no duplicate rows."""
    created = []
    with transaction.atomic():
        for document in documents:
            row = latest(user, document)
            current = CURRENT_VERSIONS[document]
            if row and row.version == current and row.withdrawn_at is None:
                continue
            created.append(LegalAcceptance.objects.create(user=user, document=document, version=current))
    return created


def withdraw_consent(user, *, delete_data=False):
    """Withdraw the sensitive-data consent: logging stops at once. With
    ``delete_data`` the goals, meal log and plan are deleted too, and the
    weight and activity level are cleared (#105; birth year and sex stay, the
    Forge's peer score uses them without the consent)."""
    with transaction.atomic():
        # The one permitted change to an acceptance row: closing a consent.
        withdrawn = LegalAcceptance.objects.filter(
            user=user, document=SENSITIVE_CONSENT, withdrawn_at__isnull=True
        ).update(withdrawn_at=timezone.now())
        if delete_data:
            PlannedMeal.objects.filter(user=user).delete()
            IntakeEntry.objects.filter(user=user).delete()
            NutrientGoal.objects.filter(user=user).delete()
            CauldronProfile.objects.filter(user=user).update(
                weight_kg=None, activity_level="", updated_at=timezone.now()
            )
    return withdrawn


def status(user):
    """What the API and the pages show: current versions, the user's latest
    acceptance of each document, what's pending, and the consent state."""
    accepted = {}
    for document in CURRENT_VERSIONS:
        row = latest(user, document)
        accepted[document] = (
            {"version": row.version, "accepted_at": row.accepted_at, "withdrawn_at": row.withdrawn_at}
            if row else None
        )
    return {
        "current": dict(CURRENT_VERSIONS),
        "accepted": accepted,
        "pending": pending_documents(user),
        "sensitive_consent": has_sensitive_consent(user),
    }

"""Keep a live program consistent with the equipment its owner actually has.

Equipment is edited from two places — the Forge's own profile endpoint and the
Django admin — and both land on the same ``UserEquipmentProfile.equipment`` M2M.
Reconciling here covers both writers at once, so a program is repaired when the
gear changes rather than on every page view.
"""

from django.db.models.signals import m2m_changed, pre_delete
from django.dispatch import receiver

from the_cauldron.models import Casting, Spell, UserEquipmentProfile
from the_cauldron.services import forge


@receiver(m2m_changed, sender=UserEquipmentProfile.equipment.through)
def reconcile_program_on_equipment_change(sender, instance, action, reverse, **kwargs):
    """Swap out prescriptions the user can no longer perform after a gear edit."""
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if reverse:
        # Editing from the Equipment side touches many profiles at once; those
        # programs are reconciled on their next read/session instead.
        return
    forge.sync_program_equipment(instance.user)


@receiver(pre_delete, sender=Spell)
def delete_authors_castings(sender, instance, **kwargs):
    """Deleting a spell deletes its owner's castings of it (#68). Other users'
    castings are kept: the FK is SET_NULL and they show as "spell removed"."""
    Casting.objects.filter(spell=instance, user_id=instance.owner_id).delete()

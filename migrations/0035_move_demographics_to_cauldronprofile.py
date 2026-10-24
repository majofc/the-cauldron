"""Birth year and sex move from the Forge's equipment profile to the Cauldron
profile (#105): one place for body data, read by the Forge's peer score and the
recommended goals. A Cauldron profile already holding a value keeps it."""

from django.db import migrations
from django.db.models import Q


def move(apps, schema_editor):
    UserEquipmentProfile = apps.get_model("the_cauldron", "UserEquipmentProfile")
    CauldronProfile = apps.get_model("the_cauldron", "CauldronProfile")
    rows = UserEquipmentProfile.objects.filter(Q(birth_year__isnull=False) | ~Q(sex="undisclosed"))
    for equipment in rows.iterator():
        profile, _ = CauldronProfile.objects.get_or_create(user_id=equipment.user_id)
        changed = []
        if profile.birth_year is None and equipment.birth_year:
            profile.birth_year = equipment.birth_year
            changed.append("birth_year")
        if profile.sex == "undisclosed" and equipment.sex != "undisclosed":
            profile.sex = equipment.sex
            changed.append("sex")
        if changed:
            profile.save(update_fields=changed)


def move_back(apps, schema_editor):
    UserEquipmentProfile = apps.get_model("the_cauldron", "UserEquipmentProfile")
    CauldronProfile = apps.get_model("the_cauldron", "CauldronProfile")
    for profile in CauldronProfile.objects.filter(Q(birth_year__isnull=False) | ~Q(sex="undisclosed")):
        UserEquipmentProfile.objects.filter(user_id=profile.user_id).update(
            birth_year=profile.birth_year, sex=profile.sex
        )


class Migration(migrations.Migration):

    dependencies = [
        ("the_cauldron", "0034_cauldronprofile_body_data"),
    ]

    operations = [
        migrations.RunPython(move, move_back),
    ]

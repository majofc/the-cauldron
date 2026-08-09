"""Join the two 0011 branches: Trial asymmetry / retest nudge and the loadable
equipment inventory (bar weight, kettlebells, plate-loaded modes).

Both branches only add fields to disjoint parts of the schema, so there is
nothing to reconcile — this migration exists purely to give the app a single
leaf again.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('the_cauldron', '0011_trial_asymmetry_and_retest_nudge'),
        ('the_cauldron', '0011_userequipmentprofile_bar_weight_and_more'),
    ]

    operations = [
    ]

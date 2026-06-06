"""Pytest bootstrap for The Cauldron tests.

The parent repo has no global pytest config, so point pytest-django at the
project settings here. Scoped to this app's test tree.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arcano.settings")
django.setup()

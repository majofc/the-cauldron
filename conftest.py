"""Pytest config for The Cauldron tests.

Django settings come from the parent repo's root ``pytest.ini``.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "age_gate: run with the real adults-only gate (#115)")


@pytest.fixture(autouse=True)
def _adults_by_default(request, monkeypatch):
    """Test users are made without a birth year, which the age gate (#115)
    would stop at the door. Every test runs as an adult unless it's marked
    ``age_gate`` to exercise the gate itself."""
    if request.node.get_closest_marker("age_gate"):
        return
    from the_cauldron.services import age_gate

    monkeypatch.setattr(age_gate, "blocked", lambda user, today=None: None)

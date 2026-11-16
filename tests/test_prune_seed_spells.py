"""prune_seed_spells (#126): starter spells the owner never used are deleted;
history keeps its snapshots; reseeding never recreates what the file dropped.
Plus the stopwatch labels without glyphs (#133)."""

import json
from datetime import date
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from the_cauldron.forge_i18n import forge_strings
from the_cauldron.management.commands import prune_seed_spells as prune
from the_cauldron.models import Casting, IntakeEntry, PlannedMeal, Spell

User = get_user_model()
OWNER = "owner@example.com"


@pytest.fixture
def seeded(db):
    call_command("seed_grimoire", stdout=StringIO())


@pytest.fixture
def owner(db):
    return User.objects.create_user(username="owner", email=OWNER, password="pw12345!")


def _spell(title):
    return Spell.objects.get(owner__username="the-grimoire", title=title)


def _run(*args):
    out = StringIO()
    call_command("prune_seed_spells", "--owner-email", OWNER, *args, stdout=out)
    return out.getvalue()


def test_dry_run_lists_keep_and_delete_and_writes_nothing(seeded, owner):
    cast = _spell("Classic Guacamole")
    Casting.objects.create(user=owner, spell=cast, spell_title=cast.title, cast_on=date.today())
    before = Spell.objects.count()
    out = _run("--dry-run")
    assert "keep    Classic Guacamole  (cast)" in out
    assert "delete  Banana Oat Pancakes" in out
    assert "owner@" not in out and "owner " not in out.replace("by the owner", "")
    assert Spell.objects.count() == before


def test_keeps_cast_logged_planned_forked_and_deletes_the_rest(seeded, owner):
    other = User.objects.create_user(username="other", email="other@example.com", password="pw12345!")
    stew, curry, oats, cake, salad = (_spell(t) for t in (
        "Smoked Paprika Lentil Stew", "Chickpea & Spinach Coconut Curry", "Overnight Oats with Blueberries",
        "Dark Cocoa Mug Cake", "Crisp Romaine Salad with Lemon Dressing"))
    Casting.objects.create(user=owner, spell=stew, spell_title=stew.title, cast_on=date.today())
    entry = IntakeEntry.objects.create(user=owner, eaten_at=timezone.now(), local_date=date.today(), source="spell",
                                       spell=curry, label=curry.title, quantity=1, unit="serving")
    PlannedMeal.objects.create(user=owner, spell=oats, spell_title=oats.title, date=date.today(), slot="breakfast",
                               quantity=1)
    Spell.objects.create(owner=owner, title="My cake", servings=1, forked_from=cake)
    # Another user's use doesn't count; their fork survives.
    other_cast = Casting.objects.create(user=other, spell=salad, spell_title=salad.title, cast_on=date.today())
    other_fork = Spell.objects.create(owner=other, title="Their salad", servings=1, forked_from=salad)

    _run()
    left = set(prune.seed_spells().values_list("title", flat=True))
    assert left == {stew.title, curry.title, oats.title, cake.title}
    other_cast.refresh_from_db()
    other_fork.refresh_from_db()
    assert other_cast.spell is None and other_cast.spell_title == salad.title
    assert other_fork.forked_from is None and Spell.objects.filter(pk=other_fork.pk).exists()
    entry.refresh_from_db()
    assert entry.spell == curry


def test_history_keeps_titles_of_deleted_spells(seeded, owner):
    pancakes = _spell("Banana Oat Pancakes")
    other = User.objects.create_user(username="eater", email="eater@example.com", password="pw12345!")
    entry = IntakeEntry.objects.create(user=other, eaten_at=timezone.now(), local_date=date.today(), source="spell",
                                       spell=pancakes, label=pancakes.title, quantity=1, unit="serving")
    _run()
    entry.refresh_from_db()
    assert entry.spell is None and entry.label == "Banana Oat Pancakes"


def test_seed_file_rewrite_stops_recreation(seeded, owner, tmp_path, monkeypatch):
    spells_file = tmp_path / prune.SPELLS_FILE
    original = (prune.SEED_DIR / prune.SPELLS_FILE).read_text(encoding="utf-8")
    spells_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(prune, "SEED_DIR", tmp_path)
    keep = _spell("Classic Guacamole")
    Casting.objects.create(user=owner, spell=keep, spell_title=keep.title, cast_on=date.today())
    _run("--write-seed-file")
    kept = [s["title"] for s in json.loads(spells_file.read_text(encoding="utf-8"))["spells"]]
    assert kept == ["Classic Guacamole"]
    # Reseeding from the rewritten file recreates nothing.
    from the_cauldron.management.commands import seed_grimoire

    real_load = seed_grimoire._load
    monkeypatch.setattr(seed_grimoire, "_load", lambda name: (
        json.loads(spells_file.read_text(encoding="utf-8")) if name == prune.SPELLS_FILE else real_load(name)))
    call_command("seed_grimoire", stdout=StringIO())
    assert list(prune.seed_spells().values_list("title", flat=True)) == ["Classic Guacamole"]


def test_needs_an_owner(seeded, monkeypatch):
    monkeypatch.delenv("SUPERADMIN_EMAIL", raising=False)
    with pytest.raises(CommandError):
        call_command("prune_seed_spells", "--owner-email", "", stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("prune_seed_spells", "--owner-email", "nobody@example.com", stdout=StringIO())


def test_stopwatch_labels_carry_no_glyph(db):
    strings = forge_strings()
    assert strings["sw_start"] == "Start" and strings["sw_stop"] == "Stop"

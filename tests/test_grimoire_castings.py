"""Grimoire castings (#68): log each time a spell is made — score, notes,
adjusted quantities — without ever changing the spell."""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework.test import APIClient

from the_cauldron.models import Casting, Reagent, ReagentNutrients, Spell, SpellReagent
from the_cauldron.services import legal

User = get_user_model()
API = "/cauldron/api/grimoire/"


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


@pytest.fixture
def alice(db):
    user = User.objects.create_user(username="alice", email="alice@example.com", password="pw12345!")
    legal.accept(user, legal.GATED)  # past the terms gate (#84) for the page tests
    return user


@pytest.fixture
def bob(db):
    return User.objects.create_user(username="bob", email="bob@example.com", password="pw12345!")


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def as_alice(alice):
    return _client(alice)


@pytest.fixture
def as_bob(bob):
    return _client(bob)


@pytest.fixture
def onion(db):
    r = Reagent.objects.create(name="Onion", status=Reagent.Status.APPROVED, grams_per_unit={"piece": 110})
    ReagentNutrients.objects.create(reagent=r, kcal=40)
    return r


def _spell(owner, onion, visibility="private", title="Soup"):
    spell = Spell.objects.create(owner=owner, title=title, visibility=visibility)
    SpellReagent.objects.create(spell=spell, reagent=onion, quantity=1, unit="piece", grams=110, order=0)
    return spell


def _payload(**extra):
    return {"cast_on": "2026-10-10", "score": 4, "notes": "Good.", "adjustments": [], "substitutions": "", **extra}


def _cast(client, spell, **extra):
    resp = client.post(f"{API}spells/{spell.uuid}/castings/", _payload(**extra), format="json")
    assert resp.status_code == 201, resp.content
    return resp.json()


class TestLogging:
    def test_log_a_casting_with_adjustments(self, as_alice, alice, onion):
        spell = _spell(alice, onion)
        adj = [{"reagent": str(onion.uuid), "reagent_name": "Onion", "original_qty": 1, "used_qty": 2, "unit": "piece"}]
        data = _cast(as_alice, spell, adjustments=adj, substitutions="Lime instead of lemon")
        assert (data["cast_on"], data["score"], data["notes"]) == ("2026-10-10", 4, "Good.")
        assert data["adjustments"] == [{
            **adj[0], "line": None, "original_qty": 1.0, "original_unit": None, "used_qty": 2.0,
        }]
        assert data["substitutions"] == "Lime instead of lemon"
        assert data["spell"] == str(spell.uuid) and data["spell_available"] is True

    def test_adjustments_never_change_the_spell(self, as_alice, alice, onion):
        spell = _spell(alice, onion)
        adj = [{"reagent": str(onion.uuid), "reagent_name": "Onion", "original_qty": 1, "used_qty": 3, "unit": "g"}]
        _cast(as_alice, spell, adjustments=adj)
        line = SpellReagent.objects.get(spell=spell)
        assert (line.quantity, line.unit, line.grams) == (1, "piece", 110)

    def test_any_public_spell_can_be_cast(self, as_bob, alice, onion):
        public = _spell(alice, onion, visibility="public")
        assert _cast(as_bob, public)["spell_available"] is True

    def test_someone_elses_private_spell_cannot_be_cast(self, as_bob, alice, onion):
        private = _spell(alice, onion)
        resp = as_bob.post(f"{API}spells/{private.uuid}/castings/", _payload(), format="json")
        assert resp.status_code == 404

    @pytest.mark.parametrize("bad", [
        {"score": 0}, {"score": 6}, {"cast_on": "not-a-date"},
        {"adjustments": [{"reagent_name": "Onion", "used_qty": "nan", "unit": "g"}]},
        {"adjustments": [{"reagent_name": "Onion", "used_qty": 1, "unit": "bucket"}]},
        {"adjustments": [{"reagent_name": "", "used_qty": 1, "unit": "g"}]},
        {"adjustments": "two onions"},
        {"adjustments": [{"reagent_name": "Onion", "used_qty": True, "unit": "g"}]},
        {"adjustments": [{"reagent_name": "Onion", "used_qty": 1, "unit": "g", "line": -1}]},
        {"adjustments": [{"reagent_name": "Onion", "used_qty": 1, "unit": "g", "original_unit": "bucket"}]},
        {"notes": "x" * 5001},
    ])
    def test_invalid_input_is_a_400(self, as_alice, alice, onion, bad):
        spell = _spell(alice, onion)
        resp = as_alice.post(f"{API}spells/{spell.uuid}/castings/", _payload(**bad), format="json")
        assert resp.status_code == 400

    def test_same_reagent_twice_keeps_both_changes_apart(self, as_alice, alice, onion):
        spell = _spell(alice, onion)
        SpellReagent.objects.create(spell=spell, reagent=onion, quantity=2, unit="piece", grams=220, order=1)
        adj = [
            {"line": 1, "reagent": str(onion.uuid), "reagent_name": "Onion", "original_qty": 2,
             "original_unit": "piece", "used_qty": 3, "unit": "piece"},
            {"line": 0, "reagent": str(onion.uuid), "reagent_name": "Onion", "original_qty": 1,
             "original_unit": "piece", "used_qty": 1, "unit": "tbsp"},
        ]
        data = _cast(as_alice, spell, adjustments=adj)
        assert [(a["line"], a["used_qty"], a["unit"], a["original_unit"]) for a in data["adjustments"]] == [
            (1, 3.0, "piece", "piece"), (0, 1.0, "tbsp", "piece"),
        ]

    def test_score_is_optional_and_extra_keys_are_dropped(self, as_alice, alice, onion):
        spell = _spell(alice, onion)
        adj = [{"reagent_name": "Onion", "used_qty": 1, "unit": "g", "evil": "<script>"}]
        data = _cast(as_alice, spell, score=None, adjustments=adj)
        assert data["score"] is None
        assert "evil" not in data["adjustments"][0]


class TestPrivacy:
    def test_users_only_see_and_edit_their_own_castings(self, as_alice, as_bob, alice, onion):
        public = _spell(alice, onion, visibility="public")
        mine = _cast(as_alice, public)
        _cast(as_bob, public, notes="Bob's")
        listed = as_alice.get(f"{API}spells/{public.uuid}/castings/").json()
        assert [c["uuid"] for c in listed] == [mine["uuid"]]
        assert [c["notes"] for c in as_bob.get(f"{API}castings/").json()] == ["Bob's"]
        url = f"{API}castings/{mine['uuid']}/"
        assert as_bob.get(url).status_code == 404
        assert as_bob.patch(url, {"notes": "hijacked"}, format="json").status_code == 404
        assert as_bob.delete(url).status_code == 404
        assert Casting.objects.get(uuid=mine["uuid"]).notes == "Good."

    def test_owner_updates_and_deletes(self, as_alice, alice, onion):
        spell = _spell(alice, onion)
        c = _cast(as_alice, spell)
        url = f"{API}castings/{c['uuid']}/"
        assert as_alice.patch(url, {"score": 5, "notes": "Better"}, format="json").json()["score"] == 5
        assert as_alice.delete(url).status_code == 204
        assert not Casting.objects.filter(uuid=c["uuid"]).exists()

    def test_spell_cannot_be_reassigned(self, as_alice, alice, onion):
        a, b = _spell(alice, onion, title="A"), _spell(alice, onion, title="B")
        c = _cast(as_alice, a)
        as_alice.patch(f"{API}castings/{c['uuid']}/", {"spell": str(b.uuid)}, format="json")
        assert Casting.objects.get(uuid=c["uuid"]).spell == a


class TestStats:
    def test_spell_shows_the_users_count_average_and_last_date(self, as_alice, as_bob, alice, onion):
        public = _spell(alice, onion, visibility="public")
        _cast(as_alice, public, score=4, cast_on="2026-10-01")
        _cast(as_alice, public, score=5, cast_on="2026-10-09")
        _cast(as_alice, public, score=None, cast_on="2026-10-05")
        _cast(as_bob, public, score=1, cast_on="2026-10-11")  # not alice's
        data = as_alice.get(f"{API}spells/{public.uuid}/").json()
        assert (data["my_casting_count"], data["my_average_score"], data["my_last_cast_on"]) == (3, 4.5, "2026-10-09")
        listed = as_alice.get(f"{API}spells/{public.uuid}/castings/").json()
        assert [c["cast_on"] for c in listed] == ["2026-10-09", "2026-10-05", "2026-10-01"]  # newest first

    def test_stats_survive_a_tag_filter_join(self, as_alice, alice, onion):
        from the_cauldron.models import Tag
        spell = _spell(alice, onion)
        spell.tags.set([Tag.objects.create(slug="soup", name="Soup"), Tag.objects.create(slug="main", name="Main")])
        _cast(as_alice, spell)
        row = as_alice.get(f"{API}spells/?tag=soup").json()[0]
        assert row["my_casting_count"] == 1

    def test_no_castings_reads_zero(self, as_alice, alice, onion):
        spell = _spell(alice, onion)
        data = as_alice.get(f"{API}spells/{spell.uuid}/").json()
        assert (data["my_casting_count"], data["my_average_score"], data["my_last_cast_on"]) == (0, None, None)


class TestSpellDeletion:
    def test_deleting_a_spell_deletes_its_owners_castings_and_keeps_others(self, as_alice, as_bob, alice, onion):
        public = _spell(alice, onion, visibility="public", title="Stew")
        _cast(as_alice, public)
        theirs = _cast(as_bob, public)
        assert as_alice.delete(f"{API}spells/{public.uuid}/").status_code == 204
        assert not Casting.objects.filter(user=alice).exists()
        kept = as_bob.get(f"{API}castings/{theirs['uuid']}/").json()
        assert (kept["spell"], kept["spell_available"], kept["spell_title"]) == (None, False, "Stew")

    def test_title_follows_the_spell_while_it_is_readable(self, as_bob, alice, onion):
        public = _spell(alice, onion, visibility="public", title="Soup")
        c = _cast(as_bob, public)
        public.title, public.title_es = "Tomato Soup", "Sopa de jitomate"
        public.save()
        data = as_bob.get(f"{API}castings/{c['uuid']}/").json()
        assert (data["spell_title"], data["spell_title_es"]) == ("Tomato Soup", "Sopa de jitomate")

    def test_a_spell_made_private_reads_as_removed(self, as_bob, alice, onion):
        public = _spell(alice, onion, visibility="public", title="Stew")
        c = _cast(as_bob, public)
        public.visibility = "private"
        public.save()
        kept = as_bob.get(f"{API}castings/{c['uuid']}/").json()
        assert (kept["spell"], kept["spell_available"], kept["spell_title"]) == (None, False, "Stew")


class TestPages:
    def test_cast_and_edit_pages(self, alice, bob, onion):
        public = _spell(alice, onion, visibility="public")
        private = _spell(bob, onion)
        c = Client()
        c.force_login(alice)
        resp = c.get(f"/cauldron/grimoire/spell/{public.uuid}/cast/")
        assert resp.status_code == 200 and 'page: "cast"' in resp.content.decode()
        assert c.get(f"/cauldron/grimoire/spell/{private.uuid}/cast/").status_code == 404
        mine = Casting.objects.create(spell=public, spell_title="Soup", user=alice, cast_on=date(2026, 10, 1))
        theirs = Casting.objects.create(spell=public, spell_title="Soup", user=bob, cast_on=date(2026, 10, 1))
        assert c.get(f"/cauldron/grimoire/casting/{mine.uuid}/edit/").status_code == 200
        assert c.get(f"/cauldron/grimoire/casting/{theirs.uuid}/edit/").status_code == 404

    def test_edit_page_never_shows_a_now_private_spells_title(self, alice, bob, onion):
        public = _spell(bob, onion, visibility="public", title="Stew")
        mine = Casting.objects.create(spell=public, spell_title="Stew", user=alice, cast_on=date(2026, 10, 1))
        public.visibility, public.title = "private", "Secret new name"
        public.save()
        c = Client()
        c.force_login(alice)
        html = c.get(f"/cauldron/grimoire/casting/{mine.uuid}/edit/").content.decode()
        assert "Secret new name" not in html and "Stew" in html

    def test_list_page_has_a_castings_tab(self, alice):
        c = Client()
        c.force_login(alice)
        assert 'data-tab="castings"' in c.get("/cauldron/grimoire/").content.decode()

"""The Grimoire core (#67): spells CRUD, permissions, publish, fork, reagent
visibility/search, unit conversion, the page + nav, and the seed command."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from rest_framework.test import APIClient

from the_cauldron.models import (
    GrimoirePreferences,
    Reagent,
    ReagentNutrients,
    Spell,
    SpellReagent,
    Tag,
)
from the_cauldron.services import grimoire, legal

User = get_user_model()
API = "/cauldron/api/grimoire/"


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Render templates without the hashed-manifest storage (see test_forge_urls)."""
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


def _reagent(name, status=Reagent.Status.APPROVED, created_by=None, **extra):
    r = Reagent.objects.create(name=name, status=status, created_by=created_by, **extra)
    ReagentNutrients.objects.create(reagent=r, kcal=100, protein_g=5)
    return r


@pytest.fixture
def flour(db):
    return _reagent("Flour", name_es="Harina", grams_per_unit={"cup": 125}, density_g_per_ml=0.53)


@pytest.fixture
def milk(db):
    return _reagent("Milk", density_g_per_ml=1.03)


@pytest.fixture
def tags(db):
    return [Tag.objects.create(slug=s, name=s.title()) for s in ("breakfast", "vegan")]


def _spell_payload(*lines, **extra):
    return {
        "title": "Pancakes",
        "description": "Fluffy.",
        "servings": 2,
        "prep_minutes": 5,
        "cook_minutes": 10,
        "steps": ["Mix", "  ", "Cook"],
        "tags": ["breakfast"],
        "lines": [
            {"reagent": str(r.uuid), "quantity": q, "unit": u, "note": ""} for r, q, u in lines
        ],
        **extra,
    }


def _create(client, *lines, **extra):
    resp = client.post(f"{API}spells/", _spell_payload(*lines, **extra), format="json")
    assert resp.status_code == 201, resp.content
    return resp.json()


# ─── Unit conversion ─────────────────────────────────────────────────────────


class TestToGrams:
    def test_mass_units(self, flour):
        assert grimoire.to_grams(flour, 2, "kg") == 2000
        assert grimoire.to_grams(flour, 1, "lb") == pytest.approx(453.59, abs=0.01)

    def test_household_unit_prefers_measured_weight(self, flour):
        assert grimoire.to_grams(flour, 2, "cup") == 250  # 125 g per cup, not density

    def test_volume_falls_back_to_density(self, flour, milk):
        assert grimoire.to_grams(milk, 100, "ml") == 103
        assert grimoire.to_grams(flour, 1, "tbsp") == pytest.approx(14.7868 * 0.53, abs=0.01)

    def test_unconvertible_raises(self, db):
        bare = _reagent("Mystery")
        with pytest.raises(grimoire.ConversionError):
            grimoire.to_grams(bare, 1, "piece")
        with pytest.raises(grimoire.ConversionError):
            grimoire.to_grams(bare, 1, "cup")


# ─── Spell CRUD ──────────────────────────────────────────────────────────────


class TestSpellCrud:
    def test_requires_auth(self, db):
        assert APIClient().get(f"{API}spells/").status_code == 403

    def test_create_computes_grams_and_cleans_steps(self, as_alice, flour, milk, tags):
        data = _create(as_alice, (flour, 1, "cup"), (milk, 250, "ml"))
        assert [l["grams"] for l in data["lines"]] == [125, 257.5]
        assert data["steps"] == ["Mix", "Cook"]
        assert data["tags"] == ["breakfast"]
        assert data["visibility"] == "private"
        assert data["is_mine"] is True

    def test_visibility_cannot_be_set_on_write(self, as_alice, flour, tags):
        data = _create(as_alice, (flour, 100, "g"), visibility="public")
        assert data["visibility"] == "private"

    @pytest.mark.parametrize("qty", [0, None, "nan", "Infinity"])
    def test_line_needs_a_positive_amount(self, as_alice, flour, tags, qty):
        payload = _spell_payload((flour, 1, "g"))
        payload["lines"][0]["quantity"] = qty
        assert as_alice.post(f"{API}spells/", payload, format="json").status_code == 400

    def test_description_is_bounded(self, as_alice, flour, tags):
        payload = _spell_payload((flour, 1, "g"), description="x" * 5001)
        assert as_alice.post(f"{API}spells/", payload, format="json").status_code == 400

    def test_unconvertible_line_is_a_400(self, as_alice, db, tags):
        bare = _reagent("Mystery")
        resp = as_alice.post(f"{API}spells/", _spell_payload((bare, 1, "piece")), format="json")
        assert resp.status_code == 400
        assert "lines" in resp.json()

    def test_edit_replaces_lines_and_tags(self, as_alice, flour, milk, tags):
        data = _create(as_alice, (flour, 1, "cup"))
        payload = _spell_payload((milk, 1, "l"), title="Milk", tags=["vegan"])
        resp = as_alice.put(f"{API}spells/{data['uuid']}/", payload, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Milk"
        assert body["tags"] == ["vegan"]
        assert [(l["reagent_name"], l["grams"]) for l in body["lines"]] == [("Milk", 1030)]
        assert SpellReagent.objects.filter(spell_id=data["uuid"]).count() == 1

    def test_delete(self, as_alice, flour, tags):
        data = _create(as_alice, (flour, 100, "g"))
        assert as_alice.delete(f"{API}spells/{data['uuid']}/").status_code == 204
        assert not Spell.objects.filter(uuid=data["uuid"]).exists()

    def test_filters(self, as_alice, flour, tags):
        _create(as_alice, (flour, 100, "g"), title="Crêpes au sucre")
        _create(as_alice, (flour, 100, "g"), title="Bread", tags=["vegan"])
        titles = lambda q: [s["title"] for s in as_alice.get(f"{API}spells/{q}").json()]  # noqa: E731
        assert titles("?q=CREPES") == ["Crêpes au sucre"]  # case + accent insensitive
        assert titles("?tag=vegan") == ["Bread"]


# ─── Permissions ─────────────────────────────────────────────────────────────


class TestSpellPermissions:
    def test_private_spell_is_invisible_to_others(self, as_alice, as_bob, flour, tags):
        data = _create(as_alice, (flour, 100, "g"))
        url = f"{API}spells/{data['uuid']}/"
        assert as_bob.get(url).status_code == 404
        assert as_bob.put(url, _spell_payload((flour, 1, "g")), format="json").status_code == 404
        assert as_bob.delete(url).status_code == 404
        assert as_bob.post(f"{url}publish/").status_code == 404
        assert as_bob.post(f"{url}fork/").status_code == 404
        assert as_bob.get(f"{API}spells/").json() == []

    def test_public_spell_is_read_only_for_others(self, as_alice, as_bob, flour, tags):
        data = _create(as_alice, (flour, 100, "g"))
        url = f"{API}spells/{data['uuid']}/"
        as_alice.post(f"{url}publish/")
        assert as_bob.get(url).status_code == 200
        assert as_bob.get(url).json()["is_mine"] is False
        assert as_bob.put(url, _spell_payload((flour, 1, "g")), format="json").status_code == 403
        assert as_bob.patch(url, {"title": "Mine now"}, format="json").status_code == 403
        assert as_bob.delete(url).status_code == 403
        assert as_bob.post(f"{url}unpublish/").status_code == 403
        assert Spell.objects.get(uuid=data["uuid"]).title == "Pancakes"


class TestPublish:
    def test_publish_and_unpublish(self, as_alice, as_bob, flour, tags):
        data = _create(as_alice, (flour, 100, "g"))
        url = f"{API}spells/{data['uuid']}/"
        assert as_alice.post(f"{url}publish/").json()["visibility"] == "public"
        public = as_bob.get(f"{API}spells/?scope=public").json()
        assert [s["uuid"] for s in public] == [data["uuid"]]
        assert as_bob.get(f"{API}spells/?scope=mine").json() == []

        assert as_alice.post(f"{url}unpublish/").json()["visibility"] == "private"
        assert as_bob.get(f"{API}spells/?scope=public").json() == []

    def test_unverified_reagent_flags_the_spell(self, as_alice, alice, flour, tags):
        mine = _reagent("Grandma's spice", status=Reagent.Status.PRIVATE, created_by=alice)
        data = _create(as_alice, (flour, 100, "g"), (mine, 5, "g"))
        assert data["has_unverified_reagents"] is True
        assert _create(as_alice, (flour, 100, "g"))["has_unverified_reagents"] is False


class TestScopes:
    """All / My spells / The Coven: one list, the user's own matches first."""

    def test_all_lists_own_spells_before_the_coven(self, as_alice, as_bob, flour, tags):
        theirs = _create(as_bob, (flour, 1, "g"), title="Bean pot")
        as_bob.post(f"{API}spells/{theirs['uuid']}/publish/")
        mine = _create(as_alice, (flour, 1, "g"), title="Bean soup")
        # Bob's spell is newer by updated_at, but Alice's own still comes first.
        as_bob.post(f"{API}spells/{theirs['uuid']}/publish/")
        listed = as_alice.get(f"{API}spells/?q=bean").json()
        assert [x["uuid"] for x in listed] == [mine["uuid"], theirs["uuid"]]
        assert [x["uuid"] for x in as_alice.get(f"{API}spells/?scope=all&q=bean").json()] == [
            mine["uuid"], theirs["uuid"],
        ]

    def test_coven_is_other_users_shared_spells(self, as_alice, as_bob, flour, tags):
        theirs = _create(as_bob, (flour, 1, "g"))
        as_bob.post(f"{API}spells/{theirs['uuid']}/publish/")
        mine = _create(as_alice, (flour, 1, "g"))
        as_alice.post(f"{API}spells/{mine['uuid']}/publish/")
        coven = [x["uuid"] for x in as_alice.get(f"{API}spells/?scope=coven").json()]
        assert coven == [theirs["uuid"]]  # own shared spells live under "My spells"
        assert [x["uuid"] for x in as_alice.get(f"{API}spells/?scope=mine").json()] == [mine["uuid"]]

    def test_search_matches_spanish_titles(self, as_alice, alice, db):
        Spell.objects.create(owner=alice, title="Classic Guacamole", title_es="Guacamole clásico")
        Spell.objects.create(owner=alice, title="Lentil stew", title_es="Guiso de lentejas")
        titles = [x["title"] for x in as_alice.get(f"{API}spells/?q=GUISO").json()]
        assert titles == ["Lentil stew"]


class TestFork:
    def test_fork_is_an_independent_private_copy(self, as_alice, as_bob, bob, flour, milk, tags):
        original = _create(as_alice, (flour, 1, "cup"))
        url = f"{API}spells/{original['uuid']}/"
        as_alice.post(f"{url}publish/")

        resp = as_bob.post(f"{url}fork/")
        assert resp.status_code == 201
        fork = resp.json()
        assert fork["is_mine"] is True
        assert fork["visibility"] == "private"
        assert fork["forked_from"] == {"uuid": original["uuid"], "title": "Pancakes", "title_es": ""}
        assert Spell.objects.get(uuid=fork["uuid"]).owner == bob

        # The original changes, then goes private: the fork must not move.
        as_alice.put(url, _spell_payload((milk, 1, "l"), title="Changed", tags=["vegan"]), format="json")
        as_alice.post(f"{url}unpublish/")
        fork_now = as_bob.get(f"{API}spells/{fork['uuid']}/").json()
        assert fork_now["title"] == "Pancakes"
        assert fork_now["tags"] == ["breakfast"]
        assert [(l["reagent_name"], l["grams"]) for l in fork_now["lines"]] == [("Flour", 125)]
        assert fork_now["forked_from"] == {"uuid": None, "title": None, "title_es": None}  # source no longer readable

        # Deleting the original leaves the fork too.
        as_alice.delete(url)
        assert Spell.objects.filter(uuid=fork["uuid"], forked_from__isnull=True).exists()

    def test_fork_is_written_in_the_forkers_language(self, as_alice, as_bob, bob, alice, db):
        src = Spell.objects.create(
            owner=alice, visibility="public", title="Classic Guacamole", title_es="Guacamole clásico",
            description="Lime and onion.", description_es="Limón y cebolla.",
            steps=["Mash."], steps_es=["Machaca."],
        )
        es = as_bob.post(f"{API}spells/{src.uuid}/fork/", HTTP_ACCEPT_LANGUAGE="es").json()
        assert (es["title"], es["description"], es["steps"]) == ("Guacamole clásico", "Limón y cebolla.", ["Machaca."])
        assert (es["title_es"], es["steps_es"]) == ("", [])  # the fork is one-language, the user's own
        SpellReagent.objects.create(
            spell=src, reagent=_reagent("Onion"), quantity=1, unit="g", grams=1, note="diced", note_es="en cubitos",
        )
        es_lines = as_bob.post(f"{API}spells/{src.uuid}/fork/", HTTP_ACCEPT_LANGUAGE="es").json()["lines"]
        assert (es_lines[0]["note"], es_lines[0]["note_es"]) == ("en cubitos", "")
        en = as_bob.post(f"{API}spells/{src.uuid}/fork/", HTTP_ACCEPT_LANGUAGE="en").json()
        assert en["lines"][0]["note"] == "diced"
        assert en["title"] == "Classic Guacamole"
        assert en["forked_from"]["title_es"] == "Guacamole clásico"

    def test_spanish_fields_are_read_only(self, as_alice, flour, tags):
        data = _create(as_alice, (flour, 1, "g"), title_es="Hackeado", steps_es=["x"])
        assert (data["title_es"], data["steps_es"]) == ("", [])

    def test_fork_ignores_list_filters(self, as_alice, as_bob, flour, tags):
        original = _create(as_alice, (flour, 1, "cup"))
        as_alice.post(f"{API}spells/{original['uuid']}/publish/")
        resp = as_bob.post(f"{API}spells/{original['uuid']}/fork/?scope=public&tag=vegan")
        assert resp.status_code == 201
        assert resp.json()["visibility"] == "private"

    def test_forker_can_keep_a_private_reagent_from_the_source(self, as_alice, as_bob, alice, tags):
        secret = _reagent("Secret salt", status=Reagent.Status.PRIVATE, created_by=alice)
        original = _create(as_alice, (secret, 3, "g"))
        as_alice.post(f"{API}spells/{original['uuid']}/publish/")
        fork = as_bob.post(f"{API}spells/{original['uuid']}/fork/").json()
        resp = as_bob.put(
            f"{API}spells/{fork['uuid']}/", _spell_payload((secret, 6, "g")), format="json"
        )
        assert resp.status_code == 200
        # …but can't put it in a brand-new spell.
        resp = as_bob.post(f"{API}spells/", _spell_payload((secret, 1, "g")), format="json")
        assert resp.status_code == 400


# ─── Reagents ────────────────────────────────────────────────────────────────


def _reagent_payload(name, **extra):
    return {
        "name": name,
        "category": "herb_spice",
        "grams_per_unit": {"tbsp": 7},
        "nutrients": {"kcal": 250, "protein_g": None, "sodium_mg": 0},
        **extra,
    }


class TestReagents:
    def test_user_reagent_is_private_to_its_creator(self, as_alice, as_bob):
        resp = as_alice.post(f"{API}reagents/", _reagent_payload("Za'atar"), format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "private"
        assert body["source"] == "user"
        assert body["is_mine"] is True
        assert [r["name"] for r in as_alice.get(f"{API}reagents/?q=za'atar").json()] == ["Za'atar"]
        assert as_bob.get(f"{API}reagents/?q=za'atar").json() == []
        assert as_bob.get(f"{API}reagents/{body['uuid']}/").status_code == 404

        Reagent.objects.filter(uuid=body["uuid"]).update(status=Reagent.Status.APPROVED)
        assert [r["name"] for r in as_bob.get(f"{API}reagents/?q=za'atar").json()] == ["Za'atar"]

    def test_share_requests_review(self, as_alice):
        body = as_alice.post(f"{API}reagents/", _reagent_payload("Sumac", share=True), format="json").json()
        assert body["status"] == "pending"

    def test_status_and_source_cannot_be_forged(self, as_alice):
        body = as_alice.post(
            f"{API}reagents/", _reagent_payload("Sumac", status="approved", source="usda"), format="json"
        ).json()
        assert (body["status"], body["source"]) == ("private", "user")

    def test_unknown_nutrients_stay_null(self, as_alice):
        body = as_alice.post(f"{API}reagents/", _reagent_payload("Sumac"), format="json").json()
        n = ReagentNutrients.objects.get(reagent_id=body["uuid"])
        assert n.protein_g is None  # unknown, not 0
        assert n.fiber_g is None  # omitted entirely
        assert n.sodium_mg == 0  # a measured zero is kept
        assert body["nutrients"]["protein_g"] is None

    def test_search_is_case_and_accent_insensitive(self, as_alice, db):
        _reagent("Paprika", name_es="Pimentón")
        _reagent("Paprika oil")
        names = [r["name"] for r in as_alice.get(f"{API}reagents/?q=PIMENTON").json()]
        assert names == ["Paprika"]
        names = [r["name"] for r in as_alice.get(f"{API}reagents/?q=paprika").json()]
        assert names == ["Paprika", "Paprika oil"]  # exact before prefix

    def test_exact_duplicate_needs_confirmation(self, as_alice, db):
        _reagent("Pimentón")
        resp = as_alice.post(f"{API}reagents/", _reagent_payload("pimenton"), format="json")
        assert resp.status_code == 409
        assert resp.json()["code"] == "duplicate"
        assert [m["name"] for m in resp.json()["matches"]] == ["Pimentón"]
        resp = as_alice.post(f"{API}reagents/", _reagent_payload("pimenton", confirm=True), format="json")
        assert resp.status_code == 201

    @pytest.mark.parametrize("field,value", [
        ("nutrients", {"kcal": "Infinity"}),
        ("nutrients", {"protein_g": "nan"}),
        ("density_g_per_ml", "nan"),
        ("grams_per_unit", {"cup": "Infinity"}),
    ])
    def test_non_finite_numbers_rejected(self, as_alice, field, value):
        payload = _reagent_payload("Sumac")
        payload[field] = value
        assert as_alice.post(f"{API}reagents/", payload, format="json").status_code == 400

    def test_spanish_exact_match_ranks_first(self, as_alice, db):
        for name in ("Arroz con leche mix", "Barroza sauce"):
            _reagent(name)
        _reagent("Rice", name_es="Arroz")
        names = [r["name"] for r in as_alice.get(f"{API}reagents/?q=arroz").json()]
        assert names[0] == "Rice"
        assert names[1] == "Arroz con leche mix"  # prefix before infix

    def test_mine_filter_applies_to_search(self, as_alice, alice, db):
        _reagent("Salsa verde")
        _reagent("Salsa macha", status=Reagent.Status.PRIVATE, created_by=alice)
        names = [r["name"] for r in as_alice.get(f"{API}reagents/?q=salsa&mine=1").json()]
        assert names == ["Salsa macha"]

    def test_own_reagents_listed_first(self, as_alice, alice, db):
        _reagent("Apple")
        _reagent("Zucchini blossoms", status=Reagent.Status.PRIVATE, created_by=alice)
        assert as_alice.get(f"{API}reagents/").json()[0]["name"] == "Zucchini blossoms"

    def test_creator_edits_own_unapproved_reagent(self, as_alice, alice, tags):
        body = as_alice.post(f"{API}reagents/", _reagent_payload("Sumac"), format="json").json()
        assert body["can_edit"] is True
        resp = as_alice.put(
            f"{API}reagents/{body['uuid']}/",
            _reagent_payload("Sumac", name_es="Zumaque", nutrients={"kcal": 300}, share=True),
            format="json",
        )
        assert resp.status_code == 200
        r = Reagent.objects.get(uuid=body["uuid"])
        assert (r.name_es, r.status, r.nutrients.kcal, r.nutrients.sodium_mg) == ("Zumaque", "pending", 300, None)

    def test_patch_response_shows_the_new_nutrients(self, as_alice, tags):
        body = as_alice.post(
            f"{API}reagents/", _reagent_payload("Sumac", nutrients={"kcal": 333, "sodium_mg": 16, "fat_g": 0.5}), format="json"
        ).json()
        resp = as_alice.patch(
            f"{API}reagents/{body['uuid']}/", {"nutrients": {"kcal": 345, "sodium_mg": 12}}, format="json"
        )
        assert resp.status_code == 200
        assert (resp.json()["nutrients"]["kcal"], resp.json()["nutrients"]["sodium_mg"]) == (345, 12)
        assert resp.json()["nutrients"]["fat_g"] == 0.5  # left alone by a PATCH

    def test_put_response_shows_the_replaced_profile(self, as_alice, tags):
        body = as_alice.post(
            f"{API}reagents/", _reagent_payload("Sumac", nutrients={"kcal": 333, "sodium_mg": 16}), format="json"
        ).json()
        resp = as_alice.put(
            f"{API}reagents/{body['uuid']}/", _reagent_payload("Sumac", nutrients={"kcal": 345}), format="json"
        )
        assert resp.status_code == 200
        assert (resp.json()["nutrients"]["kcal"], resp.json()["nutrients"]["sodium_mg"]) == (345, None)

    def test_edit_regrams_existing_lines(self, as_alice, tags):
        body = as_alice.post(f"{API}reagents/", _reagent_payload("Sumac"), format="json").json()
        spell = _create(as_alice, (Reagent.objects.get(uuid=body["uuid"]), 2, "tbsp"))
        assert spell["lines"][0]["grams"] == 14
        as_alice.patch(f"{API}reagents/{body['uuid']}/", {"grams_per_unit": {"tbsp": 9}}, format="json")
        assert SpellReagent.objects.get(spell_id=spell["uuid"]).grams == 18

    def test_approved_or_foreign_reagents_are_read_only(self, as_alice, as_bob, alice, db):
        approved_own = _reagent("Mine, approved", created_by=alice)
        catalog = _reagent("Catalog")
        private_other = _reagent("Bob's", status=Reagent.Status.PRIVATE, created_by=None)
        for r in (approved_own, catalog):
            assert as_alice.get(f"{API}reagents/{r.uuid}/").json()["can_edit"] is False
            assert as_alice.patch(f"{API}reagents/{r.uuid}/", {"name": "x"}, format="json").status_code == 403
        assert as_alice.patch(f"{API}reagents/{private_other.uuid}/", {"name": "x"}, format="json").status_code == 404
        mine = as_alice.post(f"{API}reagents/", _reagent_payload("Sumac"), format="json").json()
        assert as_bob.patch(f"{API}reagents/{mine['uuid']}/", {"name": "x"}, format="json").status_code == 404

    def test_reagent_pages(self, alice, db):
        catalog = _reagent("Catalog")
        own = _reagent("Own", status=Reagent.Status.PRIVATE, created_by=alice)
        hidden = _reagent("Hidden", status=Reagent.Status.PRIVATE)
        c = Client()
        c.force_login(alice)
        assert c.get(f"/cauldron/grimoire/reagent/{catalog.uuid}/").status_code == 200
        assert c.get(f"/cauldron/grimoire/reagent/{hidden.uuid}/").status_code == 404
        assert c.get(f"/cauldron/grimoire/reagent/{own.uuid}/edit/").status_code == 200
        assert c.get(f"/cauldron/grimoire/reagent/{catalog.uuid}/edit/").status_code == 404
        new = c.get("/cauldron/grimoire/reagent/new/?name=Sumac").content.decode()
        assert 'page: "reagent"' in new and 'prefillName: "Sumac"' in new and "<dialog" not in new

    def test_spanish_name_duplicate_is_caught(self, as_alice, db):
        _reagent("Paprika", name_es="Pimentón")
        resp = as_alice.post(f"{API}reagents/", _reagent_payload("Pimenton"), format="json")
        assert resp.status_code == 409

    def test_bad_household_unit_rejected(self, as_alice):
        resp = as_alice.post(
            f"{API}reagents/", _reagent_payload("Sumac", grams_per_unit={"bucket": 3}), format="json"
        )
        assert resp.status_code == 400


class TestPreferences:
    URL = f"{API}preferences/"

    def test_defaults_to_metric_and_saves_per_user(self, as_alice, as_bob, alice):
        assert as_alice.get(self.URL).json() == {"unit_system": "metric"}
        resp = as_alice.patch(self.URL, {"unit_system": "us"}, format="json")
        assert resp.status_code == 200 and resp.json() == {"unit_system": "us"}
        assert GrimoirePreferences.objects.get(user=alice).unit_system == "us"
        assert as_bob.get(self.URL).json() == {"unit_system": "metric"}

    def test_rejects_unknown_system(self, as_alice):
        assert as_alice.patch(self.URL, {"unit_system": "imperial-ish"}, format="json").status_code == 400

    def test_requires_auth(self, db):
        assert APIClient().get(self.URL).status_code == 403

    def test_line_carries_density_for_volume_hints(self, as_alice, milk, tags):
        line = _create(as_alice, (milk, 1, "cup"))["lines"][0]
        assert line["reagent_density"] == 1.03


# ─── Page, nav and seed ──────────────────────────────────────────────────────


class TestPage:
    def test_anonymous_is_sent_to_cauldron_login(self, db):
        resp = Client().get("/cauldron/grimoire/")
        assert resp.status_code == 302
        assert resp["Location"].startswith("/cauldron/login/")

    def test_logged_in_user_sees_the_grimoire(self, alice):
        c = Client()
        c.force_login(alice)
        resp = c.get("/cauldron/grimoire/")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "grimoire.js" in html and 'id="grim-app"' in html

    def test_page_follows_the_language_switch(self, alice):
        c = Client()
        c.force_login(alice)
        c.cookies["django_language"] = "es"
        html = c.get("/cauldron/grimoire/").content.decode()
        assert "El Grimorio" in html and "El Aquelarre" in html
        assert '"scope_coven": "El Aquelarre"' in html  # JS strings ride in translated
        assert 'lang: "es"' in html

    def test_page_carries_the_unit_system(self, alice):
        GrimoirePreferences.objects.create(user=alice, unit_system="us")
        c = Client()
        c.force_login(alice)
        html = c.get("/cauldron/grimoire/").content.decode()
        assert 'units: "us"' in html
        assert 'data-units="us" aria-pressed="true"' in html

    def test_api_errors_are_translated(self, as_alice, db):
        bare = _reagent("Mystery")
        resp = as_alice.post(
            f"{API}spells/", _spell_payload((bare, 1, "piece"), tags=[]), format="json",
            HTTP_ACCEPT_LANGUAGE="es",
        )
        assert "No se puede convertir" in resp.content.decode()

    def test_spell_opens_on_its_own_page(self, alice, bob, db):
        mine = Spell.objects.create(owner=alice, title="Mine")
        shared = Spell.objects.create(owner=bob, title="Shared", title_es="Compartido", visibility="public")
        hidden = Spell.objects.create(owner=bob, title="Hidden")
        c = Client()
        c.force_login(alice)
        for spell in (mine, shared):
            resp = c.get(f"/cauldron/grimoire/spell/{spell.uuid}/")
            assert resp.status_code == 200
            html = resp.content.decode()
            assert 'page: "spell"' in html and str(spell.uuid) in html and "<dialog" not in html
        c.cookies["django_language"] = "es"
        assert "<title>Compartido" in c.get(f"/cauldron/grimoire/spell/{shared.uuid}/").content.decode()
        # Someone else's private spell doesn't exist as far as alice can tell.
        assert c.get(f"/cauldron/grimoire/spell/{hidden.uuid}/").status_code == 404

    def test_writing_and_editing_are_full_pages(self, alice, bob, db):
        mine = Spell.objects.create(owner=alice, title="Mine")
        theirs = Spell.objects.create(owner=bob, title="Theirs", visibility="public")
        c = Client()
        c.force_login(alice)
        new = c.get("/cauldron/grimoire/spell/new/")
        assert new.status_code == 200 and 'page: "edit"' in new.content.decode()
        edit = c.get(f"/cauldron/grimoire/spell/{mine.uuid}/edit/")
        assert edit.status_code == 200 and 'id="grim-editor-form"' in edit.content.decode()
        # Public or not, only the owner edits; everyone else forks.
        assert c.get(f"/cauldron/grimoire/spell/{theirs.uuid}/edit/").status_code == 404

    @pytest.mark.parametrize("path", ["spell/new/", "spell/00000000-0000-0000-0000-000000000000/"])
    def test_spell_pages_need_login(self, db, path):
        resp = Client().get(f"/cauldron/grimoire/{path}")
        assert resp.status_code == 302 and resp["Location"].startswith("/cauldron/login/")

    @pytest.mark.parametrize("url", ["/cauldron/", "/cauldron/forge/equipment/"])
    def test_cauldron_nav_links_to_the_grimoire(self, alice, url):
        c = Client()
        c.force_login(alice)
        assert 'href="/cauldron/grimoire/"' in c.get(url, follow=True).content.decode()


class TestSeed:
    def test_seed_is_idempotent_and_big_enough(self, db):
        call_command("seed_grimoire")
        counts = (Reagent.objects.count(), Spell.objects.count(), SpellReagent.objects.count())
        call_command("seed_grimoire")
        assert (Reagent.objects.count(), Spell.objects.count(), SpellReagent.objects.count()) == counts

        assert Reagent.objects.filter(source="usda", status="approved", fdc_id__isnull=False).count() >= 40
        public = Spell.objects.filter(visibility="public")
        assert public.count() >= 12
        assert Tag.objects.filter(spells__in=public).distinct().count() >= 6
        assert all(line.grams > 0 for line in SpellReagent.objects.all())
        owner = public.first().owner
        assert not owner.is_active and not owner.has_usable_password()
        assert all(s.title_es and s.description_es and len(s.steps_es) == len(s.steps) for s in public)

    def test_rerun_leaves_seeded_rows_and_moderation_alone(self, db):
        call_command("seed_grimoire")
        spell = Spell.objects.filter(visibility="public").first()
        stamp = spell.updated_at
        Reagent.objects.filter(fdc_id=170554).update(status=Reagent.Status.PRIVATE, name="Chia (staff)")
        call_command("seed_grimoire")
        spell.refresh_from_db()
        assert spell.updated_at == stamp  # starter spells don't float to the top on boot
        chia = Reagent.objects.get(fdc_id=170554)
        assert (chia.status, chia.name) == ("private", "Chia (staff)")

        spell.visibility = Spell.Visibility.PRIVATE  # staff unpublished it
        spell.save()
        call_command("seed_grimoire", refresh=True)
        chia.refresh_from_db()
        spell.refresh_from_db()
        assert chia.name == "Chia seeds"  # refresh pushes seed data…
        assert chia.status == "private"  # …but never re-approves
        assert spell.visibility == "private"  # …or re-publishes

    def test_seed_survives_duplicated_starter_titles(self, db):
        call_command("seed_grimoire")
        spell = Spell.objects.filter(visibility="public").first()
        Spell.objects.create(owner=spell.owner, title=spell.title, visibility="private")
        call_command("seed_grimoire")
        call_command("seed_grimoire", refresh=True)
        assert Spell.objects.filter(title=spell.title).count() == 2

    def test_staff_reagents_come_from_their_labels(self, db):
        call_command("seed_grimoire")
        call_command("seed_grimoire")  # keyed by name: no duplicate on reboot
        coke = Reagent.objects.get(source="staff", name="Mexican Coca-Cola")
        assert (coke.status, coke.name_es, coke.fdc_id) == ("approved", "Coca-Cola mexicana", None)
        assert grimoire.to_grams(coke, 1, "glass") == 260  # its standard measure: a 250 ml glass
        assert coke.nutrients.sugar_g > 10 and coke.nutrients.protein_g == 0
        assert coke.nutrients.sodium_mg is None  # not on the label → unknown, never 0
        assert coke in grimoire.visible_reagents(User.objects.create_user("someone"))

    def test_seed_keeps_unknown_nutrients_null(self, db):
        call_command("seed_grimoire")
        chia = ReagentNutrients.objects.get(reagent__fdc_id=170554)
        assert chia.kcal is not None
        assert any(getattr(chia, f) is None for f in ReagentNutrients.FIELDS)


class TestLegalPages:
    """The Cauldron's own terms and privacy notice (public), linked from its footer."""

    @pytest.mark.parametrize("lang,needles", [
        ("en", ["The Cauldron Terms", "without prior notice", "aggregated statistics", "Make it mine"]),
        ("es", ["Términos y Condiciones de The Cauldron", "sin aviso previo", "estadísticas agregadas"]),
    ])
    def test_terms_cover_recipes_ingredients_and_statistics(self, db, lang, needles):
        c = Client()
        c.cookies["django_language"] = lang
        html = " ".join(c.get("/cauldron/terms/").content.decode().split())
        for needle in needles:
            assert needle in html

    @pytest.mark.parametrize("lang,needle", [("en", "Privacy Notice"), ("es", "Aviso de Privacidad")])
    def test_privacy_notice_is_public(self, db, lang, needle):
        c = Client()
        c.cookies["django_language"] = lang
        resp = c.get("/cauldron/privacy/")
        assert resp.status_code == 200 and needle in resp.content.decode()

    @pytest.mark.parametrize("url", ["/cauldron/", "/cauldron/login/", "/cauldron/terms/"])
    def test_cauldron_footer_links_its_own_legal_pages(self, db, url):
        html = Client().get(url).content.decode()
        assert 'href="/cauldron/terms/"' in html and 'href="/cauldron/privacy/"' in html
        assert "/scholomancy/console/terms/" not in html

    def test_reagent_form_warns_ingredients_can_go_public(self, alice):
        c = Client()
        c.force_login(alice)
        html = c.get("/cauldron/grimoire/reagent/new/").content.decode()
        assert "without notice" in html and 'href="/cauldron/terms/"' in html

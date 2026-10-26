"""Recommended goals beside personal ones (#105).

Body data on the Cauldron profile (moved from the Forge), the WHO/FAO + DRI
recommendation service with its conservative fallbacks, both goal sets in
``rolling/``, the Profile's ``body/`` API, MCP ``get_goals``, the legal bump and
what withdrawing with delete clears.
"""

import importlib
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from the_cauldron.models import CauldronProfile, IntakeEntry, LegalAcceptance, UserEquipmentProfile
from the_cauldron.services import forge, legal, nutrition, recommendations, rolling
from the_cauldron.services.recommendations import recommended
from the_cauldron.tests.test_mcp_server import _ok, _token

User = get_user_model()
API = "/cauldron/api/"
TODAY = date(2026, 10, 23)
MX = ZoneInfo("America/Mexico_City")


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    settings.STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


def _user(name="alice", consent=True):
    user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY] + ([legal.SENSITIVE_CONSENT] if consent else []))
    return user


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _profile(**body):
    return CauldronProfile(**body)


def _goal(rec, nutrient):
    g = rec["goals"][nutrient]
    return g.kind, g.min_value, g.max_value


class TestReferenceValues:
    def test_woman_30_60kg_moderate(self):
        rec = recommended(_profile(birth_year=1996, sex="female", weight_kg=60, activity_level="moderate"), TODAY)
        # BMR 8.126 × 60 + 845.6 = 1333.2 kcal; × PAL 1.76 = 2346.4 kcal; ±10 %.
        assert _goal(rec, "kcal") == ("between", 2112, 2581)
        assert _goal(rec, "protein_g") == ("at_least", 49.8, None)  # 0.83 g/kg
        # Fat 20–30 % and carbs 45–65 % of the energy requirement.
        assert _goal(rec, "fat_g") == ("between", pytest.approx(52.1), pytest.approx(78.2))
        assert _goal(rec, "carbs_g") == ("between", pytest.approx(264.0), pytest.approx(381.3))
        assert _goal(rec, "sat_fat_g") == ("at_most", None, pytest.approx(26.1))
        assert _goal(rec, "added_sugar_g") == ("at_most", None, pytest.approx(58.7))
        assert _goal(rec, "sodium_mg") == ("at_most", None, 2000)
        assert _goal(rec, "potassium_mg") == ("at_least", 3510, None)
        assert _goal(rec, "fiber_g") == ("at_least", 25, None)
        assert _goal(rec, "iron_mg") == ("at_least", 18, None)  # DRI women 19–50
        assert rec["goals"]["kcal"].source == "FAO/WHO/UNU 2004" and rec["assumed"] == []

    def test_man_65_80kg_vigorous(self):
        rec = recommended(_profile(birth_year=1961, sex="male", weight_kg=80, activity_level="vigorous"), TODAY)
        kcal = (11.711 * 80 + 587.7) * 2.25
        assert _goal(rec, "kcal") == ("between", round(kcal * 0.9), round(kcal * 1.1))
        assert _goal(rec, "calcium_mg")[1] == 1000 and _goal(rec, "vit_d_ug")[1] == 15

    def test_nutrients_without_a_recommendation_are_listed_with_a_note(self, db):
        user = _user()
        payload = recommendations.payload(user, TODAY)
        assert {x["nutrient"] for x in payload["none"]} == {
            "sugar_g", "mono_fat_g", "poly_fat_g", "omega3_epa_g", "omega3_dha_g", "cholesterol_mg",
        }
        assert all(x["note"] for x in payload["none"])


class TestFallbacks:
    def test_unknown_activity_is_sedentary(self):
        a = recommended(_profile(birth_year=1996, sex="female", weight_kg=60), TODAY)
        b = recommended(_profile(birth_year=1996, sex="female", weight_kg=60, activity_level="sedentary"), TODAY)
        assert _goal(a, "kcal") == _goal(b, "kcal") and a["assumed"] == ["activity"]

    def test_unknown_sex_takes_the_larger_minimum_and_smaller_maximum(self):
        rec = recommended(_profile(birth_year=1996, weight_kg=60, activity_level="moderate"), TODAY)
        assert _goal(rec, "iron_mg")[1] == 18  # women 18 > men 8
        assert _goal(rec, "vit_c_mg")[1] == 90  # men 90 > women 75
        woman = recommended(_profile(birth_year=1996, sex="female", weight_kg=60, activity_level="moderate"), TODAY)
        man = recommended(_profile(birth_year=1996, sex="male", weight_kg=60, activity_level="moderate"), TODAY)
        assert _goal(rec, "kcal")[1] == max(_goal(woman, "kcal")[1], _goal(man, "kcal")[1])
        assert _goal(rec, "kcal")[2] == min(_goal(woman, "kcal")[2], _goal(man, "kcal")[2])
        assert "sex" in rec["assumed"]

    def test_unknown_age_uses_the_most_conservative_adult_band(self):
        rec = recommended(_profile(sex="female", weight_kg=60), TODAY)
        assert _goal(rec, "calcium_mg")[1] == 1300  # the 18-year-old band
        assert _goal(rec, "vit_d_ug")[1] == 20  # the 71+ band
        assert "age" in rec["assumed"]

    def test_a_range_left_empty_by_the_fallbacks_is_left_out(self):
        rec = recommended(_profile(weight_kg=60), TODAY)  # neither sex nor age
        assert "kcal" not in rec["goals"] and "kcal" in rec["unresolved"]

    def test_unknown_weight_omits_weight_based_goals(self):
        rec = recommended(_profile(birth_year=1996, sex="female"), TODAY)
        for nutrient in ("kcal", "protein_g", "fat_g", "carbs_g", "added_sugar_g", "sat_fat_g", "trans_fat_g"):
            assert nutrient not in rec["goals"]
            assert nutrient in rec["needs_weight"]
        assert "fiber_g" in rec["goals"] and "calcium_mg" in rec["goals"]

    def test_under_18_gets_none(self):
        rec = recommended(_profile(birth_year=2010, sex="female", weight_kg=50), TODAY)
        assert rec["status"] == "minor" and rec["goals"] == {}

    def test_turning_18_this_year_still_counts_as_a_minor(self):
        # Born 2008: 18 by year, but 17 until the birthday — the safe side.
        assert recommended(_profile(birth_year=2008), TODAY)["status"] == "minor"
        assert recommended(_profile(birth_year=2007), TODAY)["status"] == "adult"

    def test_no_profile_at_all_still_gives_the_conservative_set(self, db):
        rec = recommendations.recommended_for(_user(), TODAY)
        assert rec["status"] == "adult" and "fiber_g" in rec["goals"]
        assert not CauldronProfile.objects.exists()  # reading never creates the row


class TestDemographicsMove:
    def test_migration_moves_birth_year_and_sex(self, db):
        user = _user()
        migration = importlib.import_module("the_cauldron.migrations.0035_move_demographics_to_cauldronprofile")
        # Stand-ins with the historical fields; the real columns are gone after 0036.
        equipment = mock.Mock(user_id=user.pk, birth_year=1990, sex="female")
        fake = mock.Mock()
        fake.get_model.side_effect = lambda app, name: {
            "UserEquipmentProfile": mock.Mock(objects=mock.Mock(filter=lambda *a, **k: mock.Mock(
                iterator=lambda: iter([equipment])))),
            "CauldronProfile": CauldronProfile,
        }[name]
        migration.move(fake, None)
        profile = CauldronProfile.objects.get(user=user)
        assert (profile.birth_year, profile.sex) == (1990, "female")

    def test_the_forge_peer_score_reads_the_cauldron_profile(self, db):
        user = _user()
        with mock.patch.object(forge.norms, "score", wraps=forge.norms.score) as score:
            forge.peer_score(user, "Push-up", 20)
            assert score.call_args.args[2:] == ("undisclosed", None)
            CauldronProfile.objects.create(user=user, birth_year=TODAY.year - 30, sex="female")
            forge.peer_score(user, "Push-up", 20)
            assert score.call_args.args[2] == "female" and score.call_args.args[3] in (29, 30, 31)

    def test_the_forge_no_longer_takes_them(self, db):
        user = _user()
        c = _client(user)
        r = c.put(f"{API}equipment/", {"equipment": [], "birth_year": 1990, "sex": "male"}, format="json")
        assert r.status_code == 200, r.data
        assert "birth_year" not in r.data and "sex" not in r.data
        assert not hasattr(UserEquipmentProfile.objects.get(user=user), "birth_year")


class TestBodyApi:
    def test_birth_year_and_sex_need_no_consent(self, db):
        c = _client(_user(consent=False))
        r = c.patch(f"{API}body/", {"birth_year": 1990, "sex": "female"}, format="json")
        assert r.status_code == 200, r.data
        assert r.data["birth_year"] == 1990 and r.data["has_consent"] is False

    def test_weight_and_activity_need_the_consent(self, db):
        c = _client(_user(consent=False))
        r = c.patch(f"{API}body/", {"weight_kg": 70}, format="json")
        assert r.status_code == 403 and r.data["code"] == "consent_required"
        assert not CauldronProfile.objects.filter(weight_kg=70).exists()

    def test_clearing_needs_no_consent(self, db):
        user = _user(consent=False)
        CauldronProfile.objects.create(user=user, weight_kg=80, activity_level="vigorous")
        r = _client(user).patch(f"{API}body/", {"weight_kg": None, "activity_level": ""}, format="json")
        assert r.status_code == 200, r.data
        assert r.data["weight_kg"] is None and r.data["activity_level"] == ""

    def test_save_validate_and_clear(self, db):
        c = _client(_user())
        r = c.patch(f"{API}body/", {"weight_kg": 62.5, "activity_level": "moderate"}, format="json")
        assert r.status_code == 200 and r.data["weight_kg"] == 62.5
        for bad in ({"weight_kg": 5}, {"weight_kg": "nan"}, {"birth_year": 1800}, {"activity_level": "couch"},
                    {"sex": "other"}):
            assert c.patch(f"{API}body/", bad, format="json").status_code == 400, bad
        r = c.patch(f"{API}body/", {"weight_kg": None, "activity_level": None}, format="json")
        assert r.data["weight_kg"] is None and r.data["activity_level"] == ""

    def test_posthog_gets_only_a_count(self, db):
        c = _client(_user())
        with mock.patch("the_cauldron.nutrition_api.posthog.capture") as capture:
            c.patch(f"{API}body/", {"weight_kg": 62.5, "birth_year": 1990, "sex": "female"}, format="json")
        capture.assert_called_once_with("profile_body_data_saved", properties={"fields_count": 3})

    def test_changes_apply_at_once_to_the_goals_payload(self, db):
        c = _client(_user())
        assert "kcal" not in {g["nutrient"] for g in c.get(f"{API}goals/").data["recommended"]["goals"]}
        c.patch(f"{API}body/", {"weight_kg": 60, "birth_year": 1996, "sex": "female"}, format="json")
        rec = {g["nutrient"]: g for g in c.get(f"{API}goals/").data["recommended"]["goals"]}
        assert rec["kcal"]["source"] == "FAO/WHO/UNU 2004" and rec["kcal"]["kind"] == "between"


class TestRolling:
    NOW = datetime(2026, 10, 15, 18, 30, tzinfo=MX)

    def _eat(self, user, when, **values):
        nutrients, missing = nutrition.clean_quick_values(values)
        IntakeEntry.objects.create(user=user, eaten_at=when, local_date=when.date(), source="quick", label="x",
                                   quantity=1, unit="portion", nutrients=nutrients, missing=missing)

    def test_both_sets_judged_over_shared_values(self, db):
        user = _user()
        CauldronProfile.objects.create(user=user, birth_year=1996, sex="female", weight_kg=60)
        nutrition.save_goal_set(user, nutrition.clean_goal_set(
            [{"nutrient": "sodium_mg", "kind": "at_most", "max_value": 3000}]), date(2026, 10, 1))
        self._eat(user, datetime(2026, 10, 15, 13, 0, tzinfo=MX), sodium_mg=2500, fiber_g=10)
        roll = rolling.rolling(user, "24h", 7, now=self.NOW)
        assert set(roll["sets"]) == {"mine", "who"}
        assert roll["values"]["sodium_mg"][-1] == 2500
        assert roll["sets"]["mine"]["status"]["sodium_mg"][-1] == "met"  # under my 3000
        assert roll["sets"]["who"]["status"]["sodium_mg"][-1] == "missed"  # over WHO's 2000
        assert roll["sets"]["who"]["high"]["sodium_mg"][-1] == 2000
        assert "fiber_g" in roll["tracked"] and "fiber_g" not in roll["sets"]["mine"]["status"]
        assert {s["nutrient"] for s in roll["sets"]["who"]["summary"]} >= {"sodium_mg", "fiber_g"}
        assert roll["sets"]["who"]["status_reason"] == "adult"

    def test_the_api_answers_in_one_request(self, db):
        r = _client(_user()).get(f"{API}rolling/?span=24h&days=7")
        assert r.status_code == 200 and "sets" in r.data and "status" not in r.data

    def test_minors_get_no_recommended_set(self, db):
        user = _user()
        CauldronProfile.objects.create(user=user, birth_year=2012)
        roll = rolling.rolling(user, "24h", 7, now=self.NOW)
        assert roll["sets"]["who"]["summary"] == [] and roll["sets"]["who"]["status_reason"] == "minor"


class TestMcpAndLegal:
    def test_get_goals_returns_mine_and_recommended(self, db):
        user = _user()
        CauldronProfile.objects.create(user=user, birth_year=1996, sex="female", weight_kg=60)
        d = _ok(_token(user), "get_goals")
        assert d["mine"] == [] and d["recommended"]["status"] == "adult"
        kcal = next(g for g in d["recommended"]["goals"] if g["nutrient"] == "kcal")
        assert kcal["source"] == "FAO/WHO/UNU 2004" and kcal["min"] < kcal["max"]
        assert {x["nutrient"] for x in d["recommended"]["no_recommendation"]} >= {"cholesterol_mg"}

    def test_versions_bumped_so_everyone_is_asked_again(self, db):
        user = User.objects.create_user(username="old", email="old@example.com", password="pw12345!")
        for doc, version in ((legal.TERMS, "1.2"), (legal.PRIVACY, "1.3"), (legal.SENSITIVE_CONSENT, "1.0")):
            LegalAcceptance.objects.create(user=user, document=doc, version=version)
        assert set(legal.pending_documents(user)) == {legal.TERMS, legal.PRIVACY}
        assert not legal.has_sensitive_consent(user)

    def test_withdraw_with_delete_clears_weight_and_activity(self, db):
        user = _user()
        CauldronProfile.objects.create(user=user, birth_year=1990, sex="male", weight_kg=80, activity_level="vigorous")
        legal.withdraw_consent(user, delete_data=True)
        profile = CauldronProfile.objects.get(user=user)
        assert (profile.weight_kg, profile.activity_level) == (None, "")
        assert (profile.birth_year, profile.sex) == (1990, "male")  # the Forge's, kept

    def test_withdraw_without_delete_keeps_them(self, db):
        user = _user()
        CauldronProfile.objects.create(user=user, weight_kg=80)
        legal.withdraw_consent(user)
        assert CauldronProfile.objects.get(user=user).weight_kg == 80

"""Rolling 24 h / 7-day windows and time in range (services/rolling.py).

The user is in America/Mexico_City (UTC−6). "Now" is fixed at Oct 15 2026,
18:30 local, so the hourly history ends at 18:00 plus a tick at 18:30.
"""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from the_cauldron.models import IntakeEntry, NutrientGoal
from the_cauldron.services import legal, nutrition, rolling

User = get_user_model()
MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2026, 10, 15, 18, 30, tzinfo=MX)


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=MX)


@pytest.fixture
def alice(db):
    user = User.objects.create_user(username="alice", email="alice@example.com", password="pw12345!")
    legal.accept(user, [legal.TERMS, legal.PRIVACY, legal.SENSITIVE_CONSENT])
    NutrientGoal.objects.create(user=user, nutrient="protein_g", kind="at_least", min_value=100,
                                valid_from=date(2026, 9, 1))
    return user


def eat(user, when, **values):
    nutrients, missing = nutrition.clean_quick_values(values)
    return IntakeEntry.objects.create(
        user=user, eaten_at=when, local_date=when.astimezone(MX).date(), source="quick", label="x",
        quantity=1, unit="portion", nutrients=nutrients, missing=missing,
    )


def protein_at(result, i=-1):
    return result["values"]["protein_g"][i], result["sets"]["mine"]["status"]["protein_g"][i]


class TestTicks:
    def test_hourly_ticks_end_now(self):
        ticks = rolling.ticks_for(NOW, 7)
        assert len(ticks) == 7 * 24 + 1
        assert ticks[-2] == at(15, 18) and ticks[-1] == NOW
        assert ticks[0] == at(8, 19)

    def test_long_histories_step_every_three_hours(self):
        assert rolling.step_hours(90) == 3
        assert len(rolling.ticks_for(at(15, 18), 90)) == 90 * 8  # on the hour: no extra tick


class TestWindowNow:
    def test_the_24h_window_holds_only_the_last_24_hours(self, alice):
        eat(alice, at(15, 16, 30), protein_g=50)
        eat(alice, at(14, 22, 30), protein_g=30)
        eat(alice, at(14, 12, 30), protein_g=40)  # 30 h ago: outside
        result = rolling.rolling(alice, "24h", 7, now=NOW)
        assert protein_at(result) == (80, "missed")

    def test_crossing_midnight_is_one_window(self, alice):
        eat(alice, at(14, 23, 0), protein_g=60)
        eat(alice, at(15, 1, 0), protein_g=50)
        assert protein_at(rolling.rolling(alice, "24h", 7, now=NOW)) == (110, "met")

    def test_the_7_day_window_is_a_daily_average(self, alice):
        for day in (9, 11, 13, 15):
            eat(alice, at(day, 12), protein_g=175)  # 700 g in 7 days
        eat(alice, at(8, 17), protein_g=500)  # just over 7 days ago: outside
        value, status = protein_at(rolling.rolling(alice, "7d", 7, now=NOW))
        assert value == pytest.approx(100) and status == "met"

    def test_missing_data_makes_a_maximum_uncertain(self, alice):
        NutrientGoal.objects.create(user=alice, nutrient="sodium_mg", kind="at_most", max_value=2000,
                                    valid_from=date(2026, 9, 1))
        eat(alice, at(15, 12), protein_g=120, sodium_mg=800)
        eat(alice, at(15, 13), protein_g=10)  # no sodium data
        result = rolling.rolling(alice, "24h", 7, now=NOW)
        assert result["sets"]["mine"]["status"]["sodium_mg"][-1] == "uncertain"
        assert result["missing"]["sodium_mg"][-1] == 1

    def test_an_empty_window_is_not_logged(self, alice):
        assert protein_at(rolling.rolling(alice, "24h", 7, now=NOW)) == (0, "not_logged")


class TestTimeInRange:
    @pytest.fixture
    def two_meals(self, alice):
        eat(alice, at(12, 8), protein_g=120)  # in range for the 24 ticks Oct 12 08:00 → Oct 13 07:00
        eat(alice, at(13, 12), protein_g=50)  # out of range for Oct 13 12:00 → Oct 14 11:00
        return alice

    def test_share_of_the_time_in_range(self, two_meals):
        summary = {s["nutrient"]: s for s in rolling.rolling(two_meals, "24h", 7, now=NOW)["sets"]["mine"]["summary"]}
        protein = summary["protein_g"]
        assert (protein["in_range"], protein["out_of_range"], protein["uncertain"]) == (24, 24, 0)
        assert protein["pct"] == 50
        assert protein["not_logged"] == 7 * 24 + 1 - 48  # never counted against you

    def test_daily_strip(self, two_meals):
        daily = rolling.rolling(two_meals, "24h", 7, now=NOW)["sets"]["mine"]["daily"]["protein_g"]
        by_day = {d["date"]: (d["pct"], d["judged"]) for d in daily}
        assert by_day[date(2026, 10, 12)] == (100, 16)  # 08:00–23:00
        assert by_day[date(2026, 10, 13)] == (40, 20)  # 8 in range, then 12 out from noon
        assert by_day[date(2026, 10, 14)] == (0, 12)

    def test_each_tick_uses_the_goal_of_its_day(self, two_meals):
        goal = NutrientGoal.objects.get(user=two_meals)
        goal.valid_to = date(2026, 10, 13)
        goal.save()
        NutrientGoal.objects.create(user=two_meals, nutrient="protein_g", kind="at_least", min_value=40,
                                    valid_from=date(2026, 10, 13))
        result = rolling.rolling(two_meals, "24h", 7, now=NOW)
        protein = {s["nutrient"]: s for s in result["sets"]["mine"]["summary"]}["protein_g"]
        assert (protein["in_range"], protein["out_of_range"]) == (48, 0)
        i = result["ticks"].index(at(12, 10))
        assert result["sets"]["mine"]["low"]["protein_g"][i] == 100
        assert result["sets"]["mine"]["low"]["protein_g"][result["ticks"].index(at(13, 10))] == 40


class TestApi:
    def test_window_endpoint(self, alice):
        c = APIClient()
        c.force_authenticate(alice)
        r = c.get("/cauldron/api/rolling/?span=24h&days=30")
        assert r.status_code == 200
        assert len(r.data["ticks"]) in (30 * 24, 30 * 24 + 1)
        assert set(r.data["values"]) == set(nutrition.FIELDS)
        assert [s["nutrient"] for s in r.data["sets"]["mine"]["summary"]] == ["protein_g"]
        assert c.get("/cauldron/api/rolling/?span=3h&days=7").status_code == 400
        assert c.get("/cauldron/api/rolling/?span=24h&days=12").status_code == 400

    def test_ticks_are_on_the_users_clock(self, alice):
        # The page reads date and hour straight off the string; UTC would show
        # 19:00 in Mexico City as the next day's 01:00.
        c = APIClient()
        c.force_authenticate(alice)
        body = json.loads(c.get("/cauldron/api/rolling/?span=24h&days=7").content)
        assert all(t.endswith(("-06:00", "-05:00")) for t in body["ticks"])
        assert body["now"].endswith(("-06:00", "-05:00"))

    def test_needs_the_consent(self, alice):
        legal.withdraw_consent(alice)
        c = APIClient()
        c.force_authenticate(alice)
        assert c.get("/cauldron/api/rolling/").status_code == 403

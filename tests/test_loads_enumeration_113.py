"""The plate enumerator is a cached reachable-totals table (#113).

The exhaustive ``product()`` walk it replaced is kept here as the reference:
both must build exactly the same loads and recipes (the lightest
``MAX_LOADS`` of them, once the cap applies).
"""

import random
from copy import deepcopy
from itertools import product
from types import SimpleNamespace as NS

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from the_cauldron.models import Equipment, Exercise, PrescribedExercise, Program, ProgramDay
from the_cauldron.services import forge, loads

User = get_user_model()

# The prod profile that couldn't save: 7 denominations × 4 on every implement.
BIG_BOX = [{"weight": w, "count": 4} for w in (1, 1.25, 1.5, 2, 2.5, 1.36, 2.27)]


def _reference_enumerate(plates, base, per_step, multiplier):
    """The pre-#113 enumerator, minus its cap: every combination in order."""
    usable = [
        {"weight": p["weight"], "steps": p["count"] // per_step}
        for p in plates
        if p["count"] // per_step > 0
    ]
    base = round(base, loads._PRECISION)
    stacked = multiplier == 1
    by_total = {
        base: loads.Load(total=base, per_side=[], leftover_plates=loads._leftover(plates, {}, per_step),
                         plate_count=0, stacked=stacked)
    }
    for combo in product(*[range(u["steps"] + 1) for u in usable]):
        if not any(combo):
            continue
        per_side = [{"weight": usable[i]["weight"], "count": n} for i, n in enumerate(combo) if n]
        added = sum(p["weight"] * p["count"] for p in per_side)
        total = round(base + multiplier * added, loads._PRECISION)
        used = {p["weight"]: p["count"] for p in per_side}
        plate_count = sum(used.values()) * per_step
        existing = by_total.get(total)
        if existing is not None and existing.plate_count <= plate_count:
            continue
        by_total[total] = loads.Load(total=total, per_side=per_side,
                                     leftover_plates=loads._leftover(plates, used, per_step),
                                     plate_count=plate_count, stacked=stacked)
    return sorted(by_total.values(), key=lambda l: l.total)


def _shape(result):
    return [(l.total, deepcopy(l.per_side), l.plate_count, deepcopy(l.leftover_plates), l.stacked)
            for l in result]


def _random_box(rng):
    weights = rng.sample([0.5, 1, 1.25, 2, 2.5, 5, 10, 15, 20, 25], rng.randint(1, 4))
    return [{"weight": w, "count": rng.choice([1, 2, 3, 4, 6, 8])} for w in weights]


INVENTORIES = [
    [{"weight": 2.5, "count": 8}, {"weight": 1.25, "count": 4}],
    [{"weight": 20, "count": 4}, {"weight": 5, "count": 2}],
    [{"weight": 2, "count": 6}],
    [{"weight": 5, "count": 3}],
    [{"weight": 2, "count": 4}, {"weight": 1, "count": 8}],  # ties: 2 kg = 1 kg × 2
    *[_random_box(random.Random(seed)) for seed in range(12)],
]
GEOMETRIES = [(4, 2, 2.0), (2, 2, 20.0), (1, 1, 6.0), (1, 1, 0.0)]


@pytest.fixture(autouse=True)
def _cold_cache():
    loads._enumerate_cached.cache_clear()
    yield
    loads._enumerate_cached.cache_clear()


@pytest.mark.parametrize("plates", INVENTORIES)
@pytest.mark.parametrize("per_step,multiplier,base", GEOMETRIES)
def test_table_matches_the_exhaustive_walk(plates, per_step, multiplier, base):
    box = loads.normalize_plates(plates)
    # Past the cap, the lightest MAX_LOADS of the full walk.
    expected = _reference_enumerate(box, base, per_step, multiplier)[: loads.MAX_LOADS]
    assert _shape(loads._enumerate(box, base, per_step, multiplier)) == _shape(expected)


def test_cap_keeps_the_lightest_totals_and_warns(caplog):
    box = loads.normalize_plates(BIG_BOX)
    everything = _reference_enumerate(box, 0.0, 1, 1)
    assert len(everything) > loads.MAX_LOADS

    with caplog.at_level("WARNING", logger="the_cauldron.services.loads"):
        kept = loads._enumerate(box, 0.0, 1, 1)

    assert _shape(kept) == _shape(everything[: loads.MAX_LOADS])
    assert [l.total for l in kept] == sorted(l.total for l in kept)
    assert "capped" in caplog.text


def test_callers_cannot_mutate_the_cache():
    prof = NS(equipment=["kettlebell"], kettlebell_mode="plates", kettlebell_plates=BIG_BOX,
              kettlebell_handle_weight=0.0, kettlebell_weights=[])
    first = loads.kettlebell_loads(prof)
    before = _shape(first)
    first[1].per_side[0]["count"] = 99
    first[1].leftover_plates.clear()
    first[1].total = -1
    first.pop()

    assert _shape(loads.kettlebell_loads(prof)) == before


def test_big_box_enumerates_each_implement_once_per_save(db):
    """The save path asks for loads a dozen times; only the first ask per
    implement does any work."""
    call_command("seed_forge")
    user = User.objects.create_user(username="plates", password="pw12345!")
    profile = forge.get_or_create_equipment_profile(user)
    profile.equipment.set(Equipment.objects.filter(key__in=["barbell", "dumbbells", "kettlebell", "bodyweight"]))
    profile.barbell_plates = BIG_BOX
    profile.bar_weight = 0
    profile.dumbbell_mode = "plates"
    profile.dumbbell_plates = BIG_BOX
    profile.dumbbell_handle_weight = 0
    profile.kettlebell_mode = "plates"
    profile.kettlebell_plates = BIG_BOX
    profile.kettlebell_handle_weight = 0
    profile.save()

    program = Program.objects.create(user=user, is_active=True)
    day = ProgramDay.objects.create(program=program, day_index=0, name="Today's Forge")
    for order, name in enumerate(["Barbell Overhead Press", "Goblet Squat", "Kettlebell Swing"]):
        exercise = Exercise.objects.get(name=name)
        PrescribedExercise.objects.create(
            day=day, pattern=exercise.pattern, exercise=exercise, target_sets=3,
            target_reps_min=exercise.rep_range_min, target_reps_max=exercise.rep_range_max,
            target_rest_seconds=exercise.rest_seconds, target_load=8.0, order=order,
        )

    client = APIClient()
    client.force_authenticate(user=user)
    plan = client.get("/cauldron/api/today/")
    assert plan.status_code == 200
    created = client.post("/cauldron/api/sessions/", {"sets": [
        {"exercise": s["exercise"], "prescription": s["prescription"], "set_index": s["set_index"]}
        for s in plan.json()["set_logs"]
    ]}, format="json")
    assert created.status_code == 201
    session = created.json()

    resp = client.post(
        f"/cauldron/api/sessions/{session['uuid']}/log/",
        {"sets": {s["uuid"]: {"actual_reps": 8, "actual_load": 8.0} for s in session["set_logs"]}},
        format="json",
    )

    assert resp.status_code == 200
    # Three implements, one inventory each — and nothing re-enumerated after.
    assert loads._enumerate_cached.cache_info().misses <= 3


def test_stored_junk_cannot_blow_up_the_table():
    """Rows written before the serializer caps: non-finite and absurd plates are
    dropped, huge counts clamped, so enumeration stays bounded."""
    box = loads.normalize_plates([
        {"weight": "inf", "count": 4}, {"weight": "nan", "count": 4},
        {"weight": 10_000, "count": 4}, {"weight": 2.5, "count": 10**9},
    ])
    assert box == [{"weight": 2.5, "count": loads.MAX_PLATE_COUNT}]
    assert len(loads._enumerate(box, 0.0, 1, 1)) == loads.MAX_PLATE_COUNT + 1

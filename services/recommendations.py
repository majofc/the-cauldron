"""Recommended daily goals from the user's body data (#105).

General references for healthy adults, computed on read from the Cauldron
profile (weight, birth year, sex, activity level) — never stored or versioned,
so a change to the profile changes them at once, history included.

Sources, in order of preference: WHO/FAO first, then the US National Academies'
Dietary Reference Intakes (NASEM DRI) for what WHO doesn't set by age and sex.

- Energy: FAO/WHO/UNU 2004 basal metabolic rate × physical activity level
  (PAL), as a ``between`` goal of −10 % / +10 % — the ±10 % is our choice.
  No height needed: these equations are weight-based.
- Protein ≥ 0.83 g/kg (WHO/FAO/UNU 2007). Fat 20 % (DRI AMDR floor) – 30 %
  (WHO) of that energy, saturated < 10 %, trans < 1 %; carbohydrates 45–65 %
  (DRI AMDR); added sugars < 10 % (WHO free sugars, approximated by #104).
- Sodium < 2000 mg, potassium ≥ 3510 mg, fiber ≥ 25 g (WHO).
- Calcium, iron, vitamins A/C/D/B12, ALA and linoleic acid: DRI (RDA or AI).
- No recommendation (``NO_RECOMMENDATION``): mono/poly fat, cholesterol, EPA
  and DHA individually, total sugars.

Missing data falls back to the most conservative value: every candidate
(both sexes, every adult age band) is computed and combined — the largest
minimum and the smallest maximum. A ``between`` goal whose combined range is
empty is left out (``unresolved``). Unknown activity is sedentary. Unknown
weight leaves out every weight-based goal (``needs_weight``). Under 18 there
are no recommendations at all (``status: "minor"``) — judged by the youngest
age the birth year allows, so someone turning 18 this year waits for next year.
"""

from dataclasses import dataclass

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from the_cauldron.models import CauldronProfile, NutrientGoal, ReagentNutrients

AT_LEAST, AT_MOST, BETWEEN = NutrientGoal.Kind.AT_LEAST, NutrientGoal.Kind.AT_MOST, NutrientGoal.Kind.BETWEEN
MALE, FEMALE = CauldronProfile.Sex.MALE, CauldronProfile.Sex.FEMALE
ADULT_AGE = 18

# FAO/WHO/UNU 2004 BMR (kcal/day) = a × weight + b, by sex and age band [from, to).
BMR = {
    MALE: [(18, 30, 15.057, 692.2), (30, 60, 11.472, 873.1), (60, None, 11.711, 587.7)],
    FEMALE: [(18, 30, 14.818, 486.6), (30, 60, 8.126, 845.6), (60, None, 9.082, 658.5)],
}
# A representative PAL inside each FAO/WHO/UNU 2004 band (1.40–1.69, 1.70–1.99, 2.00–2.40).
PAL = {
    CauldronProfile.Activity.SEDENTARY: 1.53,
    CauldronProfile.Activity.MODERATE: 1.76,
    CauldronProfile.Activity.VIGOROUS: 2.25,
}
ENERGY_MARGIN = 0.10
PROTEIN_G_PER_KG = 0.83
KCAL_PER_G = {"fat": 9.0, "carbs": 4.0}

# NASEM DRI (RDA or AI) minimums: nutrient → [(age from, age to, male, female)].
DRI = {
    "calcium_mg": [(18, 19, 1300, 1300), (19, 51, 1000, 1000), (51, 71, 1000, 1200), (71, None, 1200, 1200)],
    "iron_mg": [(18, 19, 11, 15), (19, 51, 8, 18), (51, None, 8, 8)],
    "vit_a_ug": [(18, None, 900, 700)],
    "vit_c_mg": [(18, 19, 75, 65), (19, None, 90, 75)],
    "vit_d_ug": [(18, 71, 15, 15), (71, None, 20, 20)],
    "vit_b12_ug": [(18, None, 2.4, 2.4)],
    "omega3_ala_g": [(18, None, 1.6, 1.1)],
    "omega6_la_g": [(18, 19, 16, 11), (19, 51, 17, 12), (51, None, 14, 11)],
}
# One age inside every band of BMR and DRI: what "unknown age" is computed over.
CANDIDATE_AGES = (18, 25, 45, 55, 65, 75)

SOURCES = {
    "energy": "FAO/WHO/UNU 2004",
    "protein": "WHO/FAO/UNU 2007",
    "fat": "WHO 2023 · NASEM AMDR",
    "who_fats": "WHO 2023",
    "carbs": "NASEM AMDR",
    "free_sugars": "WHO 2015",
    "who_2012": "WHO 2012",
    "fiber": "WHO 2023",
    "dri": "NASEM DRI",
}
NOTES = {
    "energy": gettext_noop("Energy requirement ±10 % (the ±10 % is our choice)."),
    "fat": gettext_noop("20–30 % of the energy requirement."),
    "sat_fat": gettext_noop("Under 10 % of the energy requirement."),
    "trans_fat": gettext_noop("Under 1 % of the energy requirement."),
    "carbs": gettext_noop("45–65 % of the energy requirement."),
    "added_sugar": gettext_noop("Under 10 % of the energy requirement (WHO free sugars, approximated)."),
    "protein": gettext_noop("0.83 g per kg of body weight."),
}
NO_RECOMMENDATION = {
    "sugar_g": gettext_noop("No limit on total sugars — only on added (free) sugars."),
    "mono_fat_g": gettext_noop("No recommendation."),
    "poly_fat_g": gettext_noop("No recommendation."),
    "omega3_epa_g": gettext_noop("No recommendation for EPA on its own."),
    "omega3_dha_g": gettext_noop("No recommendation for DHA on its own."),
    "cholesterol_mg": gettext_noop("No recommendation."),
}
WEIGHT_BASED = ("kcal", "protein_g", "carbs_g", "added_sugar_g", "fat_g", "sat_fat_g", "trans_fat_g")


@dataclass(frozen=True)
class Recommendation:
    """Shaped like a ``NutrientGoal`` for ``nutrition.bounds`` / ``judge``."""

    nutrient: str
    kind: str
    min_value: float = None
    max_value: float = None
    source: str = ""
    note: str = ""


def _band(rows, age):
    return next(r for r in rows if r[0] <= age and (r[1] is None or age < r[1]))


def energy_requirement(sex, age, weight_kg, activity):
    """FAO/WHO/UNU 2004 daily energy requirement, kcal."""
    _lo, _hi, a, b = _band(BMR[sex], age)
    return (a * weight_kg + b) * PAL[activity or CauldronProfile.Activity.SEDENTARY]


def _single(sex, age, weight_kg, activity):
    """Every recommendation for one known sex and age: ``{nutrient: (kind, low, high, source, note)}``."""
    recs = {
        "sodium_mg": (AT_MOST, None, 2000, SOURCES["who_2012"], ""),
        "potassium_mg": (AT_LEAST, 3510, None, SOURCES["who_2012"], ""),
        "fiber_g": (AT_LEAST, 25, None, SOURCES["fiber"], ""),
    }
    for nutrient, rows in DRI.items():
        _lo, _hi, male, female = _band(rows, age)
        recs[nutrient] = (AT_LEAST, male if sex == MALE else female, None, SOURCES["dri"], "")
    if weight_kg:
        kcal = energy_requirement(sex, age, weight_kg, activity)
        fat, carbs = kcal / KCAL_PER_G["fat"], kcal / KCAL_PER_G["carbs"]
        recs.update({
            "kcal": (BETWEEN, kcal * (1 - ENERGY_MARGIN), kcal * (1 + ENERGY_MARGIN), SOURCES["energy"], NOTES["energy"]),
            "protein_g": (AT_LEAST, PROTEIN_G_PER_KG * weight_kg, None, SOURCES["protein"], NOTES["protein"]),
            "fat_g": (BETWEEN, 0.20 * fat, 0.30 * fat, SOURCES["fat"], NOTES["fat"]),
            "sat_fat_g": (AT_MOST, None, 0.10 * fat, SOURCES["who_fats"], NOTES["sat_fat"]),
            "trans_fat_g": (AT_MOST, None, 0.01 * fat, SOURCES["who_fats"], NOTES["trans_fat"]),
            "carbs_g": (BETWEEN, 0.45 * carbs, 0.65 * carbs, SOURCES["carbs"], NOTES["carbs"]),
            "added_sugar_g": (AT_MOST, None, 0.10 * carbs, SOURCES["free_sugars"], NOTES["added_sugar"]),
        })
    return recs


def _round(nutrient, value):
    if value is None:
        return None
    if nutrient == "kcal" or nutrient.endswith("_mg"):
        return round(value)
    return round(value, 1)


def _combine(nutrient, candidates):
    """The most conservative of several candidates: largest minimum, smallest
    maximum. ``None`` when a range comes out empty."""
    kind, _lo, _hi, source, note = candidates[0]
    lows = [c[1] for c in candidates if c[1] is not None]
    highs = [c[2] for c in candidates if c[2] is not None]
    low = _round(nutrient, max(lows)) if lows else None
    high = _round(nutrient, min(highs)) if highs else None
    if low is not None and high is not None and low > high:
        return None
    return Recommendation(nutrient, kind, low, high, source, _(note) if note else "")


def recommended(profile, today=None):
    """The recommendation set for a ``CauldronProfile`` (or ``None``: nothing
    known yet). See the module docstring for every rule."""
    age = profile.age(today) if profile else None
    # By birth year alone the user may not have had this year's birthday yet:
    # judge "adult" by the youngest age they could be.
    if age is not None and age - 1 < ADULT_AGE:
        return {"status": "minor", "goals": {}, "assumed": [], "needs_weight": [], "unresolved": []}
    sex = profile.sex if profile else None
    sexes = [sex] if sex in (MALE, FEMALE) else [MALE, FEMALE]
    ages = [age] if age is not None else list(CANDIDATE_AGES)
    weight = profile.weight_kg if profile else None
    activity = (profile.activity_level if profile else "") or ""
    per_nutrient = {}
    for s in sexes:
        for a in ages:
            for nutrient, rec in _single(s, a, weight, activity).items():
                per_nutrient.setdefault(nutrient, []).append(rec)
    goals, unresolved = {}, []
    for nutrient in ReagentNutrients.FIELDS:
        if nutrient not in per_nutrient:
            continue
        rec = _combine(nutrient, per_nutrient[nutrient])
        if rec is None:
            unresolved.append(nutrient)
        else:
            goals[nutrient] = rec
    assumed = [name for name, unknown in (
        ("sex", len(sexes) > 1), ("age", age is None), ("activity", not activity),
    ) if unknown]
    return {
        "status": "adult",
        "goals": goals,
        "assumed": assumed,
        "needs_weight": [] if weight else list(WEIGHT_BASED),
        "unresolved": unresolved,
    }


def recommended_for(user, today=None):
    """``recommended`` for a user; reading never creates the profile row."""
    profile = CauldronProfile.objects.filter(user=user).first()
    return recommended(profile, today or timezone.localdate())


def payload(user, today=None):
    """JSON-ready: the recommendations, the nutrients with none, and why any
    is missing (under 18, no weight, contradictory fallbacks)."""
    rec = recommended_for(user, today)
    return {
        "status": rec["status"],
        "assumed": rec["assumed"],
        "needs_weight": rec["needs_weight"],
        "unresolved": rec["unresolved"],
        "goals": [
            {"nutrient": g.nutrient, "kind": g.kind, "min_value": g.min_value, "max_value": g.max_value,
             "source": g.source, "note": g.note}
            for g in rec["goals"].values()
        ],
        "none": [{"nutrient": n, "note": _(note)} for n, note in NO_RECOMMENDATION.items()]
        if rec["status"] == "adult" else [],
    }

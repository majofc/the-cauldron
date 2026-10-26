"""Rebuild ``seed_data/grimoire_reagents.json`` from a USDA FoodData Central
SR Legacy download. Maintainer tool — never run at deploy.

    curl -LO https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_json_2018-04.zip
    unzip FoodData_Central_sr_legacy_food_json_2018-04.zip
    python manage.py build_grimoire_reagents FoodData_Central_sr_legacy_food_json_2018-04.json

The output is committed, so ``seed_grimoire`` needs no network or API key.

Mapping notes:
- Values are per 100 g, as USDA publishes them. A nutrient USDA does not list
  for a food is written as ``null`` (unknown) — never 0. A USDA 0 is kept.
- ALA prefers nutrient 851 (18:3 n-3 c,c,c) and falls back to 619 (18:3
  undifferentiated); LA prefers 675 (18:2 n-6 c,c) and falls back to 618.
- Added sugars (#104) come from nutrient 539 when USDA lists it (SR Legacy
  never does). Otherwise an unprocessed food — a category in
  ``UNPROCESSED_CATEGORIES`` whose description doesn't match ``SWEETENED`` —
  gets 0, as does a food in ``NO_ADDED_SUGAR``; a sweetener in
  ``ALL_ADDED_SUGAR`` (sugar, honey) gets its total sugars; anything else stays
  unknown.
- cup / tbsp / tsp weights come from USDA portions; ``piece`` from the portion
  named in REAGENTS. Density is the cup weight / 236.588 ml (US cup).
"""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

OUT = Path(__file__).resolve().parents[2] / "seed_data" / "grimoire_reagents.json"
US_CUP_ML = 236.588

# nutrient field → USDA nutrient numbers, first present wins
NUTRIENT_NUMBERS = {
    "kcal": ["208"],
    "protein_g": ["203"],
    "carbs_g": ["205"],
    "sugar_g": ["269"],
    "added_sugar_g": ["539"],
    "fiber_g": ["291"],
    "fat_g": ["204"],
    "sat_fat_g": ["606"],
    "mono_fat_g": ["645"],
    "poly_fat_g": ["646"],
    "trans_fat_g": ["605"],
    "omega3_ala_g": ["851", "619"],
    "omega3_epa_g": ["629"],
    "omega3_dha_g": ["621"],
    "omega6_la_g": ["675", "618"],
    "cholesterol_mg": ["601"],
    "sodium_mg": ["307"],
    "potassium_mg": ["306"],
    "calcium_mg": ["301"],
    "iron_mg": ["303"],
    "vit_a_ug": ["320"],
    "vit_c_mg": ["401"],
    "vit_d_ug": ["328"],
    "vit_b12_ug": ["418"],
}

# SR Legacy food categories whose plain foods carry no added sugar.
UNPROCESSED_CATEGORIES = {
    "Vegetables and Vegetable Products",
    "Fruits and Fruit Juices",
    "Poultry Products",
    "Beef Products",
    "Pork Products",
    "Lamb, Veal, and Game Products",
    "Finfish and Shellfish Products",
    "Legumes and Legume Products",
    "Nut and Seed Products",
    "Cereal Grains and Pasta",
    "Spices and Herbs",
    "Fats and Oils",
    "Dairy and Egg Products",
}
# A description that says (or usually means) sugar was added keeps added sugars
# unknown even inside those categories: sweetened dried fruit, fruit yogurts,
# dressings, catsup, relish, flavoured milks, vanilla extract, peanut butter…
SWEETENED = re.compile(
    r"(?<!un)sweeten|sweet sauce|candied|syrup|glaze|honey|sugar|chocolate|flavou?r|vanilla|dulce|"
    r"dressing|catsup|ketchup|relish|pickle|creamer|topping|peanut butter|yogurt, fruit|fruit variety|"
    r"nectar|eggnog|custard|maraschino|substitute|dry mix",
    re.IGNORECASE,
)
# Seeded foods outside those categories that plainly have none added: tap water,
# plain oats, unsweetened cocoa, baking powder.
NO_ADDED_SUGAR = {175103, 173904, 169593, 172804}
# Sweeteners whose every sugar counts as added — FDA "packaged as such", WHO free
# sugars: granulated sugar, honey. Added sugars = total sugars.
ALL_ADDED_SUGAR = {169655, 169640}

# (fdc_id, name, name_es, category, piece portion modifier or None)
REAGENTS = [
    (172420, "Lentils", "Lentejas", "legume", None),
    (173756, "Chickpeas", "Garbanzos", "legume", None),
    (173734, "Black beans", "Frijoles negros", "legume", None),
    (168877, "White rice", "Arroz blanco", "grain", None),
    (169703, "Brown rice", "Arroz integral", "grain", None),
    (173904, "Rolled oats", "Avena", "grain", None),
    (168894, "All-purpose flour", "Harina de trigo", "grain", None),
    (169736, "Pasta", "Pasta", "grain", None),
    (168874, "Quinoa", "Quinoa", "grain", None),
    (170000, "Onion", "Cebolla", "vegetable", "medium (2-1/2\" dia)"),
    (169230, "Garlic", "Ajo", "vegetable", "clove"),
    (170457, "Tomato", "Jitomate", "vegetable", "medium whole (2-3/5\" dia)"),
    (170393, "Carrot", "Zanahoria", "vegetable", "medium"),
    (170026, "Potato", "Papa", "vegetable", None),
    (168462, "Spinach", "Espinaca", "vegetable", None),
    (170379, "Broccoli", "Brócoli", "vegetable", None),
    (170108, "Red bell pepper", "Pimiento rojo", "vegetable", None),
    (169988, "Celery", "Apio", "vegetable", None),
    (169247, "Romaine lettuce", "Lechuga romana", "vegetable", None),
    (168409, "Cucumber", "Pepino", "vegetable", None),
    (169251, "White mushrooms", "Champiñones", "vegetable", None),
    (168482, "Sweet potato", "Camote", "vegetable", None),
    (169291, "Zucchini", "Calabacita", "vegetable", None),
    (171705, "Avocado", "Aguacate", "fruit", None),
    (167747, "Lemon juice", "Jugo de limón amarillo", "fruit", None),
    (168156, "Lime juice", "Jugo de limón", "fruit", None),
    (169998, "Sweet corn", "Elote", "vegetable", None),
    (173944, "Banana", "Plátano", "fruit", None),
    (171688, "Apple", "Manzana", "fruit", None),
    (167762, "Strawberries", "Fresas", "fruit", None),
    (171711, "Blueberries", "Moras azules", "fruit", None),
    (171287, "Egg", "Huevo", "protein", "large"),
    (171077, "Chicken breast", "Pechuga de pollo", "protein", None),
    (171796, "Ground beef 85% lean", "Carne molida de res 85/15", "protein", None),
    (175167, "Salmon", "Salmón", "protein", None),
    (172475, "Firm tofu", "Tofu firme", "legume", None),
    (171265, "Whole milk", "Leche entera", "dairy", None),
    (170894, "Greek yogurt, nonfat", "Yogur griego natural", "dairy", None),
    (173414, "Cheddar cheese", "Queso cheddar", "dairy", None),
    (170848, "Parmesan cheese", "Queso parmesano", "dairy", None),
    (173410, "Butter", "Mantequilla", "fat", None),
    (170859, "Heavy cream", "Crema para batir", "dairy", None),
    (171413, "Olive oil", "Aceite de oliva", "fat", None),
    (170567, "Almonds", "Almendras", "nut_seed", None),
    (172470, "Peanut butter", "Crema de cacahuate", "nut_seed", None),
    (170554, "Chia seeds", "Semillas de chía", "nut_seed", None),
    (169655, "Sugar", "Azúcar", "sweetener", None),
    (169640, "Honey", "Miel", "sweetener", None),
    (173468, "Salt", "Sal", "condiment", None),
    (171329, "Paprika", "Pimentón", "herb_spice", None),
    (170923, "Cumin", "Comino", "herb_spice", None),
    (170931, "Black pepper", "Pimienta negra", "herb_spice", None),
    (171320, "Cinnamon", "Canela", "herb_spice", None),
    (172231, "Turmeric", "Cúrcuma", "herb_spice", None),
    (169593, "Cocoa powder", "Cacao en polvo", "sweetener", None),
    (170173, "Coconut milk", "Leche de coco", "beverage", None),
    (174277, "Soy sauce", "Salsa de soya", "condiment", None),
    (173469, "Cider vinegar", "Vinagre de manzana", "condiment", None),
    (169231, "Ginger", "Jengibre", "herb_spice", None),
    (169997, "Cilantro", "Cilantro", "herb_spice", None),
    (172232, "Basil", "Albahaca", "herb_spice", None),
    (170051, "Canned tomatoes", "Jitomate en lata", "vegetable", None),
    (171583, "Vegetable broth", "Caldo de verduras", "beverage", None),
    (172804, "Baking powder", "Polvo para hornear", "other", None),
    (173471, "Vanilla extract", "Extracto de vainilla", "condiment", None),
    (175103, "Water", "Agua", "beverage", None),
]


# Facts USDA has no portion data for. Water is 1 g/ml by definition.
OVERRIDES = {
    175103: {"density_g_per_ml": 1.0, "grams_per_unit": {"cup": 236.59, "tbsp": 14.79, "tsp": 4.93}},
}


def _household_unit(modifier):
    m = (modifier or "").lower().strip()
    if m == "cup" or m.startswith(("cup,", "cup ")):
        return "cup"
    if m in ("tbsp", "tablespoon") or m.startswith(("tbsp ", "tbsp,", "tablespoon ")):
        return "tbsp"
    if m in ("tsp", "teaspoon") or m.startswith(("tsp ", "tsp,", "teaspoon ")):
        return "tsp"
    return None


def _unprocessed(food):
    if food["fdcId"] in NO_ADDED_SUGAR:
        return True
    category = (food.get("foodCategory") or {}).get("description")
    return category in UNPROCESSED_CATEGORIES and not SWEETENED.search(food["description"])


def build_entry(food, name, name_es, category, piece_modifier):
    by_number = {n["nutrient"]["number"]: n.get("amount") for n in food["foodNutrients"]}
    nutrients = {}
    for field, numbers in NUTRIENT_NUMBERS.items():
        value = next((by_number[n] for n in numbers if by_number.get(n) is not None), None)
        nutrients[field] = round(float(value), 3) if value is not None else None
    if nutrients["added_sugar_g"] is None:
        if food["fdcId"] in ALL_ADDED_SUGAR:
            nutrients["added_sugar_g"] = nutrients["sugar_g"]
        elif _unprocessed(food):
            nutrients["added_sugar_g"] = 0.0

    grams = {}
    for portion in food.get("foodPortions", []):
        amount, weight = portion.get("amount") or 1, portion.get("gramWeight")
        if not weight:
            continue
        unit = _household_unit(portion.get("modifier"))
        if unit and unit not in grams:
            grams[unit] = round(weight / amount, 2)
        if piece_modifier and portion.get("modifier") == piece_modifier:
            grams["piece"] = round(weight / amount, 2)
    if piece_modifier and "piece" not in grams:
        raise CommandError(f"{food['fdcId']}: no portion named {piece_modifier!r}")

    entry = {
        "fdc_id": food["fdcId"],
        "usda_description": food["description"],
        "name": name,
        "name_es": name_es,
        "category": category,
        "grams_per_unit": grams,
        "density_g_per_ml": round(grams["cup"] / US_CUP_ML, 3) if "cup" in grams else None,
        "nutrients": nutrients,
    }
    entry.update(OVERRIDES.get(food["fdcId"], {}))
    return entry


class Command(BaseCommand):
    help = "Rebuild seed_data/grimoire_reagents.json from a USDA SR Legacy JSON download."

    def add_arguments(self, parser):
        parser.add_argument("sr_legacy_json")

    def handle(self, *args, sr_legacy_json, **options):
        with open(sr_legacy_json, encoding="utf-8") as fh:
            foods = {f["fdcId"]: f for f in json.load(fh)["SRLegacyFoods"]}
        missing = [r[0] for r in REAGENTS if r[0] not in foods]
        if missing:
            raise CommandError(f"fdc_ids not in the download: {missing}")
        entries = [build_entry(foods[r[0]], *r[1:]) for r in REAGENTS]
        OUT.parent.mkdir(exist_ok=True)
        payload = {
            "source": "USDA FoodData Central, SR Legacy (April 2018). Public domain.",
            "per": "100 g",
            "reagents": entries,
        }
        OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(entries)} reagents to {OUT}"))

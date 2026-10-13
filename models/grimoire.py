"""The Grimoire — recipe book data model (#67).

Vocabulary: a *spell* is a recipe, a *reagent* is an ingredient. Reagents form a
shared catalog (USDA-seeded, staff-approved) plus each user's own private
additions; spells are owned by one user, private until published, and can be
forked ("Make it mine") into an independent private copy.

Nutrients are always per 100 g and nullable: ``None`` means *unknown* and is
shown as "—", never coerced to 0. A stored 0 is a measured zero.
"""

import unicodedata

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .forge import ForgeBaseModel


def normalize_search(text):
    """Lower-case, accent-free form used for case/accent-insensitive search.

    Stored on the row (not computed in SQL) so search behaves the same on
    Postgres and the sqlite fallback without the ``unaccent`` extension.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


class Tag(ForgeBaseModel):
    slug = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=60)
    name_es = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Reagent(ForgeBaseModel):
    """One ingredient. ``status`` gates visibility: only ``approved`` reagents
    are shared; anything else is visible to its creator alone."""

    class Category(models.TextChoices):
        VEGETABLE = "vegetable", "Vegetable"
        FRUIT = "fruit", "Fruit"
        GRAIN = "grain", "Grain & flour"
        LEGUME = "legume", "Legume"
        PROTEIN = "protein", "Meat, fish & eggs"
        DAIRY = "dairy", "Dairy"
        FAT = "fat", "Oil & fat"
        NUT_SEED = "nut_seed", "Nut & seed"
        HERB_SPICE = "herb_spice", "Herb & spice"
        SWEETENER = "sweetener", "Sweetener"
        CONDIMENT = "condiment", "Condiment & sauce"
        BEVERAGE = "beverage", "Beverage & liquid"
        OTHER = "other", "Other"

    class Source(models.TextChoices):
        USDA = "usda", "USDA FoodData Central"
        USER = "user", "User"
        STAFF = "staff", "Staff"

    class Status(models.TextChoices):
        PRIVATE = "private", "Private"
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"

    name = models.CharField(max_length=120)
    name_es = models.CharField(max_length=120, blank=True, default="")
    search_name = models.CharField(max_length=250, editable=False, db_index=True, default="")
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.USER)
    fdc_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grimoire_reagents",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PRIVATE)
    # Grams in ONE of each household unit, e.g. {"cup": 240, "tbsp": 15, "piece": 50}.
    grams_per_unit = models.JSONField(default=dict, blank=True)
    density_g_per_ml = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(0.01)]
    )

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        # NFKD can lengthen text ("½" → "1⁄2"); clip so a long name can't overflow.
        self.search_name = normalize_search(f"{self.name} {self.name_es}")[:250]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ReagentNutrients(ForgeBaseModel):
    """Per-100 g nutrient profile. Every field nullable — null is "unknown"."""

    FIELDS = (
        "kcal", "protein_g", "carbs_g", "sugar_g", "fiber_g", "fat_g", "sat_fat_g",
        "mono_fat_g", "poly_fat_g", "trans_fat_g", "omega3_ala_g", "omega3_epa_g",
        "omega3_dha_g", "omega6_la_g", "cholesterol_mg", "sodium_mg", "potassium_mg",
        "calcium_mg", "iron_mg", "vit_a_ug", "vit_c_mg", "vit_d_ug", "vit_b12_ug",
    )

    reagent = models.OneToOneField(Reagent, on_delete=models.CASCADE, related_name="nutrients")
    kcal = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    protein_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    carbs_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    sugar_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    fiber_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    fat_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    sat_fat_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    mono_fat_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    poly_fat_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    trans_fat_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    omega3_ala_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    omega3_epa_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    omega3_dha_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    omega6_la_g = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    cholesterol_mg = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    sodium_mg = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    potassium_mg = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    calcium_mg = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    iron_mg = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    vit_a_ug = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    vit_c_mg = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    vit_d_ug = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])
    vit_b12_ug = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name_plural = "reagent nutrients"

    def __str__(self):
        return f"Nutrients for {self.reagent}"


class Spell(ForgeBaseModel):
    """A recipe. Private by default; only the owner may change it."""

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        PUBLIC = "public", "Public"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grimoire_spells"
    )
    title = models.CharField(max_length=150)
    # Spanish text — set only on seeded starter spells (staff-curated). A user's
    # own spell is written once, in their language, in the base fields.
    title_es = models.CharField(max_length=150, blank=True, default="")
    search_title = models.CharField(max_length=310, editable=False, db_index=True, default="")
    description = models.TextField(blank=True, default="")
    description_es = models.TextField(blank=True, default="")
    servings = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    prep_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    cook_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    # Ordered list of step strings.
    steps = models.JSONField(default=list, blank=True)
    steps_es = models.JSONField(default=list, blank=True)
    visibility = models.CharField(
        max_length=10, choices=Visibility.choices, default=Visibility.PRIVATE, db_index=True
    )
    forked_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="forks"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="spells")

    class Meta:
        ordering = ["-updated_at"]

    def save(self, *args, **kwargs):
        self.search_title = normalize_search(f"{self.title} {self.title_es}")[:310]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class SpellReagent(ForgeBaseModel):
    """One reagent line of a spell. ``grams`` is derived from quantity + unit at
    write time (services.grimoire.to_grams) and stored."""

    class Unit(models.TextChoices):
        G = "g", "g"
        KG = "kg", "kg"
        ML = "ml", "ml"
        L = "l", "l"
        TSP = "tsp", "tsp"
        TBSP = "tbsp", "tbsp"
        CUP = "cup", "cup"
        OZ = "oz", "oz"
        LB = "lb", "lb"
        PIECE = "piece", "piece"

    spell = models.ForeignKey(Spell, on_delete=models.CASCADE, related_name="lines")
    reagent = models.ForeignKey(Reagent, on_delete=models.PROTECT, related_name="spell_lines")
    quantity = models.FloatField(validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=8, choices=Unit.choices, default=Unit.G)
    grams = models.FloatField(validators=[MinValueValidator(0)])
    note = models.CharField(max_length=120, blank=True, default="")
    note_es = models.CharField(max_length=120, blank=True, default="")  # starter spells only
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "created_at"]

    def __str__(self):
        return f"{self.quantity} {self.unit} {self.reagent}"


class GrimoirePreferences(ForgeBaseModel):
    """Per-user Grimoire settings. Created lazily on first read."""

    class UnitSystem(models.TextChoices):
        METRIC = "metric", "Metric (g, ml)"
        US = "us", "US customary (oz, cups)"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grimoire_preferences"
    )
    unit_system = models.CharField(max_length=10, choices=UnitSystem.choices, default=UnitSystem.METRIC)

    class Meta:
        verbose_name_plural = "grimoire preferences"

    def __str__(self):
        return f"Grimoire preferences for {self.user}"


class Casting(ForgeBaseModel):
    """One time a user made a spell (#68): when, how it went, what changed.

    Adjustments live on the casting only — a casting never edits the spell.
    ``spell`` is SET_NULL so another user's castings survive when the author
    deletes a public spell ("spell removed", with ``spell_title`` kept); the
    author's own castings are deleted with the spell (signals.py).
    """

    spell = models.ForeignKey(
        Spell, null=True, blank=True, on_delete=models.SET_NULL, related_name="castings"
    )
    spell_title = models.CharField(max_length=150, blank=True, default="")  # snapshot at casting time
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grimoire_castings"
    )
    cast_on = models.DateField()
    score = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    notes = models.TextField(blank=True, default="")
    # [{"line": int|None, "reagent": uuid|None, "reagent_name": str,
    #   "original_qty": float|None, "original_unit": str|None, "used_qty": float,
    #   "unit": str}] — only lines that changed. ``line`` is the spell line's
    #   position, so a spell listing one reagent twice keeps each change apart.
    adjustments = models.JSONField(default=list, blank=True)
    substitutions = models.TextField(blank=True, default="")  # free text: "lime instead of lemon"

    class Meta:
        ordering = ["-cast_on", "-created_at"]
        indexes = [models.Index(fields=["user", "spell"])]

    def __str__(self):
        return f"{self.spell_title or self.spell_id} on {self.cast_on}"

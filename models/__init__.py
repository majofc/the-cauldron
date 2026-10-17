"""The Cauldron data model, one module per section.

- ``forge``    — The Forge (adaptive calisthenics)
- ``grimoire`` — The Grimoire (recipe book, #67)
- ``nutrition`` — daily goals, meal log, plan, legal acceptance (#85)

Everything is re-exported here so ``from the_cauldron.models import X`` keeps
working for every caller and migration.
"""

from .forge import (  # noqa: F401
    AssessmentResult,
    AssessmentSession,
    BlockedExercise,
    Equipment,
    Exercise,
    ForgeBaseModel,
    MovementPattern,
    Muscle,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    UserEquipmentProfile,
    WorkoutSession,
)
from .grimoire import (  # noqa: F401
    Casting,
    GrimoirePreferences,
    Reagent,
    ReagentNutrients,
    Spell,
    SpellReagent,
    Tag,
    normalize_search,
)
from .nutrition import (  # noqa: F401
    CauldronProfile,
    IntakeEntry,
    LegalAcceptance,
    NutrientGoal,
    PlannedMeal,
)

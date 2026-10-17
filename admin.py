from django.contrib import admin

from the_cauldron.models import (
    AssessmentResult,
    Casting,
    AssessmentSession,
    BlockedExercise,
    Equipment,
    Exercise,
    LegalAcceptance,
    MovementPattern,
    Muscle,
    PrescribedExercise,
    Program,
    ProgramDay,
    Reagent,
    ReagentNutrients,
    SetLog,
    Spell,
    SpellReagent,
    Tag,
    UserEquipmentProfile,
    WorkoutSession,
)


@admin.register(MovementPattern)
class MovementPatternAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_lower_body")


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_loadable", "load_unit")


@admin.register(Muscle)
class MuscleAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "region")
    list_filter = ("region",)
    search_fields = ("name", "key")


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "pattern", "difficulty_rank", "progression_mode", "placement_threshold")
    list_filter = ("pattern", "progression_mode", "is_timed")
    search_fields = ("name",)
    filter_horizontal = ("muscles",)


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("uuid", "user", "split", "is_active", "created_at")
    list_filter = ("is_active", "split")


@admin.register(WorkoutSession)
class WorkoutSessionAdmin(admin.ModelAdmin):
    list_display = ("uuid", "user", "status", "scheduled_for", "performed_at")
    list_filter = ("status",)


@admin.register(BlockedExercise)
class BlockedExerciseAdmin(admin.ModelAdmin):
    list_display = ("user", "exercise", "reason", "created_at")
    search_fields = ("user__email", "exercise__name")
    autocomplete_fields = ("exercise",)


admin.site.register(
    [
        UserEquipmentProfile,
        AssessmentSession,
        AssessmentResult,
        ProgramDay,
        PrescribedExercise,
        SetLog,
    ]
)


# ─── Grimoire (#67) ──────────────────────────────────────────────────────────


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "name_es", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ReagentNutrientsInline(admin.StackedInline):
    model = ReagentNutrients
    can_delete = False


@admin.register(Reagent)
class ReagentAdmin(admin.ModelAdmin):
    """User reagents arrive ``private`` (or ``pending`` when the user asked to
    share). Approving makes one visible to every user."""

    list_display = ("name", "name_es", "category", "source", "status", "created_by", "fdc_id")
    list_filter = ("status", "source", "category")
    search_fields = ("name", "name_es", "search_name")
    readonly_fields = ("search_name",)
    inlines = [ReagentNutrientsInline]
    actions = ["approve", "make_private"]

    @admin.action(description="Approve — share with every user")
    def approve(self, request, queryset):
        queryset.update(status=Reagent.Status.APPROVED)

    @admin.action(description="Return to private (creator only)")
    def make_private(self, request, queryset):
        queryset.update(status=Reagent.Status.PRIVATE)


class SpellReagentInline(admin.TabularInline):
    model = SpellReagent
    extra = 0
    autocomplete_fields = ("reagent",)


@admin.register(Spell)
class SpellAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "visibility", "servings", "forked_from", "updated_at")
    list_filter = ("visibility", "tags")
    search_fields = ("title", "owner__username")
    raw_id_fields = ("owner", "forked_from")
    inlines = [SpellReagentInline]


@admin.register(Casting)
class CastingAdmin(admin.ModelAdmin):
    list_display = ("spell_title", "user", "cast_on", "score")
    list_filter = ("score",)
    search_fields = ("spell_title", "user__username")
    raw_id_fields = ("spell", "user")


@admin.register(LegalAcceptance)
class LegalAcceptanceAdmin(admin.ModelAdmin):
    """Read-only record of who accepted which legal version and when (#84) —
    for ARCO requests. Rows are append-only; nothing is editable here."""

    list_display = ("user", "document", "version", "accepted_at", "withdrawn_at")
    list_filter = ("document", "version")
    search_fields = ("user__username",)
    date_hierarchy = "accepted_at"
    readonly_fields = ("user", "document", "version", "accepted_at", "withdrawn_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

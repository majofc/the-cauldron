from django.contrib import admin

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    BlockedExercise,
    Equipment,
    Exercise,
    MovementPattern,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    UserEquipmentProfile,
    WorkoutSession,
)


@admin.register(MovementPattern)
class MovementPatternAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_lower_body")


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_loadable", "load_unit")


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "pattern", "difficulty_rank", "progression_mode", "placement_threshold")
    list_filter = ("pattern", "progression_mode", "is_timed")
    search_fields = ("name",)


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

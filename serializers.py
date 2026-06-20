from rest_framework import serializers

from the_cauldron.models import (
    AssessmentResult,
    AssessmentSession,
    Equipment,
    Exercise,
    MovementPattern,
    Muscle,
    PrescribedExercise,
    Program,
    ProgramDay,
    SetLog,
    UserEquipmentProfile,
    WorkoutSession,
)


class MuscleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Muscle
        fields = ["key", "name", "region"]


class MovementPatternSerializer(serializers.ModelSerializer):
    class Meta:
        model = MovementPattern
        fields = ["uuid", "key", "name", "primary_muscles", "is_lower_body"]


class EquipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Equipment
        fields = ["uuid", "key", "name", "is_loadable", "load_unit"]


class ExerciseSerializer(serializers.ModelSerializer):
    pattern_key = serializers.CharField(source="pattern.key", read_only=True)
    required_equipment = serializers.SlugRelatedField(
        slug_field="key", many=True, read_only=True
    )
    muscles = MuscleSerializer(many=True, read_only=True)
    is_unilateral = serializers.SerializerMethodField()

    class Meta:
        model = Exercise
        fields = [
            "uuid", "pattern_key", "name", "difficulty_rank", "progression_mode",
            "rep_range_min", "rep_range_max", "is_timed", "placement_threshold",
            "required_equipment", "muscles", "video_url", "cues", "rest_seconds",
            "is_assessment_anchor", "is_unilateral",
        ]

    def get_is_unilateral(self, obj):
        # Single-limb movements are tested per side; derived from the pattern so
        # no per-row data is needed.
        return obj.pattern.key == "lower_unilateral"


class UserEquipmentProfileSerializer(serializers.ModelSerializer):
    equipment = serializers.SlugRelatedField(
        slug_field="key", many=True, queryset=Equipment.objects.all(), required=False
    )

    class Meta:
        model = UserEquipmentProfile
        fields = [
            "uuid", "equipment", "dumbbell_weights", "band_levels",
            "barbell_min_increment", "barbell_plates", "load_unit",
            "birth_year", "sex",
        ]
        read_only_fields = ["uuid"]


class AssessmentResultSerializer(serializers.ModelSerializer):
    pattern_key = serializers.CharField(source="pattern.key", read_only=True)
    placed_exercise_name = serializers.CharField(
        source="placed_exercise.name", read_only=True
    )

    class Meta:
        model = AssessmentResult
        fields = [
            "uuid", "pattern_key", "tested_exercise", "reps_or_seconds",
            "left_reps", "right_reps", "placed_exercise", "placed_exercise_name",
        ]
        read_only_fields = ["uuid", "placed_exercise", "placed_exercise_name"]


class AssessmentSessionSerializer(serializers.ModelSerializer):
    results = AssessmentResultSerializer(many=True, read_only=True)

    class Meta:
        model = AssessmentSession
        fields = ["uuid", "is_active", "completed_at", "created_at", "results"]


class PrescribedExerciseSerializer(serializers.ModelSerializer):
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    pattern_key = serializers.CharField(source="pattern.key", read_only=True)
    is_timed = serializers.BooleanField(source="exercise.is_timed", read_only=True)
    video_url = serializers.CharField(source="exercise.video_url", read_only=True)
    cues = serializers.CharField(source="exercise.cues", read_only=True)
    pending_progression_name = serializers.CharField(
        source="pending_progression.name", read_only=True, default=None
    )

    class Meta:
        model = PrescribedExercise
        fields = [
            "uuid", "pattern_key", "exercise_name", "is_timed", "video_url", "cues",
            "target_sets", "target_reps_min", "target_reps_max", "target_load",
            "target_rest_seconds", "order",
            "pending_progression", "pending_progression_name",
        ]


class ProgramDaySerializer(serializers.ModelSerializer):
    prescriptions = PrescribedExerciseSerializer(many=True, read_only=True)

    class Meta:
        model = ProgramDay
        fields = ["uuid", "day_index", "name", "prescriptions"]


class ProgramSerializer(serializers.ModelSerializer):
    days = ProgramDaySerializer(many=True, read_only=True)

    class Meta:
        model = Program
        fields = ["uuid", "is_active", "split", "weekly_volume_target", "days", "created_at"]


class SetLogSerializer(serializers.ModelSerializer):
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    video_url = serializers.CharField(source="exercise.video_url", read_only=True)
    is_timed = serializers.BooleanField(source="exercise.is_timed", read_only=True)
    cues = serializers.CharField(source="exercise.cues", read_only=True)
    muscles = MuscleSerializer(source="exercise.muscles", many=True, read_only=True)
    rest_seconds = serializers.SerializerMethodField()

    class Meta:
        model = SetLog
        fields = [
            "uuid", "exercise_name", "video_url", "is_timed", "cues", "muscles",
            "rest_seconds",
            "set_index", "expected_reps", "expected_load",
            "actual_reps", "actual_load", "is_amrap", "rir",
        ]
        read_only_fields = ["uuid", "exercise_name", "set_index", "expected_reps",
                            "expected_load", "is_amrap"]

    def get_rest_seconds(self, obj):
        if obj.prescribed_exercise_id:
            return obj.prescribed_exercise.target_rest_seconds
        return obj.exercise.rest_seconds


class WorkoutSessionSerializer(serializers.ModelSerializer):
    set_logs = SetLogSerializer(many=True, read_only=True)
    day_name = serializers.CharField(source="program_day.name", read_only=True)

    class Meta:
        model = WorkoutSession
        fields = [
            "uuid", "day_name", "scheduled_for", "performed_at", "status", "set_logs",
        ]

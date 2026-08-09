from rest_framework import serializers

from the_cauldron.services import loads

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


class _LoadRecipeMixin:
    """Renders "how to build this load" alongside any load a client displays.

    Computed once server-side so the plate arithmetic lives in exactly one place
    (``services.loads``) instead of being re-derived in JS. The profile is
    memoised on the serializer context, so a nested program — one profile, a
    dozen-plus prescriptions — still costs a single query. Read-only: a missing
    profile yields a null recipe rather than creating a row mid-serialization.
    """

    def _profile_for(self, user):
        cache = self.context.setdefault("_forge_profiles", {})
        if user.pk not in cache:
            cache[user.pk] = UserEquipmentProfile.objects.filter(user=user).first()
        return cache[user.pk]

    def _recipe_payload(self, user, exercise, total):
        if total is None or exercise is None:
            return None
        profile = self._profile_for(user)
        recipe = loads.recipe_for(profile, exercise, total)
        return recipe.as_dict() if recipe else None

    def _load_unit_for(self, user):
        profile = self._profile_for(user)
        return profile.load_unit if profile else None


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
            "is_assessment_anchor", "is_unilateral", "measures_asymmetry",
        ]

    def get_is_unilateral(self, obj):
        # Per-side movements are tested/logged one side at a time. Read from the
        # explicit flag (not the pattern) so archer/typewriter upper-body moves
        # are covered and bilateral squats on the unilateral ladder are not.
        return obj.is_per_side


class UserEquipmentProfileSerializer(serializers.ModelSerializer):
    equipment = serializers.SlugRelatedField(
        slug_field="key", many=True, queryset=Equipment.objects.all(), required=False
    )
    # Plates whose count doesn't divide evenly into a set (6 × 2 kg for a
    # dumbbell pair leaves 2 stranded). Reported so the user can see why a
    # denomination isn't producing steps, rather than silently ignored.
    orphan_plates = serializers.SerializerMethodField()

    class Meta:
        model = UserEquipmentProfile
        fields = [
            "uuid", "equipment", "load_unit",
            "dumbbell_mode", "dumbbell_weights", "dumbbell_plates",
            "dumbbell_handle_weight",
            "band_levels",
            "barbell_min_increment", "barbell_plates", "bar_weight",
            "kettlebell_mode", "kettlebell_weights", "kettlebell_plates",
            "kettlebell_handle_weight",
            "birth_year", "sex", "orphan_plates",
        ]
        read_only_fields = ["uuid", "orphan_plates"]

    def get_orphan_plates(self, obj):
        return {
            "dumbbells": loads.orphan_plates(obj.dumbbell_plates, 4),
            "barbell": loads.orphan_plates(obj.barbell_plates, 2),
            # A kettlebell consumes plates singly, so it can never strand one.
            "kettlebell": [],
        }

    # ── Validation ────────────────────────────────────────────────────────────
    # Without these, `dumbbell_weights: "hello"` or `[-5]` persists verbatim and
    # only blows up later inside the load engine.

    def _validate_plates(self, value, label):
        if not isinstance(value, list):
            raise serializers.ValidationError(f"{label} must be a list of plates.")
        if len(value) > loads.MAX_DENOMINATIONS:
            raise serializers.ValidationError(
                f"At most {loads.MAX_DENOMINATIONS} different {label} denominations."
            )
        cleaned = []
        for entry in value:
            if not isinstance(entry, dict):
                raise serializers.ValidationError(
                    f'Each {label} entry must look like {{"weight": 2.5, "count": 4}}.'
                )
            try:
                weight = float(entry["weight"])
                count = int(entry["count"])
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError(
                    f'Each {label} entry needs a numeric "weight" and a whole "count".'
                )
            if weight <= 0:
                raise serializers.ValidationError(f"{label} weights must be greater than zero.")
            if count <= 0:
                raise serializers.ValidationError(f"{label} counts must be greater than zero.")
            cleaned.append({"weight": weight, "count": count})
        return cleaned

    def _validate_weights(self, value, label):
        if not isinstance(value, list):
            raise serializers.ValidationError(f"{label} must be a list of numbers.")
        cleaned = []
        for entry in value:
            try:
                weight = float(entry)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"{label} must all be numbers.")
            if weight <= 0:
                raise serializers.ValidationError(f"{label} must all be greater than zero.")
            cleaned.append(weight)
        return cleaned

    def validate_dumbbell_plates(self, value):
        return self._validate_plates(value, "dumbbell plate")

    def validate_barbell_plates(self, value):
        return self._validate_plates(value, "barbell plate")

    def validate_kettlebell_plates(self, value):
        return self._validate_plates(value, "kettlebell plate")

    def validate_dumbbell_weights(self, value):
        return self._validate_weights(value, "Dumbbell weights")

    def validate_kettlebell_weights(self, value):
        return self._validate_weights(value, "Kettlebell weights")

    def validate_band_levels(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Band levels must be a list.")
        return [str(v) for v in value]

    def _non_negative(self, value, label):
        if value is None:
            return value
        if value < 0:
            raise serializers.ValidationError(f"{label} cannot be negative.")
        return value

    def validate_bar_weight(self, value):
        return self._non_negative(value, "Bar weight")

    def validate_dumbbell_handle_weight(self, value):
        return self._non_negative(value, "Handle weight")

    def validate_kettlebell_handle_weight(self, value):
        return self._non_negative(value, "Kettlebell shell weight")

    def validate_barbell_min_increment(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("Barbell increment must be greater than zero.")
        return value

    def validate(self, attrs):
        """Switching the load unit clears the inventory instead of converting it.

        Denominations belong to their unit — turning 2.5 kg into 5.51 lb would
        invent plates the user does not own and recipes nobody can build. The
        client warns first; this enforces it regardless of who calls the API.

        Only inventory the request did not itself restate is cleared, so the
        UI's "toggle, confirm, re-enter" flow and a single API call that swaps
        unit *and* supplies a matching new inventory both work. What can never
        survive is a value carried over from the previous unit.
        """
        new_unit = attrs.get("load_unit")
        if self.instance and new_unit and new_unit != self.instance.load_unit:
            for field, blank in (
                ("dumbbell_weights", []), ("dumbbell_plates", []),
                ("dumbbell_handle_weight", 0),
                ("barbell_plates", []), ("bar_weight", 0),
                ("kettlebell_weights", []), ("kettlebell_plates", []),
                ("kettlebell_handle_weight", 0),
            ):
                attrs.setdefault(field, blank)
        return attrs


class AssessmentResultSerializer(serializers.ModelSerializer):
    pattern_key = serializers.CharField(source="pattern.key", read_only=True)
    placed_exercise_name = serializers.CharField(
        source="placed_exercise.name", read_only=True
    )

    class Meta:
        model = AssessmentResult
        fields = [
            "uuid", "pattern_key", "tested_exercise", "reps_or_seconds",
            "left_reps", "right_reps", "asymmetry_pct",
            "placed_exercise", "placed_exercise_name",
        ]
        read_only_fields = [
            "uuid", "placed_exercise", "placed_exercise_name", "asymmetry_pct",
        ]


class AssessmentSessionSerializer(serializers.ModelSerializer):
    results = AssessmentResultSerializer(many=True, read_only=True)

    class Meta:
        model = AssessmentSession
        fields = ["uuid", "is_active", "completed_at", "created_at", "results"]


class PrescribedExerciseSerializer(_LoadRecipeMixin, serializers.ModelSerializer):
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    pattern_key = serializers.CharField(source="pattern.key", read_only=True)
    is_timed = serializers.BooleanField(source="exercise.is_timed", read_only=True)
    video_url = serializers.CharField(source="exercise.video_url", read_only=True)
    cues = serializers.CharField(source="exercise.cues", read_only=True)
    pending_progression_name = serializers.CharField(
        source="pending_progression.name", read_only=True, default=None
    )
    target_load_recipe = serializers.SerializerMethodField()
    load_unit = serializers.SerializerMethodField()

    class Meta:
        model = PrescribedExercise
        fields = [
            "uuid", "pattern_key", "exercise_name", "is_timed", "video_url", "cues",
            "target_sets", "target_reps_min", "target_reps_max", "target_load",
            "target_load_recipe", "load_unit",
            "target_rest_seconds", "order",
            "pending_progression", "pending_progression_name",
        ]

    def get_target_load_recipe(self, obj):
        return self._recipe_payload(obj.day.program.user, obj.exercise, obj.target_load)

    def get_load_unit(self, obj):
        return self._load_unit_for(obj.day.program.user)


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


class SetLogSerializer(_LoadRecipeMixin, serializers.ModelSerializer):
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    video_url = serializers.CharField(source="exercise.video_url", read_only=True)
    is_timed = serializers.BooleanField(source="exercise.is_timed", read_only=True)
    cues = serializers.CharField(source="exercise.cues", read_only=True)
    muscles = MuscleSerializer(source="exercise.muscles", many=True, read_only=True)
    rest_seconds = serializers.SerializerMethodField()
    is_unilateral = serializers.SerializerMethodField()
    expected_load_recipe = serializers.SerializerMethodField()
    load_unit = serializers.SerializerMethodField()

    class Meta:
        model = SetLog
        fields = [
            "uuid", "exercise_name", "video_url", "is_timed", "cues", "muscles",
            "rest_seconds", "is_unilateral",
            "set_index", "expected_reps", "expected_load",
            "expected_load_recipe", "load_unit",
            "actual_reps", "actual_load", "left_reps", "right_reps",
            "is_amrap", "rir",
        ]
        read_only_fields = ["uuid", "exercise_name", "set_index", "expected_reps",
                            "expected_load", "is_amrap"]

    def get_rest_seconds(self, obj):
        if obj.prescribed_exercise_id:
            return obj.prescribed_exercise.target_rest_seconds
        return obj.exercise.rest_seconds

    def get_is_unilateral(self, obj):
        # Per-side moves are logged per side; reads the same flag as
        # ExerciseSerializer.is_unilateral.
        return obj.exercise.is_per_side

    def get_expected_load_recipe(self, obj):
        return self._recipe_payload(obj.session.user, obj.exercise, obj.expected_load)

    def get_load_unit(self, obj):
        return self._load_unit_for(obj.session.user)


class WorkoutSessionSerializer(serializers.ModelSerializer):
    set_logs = SetLogSerializer(many=True, read_only=True)
    day_name = serializers.CharField(source="program_day.name", read_only=True)

    class Meta:
        model = WorkoutSession
        fields = [
            "uuid", "day_name", "scheduled_for", "performed_at", "status", "set_logs",
        ]

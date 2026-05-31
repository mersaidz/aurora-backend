from datetime import timedelta
from rest_framework import serializers
from django.db import transaction
from django.db.models import Q, F, ExpressionWrapper, DateTimeField
from django.db.models.functions import Coalesce

from .models import (
    Workout,
    SportType,
    DataSource,
    UserPhysioProfile,
    HealthMetrics,
    LactateMeasurement,
)



# Module-level constants — single source of truth for dedup logic.


# How close in time two workouts can be before we treat them as the same activity.
# 5 minutes covers clock drift between devices and "save now vs save 3 sec later" UX.
DEDUP_BUFFER = timedelta(minutes=5)

# Source authority for health/sleep/HRV data. Higher = more trusted.
# Oura / Whoop are dedicated wearables tuned for sleep; Garmin/Polar are workout-first.
HEALTH_SOURCE_PRIORITY = {
    'oura':         100,
    'whoop':         90,
    'apple_health':  80,
    'google_fit':    70,
    'garmin':        50,
    'polar':         40,
    'manual':        10,
}

# Same thing for workout but prioritizing most popular one - Garmin

WORKOUT_SOURCE_PRIORITY = {
    'garmin':        100,
    'polar':          95,
    'wahoo':          85,
    'strava':         70,
    'apple_health':   60,
    'google_fit':     55,
    'whoop':          30,
    'oura':           25,
    'manual':         10,
}


def _resolve_health_platform(source, source_label: str = '') -> str:
   # looking for source of truth(proirity) for HM
    if source is not None:
        return source.platform
    if source_label and source_label in HEALTH_SOURCE_PRIORITY:
        return source_label
    return 'manual'


def _resolve_workout_platform(source) -> str:
    # Workout-side platform helper — Workout has no source_label field, so just source
    # maybe add later? (need to test)
    return source.platform if source is not None else 'manual'


# Short serializers — for nesting / list views. All read-only.

class SportTypeShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = SportType
        fields = ['id', 'name', 'category']
        read_only_fields = fields


class DataSourceShortSerializer(serializers.ModelSerializer):
    # Short DataSource without tokens.
    class Meta:
        model = DataSource
        fields = ['id', 'platform', 'is_active', 'connected_at']
        read_only_fields = fields


class UserPhysioProfileShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPhysioProfile
        fields = [
            'id', 'sport_type', 'method',
            'max_hr', 'rest_hr', 'threshold_hr', 'ftp_watts',
            'is_active', 'lactate_testing_enabled',
        ]
        read_only_fields = fields


# Full serializers


class SportTypeSerializer(serializers.ModelSerializer):
    subtypes = SportTypeShortSerializer(many=True, read_only=True)

    class Meta:
        model = SportType
        fields = ['id', 'name', 'parent', 'category', 'external_mapping', 'subtypes']


class DataSourceSerializer(serializers.ModelSerializer):
    # Tokens are intentionally excluded from API responses.

    class Meta:
        model = DataSource
        fields = [
            'id', 'platform', 'is_active', 'connected_at',
            'token_expires', 'provider_user_id', 'scopes',
        ]
        read_only_fields = [
            'id', 'connected_at', 'token_expires', 'provider_user_id', 'scopes',
        ]


class UserPhysioProfileSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    zone_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = UserPhysioProfile
        fields = [
            'id', 'user',
            'sport_type', 'method', 'zone_count_setting',
            'max_hr', 'rest_hr', 'threshold_hr', 'ftp_watts',
            'hr_zones', 'power_zones',
            'is_active', 'lactate_testing_enabled',
            'zone_count', 'created_at',
        ]
        read_only_fields = ['id', 'user', 'created_at', 'zone_count']

    def validate(self, attrs):
        # Reconstruct a model instance so clean() business rules apply consistently
        # across create and partial-update flows.
        merged = {**attrs}
        if self.instance:
            for f in self.Meta.model._meta.concrete_fields:
                if f.name not in merged and f.name != 'id':
                    merged[f.name] = getattr(self.instance, f.name)

        request = self.context.get('request')
        if request and 'user' not in merged:
            merged['user'] = request.user
        merged.pop('zone_count', None)

        instance = UserPhysioProfile(**merged)
        if self.instance:
            instance.pk = self.instance.pk
        instance.clean()
        return attrs

    def create(self, validated_data):
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class LactateMeasurementSerializer(serializers.ModelSerializer):
    class Meta:
        model = LactateMeasurement
        fields = ['id', 'workout', 'measured_at', 'hr_bpm', 'mmol']
        read_only_fields = ['id']

    def validate_workout(self, workout):
        request = self.context.get('request')
        if request and workout.user_id != request.user.id:
            raise serializers.ValidationError("Workout belongs to another user.")
        return workout

    def validate(self, attrs):
        # Lower bound only: post-workout recovery samples are valid (no upper bound)
        # (if simply forgot to put right after training)
        workout = attrs.get('workout') or (self.instance.workout if self.instance else None)
        measured_at = attrs.get('measured_at') or (self.instance.measured_at if self.instance else None)
        if workout and measured_at and measured_at < workout.date:
            raise serializers.ValidationError(
                {"measured_at": "Lactate measurement cannot precede the workout start."}
            )
        return attrs


class HealthMetricsSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    source_details = DataSourceShortSerializer(source='source', read_only=True)

    class Meta:
        model = HealthMetrics
        fields = [
            'id', 'user', 'date',
            'source', 'source_details', 'source_label', 'is_primary',
            'sleep_duration', 'sleep_score', 'recovery_score',
            'hrv', 'rhr',
            'deep_sleep', 'rem_sleep', 'light_sleep', 'awake_time',
            'sleep_consistency',
            'respiratory_rate', 'skin_temp_delta',
            'spo2_avg', 'spo2_min', 'vo2max',
        ]
        read_only_fields = ['id', 'user']

    @transaction.atomic
    def create(self, validated_data):
        """
        Primary-record election on insert.

        Locks all rows for (user, date) so we can decide whether the new record
        should become primary or be archived as historical. If a higher-priority
        source already holds primary, we save the new one as is_primary=False
        without flipping anything. If the new source have higher priority then existing
        one, we demote the existing and promote the new.

        Note on residual race: if NO record exists for (user, date) yet, two
        parallel inserts both pass select_for_update() (nothing to lock) and
        both try to insert as primary. The partial UniqueConstraint
        'unique_primary_health_per_user_date' catches this — one wins with
        IntegrityError on the other. So the same user with same date and 2 records is_primary=True is impossible!
        View-layer can retry the loser with
        is_primary=False if needed.
        """
        user = self.context['request'].user
        validated_data['user'] = user
        target_date = validated_data['date']

        current_platform = _resolve_health_platform(
            source=validated_data.get('source'),
            source_label=validated_data.get('source_label', ''),
        )
        current_priority = HEALTH_SOURCE_PRIORITY.get(current_platform, 0)

        # Lock all (user, date) rows so concurrent inserts serialize on this set.
        # select_related on source avoids N+1 when we resolve the existing platform below.
        existing_metrics = (
            HealthMetrics.objects
            .select_for_update()
            .select_related('source')
            .filter(user=user, date=target_date)
        )
        primary_record = existing_metrics.filter(is_primary=True).first()

        if primary_record is None:
            # First record for this (user, date) — promote ourselves.
            validated_data['is_primary'] = True
            return super().create(validated_data)

        existing_platform = _resolve_health_platform(
            source=primary_record.source,
            source_label=primary_record.source_label,
        )
        existing_priority = HEALTH_SOURCE_PRIORITY.get(existing_platform, 0)

        if current_priority > existing_priority:
            # New source out-ranks — demote existing, promote new.
            primary_record.is_primary = False
            primary_record.save(update_fields=['is_primary'])
            validated_data['is_primary'] = True
        else:
            # Existing source is equal or better — keep new record for history only.
            validated_data['is_primary'] = False

        return super().create(validated_data)


#   WorkoutListSerializer  — lean payload for index/feed views
#   WorkoutDetailSerializer — full read + write


class WorkoutListSerializer(serializers.ModelSerializer):
    sport_type_details = SportTypeShortSerializer(source='sport_type', read_only=True)

    class Meta:
        model = Workout
        fields = [
            'id', 'date', 'end_time', 'duration',
            'sport_type', 'sport_type_details',
            'avg_hr', 'max_hr', 'rpe',
            'distance', 'training_load',
            'is_primary', 'verification_level',
        ]
        read_only_fields = fields


class WorkoutDetailSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    sport_type_details = SportTypeShortSerializer(source='sport_type', read_only=True)
    source_details = DataSourceShortSerializer(source='source', read_only=True)
    physio_profile_details = UserPhysioProfileShortSerializer(
        source='user_physio_profile', read_only=True
    )
    lactate_measurements = LactateMeasurementSerializer(many=True, read_only=True)

    # NOTE: feeling and avg_pace are auto-built by ModelSerializer from the model
    # field definitions (PositiveSmallIntegerField with choices, PositiveIntegerField
    # respectively). No manual override needed — manual overrides would silently
    # drop the auto-generated validators.

    class Meta:
        model = Workout
        fields = [
            'id', 'user',
            'sport_type', 'sport_type_details',
            'source', 'source_details',
            'user_physio_profile', 'physio_profile_details',
            'date', 'end_time', 'duration',
            'is_primary', 'duplicate_of', 'verification_level',
            'avg_hr', 'max_hr', 'calories', 'rpe', 'feeling',
            'distance', 'elevation_gain', 'avg_speed', 'avg_pace',
            'avg_power', 'avg_cadence', 'tss',
            'training_load', 'training_load_calculated_at',
            'hr_zones_data', 'power_zones_data',
            'internal_stress_score', 'variability_index',
            'additional_metrics',
            'lactate_measurements',
            'created_at', 'updated_at',
        ]
        # raw_api_response is intentionally NOT exposed via the API.
        read_only_fields = [
            'id', 'user',
            'training_load', 'training_load_calculated_at',
            'internal_stress_score', 'variability_index',
            'created_at', 'updated_at',
        ]

    # per-FK owner checks 

    def _ensure_owner(self, related_obj, field_label):
        request = self.context.get('request')
        if related_obj is None or request is None:
            return
        if related_obj.user_id != request.user.id:
            raise serializers.ValidationError({field_label: "Belongs to another user."})

    def validate_source(self, value):
        self._ensure_owner(value, 'source')
        return value

    def validate_user_physio_profile(self, value):
        self._ensure_owner(value, 'user_physio_profile')
        return value

    def validate_duplicate_of(self, value):
        self._ensure_owner(value, 'duplicate_of')
        return value

    # cross-field consistency

    def validate(self, attrs):
        def get(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        date = get('date')
        end_time = get('end_time')
        if date and end_time and end_time <= date:
            raise serializers.ValidationError(
                {"end_time": "Workout end_time must be after the start date."}
            )

        is_primary = get('is_primary')
        duplicate_of = get('duplicate_of')
        if is_primary and duplicate_of is not None:
            raise serializers.ValidationError(
                {"duplicate_of": "A primary workout cannot reference another workout as its duplicate."}
            )
        if is_primary is False and duplicate_of is None:
            raise serializers.ValidationError(
                {"duplicate_of": "Non-primary workout must reference its primary counterpart."}
            )

        avg_hr = get('avg_hr')
        max_hr = get('max_hr')
        if avg_hr is not None and max_hr is not None and avg_hr > max_hr:
            raise serializers.ValidationError(
                {"avg_hr": "Average HR cannot exceed max HR."}
            )

        return attrs

    # def create with provider-priority dedup 

    @transaction.atomic
    def create(self, validated_data):
       # Dedup workouts(+-5min):
       #DB empty -> workout is primary
       # already have one? wins with highest priority other going to dup
       # race in same sec on empty DB catching in Postgres with UniqueConstraint(IntegrityError)

        user = self.context['request'].user
        validated_data['user'] = user

        start_time = validated_data['date']
        duration = validated_data['duration']
        new_end = (validated_data.get('end_time') or (start_time + duration))
        validated_data['end_time'] = new_end

        # Buffered window for "close enough to be the same activity".
        window_start = start_time - DEDUP_BUFFER
        window_end = new_end + DEDUP_BUFFER

      
        #The intersection window is checked in a single query at the database level:
        #existing.start < new.end AND existing.end > new.start
        #2. Coalesce replaces the empty end_time in old records with (date + duration).
        candidates = (
            Workout.objects
            .select_for_update()
            .select_related('source')
            .filter(user=user, is_primary=True)
            .annotate(
                effective_end=Coalesce(
                    'end_time',
                    ExpressionWrapper(
                        F('date') + F('duration'),
                        output_field=DateTimeField(),
                    ),
                )
            )
            .filter(date__lt=window_end, effective_end__gt=window_start)
        )

        existing_primary = candidates.first()
        current_platform = _resolve_workout_platform(validated_data.get('source'))
        current_priority = WORKOUT_SOURCE_PRIORITY.get(current_platform, 0)

        if existing_primary is None:
            # No conflict — save as primary.
            validated_data['is_primary'] = True
            return super().create(validated_data)

        existing_platform = _resolve_workout_platform(existing_primary.source)
        existing_priority = WORKOUT_SOURCE_PRIORITY.get(existing_platform, 0)

        if current_priority > existing_priority:
            # New source wins — save new as primary first, then demote and link existing.
            # Order matters: we need the new workout's pk to set duplicate_of on the old one.
            validated_data['is_primary'] = True
            new_workout = super().create(validated_data)
            existing_primary.is_primary = False
            existing_primary.duplicate_of = new_workout
            existing_primary.save(update_fields=['is_primary', 'duplicate_of'])
            return new_workout

        # Existing wins (equal priority defaults to existing — first-write-wins).
        # New record is saved as non-primary and linked to surviving primary.
        validated_data['is_primary'] = False
        validated_data['duplicate_of'] = existing_primary
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        # Never let a client reassign ownership.
        validated_data.pop('user', None)
        return super().update(instance, validated_data)


# Backwards-compatible alias for older imports.
WorkoutSerializer = WorkoutDetailSerializer

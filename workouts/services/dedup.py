"""
Provider-priority deduplication engine for Workout and HealthMetrics.

Single source of truth for dedup logic, called from:
- DRF serializers (HTTP POST /api/workouts/, /api/health-metrics/)
- Sync code (workouts/strava.py and future Garmin/Wahoo/Polar syncs)
- Future: manual coach edits, batch imports

Algorithm summary (workouts):
1. Window-based overlap detection: incoming activity ± 5min buffer
2. SAME sport_type filter (critical — without it, triathlete brick
   training collapses bike + run into one record)
3. Provider priority election: higher-priority source wins, lower-priority
   gets demoted to is_primary=False with duplicate_of FK linking
4. select_for_update(of=('self',)) row-level lock — required because
   source is a nullable FK creating LEFT OUTER JOIN that Postgres refuses
   to lock without of= specification
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import F, ExpressionWrapper, DateTimeField
from django.db.models.functions import Coalesce


# Legacy time-buffer constant — no longer used by workout dedup logic.
# Kept for HealthMetricsSerializer backward compatibility, will be removed
# when health-metrics dedup is also extracted to this service.
DEDUP_BUFFER = timedelta(minutes=5)


# Minimum overlap ratio for two activities to be considered duplicates.
# Computed as: overlap_duration / max(activity_a.duration, activity_b.duration).
# Below this threshold, activities are treated as distinct events that merely
# share boundary time (e.g., cooldown after main ride, warmup → intervals →
# cooldown sequence, or Apple Watch all-day activity overlapping a focused
# Garmin gym session).
#
# 0.5 (50%) chosen empirically: the same activity from two devices shares
# ~80-100% overlap; distinct activities that touch boundaries share <10%;
# the 50% threshold sits comfortably between these bands.
MIN_OVERLAP_RATIO = 0.5

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


# Provider authority for health metrics (sleep, recovery, HRV).
# Different from workout priority because dedicated wearables (Oura, Whoop)
# are tuned for sleep — Garmin/Polar are workout-first.
HEALTH_SOURCE_PRIORITY = {
    'oura':         100,
    'whoop':         90,
    'apple_health':  80,
    'google_fit':    70,
    'garmin':        50,
    'polar':         40,
    'manual':        10,
}


def _resolve_workout_platform(source) -> str:
    return source.platform if source is not None else 'manual'


def _resolve_health_platform(source, source_label: str = '') -> str:
    """
    Resolve health-metric platform. Falls back to source_label string
    (used for HealthMetrics records ingested without a DataSource FK).
    """
    if source is not None:
        return source.platform
    if source_label and source_label in HEALTH_SOURCE_PRIORITY:
        return source_label
    return 'manual'


@transaction.atomic
def create_workout_with_dedup(user, **workout_data):
    """
    Create a Workout using intersection-based deduplication.
    
    Duplicates must overlap by at least MIN_OVERLAP_RATIO of the longer activity.
    If priorities are equal, the shorter duration wins (more focused tracking).
    """
    from workouts.models import Workout

    workout_data['user'] = user

    start_time = workout_data['date']
    duration = workout_data['duration']
    new_end = workout_data.get('end_time') or (start_time + duration)
    workout_data['end_time'] = new_end

    sport_type = workout_data['sport_type']

    # SQL pre-filter: find same-sport-type primary workouts whose time
    # windows overlap with the incoming workout. The precise duplicate
    # decision happens in Python below using overlap-percentage check.
    # sport_type filter prevents triathlete brick false-positives across
    # different sport types (bike → run within minutes).
    candidates = (
        Workout.objects
        .select_for_update(of=('self',))
        .select_related('source')
        .filter(
            user=user,
            sport_type=sport_type,
            is_primary=True,
            date__lt=new_end,
        )
        .annotate(
            effective_end=Coalesce(
                'end_time',
                ExpressionWrapper(
                    F('date') + F('duration'),
                    output_field=DateTimeField(),
                ),
            )
        )
        .filter(effective_end__gt=start_time)
    )

    # Refine: among overlapping candidates, find one with substantial overlap.
    # Overlap below MIN_OVERLAP_RATIO means activities just touch boundaries —
    # they're distinct events, not duplicates.
    existing_primary = None
    for candidate in candidates:
        overlap_start = max(start_time, candidate.date)
        overlap_end = min(new_end, candidate.effective_end)
        overlap_duration = overlap_end - overlap_start

        if overlap_duration <= timedelta(0):
            continue  # Boundary touch, no real overlap

        # Compare overlap to the LONGER activity's duration. Using longer
        # (not shorter) prevents the case where a short flutter detection
        # (e.g., Whoop 30-sec false positive within a 1-hour ride) appears
        # as "100% of itself overlaps" → falsely treated as duplicate.
        longer_duration = max(duration, candidate.duration)
        if longer_duration <= timedelta(0):
            continue  # safety: zero-duration activities can't be evaluated

        if overlap_duration / longer_duration >= MIN_OVERLAP_RATIO:
            existing_primary = candidate
            break

    current_platform = _resolve_workout_platform(workout_data.get('source'))
    current_priority = WORKOUT_SOURCE_PRIORITY.get(current_platform, 0)

    if existing_primary is None:
        # No overlap meeting threshold — incoming becomes primary.
        workout_data['is_primary'] = True
        return Workout.objects.create(**workout_data)

    existing_platform = _resolve_workout_platform(existing_primary.source)
    existing_priority = WORKOUT_SOURCE_PRIORITY.get(existing_platform, 0)

    if current_priority > existing_priority:
        # Incoming source wins (higher-priority provider overrides existing).
        workout_data['is_primary'] = True
        new_workout = Workout.objects.create(**workout_data)
        existing_primary.is_primary = False
        existing_primary.duplicate_of = new_workout
        existing_primary.save(update_fields=['is_primary', 'duplicate_of'])
        return new_workout

    if current_priority < existing_priority:
        # Existing source wins (incoming is lower-priority duplicate).
        workout_data['is_primary'] = False
        workout_data['duplicate_of'] = existing_primary
        return Workout.objects.create(**workout_data)

    # Equal priority (same source most commonly): break tie by duration —
    # shorter activity wins because it's more focused (real workout
    # boundaries), while longer activity is more likely background tracking
    # covering extra time. Order-independent unlike first-write-wins.
    if duration < existing_primary.duration:
        # Incoming is shorter more focused  wins.
        workout_data['is_primary'] = True
        new_workout = Workout.objects.create(**workout_data)
        existing_primary.is_primary = False
        existing_primary.duplicate_of = new_workout
        existing_primary.save(update_fields=['is_primary', 'duplicate_of'])
        return new_workout

    # Existing is shorter or equal, existing keeps primary, incoming duplicate.
    workout_data['is_primary'] = False
    workout_data['duplicate_of'] = existing_primary
    return Workout.objects.create(**workout_data)
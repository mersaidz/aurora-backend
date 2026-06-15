import pytest
from datetime import datetime, timedelta
from django.utils import timezone
from workouts.models import Workout, HealthMetrics, DataSource, SportType
from workouts.serializers import WorkoutDetailSerializer, HealthMetricsSerializer


@pytest.fixture
def run_sport_type():
    return SportType.objects.create(name="Running", category="cardio")


@pytest.fixture
def garmin_source(athlete_user):
    return DataSource.objects.create(user=athlete_user, platform="garmin", is_active=True)


@pytest.fixture
def strava_source(athlete_user):
    return DataSource.objects.create(user=athlete_user, platform="strava", is_active=True)


@pytest.fixture
def oura_source(athlete_user):
    return DataSource.objects.create(user=athlete_user, platform="oura", is_active=True)

@pytest.fixture
def whoop_source(athlete_user):
    return DataSource.objects.create(user=athlete_user, platform="whoop", is_active=True)


@pytest.mark.django_db
class TestWorkoutDeduplication:

    def test_workout_dedup_higher_priority_wins_retroactively(
        self, rf, athlete_user, run_sport_type, strava_source, garmin_source
    ):
        """
        Ensure a higher-priority Garmin workout overrides an existing lower-priority
        Strava workout within the deduplication time buffer.
        """
        base_time = timezone.now()
        
        # 1. Existing lower-priority Strava workout in the database
        strava_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            duration=timedelta(hours=1),
            is_primary=True
        )
        
        # 2. Incoming higher-priority Garmin workout within the 5-minute window
        request = rf.post('/api/workouts/')
        request.user = athlete_user
        
        garmin_data = {
            'sport_type': run_sport_type.id,
            'source': garmin_source.id,
            'date': base_time + timedelta(minutes=3),
            'duration': timedelta(minutes=58),
        }
        
        serializer = WorkoutDetailSerializer(data=garmin_data, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        garmin_workout = serializer.save()
        
        # 3. Verification: Garmin wins primary status, Strava is demoted to a duplicate
        strava_workout.refresh_from_db()
        
        assert garmin_workout.is_primary is True
        assert strava_workout.is_primary is False
        assert strava_workout.duplicate_of_id == garmin_workout.id


    def test_workout_dedup_lower_priority_becomes_duplicate_immediately(
        self, rf, athlete_user, run_sport_type, strava_source, garmin_source
    ):
        """
        Ensure an incoming lower-priority Strava workout is immediately flagged as a duplicate
        and linked to the existing higher-priority Garmin workout.
        """
        base_time = timezone.now()
        
        # 1. Existing higher-priority Garmin workout in the database
        garmin_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=garmin_source,
            date=base_time,
            duration=timedelta(hours=1),
            is_primary=True
        )
        
        # 2. Incoming lower-priority Strava workout within the 5-minute window
        request = rf.post('/api/workouts/')
        request.user = athlete_user
        
        strava_data = {
            'sport_type': run_sport_type.id,
            'source': strava_source.id,
            'date': base_time + timedelta(minutes=2),
            'duration': timedelta(hours=1),
        }
        
        serializer = WorkoutDetailSerializer(data=strava_data, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        strava_workout = serializer.save()
        
        # 3. Verification: Strava should immediately link to the Garmin workout as a duplicate
        assert strava_workout.is_primary is False
        assert strava_workout.duplicate_of_id == garmin_workout.id


    def test_sequential_workouts_low_overlap_are_not_deduped(
        self, rf, athlete_user, run_sport_type, strava_source
    ):
        """
        Verify that sequential same-sport workouts with trivial overlap remain distinct.

        Ensures that if the overlap is below MIN_OVERLAP_RATIO (e.g., a cooldown 
        right after a main ride), the incoming workout is correctly saved as primary 
        instead of being falsely flagged as a duplicate.
        """
        base_time = timezone.now()

        # Main ride: 60 minutes
        main_ride = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            end_time=base_time + timedelta(hours=1),
            duration=timedelta(hours=1),
            is_primary=True,
        )

        # Cooldown: 15 minutes, starts 2 minutes after main ride ended.
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        cooldown_data = {
            'sport_type': run_sport_type.id,
            'source': strava_source.id,
            'date': base_time + timedelta(hours=1, minutes=2),
            'duration': timedelta(minutes=15),
        }

        serializer = WorkoutDetailSerializer(
            data=cooldown_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        cooldown = serializer.save()

        main_ride.refresh_from_db()
        cooldown.refresh_from_db()

        assert main_ride.is_primary is True
        assert main_ride.duplicate_of_id is None
        assert cooldown.is_primary is True
        assert cooldown.duplicate_of_id is None

    def test_equal_priority_shorter_duration_wins_regardless_of_order(
        self, rf, athlete_user, run_sport_type, strava_source
    ):
        """
        Verify duration tie-break for equal-priority, overlapping activities.

        Ensures the shorter, more focused workout wins as primary over a longer,
        background tracking activity. Validates that the outcome remains identical 
        regardless of arrival order (e.g., when the longer activity is saved first).
        """
        base_time = timezone.now()

        # Garbage long activity arrives FIRST (worst case for first-write
        # tie-break). 170 minutes covering a smaller real workout window.
        garbage = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            end_time=base_time + timedelta(minutes=170),
            duration=timedelta(minutes=170),
            is_primary=True,
        )

        # Real focused workout arrives SECOND. Shorter, fully within the
        # garbage activity's window.
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        real_workout_data = {
            'sport_type': run_sport_type.id,
            'source': strava_source.id,
            'date': base_time + timedelta(minutes=30),
            'duration': timedelta(minutes=90),
        }

        serializer = WorkoutDetailSerializer(
            data=real_workout_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        real_workout = serializer.save()

        garbage.refresh_from_db()
        real_workout.refresh_from_db()

        assert real_workout.is_primary is True, (
            "Shorter focused workout must win duration tie-break against "
            "longer background-tracking activity."
        )
        assert real_workout.duplicate_of_id is None
        assert garbage.is_primary is False, (
            "Longer activity must be demoted when shorter overlapping "
            "activity arrives, even if longer was created first."
        )
        assert garbage.duplicate_of_id == real_workout.id


    def test_typical_cross_source_dedup_whoop_envelope(
        self, rf, athlete_user, run_sport_type, strava_source, whoop_source
    ):
        """
        Cross-source overlap with Whoop's HR-based window enveloping a Strava ride.

        Real-world scenario: Garmin Edge records 10:00-12:00 (2h ride). Whoop
        (worn 24/7) detects elevated HR from warm-up (09:45) through cool-down
        (12:30), creating a slightly broader activity record from the same ride.

        Strava (Garmin): 10:00 - 12:00 (2 hours)
        Whoop:           09:45 - 12:30 (2 hours 45 minutes)

        Overlap window: 10:00-12:00 = 2 hours
        Longer duration: 2h45m
        Overlap ratio: 120 min / 165 min ≈ 73% (above MIN_OVERLAP_RATIO of 50%)

        Expected: same workout detected.
        Source priority: Strava (70) > Whoop (30).
        Strava remains primary, Whoop becomes duplicate.
        """
        base_time = timezone.now()

        # 1. Existing Strava ride: 10:00 - 12:00 (already synced as primary)
        strava_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            duration=timedelta(hours=2),
            is_primary=True,
        )

        # 2. Incoming Whoop record: 09:45 - 12:30 (broader HR envelope)
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        whoop_data = {
            'sport_type': run_sport_type.id,
            'source': whoop_source.id,
            'date': base_time - timedelta(minutes=15),       # 09:45
            'duration': timedelta(hours=2, minutes=45),       # → 12:30
        }

        serializer = WorkoutDetailSerializer(
            data=whoop_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        whoop_workout = serializer.save()

        # 3. Verification: Strava wins, Whoop linked as duplicate
        strava_workout.refresh_from_db()
        whoop_workout.refresh_from_db()

        assert strava_workout.is_primary is True, \
            "Strava workout should remain primary (higher source priority)"
        assert whoop_workout.is_primary is False, \
            "Whoop workout should be demoted to duplicate (lower priority)"
        assert whoop_workout.duplicate_of_id == strava_workout.id, (
            f"Whoop should link to Strava as duplicate, "
            f"got duplicate_of_id={whoop_workout.duplicate_of_id}"
        )

    
    def test_exact_identical_times_full_overlap_dedup(
        self, rf, athlete_user, run_sport_type, strava_source, whoop_source
    ):
        """
        Two workouts from different sources with identical start time and
        duration must be deduplicated. The simplest possible overlap.

        Both records: 10:00 - 11:00 (exact same 1-hour window).
        Overlap = 60 minutes, longer = 60 minutes.
        Overlap ratio = 100%.

        Expected:
        - Same workout detected (overlap above MIN_OVERLAP_RATIO of 50%)
        - Source priority: Strava (70) wins over Whoop (30)
        - Strava remains primary, Whoop becomes duplicate
        """
        base_time = timezone.now()

        # 1. Existing Strava workout: 10:00 - 11:00
        strava_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            duration=timedelta(hours=1),
            is_primary=True,
        )

        # 2. Incoming Whoop workout identical to Strava(garmin_edge)
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        whoop_data = {
            'sport_type': run_sport_type.id,
            'source': whoop_source.id,
            'date': base_time,
            'duration': timedelta(hours=1),
        }

        serializer = WorkoutDetailSerializer(
            data=whoop_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        whoop_workout = serializer.save()

        # 3. Verification
        strava_workout.refresh_from_db()
        whoop_workout.refresh_from_db()

        assert strava_workout.is_primary is True, \
            "Strava should remain primary (higher priority)"
        assert whoop_workout.is_primary is False, \
            "Whoop should be demoted to duplicate"
        assert whoop_workout.duplicate_of_id == strava_workout.id, (
            f"Whoop should link to Strava as duplicate, "
            f"got duplicate_of_id={whoop_workout.duplicate_of_id}"
        )


    def test_battery_die_low_overlap_documents_known_limitation(
        self, rf, athlete_user, run_sport_type, strava_source, whoop_source
    ):
        """
        KNOWN LIMITATION: severely truncated recordings escape dedup detection.

        Real-world scenario: Garmin Edge battery dies mid-ride at the 1-hour
        mark. Whoop continues recording HR data for the full session.

        Strava (truncated):  10:00 - 11:00  (1 hour, battery died)
        Whoop (full record): 09:45 - 12:30  (2h 45m)

        Overlap window: 10:00 - 11:00 = 60 minutes
        Longer duration: 165 minutes
        Overlap ratio: 60 / 165 = 36% (below MIN_OVERLAP_RATIO of 50%)

        Current behavior: dedup engine treats these as TWO distinct workouts.
        From the athlete's perspective this is ONE training session, but
        the simple overlap-percentage heuristic doesn't detect this case.

        See TECH_DEBT.md for the proper fix:
        - Start-time proximity rule (same workout if starts within 5 min)
        - or the others, need to think about it

        This test pins down current behavior so any future fix is intentional.
        """
        base_time = timezone.now()

        # 1. Existing Strava workout: 10:00 - 11:00 (battery died at 1h)
        strava_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            duration=timedelta(hours=1),
            is_primary=True,
        )

        # 2. Incoming Whoop workout: 09:45 - 12:30 (full HR window)
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        whoop_data = {
            'sport_type': run_sport_type.id,
            'source': whoop_source.id,
            'date': base_time - timedelta(minutes=15),       # 09:45
            'duration': timedelta(hours=2, minutes=45),       # → 12:30
        }

        serializer = WorkoutDetailSerializer(
            data=whoop_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        whoop_workout = serializer.save()

        # 3. Verification: KNOWN LIMITATION — both treated as separate workouts
        strava_workout.refresh_from_db()
        whoop_workout.refresh_from_db()

        assert strava_workout.is_primary is True, \
            "Strava workout remains primary (untouched)"
        assert whoop_workout.is_primary is True, (
            "KNOWN LIMITATION: Whoop also primary because overlap "
            "(36%) is below MIN_OVERLAP_RATIO (50%). Athlete sees "
            "two workouts in feed for what was actually one session. "
            "See TECH_DEBT for the proper fix."
        )
        assert whoop_workout.duplicate_of is None, \
            "No duplicate link created (overlap below threshold)"
        

    def test_cross_midnight_workout_dedup(
        self, rf, athlete_user, run_sport_type, strava_source, whoop_source
    ):
        """
        Workouts spanning midnight must be correctly deduplicated.

        Real-world scenario: ultra-endurance ride or night training session
        starting before midnight and ending after.

        Strava: starts 23:30 (June 14), 2h duration → ends 01:30 (June 15)
        Whoop:  starts 23:30 (June 14), 2h duration → ends 01:30 (June 15)

        Both workouts span calendar day boundary. Overlap is 100%.

        Validates: dedup logic uses raw datetime comparisons (not calendar
        dates), so day boundaries don't interfere with overlap detection.
        """
        # Force a specific time: today at 23:30 UTC (deterministic)
        now = timezone.now()
        base_time = now.replace(hour=23, minute=30, second=0, microsecond=0)

        # 1. Existing Strava workout: 23:30 → 01:30 next day
        strava_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            duration=timedelta(hours=2),
            is_primary=True,
        )

        # 2. Incoming Whoop workout: same span 
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        whoop_data = {
            'sport_type': run_sport_type.id,
            'source': whoop_source.id,
            'date': base_time,
            'duration': timedelta(hours=2),
        }

        serializer = WorkoutDetailSerializer(
            data=whoop_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        whoop_workout = serializer.save()

        # 3. Verification: cross-midnight doesn't break dedup
        strava_workout.refresh_from_db()
        whoop_workout.refresh_from_db()

        # Sanity check: workouts actually span midnight
        strava_end = strava_workout.date + strava_workout.duration
        assert strava_end.date() > strava_workout.date.date(), \
            "Sanity check: workout should span midnight"

        # Dedup worked correctly
        assert strava_workout.is_primary is True, \
            "Strava remains primary across midnight boundary"
        assert whoop_workout.is_primary is False, \
            "Whoop deduplicated (lower priority)"
        assert whoop_workout.duplicate_of_id == strava_workout.id, \
            "Whoop linked to Strava as duplicate"
        

    def test_null_end_time_uses_duration_fallback_for_dedup(
        self, rf, athlete_user, run_sport_type, strava_source, whoop_source
    ):
        """
        Defensive code path: dedup engine must handle Workout records where
        end_time is NULL by falling back to date + duration.

        This guards the Coalesce('end_time', F('date') + F('duration'))
        expression in create_workout_with_dedup.

        Real-world cases (rare but possible):
        - Manual workout entry (POST /api/workouts/ without end_time)
        - Legacy/migration imports from systems without end_time field
        - Live tracking where the session is still in progress

        Sync flows (Strava, Whoop) always set end_time, so this is primarily
        a regression test against future refactors that might remove the
        Coalesce fallback.
        """
        base_time = timezone.now()

        # 1. Existing Strava workout — explicitly without end_time
        strava_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=run_sport_type,
            source=strava_source,
            date=base_time,
            duration=timedelta(hours=1),
            end_time=None,                # explicit NULL
            is_primary=True,
        )

        # Sanity check: end_time really is null in DB
        strava_workout.refresh_from_db()
        assert strava_workout.end_time is None, \
            "Sanity check: end_time should be None in DB"

        # 2. Incoming Whoop workout: same window
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        whoop_data = {
            'sport_type': run_sport_type.id,
            'source': whoop_source.id,
            'date': base_time,
            'duration': timedelta(hours=1),
        }

        serializer = WorkoutDetailSerializer(
            data=whoop_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        whoop_workout = serializer.save()

        # 3. Verification: dedup worked despite NULL end_time on existing
        strava_workout.refresh_from_db()
        whoop_workout.refresh_from_db()

        assert strava_workout.is_primary is True, \
            "Strava remains primary"
        assert whoop_workout.is_primary is False, (
            "Whoop should be deduped — Coalesce fallback to date+duration "
            "must work when end_time is NULL"
        )
        assert whoop_workout.duplicate_of_id == strava_workout.id, \
            "Whoop linked to Strava"


@pytest.mark.django_db
class TestHealthMetricsDeduplication:

    def test_health_metrics_priority_election(self, rf, athlete_user, garmin_source, oura_source):
        """
        Ensure a higher-priority Oura health metric record overrides an existing 
        lower-priority Garmin record for the same calendar date.
        """
        today = datetime.now().date()
        
        # 1. Existing lower-priority Garmin metric record in the database
        garmin_metrics = HealthMetrics.objects.create(
            user=athlete_user,
            date=today,
            source=garmin_source,
            is_primary=True,
            sleep_score=65
        )
        
        # 2. Incoming higher-priority Oura metric record for the same date
        request = rf.post('/api/health-metrics/')
        request.user = athlete_user
        
        oura_data = {
            'date': today,
            'source': oura_source.id,
            'sleep_score': 88,
        }
        
        serializer = HealthMetricsSerializer(data=oura_data, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        oura_metrics = serializer.save()
        
        # 3. Verification: Oura takes primary status, Garmin gets archived
        garmin_metrics.refresh_from_db()
        
        assert oura_metrics.is_primary is True
        assert garmin_metrics.is_primary is False


    def test_brick_training_different_sports_are_not_deduped(
        self, rf, athlete_user, garmin_source
    ):
        """
        Triathlete brick training regression test.

        A bike ride followed immediately by a run (sequential, no time
        overlap) must NOT collapse into a single workout — they are two
        distinct training activities.

        Bug history: prior to sport_type filter in dedup service, the
        time-window overlap check would match any user's primary workout
        regardless of sport, causing bricks to lose their run portion.
        Sport_type filter now scopes candidates to same-sport only.
        """
        base_time = timezone.now()

        cycling = SportType.objects.create(name="Cycling", category="cardio")
        running = SportType.objects.create(name="Running", category="cardio")

        # 1. Garmin records a 60-min bike ride
        bike_workout = Workout.objects.create(
            user=athlete_user,
            sport_type=cycling,
            source=garmin_source,
            date=base_time,
            duration=timedelta(hours=1),
            is_primary=True,
        )

        # 2. Same Garmin records a 30-min run starting 2 minutes after the
        # bike ride ended (sequential brick training pattern)
        request = rf.post('/api/workouts/')
        request.user = athlete_user

        run_data = {
            'sport_type': running.id,
            'source': garmin_source.id,
            'date': base_time + timedelta(hours=1, minutes=2),
            'duration': timedelta(minutes=30),
        }

        serializer = WorkoutDetailSerializer(
            data=run_data,
            context={'request': request},
        )
        assert serializer.is_valid(), serializer.errors
        run_workout = serializer.save()

        # Verification: BOTH workouts should be primary, neither marked as
        # duplicate of the other — they are distinct activities.
        bike_workout.refresh_from_db()
        run_workout.refresh_from_db()

        assert bike_workout.is_primary is True
        assert bike_workout.duplicate_of_id is None
        assert run_workout.is_primary is True
        assert run_workout.duplicate_of_id is None
        assert run_workout.sport_type_id == running.id
        assert bike_workout.sport_type_id == cycling.id

    
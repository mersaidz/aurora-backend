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

        A bike ride followed immediately by a run (within the 5-minute
        DEDUP_BUFFER window) must NOT collapse into a single workout —
        they are two distinct training activities.

        Bug history: prior to sport_type filter in dedup service, the
        window-overlap check would match any user's primary workout
        within the buffer regardless of sport, causing bricks to lose
        their run portion.
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
        # bike ride ended (well within DEDUP_BUFFER of 5 minutes)
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
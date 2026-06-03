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
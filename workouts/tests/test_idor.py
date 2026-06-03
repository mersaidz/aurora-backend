"""
IDOR / cross-user authorization tests.

The Aurora data model is multi-tenant by user: every domain object (workouts,
profiles, health metrics, sources) belongs to exactly one user, and no user
should ever be able to read, modify, or delete another user's data, even by
guessing IDs or by forging FK fields in the request body.

These tests verify that protection at two layers:
  1. HTTP layer — views' get_queryset() filters by request.user
  2. Serializer layer — per-FK _ensure_owner() rejects foreign FKs in body

We assert 404 (not 403) on cross-user access on purpose — exposing 403 would leak the
existence of a resource the requester is not allowed to see.
"""
from __future__ import annotations
import pytest
from django.urls import reverse

from workouts.models import (
    Workout,
    UserPhysioProfile,
    DataSource,
    SportType,
)
from workouts.serializers import WorkoutDetailSerializer

PHYSIO_LIST_URL = reverse('workouts:physio-profile-list')

def physio_detail_url(pk: int) -> str:
    return reverse('workouts:physio-profile-detail',kwargs= {'pk': pk})

@pytest.mark.django_db
class TestUserPhysioProfileIDOR:
    # HTTP level IDOR protection on UserPhysioProfile CRUD
    def test_list_returns_only_own_profiles(self, user_factory, auth_client_factory):
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')

        profile_a = UserPhysioProfile.objects.create(user=user_a, method='MANUAL', max_hr=190)
        profile_b = UserPhysioProfile.objects.create(user=user_b, method='MANUAL', max_hr=190)

        client_b = auth_client_factory(user_b)
        response = client_b.get(PHYSIO_LIST_URL)

        assert response.status_code == 200
        ids_returned = {item['id'] for item in response.json()}
        assert profile_b.id in ids_returned
        assert profile_a.id not in ids_returned #UserB must not see UserA physio profile in response


    def test_retrieve_foreign_profile_returns_404(self, user_factory, auth_client_factory):
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')

        profile_a = UserPhysioProfile.objects.create(user=user_a, method='MANUAL', max_hr=190)

        client_b = auth_client_factory(user_b)
        response = client_b.get(physio_detail_url(profile_a.id))

        assert response.status_code == 404
    

    def test_patch_foreign_profile_returns_404(self, user_factory, auth_client_factory):
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')

        profile_a = UserPhysioProfile.objects.create(user=user_a, method='MANUAL', max_hr=190)

        client_b = auth_client_factory(user_b)
        response = client_b.patch(physio_detail_url(profile_a.id), {'max_hr': 240}, format='json')

        assert response.status_code == 404
        profile_a.refresh_from_db()
        assert profile_a.max_hr == 190, "Foreign user must NOT mutate the row."

    
    def test_delete_foreign_profile_returns_404(self, user_factory, auth_client_factory):
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')

        profile_a = UserPhysioProfile.objects.create(user=user_a, method='MANUAL', max_hr=190)
        client_b = auth_client_factory(user_b)
        response = client_b.delete(physio_detail_url(profile_a.id))

        assert response.status_code == 404
        assert UserPhysioProfile.objects.filter(pk=profile_a.id).exists()

    def test_create_ignores_user_field_in_request_body(self, user_factory, auth_client_factory):
        # Even if a malicious client sends 'user': <other_id> in the body,
        # the serializer assigns user from request.user (context) — body is ignored.
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')

        client_a = auth_client_factory(user_a)
        response = client_a.post(
            PHYSIO_LIST_URL,
            {
                'user': user_b.id,           # <-- forged ownership attempt
                'method': 'manual',
                'max_hr': 180,
            },
            format='json',
        )
        assert response.status_code == 201, f"Expected 201, got {response.status_code}. Errors: {response.json()}"
        created = UserPhysioProfile.objects.get(pk=response.json()['id'])
        assert created.user_id == user_a.id, (
            "Serializer must derive 'user' from request.user, not from request body."
        )


@pytest.mark.django_db
class TestWorkoutSerializerOwnership:
    """
    Serializer-level IDOR protection on Workout FKs.

    I dont have public HTTP endpoint on Workout yet (sync flow comes via OAuth, not user
    POST), but its serializer is already defensively coded: every relational
    field (source, user_physio_profile, duplicate_of) is checked against
    request.user via _ensure_owner(). These tests pin that behavior.
    """
    def _make_request(self, rf, user):
        request = rf.post('/api/workouts/')
        request.user = user
        return request
    
    def test_rejects_foreign_sources(self, rf, user_factory):
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')

        sport = SportType.objects.create(name='Run', category='cardio')
        foreign_source = DataSource.objects.create(user=user_b, platform='Garmin', is_active = True)
        from datetime import timedelta
        from django.utils import timezone
        serializer = WorkoutDetailSerializer(
            data ={
                'sport_type': sport.id,
                'source': foreign_source.id,       # <-- belongs to user_b
                'date': timezone.now(),
                'duration': timedelta(hours=1),
            },
            context = {'request': self._make_request(rf, user_a)},
        )
        assert not serializer.is_valid()
        assert 'source' in serializer.errors

    def test_rejects_foreign_physio_profile(self, rf, user_factory):
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')

        sport = SportType.objects.create(name='Run', category='cardio')
        foreign_profile = UserPhysioProfile.objects.create(user=user_b, method='MANUAL', max_hr=190)
        from datetime import timedelta
        from django.utils import timezone
        serializer = WorkoutDetailSerializer(
            data={
                'sport_type': sport.id,
                'user_physio_profile': foreign_profile.id,  # <-- belongs to user_b
                'date': timezone.now(),
                'duration': timedelta(hours=1),
            },
            context = {'request': self._make_request(rf, user_a)},
        )
        assert not serializer.is_valid()
        assert 'user_physio_profile' in serializer.errors

    def test_rejects_foreign_duplicate_of(self, rf, user_factory):
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')
        sport = SportType.objects.create(name='Run', category='cardio')

        from datetime import timedelta
        from django.utils import timezone

        foreign_workout = Workout.objects.create(
            user=user_b,
            sport_type=sport,
            date=timezone.now(),
            duration=timedelta(hours=1),
            is_primary=True,
        )

        serializer = WorkoutDetailSerializer(
            data= {
                'sport_type': sport.id,
                'date': timezone.now(),
                'duration': timedelta(hours=1),
                'is_primary': False,
                'duplicate_of': foreign_workout.id,  # <-- belongs to user_b
            },
            context={'request': self._make_request(rf,user_a)},
        )
        assert not serializer.is_valid()
        assert 'duplicate_of' in serializer.errors
        

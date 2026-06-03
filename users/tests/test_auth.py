from __future__ import annotations
import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken

from config.rules import universal_user_authentication_rule as user_can_authenticate

TOKEN_URL = reverse('token_obtain_pair')
REFRESH_URL = reverse('token_refresh')
BLACKLIST_URL = reverse('token_blacklist')
PROFILE_URL = reverse('users:profile')


@pytest.mark.django_db
def test_login_with_correct_credentials_returns_token_pair(api_client, user_factory):
    user_factory(email='login@aurora.test', password='StrongPass123!')

    response = api_client.post(
        TOKEN_URL,
        {'email': 'login@aurora.test', 'password': 'StrongPass123!'},
        format='json',
    )

    assert response.status_code == 200
    body = response.json()
    assert 'access' in body
    assert 'refresh' in body
    
    token = AccessToken(body['access'])
    assert token['user_id'] is not None


@pytest.mark.django_db
def test_login_with_wrong_password_returns_401(api_client, user_factory):
    user_factory(email='login@aurora.test', password='StrongPass123!')

    response = api_client.post(
        TOKEN_URL,
        {'email': 'login@aurora.test', 'password': 'WrongPassword!'},
        format='json',
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_refresh_returns_new_pair_and_blacklists_old_refresh(api_client, athlete_user):
    #Ensure token rotation works and old refresh token is blacklisted immediately.
    old_refresh = str(RefreshToken.for_user(athlete_user))

    first = api_client.post(REFRESH_URL, {'refresh': old_refresh}, format='json')
    assert first.status_code == 200
    assert 'access' in first.json()
    assert 'refresh' in first.json()
    
    new_refresh = first.json()['refresh']
    assert new_refresh != old_refresh

    second = api_client.post(REFRESH_URL, {'refresh': old_refresh}, format='json')
    assert second.status_code == 401


@pytest.mark.django_db
def test_blacklist_endpoint_invalidates_refresh(api_client, athlete_user):
    #Explicit logout via blacklist endpoint.
    refresh = str(RefreshToken.for_user(athlete_user))

    logout = api_client.post(BLACKLIST_URL, {'refresh': refresh}, format='json')
    assert logout.status_code == 200

    after = api_client.post(REFRESH_URL, {'refresh': refresh}, format='json')
    assert after.status_code == 401


@pytest.mark.django_db
def test_inactive_user_token_is_rejected(api_client, athlete_user):
    # Token must stop working immediately if is_active flips to False.
    access = str(RefreshToken.for_user(athlete_user).access_token)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    assert api_client.get(PROFILE_URL).status_code == 200

    athlete_user.is_active = False
    athlete_user.save(update_fields=['is_active'])

    assert api_client.get(PROFILE_URL).status_code == 401


@pytest.mark.django_db
def test_soft_deleted_user_token_is_rejected(api_client, athlete_user):
    #Tokens must be rejected for soft-deleted users, even if is_active is True.
    access = str(RefreshToken.for_user(athlete_user).access_token)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    assert api_client.get(PROFILE_URL).status_code == 200

    athlete_user.deleted_at = timezone.now()
    athlete_user.save(update_fields=['deleted_at'])

    assert api_client.get(PROFILE_URL).status_code == 401


@pytest.mark.django_db
def test_user_can_authenticate_rule_truth_table(user_factory):
    # Direct unit test for the authentication rules truth table.
    active = user_factory(email='active@aurora.test')
    assert user_can_authenticate(active) is True

    inactive = user_factory(email='inactive@aurora.test')
    inactive.is_active = False
    inactive.save(update_fields=['is_active'])
    assert user_can_authenticate(inactive) is False

    soft_deleted = user_factory(email='deleted@aurora.test')
    soft_deleted.deleted_at = timezone.now()
    soft_deleted.save(update_fields=['deleted_at'])
    assert user_can_authenticate(soft_deleted) is False

    assert user_can_authenticate(None) is False
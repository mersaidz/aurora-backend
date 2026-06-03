from __future__ import annotations
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from users.models import User
from users.services.registration import register_user

@pytest.fixture
def api_client() -> APIClient:
    # Anon api client without auth headers.
    # Use for registration, login or public endpoint testing.
    return APIClient()

@pytest.fixture
def user_factory(db):
    # Factory of creating muplitple users on the go
    # Useful for IDOR(cross-user security tests) multiple separate users in 1 test.
    counter = {'n':0}

    def _make(**overrides) -> User:
        counter['n'] += 1
        defaults = {
            'email': f'user{counter["n"]}@aurora.test',
            'password': 'SomePassxd123!',
        }
        defaults.update(overrides)
        return register_user(**defaults)
    return _make

@pytest.fixture
def athlete_user(user_factory) -> User:
    # Short fixture when a test needs just one default user
    return user_factory()

@pytest.fixture
def auth_client(api_client: APIClient, athlete_user: User) -> APIClient:
    # API client pre-authenticated with a JWT token for athlete_user.
    # Use this for any endpoints that require authentication.
    refresh = RefreshToken.for_user(athlete_user)
    api_client.credentials(HTTP_AUTHORIZATION = f'Bearer {refresh.access_token}')
    return api_client
from __future__ import annotations 
import pytest
from django.urls import reverse
from users.models import User, UserProfile

REGISTER_URL = reverse('register')

@pytest.mark.django_db
def test_register_happy_path_creates_user_and_profile(api_client):
    #happy! valid email, strong password and etc.
    payload = {
        'email': 'fresh@aurora.test',
        'password': 'ValidPass123!',
    }
    response = api_client.post(REGISTER_URL, payload, format='json')
    
    assert response.status_code == 201
    body = response.json()
    assert body['email'] == 'fresh@aurora.test'
    assert 'password' not in body
    assert body['role'] == 'ATHLETE'
    
    user = User.objects.get(email='fresh@aurora.test')
    assert UserProfile.objects.filter(user=user).exists()

@pytest.mark.django_db
def test_register_duplicate_email_same_case_rejected(api_client, user_factory):
    #re-registering with same email should fail
    user_factory(email='taken@aurora.test')
    response = api_client.post(
        REGISTER_URL,
        {'email': 'taken@aurora.test', 'password': 'ValidPass123!'},
        format = 'json',
    )
    assert response.status_code == 400
    assert 'email' in response.json()

@pytest.mark.django_db
def test_register_duplicate_email_case_variant_rejected(api_client, user_factory):
    # Testing our normalize_email override protection (lower case vs upper case)
    user_factory(email='taken@aurora.test')
    response = api_client.post(
        REGISTER_URL,
        {'email': 'Taken@Aurora.TEST', 'password': 'ValidPass123!'},
        format = 'json',
    )

    assert response.status_code == 400 , (
        f"Case-variant duplicate slipped through. "
        f"Got {response.status_code}: {response.json()}"
    )
    #Make sure no second user was created
    assert User.objects.filter(email__iexact='taken@aurora.test').count() == 1


@pytest.mark.django_db
def test_register_weak_password_rejected(api_client):
    # Django's validators must reject weak passwords
    response = api_client.post(
        REGISTER_URL,
        {'email': 'weak@aurora.test', 'password': '12345678'},
        format = 'json',
    )

    assert response.status_code == 400
    assert 'password' in response.json()

    assert not User.objects.filter(email='weak@aurora.test').exists()


@pytest.mark.django_db
def test_register_invalid_email_format_rejected(api_client):
    #EmailField validator check
    response = api_client.post(
        REGISTER_URL,
        {'email': 'notemail', 'password': 'ValidPass123!'},
        format = 'json',
    )
    assert response.status_code == 400
    assert 'email' in response.json()


import pytest
from datetime import datetime, timedelta
from django.urls import reverse
from rest_framework import status

@pytest.mark.django_db
class TestUserProfileAPI:

    def test_profile_partial_update_patch_works_correctly(self, api_client, athlete_user):
        # Testing custom __init__ of serializer
        # PATCH request must update only weight without requirements of other fields
        api_client.force_authenticate(user=athlete_user)
        url = reverse('users:profile')  
        
        payload = {
            "profile": {
                "weight": "78.50"
            }
        }
        
        response = api_client.patch(url, payload, format='json')       
        assert response.status_code == status.HTTP_200_OK
        assert response.data['profile']['weight'] == "78.50"
        
        athlete_user.profile.refresh_from_db()
        assert athlete_user.profile.weight == 78.50


    def test_profile_update_fails_for_under_14(self, api_client, athlete_user):
        # Testing age validation (COPPA/GDPR) no users under 14 y.o
       
        api_client.force_authenticate(user=athlete_user)
        url = reverse('users:profile')
        
        today = datetime.now().date()
        too_young_date = today - timedelta(days=13*365)  # 13 y.o
        
        payload = {
            "profile": {
                "birth_date": too_young_date.isoformat()
            }
        }
        
        response = api_client.patch(url, payload, format='json')
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'profile' in response.data
        assert 'birth_date' in response.data['profile']
        assert "Aurora is available for users aged 14 and above." in response.data['profile']['birth_date']
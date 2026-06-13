from django.urls import path

from .views import (
    UserPhysioProfileListCreateView,
    UserPhysioProfileDetailView,
    strava_connect,
    strava_callback,
    strava_sync_view,
    whoop_connect,
    whoop_callback,
)

app_name = 'workouts'

urlpatterns = [
    path('physio-profiles/', UserPhysioProfileListCreateView.as_view(), name='physio-profile-list'),
    path('physio-profiles/<int:pk>/', UserPhysioProfileDetailView.as_view(), name='physio-profile-detail'),

    path('strava/connect/', strava_connect, name='strava-connect'),
    path('strava/callback/', strava_callback, name='strava-callback'),
    path('strava/sync/', strava_sync_view, name='strava-sync'),

    path('whoop/connect/', whoop_connect, name='whoop-connect'),
    path('whoop/callback/', whoop_callback, name='whoop-callback'),
]
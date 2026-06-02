from django.urls import path

from .views import (
    UserPhysioProfileListCreateView,
    UserPhysioProfileDetailView,
)

app_name = 'workouts'

urlpatterns = [
    path('physio-profiles/',UserPhysioProfileListCreateView.as_view(),name='physio-profile-list'),
    path('physio-profiles/<int:pk>/',UserPhysioProfileDetailView.as_view(),name='physio-profile-detail'),
]
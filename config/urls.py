from django.contrib import admin
from django.urls import path, include
from users.views import RegisterView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenBlacklistView
)


urlpatterns = [
    path('admin/', admin.site.urls),

    #JWT/Auth
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/token/blacklist/', TokenBlacklistView.as_view(), name='token_blacklist'),



    path('api/users/', include('users.urls', namespace='users')),
    path('api/workouts/', include('workouts.urls', namespace='workouts')),
]

from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import UserPhysioProfile
from .serializers import UserPhysioProfileSerializer

class UserPhysioProfileListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/workouts/physio-profiles/   → list current user's profiles
    POST /api/workouts/physio-profiles/   → create new profile for current user

    Authorization model:
    - get_queryset filters by request.user, so a user only sees their own profiles
    - the serializer's create() assigns user=request.user from context, so the
      'user' field can never be forged from the request body (IDOR-safe)
    """
    serializer_class = UserPhysioProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # This just ensures the filter is in place if anyone changes permission_classes later. 
        return (
            UserPhysioProfile.objects
            .filter(user=self.request.user)
            .select_related('sport_type', 'user')
        )
    
class UserPhysioProfileDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/workouts/physio-profiles/<id>/   → retrieve one profile
    PATCH  /api/workouts/physio-profiles/<id>/   → partial update
    DELETE /api/workouts/physio-profiles/<id>/   → delete

    Same authorization model as the list view — get_queryset narrows to the
    requesting user, so trying to access someone else's profile by ID returns
    404 (not 403, intentionally — 403 would leak existence of the resource).
    """
    serializer_class = UserPhysioProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            UserPhysioProfile.objects
            .filter(user=self.request.user)
            .select_related('sport_type', 'user')
        )
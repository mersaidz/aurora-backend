from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import UserPhysioProfile
from .serializers import UserPhysioProfileSerializer

from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.response import Response

from workouts import strava as strava_oauth

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
    
@api_view(['GET'])
@authentication_classes([SessionAuthentication, JWTAuthentication])
@permission_classes([IsAuthenticated])
def strava_connect(request):
    """
    Initiates the Strava OAuth flow.

    Accepts both session auth (browser flow — user logged in via Django admin)
    and JWT (API flow — programmatic clients). Browser session is the natural
    fit for OAuth because the redirect back from Strava must work via cookies.

    Generates a CSRF state token, stores it in the user's session, then
    redirects the user to Strava's authorization page.
    """
    state = strava_oauth.generate_state_token()
    request.session['strava_oauth_state'] = state

    authorization_url = strava_oauth.build_authorization_url(state=state)
    return redirect(authorization_url)


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
def strava_callback(request):
    """
    Strava OAuth callback — receives the authorization code after user consent.

    No permission_classes — the user is returning from Strava and proves
    identity via session cookies established at /strava/connect/. The state
    parameter then verifies this was the same flow we initiated.

    Query params from Strava:
    - code: the one-time authorization code
    - state: must match the value we stored in session before redirect
    - scope: the actual scopes user granted
    - error: present if user denied authorization
    """
    error = request.GET.get('error')
    if error:
        return HttpResponse(
            f"Strava authorization denied: {error}",
            status=400,
        )

    state_received = request.GET.get('state', '')
    state_expected = request.session.pop('strava_oauth_state', None)
    if not state_expected or state_received != state_expected:
        return HttpResponse(
            "OAuth state mismatch — possible CSRF attempt. "
            "Please restart the connect flow.",
            status=400,
        )

    code = request.GET.get('code')
    scope = request.GET.get('scope', '')

    return HttpResponse(
        f"Day 1 success — Strava callback received.\n"
        f"Code (first 8 chars): {code[:8] if code else 'NONE'}...\n"
        f"Scope: {scope}\n"
        f"State verified: yes\n"
        f"Next step (Day 2): exchange code for access/refresh tokens.",
        content_type='text/plain',
    )

    # TODO Day 2: exchange code for tokens, encrypt, save to DataSource
    return HttpResponse(
        f"Day 1 success — Strava callback received.\n"
        f"Code (first 8 chars): {code[:8] if code else 'NONE'}...\n"
        f"Scope: {scope}\n"
        f"State verified: yes\n"
        f"Next step (Day 2): exchange code for access/refresh tokens.",
        content_type='text/plain',
    )
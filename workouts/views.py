from rest_framework import generics

from .models import UserPhysioProfile
from .serializers import UserPhysioProfileSerializer

from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.response import Response

from workouts import strava as strava_oauth
import requests
from workouts.models import DataSource
from workouts.whoop import (
    generate_state_token,
    build_authorization_url,
    exchange_code_for_token,
    sync_whoop_data,
)
from datetime import timedelta
from django.utils import timezone

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


@login_required
@transaction.atomic
def strava_callback(request):
    """
    Handle Strava OAuth callback.

    Verifies the state token for CSRF protection, exchanges the auth code 
    for tokens, and saves them to the user's DataSource.
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

    if not code:
        return HttpResponse(
            "OAuth code missing from callback — Strava sent an incomplete response.",
            status=400,
        )

    # Exchange the one-time code for permanent (refreshable) tokens.
    try:
        token_data = strava_oauth.exchange_code_for_tokens(code)
    except requests.RequestException as exc:
        return HttpResponse(
            f"Strava token exchange failed: {exc}",
            status=502,
        )

    # Persist tokens to DataSource. Fernet encryption is automatic via
    # the EncryptedTextField — neither view nor caller needs to encrypt
    # manually. update_or_create handles both first-connect and re-connect
    # via the (user, platform) unique constraint.
    expires_at = strava_oauth._parse_expires_at(token_data['expires_at'])
    athlete_id = str(token_data.get('athlete', {}).get('id', ''))
    granted_scopes = scope.split(',') if scope else []

    data_source, created = DataSource.objects.update_or_create(
        user=request.user,
        platform='strava',
        defaults={
            'access_token': token_data['access_token'],
            'refresh_token': token_data['refresh_token'],
            'token_expires': expires_at,
            'provider_user_id': athlete_id,
            'scopes': granted_scopes,
            'is_active': True,
        },
    )

    action = "Created" if created else "Updated"
    return HttpResponse(
        f"Day 2 success — Strava DataSource {action.lower()}.\n"
        f"DataSource ID: {data_source.id}\n"
        f"Strava athlete ID: {athlete_id}\n"
        f"Token expires at: {expires_at.isoformat()}\n"
        f"Scopes granted: {granted_scopes}\n"
        f"is_token_valid: {data_source.is_token_valid}\n\n"
        f"Tokens are now encrypted in DB via Fernet. To verify:\n"
        f"  1. Open Django admin → DataSource → your row\n"
        f"     The access_token / refresh_token fields show CIPHERTEXT.\n"
        f"  2. Open Django shell: python manage.py shell\n"
        f"     ds = DataSource.objects.get(id={data_source.id})\n"
        f"     print(ds.access_token[:20] + '...')  # plaintext via from_db_value\n\n"
        f"Day 3: implement activity sync — fetch your workouts via Strava API.",
        content_type='text/plain',
    )


@login_required(login_url='/admin/login/')
def strava_sync_view(request):
    """
    Trigger Strava activity sync for the current user.

    Day 3 endpoint — manual sync via browser. Will be replaced later
    by a periodic Celery task that runs every N minutes per active
    DataSource (or webhook subscription where supported).

    Returns plain-text sync stats for dev visibility. Production would
    redirect to a frontend dashboard showing sync results visually.
    """
    try:
        data_source = DataSource.objects.get(
            user=request.user,
            platform='strava',
            is_active=True,
        )
    except DataSource.DoesNotExist:
        return HttpResponse(
            "No active Strava connection. Visit "
            "/api/workouts/strava/connect/ first.",
            status=404,
        )

    try:
        stats = strava_oauth.sync_strava_workouts(data_source)
    except requests.RequestException as exc:
        return HttpResponse(
            f"Strava API call failed: {exc}",
            status=502,
        )

    return HttpResponse(
        f"Strava sync complete for {request.user.email}.\n\n"
        f"Total activities fetched: {stats['total']}\n"
        f"New workouts created:     {stats['new']}\n"
        f"Already synced (skipped): {stats['existing']}\n"
        f"Errors during sync:       {stats['errors']}\n\n"
        f"Next step: open Django shell and inspect your data:\n"
        f"  python manage.py shell\n"
        f"  from workouts.models import Workout\n"
        f"  Workout.objects.filter(source__platform='strava').order_by('-date')[:5]\n",
        content_type='text/plain',
    )
  


#Whoopie
@login_required
def whoop_connect(request):
    state = generate_state_token()
    request.session['whoop_oauth_state'] = state
    auth_url = build_authorization_url(state)
    return redirect(auth_url)

@login_required
@transaction.atomic
def whoop_callback(request):
    error = request.GET.get('error')
    if error:
        return HttpResponse(
            f"Whoop authorization denied: {error}",
            status=400,
        )

    state_received = request.GET.get('state', '')
    state_expected = request.session.pop('whoop_oauth_state', None)
    if not state_expected or state_received != state_expected:
        return HttpResponse(
            "OAuth state mismatch — possible CSRF attempt. "
            "Please restart the connect flow.",
            status=400,
        )

    code = request.GET.get('code')
    if not code:
        return HttpResponse(
            "OAuth code missing from callback — Whoop sent an incomplete response.",
            status=400,
        )

    # Exchange the one-time code for permanent (refreshable) tokens.
    try:
        token_data = exchange_code_for_token(code)
    except requests.RequestException as exc:
        return HttpResponse(
            f"Whoop token exchange failed: {exc}",
            status=502,
        )

    

    # Whoop returns 'expires_in' (seconds from now), unlike Strava's 'expires_at'.
    expires_at = timezone.now() + timedelta(seconds=token_data['expires_in'])
    granted_scopes = (
        token_data.get('scope', '').split(' ')
        if token_data.get('scope')
        else []
    )

    data_source, created = DataSource.objects.update_or_create(
        user=request.user,
        platform='whoop',
        defaults={
            'access_token': token_data['access_token'],
            'refresh_token': token_data.get('refresh_token', ''),
            'token_expires': expires_at,
            'provider_user_id': '',
            'scopes': granted_scopes,
            'is_active': True,
        },
    )

    action = "Created" if created else "Updated"
    return HttpResponse(
        f"Whoop OAuth success — DataSource {action.lower()}.\n"
        f"DataSource ID: {data_source.id}\n"
        f"Token expires at: {expires_at.isoformat()}\n"
        f"Scopes granted: {granted_scopes}\n"
        f"is_token_valid: {data_source.is_token_valid}\n\n"
        f"Tokens encrypted in DB via Fernet (EncryptedTextField).\n"
        f"Next: implement sync — workouts + recovery + sleep endpoints.",
        content_type='text/plain',
    )

@login_required
def whoop_sync_view(request):
    """
    Trigger Whoop data sync for the authenticated user.

    Default range: last 7 days. Future: accept ?days=N or
    ?start=&end= query params for custom date ranges.
    """
    try:
        data_source = DataSource.objects.get(
            user=request.user,
            platform='whoop',
        )
    except DataSource.DoesNotExist:
        return HttpResponse(
            "No Whoop integration. Visit /api/workouts/whoop/connect/ first.",
            status=400,
        )

    if not data_source.is_active:
        return HttpResponse(
            "Whoop integration is inactive. "
            "Re-authorize via /api/workouts/whoop/connect/",
            status=400,
        )

    # Default sync window: last 7 days
    end = timezone.now()
    start = end - timedelta(days=7)

    try:
        stats = sync_whoop_data(data_source, start, end)
    except RuntimeError as exc:
        # Token expired and Whoop offers no refresh in our dev app —
        # surface re-auth requirement to user.
        return HttpResponse(
            f"Whoop sync requires re-authorization: {exc}",
            status=401,
        )

    w = stats['workouts']
    r = stats['recovery']
    s = stats['sleep']
    h = stats['health_metrics']

    return HttpResponse(
        f"Whoop sync complete ({start.date()} → {end.date()}):\n"
        f"\n"
        f"  Workouts: {w['new']} new, {w['existing']} existing, {w['errors']} errors\n"
        f"  Recovery: {r['fetched']} fetched, {r['errors']} errors\n"
        f"  Sleep:    {s['fetched']} fetched, {s['naps_skipped']} naps skipped, {s['errors']} errors\n"
        f"  HealthMetrics: {h['created']} created, {h['updated']} updated, {h['errors']} errors\n"
        f"\n"
        f"View synced data via Django admin → Workouts / HealthMetrics.\n",
        content_type='text/plain',
    )
"""
Strava OAuth + API integration.
Token storage: encrypted into DataSource.access_token / refresh_token
via the EncryptedTextField (Fernet) I built earlier.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlencode
import requests 
from datetime import datetime, timezone as dt_timezone 
from decimal import Decimal
from datetime import timedelta
import hashlib
import json as json_lib


# Strava OAuth endpoints (constants — won't change for a long time)
STRAVA_AUTH_URL = 'https://www.strava.com/oauth/authorize'
STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'
STRAVA_API_BASE = 'https://www.strava.com/api/v3'

# Scope = what permissions we ask the user for
# - read: basic profile info
# - activity:read_all: read all activities, including private ones
# This is the minimum for Aurora's sync flow.
STRAVA_SCOPE = 'read,activity:read_all'


def _get_client_id() -> str:
    """Read Strava client ID from env. Fails loud if missing."""
    client_id = os.getenv('STRAVA_CLIENT_ID')
    if not client_id:
        raise RuntimeError(
            "STRAVA_CLIENT_ID is not set. Configure it in .env "
            "or via Strava developer settings."
        )
    return client_id


def _get_redirect_uri() -> str:
    """Read OAuth redirect URI from env. Fails loud if missing."""
    redirect_uri = os.getenv('STRAVA_REDIRECT_URI')
    if not redirect_uri:
        raise RuntimeError(
            "STRAVA_REDIRECT_URI is not set. Configure it in .env."
        )
    return redirect_uri


def generate_state_token() -> str:
    #Generate a cryptographically secure state token for OAuth CSRF protection.
    return secrets.token_urlsafe(32)


def build_authorization_url(state: str) -> str:
    """
    Build the Strava authorization URL the user is redirected to.

    Strava will show its standard "Authorize Aurora to access your data?"
    consent screen. On accept, Strava redirects to our STRAVA_REDIRECT_URI
    """
    params = {
        'client_id': _get_client_id(),
        'redirect_uri': _get_redirect_uri(),
        'response_type': 'code',
        'approval_prompt': 'force',  # always show consent, even for returning users
        'scope': STRAVA_SCOPE,
        'state': state,
    }
    return f"{STRAVA_AUTH_URL}?{urlencode(params)}"



def _get_client_secret() -> str:
    client_secret = os.getenv('STRAVA_CLIENT_SECRET')
    if not client_secret:
        raise RuntimeError(
            "STRAVA_CLIENT_SECRET is not set. Configure it in .env."
        )
    return client_secret


def _parse_expires_at(unix_timestamp: int) -> datetime:
    """
    Convert Strava's Unix epoch timestamp to a timezone-aware UTC datetime.

    Strava returns expires_at as integer Unix timestamp (seconds since epoch).
    Django expects timezone-aware datetimes when USE_TZ=True (which we have on).
    """
    return datetime.fromtimestamp(unix_timestamp, tz=dt_timezone.utc)


def exchange_code_for_tokens(code: str) -> dict:
    """
    Exchange a one-time Strava auth code for tokens.

    Args:
    code (str): Single-use code from Strava callback redirect.

    Returns:
    dict: Parsed JSON with tokens, expires_at, and athlete profile info.
    """
    response = requests.post(
        STRAVA_TOKEN_URL,
        data={
            'client_id': _get_client_id(),
            'client_secret': _get_client_secret(),
            'code': code,
            'grant_type': 'authorization_code',
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def refresh_strava_token(data_source) -> dict:
    """
    Refresh an expired access token using the stored refresh token.

    Strava rotates refresh tokens — every refresh returns a NEW refresh token
    that replaces the old one. So we must persist both. (This rotation is a
    security feature against stolen-token replay.)

    Updates the DataSource in-place via save(), which triggers Fernet
    re-encryption of the token fields automatically.

    Returns the parsed response dict (mostly for logging/audit purposes).
    """
    response = requests.post(
        STRAVA_TOKEN_URL,
        data={
            'client_id': _get_client_id(),
            'client_secret': _get_client_secret(),
            'grant_type': 'refresh_token',
            'refresh_token': data_source.refresh_token,
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    data_source.access_token = payload['access_token']
    data_source.refresh_token = payload['refresh_token']
    data_source.token_expires = _parse_expires_at(payload['expires_at'])
    data_source.save(update_fields=['access_token', 'refresh_token', 'token_expires'])

    return payload

# ---------------------------------------------------------------------------
# Strava activity type → Aurora SportType mapping.
#
# Categorization principle:
# - 'cardio' = sport trained as sustained endurance (HR/lactate/power zones,
#   periodization cycles): cycling, running, swimming, rowing, nordic skiing.
# - 'specific' = sport-specific or recreational with variable intensity and
#   technique focus: skating, alpine ski, MTB, trail running, team sports.
# - 'strength' / 'flexibility' = self-explanatory.
#
# Fallback for unknown Strava types: store as-is with category='cardio'
# (most Strava activities not in this list are cardio-adjacent).
# ---------------------------------------------------------------------------
STRAVA_SPORT_MAP = {
    # Cycling (endurance discipline; MTB is specific due to technique)
    'Ride': ('Cycling', 'cardio'),
    'VirtualRide': ('Cycling', 'cardio'),
    'EBikeRide': ('Cycling', 'cardio'),
    'MountainBikeRide': ('Mountain Biking', 'specific'),
    'GravelRide': ('Cycling', 'cardio'),
    # Running & Walking
    'Run': ('Running', 'cardio'),
    'VirtualRun': ('Running', 'cardio'),
    'TrailRun': ('Trail Running', 'specific'),
    'Walk': ('Walking', 'cardio'),
    'Hike': ('Hiking', 'cardio'),
    # Water (endurance disciplines + technique/recreation)
    'Swim': ('Swimming', 'cardio'),
    'Rowing': ('Rowing', 'cardio'),
    'Kayaking': ('Kayaking', 'cardio'),
    'Surfing': ('Surfing', 'specific'),
    'StandUpPaddling': ('SUP', 'specific'),
    # Winter sports
    'IceSkate': ('Ice Skating', 'specific'),
    'InlineSkate': ('Inline Skating', 'specific'),
    'AlpineSki': ('Alpine Skiing', 'specific'),
    'Snowboard': ('Snowboarding', 'specific'),
    'NordicSki': ('Nordic Skiing', 'cardio'),
    'BackcountrySki': ('Backcountry Skiing', 'cardio'),
    # Gym & Fitness
    'Workout': ('General Workout', 'strength'),
    'WeightTraining': ('Weight Training', 'strength'),
    'Elliptical': ('Elliptical', 'cardio'),
    'StairStepper': ('Stair Stepper', 'cardio'),
    # Mind & Body
    'Yoga': ('Yoga', 'flexibility'),
    'Pilates': ('Pilates', 'flexibility'),
    # Sport-specific / team / technique-driven
    'Crossfit': ('CrossFit', 'specific'),
    'MartialArts': ('Martial Arts', 'specific'),
    'RockClimbing': ('Rock Climbing', 'specific'),
    'Soccer': ('Soccer', 'specific'),
    'Tennis': ('Tennis', 'specific'),
}


def get_or_create_sport_type(strava_type: str):
    """
    Return an Aurora SportType for a Strava activity type.

    Falls back to (strava_type as-is, 'cardio') for unknown types — most
    Strava activities not in our map are cardio-adjacent, and admin can
    rename / recategorize later via Django admin.
    """
    from workouts.models import SportType

    name, category = STRAVA_SPORT_MAP.get(strava_type, (strava_type, 'cardio'))
    sport_type, _ = SportType.objects.get_or_create(
        name=name,
        defaults={'category': category},
    )
    return sport_type


def fetch_strava_activities(data_source, per_page: int = 30, page: int = 1) -> list:
    """
    Fetch one page of athlete's activities from the Strava API.

    Auto-refreshes the access token if it has expired (or is within the
    60-second skew buffer of expiry — see DataSource.is_token_valid).

    Strava paginates with per_page (max 200) and page (1-indexed). The
    caller handles multi-page sync logic.

    Returns: list of activity dicts (empty list when no more activities).
    Raises: requests.HTTPError on Strava API errors (caller decides
        whether to retry, surface, or skip).
    """
    # Proactive token refresh — better than handling 401s in every API call
    # site. is_token_valid already includes a 60-second skew buffer to avoid
    # the race where a token is "valid" at check time but expires before
    # Strava processes our request.
    if not data_source.is_token_valid:
        refresh_strava_token(data_source)

    response = requests.get(
        f"{STRAVA_API_BASE}/athlete/activities",
        headers={'Authorization': f'Bearer {data_source.access_token}'},
        params={'per_page': per_page, 'page': page},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def normalize_strava_activity(activity: dict, user, sport_type, source) -> dict:
    """
    Convert a Strava activity dict (summary or detail) into Workout kwargs.
    """
    start = datetime.fromisoformat(activity['start_date'].replace('Z', '+00:00'))
    elapsed_seconds = activity.get('elapsed_time', 0)
    moving_seconds = activity.get('moving_time', 0)

    end_time = start + timedelta(seconds=elapsed_seconds)
    duration = timedelta(seconds=moving_seconds or elapsed_seconds)

    workout_data = {
        'user': user,
        'sport_type': sport_type,
        'source': source,
        'external_id': str(activity['id']),
        'date': start,
        'end_time': end_time,
        'duration': duration,
        'verification_level': 'raw',
    }

    # Heart rate metrics
    if activity.get('average_heartrate') is not None:
        workout_data['avg_hr'] = int(activity['average_heartrate'])
    if activity.get('max_heartrate') is not None:
        workout_data['max_hr'] = int(activity['max_heartrate'])

    # Distance: meters -> kilometers
    if activity.get('distance'):
        workout_data['distance'] = (
            Decimal(str(activity['distance'])) / Decimal('1000')
        ).quantize(Decimal('0.01'))

    if activity.get('total_elevation_gain') is not None:
        workout_data['elevation_gain'] = int(activity['total_elevation_gain'])

    # Speed: m/s -> km/h
    if activity.get('average_speed'):
        workout_data['avg_speed'] = (
            Decimal(str(activity['average_speed'])) * Decimal('3.6')
        ).quantize(Decimal('0.01'))

    # Power & Cadence
    if activity.get('average_watts') is not None:
        workout_data['avg_power'] = int(activity['average_watts'])
    if activity.get('average_cadence') is not None:
        workout_data['avg_cadence'] = int(activity['average_cadence'])

    # Detail payload fields
    if activity.get('calories') is not None:
        workout_data['calories'] = int(activity['calories'])

    # RPE: clamp to 1-10 range
    if activity.get('perceived_exertion') is not None:
        rpe_raw = int(round(float(activity['perceived_exertion'])))
        workout_data['rpe'] = max(1, min(10, rpe_raw))

    # Optional metadata for future analysis (e.g., brand-aware tie-breaks)
    additional = {}
    if activity.get('device_name'):
        additional['strava_device_name'] = activity['device_name']
    if activity.get('kilojoules') is not None:
        additional['strava_kilojoules'] = activity['kilojoules']
    if activity.get('average_temp') is not None:
        additional['strava_avg_temp_celsius'] = activity['average_temp']
    if activity.get('weighted_average_watts') is not None:
        additional['strava_weighted_avg_watts'] = activity['weighted_average_watts']

    if additional:
        workout_data['additional_metrics'] = additional

    return workout_data



def sync_strava_workouts(data_source, max_activities: int = 30) -> dict:
    """
    Sync recent Strava activities into Aurora.

    Fetches workouts, ensures idempotency via external_id, normalizes data, 
    and runs them through the dedup engine to resolve cross-provider conflicts.

    Returns:
    dict: Sync statistics containing 'total', 'new', 'existing', and 'errors' counts.
    """
    from workouts.models import Workout, WorkoutRawPayload
    from workouts.services.dedup import create_workout_with_dedup

    activities = fetch_strava_activities(data_source, per_page=max_activities)
    stats = {'total': len(activities), 'new': 0, 'existing': 0, 'errors': 0}

    for activity in activities:
        try:
            external_id = str(activity['id'])

            # Workout-level idempotency: if this exact Strava activity is
            # already in Aurora (by external_id + source), skip the pipeline.
            # cheap pre-check before we spend bytes on raw payload.
            if Workout.objects.filter(
                source=data_source,
                external_id=external_id,
            ).exists():
                stats['existing'] += 1
                continue

            # Raw-payload-level idempotency via sha256 of canonical JSON.
            # sort_keys=True ensures deterministic hash regardless of dict insertion order
            payload_canonical = json_lib.dumps(activity, sort_keys=True)
            sha = hashlib.sha256(payload_canonical.encode('utf-8')).hexdigest()

            raw, _ = WorkoutRawPayload.objects.get_or_create(
                user=data_source.user,
                payload_sha256=sha,
                defaults={
                    'provider': 'strava',
                    'payload': activity,
                    'provider_workout_id': external_id,
                    'workout_started_at': datetime.fromisoformat(
                        activity['start_date'].replace('Z', '+00:00')
                    ),
                    'schema_version': 'strava-v3-2026.06',
                },
            )

           # Try fetching full activity details; fallback to summary if API call fails
            try:
                detail = fetch_strava_activity_detail(data_source, external_id)
            except requests.RequestException:
                detail = activity

            # Normalize activity with preferred detail payload or summary fallback
            sport_type = get_or_create_sport_type(detail.get('type', 'Workout'))
            workout_kwargs = normalize_strava_activity(
                detail,
                user=data_source.user,
                sport_type=sport_type,
                source=data_source,
            )

            # Create Workout through the shared dedup engine. user is passed
            # positionally because that's the service signature; the rest of
            # workout_kwargs splats into the function.
            user = workout_kwargs.pop('user')
            workout = create_workout_with_dedup(user, **workout_kwargs)

            # Link raw payload back to created workout — enables
            # re-normalization in future (if we change parser logic, we
            # can re-process the existing raw payloads without re-fetching
            # from Strava API).
            raw.workout = workout
            raw.save(update_fields=['workout'])

            stats['new'] += 1
        except Exception:
            # Per-activity isolation: one bad activity doesn't kill the
            # whole sync. Log traceback to console for dev visibility.
            # Production: replace with logger.exception() + AuditLog
            # write (action='sync_failed') once audit helper module ships.
            import traceback
            traceback.print_exc()
            stats['errors'] += 1

    return stats

def fetch_strava_activity_detail(data_source, activity_id) -> dict:
    """
    Fetch full detail for a single Strava activity via GET /activities/{id}.
    
    Used to retrieve fields omitted in summary listings, such as device_name,
    calories, hr zones, and average watts and etc.
    """
    if not data_source.is_token_valid:
        refresh_strava_token(data_source)

    response = requests.get(
        f"{STRAVA_API_BASE}/activities/{activity_id}",
        headers={'Authorization': f'Bearer {data_source.access_token}'},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()



"""
Strava OAuth + API integration.
Token storage: encrypted into DataSource.access_token / refresh_token
via the EncryptedTextField (Fernet) I built earlier.
"""

from __future__ import annotations

import secrets
import logging
from urllib.parse import urlencode
import requests
from datetime import datetime, timezone as dt_timezone, timedelta
from decimal import Decimal
import hashlib
import json as json_lib

from django.conf import settings
from django.db import IntegrityError

logger = logging.getLogger(__name__)


# Strava OAuth endpoints (constants — won't change for a long time)
STRAVA_AUTH_URL = 'https://www.strava.com/oauth/authorize'
STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'
STRAVA_API_BASE = 'https://www.strava.com/api/v3'

STRAVA_SCOPE = 'read,activity:read_all'


def _get_client_id() -> str:
    if not settings.STRAVA_CLIENT_ID:
        raise RuntimeError(
            "STRAVA_CLIENT_ID is not set. Configure it in .env "
            "or via Strava developer settings."
        )
    return settings.STRAVA_CLIENT_ID


def _get_redirect_uri() -> str:
    if not settings.STRAVA_REDIRECT_URI:
        raise RuntimeError(
            "STRAVA_REDIRECT_URI is not set. Configure it in .env."
        )
    return settings.STRAVA_REDIRECT_URI


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
    if not settings.STRAVA_CLIENT_SECRET:
        raise RuntimeError(
            "STRAVA_CLIENT_SECRET is not set. Configure it in .env."
        )
    return settings.STRAVA_CLIENT_SECRET


def _parse_expires_at(unix_timestamp: int) -> datetime:
    """
    Convert Strava's Unix epoch timestamp to a timezone-aware UTC datetime.

    Strava returns expires_at as integer Unix timestamp (seconds since epoch).
    Django expects timezone-aware datetimes when USE_TZ=True (which we have on).
    """
    return datetime.fromtimestamp(unix_timestamp, tz=dt_timezone.utc)


def exchange_code_for_tokens(code: str) -> dict:
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


# Strava activity type → Aurora SportType mapping.

# Fallback for unknown Strava types: store as-is with category='cardio'
# (most Strava activities not in this list are cardio-adjacent).

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
    # Return an Aurora SportType for a Strava activity type.
    from workouts.models import SportType

    name, category = STRAVA_SPORT_MAP.get(strava_type, (strava_type, 'cardio'))
    sport_type, _ = SportType.objects.get_or_create(
        name=name,
        defaults={'category': category},
    )
    return sport_type


def fetch_strava_activities(data_source, per_page: int = 30, page: int = 1) -> list:
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
        except IntegrityError:
            # Race condition: concurrent sync inserted this activity between
            # our exists() check and create. DB correctly rejected duplicate.
            # See TECH_DEBT for proper fix (DB-level sync lock).
            stats['existing'] += 1
        except Exception:
            # Per-activity isolation: one bad activity doesn't kill the
            # whole sync. logger.exception captures the full traceback for
            # production observability (vs print to stdout).
            logger.exception("Strava workout sync failed for activity")
            stats['errors'] += 1

    return stats

def fetch_strava_activity_detail(data_source, activity_id) -> dict:
    if not data_source.is_token_valid:
        refresh_strava_token(data_source)

    response = requests.get(
        f"{STRAVA_API_BASE}/activities/{activity_id}",
        headers={'Authorization': f'Bearer {data_source.access_token}'},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()



"""
Whoop OAuth + sync service.
(Oath2.0)
Docs: https://developer.whoop.com/docs/developing/oauth
"""
from __future__ import annotations
import secrets
import logging
from urllib.parse import urlencode

from django.conf import settings

import requests
from datetime import datetime, timedelta
from decimal import Decimal

import hashlib
import json as json_lib

logger = logging.getLogger(__name__)

#Constants
WHOOP_AUTH_URL = 'https://api.prod.whoop.com/oauth/oauth2/auth'
WHOOP_TOKEN_URL = 'https://api.prod.whoop.com/oauth/oauth2/token'
WHOOP_API_BASE = 'https://api.prod.whoop.com/developer/'

# OAuth scopes — space-separated per OAuth spec.
# Must match scopes registered in the Whoop developer dashboard.
WHOOP_SCOPES = (
    'read:recovery '
    'read:cycles '
    'read:sleep '
    'read:workout '
    'read:profile '
    'read:body_measurement'
)

def generate_state_token() -> str:
    return secrets.token_urlsafe(32) #generate token for csrf protection during OAuth flow.

def build_authorization_url(state: str) -> str:
    
    params = {
        'client_id': settings.WHOOP_CLIENT_ID,
        'redirect_uri': settings.WHOOP_REDIRECT_URI,
        'response_type': 'code',
        'scope': WHOOP_SCOPES,
        'state': state,
    }
    return f'{WHOOP_AUTH_URL}?{urlencode(params)}'

def exchange_code_for_token(code: str) -> dict:
    """
    Exchange authorization code for access + refresh tokens.
    """
    payload = {
        'grant_type': 'authorization_code',
        'code': code,
        'client_id': settings.WHOOP_CLIENT_ID,
        'client_secret': settings.WHOOP_CLIENT_SECRET,
        'redirect_uri': settings.WHOOP_REDIRECT_URI,
    }


    response = requests.post(
        WHOOP_TOKEN_URL,
        data=payload,
        timeout=10,
    )

    if not response.ok:
        logger.warning(
            "Whoop token exchange failed: status=%s body=%s",
            response.status_code,
            response.text,
        )

    response.raise_for_status()
    return response.json()


# Whoop sport_name → Aurora SportType mapping.
# Whoop returns sport as a lowercase string in the workout response

# Categories follow the same principle as Strava:

# cardio = sustained endurance
# specific = sport-specific or interval/skill work
# strength / flexibility = self-explanatory

# Fallback for unknown Whoop sports: store as-is with category='cardio'.
WHOOP_SPORT_MAP = {
    # Cardio
    'cycling': ('Cycling', 'cardio'),
    'running': ('Running', 'cardio'),
    'swimming': ('Swimming', 'cardio'),
    'walking': ('Walking', 'cardio'),
    'rowing': ('Rowing', 'cardio'),
    'elliptical': ('Elliptical', 'cardio'),
    # Specific / interval
    'hiit': ('HIIT', 'specific'),
    'crossfit': ('CrossFit', 'specific'),
    'ice_skating': ('Ice Skating', 'specific'),
    # Strength
    'weightlifting': ('Weight Training', 'strength'),
    'functional_fitness': ('Functional Fitness', 'strength'),
    # Flexibility
    'yoga': ('Yoga', 'flexibility'),
    'pilates': ('Pilates', 'flexibility'),
    # Recovery activities (OAuth v2)
    'meditation': ('Meditation', 'flexibility'),
    'mobility': ('Mobility', 'flexibility'),
}


def get_or_create_whoop_sport_type(whoop_sport_name: str):
    # Return an Aurora SportType for a Whoop sport_name string.

    # Falls back to (whoop_sport_name as-is title-cased, 'cardio') for unknown.
    # Admin can rename / recategorize later via Django admin.

    from workouts.models import SportType

    name, category = WHOOP_SPORT_MAP.get(
        whoop_sport_name,
        (whoop_sport_name.replace('_', ' ').title(), 'cardio'),
    )
    sport_type, _ = SportType.objects.get_or_create(
        name=name,
        defaults={'category': category},
    )
    return sport_type

def refresh_whoop_token(data_source) -> None:
    # Whoop doesn't issue refresh tokens to my dev app, or i'm wrong
    # (offline scope not available in my dashboard scope list).

   #  Raise to force manual re-auth instead of silent failure.
   #  Caller catches this and surfaces a helpful error message to the user.

    raise RuntimeError(
        "Whoop access token expired. "
        "Re-authorize by visiting /api/workouts/whoop/connect/"
    )

def fetch_whoop_workouts(
    data_source,
    start,
    end,
    next_token: str = None,
    limit: int = 25,
) -> dict:
    if not data_source.is_token_valid:
        refresh_whoop_token(data_source)  # raises RuntimeError

    # Whoop expects ISO datetime with 'Z' suffix (UTC), not '+00:00'.
    # Both formats are valid ISO 8601 but Whoop's parser is strict.
    params = {
        'start': start.isoformat().replace('+00:00', 'Z'),
        'end': end.isoformat().replace('+00:00', 'Z'),
        'limit': limit,
    }
    if next_token:
        # Whoop quirk: response field is 'next_token' (snake_case),
        # but query param is 'nextToken' (camelCase). Per docs.
        params['nextToken'] = next_token

    response = requests.get(
        f'{WHOOP_API_BASE}v2/activity/workout',
        headers={'Authorization': f'Bearer {data_source.access_token}'},
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
    
def fetch_whoop_recovery(
    data_source,
    start,
    end,
    next_token: str = None,
    limit: int = 25,
) -> dict:
    if not data_source.is_token_valid:
        refresh_whoop_token(data_source)

    params = {
        'start': start.isoformat().replace('+00:00', 'Z'),
        'end': end.isoformat().replace('+00:00', 'Z'),
        'limit': limit,
    }
    if next_token:
        params['nextToken'] = next_token

    response = requests.get(
        f'{WHOOP_API_BASE}v2/recovery',
        headers={'Authorization': f'Bearer {data_source.access_token}'},
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def fetch_whoop_sleep(
    data_source,
    start,
    end,
    next_token: str = None,
    limit: int = 25,
) -> dict:
    if not data_source.is_token_valid:
        refresh_whoop_token(data_source)

    params = {
        'start': start.isoformat().replace('+00:00', 'Z'),
        'end': end.isoformat().replace('+00:00', 'Z'),
        'limit': limit,
    }
    if next_token:
        params['nextToken'] = next_token

    response = requests.get(
        f'{WHOOP_API_BASE}v2/activity/sleep',
        headers={'Authorization': f'Bearer {data_source.access_token}'},
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def normalize_whoop_workout(record, user, sport_type, source) -> dict:
    start = datetime.fromisoformat(record['start'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(record['end'].replace('Z', '+00:00'))
    duration = end - start

    workout_data = {
        'user': user,
        'sport_type': sport_type,
        'source': source,
        'external_id': record['id'],  # UUID string from v2
        'date': start,
        'end_time': end,
        'duration': duration,
        'verification_level': 'raw',
    }

    # Score is only present when score_state='SCORED'.
    # PENDING_SCORE / UNSCORABLE states don't have measurement data.
    score = record.get('score')
    if score and record.get('score_state') == 'SCORED':
        if score.get('average_heart_rate') is not None:
            workout_data['avg_hr'] = int(score['average_heart_rate'])
        if score.get('max_heart_rate') is not None:
            workout_data['max_hr'] = int(score['max_heart_rate'])

        # Distance: meters -> kilometers, 2 decimal precision
        if score.get('distance_meter') is not None:
            workout_data['distance'] = (
                Decimal(str(score['distance_meter'])) / Decimal('1000')
            ).quantize(Decimal('0.01'))

        # Elevation gain in meters
        if score.get('altitude_gain_meter') is not None:
            workout_data['elevation_gain'] = int(score['altitude_gain_meter'])

        # Energy: kilojoules → calories. 1 kJ ≈ 0.239 kcal.
        if score.get('kilojoule') is not None:
            workout_data['calories'] = int(score['kilojoule'] * 0.239)

        # HR zone durations (already in milliseconds — ready for JSON storage)
        if score.get('zone_durations'):
            workout_data['hr_zones_data'] = score['zone_durations']

        # Whoop-specific metrics preserved for future analysis
        # (brand-aware dedup tie-breaks, etc.)
        additional = {}
        if score.get('strain') is not None:
            additional['whoop_strain'] = score['strain']
        if score.get('kilojoule') is not None:
            additional['whoop_kilojoule'] = score['kilojoule']
        if score.get('percent_recorded') is not None:
            additional['whoop_percent_recorded'] = score['percent_recorded']
        if record.get('sport_id') is not None:
            additional['whoop_sport_id'] = record['sport_id']
        if record.get('timezone_offset'):
            additional['whoop_timezone_offset'] = record['timezone_offset']

        if additional:
            workout_data['additional_metrics'] = additional

    return workout_data


def normalize_whoop_recovery(record, user, source) -> dict:
    metrics = {
        'user': user,
        'source': source,
        'date': datetime.fromisoformat(
            record['created_at'].replace('Z', '+00:00')
        ).date(),
    }

    score = record.get('score')
    if score and record.get('score_state') == 'SCORED':
        if score.get('recovery_score') is not None:
            metrics['recovery_score'] = int(score['recovery_score'])

        # Resting heart rate (bpm)
        if score.get('resting_heart_rate') is not None:
            metrics['rhr'] = int(score['resting_heart_rate'])

        # HRV in milliseconds (RMSSD method — gold standard for HRV-based recovery)
        if score.get('hrv_rmssd_milli') is not None:
            metrics['hrv'] = Decimal(
                str(score['hrv_rmssd_milli'])
            ).quantize(Decimal('0.01'))

        # SpO2 % (oxygen saturation during sleep)
        if score.get('spo2_percentage') is not None:
            metrics['spo2_avg'] = Decimal(
                str(score['spo2_percentage'])
            ).quantize(Decimal('0.01'))

        # Skin temperature delta (Celsius, relative to baseline)
        if score.get('skin_temp_celsius') is not None:
            metrics['skin_temp_delta'] = Decimal(
                str(score['skin_temp_celsius'])
            ).quantize(Decimal('0.01'))

    return metrics

def normalize_whoop_sleep(record, user, source) -> dict | None:
    """
    Naps are SKIPPED (return None) — they shouldn't overwrite the night sleep
    record. Whoop tracks naps separately for strain calculations but they're
    not "the sleep" for HealthMetrics purposes.

    BUT We keep'em for next features(sleep debt.)

    Sleep stages come in milliseconds; we convert to timedelta for storage.
    """
    # Skip naps — they're shorter, don't represent overnight recovery
    if record.get('nap'):
        return None

    end = datetime.fromisoformat(record['end'].replace('Z', '+00:00'))

    metrics = {
        'user': user,
        'source': source,
        'date': end.date(),  # morning of waking
    }

    score = record.get('score')
    if score and record.get('score_state') == 'SCORED':
        stages = score.get('stage_summary', {})

        # All stage durations in milliseconds → convert to timedelta
        light = stages.get('total_light_sleep_time_milli') or 0
        rem = stages.get('total_rem_sleep_time_milli') or 0
        deep = stages.get('total_slow_wave_sleep_time_milli') or 0
        awake = stages.get('total_awake_time_milli') or 0

        # Total sleep = light + REM + deep (excludes awake periods).
        # This is the "asleep" time, not "in bed" time.
        if light + rem + deep > 0:
            metrics['sleep_duration'] = timedelta(milliseconds=light + rem + deep)

        if deep:
            metrics['deep_sleep'] = timedelta(milliseconds=deep)
        if rem:
            metrics['rem_sleep'] = timedelta(milliseconds=rem)
        if light:
            metrics['light_sleep'] = timedelta(milliseconds=light)
        if awake:
            metrics['awake_time'] = timedelta(milliseconds=awake)

        # Sleep performance % (Whoop's "how much of the sleep you needed")
        if score.get('sleep_performance_percentage') is not None:
            metrics['sleep_score'] = int(score['sleep_performance_percentage'])

        # Sleep consistency % (timing consistency vs typical schedule)
        if score.get('sleep_consistency_percentage') is not None:
            metrics['sleep_consistency'] = int(score['sleep_consistency_percentage'])

        # Respiratory rate (breaths per minute during sleep)
        if score.get('respiratory_rate') is not None:
            metrics['respiratory_rate'] = Decimal(
                str(score['respiratory_rate'])
            ).quantize(Decimal('0.01'))

    return metrics


def _paginate_whoop(fetch_fn, data_source, start, end, limit: int = 25, max_pages: int = 1000):
    # max_pages prevents infinite loops if Whoop returns same next_token repeatedly.
    next_token = None
    seen_tokens = set()
    for _ in range(max_pages):
        page = fetch_fn(data_source, start, end, next_token=next_token, limit=limit)
        for record in page.get('records', []):
            yield record
        next_token = page.get('next_token')
        if not next_token:
            return
        if next_token in seen_tokens:
            raise RuntimeError(
                f"Whoop pagination loop detected — next_token repeated: {next_token[:20]}"
            )
        seen_tokens.add(next_token)
    raise RuntimeError(
        f"Whoop pagination exceeded max_pages={max_pages} — possible API issue"
    )


def _save_whoop_raw_payload(user, provider_record_id, payload, schema_version, payload_dt):
    from workouts.models import WorkoutRawPayload

    canonical = json_lib.dumps(payload, sort_keys=True)
    sha = hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    raw, created = WorkoutRawPayload.objects.get_or_create(
        user=user,
        payload_sha256=sha,
        defaults={
            'provider': 'whoop',
            'payload': payload,
            'provider_workout_id': str(provider_record_id),
            'workout_started_at': payload_dt,
            'schema_version': schema_version,
        },
    )
    return raw, created


def sync_whoop_data(data_source, start, end) -> dict:   
    """
    1. Paginate through Whoop API
    2. Save raw payload (idempotent by SHA256)
    3. Normalize -> Aurora model

    Workouts go through create_workout_with_dedup (cross-source dedup).
    Recovery + sleep merge by date into a single HealthMetrics row per (user, date).
    """
    from workouts.models import Workout, HealthMetrics
    from workouts.services.dedup import create_workout_with_dedup

    user = data_source.user
    stats = {
        'workouts': {'fetched': 0, 'new': 0, 'existing': 0, 'errors': 0},
        'recovery': {'fetched': 0, 'errors': 0},
        'sleep': {'fetched': 0, 'naps_skipped': 0, 'errors': 0},
        'health_metrics': {'created': 0, 'updated': 0, 'errors': 0},
    }

    for record in _paginate_whoop(fetch_whoop_workouts, data_source, start, end):
        stats['workouts']['fetched'] += 1
        try:
            external_id = record['id']
            # Workout-level idempotency — skip if already synced
            if Workout.objects.filter(
                source=data_source,
                external_id=external_id,
            ).exists():
                stats['workouts']['existing'] += 1
                continue

            # Save raw
            start_dt = datetime.fromisoformat(record['start'].replace('Z', '+00:00'))
            _save_whoop_raw_payload(
                user=user,
                provider_record_id=external_id,
                payload=record,
                schema_version='whoop-v2-w-2026.06',
                payload_dt=start_dt,
            )

            # Normalize + create through dedup engine
            sport_type = get_or_create_whoop_sport_type(record.get('sport_name', 'other'))
            workout_kwargs = normalize_whoop_workout(record, user, sport_type, data_source)
            workout_kwargs.pop('user')  # passed positionally per service signature
            create_workout_with_dedup(user, **workout_kwargs)

            stats['workouts']['new'] += 1
        except Exception:
            import traceback
            traceback.print_exc()
            stats['workouts']['errors'] += 1

    # RECOVERY + SLEEP streams (merge by date into HealthMetrics) 
    metrics_by_date = {}  # date -> kwargs dict

    # Recovery first (creates initial rows)
    for record in _paginate_whoop(fetch_whoop_recovery, data_source, start, end):
        stats['recovery']['fetched'] += 1
        try:
            created_dt = datetime.fromisoformat(record['created_at'].replace('Z', '+00:00'))
            _save_whoop_raw_payload(
                user=user,
                provider_record_id=f"recovery_{record['cycle_id']}",
                payload=record,
                schema_version='whoop-v2-r-2026.06',
                payload_dt=created_dt,
            )

            normalized = normalize_whoop_recovery(record, user, data_source)
            date = normalized['date']
            if date in metrics_by_date:
                # Merge non-key fields into existing entry
                metrics_by_date[date].update({
                    k: v for k, v in normalized.items()
                    if k not in ('user', 'source', 'date')
                })
            else:
                metrics_by_date[date] = normalized
        except Exception:
            import traceback
            traceback.print_exc()
            stats['recovery']['errors'] += 1

    # Sleep next (merges sleep fields into recovery-keyed entries, or creates new)
    for record in _paginate_whoop(fetch_whoop_sleep, data_source, start, end):
        stats['sleep']['fetched'] += 1
        try:
            # Always save raw — including naps (they have value for future Nap model)
            end_dt = datetime.fromisoformat(record['end'].replace('Z', '+00:00'))
            _save_whoop_raw_payload(
                user=user,
                provider_record_id=record['id'],
                payload=record,
                schema_version='whoop-v2-s-2026.06',
                payload_dt=end_dt,
            )

            normalized = normalize_whoop_sleep(record, user, data_source)
            if normalized is None:
                stats['sleep']['naps_skipped'] += 1
                continue

            date = normalized['date']
            if date in metrics_by_date:
                metrics_by_date[date].update({
                    k: v for k, v in normalized.items()
                    if k not in ('user', 'source', 'date')
                })
            else:
                metrics_by_date[date] = normalized
        except Exception:
            import traceback
            traceback.print_exc()
            stats['sleep']['errors'] += 1

    # Persist HealthMetrics 
    for date, kwargs in metrics_by_date.items():
        try:
            existing = HealthMetrics.objects.filter(
                user=user,
                date=date,
                source=data_source,
            ).first()

            if existing:
                # Update existing record with newly merged fields
                for field, value in kwargs.items():
                    if field not in ('user', 'source', 'date'):
                        setattr(existing, field, value)
                existing.save()
                stats['health_metrics']['updated'] += 1
            else:
                # Simple primary election: first record for (user, date) wins.
                # Full source-priority logic lives in HealthMetricsSerializer;
                # this MVP path skips it for sync simplicity. See TECH_DEBT.
                already_primary = HealthMetrics.objects.filter(
                    user=user, date=date, is_primary=True,
                ).exists()
                HealthMetrics.objects.create(
                    is_primary=not already_primary,
                    **kwargs,
                )
                stats['health_metrics']['created'] += 1
        except Exception:
            import traceback
            traceback.print_exc()
            stats['health_metrics']['errors'] += 1

    return stats

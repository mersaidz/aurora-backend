"""
Strava OAuth + API integration.
Token storage: encrypted into DataSource.access_token / refresh_token
via the EncryptedTextField (Fernet) I built earlier.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlencode


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

import requests 
from datetime import datetime, timezone as dt_timezone 

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
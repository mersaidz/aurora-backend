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
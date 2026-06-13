"""
Whoop OAuth + sync service.
(Oath2.0)
Docs: https://developer.whoop.com/docs/developing/oauth
"""
from __future__ import annotations
import secrets
from urllib.parse import urlencode

from django.conf import settings

import requests


#Constants
WHOOP_AUTH_URL = 'https://api.prod.whoop.com/oauth/oauth2/auth'
WHOOP_TOKEN_URL = 'https://api.prod.whoop.com/oauth/oauth2/token'
WHOOP_API_BASE = 'https://api.prod.whoop.com/developer/v1/'

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
    # Build the Whoop OAuth authorization URL.
    # User is redirected here, authenticates with Whoop, then redirected back
    # to WHOOP_REDIRECT_URI with `code` and `state` query parameters.
    
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

    # DEBUG: log exact request
    print('=' * 60)
    print('WHOOP TOKEN EXCHANGE REQUEST:')
    print(f'  URL: {WHOOP_TOKEN_URL}')
    print(f'  grant_type: {payload["grant_type"]!r}')
    print(f'  redirect_uri: {payload["redirect_uri"]!r}')
    print(f'  client_id: {payload["client_id"][:15]}...')
    print(f'  client_secret length: {len(payload["client_secret"])}')
    print(f'  code: {code[:20]}...')
    print('=' * 60)

    response = requests.post(
        WHOOP_TOKEN_URL,
        data=payload,
        timeout=10,
    )

    if not response.ok:
        print('WHOOP TOKEN EXCHANGE FAILED:')
        print(f'  Status: {response.status_code}')
        print(f'  Body: {response.text}')

    response.raise_for_status()
    return response.json()
    


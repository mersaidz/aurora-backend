"""
JSON Denylist Filter: Keeping sensitive data safe.

Note on privacy: This architecture prevents from accidental leaks of user tokens, 
passwords, or sensitive keys into logs and database payloads. It operates in two ways:

1. find_forbidden_keys: Searches the JSON and raises an error during dev/test 
   if any sensitive data accidentally sneaks into AuditLog.extra_info.
2. sanitize_payload: Cleans up raw JSON responses from Garmin/Strava, replacing forbidden 
   values with '[REDACTED]' right before we save them to WorkoutRawPayload.
"""
from __future__ import annotations
from typing import Any

DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset({
    'access_token', 'refresh_token', 'token', 'id_token', 'token_expires',
    'authorization', 'auth', 'password', 'passwd', 'secret', 'jwt',
    'api_key', 'apikey', 'client_secret', 'private_key',
    'session', 'session_id', 'cookie', 'set-cookie',
})

REDACTED = '[REDACTED]'


def _is_forbidden(key: Any, forbidden: frozenset[str]) -> bool:
    return isinstance(key, str) and key.lower() in forbidden


def find_forbidden_keys(
    data: Any,
    forbidden: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
    _path: str = '',
) -> list[str]:
    hits: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            key_path = f'{_path}.{k}' if _path else str(k)
            if _is_forbidden(k, forbidden):
                hits.append(key_path)
            hits.extend(find_forbidden_keys(v, forbidden, key_path))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            hits.extend(find_forbidden_keys(item, forbidden, f'{_path}[{i}]'))
    return hits


def sanitize_payload(
    data: Any,
    forbidden: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
) -> Any:
    
    #Return a deep copy of "data" where any value whose key matches the
    #denylist is replaced with "[REDACTED]"". Non-dict/non-list values are
    #returned as-is.
    if isinstance(data, dict):
        return {
            k: (REDACTED if _is_forbidden(k, forbidden) else sanitize_payload(v, forbidden))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [sanitize_payload(item, forbidden) for item in data]
    return data

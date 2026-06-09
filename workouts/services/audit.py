from __future__ import annotations

from typing import Any, Optional
from workouts.models import AuditLog
from workouts.sanitize import DEFAULT_SENSITIVE_KEYS, _is_forbidden


def log_event(
    *,
    user=None,
    user_id_snapshot: Optional[int] = None,
    action: str,
    request=None,
    platform: str = '',
    extra_info: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: str = '',
) -> AuditLog:
    """
    Centralized helper to log audit events.
    
    Extracts metadata from request and silently strips forbidden keys 
    from extra_info. Use user_id_snapshot if the User row is hard-deleted.
    """
    if request is not None:
        if ip_address is None:
            ip_address = _extract_client_ip(request)
        if not user_agent:
            user_agent = request.META.get('HTTP_USER_AGENT', '')[:512]

    # Clean sensitive keys before database write
    cleaned_extra = _strip_forbidden_keys(extra_info) if extra_info else {}

    return AuditLog.objects.create(
        user=user,
        user_id_snapshot=user_id_snapshot,
        action=action,
        platform=platform,
        ip_address=ip_address,
        user_agent=user_agent,
        extra_info=cleaned_extra,
    )


def _extract_client_ip(request) -> Optional[str]:
    #Extract client IP from request, prioritizing X-Forwarded-For header. 
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def _strip_forbidden_keys(data: Any) -> Any:
    # Recursively remove sensitive keys entirely from the data structure.
    if isinstance(data, dict):
        return {
            k: _strip_forbidden_keys(v)
            for k, v in data.items()
            if not _is_forbidden(k, DEFAULT_SENSITIVE_KEYS)
        }
    if isinstance(data, list):
        return [_strip_forbidden_keys(item) for item in data]
    return data
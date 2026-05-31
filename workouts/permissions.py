"""
The ultimate Shield: Centralized access control for all athlete/user data.

Note on architecture: Never write manual 'obj.user == request.user' checks anywhere else. 
Every view, serializer, and task must call this module to check permissions. 

1. Today (MVP): Only the athlete can view or modify their own workouts and health data.
2. In future (v2): This is where I will handle coach access, club managers, 
   and support roles without breaking the rest of the code.

When I get to the next version, I'll only change this file.
"""
from __future__ import annotations

from typing import Any


def _is_authenticated(viewer) -> bool:
    return bool(viewer is not None and getattr(viewer, 'is_authenticated', False))


def can_view_athlete_data(viewer, athlete) -> bool:
    """Can ``viewer`` read data belonging to ``athlete``?"""
    if not _is_authenticated(viewer) or athlete is None:
        return False
    
    # 1. Athletes can always view their own data
    if viewer.pk == athlete.pk:
        return True
        
    # 2. Staff support / admin read access — auditable separately via AuditLog
    if getattr(viewer, 'is_staff', False):
        return True
        
    # TODO later on: lookup active CoachAccessGrant(coach=viewer, athlete=athlete)
    return False


def can_modify_athlete_data(viewer, athlete) -> bool:
    """Can ``viewer`` write/update/delete data belonging to ``athlete``?"""
    if not _is_authenticated(viewer) or athlete is None:
        return False
    
    # 1. Athletes can always modify their own data
    if viewer.pk == athlete.pk:
        return True
    
    # TODO later on: Coaches will get restricted write access (to assign training plans 
    #             or update HR zones). They will NEVER be allowed to edit raw workout files.
    # Staff/Admins never write data directly — they must use audited admin actions instead.
    return False


def can_view_object(viewer, obj: Any) -> bool:
    """Convenience: dispatch via ``obj.user``. Use when the resource has a single owner."""
    return can_view_athlete_data(viewer, getattr(obj, 'user', None))


def can_modify_object(viewer, obj: Any) -> bool:
    """Convenience: dispatch via ``obj.user``. Use when the resource has a single owner."""
    return can_modify_athlete_data(viewer, getattr(obj, 'user', None))
"""
Cross-FK validation mixin to prevent data leaks.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models


class UserOwnedMixin(models.Model):
    """
    Abstract mixin that ensures all related records belong to the exact same user.
    
    Preventing those scenarios when nobody can accidentally(or on puprose) link one
    user's workout to someone else's data source or profile
    (User #2 connecting someone's garmin(of user#5))
   
    """
    # Tuple of FK field names whose "related.user_id" must equal "self.user_id".
    _owner_check_fields: tuple[str, ...] = ()

    class Meta:
        abstract = True

    def clean(self) -> None:
        super().clean()
        owner_id = getattr(self, 'user_id', None)
        if owner_id is None:
            # No user assigned yet — nothing to compare against.
            return

        errors: dict[str, str] = {}
        for field_name in self._owner_check_fields:
            related = getattr(self, field_name, None)
            if related is None:
                continue
                
            related_owner = getattr(related, 'user_id', None)
            if related_owner is not None and related_owner != owner_id:
                errors[field_name] = (
                    f"Security mismatch: {field_name} belongs to user {related_owner}, "
                    f"but this record is owned by user {owner_id}."
                )

        if errors:
            raise ValidationError(errors)

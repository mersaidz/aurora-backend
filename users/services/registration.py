
#User registration service.

from __future__ import annotations

from django.db import transaction
from users.models import UserProfile, User


def register_user(*, email: str, password: str, **extra_fields) -> User:
    """
    Create a User and its UserProfile in a single atomic transaction.

    Dealing with database transactions always makes me check the docs twice, 
    but we absolutely need 'transaction.atomic' here. If we don't wrap this, we risk 
    creating a User without a profile if something breaks halfway through, which will 
    immediately crash the frontend on the first GET /profile/.

    The get_or_create() below is just a safety buffer in case our post_save signal 
    already had an a false start and created the profile first.
    """
    with transaction.atomic():
        user = User.objects.create_user(email=email, password=password, **extra_fields)
        
        # Defensive check in case the signal already created the profile.
        UserProfile.objects.get_or_create(user=user)
        return user

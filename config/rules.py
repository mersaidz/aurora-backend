from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed


def universal_user_authentication_rule(user) -> bool:
    """
    Base authentication rule for Aurora.
    Returns False if the user is missing, inactive, or soft-deleted.
    """
    if user is None:
        return False
    if not user.is_active:
        return False
    if getattr(user, 'deleted_at', None) is not None:
        return False
    return True


class AuroraJWTAuthentication(JWTAuthentication):
    """
    Custom JWT Authentication backend for DRF.
    Enforces the soft-delete check on every incoming API request.
    """
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not universal_user_authentication_rule(user):
            raise AuthenticationFailed('User is inactive or deleted', code='user_not_found')
        return user
from django.contrib.auth import get_user_model

User = get_user_model

def universal_user_authentication_rule(user) -> bool:
    if user is None:
        return False
    
    if not user.is_active:
        return False
    
    if getattr(user, 'deleted_at', None) is not None:
        return False
    
    return True
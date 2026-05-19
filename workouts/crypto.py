import os 
import logging
from django.db import models
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

def _get_fernet() -> Fernet:
    key = os.getenv('FERNET_KEY')
    if not key:
        raise ImproperlyConfigured("FERNET_KEY is not set in environment variables")
    try:
        return Fernet(key.encode())
    except Exception as e:
        raise ImproperlyConfigured(f"Invalid FERNET_KEY: {e}")
    
def encrypt_value(value: str) -> str:
    if not value:
        return value # Don't encrypt empty values
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # Value is not encrypted
        logger.error("Invalid token for encryption")
        return None 
    
class EncryptedTextField(models.TextField):
    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return encrypt_value(value)
    
    def get_prep_value(self, value):
        if value is None:
            return None
        return encrypt_value(value)
    
    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        return self.get_prep_value(value)
    

        



from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        # Wake up our custom signal handlers (like auto-creating AthleteProfile) on server startup.
        # noqa: F401 silences the IDE warning about an unused import, which is intentional here.
        from users import signals  # noqa: F401

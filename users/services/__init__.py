"""
Service layer for users app.

Convention: views, signals, management commands and tests should call these
functions instead of using "User.objects.create()" or "user.delete()"
directly. Putting business operations behind a thin service makes them easy
to audit, atomic, and consistent across entry points.
"""

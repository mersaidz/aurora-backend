# Aurora — Tech Debt

A list of known issues and architectural shortcuts that were intentionally deferred for the MVP. 

---

## Schema & Models

### `unit_system` placement
* **Where:** `users.models.AthleteProfile.unit_system`
* **Problem:** It's a UI preference (metric vs imperial) but currently lives on the profile. It should be moved directly to the `User` model to avoid unnecessary database joins during workout processing.
* **Todo:** Add `unit_system` to `User`, run a data migration to copy existing preferences, and drop the column from `AthleteProfile`.

### Athlete age filtering
* **Where:** `users.models.AthleteProfile.age` (@property)
* **Problem:** Computed dynamically in Python, so it works for display but can't be used in ORM filters (e.g., `filter(age__gte=20)` fails). 
* **Todo:** When age-based filtering is actually needed (like coach dashboards), implement a custom manager method using `birth_date` math. Do NOT use Django's `GeneratedField` since birthdays change without row saves.

---

## Architecture & Workflows

### Profile creation overlap
* **Where:** `users.signals.ensure_athlete_profile` vs `register_user` service.
* **Problem:** We have a post_save signal as a safety net for django-admin or `createsuperuser`, but the registration service should ideally own this completely. Also, `User.objects.bulk_create()` bypasses signals anyway.
* **Todo:** Route all user creation paths through the service and safely remove the signal file.

### Hardcoded source priorities
* **Where:** `HEALTH_SOURCE_PRIORITY` and `WORKOUT_SOURCE_PRIORITY` in `workouts/serializers.py`.
* **Problem:** Data source priority (Garmin vs Polar vs Apple) is currently hardcoded. 
* **Todo:** Move this to a proper database model when we need to support custom per-user source overrides.

### Raw payload retention
* **Where:** `workouts.models.WorkoutRawPayload`
* **Problem:** Storing heavy raw JSON blobs from Strava/Garmin directly in Postgres. 
* **Todo:** When database size or backup times become an issue, migrate these blobs to object storage (S3/R2) and keep only URIs in Postgres.

---

## Auth & Endpoints

### SimpleJWT configuration
* **Where:** Settings and views.
* **Todo:** Complete SimpleJWT installation and add a custom authentication rule checking `deleted_at__isnull=True`. This ensures soft-deleted users have their access tokens revoked immediately instead of waiting for token expiration.
* **Todo:** Implement actual registration and login API views using the existing `register_user` service.

---

## Observability & Testing

### Direct AuditLog calls
* **Where:** `workouts.tasks` and `users.services.account_deletion`.
* **Problem:** Writing logs via direct `AuditLog.objects.create()` calls is messy and repetitive.
* **Todo:** Create an `audit.py` helper to automatically extract IP, User-Agent from requests, and run payloads through `sanitize_payload`.

### Missing Test Coverage
* **Todo:** Set up `pytest-django` and write integration tests for critical paths:
  * Workout deduplication logic (priority checks).
  * Account deletion flow and Celery task idempotency.
  * Email masking utility on `User.__str__`.

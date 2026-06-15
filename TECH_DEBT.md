# Aurora — Technical Debt & Backlog

This document tracks known architectural shortcuts, trade-offs, and gaps intentionally deferred from the MVP stage. 

---

## Schema & Models

### `unit_system` location
* **Where:** `users.models.UserProfile.unit_system`
* **Problem:** Currently lives on the profile table, causing unnecessary DB joins during workout payload processing.
* **Todo:** Move `unit_system` directly to the custom `User` model via a data migration.

### Dynamic User Age Filtering
* **Where:** `users.models.UserProfile.age` (`@property`)
* **Problem:** Calculated dynamically in Python. Works for display, but breaks ORM filtering (`filter(age__gte=20)` fails).
* **Todo:** Implement a custom model manager using database-level `birth_date` math. Django's `GeneratedField` won't work since birthdays change without row saves.

### Strength Training Schema
* **Where:** `workouts/models.py`
* **Problem:** The current schema is strictly cardio-shaped (avg_hr, distance, pace). Velocity-Based Training (VBT) devices like GymAware generate per-rep metrics (velocity, peak power, weight) which don't fit into the current flat structure.
* **Current state:** `WorkoutRawPayload` safely saves the raw provider JSON, but queryable models are missing.
* **Todo:** Add `StrengthSession` and `StrengthSet` models (storing per-rep arrays in a JSONField). Deferred because GymAware integration requires the Pro-tier team auth model first.

---

## Architecture & Workflows

### Profile Creation Split (Signals vs Services)
* **Where:** `users.signals.ensure_user_profile` vs `users.services.registration.register_user`
* **Problem:** The `post_save` signal is used as a safety net for django-admin, but the registration service should own profile creation entirely. Also, `bulk_create` bypasses signals.
* **Todo:** Consolidate user creation inside `register_user` and remove the signal.

### Hardcoded Source Priorities
* **Where:** `HEALTH_SOURCE_PRIORITY` and `WORKOUT_SOURCE_PRIORITY` constants.
* **Problem:** Source priority maps are static constants. Fine for MVP, but prevents per-user customization (e.g., overriding Garmin over Apple Health for specific users).
* **Todo:** Move priority maps to a database model with sensible defaults when multi-tenant overrides are requested.

### Raw Payload DB Bloat
* **Where:** `workouts.models.WorkoutRawPayload`
* **Problem:** Storing heavy, unparsed third-party JSON responses directly in PostgreSQL will bloat database backups at scale.
* **Todo:** Migrate raw blobs to S3/R2 storage, keeping only object URIs and hashes in Postgres.

### CoachAccessGrant (Deferred Team Permissions)
* **Problem:** Pro tier features require a relational model granting coaches scoped, audited access to athlete data. The MVP currently relies on flat `user=request.user` ownership filters.
* **Todo:** Implement `CoachAccessGrant(coach, athlete, scope, expires_at)` and integrate it into `workouts/permissions.py`.

---

## Naming & Conventions

### Role Enum Naming (`ATHLETE`)
* **Status:** Intentional choice. Aurora treats every user as an athlete, regardless of fitness level. 
* **Trigger:** If metrics-only users find the term alienating, migrate `ATHLETE` to `MEMBER`. The impact is minor since code paths are well-isolated.

---

## Authorization Predicates (Pending Integration)

* **Where:** `workouts/permissions.py`
* **Current Status:** 100% unit-tested via `test_permissions.py`. However, views currently use explicit `get_queryset().filter(user=self.request.user)` and serializers use manual `_ensure_owner()` checks.
* **Reason:** Kept manual to avoid over-engineering the MVP before complex coach/athlete relationships land.
* **Migration Plan:** Once team workflows land, replace manual ownership checks with `permissions.can_*` hooks in a single refactoring pass.

---

## Known Bugs & Edge Cases

### Workout Deletion: Constraint Conflict
* **Where:** `Workout.duplicate_of` + `workout_primary_state_consistent` CheckConstraint.
* **Problem:** Deleting a primary workout triggers `ON DELETE SET_NULL` on dependent duplicates. This leaves them in an invalid state (`is_primary=False, duplicate_of=NULL`), blowing up the database CheckConstraint.
* **Workaround:** Currently requires deleting duplicates before the primary.
* **Todo:** Investigate `ON DELETE CASCADE` or a `pre_delete` signal to safely auto-promote the oldest duplicate to primary.

### Whoop OAuth Token Limitations
* **Problem:** The Whoop API returns an `invalid_scope` error when requesting the `offline` scope (Ory Hydra rejection). Without it, tokens expire in 1 hour, forcing manual re-auth via `/whoop/connect/`.
* **Trade-off:** Accepted 1-hour expiration limit for the MVP rather than blocking the Whoop integration completely.
* **Todo:** Test Basic Auth headers for token exchange and contact Whoop developer support.

### Calorie Semantic Drift
* **Problem:** Whoop/Garmin "Active" calories exclude Basal Metabolic Rate (BMR), while Strava/Garmin "Total" calories include it. This causes up to a 50% discrepancy for the exact same session.
* **Todo:** Add a `calorie_methodology` field (`active` | `total`) and implement a normalization service based on computed active work.

### Missing Nap Normalization
* **Problem:** Whoop returns naps via `/v2/activity/sleep` (with `nap: true`). Currently, `normalize_whoop_sleep` skips them to prevent overwriting overnight metrics in `HealthMetrics`. Raw data is safe in `WorkoutRawPayload`, but unparsed.
* **Todo:** Design a standalone `Nap` model, parse historical payloads, and build a `SleepDebt` engine.

### Dedup Engine: Dead Battery Edge Case
* **Problem:** If a Garmin battery dies mid-ride, the truncated Strava record and the full Whoop record won't match because the overlap falls below `MIN_OVERLAP_RATIO` (50%).
* **Real-world Impact:** User sees two workouts for one actual session.
* **Todo:** Introduce a start-time proximity rule (if within 5 minutes and same sport, trigger dedup regardless of duration match) and source-fidelity weights.
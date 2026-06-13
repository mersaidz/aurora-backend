# Aurora — Tech Debt

A curated list of architectural shortcuts, naming nits, and known gaps intentionally deferred from the v1 MVP. Each item documents the *why* alongside the *what*, so future work lands with context.

---

## Schema & Models

### `unit_system` placement

* **Where:** `users.models.UserProfile.unit_system`
* **Problem:** This is a UI preference (metric vs imperial) but lives on the profile. It should be moved directly to the `User` model to avoid unnecessary database joins during workout processing.
* **Todo:** Add `unit_system` to `User`, run a data migration to copy existing preferences, drop the column from `UserProfile`.

### User age filtering

* **Where:** `users.models.UserProfile.age` (`@property`)
* **Problem:** Age is computed dynamically in Python — works for display but cannot be used in ORM filters (`filter(age__gte=20)` fails because age isn't a real column).
* **Todo:** When age-based filtering is actually needed (e.g., coach dashboards filtering by age category), implement a custom manager method using `birth_date` math. Do NOT use Django's `GeneratedField` — birthdays change the result without row saves.

### Strength-training schema not yet shaped

* **Where:** `workouts/models.py` — no dedicated strength models yet.
* **Problem:** The current `Workout` schema is cardio-shaped (rate-based metrics like avg_hr, avg_power, distance, pace). GymAware and other velocity-based-training (VBT) sources produce per-rep records: exercise name, weight, individual reps, mean/peak velocity per rep, power output per rep. These don't fit cleanly into the existing `Workout` row.
* **Current capability:** `WorkoutRawPayload` already absorbs arbitrary provider JSON, so GymAware data can be synced and stored as raw payload immediately. What's missing is the parsed, query-able structured form.
* **Todo (Phase 2):** Add `StrengthSession` (one per training session) and `StrengthSet` (one per set, with per-rep metrics as a JSON array) models. Link to `Workout` via OneToOne or standalone — decision depends on coach-dashboard query patterns.
* **Why deferred:** GymAware integration itself is Pro-tier work, blocked on `CoachAccessGrant`. No point building strength schema before the auth model for team data exists.

---

## Architecture & Workflows

### Profile creation overlap (signal vs service)

* **Where:** `users.signals.ensure_user_profile` vs `users.services.registration.register_user`
* **Problem:** A `post_save` signal exists as a safety net for django-admin and `createsuperuser`, but the registration service should ideally own this completely. Also, `User.objects.bulk_create()` bypasses signals entirely — silent failure mode if anyone ever needs bulk imports.
* **Todo:** Route all user-creation paths through `register_user`. Then safely remove the signal. Add a regression test that `bulk_create` followed by an admin save still produces a working profile.

### Hardcoded source priorities

* **Where:** `HEALTH_SOURCE_PRIORITY` and `WORKOUT_SOURCE_PRIORITY` in `workouts/serializers.py`.
* **Problem:** Data-source priority (Garmin vs Polar vs Apple Health, etc.) is hardcoded as module-level constants. Fine for v1, but doesn't support per-user customization (e.g., a power-meter-only athlete who wants to override the workout priority map).
* **Todo:** When per-user source overrides are actually requested, move the maps to a proper database model with sensible defaults. Migration is straightforward — the in-code defaults become seed data.

### Raw payload retention strategy

* **Where:** `workouts.models.WorkoutRawPayload`
* **Problem:** Storing heavy raw JSON blobs from Strava/Garmin/etc. directly in PostgreSQL. Fine at v1 scale, problematic at 100K+ users where backup times and disk costs become real.
* **Todo:** When database size or backup times become an operational issue, migrate raw blobs to object storage (S3 / Cloudflare R2) and keep only URIs and metadata in Postgres. The `payload_sha256` field stays for idempotency.

### `CoachAccessGrant` — team workflow boundary (deferred to v2/v3)

* **Where:** Not yet implemented. Will live in `users/` or a new `teams/` app.
* **Problem:** The Pro tier requires a model granting coaches scoped access to athletes' data, with audit logging on every cross-user data view. The v1 architecture deliberately uses simple per-user filters and does not yet implement the coach-athlete grant relationship.
* **Todo:** Design `CoachAccessGrant(coach, athlete, granted_at, scope, expires_at)` with `AuditLog` integration on every read. Wire `workouts/permissions.py` predicates to query it.
* **Trigger:** Lands together with Coach Dashboard (Phase 3). GymAware integration's team-account-to-athlete mapping is blocked on this model.

---

### Email masking utility not directly tested

* **Where:** `users.models.User.__str__` — masks the email local part (e.g., `john@example.com` → `jo***@example.com`) for safe logging.
* **Problem:** Method is exercised indirectly via repr/logging in other tests, but no explicit test pins the masking rules (length thresholds, partial vs full mask, empty-email fallback, missing-`@` defensive branch).
* **Todo:** Add `test_user_str_masking_*` tests covering: long local part (truncated mask), short local part (full asterisks), missing email (fallback to `User #{pk}`), missing `@` in stored value (defensive branch).

---

## Naming & Conventions

### Role enum naming — `ATHLETE` vs broader alternatives (conscious choice)

* **Where:** `users/models.py` — `User.Role.ATHLETE / COACH / ADMIN`.
* **Status:** Intentional v1 choice. Aurora's positioning treats every user as an athlete in training, regardless of competitive level. The Role enum is a permission boundary (tracked-person vs coach vs admin), not a skill label.
* **Trigger:** If user research shows the "athlete" framing alienates casual users (e.g., sleep-only Free tier subscribers), rename to `MEMBER` in one migration. Code paths are isolated — refactoring cost is low.

---

## `workouts/permissions.py` — Authorization Predicates (Designed & Tested, Pending Wiring)

### Context

I created a centralized authorization module `workouts/permissions.py` with core safety predicates:

* `can_view_athlete_data(viewer, athlete)` / `can_modify_athlete_data(viewer, athlete)`
* `can_view_object(viewer, obj)` / `can_modify_object(viewer, obj)` (wrappers that extract `.user` safely)

The long-term goal is to route every view, serializer, and background task through this module for authorization decisions.

### Current Status (v1)

* **Coverage:** 100% unit-tested via `workouts/tests/test_permissions.py`. All truth tables and the fail-closed security pattern are locked down.
* **Wiring:** Temporarily bypassed in v1 code. Current views and serializers use simple, hardcoded ownership checks:
  - Views filter data directly: `get_queryset().filter(user=self.request.user)`
  - Serializers enforce ownership manually: `_ensure_owner()`
* **Reason:** Keeping v1 simple for MVP launch. Implementing complex predicate wrappers right now adds unnecessary abstraction before it's actually needed.

### v2 Migration Plan

When `CoachAccessGrant` (allowing coaches to view/manage athlete data) lands, I will replace all manual `user == request.user` checks with `permissions.can_*` calls in a single refactoring pass. Since the permission rules are already fully pinned by tests, this migration will be mechanical and completely safe.

## Workout deletion — SET_NULL vs CheckConstraint conflict

* **Where:** `workouts/models.py` Workout.duplicate_of + workout_primary_state_consistent constraint
* **Problem:** When a primary Workout is deleted, `ON DELETE SET_NULL` sets dependent duplicates' `duplicate_of` to NULL. This leaves the duplicate in `is_primary=False, duplicate_of=NULL` state, which violates the `workout_primary_state_consistent` CheckConstraint.
* **Workaround:** Delete duplicates BEFORE deleting their primary (manual two-step deletion).
* **Todo:** Options to investigate:
  1. Change `on_delete=SET_NULL` to `on_delete=CASCADE` — automatic cleanup of duplicates when primary deleted. Risk: surprising behavior, loses duplicate audit trail.
  2. Add pre_delete signal that promotes duplicates to primary (is_primary=True, duplicate_of=NULL) when their primary is deleted. Preserves audit but creates orphans.
  3. Add custom Workout.delete() that handles the order automatically.
* **Trigger:** Address when adding admin actions for sync-replay or test-data-reset workflows.

## Technical Debt: Whoop Manual Re-Auth Every Hour

### Problem
* The Whoop API does not return a `refresh_token` with the default scopes.
* Adding the `offline` scope causes an `invalid_scope` error because Whoop's Ory Hydra system rejects it.
* As a result, the `access_token` expires in 1 hour, and the user must manually re-authenticate via `/whoop/connect/`.

### Production Requirements (To Fix Later)
* Check the Whoop developer dashboard settings for refresh tokens.
* Try using a `Basic Auth` header instead of sending credentials in the request body.
* Contact Whoop developer support if the issue continues.

### Trade-off
* Live with a 1-hour manual auth limit for now vs. blocking the feature.
* **Decision:** Ship now, refactor later.
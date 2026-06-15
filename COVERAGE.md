# Aurora — Test Coverage Baseline

**Total Coverage: 75%** (2125 statements, 535 missed)

This document tracks the current test coverage baseline for the Aurora backend. Core business logic and security paths are tightly covered, while external API integration modules are verified manually pending mocking infrastructure.

---

## Coverage by category

### Core Business Logic & Security (≥90%)

| Module | Coverage | Notes |
|---|---|---|
| `workouts/permissions.py` | 100% | API access control and security predicates |
| `workouts/services/audit.py` | 96% | Core audit logging with PII masking |
| `workouts/crypto.py` | 93% | Fernet token encryption for data sources |
| `users/services/registration.py` | 100% | User signup and profiling flows |
| `users/services/account_deletion.py` | 100% | GDPR-compliant data erasure |
| `users/views.py` | 100% | JWT Auth endpoints |

### Models & Serializers (80–89%)

| Module | Coverage | Target |
|---|---|---|
| `workouts/services/dedup.py` | 88% | **Cross-source dedup engine** (11 edge-case tests) |
| `workouts/serializers.py` | 84% | Validation for Workouts, HealthMetrics, and Profiles |
| `users/models.py` | 82% | Custom User model, roles, and masks |
| `workouts/models.py` | 81% | Core domain models |

### Integration & Async Tasks (Expected Gaps)

These modules handle external OAuth flows, heavy API payloads (Strava, Whoop), and async workers. Unit coverage is lower here because full HTTP mocking is moved to the next development phase (**Task #41**).

| Module | Coverage | Current Verification |
|---|---|---|
| `workouts/whoop.py` | 13% | Manually tested with multi-day Whoop v2 production payloads |
| `workouts/strava.py` | 22% | Manually tested; covers webhook validation and token rotation |
| `workouts/views.py` | 37% | OAuth connect/callback/sync endpoints |
| `workouts/tasks.py` | 43% | Celery async tasks |
| `workouts/sanitize.py` | 67% | Payload sanitization (happy path covered) |

---

## Engineering Notes: Why Integration Coverage is Lower

1. **Real-world Manual Testing:** During the MVP phase, integration with Strava and Whoop was thoroughly tested against real athletic multi-source data. This manual deep-dive helped catch and fix critical edge cases:
   * Database constraints crashes (e.g., `varchar(20)` overflow on Whoop IDs).
   * Semantic drift across platforms (e.g., converting kJ to kcal properly between Garmin and Strava).
   * Token race conditions on rapid browser refreshes during OAuth callback.

2. **Deduplication Verification:** Cross-source deduplication logic was validated using real sequential logs (such as multi-sport brick sessions: Cycling immediately followed by Running) to ensure the 5-minute time-margin doesn't accidentally wipe valid sequential workouts.

3. **Next Steps (Task #41):** Future iterations will implement `responses` or `vcrpy` to mock third-party API responses, simulating network dropouts, rate limits, and paginated syncs.

---

## How to run coverage

```bash
# Terminal report with missing lines
pytest --cov=workouts --cov=users --cov-report=term-missing

# Generate interactive HTML report
pytest --cov=workouts --cov=users --cov-report=html
open htmlcov/index.html
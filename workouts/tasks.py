"""
Background cleanup of user data (Celery task)

Notes and thoughts: Meta courses in Coursera didn't teach me this, im learning a lot now
by working on my own project with my own architectural vision, domain experience and athlete background 
I honestly don't know yet how this gonna work and will perform under real heavy load, 
but I'm going to test it thoroughly. For now, the implementation plan is:

1. Soft Delete: The service layer marks user.deleted_at = now() and triggers 
   this background task.
2. Batching: To prevent database locks for active athletes, the task deletes 
   heavy WorkoutRawPayload rows in small batches of 100 with a short pause between them.
3. Hard Delete: The task writes the final 'account_deletion_completed' AuditLog 
   (keeping an email snapshot), then hard-deletes the User. Postgres cascade-deletes 
   the lighter tables (Workouts, HealthMetrics, etc.) automatically.
4. Idempotency: If Celery retries the task after a network glitch, the code checks 
   for the final AuditLog and exits immediately to avoid double-deletion bugs.

As i wrotem im gonna test it with real data, and adjust as we go.
"""
import time
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from workouts.services.audit import log_event
from .models import WorkoutRawPayload, AuditLog

logger = logging.getLogger(__name__)
User = get_user_model()

PAYLOAD_BATCH_SIZE = 100
PAYLOAD_BATCH_SLEEP_SECONDS = 0.5


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def delete_user_raw_payloads_in_batches(self, user_id, batch_size=PAYLOAD_BATCH_SIZE):
    """Drain WorkoutRawPayload for ``user_id`` in batches, then purge the user."""

    #  Idempotency guard 
    # Celery may retry this task (worker crash, broker hiccup, manual replay).
    # If we already recorded the completion event, exit silently — no double-log,
    # no double-delete-attempt, no AuditLog noise.
    already_completed = AuditLog.objects.filter(
        user_id_snapshot=user_id,
        action='account_deletion_completed',
    ).exists()
    if already_completed:
        logger.info("account_deletion_completed already recorded for user_id=%s; skipping retry", user_id)
        return

    #  Batched payload drain 
    # Pull only ids per batch so we don't load full JSON blobs into worker memory.
    # Sleep between batches yields the DB connection back so live traffic isn't
    # blocked when the user being deleted has tens of thousands of payloads.
    while True:
        ids = list(
            WorkoutRawPayload.objects
            .filter(user_id=user_id)
            .values_list('id', flat=True)[:batch_size]
        )
        if not ids:
            break
        WorkoutRawPayload.objects.filter(id__in=ids).delete()
        time.sleep(PAYLOAD_BATCH_SLEEP_SECONDS)

    #   Final completion audit + user purge 
    # Load the user before logging so AuditLog.save() can snapshot email/id from
    # the live FK. If the user row is somehow already gone (manual cleanup, prior
    # partial run before idempotency was in place), fall back to id-only snapshot.
    user = User.objects.filter(pk=user_id).first()

    if user is not None:
        log_event(
            user=user,
            action='account_deletion_completed',
            extra_info={"status": "raw_payloads_drained_user_purged"},
        )
        # Cascades to Workout / HealthMetrics / LactateMeasurement / DataSource etc.
        # Those tables are bounded per user and small relative to raw payloads.
        user.delete()
    else:
        log_event(
            user_id_snapshot=user_id,
            action='account_deletion_completed',
            extra_info={"status": "user_row_missing_payloads_drained"},
        )

    logger.info("Account deletion completed for user_id=%s", user_id)

#Account deletion orchestration.

from __future__ import annotations
from django.db import transaction
from django.utils import timezone
from users.models import User
from workouts.models import AuditLog
from workouts.tasks import delete_user_raw_payloads_in_batches


def schedule_account_deletion(
    user: User,
    *,
    ip_address: str | None = None,
    user_agent: str = '',
) -> None:
    """
    Mark a user for soft-delete and dispatch the Celery task to clean up raw payloads.

    This is actually my first time setting up a Celery workflow, but after digging into 
    how background workers handle database states, I realized we have to be careful. 
    Wrapping the DB changes in a transaction is a must, but firing the task has to 
    happen outside of it to prevent the worker from racing the database commit.
    """
    with transaction.atomic():
        user.deleted_at = timezone.now()
        user.is_active = False
        user.save(update_fields=['deleted_at', 'is_active'])

        AuditLog.objects.create(
            user=user,
            action='account_deletion_scheduled',
            ip_address=ip_address,
            user_agent=user_agent,
            extra_info={'status': 'soft_deleted_awaiting_celery'},
        )

    # Dispatching strictly outside the transaction block. 
    # Since the DB needs a moment to commit, this ensures the worker actually 
    # sees the 'deleted_at' flag instead of throwing a race condition error.
    delete_user_raw_payloads_in_batches.delay(user.id)

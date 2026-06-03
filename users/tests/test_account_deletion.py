from __future__ import annotations
from unittest.mock import patch #learned about mock(car security system analogy)
import pytest
from users.services.account_deletion import schedule_account_deletion
from workouts.models import AuditLog

@pytest.mark.django_db
class TestScheduleAccountDeletion:

    # SOFT DELETE TESTING
    @patch('users.services.account_deletion.delete_user_raw_payloads_in_batches')
    def test_marks_user_as_soft_deleted(self, mock_task, athlete_user):
      
        assert athlete_user.deleted_at is None
        assert athlete_user.is_active is True

        schedule_account_deletion(athlete_user)

        athlete_user.refresh_from_db()

        # checking last string
        assert athlete_user.deleted_at is not None
        assert athlete_user.is_active is False

    # Testing snapshots in auditlog (GDPR)
    @patch('users.services.account_deletion.delete_user_raw_payloads_in_batches')
    def test_writes_audit_log_with_snapshots(self, mock_task, athlete_user):
        original_id = athlete_user.id
        original_email = athlete_user.email

        schedule_account_deletion(athlete_user)

        # extracting log from datab which should have been created automatically
        log = AuditLog.objects.get(
            user=athlete_user, action='account_deletion_scheduled'
        )

        # Checking that log is permanently saved ID and email
        # necessary, even if user is permanently deleted, the log is not.
        assert log.user_id_snapshot == original_id
        assert log.user_email_snapshot == original_email
        assert log.extra_info.get('status') == 'soft_deleted_awaiting_celery'

    # Celery background task launch test.
    @patch('users.services.account_deletion.delete_user_raw_payloads_in_batches')
    def test_dispatches_cleanup_task_with_user_id(self, mock_task, athlete_user):
        schedule_account_deletion(athlete_user)

        # The magic of mocks. Verifying that the .delay() method of our
        # Celery task was called exactly once, and with our user ID.
        mock_task.delay.assert_called_once_with(athlete_user.id)

    # Logging test (IP/Browser)
    @patch('users.services.account_deletion.delete_user_raw_payloads_in_batches')
    def test_propagates_ip_and_user_agent_to_audit(self, mock_task, athlete_user):
        # Our simulator
        schedule_account_deletion(
            athlete_user,
            ip_address='203.0.113.42',
            user_agent='Mozilla/5.0 (Aurora Mobile)',
        )

        log = AuditLog.objects.get(
            user=athlete_user, action='account_deletion_scheduled'
        )
        assert log.ip_address == '203.0.113.42'
        assert log.user_agent == 'Mozilla/5.0 (Aurora Mobile)'
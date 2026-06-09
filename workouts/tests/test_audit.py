"""
Tests for workouts.services.audit.log_event — centralized audit log helper.
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIRequestFactory

from workouts.models import AuditLog
from workouts.services.audit import log_event


@pytest.mark.django_db
class TestLogEvent:

    def test_creates_audit_log_with_required_fields(self, athlete_user):
        #Verify AuditLog instance creation and automatic snapshot population.
        log = log_event(
            user=athlete_user,
            action='source_connect',
            platform='strava',
            extra_info={'state': 'ok'},
        )

        assert log.pk is not None
        assert log.user_id == athlete_user.pk
        assert log.action == 'source_connect'
        assert log.platform == 'strava'
        assert log.extra_info == {'state': 'ok'}
        assert log.user_id_snapshot == athlete_user.pk
        assert log.user_email_snapshot == athlete_user.email

    def test_extracts_ip_from_x_forwarded_for(self, athlete_user):
        #Verify originating client IP is parsed from X-Forwarded-For chain.
        rf = APIRequestFactory()
        request = rf.post('/dummy/', HTTP_X_FORWARDED_FOR='203.0.113.10, 10.0.0.1')

        log = log_event(
            user=athlete_user,
            action='source_connect',
            request=request,
        )

        assert log.ip_address == '203.0.113.10'

    def test_falls_back_to_remote_addr_when_no_proxy_header(self, athlete_user):
        #Verify fallback to REMOTE_ADDR when proxy headers are missing.
        rf = APIRequestFactory()
        request = rf.post('/dummy/', REMOTE_ADDR='198.51.100.42')

        log = log_event(
            user=athlete_user,
            action='source_connect',
            request=request,
        )

        assert log.ip_address == '198.51.100.42'

    def test_extracts_user_agent_from_header(self, athlete_user):
        #Verify User-Agent extraction from the request metadata.
        rf = APIRequestFactory()
        request = rf.post(
            '/dummy/',
            HTTP_USER_AGENT='Mozilla/5.0 (Aurora Test)',
        )

        log = log_event(
            user=athlete_user,
            action='source_connect',
            request=request,
        )

        assert log.user_agent == 'Mozilla/5.0 (Aurora Test)'

    def test_explicit_ip_overrides_request_extraction(self, athlete_user):
        #Verify explicit ip_address argument overrides automatic request parsing.
        rf = APIRequestFactory()
        request = rf.post('/dummy/', REMOTE_ADDR='10.0.0.1')

        log = log_event(
            user=athlete_user,
            action='source_connect',
            request=request,
            ip_address='203.0.113.99',
        )

        assert log.ip_address == '203.0.113.99'

    def test_strips_forbidden_keys_from_extra_info(self, athlete_user):
        #Verify recursive removal of sensitive keys from extra_info payload.
        log = log_event(
            user=athlete_user,
            action='sync_success',
            extra_info={
                'status': 'completed',
                'access_token': 'should-never-land-here',
                'nested': {
                    'count': 5,
                    'password': 'also-stripped',
                },
            },
        )

        assert 'access_token' not in log.extra_info
        assert 'password' not in log.extra_info['nested']
        assert log.extra_info['status'] == 'completed'
        assert log.extra_info['nested']['count'] == 5

    def test_user_id_snapshot_path_when_user_is_gone(self, db):
        #Verify logging via explicit user_id_snapshot when user row is deleted.
        log = log_event(
            user_id_snapshot=42,
            action='account_deletion_completed',
            extra_info={'status': 'user_row_missing_payloads_drained'},
        )

        assert log.user_id is None
        assert log.user_id_snapshot == 42
        assert log.user_email_snapshot == ''
from __future__ import annotations
import pytest
from django.contrib.auth.models import AnonymousUser
from workouts.permissions import (
    can_view_athlete_data,
    can_modify_athlete_data,
    can_view_object,
    can_modify_object,
)


@pytest.mark.django_db
class TestCanViewAthleteData:

    def test_user_can_view_own_data(self, athlete_user):
        # Athlete can always view their own metrics and profile
        assert can_view_athlete_data(athlete_user, athlete_user) is True

    def test_user_cannot_view_other_users_data(self, user_factory):
        # Prevent IDOR: regular user cannot view another user's data
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')
        assert can_view_athlete_data(user_a, user_b) is False

    def test_staff_can_view_any_user_data(self, user_factory):
        # Support/admin staff can view any athlete's data for debugging
        staff = user_factory(email='support@aurora.test')
        staff.is_staff = True
        staff.save(update_fields=['is_staff'])
        
        regular_athlete = user_factory(email='athlete@aurora.test')
        assert can_view_athlete_data(staff, regular_athlete) is True

    def test_anonymous_viewer_returns_false(self, athlete_user):
        # Unauthenticated users (AnonymousUser) must be rejected
        assert can_view_athlete_data(AnonymousUser(), athlete_user) is False

    def test_none_viewer_returns_false(self, athlete_user):
        # Defensive check: if viewer is None, deny access
        assert can_view_athlete_data(None, athlete_user) is False

    def test_none_athlete_returns_false(self, athlete_user):
        # Defensive check: if target athlete is None, deny access
        assert can_view_athlete_data(athlete_user, None) is False


@pytest.mark.django_db
class TestCanModifyAthleteData:

    def test_user_can_modify_own_data(self, athlete_user):
        assert can_modify_athlete_data(athlete_user, athlete_user) is True

    def test_user_cannot_modify_other_users_data(self, user_factory):
        # Block malicious writes: user cannot change another user's data
        user_a = user_factory(email='auser@aurora.test')
        user_b = user_factory(email='buser@aurora.test')
        assert can_modify_athlete_data(user_a, user_b) is False

    def test_staff_cannot_modify_other_user_data(self, user_factory):
        # Strict rules: staff can VIEW but CANNOT MODIFY athlete data via API
        staff = user_factory(email='support@aurora.test')
        staff.is_staff = True
        staff.save(update_fields=['is_staff'])
        
        regular_athlete = user_factory(email='athlete@aurora.test')
        assert can_modify_athlete_data(staff, regular_athlete) is False

    def test_anonymous_viewer_returns_false(self, athlete_user):
        # Anonymous requests are blocked from making changes
        assert can_modify_athlete_data(AnonymousUser(), athlete_user) is False


@pytest.mark.django_db
class TestObjectDispatch:

    def test_can_view_object_uses_obj_user_as_owner(self, athlete_user, user_factory):
        # Wrapper must automatically extract the .user attribute from the object
        own_profile = athlete_user.profile
        assert can_view_object(athlete_user, own_profile) is True
        
        other_user = user_factory(email='other@aurora.test')
        assert can_view_object(other_user, own_profile) is False

    def test_can_modify_object_uses_obj_user_as_owner(self, athlete_user, user_factory):
        # Wrapper extracts .user attribute for modification access checks
        own_profile = athlete_user.profile
        assert can_modify_object(athlete_user, own_profile) is True
        
        other_user = user_factory(email='other@aurora.test')
        assert can_modify_object(other_user, own_profile) is False

    def test_obj_without_user_attribute_returns_false(self, athlete_user):
        # Fail-Closed Pattern: if object has no .user attribute, deny access instead of crashing
        class InvalidObject:
            pass
            
        assert can_view_object(athlete_user, InvalidObject()) is False
        assert can_modify_object(athlete_user, InvalidObject()) is False
from __future__ import annotations
import pytest
from users.models import User

@pytest.mark.django_db
class TestUserStrMasking:

    def test_long_local_part_masked_first_two_chars(self, user_factory):
        user = user_factory(email='cristiano@aurora.test')
        result = str(user)
        assert 'cr***@aurora.test' in result 
        assert 'cristiano' not in result, (
            'Full local part must never appear in str(user).'
        )

    def test_short_local_part_fully_masked(self, user_factory):
        user = user_factory(email='ab@aurora.test')
        result = str(user)
        assert '**@aurora.test' in result
        assert 'ab' not in result 

    def test_single_char_local_part_fully_masked(self, user_factory):
        user = user_factory(email='a@aurora.test')
        result = str(user)
        assert '*@aurora.test' in result

    def test_user_id_prefix_always_prestnt(self, user_factory):
        user = user_factory(email='notimportant@aurora.test')
        result = str(user)
        assert f"User #{user.pk}" in result

    def test_empty_email_falls_back_to_user_id_only(self, user_factory):
        # Defensive branch: stored email is empty for any reason.
        user = user_factory(email='valid@aurora.test')
        user.email = '' # Bypass create_user normalization, test branch directly
        result = str(user)
        assert result == f"User #{user.pk}"
        assert '@' not in result, "No @ should appear when email is empty." 

    def test_email_without_at_falls_back_to_user_id_only(self, user_factory):
        # Defensive branch: stored value missing @ for any reason.
        user = user_factory(email='valid@aurora.test')
        user.email = 'malformed-no-at-sign'
        result = str(user)
        assert result == f"User #{user.pk}"
        assert 'malformed' not in result, \
            "Malformed local part must not leak through."

    def test_domain_preserved_for_environment_context(self, user_factory):
        # Keeping the domain is intentional: 'gmail.com' vs '@aurora.corp' vs
        # '@example.test' tells you something about the env at a glance.
        user = user_factory(email='someone@gmail.com')
        result = str(user)
        assert '@gmail.com' in result

        
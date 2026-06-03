"""Smoke-test that pytest + Django + DB integration works end-to-end."""
import pytest


@pytest.mark.django_db
def test_django_orm_works():
    """Sanity check: pytest can hit the Django ORM and DB."""
    from users.models import User
    assert User.objects.count() == 0


def test_pytest_runs():
    """Sanity check: pytest itself runs even without DB."""
    assert 1 + 1 == 2
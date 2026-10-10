from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


def admin_client():
    """Explicit identity for existing authorized-business regression tests."""
    user, _ = get_user_model().objects.get_or_create(
        username='business-test-admin', defaults={'is_staff': True, 'is_active': True},
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client

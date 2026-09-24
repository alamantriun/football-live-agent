import pytest
from pydantic import ValidationError

from football_live.settings import Settings


def test_production_requires_server_secrets():
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_secrets_are_not_rendered():
    settings = Settings(
        environment="test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="server-secret",
        cron_secret="cron-secret",
    )
    assert "server-secret" not in repr(settings)
    assert "cron-secret" not in repr(settings)

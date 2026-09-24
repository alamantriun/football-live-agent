import pytest
from pydantic import ValidationError

from football_live.settings import Settings


def test_production_requires_server_secrets():
    with pytest.raises(ValidationError):
        Settings(environment="production")


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("supabase_service_role_key", ""),
        ("supabase_service_role_key", " \t "),
        ("cron_secret", ""),
        ("cron_secret", " \t "),
    ],
)
def test_production_rejects_blank_server_secrets(field_name, invalid_value):
    values = {
        "environment": "production",
        "supabase_url": "https://example.supabase.co",
        "supabase_service_role_key": "server-secret",
        "cron_secret": "cron-secret",
    }
    values[field_name] = invalid_value

    with pytest.raises(ValidationError):
        Settings(**values)


def test_secrets_are_not_rendered():
    settings = Settings(
        environment="test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="server-secret",
        cron_secret="cron-secret",
    )
    assert "server-secret" not in repr(settings)
    assert "cron-secret" not in repr(settings)

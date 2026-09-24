from functools import lru_cache
from typing import Literal

from pydantic import HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    supabase_url: HttpUrl | None = None
    supabase_service_role_key: SecretStr | None = None
    cron_secret: SecretStr | None = None
    allowed_hosts: str = "localhost,127.0.0.1"
    public_origin: str = "http://127.0.0.1:8765"
    provider_timeout_seconds: float = 12.0

    @model_validator(mode="after")
    def require_production_secrets(self):
        if self.environment == "production":
            required = (
                self.supabase_url,
                self.supabase_service_role_key,
                self.cron_secret,
            )
            if any(value is None for value in required):
                raise ValueError("Faltan secretos requeridos del servidor")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

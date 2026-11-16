from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ShopHub API"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./shophub.db"
    jwt_secret_key: SecretStr | None = None
    access_token_expire_minutes: int = Field(default=30, gt=0, le=1440)
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    bootstrap_admin_email: EmailStr | None = None
    bootstrap_admin_name: str = "ShopHub Admin"
    bootstrap_admin_password: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, gt=0, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_sender: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def require_production_secret(self) -> "Settings":
        if self.environment == "production" and self.jwt_secret_key is None:
            raise ValueError("JWT_SECRET_KEY must be configured in production")
        if (
            self.jwt_secret_key is not None
            and len(self.jwt_secret_key.get_secret_value()) < 32
        ):
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        if (self.bootstrap_admin_email is None) != (
            self.bootstrap_admin_password is None
        ):
            raise ValueError(
                "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD must be set together"
            )
        if (
            self.bootstrap_admin_password is not None
            and len(self.bootstrap_admin_password.get_secret_value()) < 12
        ):
            raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

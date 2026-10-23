from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration supporting local and AWS modes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    USE_AWS: bool = False
    AWS_REGION: str = "us-east-1"
    DYNAMODB_TABLE: str = "AmazonContinuityState"
    BEDROCK_MODEL_ID: str = "us.amazon.nova-micro-v1:0"
    HUB_API_KEY: str = ""
    DEFAULT_USER_ID: str = "default-user"
    CORS_ORIGINS: list[str] = ["*"]
    LOG_LEVEL: str = "INFO"
    STATE_TTL_DAYS: int = 7


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()

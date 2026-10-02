from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import List

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemma-4-31b-it"
    ADMIN_USER_IDS: List[int] = Field(default_factory=list)
    DATABASE_URL: str
    WEBAPP_URL: str
    FORCE_IPV6: bool = False
    AI_REQUESTS_PER_MINUTE: int = Field(default=15, gt=0)
    AI_REQUESTS_PER_DAY: int = Field(default=1500, gt=0)
    AI_USER_REQUESTS_PER_MINUTE: int = Field(default=5, gt=0)
    AI_QUEUE_MAX_RETRIES: int = Field(default=8, ge=1, le=20)

    @field_validator("ADMIN_USER_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

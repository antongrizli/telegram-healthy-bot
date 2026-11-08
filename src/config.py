import json
from typing import Annotated, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    AI_PROVIDER: str = "gemini"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemma-4-31b-it"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    OPENROUTER_FALLBACK_MODEL: str = ""
    OPENROUTER_REQUEST_TIMEOUT: float = Field(default=90.0, gt=0)
    OPENROUTER_MAX_TOKENS: int = Field(default=8192, ge=256)
    OPENROUTER_REASONING_TOKENS: int = Field(default=1024, ge=0)
    # NoDecode: pydantic-settings would otherwise json.loads this complex field before our validator runs, so
    # "111,222" and "" would fail at import time. The validator below accepts all of: "", "1", "1,2", "[1,2]".
    ADMIN_USER_IDS: Annotated[List[int], NoDecode] = Field(default_factory=list)
    DATABASE_URL: str
    WEBAPP_URL: str
    FORCE_IPV6: bool = False
    WORKOUT_HISTORY_WEEKS: int = Field(default=6, ge=4, le=6)
    AI_COUNT_TOKENS: bool = False
    AI_REQUESTS_PER_MINUTE: int = Field(default=15, gt=0)
    AI_REQUESTS_PER_DAY: int = Field(default=1500, gt=0)
    AI_USER_REQUESTS_PER_MINUTE: int = Field(default=5, gt=0)
    AI_USER_REQUESTS_PER_DAY: int = Field(default=100, gt=0)
    AI_USER_MAX_PENDING_QUEUE: int = Field(default=5, gt=0)
    AI_QUEUE_MAX_RETRIES: int = Field(default=8, ge=1, le=20)

    @field_validator("ADMIN_USER_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return []
            if raw.startswith("["):  # JSON list form, supported before NoDecode
                return [int(x) for x in json.loads(raw)]
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

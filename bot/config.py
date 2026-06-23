from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    TELEGRAM_BOT_TOKEN: str = Field(...)
    ADMIN_IDS: list[int] = Field(default_factory=list)
    DB_PATH: str = Field(default='data/shopbot.db')
    LANGUAGE: str = Field(default='ru')
    YUKASSA_SHOP_ID: str = Field(default='')
    YUKASSA_SECRET_KEY: str = Field(default='')

    @field_validator('ADMIN_IDS', mode='before')
    @classmethod
    def parse_admin_ids(cls, value: Any) -> list[int]:
        if value is None or value == '':
            return []
        if isinstance(value, list):
            return [int(v) for v in value]
        if isinstance(value, (int, float)):
            return [int(value)]
        if isinstance(value, str):
            return [int(p.strip()) for p in value.split(',') if p.strip()]
        raise ValueError('ADMIN_IDS invalid format')

    @field_validator('LANGUAGE', mode='before')
    @classmethod
    def parse_language(cls, value: Any) -> str:
        lang = str(value or 'ru').strip().lower()
        return lang if lang in {'ru', 'en'} else 'ru'


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

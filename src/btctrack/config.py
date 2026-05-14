from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Currency = Literal["CHF", "EUR", "USD"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    electrum_host: str = "127.0.0.1"
    electrum_port: int = 50002
    electrum_use_ssl: bool = True

    base_currency: Currency = "CHF"
    gap_limit: int = 20

    btctrack_db_path: Path = Field(default=Path("data/btctrack.db"))

    @property
    def db_url(self) -> str:
        path = Path(self.btctrack_db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()

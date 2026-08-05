"""Process configuration sourced from the environment.

Everything the add-on's ``run`` script exports is read here.  Nothing in this
module reads the database: settings that a user can change at runtime live in
``app.services.settings_service`` instead.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["trace", "debug", "info", "warning", "error"]


class AppConfig(BaseSettings):
    """Immutable, process-level configuration."""

    model_config = SettingsConfigDict(env_prefix="FX_", extra="ignore")

    app_version: str = "1.3.1"
    build_arch: str = "unknown"
    log_level: LogLevel = "info"

    #: Persistent storage. Home Assistant backs this directory up.
    data_dir: Path = Path("/data")
    #: User-writable directory mapped from the Home Assistant app config share.
    app_config_dir: Path = Path("/config")
    #: Compiled frontend bundle.
    static_dir: Path = Path("/app/frontend")

    #: Ingress entry point reported by the Supervisor, e.g. ``/api/hassio_ingress/abc``.
    #: Only used for logging: the frontend derives its own base path at runtime.
    ingress_entry: str = ""

    simulation_mode: bool = False

    supervisor_token: str = Field(default="", alias="SUPERVISOR_TOKEN")
    supervisor_api: str = "http://supervisor"
    home_assistant_api: str = "http://supervisor/core/api"

    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""

    #: Set by the test suite to keep everything in a temporary directory.
    testing: bool = False

    @field_validator("data_dir", "app_config_dir", "static_dir", mode="before")
    @classmethod
    def _expand(cls, value: str | Path) -> Path:
        return Path(str(value)).expanduser()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "fx_strategy.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"

    @property
    def secrets_path(self) -> Path:
        return self.data_dir / "secrets.json"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def mqtt_configured(self) -> bool:
        return bool(self.mqtt_host)

    @property
    def supervisor_available(self) -> bool:
        return bool(self.supervisor_token)

    @property
    def python_log_level(self) -> str:
        # `trace` is a bashio log level with no Python equivalent.
        return "DEBUG" if self.log_level == "trace" else self.log_level.upper()


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return the process configuration (cached)."""
    # SUPERVISOR_TOKEN has no FX_ prefix, so it is passed explicitly.
    return AppConfig(SUPERVISOR_TOKEN=os.environ.get("SUPERVISOR_TOKEN", ""))


def reset_config_cache() -> None:
    """Drop the cached config. Used by tests after mutating the environment."""
    get_config.cache_clear()

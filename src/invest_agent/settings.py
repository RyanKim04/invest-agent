"""Settings read from the environment."""

import os
from pathlib import Path


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


def database_url() -> str:
    """App connection. On Neon use the direct URL: advisory locks need a session, not the pooler."""
    return _required("INVEST_DATABASE_URL")


def migrate_database_url() -> str:
    """Owner connection for migrations. Falls back to the app URL for local development."""
    return os.environ.get("INVEST_MIGRATE_DATABASE_URL") or database_url()


def config_dir() -> Path:
    return Path(os.environ.get("INVEST_CONFIG_DIR", "config"))

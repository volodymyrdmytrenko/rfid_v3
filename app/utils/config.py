from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
from app.utils.paths import ENV_FILE, ensure_runtime_dirs


ensure_runtime_dirs()

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


class ConfigError(RuntimeError):
    pass


def _get_str(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise ConfigError(f"Missing required environment variable: {name}")
    return "" if value is None else str(value).strip()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer") from exc


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = _get_str("APP_ENV", "development")
TEST_MODE = _get_bool("TEST_MODE", False)

RFID_PORT = _get_str("RFID_PORT", "COM20")
RFID_BAUDRATE = _get_int("RFID_BAUDRATE", 9600)

MYSQL_HOST = _get_str("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = _get_int("MYSQL_PORT", 3306)
MYSQL_USER = _get_str("MYSQL_USER", required=True)
MYSQL_PASSWORD = _get_str("MYSQL_PASSWORD", required=True)
MYSQL_DB = _get_str("MYSQL_DB", required=True)

SYNC_FULL_INTERVAL = _get_int("SYNC_FULL_INTERVAL", 1200)
SYNC_VISITS_INTERVAL = _get_int("SYNC_VISITS_INTERVAL", 300)
SYNC_BATCH_SIZE = _get_int("SYNC_BATCH_SIZE", 200)

DEBOUNCE_MS = _get_int("DEBOUNCE_MS", 5000)
DAYS_SAVE = _get_int("DAYS_SAVE", 60)

ENABLE_STOPNET_SYNC = _get_bool("ENABLE_STOPNET_SYNC", False)

MSSQL_SERVER = _get_str("MSSQL_SERVER", "")
MSSQL_DATABASE = _get_str("MSSQL_DATABASE", "")
MSSQL_USERNAME = _get_str("MSSQL_USERNAME", "")
MSSQL_PASSWORD = _get_str("MSSQL_PASSWORD", "")
MSSQL_DRIVER = _get_str("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")

STOPNET_DEFAULT_FMONEY = _get_int("STOPNET_DEFAULT_FMONEY", 50)


def validate_config() -> None:
    if ENABLE_STOPNET_SYNC:
        required = {
            "MSSQL_SERVER": MSSQL_SERVER,
            "MSSQL_DATABASE": MSSQL_DATABASE,
            "MSSQL_USERNAME": MSSQL_USERNAME,
            "MSSQL_PASSWORD": MSSQL_PASSWORD,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ConfigError(
                "ENABLE_STOPNET_SYNC=true, but missing variables: " + ", ".join(missing)
            )
import os
from dotenv import load_dotenv

from app.utils.paths import ENV_FILE, ensure_runtime_dirs


def ensure_env_file():
    if not ENV_FILE.exists():
        ENV_FILE.write_text(
            "RFID_PORT=COM20\n"
            "TEST_MODE=false\n"
            "MYSQL_HOST=10.0.0.217\n"
            "MYSQL_USER=canteen\n"
            "MYSQL_PASSWORD=GNgfvPeRNX0c5n\n"
            "MYSQL_DB=canteen\n"
            "SYNC_FULL_INTERVAL=1200\n"
            "SYNC_VISITS_INTERVAL=300\n"
            "DEBOUNCE_MS=5000\n"
            "DAYS_SAVE=60\n"
            ,
            encoding="utf-8"
        )


ensure_runtime_dirs()
ensure_env_file()
load_dotenv(ENV_FILE)

RFID_PORT = os.getenv("RFID_PORT", "COM20")
TEST_MODE = os.getenv("TEST_MODE", "false").strip().lower() == "true"

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_USER = os.getenv("MYSQL_USER", "canteen")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "GNgfvPeRNX0c5n")
MYSQL_DB = os.getenv("MYSQL_DB", "canteen")

SYNC_FULL_INTERVAL = int(os.getenv("SYNC_FULL_INTERVAL", "1200"))
SYNC_VISITS_INTERVAL = int(os.getenv("SYNC_VISITS_INTERVAL", "300"))
DEBOUNCE_MS = int(os.getenv("DEBOUNCE_MS", "5000"))
DAYS_SAVE = int(os.getenv("DAYS_SAVE", "60"))


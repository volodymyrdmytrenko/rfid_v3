from pathlib import Path
import sys


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


APP_DIR = get_app_dir()
LOGS_DIR = APP_DIR / "logs"
BACKUP_DIR = APP_DIR / "backup"
ENV_FILE = APP_DIR / ".env"
DB_FILE = APP_DIR / "local.db"
ICON_FILE = APP_DIR / "favicon.ico"


def ensure_runtime_dirs():
    LOGS_DIR.mkdir(exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)
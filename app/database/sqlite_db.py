import os
import sys
import sqlite3
from pathlib import Path

from app.utils.paths import DB_FILE


def resource_path(relative_path: str):
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base_path = Path(__file__).resolve().parents[2]
    return os.path.join(str(base_path), relative_path)


schema_file = resource_path("app/database/sqlite_schema.sql")
DB_PATH = DB_FILE


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_sqlite():
    with open(schema_file, "r", encoding="utf-8") as f:
        schema = f.read()

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.executescript(schema)
        conn.commit()
    finally:
        conn.close()
from datetime import datetime
import threading
import time

from app.database.mysql_db import get_mysql_connection
from app.database.sqlite_db import get_connection
from app.utils.config import SYNC_FULL_INTERVAL
from app.utils.logger import get_logger

logger = get_logger("DirectorySync")


def normalize_name(s: str) -> str:
    return " ".join((s or "").strip().split()).casefold()


def ensure_employees_schema(sqlite_conn):
    scur = sqlite_conn.cursor()

    scur.execute("PRAGMA table_info(employees)")
    cols = [r[1] for r in scur.fetchall()]

    if "full_name_norm" not in cols:
        scur.execute("ALTER TABLE employees ADD COLUMN full_name_norm TEXT")

    scur.execute(
        "CREATE INDEX IF NOT EXISTS ix_employees_full_name_norm "
        "ON employees(full_name_norm)"
    )


def sync_employees():
    """Повна синхронізація employees з MySQL → SQLite."""
    logger.info("Syncing employees...")

    mysql_conn = None
    sqlite_conn = None

    try:
        mysql_conn = get_mysql_connection()
        sqlite_conn = get_connection()

        mcur = mysql_conn.cursor(dictionary=True)
        scur = sqlite_conn.cursor()

        mcur.execute("""
            SELECT id, rfid, full_name, fmoney,active, updated_at
            FROM employees
        """)
        rows = mcur.fetchall()

        ensure_employees_schema(sqlite_conn)

        prepared_rows = []
        skipped = 0

        for row in rows:
            rfid = (row.get("rfid") or "").strip()
            if not rfid:
                skipped += 1
                continue

            full_name = (row.get("full_name") or "").strip()
            prepared_rows.append((
                int(row["id"]),
                rfid,
                full_name,
                normalize_name(full_name),
                int(row.get("fmoney", 50) or 50),
                int(row.get("active", 1) or 0),
                str(row.get("updated_at") or datetime.now().isoformat())
            ))

        scur.execute("DELETE FROM employees")

        scur.executemany("""
            INSERT INTO employees (
                id, rfid, full_name, full_name_norm, fmoney, active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, prepared_rows)

        sqlite_conn.commit()

        logger.info(
            "Employees synced: %s, skipped without RFID: %s",
            len(prepared_rows),
            skipped
        )

    finally:
        if mysql_conn:
            mysql_conn.close()
        if sqlite_conn:
            sqlite_conn.close()


def full_sync():
    """Повна синхронізація всіх довідників."""
    try:
        sync_employees()
        logger.info("Directory sync completed successfully.")
    except Exception:
        logger.exception("Directory sync failed")


def _sync_loop():
    while True:
        full_sync()
        time.sleep(SYNC_FULL_INTERVAL)


def start_periodic_sync():
    """Запускає періодичну синхронізацію у фоні."""
    thread = threading.Thread(
        target=_sync_loop,
        name="directory-sync",
        daemon=True
    )
    thread.start()
    logger.info("Started periodic directory sync thread.")
from __future__ import annotations

import threading
import time
from datetime import datetime

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

    if "fmoney" not in cols:
        scur.execute("ALTER TABLE employees ADD COLUMN fmoney INTEGER DEFAULT 50")

    scur.execute(
        "CREATE INDEX IF NOT EXISTS ix_employees_full_name_norm "
        "ON employees(full_name_norm)"
    )


def sync_employees():
    logger.info("Syncing employees...")

    mysql_conn = None
    sqlite_conn = None

    try:
        mysql_conn = get_mysql_connection()
        sqlite_conn = get_connection()

        mcur = mysql_conn.cursor(dictionary=True)
        scur = sqlite_conn.cursor()

        mcur.execute(
            """
            SELECT id, rfid, full_name, COALESCE(fmoney, 50) AS fmoney, active, updated_at
            FROM employees
            """
        )
        rows = mcur.fetchall()

        ensure_employees_schema(sqlite_conn)

        prepared_rows = []
        active_ids = set()
        skipped = 0

        for row in rows:
            rfid = (row.get("rfid") or "").strip()
            if not rfid:
                skipped += 1
                continue

            full_name = (row.get("full_name") or "").strip()
            emp_id = int(row["id"])
            active_ids.add(emp_id)

            prepared_rows.append(
                (
                    emp_id,
                    rfid,
                    full_name,
                    normalize_name(full_name),
                    int(row.get("fmoney", 50) or 50),
                    int(row.get("active", 1) or 0),
                    str(row.get("updated_at") or datetime.now().isoformat(timespec="seconds")),
                )
            )

        scur.execute("BEGIN")

        scur.executemany(
            """
            INSERT INTO employees (
                id, rfid, full_name, full_name_norm, fmoney, active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                rfid = excluded.rfid,
                full_name = excluded.full_name,
                full_name_norm = excluded.full_name_norm,
                fmoney = excluded.fmoney,
                active = excluded.active,
                updated_at = excluded.updated_at
            """,
            prepared_rows,
        )

        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            scur.execute(
                f"UPDATE employees SET active = 0 WHERE id NOT IN ({placeholders})",
                tuple(active_ids),
            )
        else:
            scur.execute("UPDATE employees SET active = 0")

        sqlite_conn.commit()

        logger.info(
            "Employees synced: %s, skipped without RFID: %s",
            len(prepared_rows),
            skipped,
        )

    except Exception:
        if sqlite_conn:
            sqlite_conn.rollback()
        logger.exception("sync_employees failed")
        raise

    finally:
        if mysql_conn:
            mysql_conn.close()
        if sqlite_conn:
            sqlite_conn.close()


def full_sync():
    sync_employees()
    logger.info("Directory sync completed successfully.")


def _sync_loop():
    while True:
        try:
            full_sync()
        except Exception:
            logger.exception("Directory sync loop failed")
        time.sleep(SYNC_FULL_INTERVAL)


def start_periodic_sync():
    thread = threading.Thread(
        target=_sync_loop,
        name="directory-sync",
        daemon=True,
    )
    thread.start()
    logger.info("Started periodic directory sync thread.")
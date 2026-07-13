from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime

from app.database.mysql_db import get_mysql_connection
from app.database.sqlite_db import get_connection
from app.utils.config import SYNC_FULL_INTERVAL
from app.utils.logger import get_logger

logger = get_logger("DirectorySync")


LEGACY_RFID_PREFIX = "legacy:"


def normalize_name(s: str) -> str:
    return " ".join((s or "").strip().split()).casefold()


def make_legacy_rfid(emp_id: int, old_rfid: str) -> str:
    """
    Generates a technical RFID value for an old/local employee record.

    This is needed when a physical RFID card is re-issued to another employee.
    Visits remain linked by employee_id, so only the old employee RFID is moved
    away from the real card number.
    """
    safe_old_rfid = (old_rfid or "").strip()
    suffix = uuid.uuid4().hex[:12]
    return f"{LEGACY_RFID_PREFIX}{emp_id}:{safe_old_rfid}:{suffix}"[:128]


def make_unique_legacy_rfid(sqlite_cursor, emp_id: int, old_rfid: str, reserved_rfids: set[str] | None = None) -> str:
    """
    Generates a legacy RFID that does not already exist in local SQLite and is
    not reserved by the incoming MySQL payload.
    """
    reserved_rfids = reserved_rfids or set()

    while True:
        legacy_rfid = make_legacy_rfid(emp_id, old_rfid)
        if legacy_rfid in reserved_rfids:
            continue

        sqlite_cursor.execute(
            "SELECT id FROM employees WHERE rfid = ? LIMIT 1",
            (legacy_rfid,),
        )
        if sqlite_cursor.fetchone() is None:
            return legacy_rfid


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


def _move_local_rfid_owner_to_legacy(sqlite_cursor, emp_id: int, rfid: str, full_name: str, reserved_rfids: set[str]) -> None:
    """
    Frees RFID in local SQLite before upserting the incoming employee.

    Scenario:
        local SQLite: old employee id=101 has RFID=555
        MySQL:        new employee id=202 now has RFID=555

    Without this step SQLite raises:
        UNIQUE constraint failed: employees.rfid

    We keep the old employee row for historical visits, but replace its RFID
    with a technical legacy value and mark it inactive.
    """
    sqlite_cursor.execute(
        """
        SELECT id, rfid, full_name
        FROM employees
        WHERE rfid = ? AND id <> ?
        LIMIT 1
        """,
        (rfid, emp_id),
    )
    existing_by_rfid = sqlite_cursor.fetchone()

    if existing_by_rfid is None:
        return

    old_emp_id = int(existing_by_rfid["id"])
    old_rfid = str(existing_by_rfid["rfid"] or "").strip()
    old_full_name = str(existing_by_rfid["full_name"] or "").strip()
    legacy_rfid = make_unique_legacy_rfid(
        sqlite_cursor,
        old_emp_id,
        old_rfid,
        reserved_rfids=reserved_rfids,
    )

    logger.warning(
        "Local RFID conflict detected during directory sync. "
        "Incoming employee id=%s (%s) wants RFID=%s, but local employee id=%s (%s) already has it. "
        "Old local employee will be moved to legacy RFID=%s",
        emp_id,
        full_name,
        rfid,
        old_emp_id,
        old_full_name,
        legacy_rfid,
    )

    sqlite_cursor.execute(
        """
        UPDATE employees
        SET rfid = ?, active = 0, updated_at = ?
        WHERE id = ?
        """,
        (
            legacy_rfid,
            datetime.now().isoformat(timespec="seconds"),
            old_emp_id,
        ),
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
        incoming_rfid_to_emp_id: dict[str, int] = {}
        reserved_rfids: set[str] = set()
        skipped = 0
        skipped_duplicate_payload = 0

        for row in rows:
            rfid = (row.get("rfid") or "").strip()
            if not rfid:
                skipped += 1
                continue

            full_name = (row.get("full_name") or "").strip()
            emp_id = int(row["id"])

            # Extra protection: if the MySQL payload somehow contains the same
            # RFID for two different employee IDs, keep the first row and skip
            # the next one instead of crashing local SQLite sync.
            existing_emp_id_for_rfid = incoming_rfid_to_emp_id.get(rfid)
            if existing_emp_id_for_rfid is not None and existing_emp_id_for_rfid != emp_id:
                skipped_duplicate_payload += 1
                logger.error(
                    "Duplicate RFID in MySQL employees payload. RFID=%s belongs to incoming employee ids %s and %s. "
                    "Employee id=%s (%s) was skipped. Fix this conflict in MySQL/MSSQL.",
                    rfid,
                    existing_emp_id_for_rfid,
                    emp_id,
                    emp_id,
                    full_name,
                )
                continue

            incoming_rfid_to_emp_id[rfid] = emp_id
            reserved_rfids.add(rfid)
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

        upsert_sql = """
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
            """

        local_conflicts_resolved = 0

        for prepared_row in prepared_rows:
            emp_id, rfid, full_name = prepared_row[0], prepared_row[1], prepared_row[2]

            # This is the critical fix for re-issued RFID cards.
            # If local SQLite still has this RFID on another employee, move that
            # old local employee to a technical legacy RFID before the UPSERT.
            scur.execute(
                "SELECT id FROM employees WHERE rfid = ? AND id <> ? LIMIT 1",
                (rfid, emp_id),
            )
            conflict_exists = scur.fetchone() is not None

            if conflict_exists:
                _move_local_rfid_owner_to_legacy(
                    scur,
                    emp_id=emp_id,
                    rfid=rfid,
                    full_name=full_name,
                    reserved_rfids=reserved_rfids,
                )
                local_conflicts_resolved += 1

            scur.execute(upsert_sql, prepared_row)

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
            "Employees synced: %s, skipped without RFID: %s, skipped duplicate payload: %s, local RFID conflicts resolved: %s",
            len(prepared_rows),
            skipped,
            skipped_duplicate_payload,
            local_conflicts_resolved,
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

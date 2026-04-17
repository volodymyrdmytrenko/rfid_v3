from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.database.mysql_db import get_mysql_connection
from app.database.sqlite_db import get_connection
from app.services.state import AppState
from app.utils.config import SYNC_BATCH_SIZE, SYNC_VISITS_INTERVAL
from app.utils.logger import get_logger

logger = get_logger("SyncService")


def _recent_window_start() -> str:
    """
    Returns the inclusive start of the local retention window:
    current month + previous month.
    Example:
      now = 2026-04-14 -> returns '2026-03-01 00:00:00'
    """
    now = datetime.now()
    year = now.year
    month = now.month

    # Previous month relative to current month
    month -= 1
    if month == 0:
        month = 12
        year -= 1

    return f"{year:04d}-{month:02d}-01 00:00:00"


class SyncService:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.unsynced_count = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="visits-sync",
            daemon=True,
        )
        self._thread.start()
        logger.info("SyncService started.")

    def stop(self):
        self._stop_event.set()
        logger.info("SyncService stop requested.")

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self.sync_visits()
            except Exception:
                logger.exception("Sync loop error")

            self._stop_event.wait(SYNC_VISITS_INTERVAL)

    def ensure_sqlite_visits_schema(self, sqlite_conn):
        cur = sqlite_conn.cursor()

        cur.execute("PRAGMA table_info(visits)")
        cols = [r[1] for r in cur.fetchall()]

        if "sync_uuid" not in cols:
            raise RuntimeError("SQLite visits table must contain sync_uuid column")

        if "synced" not in cols:
            raise RuntimeError("SQLite visits table must contain synced column")

        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_visits_sync_uuid "
            "ON visits(sync_uuid)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_visits_visit_time "
            "ON visits(visit_time)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_visits_synced "
            "ON visits(synced)"
        )

        sqlite_conn.commit()

    def push_unsynced_to_mysql(self, sqlite_conn, mysql_conn) -> int:
        sqlite_cur = sqlite_conn.cursor()
        sqlite_cur.execute(
            """
            SELECT id, employee_id, visit_time, source, sync_uuid
            FROM visits
            WHERE synced = 0
            ORDER BY id
            LIMIT ?
            """,
            (SYNC_BATCH_SIZE,),
        )
        unsynced = sqlite_cur.fetchall()

        self.unsynced_count = len(unsynced)
        AppState.unsynced_count = self.unsynced_count

        if not unsynced:
            logger.info("No unsynced local visits to push.")
            return 0

        logger.info(
            "Preparing to push %s unsynced local visits to MySQL.",
            len(unsynced),
        )

        mysql_cur = mysql_conn.cursor()
        synced_sqlite_ids: list[int] = []

        for row in unsynced:
            mysql_cur.execute(
                """
                INSERT INTO visits (employee_id, visit_time, source, sync_uuid)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    employee_id = VALUES(employee_id),
                    visit_time = VALUES(visit_time),
                    source = VALUES(source)
                """,
                (
                    row["employee_id"],
                    row["visit_time"],
                    row["source"],
                    row["sync_uuid"],
                ),
            )
            synced_sqlite_ids.append(int(row["id"]))

        mysql_conn.commit()

        logger.info(
            "MySQL commit successful for %s pushed visits. Marking SQLite rows as synced.",
            len(synced_sqlite_ids),
        )

        sqlite_cur.executemany(
            "UPDATE visits SET synced = 1 WHERE id = ?",
            [(visit_id,) for visit_id in synced_sqlite_ids],
        )
        sqlite_conn.commit()

        logger.info("Pushed %s local visits to MySQL.", len(synced_sqlite_ids))
        return len(synced_sqlite_ids)

    def pull_recent_from_mysql(self, sqlite_conn, mysql_conn, cutoff_str: str) -> int:
        """
        Mirrors all visits from MySQL to SQLite inside the retention window.
        """
        mysql_cur = mysql_conn.cursor(dictionary=True)
        sqlite_cur = sqlite_conn.cursor()

        mysql_cur.execute(
            """
            SELECT employee_id, visit_time, source, sync_uuid
            FROM visits
            WHERE visit_time >= %s
            ORDER BY visit_time, sync_uuid
            """,
            (cutoff_str,),
        )
        rows = mysql_cur.fetchall()

        if not rows:
            logger.info("No recent visits found in MySQL for local mirror window.")
            return 0

        payload: list[tuple[Any, Any, Any, Any, int]] = []
        for row in rows:
            payload.append(
                (
                    int(row["employee_id"]),
                    row["visit_time"],
                    row["source"],
                    row["sync_uuid"],
                    1,  # pulled from MySQL => already synced by definition
                )
            )

        logger.info(
            "Pulling recent visits from MySQL into SQLite mirror. cutoff=%s rows=%s",
            cutoff_str,
            len(payload),
        )

        sqlite_cur.execute("BEGIN")

        sqlite_cur.executemany(
            """
            INSERT INTO visits (employee_id, visit_time, source, sync_uuid, synced)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sync_uuid) DO UPDATE SET
                employee_id = excluded.employee_id,
                visit_time = excluded.visit_time,
                source = excluded.source,
                synced = 1
            """,
            payload,
        )

        sqlite_conn.commit()

        logger.info(
            "Pulled %s recent visits from MySQL into SQLite mirror.",
            len(payload),
        )
        return len(payload)

    def reconcile_recent_window(self, sqlite_conn, mysql_conn, cutoff_str: str) -> int:
        """
        Removes from SQLite recent-window rows that no longer exist in MySQL.
        This keeps SQLite equal to MySQL for the retained window.
        """
        mysql_cur = mysql_conn.cursor(dictionary=True)
        sqlite_cur = sqlite_conn.cursor()

        mysql_cur.execute(
            """
            SELECT sync_uuid
            FROM visits
            WHERE visit_time >= %s
            """,
            (cutoff_str,),
        )
        mysql_sync_uuids = {
            str(row["sync_uuid"])
            for row in mysql_cur.fetchall()
            if row.get("sync_uuid")
        }

        # IMPORTANT:
        # Reconcile must only touch rows that are already synced locally.
        # Fresh local rows with synced=0 may have been created after the current
        # sync cycle started and may not exist in MySQL yet.
        sqlite_cur.execute(
            """
            SELECT id, employee_id, visit_time, source, sync_uuid, synced
            FROM visits
            WHERE visit_time >= ?
              AND synced = 1
            """,
            (cutoff_str,),
        )
        sqlite_rows = sqlite_cur.fetchall()

        to_delete: list[tuple[int]] = []
        deleted_details: list[str] = []
        for row in sqlite_rows:
            sync_uuid = str(row["sync_uuid"]) if row["sync_uuid"] is not None else ""
            if sync_uuid and sync_uuid not in mysql_sync_uuids:
                to_delete.append((int(row["id"]),))
                deleted_details.append(
                    "id=%s employee_id=%s visit_time=%s source=%s sync_uuid=%s"
                    % (
                        row["id"],
                        row["employee_id"],
                        row["visit_time"],
                        row["source"],
                        sync_uuid,
                    )
                )

        if to_delete:
            logger.warning(
                "Reconcile will delete %s SQLite synced rows missing in MySQL recent window: %s",
                len(to_delete),
                "; ".join(deleted_details[:20]),
            )
            sqlite_cur.executemany(
                "DELETE FROM visits WHERE id = ?",
                to_delete,
            )
            sqlite_conn.commit()
        else:
            logger.info(
                "Reconcile found no SQLite synced rows missing in MySQL recent window."
            )

        logger.info(
            "Reconcile finished. Deleted %s SQLite synced rows missing in MySQL recent window.",
            len(to_delete),
        )
        return len(to_delete)

    def cleanup_old_local_visits(self, sqlite_conn, cutoff_str: str) -> int:
        sqlite_cur = sqlite_conn.cursor()
        sqlite_cur.execute(
            "DELETE FROM visits WHERE visit_time < ?",
            (cutoff_str,),
        )
        deleted = sqlite_cur.rowcount
        sqlite_conn.commit()

        logger.info(
            "Deleted %s old SQLite visits older than %s.",
            deleted,
            cutoff_str,
        )
        return deleted

    def sync_visits(self):
        cutoff_str = _recent_window_start()
        sqlite_conn = get_connection()
        mysql_conn = None

        logger.info("Visits sync started. window_start=%s", cutoff_str)

        try:
            self.ensure_sqlite_visits_schema(sqlite_conn)

            mysql_conn = get_mysql_connection()
            logger.info("MySQL connection acquired for visits sync.")

            pushed = self.push_unsynced_to_mysql(sqlite_conn, mysql_conn)
            pulled = self.pull_recent_from_mysql(sqlite_conn, mysql_conn, cutoff_str)
            reconciled_deleted = self.reconcile_recent_window(sqlite_conn, mysql_conn, cutoff_str)
            old_deleted = self.cleanup_old_local_visits(sqlite_conn, cutoff_str)

            sqlite_cur = sqlite_conn.cursor()
            sqlite_cur.execute("SELECT COUNT(*) AS cnt FROM visits WHERE synced = 0")
            unsynced_after_sync = int(sqlite_cur.fetchone()["cnt"])
            self.unsynced_count = unsynced_after_sync
            AppState.unsynced_count = unsynced_after_sync

            logger.info(
                "Visits sync finished. pushed=%s pulled=%s deleted_missing_recent=%s deleted_old=%s unsynced_after_sync=%s window_start=%s",
                pushed,
                pulled,
                reconciled_deleted,
                old_deleted,
                unsynced_after_sync,
                cutoff_str,
            )

            return {
                "pushed": pushed,
                "pulled": pulled,
                "deleted_missing_recent": reconciled_deleted,
                "deleted_old": old_deleted,
                "window_start": cutoff_str,
            }

        except Exception:
            if mysql_conn:
                try:
                    mysql_conn.rollback()
                except Exception:
                    pass
            logger.exception("Visits sync failed")
            raise

        finally:
            if mysql_conn:
                try:
                    mysql_conn.close()
                except Exception:
                    pass
            sqlite_conn.close()
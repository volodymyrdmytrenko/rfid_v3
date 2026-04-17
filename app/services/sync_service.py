from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from app.database.mysql_db import get_mysql_connection
from app.database.sqlite_db import get_connection
from app.services.state import AppState
from app.utils.config import SYNC_BATCH_SIZE, SYNC_VISITS_INTERVAL
from app.utils.logger import get_logger

logger = get_logger("SyncService")

# Small overlap to protect against equal timestamps, clock skew,
# and writes that happen around the sync boundary.
PULL_OVERLAP_SECONDS = 30
SYNC_STATE_KEY_LAST_PULL = "visits_last_pull_at"


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
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_visits_employee_visit_time "
            "ON visits(employee_id, visit_time)"
        )

        sqlite_conn.commit()
        logger.info("SQLite visits schema verified.")

    def ensure_sqlite_sync_state_schema(self, sqlite_conn):
        cur = sqlite_conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        sqlite_conn.commit()
        logger.info("SQLite sync_state schema verified.")

    def get_sync_state_value(self, sqlite_conn, key: str) -> str | None:
        cur = sqlite_conn.cursor()
        cur.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
        row = cur.fetchone()
        if not row:
            return None

        try:
            return row["value"]
        except Exception:
            return row[0]

    def set_sync_state_value(self, sqlite_conn, key: str, value: str):
        cur = sqlite_conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO sync_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, now_str),
        )
        sqlite_conn.commit()

    def _safe_parse_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value

        text = str(value).strip()
        if not text:
            return None

        formats = (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        )
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _format_datetime(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def _get_pull_since(self, sqlite_conn, cutoff_str: str) -> str:
        last_pull_raw = self.get_sync_state_value(sqlite_conn, SYNC_STATE_KEY_LAST_PULL)
        if not last_pull_raw:
            logger.info(
                "No last pull watermark found. Initializing pull from retention window start=%s.",
                cutoff_str,
            )
            return cutoff_str

        last_pull_dt = self._safe_parse_datetime(last_pull_raw)
        cutoff_dt = self._safe_parse_datetime(cutoff_str)

        if last_pull_dt is None or cutoff_dt is None:
            logger.warning(
                "Invalid last pull watermark '%s'. Falling back to retention window start=%s.",
                last_pull_raw,
                cutoff_str,
            )
            return cutoff_str

        pull_since_dt = last_pull_dt - timedelta(seconds=PULL_OVERLAP_SECONDS)
        if pull_since_dt < cutoff_dt:
            pull_since_dt = cutoff_dt

        pull_since_str = self._format_datetime(pull_since_dt)
        logger.info(
            "Calculated incremental pull watermark. last_pull_at=%s overlap_seconds=%s pull_since=%s.",
            last_pull_raw,
            PULL_OVERLAP_SECONDS,
            pull_since_str,
        )
        return pull_since_str

    def refresh_unsynced_count(self, sqlite_conn) -> int:
        cur = sqlite_conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM visits WHERE synced = 0")
        row = cur.fetchone()
        try:
            count = int(row["cnt"])
        except Exception:
            count = int(row[0])

        self.unsynced_count = count
        AppState.unsynced_count = count
        return count

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
            "Preparing to push %s unsynced local visits to MySQL (batch_size=%s).",
            len(unsynced),
            SYNC_BATCH_SIZE,
        )

        mysql_cur = mysql_conn.cursor()
        synced_sqlite_ids: list[int] = []

        for row in unsynced:
            logger.info(
                "Push candidate: sqlite_id=%s employee_id=%s visit_time=%s source=%s sync_uuid=%s",
                row["id"],
                row["employee_id"],
                row["visit_time"],
                row["source"],
                row["sync_uuid"],
            )
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
        logger.info("MySQL commit completed for %s pushed local visits.", len(synced_sqlite_ids))

        sqlite_cur.executemany(
            "UPDATE visits SET synced = 1 WHERE id = ?",
            [(visit_id,) for visit_id in synced_sqlite_ids],
        )
        sqlite_conn.commit()

        logger.info("Pushed %s local visits to MySQL.", len(synced_sqlite_ids))
        return len(synced_sqlite_ids)

    def pull_recent_from_mysql(self, sqlite_conn, mysql_conn, cutoff_str: str) -> int:
        """
        Pulls only recent changes from MySQL into SQLite.
        Uses a persisted watermark with a small overlap window to reduce traffic
        and avoid missing rows around sync boundaries.
        """
        pull_since_str = self._get_pull_since(sqlite_conn, cutoff_str)

        mysql_cur = mysql_conn.cursor(dictionary=True)
        sqlite_cur = sqlite_conn.cursor()

        logger.info(
            "Starting incremental MySQL->SQLite pull. retention_window_start=%s pull_since=%s.",
            cutoff_str,
            pull_since_str,
        )

        mysql_cur.execute(
            """
            SELECT employee_id, visit_time, source, sync_uuid
            FROM visits
            WHERE visit_time >= %s
            ORDER BY visit_time, sync_uuid
            """,
            (pull_since_str,),
        )
        rows = mysql_cur.fetchall()

        if not rows:
            logger.info(
                "No recent MySQL changes found for incremental pull since %s.",
                pull_since_str,
            )
            return 0

        payload: list[tuple[Any, Any, Any, Any, int]] = []
        max_visit_time: datetime | None = None

        for row in rows:
            visit_time = row["visit_time"]
            payload.append(
                (
                    int(row["employee_id"]),
                    visit_time,
                    row["source"],
                    row["sync_uuid"],
                    1,
                )
            )
            visit_time_dt = self._safe_parse_datetime(visit_time)
            if visit_time_dt and (max_visit_time is None or visit_time_dt > max_visit_time):
                max_visit_time = visit_time_dt

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

        if max_visit_time is not None:
            new_last_pull_at = self._format_datetime(max_visit_time)
            self.set_sync_state_value(sqlite_conn, SYNC_STATE_KEY_LAST_PULL, new_last_pull_at)
            logger.info(
                "Updated incremental pull watermark: %s -> %s.",
                pull_since_str,
                new_last_pull_at,
            )
        else:
            logger.warning(
                "Pulled %s rows from MySQL, but could not compute max visit_time. Watermark unchanged.",
                len(payload),
            )

        logger.info(
            "Pulled %s recent visits from MySQL into SQLite mirror (incremental).",
            len(payload),
        )
        return len(payload)

    def cleanup_old_local_visits(self, sqlite_conn, cutoff_str: str) -> int:
        sqlite_cur = sqlite_conn.cursor()
        sqlite_cur.execute(
            """
            DELETE FROM visits
            WHERE visit_time < ?
              AND synced = 1
            """,
            (cutoff_str,),
        )
        deleted = sqlite_cur.rowcount
        sqlite_conn.commit()

        logger.info(
            "Deleted %s old synced SQLite visits older than %s.",
            deleted,
            cutoff_str,
        )
        return deleted

    def sync_visits(self):
        cutoff_str = _recent_window_start()
        sqlite_conn = get_connection()
        mysql_conn = None

        try:
            logger.info("Visits sync started. retention_window_start=%s", cutoff_str)
            self.ensure_sqlite_visits_schema(sqlite_conn)
            self.ensure_sqlite_sync_state_schema(sqlite_conn)

            unsynced_before = self.refresh_unsynced_count(sqlite_conn)
            logger.info("Unsynced local visits before sync: %s", unsynced_before)

            mysql_conn = get_mysql_connection()
            logger.info("MySQL connection acquired.")

            pushed = self.push_unsynced_to_mysql(sqlite_conn, mysql_conn)
            pulled = self.pull_recent_from_mysql(sqlite_conn, mysql_conn, cutoff_str)
            old_deleted = self.cleanup_old_local_visits(sqlite_conn, cutoff_str)

            unsynced_after = self.refresh_unsynced_count(sqlite_conn)
            last_pull_at = self.get_sync_state_value(sqlite_conn, SYNC_STATE_KEY_LAST_PULL)

            logger.info(
                "Visits sync finished. pushed=%s pulled=%s deleted_old=%s unsynced_before=%s unsynced_after=%s window_start=%s last_pull_at=%s",
                pushed,
                pulled,
                old_deleted,
                unsynced_before,
                unsynced_after,
                cutoff_str,
                last_pull_at,
            )

            return {
                "pushed": pushed,
                "pulled": pulled,
                "deleted_old": old_deleted,
                "unsynced_before": unsynced_before,
                "unsynced_after": unsynced_after,
                "window_start": cutoff_str,
                "last_pull_at": last_pull_at,
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

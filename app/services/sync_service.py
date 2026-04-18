from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from app.database.mysql_db import get_mysql_connection
from app.database.sqlite_db import get_connection
from app.services.state import AppState
from app.utils.config import SYNC_BATCH_SIZE, SYNC_VISITS_INTERVAL
from app.utils.logger import get_logger
from mysql.connector.errors import IntegrityError as MySQLIntegrityError

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


    def get_pull_watermark(self, sqlite_conn, default_value: str) -> str:
        """Backward-compatible wrapper for incremental pull watermark."""
        return self._get_pull_since(sqlite_conn, default_value)

    def set_pull_watermark(self, sqlite_conn, value: str):
        """Backward-compatible wrapper for storing the last successful pull watermark."""
        parsed = self._safe_parse_datetime(value)
        formatted = self._format_datetime(parsed) if parsed else str(value)
        self.set_sync_state_value(sqlite_conn, SYNC_STATE_KEY_LAST_PULL, formatted)
        logger.info("Updated last pull watermark in SQLite sync_state: %s", formatted)

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

        mysql_cur = mysql_conn.cursor(dictionary=True)
        synced_sqlite_ids: list[int] = []

        inserted_count = 0
        duplicate_sync_uuid_count = 0
        employee_day_conflict_count = 0

        logger.info("Preparing to push %s unsynced local visits to MySQL.", len(unsynced))

        for row in unsynced:
            sqlite_id = int(row["id"])
            employee_id = int(row["employee_id"])
            visit_time = row["visit_time"]
            source = row["source"]
            sync_uuid = row["sync_uuid"]

            try:
                mysql_cur.execute(
                    """
                    INSERT INTO visits (employee_id, visit_time, source, sync_uuid)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (employee_id, visit_time, source, sync_uuid),
                )

                synced_sqlite_ids.append(sqlite_id)
                inserted_count += 1

                logger.info(
                    "MySQL insert OK. sqlite_id=%s employee_id=%s visit_time=%s source=%s sync_uuid=%s",
                    sqlite_id,
                    employee_id,
                    visit_time,
                    source,
                    sync_uuid,
                )

            except MySQLIntegrityError as e:
                err_text = str(e)

                # 1) Нормальний кейс: цей sync_uuid уже є в MySQL
                if "ux_visits_sync_uuid" in err_text:
                    duplicate_sync_uuid_count += 1
                    synced_sqlite_ids.append(sqlite_id)

                    logger.warning(
                        "MySQL duplicate by sync_uuid. Marking local row as synced. "
                        "sqlite_id=%s employee_id=%s visit_time=%s source=%s sync_uuid=%s error=%s",
                        sqlite_id,
                        employee_id,
                        visit_time,
                        source,
                        sync_uuid,
                        err_text,
                    )
                    continue

                # 2) Конфлікт бізнес-правила: у MySQL уже є візит цього працівника за цей день
                if "ux_visits_employee_day" in err_text:
                    employee_day_conflict_count += 1

                    mysql_cur.execute(
                        """
                        SELECT id, employee_id, visit_time, source, sync_uuid, visit_date
                        FROM visits
                        WHERE employee_id = %s
                        AND visit_date = DATE(%s)
                        LIMIT 1
                        """,
                        (employee_id, visit_time),
                    )
                    existing = mysql_cur.fetchone()

                    logger.error(
                        "MySQL employee-day conflict. Local row NOT marked as synced. "
                        "sqlite_id=%s employee_id=%s local_visit_time=%s local_source=%s local_sync_uuid=%s "
                        "mysql_existing_id=%s mysql_existing_visit_time=%s mysql_existing_source=%s "
                        "mysql_existing_sync_uuid=%s mysql_existing_visit_date=%s error=%s",
                        sqlite_id,
                        employee_id,
                        visit_time,
                        source,
                        sync_uuid,
                        existing["id"] if existing else None,
                        existing["visit_time"] if existing else None,
                        existing["source"] if existing else None,
                        existing["sync_uuid"] if existing else None,
                        existing["visit_date"] if existing else None,
                        err_text,
                    )
                    continue

                logger.exception(
                    "Unexpected MySQL integrity error while pushing visit. "
                    "sqlite_id=%s employee_id=%s visit_time=%s source=%s sync_uuid=%s",
                    sqlite_id,
                    employee_id,
                    visit_time,
                    source,
                    sync_uuid,
                )
                raise

        mysql_conn.commit()

        if synced_sqlite_ids:
            sqlite_cur.executemany(
                "UPDATE visits SET synced = 1 WHERE id = ?",
                [(visit_id,) for visit_id in synced_sqlite_ids],
            )
            sqlite_conn.commit()

        logger.info(
            "Push to MySQL finished. inserted=%s duplicate_sync_uuid=%s employee_day_conflicts=%s marked_synced=%s",
            inserted_count,
            duplicate_sync_uuid_count,
            employee_day_conflict_count,
            len(synced_sqlite_ids),
        )

        return inserted_count


    def pull_recent_from_mysql(self, sqlite_conn, mysql_conn, cutoff_str: str) -> int:
        """
        Incrementally mirrors visits from MySQL to SQLite inside the retention window.

        Rules:
        - sync_uuid is the identity of the same event across DBs
        - employee_id + date(visit_time) is a business uniqueness rule
        - if MySQL contains a row that conflicts with an existing SQLite employee-day row
        but has a different sync_uuid, we do NOT insert it and do NOT crash the sync;
        we log a detailed conflict for manual investigation.
        """
        mysql_cur = mysql_conn.cursor(dictionary=True)
        sqlite_cur = sqlite_conn.cursor()

        pull_since = self._get_pull_since(sqlite_conn, cutoff_str)

        logger.info(
            "Starting incremental MySQL->SQLite pull. retention_window_start=%s pull_since=%s.",
            cutoff_str,
            pull_since,
        )

        mysql_cur.execute(
            """
            SELECT employee_id, visit_time, source, sync_uuid
            FROM visits
            WHERE visit_time >= %s
            ORDER BY visit_time, sync_uuid
            """,
            (pull_since,),
        )
        rows = mysql_cur.fetchall()

        if not rows:
            logger.info("No MySQL visits found for incremental pull since %s.", pull_since)
            return 0

        logger.info("Fetched %s MySQL visits for incremental pull.", len(rows))

        inserted_count = 0
        updated_by_sync_uuid_count = 0
        employee_day_conflict_count = 0
        skipped_bad_rows_count = 0

        latest_visit_time = None

        sqlite_cur.execute("BEGIN")

        try:
            for row in rows:
                employee_id = int(row["employee_id"])
                visit_time = row["visit_time"]
                source = row["source"]
                sync_uuid = row["sync_uuid"]

                if not visit_time or not sync_uuid:
                    skipped_bad_rows_count += 1
                    logger.error(
                        "Skipping invalid MySQL row during pull. employee_id=%s visit_time=%s source=%s sync_uuid=%s",
                        employee_id,
                        visit_time,
                        source,
                        sync_uuid,
                    )
                    continue

                # Track watermark candidate
                if latest_visit_time is None or str(visit_time) > str(latest_visit_time):
                    latest_visit_time = visit_time

                # 1) Exact same event already exists locally by sync_uuid -> update it
                sqlite_cur.execute(
                    """
                    SELECT id, employee_id, visit_time, source, sync_uuid, synced
                    FROM visits
                    WHERE sync_uuid = ?
                    LIMIT 1
                    """,
                    (sync_uuid,),
                )
                existing_by_uuid = sqlite_cur.fetchone()

                if existing_by_uuid:
                    sqlite_cur.execute(
                        """
                        UPDATE visits
                        SET employee_id = ?,
                            visit_time = ?,
                            source = ?,
                            synced = 1
                        WHERE sync_uuid = ?
                        """,
                        (
                            employee_id,
                            visit_time,
                            source,
                            sync_uuid,
                        ),
                    )
                    updated_by_sync_uuid_count += 1

                    logger.info(
                        "SQLite row updated by sync_uuid during pull. sqlite_id=%s employee_id=%s visit_time=%s source=%s sync_uuid=%s",
                        existing_by_uuid["id"],
                        employee_id,
                        visit_time,
                        source,
                        sync_uuid,
                    )
                    continue

                # 2) No same sync_uuid, but maybe same employee-day already exists locally
                sqlite_cur.execute(
                    """
                    SELECT id, employee_id, visit_time, source, sync_uuid, synced
                    FROM visits
                    WHERE employee_id = ?
                    AND date(visit_time) = date(?)
                    LIMIT 1
                    """,
                    (employee_id, visit_time),
                )
                existing_employee_day = sqlite_cur.fetchone()

                if existing_employee_day:
                    employee_day_conflict_count += 1

                    logger.error(
                        "Employee-day conflict during MySQL->SQLite pull. MySQL row NOT inserted. "
                        "mysql_employee_id=%s mysql_visit_time=%s mysql_source=%s mysql_sync_uuid=%s "
                        "sqlite_existing_id=%s sqlite_existing_visit_time=%s sqlite_existing_source=%s "
                        "sqlite_existing_sync_uuid=%s sqlite_existing_synced=%s",
                        employee_id,
                        visit_time,
                        source,
                        sync_uuid,
                        existing_employee_day["id"],
                        existing_employee_day["visit_time"],
                        existing_employee_day["source"],
                        existing_employee_day["sync_uuid"],
                        existing_employee_day["synced"],
                    )
                    continue

                # 3) Clean insert
                sqlite_cur.execute(
                    """
                    INSERT INTO visits (employee_id, visit_time, source, sync_uuid, synced)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        employee_id,
                        visit_time,
                        source,
                        sync_uuid,
                        1,  # pulled from MySQL => synced
                    ),
                )
                inserted_count += 1

                logger.info(
                    "Inserted MySQL row into SQLite. employee_id=%s visit_time=%s source=%s sync_uuid=%s",
                    employee_id,
                    visit_time,
                    source,
                    sync_uuid,
                )

            sqlite_conn.commit()

        except Exception:
            sqlite_conn.rollback()
            logger.exception("Incremental MySQL->SQLite pull failed and was rolled back.")
            raise

        # Update watermark only after successful commit
        if latest_visit_time is not None:
            self.set_pull_watermark(sqlite_conn, str(latest_visit_time))

        total_applied = inserted_count + updated_by_sync_uuid_count

        logger.info(
            "Incremental MySQL->SQLite pull finished. inserted=%s updated_by_sync_uuid=%s "
            "employee_day_conflicts=%s skipped_bad_rows=%s total_rows=%s next_watermark=%s",
            inserted_count,
            updated_by_sync_uuid_count,
            employee_day_conflict_count,
            skipped_bad_rows_count,
            len(rows),
            latest_visit_time,
        )

        return total_applied


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

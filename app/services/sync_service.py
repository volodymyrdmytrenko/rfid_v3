from __future__ import annotations

import threading
import time

from app.database.mysql_db import get_mysql_connection
from app.database.sqlite_db import get_connection
from app.services.state import AppState
from app.utils.config import SYNC_BATCH_SIZE, SYNC_VISITS_INTERVAL
from app.utils.logger import get_logger

logger = get_logger("SyncService")


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

    def sync_visits(self):
        sqlite_conn = get_connection()
        mysql_conn = None

        try:
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
                logger.info("No visits to sync.")
                return 0

            mysql_conn = get_mysql_connection()
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

            sqlite_cur.executemany(
                "UPDATE visits SET synced = 1 WHERE id = ?",
                [(visit_id,) for visit_id in synced_sqlite_ids],
            )
            sqlite_conn.commit()

            logger.info("Synced %s visits.", len(synced_sqlite_ids))
            return len(synced_sqlite_ids)

        except Exception:
            if mysql_conn:
                try:
                    mysql_conn.rollback()
                except Exception:
                    pass
            logger.exception("MySQL sync failed")
            raise

        finally:
            if mysql_conn:
                try:
                    mysql_conn.close()
                except Exception:
                    pass
            sqlite_conn.close()
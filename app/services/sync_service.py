import threading
import time
from datetime import datetime
from app.database.mysql_db import get_mysql_connection
from app.database.sqlite_db import get_connection
from app.utils.logger import get_logger
from app.utils.config import SYNC_VISITS_INTERVAL
from app.state import AppState

logger = get_logger("SyncService")


class SyncService:
    def __init__(self):
        self.running = False
        self.unsynced_count = 0

    def start(self):
        """Запускає фоновий потік синхронізації."""
        if self.running:
            return
        self.running = True
        thread = threading.Thread(target=self._loop, daemon=True)
        thread.start()
        logger.info("SyncService started.")

    def stop(self):
        self.running = False
        logger.info("SyncService stopped.")

    def _loop(self):
        """Періодичний запуск синхронізації."""
        while self.running:
            try:
                self.sync_visits()
            except Exception as e:
                logger.error(f"Sync error: {e}")
            time.sleep(SYNC_VISITS_INTERVAL)

    def sync_visits(self):
        """Синхронізує всі несинхронізовані visits."""
        logger.info("Syncing visits...")

        sqlite_conn = get_connection()
        sqlite_cur = sqlite_conn.cursor()

        sqlite_cur.execute("""
            SELECT id, employee_id, visit_time, source
            FROM visits
            WHERE synced = 0
        """)
        unsynced = sqlite_cur.fetchall()

        if not unsynced:
            logger.info("No visits to sync.")
            unsynced_count = 0
            return
        
        AppState.unsynced_count = self.unsynced_count = len(unsynced)

        logger.info(f"Found {self.unsynced_count} unsynced visits.")

        # Спробуємо записати в MySQL
        try:
            mysql_conn = get_mysql_connection()
            mysql_cur = mysql_conn.cursor()

            for row in unsynced:
                mysql_cur.execute("""
                    INSERT INTO visits (employee_id, visit_time, source)
                    VALUES (%s, %s, %s)
                """, (
                    row["employee_id"],
                    row["visit_time"],
                    row["source"]
                ))

                # Помічаємо у SQLite як synced
                sqlite_cur.execute("""
                    UPDATE visits SET synced = 1 WHERE id = ?
                """, (row["id"],))

            mysql_conn.commit()
            sqlite_conn.commit()

            logger.info(f"Synced {len(unsynced)} visits.")

        except Exception as e:
            logger.error(f"MySQL sync failed: {e}")

        finally:
            try:
                mysql_conn.close()
            except:
                pass
            sqlite_conn.close()

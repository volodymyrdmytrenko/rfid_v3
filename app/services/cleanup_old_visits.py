import os
from datetime import datetime, timedelta

from app.database.sqlite_db import get_connection
from app.utils.logger import get_logger
from app.utils.config import DAYS_SAVE

logger = get_logger("Cleanup")


def cleanup_old_visits() -> int:

    if DAYS_SAVE <= 0:
        logger.info("cleanup_old_visits skipped (DAYS_SAVE<=0)")
        return 0

    cutoff_date = datetime.now() - timedelta(days=DAYS_SAVE)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM visits WHERE visit_time < ?",
        (cutoff_str,)
    )

    deleted = cur.rowcount

    conn.commit()
    conn.close()

    logger.info(
        f"cleanup_old_visits: deleted={deleted}, older_than_days={DAYS_SAVE}"
    )

    return deleted
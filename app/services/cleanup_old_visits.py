from datetime import datetime

from app.database.sqlite_db import get_connection
from app.utils.logger import get_logger

logger = get_logger("Cleanup")


def _recent_window_start() -> str:
    """
    Keeps current month + previous month.
    Example:
      2026-04-14 -> 2026-03-01 00:00:00
    """
    now = datetime.now()
    year = now.year
    month = now.month - 1

    if month == 0:
        month = 12
        year -= 1

    return f"{year:04d}-{month:02d}-01 00:00:00"


def cleanup_old_visits() -> int:
    cutoff_str = _recent_window_start()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM visits WHERE visit_time < ?",
        (cutoff_str,),
    )

    deleted = cur.rowcount

    conn.commit()
    conn.close()

    logger.info(
        "cleanup_old_visits: deleted=%s, window_start=%s",
        deleted,
        cutoff_str,
    )

    return deleted
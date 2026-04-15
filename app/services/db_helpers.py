from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from app.database.sqlite_db import get_connection
from typing import Any


def _visit_time_sort_key(row: dict[str, Any]):
    """
    Safe sort key for rows returned by db_get_visits_for_date().
    Works with datetime or ISO-like string values.
    """
    value = row.get("visit_time")

    if value is None:
        return datetime.min

    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return datetime.min

        normalized = raw.replace("T", " ")
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue

        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.min

    return datetime.min


def db_get_last_registered_today() -> dict[str, Any] | None:
    """
    Return the last registration row for today only.

    This implementation intentionally uses the already existing
    db_get_visits_for_date() helper, so you can paste it into the current
    db_helpers.py without touching connection code.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    rows = db_get_visits_for_date(today)

    if not rows:
        return None

    return max(rows, key=_visit_time_sort_key)



def normalize_name(s: str) -> str:
    return " ".join((s or "").strip().split()).casefold()


def db_search_employees(query: str, limit: int = 20):
    q = f"%{normalize_name(query)}%"
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, full_name, fmoney
            FROM employees
            WHERE active = 1
              AND full_name_norm LIKE ?
            ORDER BY full_name
            LIMIT ?
            """,
            (q, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def db_find_employee_by_rfid(rfid: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, full_name, fmoney
            FROM employees
            WHERE rfid = ? AND active = 1
            """,
            (rfid,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def db_register_visit(employee_id: int, source: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO visits (employee_id, visit_time, source, synced, sync_uuid)
            VALUES (?, ?, ?, 0, ?)
            """,
            (
                employee_id,
                datetime.now().isoformat(timespec="seconds"),
                source,
                str(uuid.uuid4()),
            ),
        )
        conn.commit()
        return "ok", None
    except sqlite3.IntegrityError:
        return "duplicate", None
    except Exception as e:
        return "error", str(e)
    finally:
        conn.close()


def db_get_visits_for_date(report_date: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT v.visit_time, e.full_name, e.fmoney
            FROM visits v
            JOIN employees e ON e.id = v.employee_id
            WHERE date(v.visit_time) = date(?)
            ORDER BY e.full_name, v.id
            """,
            (report_date,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def db_get_unsynced_count():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM visits WHERE synced = 0")
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def db_get_today_visits_count():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM visits
            WHERE date(visit_time) = date('now','localtime')
            """
        )
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def db_get_last_registered():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT e.full_name, e.fmoney, v.visit_time, v.source
            FROM visits v
            JOIN employees e ON e.id = v.employee_id
            ORDER BY v.visit_time DESC, v.id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import pyodbc

from app.database.mysql_db import get_mysql_connection
from app.utils.config import (
    MSSQL_DATABASE,
    MSSQL_DRIVER,
    MSSQL_PASSWORD,
    MSSQL_SERVER,
    MSSQL_USERNAME,
    STOPNET_DEFAULT_FMONEY,
)
from app.utils.logger import get_logger

logger = get_logger("StopNetSync")

MSSQL_QUERY = """
SELECT
    e.colID AS id,
    ae.colAuthorizationCode AS rfid,
    LTRIM(RTRIM(
        ISNULL(e.colSurname, '') + ' ' + ISNULL(e.colName, '')
    )) AS full_name,
    CAST(1 AS int) AS active,
    ae.colBeginDateAction AS updated_at
FROM StopNet4.dbo.tblEmployees e
JOIN StopNet4.dbo.tblAccountEmployees ae
    ON e.colID = ae.colHolderID;
"""


def get_mssql_connection():
    conn_str = (
        f"DRIVER={{{MSSQL_DRIVER}}};"
        f"SERVER={MSSQL_SERVER};"
        f"DATABASE={MSSQL_DATABASE};"
        f"UID={MSSQL_USERNAME};"
        f"PWD={MSSQL_PASSWORD};"
        f"TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)


def fetch_from_mssql():
    conn = get_mssql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(MSSQL_QUERY)
        rows = cursor.fetchall()

        raw_data = []
        for row in rows:
            raw_data.append(
                {
                    "id": int(row.id),
                    "rfid": str(row.rfid).strip() if row.rfid is not None else None,
                    "full_name": row.full_name.strip() if row.full_name else "",
                    "active": int(row.active),
                    "updated_at": row.updated_at,
                    "fmoney": STOPNET_DEFAULT_FMONEY,
                }
            )

        return raw_data
    finally:
        conn.close()


def deduplicate_employees(raw_data):
    grouped = defaultdict(list)

    for row in raw_data:
        grouped[row["id"]].append(row)

    result = []

    for emp_id, rows in grouped.items():
        rows_sorted = sorted(
            rows,
            key=lambda x: (
                x["updated_at"] is not None,
                x["updated_at"] or datetime.min,
                x["rfid"] or "",
            ),
            reverse=True,
        )
        result.append(rows_sorted[0])

    return result


def sync_to_mysql(data):
    conn = get_mysql_connection()
    try:
        cur = conn.cursor()

        upsert_sql = """
        INSERT INTO employees
            (id, rfid, full_name, active, updated_at, fmoney)
        VALUES
            (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            rfid = VALUES(rfid),
            full_name = VALUES(full_name),
            active = VALUES(active),
            updated_at = VALUES(updated_at),
            fmoney = VALUES(fmoney)
        """

        if data:
            cur.executemany(
                upsert_sql,
                [
                    (
                        row["id"],
                        row["rfid"],
                        row["full_name"],
                        row["active"],
                        row["updated_at"],
                        row["fmoney"],
                    )
                    for row in data
                ],
            )

            ids = sorted({row["id"] for row in data})
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"UPDATE employees SET active = 0 WHERE id NOT IN ({placeholders})",
                ids,
            )
        else:
            cur.execute("UPDATE employees SET active = 0")

        conn.commit()
        logger.info("StopNet sync completed. Employees: %s", len(data))

    except Exception:
        conn.rollback()
        logger.exception("StopNet sync failed")
        raise
    finally:
        conn.close()


def stopnet_sync():
    raw_data = fetch_from_mssql()
    prepared_data = deduplicate_employees(raw_data)
    sync_to_mysql(prepared_data)
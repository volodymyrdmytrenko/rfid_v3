from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import pyodbc

from app.database.mysql_db import get_mysql_connection
from app.utils.config import (
    ENABLE_STOPNET_SYNC,
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
    ON e.colID = ae.colHolderID
WHERE ae.colAuthorizationCode IS NOT NULL
"""


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def _build_mssql_connection_string() -> str:
    return (
        f"DRIVER={{{MSSQL_DRIVER}}};"
        f"SERVER={MSSQL_SERVER};"
        f"DATABASE={MSSQL_DATABASE};"
        f"UID={MSSQL_USERNAME};"
        f"PWD={MSSQL_PASSWORD};"
        f"TrustServerCertificate=yes;"
        f"Connection Timeout=5;"
    )


def get_mssql_connection():
    logger.info(
        "Opening MSSQL connection. server=%s database=%s user=%s driver=%s",
        MSSQL_SERVER,
        MSSQL_DATABASE,
        _mask_secret(MSSQL_USERNAME),
        MSSQL_DRIVER,
    )
    conn = pyodbc.connect(_build_mssql_connection_string())
    logger.info("MSSQL connection opened successfully.")
    return conn


def fetch_from_mssql() -> list[dict[str, Any]]:
    logger.info("Starting fetch from MSSQL StopNet...")

    conn = None
    try:
        conn = get_mssql_connection()
        cursor = conn.cursor()
        cursor.execute(MSSQL_QUERY)
        rows = cursor.fetchall()

        raw_data: list[dict[str, Any]] = []

        for row in rows:
            try:
                emp_id = int(row.id)
            except Exception:
                logger.warning("Skipping row with invalid employee id: %s", row)
                continue

            rfid = str(row.rfid).strip() if row.rfid is not None else ""
            full_name = (row.full_name or "").strip()
            active = int(row.active or 1)
            updated_at = row.updated_at

            if not rfid:
                logger.debug("Skipping employee id=%s because RFID is empty.", emp_id)
                continue

            raw_data.append(
                {
                    "id": emp_id,
                    "rfid": rfid,
                    "full_name": full_name,
                    "active": active,
                    "updated_at": updated_at,
                    "fmoney": STOPNET_DEFAULT_FMONEY,
                }
            )

        logger.info(
            "Fetched %s raw employee rows from MSSQL. After empty-RFID filtering: %s",
            len(rows),
            len(raw_data),
        )
        return raw_data

    except Exception:
        logger.exception("Failed to fetch employees from MSSQL StopNet")
        raise

    finally:
        if conn is not None:
            try:
                conn.close()
                logger.info("MSSQL connection closed.")
            except Exception:
                logger.exception("Failed to close MSSQL connection")


def deduplicate_employees(raw_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    logger.info("Starting employee deduplication. Input rows: %s", len(raw_data))

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_data:
        grouped[row["id"]].append(row)

    result: list[dict[str, Any]] = []
    duplicates_count = 0

    for emp_id, rows in grouped.items():
        if len(rows) > 1:
            duplicates_count += len(rows) - 1
            logger.debug("Employee id=%s has %s duplicate rows.", emp_id, len(rows) - 1)

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

    logger.info(
        "Deduplication finished. Output rows: %s. Removed duplicates: %s",
        len(result),
        duplicates_count,
    )
    return result


def sync_to_mysql(data: list[dict[str, Any]]) -> int:
    logger.info("Starting sync to MySQL. Rows to sync: %s", len(data))

    conn = None
    try:
        conn = get_mysql_connection()
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
            payload = [
                (
                    row["id"],
                    row["rfid"],
                    row["full_name"],
                    row["active"],
                    row["updated_at"],
                    row["fmoney"],
                )
                for row in data
            ]

            cur.executemany(upsert_sql, payload)
            logger.info("MySQL UPSERT done for %s employees.", len(payload))

            ids = sorted({int(row["id"]) for row in data})
            placeholders = ",".join(["%s"] * len(ids))

            cur.execute(
                f"UPDATE employees SET active = 0 WHERE id NOT IN ({placeholders})",
                ids,
            )
            logger.info("Marked missing employees as inactive. Active ids count: %s", len(ids))
        else:
            cur.execute("UPDATE employees SET active = 0")
            logger.warning("Input data is empty. All MySQL employees were marked inactive.")

        conn.commit()
        logger.info("MySQL transaction committed successfully.")
        return len(data)

    except Exception:
        if conn is not None:
            try:
                conn.rollback()
                logger.warning("MySQL transaction rolled back.")
            except Exception:
                logger.exception("Failed to rollback MySQL transaction")

        logger.exception("StopNet sync to MySQL failed")
        raise

    finally:
        if conn is not None:
            try:
                conn.close()
                logger.info("MySQL connection closed.")
            except Exception:
                logger.exception("Failed to close MySQL connection")


def stopnet_sync() -> int:
    if not ENABLE_STOPNET_SYNC:
        logger.info("StopNet sync skipped because ENABLE_STOPNET_SYNC=false")
        return 0

    logger.info("StopNet sync started.")

    raw_data = fetch_from_mssql()
    prepared_data = deduplicate_employees(raw_data)
    synced_count = sync_to_mysql(prepared_data)

    logger.info(
        "StopNet sync finished successfully. raw=%s prepared=%s synced=%s",
        len(raw_data),
        len(prepared_data),
        synced_count,
    )
    return synced_count
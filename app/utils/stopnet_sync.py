import logging
from collections import defaultdict
from datetime import datetime

import pyodbc
import pymysql
from pymysql.cursors import DictCursor

# =========================
# MSSQL CONFIG
# =========================
MSSQL_SERVER = "100.122.229.15"
MSSQL_DATABASE = "StopNet4"
MSSQL_USERNAME = "vd"
MSSQL_PASSWORD = "112"
MSSQL_DRIVER = "ODBC Driver 18 for SQL Server"
# або "ODBC Driver 18 for SQL Server"

# =========================
# MYSQL CONFIG
# =========================
MYSQL_HOST = "vdvm.tailcc200e.ts.net"
MYSQL_PORT = 3306
MYSQL_DATABASE = "canteen"
MYSQL_USERNAME = "canteen"
MYSQL_PASSWORD = "GNgfvPeRNX0c5n"

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

MSSQL_QUERY = """
SELECT 
    e.colID AS id,
    ae.colAuthorizationCode AS rfid,
    LTRIM(RTRIM(
        ISNULL(e.colSurname, '') + ' ' + ISNULL(e.colName, '')
    )) AS full_name,
    CAST(1 AS int) AS active,
    ae.colBeginDateAction AS updated_at,
    CAST(50 AS int) AS fmoney
FROM StopNet4.dbo.tblEmployees e
JOIN StopNet4.dbo.tblAccountEmployees ae
    ON e.colID = ae.colHolderID;
"""

MYSQL_UPSERT = """
INSERT INTO employees
    (id, rfid, full_name, active, updated_at, fmoney)
VALUES
    (%s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    rfid = VALUES(rfid),
    full_name = VALUES(full_name),
    active = VALUES(active),
    updated_at = VALUES(updated_at);
"""

MYSQL_DEACTIVATE_MISSING = """
UPDATE employees
SET active = 0
WHERE id NOT IN ({placeholders});
"""

MYSQL_DEACTIVATE_ALL = """
UPDATE employees
SET active = 0;
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


def get_mysql_connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USERNAME,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


def fetch_from_mssql():
    conn = get_mssql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(MSSQL_QUERY)
        rows = cursor.fetchall()

        raw_data = []
        for row in rows:
            raw_data.append({
                "id": int(row.id),
                "rfid": str(row.rfid) if row.rfid is not None else None,
                "full_name": row.full_name.strip() if row.full_name else "",
                "active": int(row.active),
                "updated_at": row.updated_at,
                "fmoney": int(row.fmoney),
            })

        return raw_data
    finally:
        conn.close()


def deduplicate_employees(raw_data):
    grouped = defaultdict(list)

    for row in raw_data:
        grouped[row["id"]].append(row)

    result = []

    for emp_id, rows in grouped.items():
        if len(rows) > 1:
            logging.warning(
                "Працівник id=%s має %s карток у MSSQL. "
                "Буде використано останній запис за updated_at.",
                emp_id, len(rows)
            )

        rows_sorted = sorted(
            rows,
            key=lambda x: (
                x["updated_at"] is not None,
                x["updated_at"] or datetime.min,
                x["rfid"] or ""
            ),
            reverse=True
        )

        chosen = rows_sorted[0]
        result.append(chosen)

    return result


def sync_to_mysql(data):
    conn = get_mysql_connection()

    try:
        with conn.cursor() as cursor:
            upsert_data = [
                (
                    row["id"],
                    row["rfid"],
                    row["full_name"],
                    row["active"],
                    row["updated_at"],
                    row["fmoney"],  # тільки для INSERT
                )
                for row in data
            ]

            if upsert_data:
                cursor.executemany(MYSQL_UPSERT, upsert_data)

            current_ids = sorted({row["id"] for row in data})

            if current_ids:
                placeholders = ",".join(["%s"] * len(current_ids))
                sql = MYSQL_DEACTIVATE_MISSING.format(placeholders=placeholders)
                cursor.execute(sql, current_ids)
            else:
                cursor.execute(MYSQL_DEACTIVATE_ALL)

        conn.commit()
        logging.info("Синхронізація завершена. Оброблено працівників: %s", len(data))

    except Exception:
        conn.rollback()
        logging.exception("Помилка під час синхронізації")
        raise
    finally:
        conn.close()


def stopnet_sync():
    raw_data = fetch_from_mssql()
    prepared_data = deduplicate_employees(raw_data)
    sync_to_mysql(prepared_data)

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.database.sqlite_db import get_connection
from app.utils.logger import get_logger

logger = get_logger("RegistrationService")


@dataclass(slots=True)
class RegistrationResult:
    status: str
    message: str
    employee_id: int | None = None
    full_name: str | None = None
    fmoney: int | None = None


class RegistrationService:
    def register_by_rfid(self, rfid: str) -> RegistrationResult:
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
                """
                SELECT id, full_name, COALESCE(fmoney, 50) AS fmoney
                FROM employees
                WHERE rfid = ? AND active = 1
                """,
                (rfid,),
            )
            row = cur.fetchone()

            if row is None:
                logger.info("Unknown RFID: %s", rfid)
                return RegistrationResult(
                    status="unknown",
                    message="Невідомий брелок",
                )

            return self._register_employee(
                conn=conn,
                employee_id=int(row["id"]),
                full_name=(row["full_name"] or "").strip(),
                fmoney=int(row["fmoney"] or 50),
                source="rfid",
            )
        finally:
            conn.close()

    def register_manual(self, employee_id: int) -> RegistrationResult:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, full_name, COALESCE(fmoney, 50) AS fmoney
                FROM employees
                WHERE id = ? AND active = 1
                """,
                (employee_id,),
            )
            row = cur.fetchone()

            if row is None:
                return RegistrationResult(
                    status="not_found",
                    message="Співробітника не знайдено",
                )

            return self._register_employee(
                conn=conn,
                employee_id=int(row["id"]),
                full_name=(row["full_name"] or "").strip(),
                fmoney=int(row["fmoney"] or 50),
                source="manual",
            )
        finally:
            conn.close()

    def _register_employee(
        self,
        *,
        conn,
        employee_id: int,
        full_name: str,
        fmoney: int,
        source: str,
    ) -> RegistrationResult:
        cur = conn.cursor()

        try:
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

            logger.info("Visit registered: %s (%s)", full_name, source)
            return RegistrationResult(
                status="ok",
                message=f"Відмічено — {full_name}",
                employee_id=employee_id,
                full_name=full_name,
                fmoney=fmoney,
            )

        except sqlite3.IntegrityError:
            logger.info("Employee already visited today: %s", full_name)
            return RegistrationResult(
                status="duplicate",
                message=f"Вже був сьогодні — {full_name}",
                employee_id=employee_id,
                full_name=full_name,
                fmoney=fmoney,
            )

        except Exception as e:
            logger.exception("Registration failed for %s", full_name)
            return RegistrationResult(
                status="error",
                message=f"Помилка реєстрації — {full_name}: {e}",
                employee_id=employee_id,
                full_name=full_name,
                fmoney=fmoney,
            )
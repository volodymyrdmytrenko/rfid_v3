import time
from datetime import datetime
from app.database.sqlite_db import get_connection
from app.utils.logger import get_logger

logger = get_logger("Registration")


class RegistrationService:
    def __init__(self, beeper=None, ws_broadcast=None):
        """
        beeper: сервіс для сигналів (beep, beep-beep…)
        ws_broadcast: функція для трансляції в WebUI
        """
        self.beeper = beeper
        self.ws_broadcast = ws_broadcast

    def process_rfid(self, rfid):
        """Основна логіка реєстрації співробітника."""
        conn = get_connection()
        cur = conn.cursor()

        # 1. Чи існує такий RFID?
        cur.execute(
            "SELECT id, full_name FROM employees WHERE rfid = ? AND active = 1",
            (rfid,)
        )

        row = cur.fetchone()

        if row is None:
            logger.info(f"Unknown RFID: {rfid}")
            if self.beeper:
                self.beeper.beep_unknown()
            self._broadcast("Невідомий брелок")
            return

        emp_id = row["id"]
        # full_name = f"{row['fullname']} {row['first_name']}"
        full_name = f"{row['full_name']}"
        
        # 2. Чи був вже сьогодні?
        cur.execute("""
            SELECT 1 FROM visits
            WHERE employee_id = ?
            AND date(visit_time) = date('now','localtime')
            LIMIT 1
        """, (emp_id,))

        if cur.fetchone() is not None:
            logger.info(f"Employee already visited today: {full_name}")
            if self.beeper:
                self.beeper.beep_repeated()
            self._broadcast(f"Вже був сьогодні — {full_name}")
            return

        # 3. Створити запис у visits
        cur.execute("""
            INSERT INTO visits (employee_id, visit_time, source, synced)
            VALUES (?, ?, ?, 0)
        """, (emp_id, datetime.now().isoformat(), "rfid"))

        conn.commit()
        conn.close()

        logger.info(f"Visit registered: {full_name}")

        if self.beeper:
            self.beeper.beep_ok()

        self._broadcast(f"Відмічено — {full_name}")

    def _broadcast(self, message):
        if self.ws_broadcast:
            self.ws_broadcast(message)

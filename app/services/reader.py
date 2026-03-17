import serial
import threading
import time

from app.utils.config import RFID_PORT, TEST_MODE
from app.utils.logger import get_logger

logger = get_logger("RFIDReader")


class RFIDReader:
    """
    Працює з RDM6300 / подібними модулями.

    Кадр:
      0x02 (STX)
      12 ASCII HEX символів
      0x03 (ETX)

    Приклад: 02 35 35 30 30 39 46 41 33 44 30 42 39 03
             -> "55009FA3D0B9"

    Конвертація у DEC:
      беремо перші 5 байт (10 HEX)
      little-endian
      -> decimal
    """

    def __init__(self, port=RFID_PORT, baudrate=9600, callback=None):
        self.port = port
        self.baudrate = baudrate
        self.callback = callback
        self.running = False

        # Останній успішно зчитаний UID.
        # Повторно той самий UID не пропускаємо,
        # доки не буде зчитаний інший.
        self.last_accepted_uid = None

    # =======================
    # Публічні методи
    # =======================

    def start(self):
        self.running = True

        if TEST_MODE:
            t = threading.Thread(target=self._fake_loop, daemon=True)
            t.start()
            logger.info("RFID reader started in TEST MODE.")
            return

        t = threading.Thread(target=self._read_loop, daemon=True)
        t.start()
        logger.info(f"RFID reader started on port {self.port}")

    def stop(self):
        self.running = False
        logger.info("RFID reader stopped.")

    # =======================
    # Основний цикл читання
    # =======================

    def _read_loop(self):
        while self.running:
            try:
                with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
                    in_frame = False
                    buffer = bytearray()

                    while self.running:
                        b = ser.read(1)
                        if not b:
                            continue

                        value = b[0]

                        # STX
                        if value == 0x02:
                            buffer.clear()
                            in_frame = True
                            continue

                        # якщо не в кадрі — ігноруємо
                        if not in_frame:
                            continue

                        # ETX
                        if value == 0x03:
                            hex_uid = buffer.decode(errors="ignore").strip()
                            in_frame = False
                            buffer.clear()

                            if len(hex_uid) == 12:
                                dec_uid = self._hex12_to_dec_str(hex_uid)

                                if dec_uid:
                                    # Якщо це той самий брелок, що був останнім успішно прийнятим,
                                    # то повторне зчитування ігноруємо
                                    if dec_uid == self.last_accepted_uid:
                                        logger.debug(
                                            f"RFID duplicate ignored: HEX={hex_uid} DEC={dec_uid}"
                                        )
                                        continue

                                    # Приймаємо тільки якщо це інший брелок
                                    self.last_accepted_uid = dec_uid

                                    logger.info(
                                        f"RFID read HEX={hex_uid} DEC={dec_uid}"
                                    )
                                    if self.callback:
                                        self.callback(dec_uid)
                            continue

                        buffer.append(value)

            except serial.SerialException as e:
                logger.error(f"RFID serial error: {e}")
                time.sleep(2)

    # =======================
    # TEST MODE
    # =======================

    def _fake_loop(self):
        test_tags = [
            "55009FA3D0B9",  # A
            "55009FA3D0B9",  # A -> ignore
            "55009FA3D0B9",  # A -> ignore
            "66009FA3D0B9",  # B -> accept
            "55009FA3D0B9",  # A -> accept again
        ]

        i = 0
        while self.running:
            time.sleep(3)

            fake_hex = test_tags[i % len(test_tags)]
            i += 1

            fake_dec = self._hex12_to_dec_str(fake_hex)
            if not fake_dec:
                continue

            if fake_dec == self.last_accepted_uid:
                logger.debug(f"[TEST MODE] duplicate ignored: HEX={fake_hex} DEC={fake_dec}")
                continue

            self.last_accepted_uid = fake_dec

            logger.info(f"[TEST MODE] HEX={fake_hex} DEC={fake_dec}")

            if self.callback:
                self.callback(fake_dec)

    # =======================
    # Конвертація HEX -> DEC
    # =======================

    @staticmethod
    def _hex12_to_dec_str(hex12: str) -> str | None:
        """
        12 HEX -> беремо перші 5 байт (10 HEX)
        little-endian -> decimal
        """
        try:
            h = (hex12 or "").strip().upper()

            if len(h) < 10:
                return None

            five_bytes = bytes.fromhex(h[:10])
            value = int.from_bytes(five_bytes, "little")

            return str(value)

        except Exception:
            return None
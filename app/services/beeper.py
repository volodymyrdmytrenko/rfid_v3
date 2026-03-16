import os
from playsound import playsound
from app.utils.logger import get_logger

logger = get_logger("Beeper")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")


class Beeper:

    def beep_ok(self):
        logger.info("Зареєстровано успішно!")
        self._play("ok.mp3")

    def beep_unknown(self):
        logger.info("Невідомий брелок")
        self._play("unknown.mp3")

    def beep_error(self):
        logger.info("Помилка реєстрації")
        self._play("error.mp3")

    def beep_repeated(self):
        logger.info("Спроба повторної реєстрації")
        self._play("repeated.mp3")

    def _play(self, filename):
        try:
            path = os.path.join(SOUNDS_DIR, filename)
            playsound(path, block=False)
        except Exception as e:
            logger.error(f"Помилка відтворення звуку: {e}")
import logging
from logging.handlers import RotatingFileHandler

from app.utils.paths import LOGS_DIR, ensure_runtime_dirs


ensure_runtime_dirs()
LOG_FILE = LOGS_DIR / "system.log"


def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s | %(message)s"
        )

        ch = logging.StreamHandler()
        ch.setFormatter(fmt)

        fh = RotatingFileHandler(
            LOG_FILE,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8"
        )
        fh.setFormatter(fmt)

        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger
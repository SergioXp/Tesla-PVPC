"""Logging, date/time helpers, and shared utilities."""

import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

# Central timezone reference
SPAIN_TZ = timezone(timedelta(hours=2))  # CEST (UTC+2). For CET (winter): timedelta(hours=1)

def get_spain_tz() -> timezone:
    """Return the current Spanish timezone (CET or CEST based on DST)."""
    now_utc = datetime.now(timezone.utc)
    # Quick heuristic: last Sunday of March to last Sunday of October is CEST (UTC+2)
    # This is a simplified check; for production use zoneinfo/pytz
    month = now_utc.month
    if 4 <= month <= 9:
        return timezone(timedelta(hours=2))
    elif month == 3:
        # After last Sunday of March → CEST
        return timezone(timedelta(hours=2))
    elif month == 10:
        # Before last Sunday of October → CEST, after → CET
        return timezone(timedelta(hours=2))
    else:
        return timezone(timedelta(hours=1))


def now_spain() -> datetime:
    """Current datetime in Spanish timezone."""
    return datetime.now(timezone.utc).astimezone(get_spain_tz())


def today_str() -> str:
    """YYYY-MM-DD in Spanish timezone."""
    return now_spain().strftime("%Y-%m-%d")


def tomorrow_str() -> str:
    """Tomorrow's date as YYYY-MM-DD in Spanish timezone."""
    spain = get_spain_tz()
    now = datetime.now(timezone.utc).astimezone(spain)
    tomorrow = now + timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%d")


def hour_spanish(ts: datetime) -> int:
    """Extract the hour (0-23) from a datetime, converted to Spanish timezone."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    local = ts.astimezone(get_spain_tz())
    return local.hour


def setup_logger(name: str = "autocharge", level: int = logging.INFO) -> logging.Logger:
    """Configure and return a logger with timestamped console output."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


logger = setup_logger()


def mask_token(value: Any) -> str:
    """Mask a sensitive token/value for display, showing only first/last chars."""
    if value is None:
        return "(vacío)"
    s = str(value)
    if not s:
        return "(vacío)"
    if len(s) > 12:
        return s[:8] + "..." + s[-2:]
    else:
        return s[:4] + "..."

"""
Core utilities: logging, config loading, retry decorators, rate limiting.
"""

import json
import logging
import os
import time
import functools
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"

def load_config() -> dict:
    """Load and return the main config.json."""
    with open(_CONFIG_PATH, "r") as f:
        return json.load(f)

CONFIG = load_config()

# ── Logging ─────────────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """Return a configured logger with file + console handlers."""
    log_cfg = CONFIG["logging"]
    log_path = Path(log_cfg["file"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    level = getattr(logging, os.getenv("LOG_LEVEL", log_cfg["level"]).upper(), logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler
    fh = RotatingFileHandler(
        log_path,
        maxBytes=log_cfg["max_bytes"],
        backupCount=log_cfg["backup_count"],
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ── Retry decorator ─────────────────────────────────────────────────────────────

def retry(
    attempts: int = 3,
    delay: float = 5.0,
    exceptions: tuple = (Exception,),
    logger_name: str = "retry",
):
    """Decorator that retries a function on specified exceptions."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            log = get_logger(logger_name)
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == attempts:
                        log.error(f"{func.__name__} failed after {attempts} attempts: {e}")
                        raise
                    log.warning(f"{func.__name__} attempt {attempt}/{attempts} failed: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
        return wrapper
    return decorator


# ── Rate limiter ────────────────────────────────────────────────────────────────

class RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second
        self._last_call = 0.0

    def wait(self):
        elapsed = time.time() - self._last_call
        wait_time = self.min_interval - elapsed
        if wait_time > 0:
            time.sleep(wait_time)
        self._last_call = time.time()


# ── Helpers ─────────────────────────────────────────────────────────────────────

def env(key: str, default: Optional[str] = None) -> str:
    """Get environment variable with optional default."""
    val = os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    return val


def sanitize_filename(name: str) -> str:
    """Remove characters not safe for filenames."""
    import re
    return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name)[:100].strip()


def ensure_dir(path: str | Path) -> Path:
    """Create directory if it doesn't exist and return Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p

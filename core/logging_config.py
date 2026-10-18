"""Central logging configuration for MindBroadcast.

Sets up module loggers with a rotating file handler in the data directory.
Import and call `configure_logging()` once at startup.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

# Error counter for /api/health exposure.
_error_counts: dict = {}
_error_lock = None  # threading.Lock, created lazily


def _get_lock():
    global _error_lock
    if _error_lock is None:
        import threading
        _error_lock = threading.Lock()
    return _error_lock


def increment_error(category: str) -> None:
    """Increment an error counter for /api/health."""
    with _get_lock():
        _error_counts[category] = _error_counts.get(category, 0) + 1


def get_error_counts() -> dict:
    """Return a copy of the error counters."""
    with _get_lock():
        return dict(_error_counts)


def reset_error_counts() -> None:
    """Reset all error counters (for testing)."""
    with _get_lock():
        _error_counts.clear()


def configure_logging(
    data_dir: Path | None = None,
    level: int = logging.INFO,
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
) -> None:
    """Configure root logger with a rotating file handler.

    If data_dir is None, logs to stderr only (useful for tests).
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-configuration.
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Always log to stderr.
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    # Also log to a rotating file in the data directory.
    if data_dir is not None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = data_dir / "mindbroadcast.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

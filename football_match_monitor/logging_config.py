"""Structured JSON logging setup shared by the monitor and local website."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Convert each standard Python log record into one structured JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        """Return one log event as a single JSON object per line."""
        # These fields make log files searchable by both people and log tools.
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "event", "league_id", "date", "match_id", "change", "match_count",
            "host", "port", "data_source",
        ):
            # Extra fields are added only when a caller supplied them.
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        # Preserve a full traceback when logging an exception.
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(log_file: str) -> logging.Logger:
    """Create the log directory and send monitor logs to the configured file."""
    path = Path(log_file)
    # The log directory may not exist yet on the first run.
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("football_match_monitor")
    logger.setLevel(logging.INFO)
    # Avoid duplicate lines if setup is called more than once in the process.
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    return logger

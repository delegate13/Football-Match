import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
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
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(log_file: str) -> logging.Logger:
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("football_match_monitor")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    return logger

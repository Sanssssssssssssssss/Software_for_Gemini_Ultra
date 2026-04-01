from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in payload or key in {
                "args",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }:
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(settings: Settings) -> None:
    root_logger = logging.getLogger()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root_logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if settings.log_json
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

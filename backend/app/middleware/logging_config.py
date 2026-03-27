"""Structured JSON logging for production."""

import logging
import sys
import json
from datetime import datetime


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "service": "multisig-wallet",
            "message": record.getMessage(),
        }
        if hasattr(record, "trace_id"):
            log["trace_id"] = record.trace_id
        if record.exc_info and record.exc_info[1]:
            log["exception"] = str(record.exc_info[1])
        return json.dumps(log, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_format: bool = True):
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper()))
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

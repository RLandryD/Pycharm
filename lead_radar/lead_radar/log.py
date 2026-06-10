"""Structured JSON-lines logger. Every feature logs feature_invoked / feature_completed."""
import json
import logging
import sys
import time

_logger = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("lead_radar")
        _logger.setLevel(logging.INFO)
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(h)
    return _logger


def log_event(event: str, **fields):
    """Emit one JSON line. Never log credentials or API keys."""
    rec = {"ts": round(time.time(), 3), "event": event}
    rec.update(fields)
    get_logger().info(json.dumps(rec, ensure_ascii=False, default=str))


class feature:
    """Context manager: logs feature_invoked / feature_completed / feature_failed."""

    def __init__(self, name: str, **fields):
        self.name = name
        self.fields = fields

    def __enter__(self):
        log_event("feature_invoked", feature=self.name, **self.fields)
        self.t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        dur = round(time.time() - self.t0, 3)
        if exc is None:
            log_event("feature_completed", feature=self.name, duration_s=dur)
        else:
            log_event("feature_failed", feature=self.name, duration_s=dur,
                      error=f"{exc_type.__name__}: {exc}")
        return False

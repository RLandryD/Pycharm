"""
fetcher/run_collector.py — accumulate MPL runs into ONE file with dedup.

Shared by the one-shot "Fetch runs now" button and the standalone poller, so
both write the same single, deduplicated file the user can open anytime. Dedup
is by MessageGuid (stable per run); newest data wins on conflict.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ODATA_DATE = re.compile(r"/Date\((\d+)([+-]\d+)?\)/")


def parse_odata_date(value) -> Optional[datetime]:
    """Parse an OData v2 date such as '/Date(1717500000000)/' into a UTC
    datetime. Returns None if it isn't that shape."""
    if not isinstance(value, str):
        return None
    m = _ODATA_DATE.match(value.strip())
    if not m:
        return None
    try:
        return datetime.fromtimestamp(int(m.group(1)) / 1000.0, tz=timezone.utc)
    except Exception:                                  # noqa
        return None


def max_log_end(runs: list[dict]) -> Optional[datetime]:
    """Newest LogEnd across runs (for an incremental poll cursor)."""
    best = None
    for r in runs:
        dt = parse_odata_date(r.get("LogEnd"))
        if dt and (best is None or dt > best):
            best = dt
    return best


def load_runs(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:                                  # noqa
        return []


def append_runs(path, runs: list[dict]) -> tuple[int, int]:
    """Merge `runs` into the file at `path`, dedup by MessageGuid (newest wins),
    keep newest-first by LogEnd. Returns (added_count, total_count)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load_runs(p)
    by_guid: dict[str, dict] = {}
    for r in existing:
        g = r.get("MessageGuid") or r.get("MessageId") or id(r)
        by_guid[str(g)] = r
    added = 0
    for r in runs:
        g = str(r.get("MessageGuid") or r.get("MessageId") or "")
        if not g:
            continue
        if g not in by_guid:
            added += 1
        by_guid[g] = r  # newest wins

    def _sort_key(r):
        dt = parse_odata_date(r.get("LogEnd"))
        return dt.timestamp() if dt else 0.0

    merged = sorted(by_guid.values(), key=_sort_key, reverse=True)
    p.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return added, len(merged)

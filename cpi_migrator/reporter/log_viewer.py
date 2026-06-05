"""
reporter/log_viewer.py

Reads the cpi_migrator log file and collapses duplicate entries.

Log lines are timestamped, so the same recurring issue produces many lines
that differ only by their timestamp (and sometimes a counter or volatile id).
Naive de-duplication fails because no two lines are byte-identical. This
module strips the volatile parts to form a stable SIGNATURE, groups by it,
and reports each unique issue once with an occurrence count and the latest
timestamp.

Used by the workbench's log viewer (a "show de-duplicated log" button).
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Patterns for volatile fragments that should NOT distinguish two otherwise
# identical log entries.
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_LEVEL_PREFIX_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}.*?\s)?(DEBUG|INFO|WARNING|ERROR|CRITICAL)[:\s]",
    re.IGNORECASE,
)
# UUIDs, hex ids, exchange ids, and standalone long hex tokens are volatile
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEXID_RE = re.compile(r"\b[0-9A-F]{12,}-\d+\b")          # e.g. Exchange ids
_TRAILNUM_RE = re.compile(r"\b\d{3,}\b")                  # long bare numbers


@dataclass
class LogGroup:
    signature: str
    sample: str                 # a representative (latest) full line
    count: int = 0
    first_ts: str = ""
    last_ts: str = ""
    level: str = "INFO"


def _extract_timestamp(line: str) -> Optional[str]:
    m = _TIMESTAMP_RE.search(line)
    return m.group(0) if m else None


def _extract_level(line: str) -> str:
    m = _LEVEL_PREFIX_RE.search(line)
    if m:
        return m.group(1).upper()
    for lvl in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
        if lvl in line.upper():
            return lvl
    return "INFO"


def _signature(line: str) -> str:
    """Strip volatile fragments to produce a stable signature for grouping."""
    s = line
    s = _TIMESTAMP_RE.sub("<TS>", s)
    s = _UUID_RE.sub("<UUID>", s)
    s = _HEXID_RE.sub("<ID>", s)
    s = _TRAILNUM_RE.sub("<N>", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def deduplicate_log(
    log_path: str | Path,
    levels: Optional[set[str]] = None,
    max_lines: int = 50_000,
) -> list[LogGroup]:
    """Read the log and return de-duplicated groups, most frequent first.

    levels: if given, only include entries at these levels (e.g. {"ERROR",
            "WARNING"}). None = all levels.
    Multi-line log entries (e.g. the upload error block) are joined to the
    preceding entry so the whole block groups as one signature.
    """
    path = Path(log_path)
    if not path.exists():
        return []

    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(raw) > max_lines:
        raw = raw[-max_lines:]   # only the most recent window

    # Reassemble multi-line entries: a new entry starts with a timestamp or a
    # level keyword; continuation lines (indented / no level) attach to it.
    entries: list[str] = []
    for line in raw:
        starts_entry = bool(_TIMESTAMP_RE.match(line)) or bool(
            re.match(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)[:\s]", line, re.IGNORECASE))
        if starts_entry or not entries:
            entries.append(line)
        else:
            entries[-1] += "\n" + line

    groups: "OrderedDict[str, LogGroup]" = OrderedDict()
    for entry in entries:
        if not entry.strip():
            continue
        level = _extract_level(entry)
        if levels and level not in levels:
            continue
        sig = _signature(entry)
        ts = _extract_timestamp(entry) or ""
        if sig not in groups:
            groups[sig] = LogGroup(signature=sig, sample=entry, count=0,
                                   first_ts=ts, last_ts=ts, level=level)
        g = groups[sig]
        g.count += 1
        g.sample = entry          # keep the latest as representative
        if ts:
            if not g.first_ts or ts < g.first_ts:
                g.first_ts = ts
            if ts > g.last_ts:
                g.last_ts = ts

    # Sort: errors first, then by frequency
    level_rank = {"CRITICAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3, "DEBUG": 4}
    return sorted(groups.values(),
                  key=lambda g: (level_rank.get(g.level, 9), -g.count))


def log_summary(groups: list[LogGroup]) -> dict:
    """Headline counts for display."""
    total_lines = sum(g.count for g in groups)
    return {
        "unique_issues": len(groups),
        "total_lines":   total_lines,
        "errors":   sum(g.count for g in groups if g.level == "ERROR"),
        "warnings": sum(g.count for g in groups if g.level == "WARNING"),
        "collapsed": total_lines - len(groups),  # how many lines were noise
    }

"""
fetcher/mpl_fetcher.py — read Message Processing Logs (MPL) from a CPI tenant
via the documented OData v2 API (`/api/v1/MessageProcessingLogs`).

This is the OAuth-safe, supported way to pull what actually happened in a run:
status, timing, the iFlow, the error text, and — when the iFlow persisted it —
the message body via MessageStoreEntries. It reuses an authenticated
`requests.Session` exactly like CPIFetcher, so callers don't re-do auth.

Nothing here touches the internal Web-UI endpoints (those need cookie/CSRF auth
and are undocumented); everything is the public OData service.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ── pure helpers (unit-testable without a session) ─────────────────────────
def _odata_datetime(dt: datetime) -> str:
    """OData v2 datetime literal, e.g. datetime'2026-06-04T00:00:00'."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return "datetime'" + dt.strftime("%Y-%m-%dT%H:%M:%S") + "'"


def build_mpl_filter(iflow_name: str = "", status: str = "",
                     since: Optional[datetime] = None) -> str:
    """Build the $filter string for MessageProcessingLogs from optional parts.
    Returns "" when no constraints are given."""
    clauses = []
    if status:
        clauses.append(f"Status eq '{status}'")
    if iflow_name:
        # IntegrationFlowName is the human iFlow name on the MPL entity.
        clauses.append(f"IntegrationFlowName eq '{iflow_name}'")
    if since is not None:
        clauses.append(f"LogEnd gt {_odata_datetime(since)}")
    return " and ".join(clauses)


def _unwrap(data: dict):
    """OData v2 JSON wraps collections in {'d': {'results': [...]}} and single
    entities in {'d': {...}}. Return the inner payload."""
    d = data.get("d", data)
    if isinstance(d, dict) and "results" in d:
        return d["results"]
    return d


# ── fetcher ────────────────────────────────────────────────────────────────
class MPLFetcher:
    """Read message processing logs from a CPI tenant. `session` must already
    carry auth (Bearer token or basic), same contract as CPIFetcher."""

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session = session
        # Last-call diagnostics (so the UI can explain a "0 runs" result:
        # 403 = missing monitoring role, 200+0 = empty/host mismatch, etc.)
        self.last_status: int = 0
        self.last_url: str = ""
        self.last_count: int = 0
        self.last_error: str = ""

    # -- runs -----------------------------------------------------------------
    def recent_runs(self, iflow_name: str = "", status: str = "",
                    since: Optional[datetime] = None, top: int = 50) -> list[dict]:
        """Return recent MPL entries (newest first). Any of iflow_name / status /
        since narrow the query; all optional."""
        params = {
            "$format": "json",
            "$orderby": "LogEnd desc",
            "$top": str(max(1, min(top, 1000))),
        }
        flt = build_mpl_filter(iflow_name, status, since)
        if flt:
            params["$filter"] = flt
        url = f"{self.base_url}/api/v1/MessageProcessingLogs"
        self.last_url = url
        self.last_error = ""
        try:
            resp = self.session.get(url, params=params, timeout=30)
        except Exception as exc:                       # noqa
            self.last_status = -1
            self.last_error = str(exc)
            logger.warning("MPL query errored: %s", exc)
            return []
        self.last_status = resp.status_code
        if resp.status_code != 200:
            self.last_error = (resp.text or "")[:300]
            logger.warning("MPL query returned %d: %s", resp.status_code,
                           self.last_error)
            return []
        try:
            rows = _unwrap(resp.json())
        except Exception as exc:                       # noqa
            self.last_error = f"JSON parse failed: {exc}"
            return []
        rows = rows if isinstance(rows, list) else [rows]
        self.last_count = len(rows)
        return rows

    def runs_since(self, last_log_end: Optional[datetime], iflow_name: str = "",
                   top: int = 100) -> list[dict]:
        """Incremental fetch for a poller: only runs newer than last_log_end."""
        return self.recent_runs(iflow_name=iflow_name, since=last_log_end, top=top)

    # -- error detail ---------------------------------------------------------
    def error_text(self, guid: str) -> str:
        """The detailed error log for a message (deferred nav property).
        Returns "" if none / not available."""
        url = (f"{self.base_url}/api/v1/MessageProcessingLogs('{guid}')"
               f"/ErrorInformation/$value")
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.text or ""
            logger.info("No ErrorInformation for %s (HTTP %d)", guid, resp.status_code)
        except Exception as exc:                       # noqa
            logger.info("ErrorInformation fetch failed for %s: %s", guid, exc)
        return ""

    # -- message body (documented MessageStore path — OAuth-safe slice) -------
    def message_store_entries(self, guid: str) -> list[dict]:
        """List MessageStore entries for a run (only present if the iFlow wrote
        to the message store). Navigate from the MPL entity per SAP guidance."""
        url = (f"{self.base_url}/api/v1/MessageProcessingLogs('{guid}')"
               f"/MessageStoreEntries")
        try:
            resp = self.session.get(url, params={"$format": "json"}, timeout=30)
            if resp.status_code == 200:
                rows = _unwrap(resp.json())
                return rows if isinstance(rows, list) else [rows]
            logger.info("No MessageStoreEntries for %s (HTTP %d)", guid, resp.status_code)
        except Exception as exc:                       # noqa
            logger.info("MessageStoreEntries fetch failed for %s: %s", guid, exc)
        return []

    def message_body(self, entry_id: str) -> bytes:
        """Fetch a stored message body by MessageStoreEntry id ($value).
        Empty bytes if unavailable. NOTE: only works when the iFlow persisted
        the payload to the message store; live trace payloads are retention-
        limited and not covered here."""
        url = (f"{self.base_url}/api/v1/MessageStoreEntries('{entry_id}')/$value")
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.content or b""
            logger.info("No body for entry %s (HTTP %d)", entry_id, resp.status_code)
        except Exception as exc:                       # noqa
            logger.info("Message body fetch failed for %s: %s", entry_id, exc)
        return b""

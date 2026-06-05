#!/usr/bin/env python3
"""
service_key.py  --  load a CF/BTP service key (.json), connect with it, and
remember where the key files live so they can be picked again.

A "service key" here is the CF *Process Integration Runtime* key with an oauth
block: {clientid, clientsecret, tokenurl, url}. This mirrors what the standalone
cpi_api_deploy.py reads, but returns an authenticated requests.Session via the
project's CFAuthenticator so the workbench deploys with the same plumbing it
already uses for the sidebar connection.

Pure logic + auth import only (no Streamlit), so it is unit-testable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Remember the folder the user last picked keys from, next to the profiles.
try:
    from models.credential_store import PROFILES_DIR as _BASE_DIR  # type: ignore
except Exception:  # pragma: no cover - fallback if import path differs
    _BASE_DIR = Path.home() / ".cpi_migrator"

_KEYS_DIR_MARKER = Path(_BASE_DIR) / "service_keys_dir.txt"


def keys_store_dir() -> str:
    """Stable folder for keys uploaded through the UI, so they persist across
    sessions, appear in the picker, and are reachable by the background poller.
    Lives under the external store (survives re-importing the project)."""
    d = Path(_BASE_DIR) / "keys"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:  # pragma: no cover
        pass
    return str(d)



def load_service_key(path: str) -> Dict[str, str]:
    """Parse a service key JSON → {token_url, base_url, client_id, client_secret}.

    Accepts keys whose fields are nested under "oauth" or at the top level.
    base_url is normalised to the tenant HOST ROOT (no /api/v1), which is what
    the workbench's CPIUploader expects (it appends /api/v1 itself).
    """
    with open(path, "r", encoding="utf-8") as fh:
        key = json.load(fh)
    oauth = key.get("oauth", key)
    token_url = (oauth.get("tokenurl") or "").rstrip("/")
    if token_url and not token_url.endswith("/oauth/token"):
        token_url += "/oauth/token"
    base = (oauth.get("url") or key.get("url") or "").rstrip("/")
    # strip a trailing /api/v1 (or /api) so we hold the host root
    for suffix in ("/api/v1", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return {
        "token_url": token_url,
        "base_url": base.rstrip("/"),
        "client_id": oauth.get("clientid") or "",
        "client_secret": oauth.get("clientsecret") or "",
    }


def connect_with_service_key(path: str):
    """Load the key and return (session, base_url) using CFAuthenticator.

    Imported lazily so this module stays importable even where auth deps differ.
    """
    creds = load_service_key(path)
    missing = [k for k in ("token_url", "base_url", "client_id", "client_secret")
               if not creds.get(k)]
    if missing:
        raise ValueError("Service key missing fields: " + ", ".join(missing))
    from auth.authenticator import CFAuthenticator
    session = CFAuthenticator(creds["token_url"], creds["client_id"],
                              creds["client_secret"]).get_session()
    return session, creds["base_url"]


def list_service_keys(directory: str) -> List[str]:
    """Return the *.json filenames in a directory (sorted), or [] if none."""
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.json"))


def remember_keys_dir(directory: str) -> None:
    """Persist the keys folder in the unified external store (survives project
    re-import). Falls back to the legacy marker file if settings are unavailable."""
    try:
        from fetcher.user_settings import set_setting
        set_setting("service_keys_dir", str(directory))
        return
    except Exception:
        pass
    try:  # legacy fallback
        _KEYS_DIR_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _KEYS_DIR_MARKER.write_text(str(directory), encoding="utf-8")
    except Exception:
        pass


def recall_keys_dir() -> Optional[str]:
    """Return the last-picked keys directory (unified store first, then the
    legacy marker for backward compatibility)."""
    try:
        from fetcher.user_settings import get_setting
        v = get_setting("service_keys_dir")
        if v:
            return v
    except Exception:
        pass
    try:
        if _KEYS_DIR_MARKER.is_file():
            d = _KEYS_DIR_MARKER.read_text(encoding="utf-8").strip()
            return d or None
    except Exception:
        pass
    return None


def remember_key_path(path: str) -> None:
    """Persist the specific .json service-key file last used (external, unified)."""
    try:
        from fetcher.user_settings import set_setting
        set_setting("service_key_path", str(path))
    except Exception:
        pass


def recall_key_path() -> Optional[str]:
    """Return the specific .json key file last used, if it still exists on disk."""
    try:
        import os
        from fetcher.user_settings import get_setting
        v = get_setting("service_key_path")
        if v and os.path.isfile(v):
            return v
    except Exception:
        pass
    return None


if __name__ == "__main__":
    print("Library: load_service_key / connect_with_service_key / "
          "list_service_keys / remember_keys_dir / recall_keys_dir / "
          "remember_key_path / recall_key_path")

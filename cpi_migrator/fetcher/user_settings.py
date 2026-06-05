#!/usr/bin/env python3
"""
user_settings.py  --  tiny external settings store.

Persists small user preferences (folder paths, etc.) to a JSON file OUTSIDE the
project directory, so deleting/re-importing the project does NOT wipe them. Uses
the same home-dir convention the rest of the app already uses (~/.cpi_migrator),
so it sits alongside the wire log, cached samples and remembered service-key dir.

Override the location for tests (or a custom setup) with the CPI_MIGRATOR_HOME
environment variable.

Typical keys:
  capability_corpus_dir   the by-type harvest folder for the capability catalog
                          (e.g. .../Final) -> feeds build_corpus(path=...)
  template_library_dir    the folder of real packages for clone-and-adapt
                          (e.g. .../Resources/Packages)
  service_keys_dir        folder holding CPI service-key .json files
  service_key_path        a specific CPI service-key .json to connect the tenant
  hub_api_key             SAP Business Accelerator Hub API key (used to fetch
                          the Hub catalog)

CLI:
  python3 -m fetcher.user_settings list
  python3 -m fetcher.user_settings get  capability_corpus_dir
  python3 -m fetcher.user_settings set  capability_corpus_dir /home/me/Final
  python3 -m fetcher.user_settings unset capability_corpus_dir
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _base_dir() -> Path:
    """The external directory the settings file lives in (never under the
    project)."""
    env = os.environ.get("CPI_MIGRATOR_HOME")
    if env:
        return Path(env)
    return Path.home() / ".cpi_migrator"


def settings_path() -> Path:
    return _base_dir() / "settings.json"


def all_settings() -> Dict[str, Any]:
    """Return all stored settings (empty dict if none/unreadable)."""
    p = settings_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_setting(key: str, default: Any = None) -> Any:
    return all_settings().get(key, default)


def set_setting(key: str, value: Any) -> None:
    """Persist one setting, creating the external dir if needed."""
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = all_settings()
    data[key] = value
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def unset_setting(key: str) -> None:
    data = all_settings()
    if key in data:
        del data[key]
        settings_path().write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def get_dir(key: str) -> Optional[str]:
    """Return a stored path ONLY if it still exists on disk, else None.
    Keeps callers from feeding a stale/missing folder into the pipeline."""
    val = get_setting(key)
    if val and os.path.isdir(val):
        return val
    return None


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    if cmd == "list":
        s = all_settings()
        print(f"settings file: {settings_path()}")
        if not s:
            print("(empty)")
        for k, v in s.items():
            exists = ""
            if isinstance(v, str) and ("/" in v or "\\" in v):
                exists = "  [exists]" if os.path.isdir(v) else "  [MISSING]"
            print(f"  {k} = {v}{exists}")
    elif cmd == "get" and len(args) >= 2:
        print(get_setting(args[1], ""))
    elif cmd == "set" and len(args) >= 3:
        set_setting(args[1], args[2])
        print(f"set {args[1]} = {args[2]}\n-> {settings_path()}")
    elif cmd == "unset" and len(args) >= 2:
        unset_setting(args[1])
        print(f"unset {args[1]}")
    else:
        print(__doc__)

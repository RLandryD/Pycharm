"""
fetcher/wire_log.py

Always-on "wire log" — records the full HTTP exchange between the workbench
and the CPI tenant (every request + response), so a failed upload produces a
readable transcript instead of a single cryptic warning.

What it captures per call:
  - request: method, URL, headers (Authorization/token REDACTED), body size
  - response: status code, key headers, body (truncated)
Each entry is timestamped and labeled with a step name (e.g. "CSRF fetch",
"create package", "upload artifact").

It writes to ~/.cpi_migrator/cpi_wire.log (separate from the main log so it's
easy to copy and hand over). A copy button in the UI reads this file.

Noise filtering: Streamlit's use_container_width / width deprecation warnings
(and similar persistent non-actionable warnings) are filtered out so the
transcript stays focused on the actual client↔server communication.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

WIRE_LOG_DIR = Path.home() / ".cpi_migrator"
WIRE_LOG_FILE = WIRE_LOG_DIR / "cpi_wire.log"

# Persistent, non-actionable warning fragments to drop from any logging.
_NOISE_FRAGMENTS = (
    "use_container_width",
    "Please replace `use_container_width`",
    "width='stretch'",
    "width='content'",
    "missing ScriptRunContext",
)

_SENSITIVE_HEADERS = {"authorization", "x-csrf-token", "cookie", "set-cookie",
                      "apikey", "api-key"}


class _NoiseFilter(logging.Filter):
    """A logging filter that drops persistent non-actionable warnings."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(frag in msg for frag in _NOISE_FRAGMENTS)


def install_noise_filter():
    """Attach the noise filter to the root logger + handlers so the spammy
    Streamlit deprecation warnings stop polluting the log."""
    f = _NoiseFilter()
    root = logging.getLogger()
    root.addFilter(f)
    for h in root.handlers:
        h.addFilter(f)
    # Streamlit's own loggers emit the width deprecation — quiet them.
    for name in ("streamlit", "streamlit.elements", "streamlit.runtime"):
        logging.getLogger(name).setLevel(logging.ERROR)


def _redact(headers: dict) -> dict:
    out = {}
    for k, v in (headers or {}).items():
        out[k] = "<redacted>" if k.lower() in _SENSITIVE_HEADERS else v
    return out


def _ensure_dir():
    WIRE_LOG_DIR.mkdir(parents=True, exist_ok=True)


def reset_wire_log():
    """Start a fresh transcript (called at the start of an upload run)."""
    _ensure_dir()
    try:
        WIRE_LOG_FILE.write_text(
            f"=== CPI wire log — started {datetime.now().isoformat(timespec='seconds')} ===\n",
            encoding="utf-8")
    except Exception:
        pass


def _append(text: str):
    _ensure_dir()
    try:
        with open(WIRE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def log_request(step: str, method: str, url: str,
                headers: Optional[dict] = None, body=None):
    ts = datetime.now().strftime("%H:%M:%S")
    size = ""
    if body is not None:
        try:
            size = f" body={len(body) if isinstance(body,(bytes,str)) else len(str(body))} bytes"
        except Exception:
            size = ""
    lines = [f"\n[{ts}] ▶ REQUEST · {step}",
             f"  {method} {url}{size}"]
    if headers:
        for k, v in _redact(headers).items():
            lines.append(f"    {k}: {v}")
    _append("\n".join(lines))


def log_response(step: str, status: int,
                 headers: Optional[dict] = None, body: str = ""):
    ts = datetime.now().strftime("%H:%M:%S")
    lines = [f"[{ts}] ◀ RESPONSE · {step}",
             f"  HTTP {status}"]
    if headers:
        # Only the informative response headers
        for k in ("X-CSRF-Token", "Content-Type", "Location",
                  "x-vcap-request-id", "WWW-Authenticate"):
            for hk, hv in headers.items():
                if hk.lower() == k.lower():
                    val = "<redacted>" if hk.lower() in _SENSITIVE_HEADERS else hv
                    lines.append(f"    {hk}: {val}")
    if body:
        snippet = body[:1000]
        lines.append(f"  body: {snippet}")
    _append("\n".join(lines))


def log_note(text: str):
    ts = datetime.now().strftime("%H:%M:%S")
    _append(f"[{ts}] • {text}")


def read_wire_log() -> str:
    try:
        return WIRE_LOG_FILE.read_text(encoding="utf-8")
    except Exception:
        return "(wire log is empty — run an upload to populate it)"


class WireLogHandler(logging.Handler):
    """A logging handler that appends every log record into the unified wire
    log file, so the communication transcript AND the diagnostic messages live
    in ONE place (one copy button, no two separate logs)."""
    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
        except Exception:
            return
        if any(frag in msg for frag in _NOISE_FRAGMENTS):
            return
        ts = datetime.now().strftime("%H:%M:%S")
        lvl = record.levelname
        name = record.name
        _append(f"[{ts}] {lvl:7} {name}: {msg}")


def install_unified_logging():
    """Route all app logging into the single wire log file too, and install
    the noise filter. After this, the wire log is the ONE log to copy."""
    install_noise_filter()
    root = logging.getLogger()
    # Avoid adding the handler twice on Streamlit reruns
    if not any(isinstance(h, WireLogHandler) for h in root.handlers):
        h = WireLogHandler()
        h.setLevel(logging.INFO)
        root.addHandler(h)

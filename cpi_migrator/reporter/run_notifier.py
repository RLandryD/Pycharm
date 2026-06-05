"""
reporter/run_notifier.py

Two things for long-running batches:
  1. ETA tracking — per-item timing → estimated time remaining.
  2. Notifications — email-on-finish (away from desk) + a browser
     banner/sound (at the desk), triggered from Streamlit.

The email uses SMTP (configured in settings). The browser banner/sound is
emitted as a small HTML/JS snippet Streamlit renders.
"""
from __future__ import annotations

import logging
import smtplib
import time
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger("cpi.notifier")


@dataclass
class ETATracker:
    """Tracks per-item durations to estimate time remaining."""
    total: int
    done: int = 0
    _start: float = field(default_factory=time.time)
    _per_item: list = field(default_factory=list)
    _last: float = field(default_factory=time.time)

    def tick(self):
        """Call once per completed item."""
        now = time.time()
        self._per_item.append(now - self._last)
        self._last = now
        self.done += 1

    @property
    def avg_seconds(self) -> float:
        return (sum(self._per_item) / len(self._per_item)) if self._per_item else 0.0

    @property
    def remaining_seconds(self) -> float:
        return self.avg_seconds * max(0, self.total - self.done)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start

    def label(self) -> str:
        if self.done == 0:
            return f"Starting… 0 of {self.total}"
        rem = self.remaining_seconds
        mins, secs = divmod(int(rem), 60)
        eta = f"{mins}m {secs}s" if mins else f"{secs}s"
        return f"{self.done} of {self.total} done · ~{eta} remaining"


def send_email_notification(smtp_cfg: dict, subject: str, body: str) -> tuple[bool, str]:
    """Send a completion email. smtp_cfg keys: host, port, user, password,
    from_addr, to_addr, use_tls."""
    required = ("host", "port", "from_addr", "to_addr")
    missing = [k for k in required if not smtp_cfg.get(k)]
    if missing:
        return False, f"SMTP not configured (missing: {', '.join(missing)})"
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_cfg["from_addr"]
        msg["To"] = smtp_cfg["to_addr"]
        port = int(smtp_cfg["port"])
        if smtp_cfg.get("use_tls", True):
            server = smtplib.SMTP(smtp_cfg["host"], port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP(smtp_cfg["host"], port, timeout=30)
        if smtp_cfg.get("user") and smtp_cfg.get("password"):
            server.login(smtp_cfg["user"], smtp_cfg["password"])
        server.send_message(msg)
        server.quit()
        logger.info("Notification email sent to %s", smtp_cfg["to_addr"])
        return True, "Email sent"
    except Exception as exc:
        logger.error("Email notification failed: %s", exc)
        return False, str(exc)


def browser_notify_html(message: str, play_sound: bool = True) -> str:
    """Return an HTML/JS snippet that shows a browser notification + optional
    beep. Rendered via st.components.v1.html(...). Falls back to an on-page
    banner if notifications are blocked."""
    safe = message.replace("'", "\\'").replace("\n", " ")
    sound_js = ""
    if play_sound:
        sound_js = """
        try {
          var ctx = new (window.AudioContext||window.webkitAudioContext)();
          var o = ctx.createOscillator(); var g = ctx.createGain();
          o.connect(g); g.connect(ctx.destination);
          o.frequency.value = 880; o.type='sine';
          g.gain.setValueAtTime(0.2, ctx.currentTime);
          o.start();
          g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime+0.6);
          o.stop(ctx.currentTime+0.6);
        } catch(e){}
        """
    return f"""
    <div style="padding:12px;border-radius:8px;background:#16a34a;color:white;
                font-weight:600;">✅ {message}</div>
    <script>
      {sound_js}
      try {{
        if ("Notification" in window) {{
          if (Notification.permission === "granted") {{
            new Notification("CPI Migration", {{body: '{safe}'}});
          }} else if (Notification.permission !== "denied") {{
            Notification.requestPermission().then(function(p){{
              if (p === "granted") new Notification("CPI Migration", {{body: '{safe}'}});
            }});
          }}
        }}
      }} catch(e) {{}}
    </script>
    """

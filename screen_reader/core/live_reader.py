"""
core/live_reader.py

Fixes applied:
  1. Multi-frame OCR voting — 3 reads per decision, take majority
  2. Slow-transition tolerance — small drifts (<=0.08) don't reset stability timer
  3. Flush on stop — if value was stable >= 50% of stable_secs when reader stops, accept it
  4. Departure detection unchanged (works correctly)
  5. Frame-to-frame dedup unchanged
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable

DEPARTURE_DELTA  = 0.10   # how far reading must move before re-accepting same value
SLOW_DRIFT       = 0.08   # drifts smaller than this don't reset stability timer
VOTE_FRAMES      = 3      # how many consecutive reads to take majority from
FLUSH_RATIO      = 0.50   # flush if stable for >= this fraction of stable_secs


@dataclass
class LogEntry:
    timestamp:   float
    value:       float
    raw_ocr:     str
    status:      str        # "READING" | "CAPTURED" | "WAITING_DEPARTURE" | "FLUSHED"
    user_mark:   str = ""   # "final" | "transition" | ""
    sequence_no: int = 0


class LiveReader:
    READ_INTERVAL = 0.08

    def __init__(self,
                 bbox:         dict,
                 min_val:      float,
                 max_val:      float,
                 target_count: int,
                 stable_secs:  float,
                 on_log_entry: Callable,
                 on_captured:  Callable,
                 on_done:      Callable,
                 on_status:    Callable,
                 tk_after:     Callable):
        self.bbox         = bbox
        self.min_val      = min_val
        self.max_val      = max_val
        self.target_count = target_count
        self.stable_secs  = stable_secs
        self.on_log_entry = on_log_entry
        self.on_captured  = on_captured
        self.on_done      = on_done
        self.on_status    = on_status
        self._after       = tk_after

        self._running = False
        self._paused  = False
        self._thread: threading.Thread | None = None

        self._last_logged:   float | None = None
        self._stable_value:  float | None = None
        self._stable_since:  float        = 0.0
        self._last_captured: float | None = None
        self._departed:      bool         = True
        self._count:         int          = 0

        # Vote buffer
        self._vote_buf: list[float] = []

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._paused  = False
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def pause(self):  self._paused = True
    def resume(self): self._paused = False

    def stop(self):
        self._running = False
        self._paused  = False

    @property
    def is_paused(self)  -> bool: return self._paused
    @property
    def is_running(self) -> bool: return self._running

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        import pytesseract
        self._emit_status(f"Reading…  0 / {self.target_count}")

        while self._running and self._count < self.target_count:

            if self._paused:
                time.sleep(0.1)
                continue

            # ── Multi-frame vote: collect VOTE_FRAMES reads, take majority
            value, raw = self._voted_read()

            # ── Out of range or unreadable → small drift tolerance
            if value is None or not (self.min_val <= value <= self.max_val):
                # Only reset stability if we had a large jump, not just a bad frame
                if value is not None and self._stable_value is not None:
                    if abs(value - self._stable_value) > (self.max_val - self.min_val) * 0.5:
                        self._stable_value = None  # genuine large jump
                    # else: ignore noisy frame, keep stability timer running
                else:
                    self._stable_value = None
                time.sleep(self.READ_INTERVAL)
                continue

            now = time.monotonic()

            # ── Frame-to-frame dedup
            if self._last_logged is None or abs(value - self._last_logged) > 0.02:
                self._last_logged = value

                if self._last_captured is not None and not self._departed:
                    status = "WAITING_DEPARTURE"
                elif (self._stable_value is not None
                      and abs(value - self._stable_value) <= 0.05
                      and (now - self._stable_since) >= self.stable_secs):
                    status = "CAPTURED"
                else:
                    status = "READING"

                entry = LogEntry(timestamp=now, value=value, raw_ocr=raw, status=status)
                self._after(0, lambda e=entry: self.on_log_entry(e))

            # ── Departure detection
            if self._last_captured is not None:
                if abs(value - self._last_captured) > DEPARTURE_DELTA:
                    self._departed = True

            # ── Stability tracking
            if self._stable_value is not None:
                drift = abs(value - self._stable_value)
                if drift <= 0.05:
                    # Steady — check if stable long enough
                    elapsed = now - self._stable_since
                    if elapsed >= self.stable_secs and self._departed:
                        self._do_capture(value, raw, now, "CAPTURED")
                elif drift <= SLOW_DRIFT:
                    # Slow drift — update stable value but DON'T reset timer
                    self._stable_value = value
                else:
                    # Large jump — reset
                    self._stable_value = value
                    self._stable_since = now
            else:
                self._stable_value = value
                self._stable_since = now

            time.sleep(self.READ_INTERVAL)

        # ── Flush: accept pending stable value if >= FLUSH_RATIO of stable_secs
        if self._stable_value is not None and self._departed:
            elapsed = time.monotonic() - self._stable_since
            if elapsed >= self.stable_secs * FLUSH_RATIO:
                self._do_capture(self._stable_value, "", time.monotonic(), "FLUSHED")

        if self._count >= self.target_count:
            self._emit_status(f"Set complete!  {self._count} values captured.")
            self._after(0, self.on_done)
        self._running = False

    # ── Capture helper ────────────────────────────────────────────────────────

    def _do_capture(self, value: float, raw: str, now: float, status: str):
        self._last_captured = value
        self._departed      = False
        self._count        += 1
        self._stable_value  = None
        entry = LogEntry(timestamp=now, value=value, raw_ocr=raw, status=status)
        self._after(0, lambda e=entry: self.on_log_entry(e))
        self._after(0, lambda v=value: self.on_captured(v))
        self._emit_status(f"Reading…  {self._count} / {self.target_count}")

    # ── Multi-frame voting ────────────────────────────────────────────────────

    def _voted_read(self) -> tuple[float | None, str]:
        """
        Take VOTE_FRAMES rapid reads and return the most common valid value.
        Falls back to single read if votes are all different.
        """
        from core.screen_capture import capture_region
        import pytesseract

        reads: list[tuple[float, str]] = []
        for _ in range(VOTE_FRAMES):
            img  = capture_region(self.bbox)
            proc = self._preprocess(img)
            # Try normal then inverted
            for use_inv in (False, True):
                src = proc if not use_inv else self._invert(proc)
                cfg = "--psm 7 -c tessedit_char_whitelist=0123456789."
                raw = pytesseract.image_to_string(src, config=cfg).strip()
                v   = self._parse(raw)
                if v is not None and self.min_val <= v <= self.max_val:
                    reads.append((v, raw))
                    break
            time.sleep(0.02)

        if not reads:
            return None, ""

        # Majority vote: find most common rounded value
        from collections import Counter
        counts = Counter(round(r[0], 2) for r in reads)
        winner = counts.most_common(1)[0][0]
        # Return the raw string from the first read matching the winner
        for v, raw in reads:
            if round(v, 2) == winner:
                return winner, raw
        return reads[0]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _preprocess(self, img):
        from PIL import ImageEnhance, ImageFilter
        w, h = img.size
        img  = img.resize((w * 4, h * 4))
        img  = img.convert("L")
        img  = ImageEnhance.Contrast(img).enhance(3.0)
        img  = img.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
        return img

    def _invert(self, img):
        from PIL import ImageOps
        return ImageOps.invert(img)

    def _parse(self, raw: str) -> float | None:
        import re
        m = re.search(r"\d+\.\d+", raw)
        if m:
            try: return float(m.group())
            except ValueError: pass
        m = re.search(r"\d+", raw)
        if m:
            try: return float(m.group())
            except ValueError: pass
        return None

    def _emit_status(self, msg: str):
        self._after(0, lambda m=msg: self.on_status(m))

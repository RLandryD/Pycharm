"""reporter/effort_model.py

The project effort/cost model that turns engine + intervention output into a
quotable estimate, with every component shown broken out.

Design (locked with the user):

    BASE        = SAP MA assessment of the SOURCE interfaces, BEFORE automation
                  (engine Mode 1 from a real MA export — primary path;
                   Mode 2 from PI/PO *source* artifacts — fallback only.
                   NEVER the finished CPI bundle, which is post-migration and
                   structurally understates the job.)
    + GAP HOURS = the intervention estimator's itemized manual tasks
                  (cert import, BPM redesign, partner setup, undocumented
                   research, …). These ARE the per-item research/testing hours,
                   itemized by real task rather than a flat per-item buffer.
    × MULTIPLIER  (1.0–3.0, 0.25 steps) — the pre-automation risk lever. Default
                  1.0 (honesty as baseline). A mode (Migration/Support/
                  Implementation) may SET this default; the slider then fine-
                  tunes. Mode → slider is one-directional: the slider never
                  changes the mode.
    + HYPERCARE   (OPTIONAL — off by default. Some clients use their own
                  consultants for hypercare, which would inflate the quote.
                  When on, a flat project-level hours add, editable.)

    = base expected effort (hours-primary; days = hours / hours_per_day).

The multiplier applies to (base + gaps) — the per-interface work — but NOT to
hypercare, which is a flat project add (folding hypercare into the multiplier
would over-inflate small projects and under-cover large ones).

Honest caveat: the constants here (mode default multipliers, hypercare hours)
are reasonable starting defaults from experience, NOT calibrated against a
portfolio of real project actuals. Every one is meant to be overridable, and
they should be refined as real data accrues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Multiplier bounds / step — the slider contract.
MULTIPLIER_MIN = 1.0
MULTIPLIER_MAX = 3.0
MULTIPLIER_STEP = 0.25
MULTIPLIER_DEFAULT = 1.0

# Mode → default multiplier. Mode SETS the slider's starting value; the slider
# then overrides. Directionally Migration < Support < Implementation:
#   Migration       — converting existing interfaces; design already exists;
#                     lowest uncertainty. Trust the engine baseline.
#   Support         — troubleshooting; unpredictable per issue but small scope.
#   Implementation  — greenfield build; highest effort vs any automated
#                     baseline (the "build from nothing" world).
MODE_DEFAULT_MULTIPLIER = {
    "Migration":      1.0,
    "Support":        1.75,
    "Implementation": 2.75,
}

# Default flat hypercare hours when the OPTIONAL hypercare line is enabled.
# ~1.5 weeks at typical engagement intensity. Editable.
HYPERCARE_DEFAULT_HOURS = 60.0

DEFAULT_HOURS_PER_DAY = 8.0


def snap_multiplier(value: float) -> float:
    """Clamp to [MIN, MAX] and snap to the nearest STEP."""
    v = max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, float(value)))
    steps = round((v - MULTIPLIER_MIN) / MULTIPLIER_STEP)
    return round(MULTIPLIER_MIN + steps * MULTIPLIER_STEP, 2)


def default_multiplier_for_mode(mode: Optional[str]) -> float:
    """The slider's starting value for a given project mode (snapped)."""
    if not mode:
        return MULTIPLIER_DEFAULT
    return snap_multiplier(MODE_DEFAULT_MULTIPLIER.get(mode, MULTIPLIER_DEFAULT))


@dataclass
class EffortBreakdown:
    """A fully itemized, quotable effort estimate — every component visible."""
    # Inputs / context
    multiplier: float = 1.0
    mode: str = ""
    hypercare_enabled: bool = False
    hypercare_hours: float = 0.0
    hours_per_day: float = DEFAULT_HOURS_PER_DAY

    # Base = engine MA assessment (before automation), a low/high range.
    base_hours_low: float = 0.0
    base_hours_high: float = 0.0

    # Gap hours = itemized intervention manual tasks (your side only — client
    # hours are tracked separately and not multiplied by your risk lever).
    gap_hours: float = 0.0
    client_hours: float = 0.0   # carried through for transparency, not costed

    @property
    def base_plus_gap_low(self) -> float:
        return self.base_hours_low + self.gap_hours

    @property
    def base_plus_gap_high(self) -> float:
        return self.base_hours_high + self.gap_hours

    # Multiplier applies to (base + gap), NOT to hypercare.
    @property
    def adjusted_low(self) -> float:
        return self.base_plus_gap_low * self.multiplier

    @property
    def adjusted_high(self) -> float:
        return self.base_plus_gap_high * self.multiplier

    @property
    def hypercare_add(self) -> float:
        return self.hypercare_hours if self.hypercare_enabled else 0.0

    # Totals (hours-primary)
    @property
    def total_low(self) -> float:
        return self.adjusted_low + self.hypercare_add

    @property
    def total_high(self) -> float:
        return self.adjusted_high + self.hypercare_add

    # Optional days view
    @property
    def total_days_low(self) -> float:
        return self.total_low / self.hours_per_day if self.hours_per_day else 0.0

    @property
    def total_days_high(self) -> float:
        return self.total_high / self.hours_per_day if self.hours_per_day else 0.0

    def as_lines(self) -> list[tuple[str, str]]:
        """Human-readable broken-out lines (label, value) for display."""
        def hr(lo, hi):
            return f"{lo:.0f}–{hi:.0f}h" if abs(hi - lo) > 0.05 else f"{lo:.0f}h"
        lines = [
            ("Base (MA assessment, before automation)",
             hr(self.base_hours_low, self.base_hours_high)),
            ("+ Gap / research hours (itemized manual tasks)",
             f"{self.gap_hours:.0f}h"),
            ("= Base + gaps",
             hr(self.base_plus_gap_low, self.base_plus_gap_high)),
            (f"× Multiplier ({self.multiplier:.2f}×"
             + (f", {self.mode} default" if self.mode else "") + ")",
             hr(self.adjusted_low, self.adjusted_high)),
        ]
        if self.hypercare_enabled:
            lines.append((f"+ Hypercare (flat, optional)",
                          f"{self.hypercare_hours:.0f}h"))
        lines.append(("= TOTAL (your effort)",
                      hr(self.total_low, self.total_high)))
        lines.append(("  in days (÷ %.0fh)" % self.hours_per_day,
                      f"{self.total_days_low:.1f}–{self.total_days_high:.1f}d"
                      if abs(self.total_days_high - self.total_days_low) > 0.05
                      else f"{self.total_days_low:.1f}d"))
        if self.client_hours:
            lines.append(("(Client-side hours, not in your total)",
                          f"{self.client_hours:.0f}h"))
        return lines


def build_effort(
    base_hours_low: float,
    base_hours_high: float,
    gap_hours: float,
    client_hours: float = 0.0,
    multiplier: float = MULTIPLIER_DEFAULT,
    mode: str = "",
    hypercare_enabled: bool = False,
    hypercare_hours: float = HYPERCARE_DEFAULT_HOURS,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
) -> EffortBreakdown:
    """Assemble an EffortBreakdown from the component inputs.

    base_*  : engine MA assessment hours (before automation), low/high.
    gap_hours: summed itemized manual-task hours (your side).
    multiplier: 1.0–3.0; snapped to the 0.25 grid.
    mode    : optional label, recorded for display (does not re-derive the
              multiplier here — the caller decides whether to seed from mode).
    """
    return EffortBreakdown(
        multiplier=snap_multiplier(multiplier),
        mode=mode or "",
        hypercare_enabled=hypercare_enabled,
        hypercare_hours=float(hypercare_hours),
        hours_per_day=float(hours_per_day),
        base_hours_low=float(base_hours_low),
        base_hours_high=float(base_hours_high),
        gap_hours=float(gap_hours),
        client_hours=float(client_hours),
    )

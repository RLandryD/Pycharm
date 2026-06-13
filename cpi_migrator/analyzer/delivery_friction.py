"""analyzer/delivery_friction.py — the two-axis effort model.

Why two axes (agreed 2026-06-11): single-number estimators collapse two
different quantities and get both wrong. The user's own data: RCI093
(engine weight 170, "M") took 4 MONTHS with a client — third party
(OpenText), test-data gaps, business validation, cert procurement — while
12-step "monsters" with credentials take under a week when none of that
friction exists. Step count predicts neither.

  Axis 1 — BUILD EFFORT (days): artifact-derived. days = weight × coeff.
           Default coeff 0.1 day/weight-point (RCI093: weight 170 → ~17
           build-days ≈ 3.5 weeks, matching the user's recollection that
           the pure build portion of the 4 months was ~3-4 weeks).
           Recalibrated from logged actuals — the flywheel.

  Axis 2 — DELIVERY FRICTION (multiplier on calendar): consultant
           enrichment no scan can see. 30 seconds per interface.

  calendar_weeks = build_days × Π(friction multipliers) / workdays_per_week
                   + wave overhead (governance/cutover, amortized per wave)

PIMAS/SAP reference numbers stay in reports as the client-trusted
vocabulary; THIS model is the planning truth, and it improves with every
engagement via record_actual().
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("analyzer.delivery_friction")

# ── Axis 2: friction factors ────────────────────────────────────────────────
# {factor: {answer: multiplier}} — first answer is the default.
FRICTION_FACTORS = {
    "external_parties": {          # the RCI093 killer
        "none": 1.0, "one": 2.0, "two_plus": 3.0},
    "business_criticality": {      # validation gauntlet depth
        "low": 1.0, "medium": 1.3, "high": 1.7},
    "test_data": {                 # waiting for fixtures
        "available": 1.0, "partial": 1.4, "must_be_created": 1.8},
    "environments": {              # transport ceremony per env
        "one": 1.0, "two": 1.2, "three_plus": 1.4},
    "data_sensitivity": {          # privacy/security review loops
        "normal": 1.0, "sensitive": 1.3},
}

DEFAULT_BUILD_COEFF = 0.1          # days per weight point
DEFAULT_WAVE_OVERHEAD_WEEKS = 1.0  # governance + cutover per wave
WORKDAYS_PER_WEEK = 5.0


@dataclass
class FrictionProfile:
    """One interface's consultant enrichment (Axis 2 answers)."""
    external_parties: str = "none"
    business_criticality: str = "low"
    test_data: str = "available"
    environments: str = "one"
    data_sensitivity: str = "normal"

    def multiplier(self) -> float:
        m = 1.0
        for factor, table in FRICTION_FACTORS.items():
            ans = getattr(self, factor, None)
            if ans not in table:
                logger.warning("unknown answer %r for %s; using default",
                               ans, factor)
                ans = next(iter(table))
            m *= table[ans]
        return round(m, 3)


@dataclass
class EffortEstimate:
    interface: str
    weight: int
    build_days: float              # Axis 1
    friction_multiplier: float     # Axis 2
    calendar_weeks: float          # the combined answer
    coeff_used: float
    profile: dict = field(default_factory=dict)


def estimate(interface: str, weight: int,
             profile: FrictionProfile | None = None,
             coeff: float | None = None,
             wave_overhead_weeks: float = DEFAULT_WAVE_OVERHEAD_WEEKS,
             ) -> EffortEstimate:
    """The two-axis estimate for one interface."""
    profile = profile or FrictionProfile()
    coeff = coeff if coeff is not None else DEFAULT_BUILD_COEFF
    build_days = round(max(0, weight) * coeff, 1)
    mult = profile.multiplier()
    calendar = round(build_days * mult / WORKDAYS_PER_WEEK
                     + wave_overhead_weeks, 1)
    return EffortEstimate(interface=interface, weight=weight,
                          build_days=build_days, friction_multiplier=mult,
                          calendar_weeks=calendar, coeff_used=coeff,
                          profile=asdict(profile))


# ── the calibration flywheel ───────────────────────────────────────────────
@dataclass
class ActualRecord:
    interface: str
    weight: int
    actual_build_days: float
    actual_calendar_weeks: float = 0.0
    profile: dict = field(default_factory=dict)
    note: str = ""


class CalibrationStore:
    """Logged actuals + the derived build coefficient. JSON on disk so it
    persists across engagements — every project sharpens the next one."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.expanduser(
            "~/.cpi_migrator/effort_calibration.json")
        self.records: list = []
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path) as fh:
                    self.records = [ActualRecord(**r)
                                    for r in json.load(fh).get("records", [])]
        except Exception as exc:
            logger.warning("calibration load failed (%s); starting empty",
                           exc)
            self.records = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as fh:
                json.dump({"records": [asdict(r) for r in self.records]},
                          fh, indent=1)
        except Exception as exc:
            logger.warning("calibration save failed: %s", exc)

    def record_actual(self, rec: ActualRecord):
        self.records.append(rec)
        self._save()
        logger.info("actual recorded for %s: %.1f build-days at weight %d "
                    "(coeff now %.4f over %d records)", rec.interface,
                    rec.actual_build_days, rec.weight,
                    self.build_coeff(), len(self.records))

    def build_coeff(self) -> float:
        """Weight-weighted mean of actual days/weight over all records.
        Falls back to the default until real data exists."""
        usable = [r for r in self.records
                  if r.weight > 0 and r.actual_build_days > 0]
        if not usable:
            return DEFAULT_BUILD_COEFF
        tot_days = sum(r.actual_build_days for r in usable)
        tot_weight = sum(r.weight for r in usable)
        return round(tot_days / tot_weight, 4)

    def n_records(self) -> int:
        return len(self.records)


def estimate_calibrated(interface: str, weight: int,
                        profile: FrictionProfile | None = None,
                        store: CalibrationStore | None = None,
                        **kw) -> EffortEstimate:
    """estimate() using the store's learned coefficient."""
    store = store or CalibrationStore()
    return estimate(interface, weight, profile=profile,
                    coeff=store.build_coeff(), **kw)

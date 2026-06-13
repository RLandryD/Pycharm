"""analyzer/ma_assessments.py — THE assessment entry point (2026-06-11).

Two models, one adapter, no third opinion:

MODE 1 — MA files: completely loyal. When a record carries real SAP MA
figures (ma_weight from an imported export), the size comes from SAP's
weight bands, the category from SAP's Migration Status, and the effort is
THE FILE'S OWN Est. Effort hours verbatim (engine table only when the file
lacks an effort column). Nothing is re-scored.

MODE 2 — packages / iflows / PI extracts: the SAP rules engine — derive
signals from the artifact (steps, senders, receivers, mappings, modules…),
fire the real MA rules against them ('1 sender' is compared against the
missing-sender rule and so on), sum the rule weights, size + effort from
the scaling weight.

The legacy LOW/MEDIUM/HIGH ComplexityAnalyzer is DEPRECATED: no live code
path may call it. Its 3-band label survives only as a derived display
alias (S→LOW, M→MEDIUM, L/XL→HIGH) on the assessment for old UI spots.
"""
from __future__ import annotations

import logging

from analyzer.complexity_analyzer import MigrationAssessment
from analyzer.sap_complexity_engine import SAPComplexityEngine

logger = logging.getLogger("analyzer.ma_assessments")

_ENGINE: SAPComplexityEngine | None = None


def _engine() -> SAPComplexityEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = SAPComplexityEngine()
    return _ENGINE


_PATTERN_BY_SIZE = {
    "S": "Point-to-point (direct conversion)",
    "M": "Content-based routing / mapping pipeline",
    "L": "Staged pipeline with explicit error handling",
    "XL": "Decomposed multi-iFlow architecture",
}


def assess_records(records, log_summary: bool = True):
    """Records → list[MigrationAssessment], MA-model only."""
    eng = _engine()
    out = []
    for rec in records:
        ma_weight = getattr(rec, "ma_weight", None)
        if ma_weight is not None:
            r = eng.assess_true_ma(
                getattr(rec, "name", ""), weight=int(ma_weight),
                size=getattr(rec, "ma_size", "") or "",
                category=getattr(rec, "ma_status", "") or "")
            raw = getattr(rec, "raw", None) or {}
            file_hrs = raw.get("sap_ma_effort_hrs") or 0.0
            # LOYALTY RULE: the file's Est. Effort wins over any table
            eff_days = round(float(file_hrs) / 8.0, 2) if file_hrs \
                else r.effort_days_avg
            notes = [f"SAP MA: weight {r.total_weight} · size {r.size} · "
                     f"{getattr(rec, 'ma_status', '') or r.category}"
                     + (f" · {file_hrs:g} Hrs (from file)" if file_hrs
                        else " (effort from engine table — file had no "
                             "effort column)")]
            reasoning = notes[:]
        else:
            r = eng.assess_interface(rec)
            eff_days = r.effort_days_avg
            notes = [f"{f.rule} (+{f.weight}): {f.signal_note or f.matched_value}"
                     for f in r.fired_rules] or \
                ["no MA rules fired — plain pipeline"]
            reasoning = ([f"signals → {len(r.fired_rules)} rule(s) fired, "
                          f"scaling weight {r.total_weight} → size {r.size}"]
                         + notes)
        a = MigrationAssessment(
            interface=rec, score=r.total_weight,
            complexity=r.legacy_complexity,        # display alias ONLY
            effort_days=eff_days, notes=notes,
            recommended_pattern=_PATTERN_BY_SIZE.get(r.size, ""),
            reasoning=reasoning)
        a.ma_size = r.size
        a.ma_weight = r.total_weight
        a.ma_category = r.category
        a.ma_mode = r.mode
        a.ma_effort_hours = round(eff_days * 8.0, 1)
        out.append(a)
    if log_summary and out:
        sizes = {"S": 0, "M": 0, "L": 0, "XL": 0}
        for a in out:
            sizes[a.ma_size] = sizes.get(a.ma_size, 0) + 1
        total_h = round(sum(a.ma_effort_hours for a in out), 1)
        mode = ("MA file (loyal)" if all(a.ma_mode == "true_ma" for a in out)
                else "rules engine" if all(a.ma_mode == "signal" for a in out)
                else "mixed")
        logger.info(
            "MA assessment [%s] — S: %d | M: %d | L: %d | XL: %d | "
            "Total effort: %.1f hrs (%.1f days)", mode, sizes["S"],
            sizes["M"], sizes["L"], sizes["XL"], total_h, total_h / 8.0)
    return out

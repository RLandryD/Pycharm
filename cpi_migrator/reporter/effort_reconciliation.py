"""
reporter/effort_reconciliation.py

Compares the tool's own effort estimate against SAP Migration Assessment's
effort numbers, per interface, and flags where they diverge.

Why: the workbench computes effort from its complexity model; SAP MA computes
effort from its own rules + T-shirt sizing. When they disagree significantly,
that's exactly the conversation a consultant wants to have with a client
("the tool says 3 days, SAP says 8 — here's why"). This surfaces those gaps
instead of silently picking one number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EffortComparison:
    interface_name: str
    tool_days: float
    sap_hours: float            # from SAP MA (per-interface)
    sap_days: float             # sap_hours / 8
    delta_days: float           # tool - sap (positive = tool higher)
    divergence_pct: float       # |delta| / max(both) * 100
    flag: str                   # "aligned" | "tool_higher" | "sap_higher"
    note: str = ""


@dataclass
class ReconciliationReport:
    comparisons: list[EffortComparison] = field(default_factory=list)
    tool_total_days: float = 0.0
    sap_total_days: float = 0.0

    @property
    def divergent(self) -> list[EffortComparison]:
        return [c for c in self.comparisons if c.flag != "aligned"]

    def summary(self) -> dict:
        return {
            "interfaces":     len(self.comparisons),
            "aligned":        sum(1 for c in self.comparisons if c.flag == "aligned"),
            "tool_higher":    sum(1 for c in self.comparisons if c.flag == "tool_higher"),
            "sap_higher":     sum(1 for c in self.comparisons if c.flag == "sap_higher"),
            "tool_total_days": round(self.tool_total_days, 1),
            "sap_total_days":  round(self.sap_total_days, 1),
            "total_delta_days": round(self.tool_total_days - self.sap_total_days, 1),
        }


def reconcile(assessments: list, ma_report=None,
              divergence_threshold_pct: float = 30.0,
              hours_per_day: int = 8) -> ReconciliationReport:
    """Build a reconciliation report.

    assessments : the tool's MigrationAssessment list (have .effort_days)
    ma_report   : a parsed SAPMAReport (interfaces carry raw sap_ma_effort_hrs)
    divergence_threshold_pct : above this, flag as divergent
    """
    report = ReconciliationReport()

    # Index SAP MA effort by a normalised interface name
    sap_hours_by_name: dict[str, float] = {}
    if ma_report is not None:
        for rec in getattr(ma_report, "interfaces", []):
            raw = getattr(rec, "raw", {}) or {}
            hrs = raw.get("sap_ma_effort_hrs", 0) or 0
            sap_hours_by_name[_norm(rec.name)] = float(hrs)

    for a in assessments:
        name = a.interface.name
        tool_days = float(getattr(a, "effort_days", 0) or 0)
        sap_hours = sap_hours_by_name.get(_norm(name), 0.0)
        sap_days  = sap_hours / hours_per_day if sap_hours else 0.0

        report.tool_total_days += tool_days
        report.sap_total_days  += sap_days

        if sap_hours == 0:
            # No SAP number to compare against
            report.comparisons.append(EffortComparison(
                interface_name=name, tool_days=tool_days, sap_hours=0,
                sap_days=0, delta_days=tool_days, divergence_pct=0,
                flag="aligned", note="No SAP MA effort to compare"))
            continue

        delta = tool_days - sap_days
        denom = max(tool_days, sap_days, 0.1)
        div_pct = abs(delta) / denom * 100

        if div_pct <= divergence_threshold_pct:
            flag, note = "aligned", ""
        elif delta > 0:
            flag = "tool_higher"
            note = f"Tool estimate {div_pct:.0f}% higher than SAP MA"
        else:
            flag = "sap_higher"
            note = f"SAP MA estimate {div_pct:.0f}% higher than tool"

        report.comparisons.append(EffortComparison(
            interface_name=name, tool_days=round(tool_days, 1),
            sap_hours=round(sap_hours, 1), sap_days=round(sap_days, 1),
            delta_days=round(delta, 1), divergence_pct=round(div_pct, 1),
            flag=flag, note=note))

    return report


def _norm(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

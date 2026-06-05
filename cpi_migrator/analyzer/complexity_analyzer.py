"""
analyzer/complexity_analyzer.py

.. deprecated::
    This LOW/MEDIUM/HIGH→days scorer is SUPERSEDED by
    ``analyzer.sap_complexity_engine`` (SAP MA-faithful, hours-based) combined
    with ``reporter.effort_model`` (project multiplier + optional hypercare).
    It remains in place ONLY because the live workbench / report / scaffolder
    flow still imports it. Do NOT build new features on this scorer. The swap
    to the new engine is scheduled for the workbench-wiring session, where the
    UI can be visually validated. New code should use the engine + effort model.

Scores each InterfaceRecord for migration complexity and effort,
producing a MigrationAssessment per interface.

Scoring model
─────────────
Each factor adds points. Thresholds (configurable) map the total
score to LOW / MEDIUM / HIGH, then to an effort estimate in days.

Factor                         Points
─────────────────────────────────────
Sender adapter complexity        0-10
Receiver adapter complexity      0-10
Has BPM / ccBPM                 +15
Has multi-mapping               + 8
Has message mapping             + 5
Channel count (each extra)      + 2
Non-standard adapter (unknown)  + 5
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from extractor.pi_extractor import InterfaceRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adapter complexity scores  (higher = harder to migrate)
# ---------------------------------------------------------------------------

ADAPTER_SCORES = {
    "HTTPS":          1,
    "HTTP":           1,
    "SOAP":           3,
    "REST":           2,
    "File":           3,
    "FTP":            3,
    "SFTP":           3,
    "IDoc":           5,
    "RFC":            7,
    "JDBC":           6,
    "JMS":            4,
    "Mail":           3,
    "OData":          4,
    "AS2":            6,
    "AS4":            6,
    "ProcessDirect":  2,
    "XI":             2,
}

MIGRATION_NOTES = {
    "RFC":   "RFC → OData/SOAP conversion needed; BAPI calls require CPI RFC adapter or wrapping.",
    "JDBC":  "JDBC requires CPI JDBC adapter configuration and DB firewall rules.",
    "IDoc":  "IDoc requires SAP system partner profile setup in CPI.",
    "BPM":   "BPM/ccBPM has no direct CPI equivalent; redesign as multi-step iFlow.",
    "AS2":   "AS2 requires B2B add-on or Integration Advisor trading partner setup.",
    "AS4":   "AS4 requires B2B add-on or Integration Advisor trading partner setup.",
}


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class MigrationAssessment:
    interface: InterfaceRecord
    score: int
    complexity: str          # "LOW", "MEDIUM", "HIGH"
    effort_days: float
    notes: list[str]
    recommended_pattern: str  # suggested CPI iFlow pattern
    reasoning: list[str] = None  # human-readable explanation of score

    def __post_init__(self):
        if self.reasoning is None:
            self.reasoning = []


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class ComplexityAnalyzer:

    def __init__(self, cfg: dict):
        migration_cfg = cfg.get("migration", {})
        thresholds = migration_cfg.get("complexity_thresholds", {})
        effort = migration_cfg.get("effort_days", {})

        self.low_max    = thresholds.get("low",    {}).get("max_score", 10)
        self.medium_max = thresholds.get("medium", {}).get("max_score", 25)

        self.effort_low    = effort.get("low",    1)
        self.effort_medium = effort.get("medium", 3)
        self.effort_high   = effort.get("high",   8)
        # Per-point effort added for each score point ABOVE medium_max, so HIGH
        # no longer flat-lines (a 'monster' outscores an 'XL' in days too).
        # Heuristic; a calibrated figure needs a real MA export (engine Mode 1).
        self.effort_high_per_point = effort.get("high_per_point", 0.3)

    def assess(self, record: InterfaceRecord) -> MigrationAssessment:
        score = 0
        notes = []

        # Adapter scores
        sa_score = ADAPTER_SCORES.get(record.sender_adapter, 5)
        ra_score = ADAPTER_SCORES.get(record.receiver_adapter, 5)
        score += sa_score + ra_score

        if record.sender_adapter not in ADAPTER_SCORES:
            score += 5
            notes.append(f"Unknown sender adapter '{record.sender_adapter}' — manual review needed.")
        if record.receiver_adapter not in ADAPTER_SCORES:
            score += 5
            notes.append(f"Unknown receiver adapter '{record.receiver_adapter}' — manual review needed.")

        # BPM
        if record.has_bpm:
            score += 15
            notes.append(MIGRATION_NOTES["BPM"])

        # Multi-mapping
        if record.has_multi_mapping:
            score += 8
            notes.append("Multi-mapping detected — consider Multicast or Router step in CPI.")

        # Message mapping
        if record.mapping_program:
            score += 5

        # Extra channels
        extra_channels = max(0, record.channel_count - 1)
        score += extra_channels * 2

        # Specific adapter notes
        for adapter in [record.sender_adapter, record.receiver_adapter]:
            if adapter in MIGRATION_NOTES and MIGRATION_NOTES[adapter] not in notes:
                notes.append(MIGRATION_NOTES[adapter])

        # Build reasoning
        reasoning = []
        reasoning.append(f"Sender adapter '{record.sender_adapter}' scored {sa_score} pt(s) "
                         f"({'complex' if sa_score >= 5 else 'simple'} adapter type).")
        reasoning.append(f"Receiver adapter '{record.receiver_adapter}' scored {ra_score} pt(s) "
                         f"({'complex' if ra_score >= 5 else 'simple'} adapter type).")
        if record.has_bpm:
            reasoning.append("BPM/ccBPM process +15 pts: no direct CPI equivalent, requires full redesign.")
        if record.has_multi_mapping:
            reasoning.append("Multi-mapping +8 pts: fan-out logic adds migration complexity.")
        if record.mapping_program:
            reasoning.append(f"Message mapping '{record.mapping_program}' +5 pts: must be re-implemented in CPI.")
        if extra_channels > 0:
            reasoning.append(f"{extra_channels} extra channel(s) +{extra_channels*2} pts: each adds configuration effort.")
        if record.sender_adapter not in ADAPTER_SCORES:
            reasoning.append(f"Unknown sender adapter '{record.sender_adapter}' +5 pts: needs manual investigation.")
        if record.receiver_adapter not in ADAPTER_SCORES:
            reasoning.append(f"Unknown receiver adapter '{record.receiver_adapter}' +5 pts: needs manual investigation.")

        # Complexity band
        if score <= self.low_max:
            complexity = "LOW"
            effort_days = self.effort_low
            reasoning.append(f"Total score {score} ≤ {self.low_max}: LOW complexity — standard migration, minimal customisation.")
        elif score <= self.medium_max:
            complexity = "MEDIUM"
            effort_days = self.effort_medium
            reasoning.append(f"Total score {score} ({self.low_max+1}–{self.medium_max}): MEDIUM complexity — some redesign or mapping work needed.")
        else:
            complexity = "HIGH"
            # Scale with how far past the HIGH threshold the score sits, so a
            # 'monster' (every pattern) costs more days than a borderline-HIGH
            # iFlow instead of both pinning at the bucket value.
            over = max(0, score - self.medium_max)
            effort_days = round(self.effort_high + over * self.effort_high_per_point, 1)
            reasoning.append(
                f"Total score {score} > {self.medium_max}: HIGH complexity — "
                f"effort scales with score ({self.effort_high}d base + {over}"
                f"×{self.effort_high_per_point}/pt = {effort_days}d).")

        pattern = self._recommend_pattern(record)

        return MigrationAssessment(
            interface=record,
            score=score,
            complexity=complexity,
            effort_days=effort_days,
            notes=notes,
            recommended_pattern=pattern,
            reasoning=reasoning,
        )

    def assess_all(self, records: list[InterfaceRecord]) -> list[MigrationAssessment]:
        assessments = [self.assess(r) for r in records]
        low    = sum(1 for a in assessments if a.complexity == "LOW")
        medium = sum(1 for a in assessments if a.complexity == "MEDIUM")
        high   = sum(1 for a in assessments if a.complexity == "HIGH")
        total_days = sum(a.effort_days for a in assessments)
        logger.info(
            "Assessment summary — LOW: %d | MEDIUM: %d | HIGH: %d | Total effort: %.1f days",
            low, medium, high, total_days,
        )
        return assessments

    @staticmethod
    def _recommend_pattern(record: InterfaceRecord) -> str:
        """Map adapter combination to a CPI iFlow design pattern."""
        sa = record.sender_adapter
        ra = record.receiver_adapter

        if record.has_bpm:
            return "Process Orchestration (redesign as sequential/parallel iFlow with local subprocess)"
        if sa in ("AS2", "AS4") or ra in ("AS2", "AS4"):
            return "B2B Integration (Integration Advisor + Trading Partner)"
        if sa == "IDoc" or ra == "IDoc":
            return "IDoc Receiver / Sender Adapter"
        if sa == "RFC" or ra == "RFC":
            return "RFC → SOAP/OData Bridge"
        if sa == "JDBC" or ra == "JDBC":
            return "Database Integration (JDBC Adapter)"
        if sa in ("File", "FTP", "SFTP") and ra in ("File", "FTP", "SFTP"):
            return "File-to-File Transfer"
        if sa in ("File", "FTP", "SFTP"):
            return "File Sender → Service Call"
        if ra in ("File", "FTP", "SFTP"):
            return "Service Call → File Drop"
        return "Point-to-Point HTTP/SOAP"

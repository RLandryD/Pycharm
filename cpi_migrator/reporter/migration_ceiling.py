"""
reporter/migration_ceiling.py

Classifies each interface into one of three migration tiers:

  🟢 AUTO      — Tool generates production-ready iFlow, minimal review needed
  🟡 GUIDED    — Tool generates scaffold, consultant fills logic (standard work)
  🔴 SPECIALIST — Needs senior architect input; client decision required

Outputs:
  - MigrationCeiling per interface with tier, blockers, options, cost impact
  - Summary report (Excel + Word) with client-facing and internal versions
  - Decision matrix for client sign-off
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIER_AUTO       = "AUTO"
TIER_GUIDED     = "GUIDED"
TIER_SPECIALIST = "SPECIALIST"

TIER_EMOJI = {
    TIER_AUTO:       "🟢",
    TIER_GUIDED:     "🟡",
    TIER_SPECIALIST: "🔴",
}

TIER_COLOURS = {
    TIER_AUTO:       "C6EFCE",   # green
    TIER_GUIDED:     "FFEB9C",   # amber
    TIER_SPECIALIST: "FFC7CE",   # red
}

# ---------------------------------------------------------------------------
# Specialist triggers
# ---------------------------------------------------------------------------

@dataclass
class CeilingTrigger:
    code: str
    description: str
    reason: str           # why it needs a specialist
    options: list[str]    # what client/consultant can decide
    extra_days: tuple     # (min_days, max_days) additional effort
    extra_cost_usd: tuple # (min_usd, max_usd) additional cost range


SPECIALIST_TRIGGERS: list[CeilingTrigger] = [
    CeilingTrigger(
        code="BPM",
        description="BPM / ccBPM orchestration process",
        reason="No direct CPI equivalent. Requires full architectural redesign "
               "as sequential iFlow steps or SAP BTP Workflow Service.",
        options=[
            "Redesign as CPI iFlow process steps (senior architect, +3-5d)",
            "Replace with SAP BTP Workflow Service (separate project scope)",
            "Defer — keep on PI/PO until S/4HANA migration",
            "Retire — evaluate if this process is still needed",
        ],
        extra_days=(3, 5),
        extra_cost_usd=(3000, 6000),
    ),
    CeilingTrigger(
        code="JAVA_BINARY",
        description="Java mapping with binary/file-system operations",
        reason="Java wrapper pattern fails for PDF attachments, Office documents, "
               "file-system I/O, and cryptographic keystore operations. "
               "Must be rewritten natively in Groovy.",
        options=[
            "Rewrite mapping in native Groovy (senior developer, +2-4d per mapping)",
            "Use SAP Integration Advisor for schema mapping if structure is standard",
            "Scope out of migration — keep on PI/PO",
        ],
        extra_days=(2, 4),
        extra_cost_usd=(2000, 5000),
    ),
    CeilingTrigger(
        code="UNDOCUMENTED",
        description="Undocumented interface — no mapping program, no description",
        reason="Cannot migrate what is not understood. Requires reverse-engineering "
               "the original interface logic before migration can begin.",
        options=[
            "Discovery workshop with client SME to document interface (+1-2d)",
            "Extract and analyze historical PI/PO payloads to infer mapping logic",
            "Descope if interface has low usage or no business owner",
        ],
        extra_days=(1, 2),
        extra_cost_usd=(1000, 2500),
    ),
    CeilingTrigger(
        code="EOIO_HIGH_VOLUME",
        description="EOIO ordering + high channel count",
        reason="DataStore staging pattern requires performance tuning and "
               "load testing at production volumes. Incorrect sizing causes "
               "message backlog in production.",
        options=[
            "Performance test DataStore pattern at expected peak volume (+1-2d)",
            "Use Advanced Event Mesh with FIFO queue ordering instead",
        ],
        extra_days=(1, 2),
        extra_cost_usd=(1000, 3000),
    ),
    CeilingTrigger(
        code="B2B_COMPLEX",
        description="AS2/AS4 with complex EDI schemas (EDIFACT/X12)",
        reason="B2B EDI migration requires Integration Advisor expertise, "
               "Trading Partner Management setup, and partner coordination. "
               "Outside standard PI/PO migration scope.",
        options=[
            "Engage B2B/EDI specialist for Integration Advisor setup (+3-5d)",
            "Use SAP pre-built EDI content from Accelerator Hub if available",
            "Scope as separate B2B workstream with dedicated timeline",
        ],
        extra_days=(3, 5),
        extra_cost_usd=(3000, 7000),
    ),
    CeilingTrigger(
        code="MULTI_BPM",
        description="Multi-mapping + BPM combined",
        reason="Two HIGH complexity factors together. Fan-out orchestration "
               "inside a BPM process requires complete redesign of both "
               "the routing logic and the mapping architecture.",
        options=[
            "Full redesign as Pipeline Concept + Multicast pattern (+4-6d)",
            "Split into multiple simpler interfaces if business allows",
            "Escalate to integration architect for design review first",
        ],
        extra_days=(4, 6),
        extra_cost_usd=(4000, 8000),
    ),
    CeilingTrigger(
        code="SCORE_EXTREME",
        description="Complexity score > 35",
        reason="Interface exceeds the complexity ceiling for standard migration. "
               "Multiple interacting factors create unpredictable migration risk.",
        options=[
            "Senior architect review before migration start (+1d scoping)",
            "Break interface into smaller, simpler sub-interfaces",
            "Greenfield redesign using SAP standard content as baseline",
        ],
        extra_days=(1, 3),
        extra_cost_usd=(1500, 4000),
    ),
    CeilingTrigger(
        code="RFC_JDBC",
        description="RFC + JDBC adapters combined",
        reason="Two Clean Core blockers in one interface. Both require "
               "architectural substitution (OData APIs + Data Source config) "
               "and infrastructure work on both ends.",
        options=[
            "Architect OData API replacement for RFC + JDBC layer (+2-3d)",
            "Raise as Clean Core quality gate blocker with client architect",
        ],
        extra_days=(2, 3),
        extra_cost_usd=(2000, 4000),
    ),
    CeilingTrigger(
        code="UNKNOWN_ADAPTER",
        description="Unknown or custom adapter type",
        reason="Tool cannot generate any configuration for adapters not in "
               "the standard SAP CPI adapter set. Requires investigation of "
               "the original adapter and finding a CPI equivalent.",
        options=[
            "Identify original adapter and map to CPI equivalent adapter (+1d)",
            "Check if Open Connectors has a pre-built connector",
            "Build custom adapter using OData/REST wrapper if no standard exists",
        ],
        extra_days=(1, 3),
        extra_cost_usd=(1000, 3000),
    ),
    CeilingTrigger(
        code="NO_ADDRESS",
        description="Missing sender and receiver addresses",
        reason="Cannot generate working connectivity configuration without "
               "endpoint URLs. Interface will deploy but immediately fail.",
        options=[
            "Gather endpoint details from client system documentation (+0.5d)",
            "Schedule technical workshop with client Basis team",
        ],
        extra_days=(0, 1),
        extra_cost_usd=(500, 1000),
    ),
]

# Index triggers by code for fast lookup
TRIGGER_INDEX = {t.code: t for t in SPECIALIST_TRIGGERS}

# Score threshold above which GUIDED becomes SPECIALIST
SPECIALIST_SCORE_THRESHOLD = 35

# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class MigrationCeiling:
    interface_name: str
    tier: str                                    # AUTO / GUIDED / SPECIALIST
    score: int
    complexity: str                              # LOW / MEDIUM / HIGH
    triggered_by: list[CeilingTrigger]          = field(default_factory=list)
    options: list[str]                           = field(default_factory=list)
    extra_days_min: int                          = 0
    extra_days_max: int                          = 0
    extra_cost_min_usd: int                      = 0
    extra_cost_max_usd: int                      = 0
    automation_pct: int                          = 0   # % the tool can handle
    manual_tasks: list[str]                      = field(default_factory=list)
    client_decision_required: bool               = False

    @property
    def emoji(self) -> str:
        return TIER_EMOJI.get(self.tier, "⚪")

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.tier}"

    def summary_card(self) -> str:
        lines = [
            f"Interface : {self.interface_name}",
            f"Tier      : {self.label}",
            f"Score     : {self.score} ({self.complexity})",
            f"Automation: ~{self.automation_pct}%",
        ]
        if self.triggered_by:
            lines.append("Blockers  :")
            for t in self.triggered_by:
                lines.append(f"  • {t.description}")
        if self.options:
            lines.append("Options   :")
            for opt in self.options[:3]:
                lines.append(f"  {opt}")
        if self.extra_cost_min_usd:
            lines.append(f"Extra cost: ${self.extra_cost_min_usd:,}–"
                         f"${self.extra_cost_max_usd:,} USD")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

STANDARD_ADAPTERS = {
    "HTTPS", "HTTP", "SOAP", "OData", "REST", "IDoc", "File",
    "FTP", "SFTP", "JMS", "AMQP", "AS2", "AS4", "SuccessFactors",
    "Mail", "ProcessDirect", "XI", "RFC", "JDBC",
}


class MigrationCeilingClassifier:

    def classify(
        self,
        assessment,
        cfg=None,
        clean_core_report=None,
    ) -> MigrationCeiling:
        iface    = assessment.interface
        triggers = []

        # ── Check each specialist trigger ────────────────────────────

        # BPM
        if iface.has_bpm:
            triggers.append(TRIGGER_INDEX["BPM"])

        # Java binary (detect from mapping name keywords)
        if iface.mapping_program:
            mp_lower = iface.mapping_program.lower()
            if any(kw in mp_lower for kw in
                   ["pdf", "binary", "attachment", "file", "excel", "word",
                    "office", "encrypt", "decrypt", "zip"]):
                triggers.append(TRIGGER_INDEX["JAVA_BINARY"])

        # Undocumented
        if (not iface.mapping_program and
                not iface.description and
                not iface.message_interface):
            triggers.append(TRIGGER_INDEX["UNDOCUMENTED"])

        # EOIO + high volume (channels > 3)
        if (iface.sender_adapter in ("JMS", "XI", "ProcessDirect") and
                iface.channel_count > 3):
            triggers.append(TRIGGER_INDEX["EOIO_HIGH_VOLUME"])

        # B2B complex
        if (iface.sender_adapter in ("AS2", "AS4") or
                iface.receiver_adapter in ("AS2", "AS4")):
            if iface.has_multi_mapping or iface.mapping_program:
                triggers.append(TRIGGER_INDEX["B2B_COMPLEX"])

        # Multi-mapping + BPM
        if iface.has_bpm and iface.has_multi_mapping:
            triggers.append(TRIGGER_INDEX["MULTI_BPM"])

        # Extreme score
        if assessment.score > SPECIALIST_SCORE_THRESHOLD:
            triggers.append(TRIGGER_INDEX["SCORE_EXTREME"])

        # RFC + JDBC
        adapters = {iface.sender_adapter, iface.receiver_adapter}
        if "RFC" in adapters and "JDBC" in adapters:
            triggers.append(TRIGGER_INDEX["RFC_JDBC"])

        # Unknown adapter
        for adapter in adapters:
            if adapter and adapter not in STANDARD_ADAPTERS:
                triggers.append(TRIGGER_INDEX["UNKNOWN_ADAPTER"])
                break

        # No addresses
        if cfg:
            no_sender   = not cfg.sender_connectivity.address
            no_receiver = not cfg.receiver_connectivity.address
            if no_sender and no_receiver:
                triggers.append(TRIGGER_INDEX["NO_ADDRESS"])

        # Deduplicate triggers
        seen_codes = set()
        unique_triggers = []
        for t in triggers:
            if t.code not in seen_codes:
                seen_codes.add(t.code)
                unique_triggers.append(t)

        # ── Determine tier ───────────────────────────────────────────
        if unique_triggers:
            tier = TIER_SPECIALIST
        elif assessment.complexity == "HIGH":
            tier = TIER_GUIDED
        elif assessment.complexity == "MEDIUM":
            tier = TIER_GUIDED
        else:
            tier = TIER_AUTO

        # ── Aggregate options and costs ──────────────────────────────
        all_options      = []
        extra_days_min   = 0
        extra_days_max   = 0
        extra_cost_min   = 0
        extra_cost_max   = 0

        for t in unique_triggers:
            all_options.extend(t.options[:2])
            extra_days_min   += t.extra_days[0]
            extra_days_max   += t.extra_days[1]
            extra_cost_min   += t.extra_cost_usd[0]
            extra_cost_max   += t.extra_cost_usd[1]

        # ── Automation percentage ────────────────────────────────────
        auto_pct = {
            TIER_AUTO:       90,
            TIER_GUIDED:     65,
            TIER_SPECIALIST: 30,
        }.get(tier, 50)

        # Reduce for each trigger
        auto_pct = max(10, auto_pct - len(unique_triggers) * 10)

        # ── Manual tasks ─────────────────────────────────────────────
        manual_tasks = self._build_manual_tasks(iface, cfg, unique_triggers)

        return MigrationCeiling(
            interface_name=iface.name,
            tier=tier,
            score=assessment.score,
            complexity=assessment.complexity,
            triggered_by=unique_triggers,
            options=list(dict.fromkeys(all_options)),  # deduplicate
            extra_days_min=extra_days_min,
            extra_days_max=extra_days_max,
            extra_cost_min_usd=extra_cost_min,
            extra_cost_max_usd=extra_cost_max,
            automation_pct=auto_pct,
            manual_tasks=manual_tasks,
            client_decision_required=tier == TIER_SPECIALIST,
        )

    def classify_all(
        self,
        assessments: list,
        configs: dict = None,
        clean_core_reports: dict = None,
    ) -> list[MigrationCeiling]:
        configs            = configs or {}
        clean_core_reports = clean_core_reports or {}
        ceilings = []
        for a in assessments:
            name = a.interface.name
            c    = self.classify(
                a,
                cfg=configs.get(name),
                clean_core_report=clean_core_reports.get(name),
            )
            ceilings.append(c)
        auto   = sum(1 for c in ceilings if c.tier == TIER_AUTO)
        guided = sum(1 for c in ceilings if c.tier == TIER_GUIDED)
        spec   = sum(1 for c in ceilings if c.tier == TIER_SPECIALIST)
        logger.info("Migration ceiling — AUTO: %d | GUIDED: %d | SPECIALIST: %d",
                    auto, guided, spec)
        return ceilings

    @staticmethod
    def _build_manual_tasks(iface, cfg, triggers: list[CeilingTrigger]) -> list[str]:
        tasks = []
        if iface.mapping_program:
            tasks.append(f"Complete message mapping logic in '{iface.mapping_program}'")
        if iface.has_bpm:
            tasks.append("Redesign BPM process as CPI iFlow steps")
        if cfg and not cfg.sender_connectivity.address:
            tasks.append("Configure sender endpoint address/URL")
        if cfg and not cfg.receiver_connectivity.address:
            tasks.append("Configure receiver endpoint address/URL")
        if cfg and cfg.sender_auth.method != "None" and not cfg.sender_auth.credential_name:
            tasks.append("Create sender credential in CPI secure parameters")
        if cfg and cfg.receiver_auth.method != "None" and not cfg.receiver_auth.credential_name:
            tasks.append("Create receiver credential in CPI secure parameters")
        if iface.sender_adapter == "IDoc" or iface.receiver_adapter == "IDoc":
            tasks.append("Configure WE20 partner profile + WE21 port in SAP system")
        if iface.sender_adapter == "RFC" or iface.receiver_adapter == "RFC":
            tasks.append("Create SM59 RFC destination in SAP system")
        if iface.sender_adapter == "JDBC" or iface.receiver_adapter == "JDBC":
            tasks.append("Upload JDBC driver and create Data Source in CPI")
        for t in triggers:
            if t.options:
                tasks.append(f"Decision needed: {t.description}")
        return tasks


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def ceiling_summary(ceilings: list[MigrationCeiling]) -> dict:
    return {
        "total":          len(ceilings),
        "auto":           sum(1 for c in ceilings if c.tier == TIER_AUTO),
        "guided":         sum(1 for c in ceilings if c.tier == TIER_GUIDED),
        "specialist":     sum(1 for c in ceilings if c.tier == TIER_SPECIALIST),
        "avg_automation": round(
            sum(c.automation_pct for c in ceilings) / len(ceilings), 1
        ) if ceilings else 0,
        "total_extra_days_min": sum(c.extra_days_min for c in ceilings),
        "total_extra_days_max": sum(c.extra_days_max for c in ceilings),
        "total_extra_cost_min": sum(c.extra_cost_min_usd for c in ceilings),
        "total_extra_cost_max": sum(c.extra_cost_max_usd for c in ceilings),
        "client_decisions":     sum(1 for c in ceilings if c.client_decision_required),
    }

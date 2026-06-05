"""
analyzer/recommendation_engine.py

Analyzes the full interface inventory and produces actionable
recommendations per interface and project-level:

  - START NOW: ready to migrate immediately
  - BLOCKED ON CLIENT: waiting for information from client
  - PARK + RESEARCH: needs expertise you're developing
  - DEFER: retire or descope this interface
  - SPECIALIST: escalate to senior architect

Also generates the advisory banners shown in Tab 2 for each interface,
including specific expertise warnings (ABAP, CAP, RAP, S/4 API research).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Recommendation tiers
# ---------------------------------------------------------------------------

TIER_START       = "START NOW"
TIER_BLOCKED     = "BLOCKED ON CLIENT"
TIER_PARK        = "PARK + RESEARCH"
TIER_DEFER       = "DEFER"
TIER_SPECIALIST  = "SPECIALIST"

TIER_COLOURS = {
    TIER_START:      "C6EFCE",   # green
    TIER_BLOCKED:    "FFEB9C",   # amber
    TIER_PARK:       "FFE0B2",   # orange
    TIER_DEFER:      "E0E0E0",   # grey
    TIER_SPECIALIST: "FFC7CE",   # red
}

TIER_ICONS = {
    TIER_START:      "🟢",
    TIER_BLOCKED:    "🟡",
    TIER_PARK:       "🟠",
    TIER_DEFER:      "⚫",
    TIER_SPECIALIST: "🔴",
}


# ---------------------------------------------------------------------------
# Advisory flags
# ---------------------------------------------------------------------------

@dataclass
class AdvisoryFlag:
    code: str
    severity: str          # BLOCKER / WARNING / INFO
    title: str
    detail: str
    expertise_needed: str  # what skill you need
    action: str            # what to do
    problem_type: str      # maps to ClientProblemTracker.PROBLEM_TYPES
    quote_type: str        # FIXED / FIXED_BUFFER / TM


# All possible advisory flags
ADVISORY_FLAGS = {

    "ABAP_PROXY": AdvisoryFlag(
        code="ABAP_PROXY",
        severity="BLOCKER",
        title="ABAP Proxy migration",
        detail="Legacy ABAP proxies require SPROXY/SE80 access and an ABAP developer "
               "to regenerate or migrate to a Metadata Repository (MDR). "
               "The CPI tool cannot do this automatically.",
        expertise_needed="ABAP development + SPROXY transaction access",
        action="Confirm client has an ABAP developer available. "
               "Park this interface until confirmed.",
        problem_type="ABAP_PROXY",
        quote_type="TM",
    ),

    "BPM_SIMPLE": AdvisoryFlag(
        code="BPM_SIMPLE",
        severity="BLOCKER",
        title="BPM/ccBPM process detected (simple)",
        detail="Simple linear BPM flows (approval, notification, sequential steps) "
               "can be redesigned as CPI iFlow steps, but require architectural "
               "judgment — not just configuration.",
        expertise_needed="CPI iFlow orchestration design",
        action="Research the business process intent before migrating. "
               "Complete other interfaces first, return to this one.",
        problem_type="BPM_SIMPLE",
        quote_type="FIXED_BUFFER",
    ),

    "BPM_COMPLEX": AdvisoryFlag(
        code="BPM_COMPLEX",
        severity="BLOCKER",
        title="BPM/ccBPM process detected (complex)",
        detail="Multi-step BPM with correlation IDs, parallel branches, or "
               "long-running processes has no direct CPI equivalent. "
               "Requires full architectural redesign.",
        expertise_needed="Senior CPI architect + BTP Workflow Service knowledge",
        action="Escalate to senior architect or defer to SAP BTP Workflow Service project.",
        problem_type="BPM_COMPLEX",
        quote_type="TM",
    ),

    "JAVA_BINARY": AdvisoryFlag(
        code="JAVA_BINARY",
        severity="BLOCKER",
        title="Java mapping with binary operations",
        detail="Java mappings that process PDF attachments, Office documents, "
               "or use file system I/O cannot be wrapped — they must be "
               "fully rewritten as native Groovy.",
        expertise_needed="Java + Apache Groovy + CPI Script API",
        action="Park this interface. Study the Java source code first to "
               "understand what it does, then rewrite in Groovy.",
        problem_type="JAVA_BINARY",
        quote_type="TM",
    ),

    "RFC_NO_ODATA": AdvisoryFlag(
        code="RFC_NO_ODATA",
        severity="WARNING",
        title="RFC/BAPI — OData equivalent research required",
        detail="This RFC or BAPI needs a standard OData V4 equivalent on the "
               "SAP Business Accelerator Hub before migration to S/4HANA Cloud. "
               "Custom Z-BAPIs have no standard equivalent.",
        expertise_needed="SAP Business Accelerator Hub research + OData knowledge",
        action="Search api.sap.com for the BAPI name. "
               "Budget 1-2 hours research time.",
        problem_type="RFC_NO_ODATA",
        quote_type="FIXED_BUFFER",
    ),

    "Z_OBJECT": AdvisoryFlag(
        code="Z_OBJECT",
        severity="BLOCKER",
        title="Custom Z-BAPI or Z-table detected",
        detail="Custom Z-objects have no standard API equivalent. "
               "Client needs to expose them via RAP (ABAP RESTful Programming Model) "
               "or CAP (SAP Cloud Application Programming Model) before migration.",
        expertise_needed="ABAP RAP or CAP development — client's ABAP team",
        action="Inform client their ABAP team needs to build an API wrapper "
               "before this interface can be migrated.",
        problem_type="Z_OBJECT",
        quote_type="TM",
    ),

    "EOIO_HIGHVOL": AdvisoryFlag(
        code="EOIO_HIGHVOL",
        severity="WARNING",
        title="EOIO high volume — performance tuning required",
        detail="The DataStore sequencing pattern works for standard volumes. "
               "High volume (>100k messages/day) requires performance tuning "
               "of polling intervals and DataStore retention settings.",
        expertise_needed="CPI performance tuning + DataStore optimization",
        action="Implement DataStore pattern, test at expected volume, "
               "tune polling interval and retention before go-live.",
        problem_type="EOIO_HIGHVOL",
        quote_type="FIXED_BUFFER",
    ),

    "CUSTOM_ADAPTER": AdvisoryFlag(
        code="CUSTOM_ADAPTER",
        severity="BLOCKER",
        title="Unknown or custom adapter type",
        detail="This adapter is not in the standard CPI adapter set. "
               "Cannot generate configuration or estimate effort "
               "without understanding what it does.",
        expertise_needed="Investigation of adapter functionality before quoting",
        action="Do not quote fixed price. Investigate what the adapter does "
               "and whether a standard CPI adapter can replace it.",
        problem_type="CUSTOM_ADAPTER",
        quote_type="TM",
    ),

    "AS2_COMPLEX": AdvisoryFlag(
        code="AS2_COMPLEX",
        severity="WARNING",
        title="Complex AS2/EDI schema",
        detail="AS2 interfaces with complex EDIFACT/X12 schemas require "
               "Integration Advisor for MAG/MIG generation and "
               "Trading Partner Management setup.",
        expertise_needed="Integration Advisor + B2B/EDI knowledge",
        action="Budget additional time for Integration Advisor setup. "
               "Confirm client has B2B add-on licensed.",
        problem_type="AS2_COMPLEX",
        quote_type="FIXED_BUFFER",
    ),

    "JDBC_SAP_DB": AdvisoryFlag(
        code="JDBC_SAP_DB",
        severity="BLOCKER",
        title="JDBC to SAP internal database",
        detail="Direct database access to SAP internal tables violates Clean Core "
               "and is not supported by SAP in cloud deployments. "
               "This interface needs to be redesigned.",
        expertise_needed="SAP API design + Clean Core architecture",
        action="Replace with an OData service or CDS View exposing the required data.",
        problem_type="JDBC_SAP_DB",
        quote_type="TM",
    ),

    "UNDOCUMENTED": AdvisoryFlag(
        code="UNDOCUMENTED",
        severity="BLOCKER",
        title="Undocumented interface",
        detail="No description, no mapping program, no message interface defined. "
               "Cannot migrate what is not understood. "
               "Reverse engineering is required first.",
        expertise_needed="PI/PO system access + business knowledge from client SME",
        action="Do not quote fixed price. "
               "Schedule a session with the client's business SME to document "
               "the interface before attempting migration.",
        problem_type="UNDOCUMENTED",
        quote_type="TM",
    ),

    "S4_API_RESEARCH": AdvisoryFlag(
        code="S4_API_RESEARCH",
        severity="INFO",
        title="S/4HANA Cloud API research required",
        detail="This interface targets S/4HANA Cloud. The source RFC/BAPI needs "
               "an OData V4 equivalent verified on the SAP Business Accelerator Hub "
               "before the migration can be completed.",
        expertise_needed="SAP Hub research — 1-2 hours",
        action="Search api.sap.com for the equivalent API. "
               "If not found, escalate to client's SAP architect.",
        problem_type="RFC_NO_ODATA",
        quote_type="FIXED_BUFFER",
    ),
}


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

@dataclass
class InterfaceRecommendation:
    interface_name: str
    tier: str
    tier_icon: str
    quote_type: str          # FIXED / FIXED_BUFFER / TM
    advisory_flags: list[AdvisoryFlag]
    blocking_flags: list[AdvisoryFlag]
    next_steps: list[str]
    ready_to_start: bool
    estimated_hours: float
    park_reason: str = ""
    problem_type: str = ""   # for ClientProblemTracker


@dataclass
class ProjectRecommendation:
    total: int
    start_now: list[InterfaceRecommendation]
    blocked_on_client: list[InterfaceRecommendation]
    park_research: list[InterfaceRecommendation]
    specialist: list[InterfaceRecommendation]
    defer: list[InterfaceRecommendation]
    total_startable_hours: float
    summary_message: str


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RecommendationEngine:

    def analyze(
        self,
        assessment,
        cfg=None,
        verification_report=None,
        clean_core_report=None,
        target_id: str = "s4hana_cloud",
    ) -> InterfaceRecommendation:
        iface  = assessment.interface
        flags  = self._detect_flags(assessment, cfg, target_id)
        blocks = [f for f in flags if f.severity == "BLOCKER"]

        # Determine tier
        if blocks:
            # Check if it's specialist-level or just park+research
            specialist_codes = {"BPM_COMPLEX","JAVA_BINARY","ABAP_PROXY",
                                 "JDBC_SAP_DB","CUSTOM_ADAPTER"}
            if any(f.code in specialist_codes for f in blocks):
                tier = TIER_SPECIALIST
            else:
                tier = TIER_PARK
        elif verification_report and verification_report.blocking_gaps:
            tier = TIER_BLOCKED
        elif assessment.complexity == "LOW":
            tier = TIER_START
        elif assessment.complexity == "MEDIUM":
            tier = TIER_START
        else:
            tier = TIER_PARK if flags else TIER_START

        # Undocumented = defer if no info at all
        if (not iface.description and not iface.mapping_program
                and not iface.message_interface
                and iface.sender_adapter == iface.receiver_adapter == "HTTPS"):
            tier = TIER_DEFER

        # Quote type — take strictest from flags
        quote_types = [f.quote_type for f in flags]
        if "TM" in quote_types:
            quote_type = "T&M only"
        elif "FIXED_BUFFER" in quote_types:
            quote_type = "Fixed + 20% buffer"
        else:
            quote_type = "Fixed price"

        # Next steps
        next_steps = self._build_next_steps(tier, flags, cfg,
                                             verification_report)

        # Park reason and problem type
        park_reason  = blocks[0].detail if blocks else ""
        problem_type = blocks[0].problem_type if blocks else ""

        # Estimated hours (rough — for planning)
        base = {"LOW": 2.5, "MEDIUM": 6.0, "HIGH": 16.0}
        estimated = base.get(assessment.complexity, 6.0)
        estimated += len(flags) * 1.5

        return InterfaceRecommendation(
            interface_name=iface.name,
            tier=tier,
            tier_icon=TIER_ICONS.get(tier, "•"),
            quote_type=quote_type,
            advisory_flags=flags,
            blocking_flags=blocks,
            next_steps=next_steps,
            ready_to_start=(tier == TIER_START),
            estimated_hours=estimated,
            park_reason=park_reason,
            problem_type=problem_type,
        )

    def analyze_all(
        self,
        assessments: list,
        configs: dict = None,
        verification_reports: dict = None,
        clean_core_reports: dict = None,
        target_ids: dict = None,
    ) -> ProjectRecommendation:
        configs              = configs or {}
        verification_reports = verification_reports or {}
        clean_core_reports   = clean_core_reports or {}
        target_ids           = target_ids or {}

        recs = [
            self.analyze(
                a,
                cfg=configs.get(a.interface.name),
                verification_report=verification_reports.get(a.interface.name),
                clean_core_report=clean_core_reports.get(a.interface.name),
                target_id=target_ids.get(a.interface.name, "s4hana_cloud"),
            )
            for a in assessments
        ]

        start      = [r for r in recs if r.tier == TIER_START]
        blocked    = [r for r in recs if r.tier == TIER_BLOCKED]
        park       = [r for r in recs if r.tier == TIER_PARK]
        specialist = [r for r in recs if r.tier == TIER_SPECIALIST]
        defer      = [r for r in recs if r.tier == TIER_DEFER]

        startable_hours = sum(r.estimated_hours for r in start)

        summary = (
            f"You can start {len(start)} interface(s) immediately "
            f"(~{startable_hours:.0f} hrs). "
            f"{len(blocked)} waiting on client info. "
            f"{len(park)} parked for research. "
            f"{len(specialist)} need specialist review."
        )

        return ProjectRecommendation(
            total=len(recs),
            start_now=start,
            blocked_on_client=blocked,
            park_research=park,
            specialist=specialist,
            defer=defer,
            total_startable_hours=startable_hours,
            summary_message=summary,
        )

    # ── Flag detection ────────────────────────────────────────────────

    def _detect_flags(
        self, assessment, cfg, target_id: str
    ) -> list[AdvisoryFlag]:
        flags  = []
        iface  = assessment.interface
        sa     = iface.sender_adapter
        ra     = iface.receiver_adapter
        name   = iface.name.lower()
        desc   = (iface.description or "").lower()

        # BPM
        if iface.has_bpm:
            if iface.has_multi_mapping or "correlation" in desc or "parallel" in desc:
                flags.append(ADVISORY_FLAGS["BPM_COMPLEX"])
            else:
                flags.append(ADVISORY_FLAGS["BPM_SIMPLE"])

        # ABAP Proxy
        if "proxy" in name or "abap" in name or "sproxy" in name:
            flags.append(ADVISORY_FLAGS["ABAP_PROXY"])

        # Java binary
        if iface.mapping_program:
            mp = iface.mapping_program.lower()
            if any(kw in mp for kw in ("java","jar","binary","pdf","office","excel")):
                flags.append(ADVISORY_FLAGS["JAVA_BINARY"])

        # RFC
        if sa == "RFC" or ra == "RFC":
            bapi = iface.mapping_program or iface.message_interface or ""
            if bapi.upper().startswith("Z") or bapi.upper().startswith("Y"):
                flags.append(ADVISORY_FLAGS["Z_OBJECT"])
            else:
                if target_id in ("s4hana_cloud", "s4hana_op"):
                    flags.append(ADVISORY_FLAGS["S4_API_RESEARCH"])
                else:
                    flags.append(ADVISORY_FLAGS["RFC_NO_ODATA"])

        # EOIO
        if sa in ("JMS","XI") or ra in ("JMS","XI"):
            if iface.channel_count > 5:
                flags.append(ADVISORY_FLAGS["EOIO_HIGHVOL"])

        # Custom adapter
        known = {"HTTPS","HTTP","SOAP","OData","REST","IDoc","RFC","File",
                 "FTP","SFTP","JDBC","JMS","AMQP","AS2","AS4",
                 "SuccessFactors","Mail","ProcessDirect","XI"}
        if sa not in known or ra not in known:
            flags.append(ADVISORY_FLAGS["CUSTOM_ADAPTER"])

        # AS2 complex
        if sa in ("AS2","AS4") or ra in ("AS2","AS4"):
            if iface.has_multi_mapping or "edifact" in desc or "x12" in desc:
                flags.append(ADVISORY_FLAGS["AS2_COMPLEX"])

        # JDBC to SAP DB
        if (sa == "JDBC" or ra == "JDBC"):
            sys_name = (iface.sender_system + iface.receiver_system).upper()
            if any(s in sys_name for s in ("SAP","ECC","S4","HANA","ERP")):
                flags.append(ADVISORY_FLAGS["JDBC_SAP_DB"])

        # Undocumented
        if (not iface.description and not iface.mapping_program
                and not iface.message_interface):
            flags.append(ADVISORY_FLAGS["UNDOCUMENTED"])

        # Deduplicate
        seen  = set()
        dedup = []
        for f in flags:
            if f.code not in seen:
                seen.add(f.code)
                dedup.append(f)
        return dedup

    @staticmethod
    def _build_next_steps(tier, flags, cfg, vr) -> list[str]:
        steps = []

        if tier == TIER_START:
            steps.append("Configure connectivity and credentials in Tab 4")
            steps.append("Generate iFlow in Tab 5")
            steps.append("Deploy to DEV tenant via AI Solver in Tab 8")
            steps.append("Test with mock payload")

        elif tier == TIER_BLOCKED:
            if vr:
                for gap in vr.gaps:
                    if gap.severity == "BLOCKING":
                        steps.append(f"Client action: {gap.suggested_fix}")

        elif tier in (TIER_PARK, TIER_SPECIALIST):
            for flag in flags:
                if flag.severity == "BLOCKER":
                    steps.append(flag.action)
            steps.append("Complete other LOW/MEDIUM interfaces first")
            steps.append("Add to client tracker for follow-up when solved")

        elif tier == TIER_DEFER:
            steps.append("Confirm with client if this interface is still needed")
            steps.append("Consider retiring rather than migrating")

        return steps

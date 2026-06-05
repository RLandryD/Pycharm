"""
analyzer/clean_core_analyzer.py

Analyzes iFlow configurations against SAP Clean Core principles:
  - No direct ABAP/RFC calls (use OData/SOAP APIs instead)
  - No direct DB access (JDBC to SAP DBs)
  - No custom adapter types
  - Standard API-first integration patterns
  - BTP/Cloud-native authentication (OAuth2, not Basic where avoidable)
  - Proper error handling and observability

Returns a CleanCoreReport per interface with:
  - Compliance score (0-100)
  - Traffic light (GREEN / AMBER / RED)
  - Specific violations with remediation guidance
  - Recommended API alternatives
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from extractor.pi_extractor import InterfaceRecord
from models.interface_config import InterfaceConfig


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

@dataclass
class CleanCoreRule:
    id: str
    name: str
    description: str
    severity: str          # "BLOCKER" / "MAJOR" / "MINOR"
    deduction: int         # points deducted from score (0-100 total)
    remediation: str
    api_alternative: str = ""


CLEAN_CORE_RULES: list[CleanCoreRule] = [
    CleanCoreRule(
        id="CC-001",
        name="Direct RFC/BAPI call",
        description="RFC adapter bypasses SAP's published API layer — violates Clean Core.",
        severity="BLOCKER",
        deduction=30,
        remediation="Replace RFC/BAPI call with equivalent OData V4 or SOAP API published on SAP Business Accelerator Hub.",
        api_alternative="Search api.sap.com for the business object (e.g. 'Purchase Order' → API_PURCHASEORDER_PROCESS_SRV)",
    ),
    CleanCoreRule(
        id="CC-002",
        name="Direct database access (JDBC to SAP DB)",
        description="JDBC adapter directly queries SAP database tables — fragile and unsupported in cloud.",
        severity="BLOCKER",
        deduction=30,
        remediation="Expose required data via a CDS View + OData service, or use a released ABAP API.",
        api_alternative="Use SAP BTP CAP or ABAP RESTful Application Programming Model (RAP) to expose data.",
    ),
    CleanCoreRule(
        id="CC-003",
        name="BPM / ccBPM process",
        description="PI/PO BPM has no cloud equivalent and indicates complex orchestration not aligned to microservice patterns.",
        severity="BLOCKER",
        deduction=25,
        remediation="Redesign as stateless CPI iFlow steps. Use SAP BTP Workflow Service for long-running processes.",
        api_alternative="SAP BTP Workflow Service API for human task / approval flows.",
    ),
    CleanCoreRule(
        id="CC-004",
        name="Basic authentication to SAP cloud system",
        description="Basic auth (username/password) is deprecated for SAP cloud systems. OAuth2 is required.",
        severity="MAJOR",
        deduction=15,
        remediation="Switch to OAuth2 Client Credentials flow. Create a Communication Arrangement in S/4HANA Cloud.",
        api_alternative="",
    ),
    CleanCoreRule(
        id="CC-005",
        name="Non-standard / custom adapter",
        description="Custom or unknown adapter type not in SAP standard set — maintenance risk.",
        severity="MAJOR",
        deduction=15,
        remediation="Review if a standard CPI adapter (HTTPS, OData, SOAP) can replace the custom adapter.",
        api_alternative="",
    ),
    CleanCoreRule(
        id="CC-006",
        name="No error handling configured",
        description="Missing retry, dead letter queue, or failure alerting — reduces observability.",
        severity="MAJOR",
        deduction=10,
        remediation="Enable retry (min 3 attempts), store message on failure, and configure alert on failure.",
        api_alternative="",
    ),
    CleanCoreRule(
        id="CC-007",
        name="No message logging",
        description="Log level set to None — makes troubleshooting impossible in production.",
        severity="MINOR",
        deduction=5,
        remediation="Set log level to 'Header only' minimum. Use 'Header + Body' for critical interfaces.",
        api_alternative="",
    ),
    CleanCoreRule(
        id="CC-008",
        name="Synchronous BPM pattern",
        description="Synchronous processing with BPM indicates tight coupling — not cloud-native.",
        severity="MINOR",
        deduction=10,
        remediation="Consider async processing with event-driven pattern using Advanced Event Mesh.",
        api_alternative="SAP Advanced Event Mesh for decoupled event-driven integration.",
    ),
    CleanCoreRule(
        id="CC-009",
        name="Multi-mapping pattern",
        description="Multi-mapping indicates fan-out complexity — consider event-driven multicast.",
        severity="MINOR",
        deduction=5,
        remediation="Use CPI Multicast step or Advanced Event Mesh topic fan-out.",
        api_alternative="",
    ),
    CleanCoreRule(
        id="CC-010",
        name="No idempotency check",
        description="Exactly-once delivery without idempotency check risks duplicate processing.",
        severity="MINOR",
        deduction=5,
        remediation="Enable idempotency check using message ID header or JMS duplicate check.",
        api_alternative="",
    ),
]

STANDARD_ADAPTERS = {
    "HTTPS", "HTTP", "SOAP", "OData", "REST", "IDoc", "File", "FTP", "SFTP",
    "JMS", "AMQP", "MQTT", "AS2", "AS4", "SuccessFactors", "Mail",
    "ProcessDirect", "XI", "RFC", "JDBC",
}


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class CleanCoreViolation:
    rule: CleanCoreRule
    detail: str            # specific detail about this interface


@dataclass
class CleanCoreReport:
    interface_name: str
    score: int                              # 0–100
    traffic_light: str                      # GREEN / AMBER / RED
    violations: list[CleanCoreViolation]    = field(default_factory=list)
    passed_rules: list[str]                 = field(default_factory=list)
    recommendations: list[str]             = field(default_factory=list)
    rise_ready: bool                        = False

    @property
    def blocker_count(self) -> int:
        return sum(1 for v in self.violations if v.rule.severity == "BLOCKER")

    @property
    def major_count(self) -> int:
        return sum(1 for v in self.violations if v.rule.severity == "MAJOR")

    @property
    def minor_count(self) -> int:
        return sum(1 for v in self.violations if v.rule.severity == "MINOR")

    def summary(self) -> str:
        return (f"Score: {self.score}/100 [{self.traffic_light}] — "
                f"{self.blocker_count} blockers, {self.major_count} major, "
                f"{self.minor_count} minor")


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class CleanCoreAnalyzer:

    def analyze_record(
        self,
        record: InterfaceRecord,
        cfg: Optional[InterfaceConfig] = None,
    ) -> CleanCoreReport:
        """Analyze a PI/PO InterfaceRecord (+ optional InterfaceConfig) for Clean Core compliance."""
        violations = []
        passed     = []

        # CC-001: RFC
        if record.sender_adapter == "RFC" or record.receiver_adapter == "RFC":
            violations.append(CleanCoreViolation(
                rule=self._rule("CC-001"),
                detail=f"RFC adapter used on {'sender' if record.sender_adapter == 'RFC' else 'receiver'} side.",
            ))
        else:
            passed.append("CC-001: No RFC adapter")

        # CC-002: JDBC
        if record.sender_adapter == "JDBC" or record.receiver_adapter == "JDBC":
            violations.append(CleanCoreViolation(
                rule=self._rule("CC-002"),
                detail="JDBC adapter detected — direct database access.",
            ))
        else:
            passed.append("CC-002: No JDBC adapter")

        # CC-003: BPM
        if record.has_bpm:
            violations.append(CleanCoreViolation(
                rule=self._rule("CC-003"),
                detail="BPM/ccBPM process detected in source interface.",
            ))
        else:
            passed.append("CC-003: No BPM")

        # CC-004: Basic auth (only if config provided)
        if cfg:
            uses_basic = (
                cfg.sender_auth.method == "Basic" or
                cfg.receiver_auth.method == "Basic"
            )
            if uses_basic:
                violations.append(CleanCoreViolation(
                    rule=self._rule("CC-004"),
                    detail="Basic authentication configured — upgrade to OAuth2.",
                ))
            else:
                passed.append("CC-004: OAuth2 or non-Basic auth")

        # CC-005: Non-standard adapter
        for adapter in [record.sender_adapter, record.receiver_adapter]:
            if adapter not in STANDARD_ADAPTERS:
                violations.append(CleanCoreViolation(
                    rule=self._rule("CC-005"),
                    detail=f"Non-standard adapter: '{adapter}'",
                ))
                break
        else:
            passed.append("CC-005: Standard adapters only")

        # CC-006: Error handling (requires config)
        if cfg:
            no_error_handling = (
                not cfg.reliability.retry_enabled and
                not cfg.reliability.dead_letter_enabled and
                not cfg.reliability.store_message_on_failure
            )
            if no_error_handling:
                violations.append(CleanCoreViolation(
                    rule=self._rule("CC-006"),
                    detail="No retry, DLQ, or store-on-failure configured.",
                ))
            else:
                passed.append("CC-006: Error handling configured")

            # CC-007: Logging
            if cfg.reliability.log_level == "None":
                violations.append(CleanCoreViolation(
                    rule=self._rule("CC-007"),
                    detail="Message log level is None.",
                ))
            else:
                passed.append("CC-007: Message logging enabled")

            # CC-010: Idempotency
            if (cfg.runtime.quality_of_service == "Exactly Once" and
                    not cfg.reliability.idempotency_enabled):
                violations.append(CleanCoreViolation(
                    rule=self._rule("CC-010"),
                    detail="Exactly Once QoS without idempotency check.",
                ))
            else:
                passed.append("CC-010: Idempotency OK")

        # CC-008: Sync BPM
        if record.has_bpm and cfg and not cfg.message.is_async:
            violations.append(CleanCoreViolation(
                rule=self._rule("CC-008"),
                detail="Synchronous BPM pattern detected.",
            ))

        # CC-009: Multi-mapping
        if record.has_multi_mapping:
            violations.append(CleanCoreViolation(
                rule=self._rule("CC-009"),
                detail="Multi-mapping pattern — consider event-driven multicast.",
            ))
        else:
            passed.append("CC-009: No multi-mapping")

        # Score
        deductions = sum(v.rule.deduction for v in violations)
        score      = max(0, 100 - deductions)

        if score >= 80:
            traffic_light = "GREEN"
        elif score >= 50:
            traffic_light = "AMBER"
        else:
            traffic_light = "RED"

        # RISE readiness: no blockers and score >= 70
        rise_ready = (
            sum(1 for v in violations if v.rule.severity == "BLOCKER") == 0
            and score >= 70
        )

        # Recommendations
        recommendations = []
        for v in violations:
            if v.rule.api_alternative:
                recommendations.append(v.rule.api_alternative)

        return CleanCoreReport(
            interface_name=record.name,
            score=score,
            traffic_light=traffic_light,
            violations=violations,
            passed_rules=passed,
            recommendations=recommendations,
            rise_ready=rise_ready,
        )

    def analyze_all(
        self,
        records: list[InterfaceRecord],
        configs: Optional[dict] = None,
    ) -> list[CleanCoreReport]:
        """Analyze all records. configs = {interface_name: InterfaceConfig}."""
        configs = configs or {}
        return [
            self.analyze_record(r, cfg=configs.get(r.name))
            for r in records
        ]

    @staticmethod
    def _rule(rule_id: str) -> CleanCoreRule:
        return next(r for r in CLEAN_CORE_RULES if r.id == rule_id)


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def clean_core_summary(reports: list[CleanCoreReport]) -> dict:
    return {
        "total":       len(reports),
        "green":       sum(1 for r in reports if r.traffic_light == "GREEN"),
        "amber":       sum(1 for r in reports if r.traffic_light == "AMBER"),
        "red":         sum(1 for r in reports if r.traffic_light == "RED"),
        "rise_ready":  sum(1 for r in reports if r.rise_ready),
        "avg_score":   round(sum(r.score for r in reports) / len(reports), 1) if reports else 0,
        "total_blockers": sum(r.blocker_count for r in reports),
    }

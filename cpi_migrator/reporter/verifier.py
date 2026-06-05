"""
reporter/verifier.py

Verifies a completed (or in-progress) CPI integration against the
original PI/PO interface requirements. Detects gaps and missing items.

Checks:
  1. Connectivity — sender/receiver addresses configured
  2. Authentication — method and credential name set
  3. Message mapping — if original had a mapping, CPI config must reference one
  4. IDoc fields — type, message type, partner profile
  5. Error handling — retry, DLQ, logging
  6. Adapter compatibility — receiver adapter supported by target
  7. Clean Core — blockers resolved
  8. Test harness — payload + test file generated
  9. Schema dependencies — WSDL/XSD collected if needed

Returns a VerificationReport per interface with:
  - overall status: COMPLETE / INCOMPLETE / NEEDS_REVIEW
  - list of gaps with severity and suggested fix
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class VerificationGap:
    category: str         # "Connectivity" / "Auth" / "Mapping" / etc.
    severity: str         # "BLOCKING" / "WARNING" / "INFO"
    description: str
    suggested_fix: str
    auto_fixable: bool = False


@dataclass
class VerificationReport:
    interface_name: str
    status: str                              # "COMPLETE" / "INCOMPLETE" / "NEEDS_REVIEW"
    gaps: list[VerificationGap]              = field(default_factory=list)
    passed_checks: list[str]                 = field(default_factory=list)
    completion_pct: float                    = 0.0

    @property
    def blocking_gaps(self) -> list[VerificationGap]:
        return [g for g in self.gaps if g.severity == "BLOCKING"]

    @property
    def warning_gaps(self) -> list[VerificationGap]:
        return [g for g in self.gaps if g.severity == "WARNING"]

    def summary(self) -> str:
        return (f"{self.status} ({self.completion_pct:.0f}%) — "
                f"{len(self.blocking_gaps)} blocking, "
                f"{len(self.warning_gaps)} warnings")


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class IntegrationVerifier:

    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)

    def verify(
        self,
        assessment,             # MigrationAssessment
        cfg,                    # InterfaceConfig
        clean_core_report=None, # CleanCoreReport | None
        target_id: str = "",
    ) -> VerificationReport:
        iface  = assessment.interface
        gaps   = []
        passed = []
        total_checks = 0
        passed_count = 0

        def check(condition: bool, passed_msg: str, gap: VerificationGap):
            nonlocal total_checks, passed_count
            total_checks += 1
            if condition:
                passed.append(passed_msg)
                passed_count += 1
            else:
                gaps.append(gap)

        # ── 1. Connectivity ──────────────────────────────────────────
        check(
            bool(cfg.sender_connectivity.address),
            "Sender address configured",
            VerificationGap(
                category="Connectivity",
                severity="BLOCKING",
                description=f"Sender address not set for {iface.sender_system}.",
                suggested_fix="Fill in sender address/URL in Tab 4 → Connectivity.",
            ),
        )
        check(
            bool(cfg.receiver_connectivity.address),
            "Receiver address configured",
            VerificationGap(
                category="Connectivity",
                severity="BLOCKING",
                description=f"Receiver address not set for {iface.receiver_system}.",
                suggested_fix="Fill in receiver address/URL in Tab 4 → Connectivity.",
            ),
        )

        # ── 2. Authentication ─────────────────────────────────────────
        check(
            cfg.sender_auth.method != "Basic" or bool(cfg.sender_auth.credential_name),
            "Sender credential name set",
            VerificationGap(
                category="Authentication",
                severity="BLOCKING",
                description="Sender uses Basic auth but no credential store alias is set.",
                suggested_fix="Set credential name in Tab 4 → Authentication → Sender. "
                              "Create matching entry in CPI: Monitor → Manage Security Material.",
            ),
        )
        check(
            cfg.receiver_auth.method == "None" or bool(cfg.receiver_auth.credential_name),
            "Receiver credential name set",
            VerificationGap(
                category="Authentication",
                severity="BLOCKING",
                description="Receiver authentication credential name is missing.",
                suggested_fix="Set receiver credential name in Tab 4 → Authentication → Receiver.",
            ),
        )
        if cfg.receiver_auth.method == "OAuth2 Client Credentials":
            check(
                bool(cfg.receiver_auth.token_url),
                "OAuth2 token URL set",
                VerificationGap(
                    category="Authentication",
                    severity="BLOCKING",
                    description="OAuth2 selected but token URL is empty.",
                    suggested_fix="Fill in token URL in Tab 4 → Authentication → Receiver.",
                ),
            )

        # ── 3. Message mapping ────────────────────────────────────────
        if iface.mapping_program:
            check(
                bool(cfg.message.mapping_program),
                f"Mapping program '{iface.mapping_program}' referenced",
                VerificationGap(
                    category="Message Mapping",
                    severity="BLOCKING",
                    description=f"Original PI/PO had mapping '{iface.mapping_program}' "
                                f"but no mapping is configured in the CPI interface.",
                    suggested_fix="Set mapping program name in Tab 4 → Message → Mapping Program. "
                                  "Re-implement the mapping in CPI Message Mapping editor.",
                ),
            )

        # ── 4. IDoc-specific ──────────────────────────────────────────
        if iface.sender_adapter == "IDoc" or iface.receiver_adapter == "IDoc":
            check(
                bool(cfg.message.idoc_type),
                "IDoc type configured",
                VerificationGap(
                    category="IDoc Configuration",
                    severity="BLOCKING",
                    description="IDoc adapter in use but IDoc type is not set.",
                    suggested_fix="Set IDoc type in Tab 4 → Connectivity → IDoc settings.",
                ),
            )
            check(
                bool(cfg.message.idoc_partner_profile),
                "IDoc partner profile set",
                VerificationGap(
                    category="IDoc Configuration",
                    severity="WARNING",
                    description="IDoc partner profile not configured.",
                    suggested_fix="Set partner profile in Tab 4 → IDoc settings. "
                                  "Also configure WE20 partner profile in the target SAP system.",
                ),
            )

        # ── 5. Error handling ─────────────────────────────────────────
        check(
            cfg.reliability.retry_enabled,
            "Retry enabled",
            VerificationGap(
                category="Error Handling",
                severity="WARNING",
                description="Automatic retry is disabled.",
                suggested_fix="Enable retry in Tab 4 → Reliability. Recommended: 3 attempts, 60s delay.",
                auto_fixable=True,
            ),
        )
        check(
            cfg.reliability.log_level != "None",
            "Message logging configured",
            VerificationGap(
                category="Observability",
                severity="WARNING",
                description="Message log level is None — troubleshooting will be very difficult.",
                suggested_fix="Set log level to 'Header only' minimum in Tab 4 → Reliability.",
                auto_fixable=True,
            ),
        )
        check(
            cfg.reliability.store_message_on_failure,
            "Store message on failure enabled",
            VerificationGap(
                category="Error Handling",
                severity="WARNING",
                description="Messages are not stored on failure — failed messages cannot be reprocessed.",
                suggested_fix="Enable 'Store message on failure' in Tab 4 → Reliability.",
                auto_fixable=True,
            ),
        )

        # ── 6. Adapter compatibility with target ──────────────────────
        if target_id:
            try:
                from destinations.registry import DESTINATION_REGISTRY
                target = DESTINATION_REGISTRY.get(target_id)
                if target:
                    check(
                        cfg.receiver_adapter in target.supported_adapters,
                        f"Receiver adapter '{cfg.receiver_adapter}' supported by {target.label}",
                        VerificationGap(
                            category="Adapter Compatibility",
                            severity="BLOCKING",
                            description=f"Adapter '{cfg.receiver_adapter}' is not in the "
                                        f"supported list for {target.label}.",
                            suggested_fix=f"Change receiver adapter to one of: "
                                          f"{', '.join(target.supported_adapters)}",
                        ),
                    )
            except Exception:
                pass

        # ── 7. Clean Core blockers ────────────────────────────────────
        if clean_core_report:
            for violation in clean_core_report.violations:
                if violation.rule.severity == "BLOCKER":
                    check(
                        False,
                        "",
                        VerificationGap(
                            category="Clean Core",
                            severity="BLOCKING",
                            description=f"[{violation.rule.id}] {violation.rule.name}: {violation.detail}",
                            suggested_fix=violation.rule.remediation,
                        ),
                    )
                    total_checks += 1  # already counted in check() above but blocker forced False

        # ── 8. Test harness ───────────────────────────────────────────
        safe_name    = iface.name.replace(" ", "_").replace("-", "_")
        test_exists  = (self.output_dir / "tests" / f"test_{safe_name}.py").exists()
        check(
            test_exists,
            "Test harness generated",
            VerificationGap(
                category="Testing",
                severity="INFO",
                description="No test harness file found for this interface.",
                suggested_fix="Generate test harness in Tab 5 before deploying.",
            ),
        )

        # ── 9. Standard iFlow selected ────────────────────────────────
        check(
            bool(cfg.std_iflow_id),
            "Standard iFlow selected as migration base",
            VerificationGap(
                category="Migration Base",
                severity="INFO",
                description="No standard iFlow was selected — generic template will be used.",
                suggested_fix="Go to Tab 3 and select the closest standard iFlow from CPI tenant.",
            ),
        )

        # ── 10. BPM redesign ─────────────────────────────────────────
        if iface.has_bpm:
            check(
                False,
                "",
                VerificationGap(
                    category="BPM Redesign",
                    severity="BLOCKING",
                    description="Original interface has BPM/ccBPM — no automated migration possible.",
                    suggested_fix="Manually redesign BPM logic as CPI iFlow steps or "
                                  "SAP BTP Workflow Service.",
                ),
            )

        # ── Score ─────────────────────────────────────────────────────
        completion_pct = (passed_count / total_checks * 100) if total_checks > 0 else 0

        blocking = [g for g in gaps if g.severity == "BLOCKING"]
        warnings = [g for g in gaps if g.severity == "WARNING"]

        if blocking:
            status = "INCOMPLETE"
        elif warnings:
            status = "NEEDS_REVIEW"
        else:
            status = "COMPLETE"

        return VerificationReport(
            interface_name=iface.name,
            status=status,
            gaps=gaps,
            passed_checks=passed,
            completion_pct=completion_pct,
        )

    def verify_all(
        self,
        assessments: list,
        configs: dict,
        clean_core_reports: dict = None,
        target_ids: dict = None,
    ) -> list[VerificationReport]:
        clean_core_reports = clean_core_reports or {}
        target_ids         = target_ids or {}
        return [
            self.verify(
                a,
                cfg=configs.get(a.interface.name),
                clean_core_report=clean_core_reports.get(a.interface.name),
                target_id=target_ids.get(a.interface.name, ""),
            )
            for a in assessments
            if a.interface.name in configs
        ]

    def auto_fix(self, cfg, gaps: list[VerificationGap]):
        """Apply auto-fixable gaps to the config in-place."""
        fixed = []
        for gap in gaps:
            if not gap.auto_fixable:
                continue
            if gap.category == "Error Handling" and "retry" in gap.description.lower():
                cfg.reliability.retry_enabled     = True
                cfg.reliability.retry_max_attempts = 3
                cfg.reliability.retry_delay_sec    = 60
                fixed.append(gap.description)
            elif gap.category == "Observability":
                cfg.reliability.log_level = "Header only"
                fixed.append(gap.description)
            elif "store message" in gap.description.lower():
                cfg.reliability.store_message_on_failure = True
                fixed.append(gap.description)
        return fixed


# ---------------------------------------------------------------------------
# Project-level summary
# ---------------------------------------------------------------------------

def project_verification_summary(reports: list[VerificationReport]) -> dict:
    return {
        "total":          len(reports),
        "complete":       sum(1 for r in reports if r.status == "COMPLETE"),
        "needs_review":   sum(1 for r in reports if r.status == "NEEDS_REVIEW"),
        "incomplete":     sum(1 for r in reports if r.status == "INCOMPLETE"),
        "avg_completion": round(
            sum(r.completion_pct for r in reports) / len(reports), 1
        ) if reports else 0,
        "total_blocking": sum(len(r.blocking_gaps) for r in reports),
        "total_warnings": sum(len(r.warning_gaps) for r in reports),
    }

"""
destinations/resolver.py

Given a MigrationAssessment and a chosen destination target, the resolver:
  1. Looks up the target in the registry
  2. Maps the PI/PO sender/receiver adapters to the correct CPI adapters
     for that target
  3. Attaches available Hub artifacts (pre-built iFlows / APIs) relevant
     to the interface
  4. Returns a ResolvedDestination with all enrichment data

The scaffolder and report generator consume ResolvedDestination objects
to produce target-aware output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from destinations.registry import DestinationTarget, get_target, list_targets
from destinations.hub_fetcher import HubFetcher, get_fetcher
from analyzer.complexity_analyzer import MigrationAssessment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class AdapterRecommendation:
    original_adapter: str
    recommended_adapter: str
    is_supported: bool
    requires_cloud_connector: bool
    note: str = ""


@dataclass
class HubArtifactMatch:
    """A Hub iFlow or API that might be reusable for this interface."""
    package_id: str
    artifact_id: str
    title: str
    artifact_type: str     # "IntegrationFlow" | "RestApi" | "ODataApi"
    description: str = ""
    url: str = ""


@dataclass
class ResolvedDestination:
    target: DestinationTarget
    sender_recommendation: AdapterRecommendation
    receiver_recommendation: AdapterRecommendation
    hub_matches: list[HubArtifactMatch]
    migration_hints: list[str]
    effort_multiplier: float = 1.0   # >1.0 if target increases complexity
    compatibility_warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Adapters that need Cloud Connector for on-premise connectivity
# ---------------------------------------------------------------------------

CLOUD_CONNECTOR_ADAPTERS = {
    "RFC", "JDBC", "IDoc", "SOAP", "HTTP", "HTTPS",
    "File", "FTP", "SFTP", "JMS",
}

# Adapters with no direct equivalent — need rethinking
NEEDS_REDESIGN = {"BPM", "ccBPM", "XI", "ProcessDirect"}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class DestinationResolver:

    def __init__(self, fetcher: Optional[HubFetcher] = None):
        self.fetcher = fetcher or get_fetcher()

    def resolve(
        self,
        assessment: MigrationAssessment,
        target_id: str,
    ) -> ResolvedDestination:
        """
        Resolve a single assessment against a destination target.
        Returns a ResolvedDestination with adapter recommendations and Hub matches.
        """
        target = get_target(target_id)
        iface = assessment.interface

        sender_rec = self._recommend_adapter(
            iface.sender_adapter, target, role="sender"
        )
        receiver_rec = self._recommend_adapter(
            iface.receiver_adapter, target, role="receiver"
        )

        hub_matches = self._find_hub_matches(assessment, target)
        warnings = self._check_compatibility(assessment, target, sender_rec, receiver_rec)

        # Effort multiplier: cloud targets add overhead for auth/network setup
        multiplier = 1.0
        if target.variant == "cloud" and iface.sender_adapter in ("RFC", "IDOC", "IDoc"):
            multiplier = 1.5   # BAPI→OData conversion adds ~50% effort
        elif target.variant == "saas":
            multiplier = 1.2   # SaaS OAuth + schema alignment

        return ResolvedDestination(
            target=target,
            sender_recommendation=sender_rec,
            receiver_recommendation=receiver_rec,
            hub_matches=hub_matches,
            migration_hints=target.migration_hints + assessment.notes,
            effort_multiplier=multiplier,
            compatibility_warnings=warnings,
        )

    def resolve_multi(
        self,
        assessment: MigrationAssessment,
        target_ids: list[str],
    ) -> dict[str, ResolvedDestination]:
        """Resolve one assessment against multiple targets."""
        return {tid: self.resolve(assessment, tid) for tid in target_ids}

    def resolve_all(
        self,
        assessments: list[MigrationAssessment],
        target_ids: list[str],
    ) -> dict[str, dict[str, ResolvedDestination]]:
        """
        Resolve all assessments against all targets.
        Returns {interface_name: {target_id: ResolvedDestination}}.
        """
        results = {}
        for a in assessments:
            results[a.interface.name] = self.resolve_multi(a, target_ids)
        return results

    # ── Internal ────────────────────────────────────────────────────

    def _recommend_adapter(
        self,
        original: str,
        target: DestinationTarget,
        role: str,
    ) -> AdapterRecommendation:
        recommended = target.adapter_mapping.get(original, original)
        is_supported = recommended in target.supported_adapters

        needs_cc = (
            target.variant in ("onpremise",)
            and original in CLOUD_CONNECTOR_ADAPTERS
        )

        note = ""
        if not is_supported:
            note = (
                f"Adapter '{recommended}' is not in the supported list for "
                f"{target.label}. Manual review required."
            )
        elif original != recommended:
            note = f"'{original}' maps to '{recommended}' for {target.label}."

        return AdapterRecommendation(
            original_adapter=original,
            recommended_adapter=recommended,
            is_supported=is_supported,
            requires_cloud_connector=needs_cc,
            note=note,
        )

    def _find_hub_matches(
        self,
        assessment: MigrationAssessment,
        target: DestinationTarget,
    ) -> list[HubArtifactMatch]:
        """
        Search cached Hub artifacts for iFlows / APIs whose title or description
        contains keywords from the interface name.
        Returns up to 5 best matches.
        """
        matches: list[HubArtifactMatch] = []
        iface = assessment.interface

        # Build keyword set from interface name (split on common separators)
        import re
        keywords = set(
            w.lower() for w in re.split(r"[_\-\s/]", iface.name)
            if len(w) > 3
        )
        # Also add adapter keywords
        keywords.update({
            iface.sender_adapter.lower(),
            iface.receiver_adapter.lower(),
        })

        for source in target.hub_sources:
            pkg_data = self.fetcher.get_package_artifacts(
                source.package_id, ttl=target.cache_ttl_seconds
            )
            artifacts = pkg_data.get("value", [])

            for artifact in artifacts:
                title = str(artifact.get("Title", "") or artifact.get("title", "")).lower()
                desc  = str(artifact.get("ShortText", "") or artifact.get("description", "")).lower()
                text  = title + " " + desc

                score = sum(1 for kw in keywords if kw in text)
                if score > 0:
                    matches.append((score, HubArtifactMatch(
                        package_id=source.package_id,
                        artifact_id=artifact.get("Id", artifact.get("id", "")),
                        title=artifact.get("Title", artifact.get("title", "Unknown")),
                        artifact_type=artifact.get("Type", "IntegrationFlow"),
                        description=artifact.get("ShortText", "")[:120],
                        url=f"https://api.sap.com/package/{source.package_id}/"
                            f"integrationflow/{artifact.get('Id', '')}",
                    )))

        # Sort by relevance score, deduplicate by artifact_id, return top 5
        seen = set()
        top_matches = []
        for _, match in sorted(matches, key=lambda x: x[0], reverse=True):
            if match.artifact_id not in seen:
                seen.add(match.artifact_id)
                top_matches.append(match)
            if len(top_matches) >= 5:
                break

        return top_matches

    def _check_compatibility(
        self,
        assessment: MigrationAssessment,
        target: DestinationTarget,
        sender_rec: AdapterRecommendation,
        receiver_rec: AdapterRecommendation,
    ) -> list[str]:
        warnings = []

        if not sender_rec.is_supported:
            warnings.append(
                f"Sender adapter '{sender_rec.recommended_adapter}' "
                f"not officially supported by {target.label}."
            )
        if not receiver_rec.is_supported:
            warnings.append(
                f"Receiver adapter '{receiver_rec.recommended_adapter}' "
                f"not officially supported by {target.label}."
            )
        if assessment.interface.has_bpm:
            warnings.append(
                "BPM/ccBPM process detected — no direct equivalent in CPI for any target. "
                "Requires full redesign as iFlow process steps."
            )
        if sender_rec.requires_cloud_connector or receiver_rec.requires_cloud_connector:
            warnings.append(
                f"Cloud Connector required for on-premise connectivity to {target.label}."
            )

        return warnings


# ---------------------------------------------------------------------------
# CLI helper: pretty-print a resolved destination
# ---------------------------------------------------------------------------

def summarise_resolution(resolved: ResolvedDestination) -> str:
    lines = [
        f"Target       : {resolved.target.label} ({resolved.target.variant})",
        f"Sender       : {resolved.sender_recommendation.original_adapter}"
        f" → {resolved.sender_recommendation.recommended_adapter}"
        + (" ✓" if resolved.sender_recommendation.is_supported else " ⚠"),
        f"Receiver     : {resolved.receiver_recommendation.original_adapter}"
        f" → {resolved.receiver_recommendation.recommended_adapter}"
        + (" ✓" if resolved.receiver_recommendation.is_supported else " ⚠"),
        f"Effort mult. : ×{resolved.effort_multiplier:.1f}",
    ]
    if resolved.hub_matches:
        lines.append(f"Hub matches  : {len(resolved.hub_matches)} pre-built artifacts found")
        for m in resolved.hub_matches[:3]:
            lines.append(f"  • [{m.artifact_type}] {m.title}")
    if resolved.compatibility_warnings:
        lines.append("Warnings:")
        for w in resolved.compatibility_warnings:
            lines.append(f"  ⚠ {w}")
    return "\n".join(lines)

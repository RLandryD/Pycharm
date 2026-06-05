"""
fetcher/match_aggregator.py

Orchestrates the three sources of standard-iFlow match candidates that the
Workbench can offer for any PI/PO interface:

  1. CPI tenant            (CPIFetcher.suggest_matches)        — what the user
                                                               already has on
                                                               their tenant
  2. SAP Business Hub      (HubCatalogClient.search_for_interface)
                                                               — official
                                                               packaged content
                                                               from api.sap.com
  3. SAP-org GitHub recipes (SAPSamplesBrowser via package index)
                                                               — community /
                                                               recipe-style
                                                               starting points

The aggregator is the single entry-point Tab 3 talks to. It encapsulates:

  - "Default smart fallback" mode (chain): tenant first; if too few results,
    extend with Hub; if still thin, extend with GitHub recipes.
  - "Explicit source" mode: just one source, no fallback.
  - Hub-preferred deduplication when the same SAP package id appears in
    multiple sources.
  - Caching is delegated to each source client (HubCatalogClient has 24h
    cache; SAPSamplesBrowser has its own; tenant calls are short-lived and
    don't need caching here).

Read-only. No source module is modified by this module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class MatchSource(str, Enum):
    TENANT = "Tenant"
    HUB    = "Hub"
    GITHUB = "GitHub Recipes"


class MatchMode(str, Enum):
    FALLBACK_CHAIN = "fallback_chain"  # default: tenant → Hub → GitHub
    TENANT_ONLY    = "tenant_only"
    HUB_ONLY       = "hub_only"
    GITHUB_ONLY    = "github_only"


# Minimum tenant-result count to short-circuit the fallback chain. Below this,
# we extend with the next source. Three felt right — a single weak match isn't
# enough but ten is more than enough.
FALLBACK_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Normalised result type
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """A normalised match candidate, source-tagged.

    score is the original source's relevance score (higher is better, scales
    differ per source — they're only meaningful within one source).
    """
    source: MatchSource
    id: str                        # package or artifact id
    name: str
    score: int = 0
    package_id: str = ""           # for Hub artifacts / tenant artifacts
    description: str = ""
    url: str = ""                  # browsable URL (Hub web link, GitHub link)
    artifact_count: int = 0        # how many iFlows in the package, if known
    raw: object = None             # original object (HubPackage / CPIArtifact / SAPSamplePackage)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

class MatchAggregator:
    """Aggregates match candidates from the three sources.

    Pass any of the clients as None to skip that source. The aggregator will
    silently degrade — missing Hub key just means Hub isn't queried, not a
    crash.
    """

    def __init__(
        self,
        cpi_fetcher=None,       # CPIFetcher
        hub_client=None,        # HubCatalogClient
        samples_browser=None,   # SAPSamplesBrowser
    ):
        self.cpi_fetcher     = cpi_fetcher
        self.hub_client      = hub_client
        self.samples_browser = samples_browser

    # ── Public entry point ────────────────────────────────────────────

    def find_matches(
        self,
        interface_name: str,
        sender_adapter: str,
        receiver_adapter: str,
        target_id: str = "",
        tenant_artifacts: Optional[list] = None,
        mode: MatchMode = MatchMode.FALLBACK_CHAIN,
        top_per_source: int = 5,
    ) -> list[MatchResult]:
        """Return ranked match candidates per the requested mode."""
        if mode == MatchMode.TENANT_ONLY:
            return self._tenant_matches(interface_name, sender_adapter,
                                        receiver_adapter, tenant_artifacts,
                                        top_per_source)
        if mode == MatchMode.HUB_ONLY:
            return self._hub_matches(interface_name, sender_adapter,
                                     receiver_adapter, target_id,
                                     top_per_source)
        if mode == MatchMode.GITHUB_ONLY:
            return self._github_matches(interface_name, sender_adapter,
                                        receiver_adapter, top_per_source)

        # FALLBACK_CHAIN — tenant first, extend if thin
        results = self._tenant_matches(interface_name, sender_adapter,
                                       receiver_adapter, tenant_artifacts,
                                       top_per_source)
        if len(results) < FALLBACK_THRESHOLD:
            results = self._dedup(results + self._hub_matches(
                interface_name, sender_adapter, receiver_adapter,
                target_id, top_per_source))
        if len(results) < FALLBACK_THRESHOLD:
            results = self._dedup(results + self._github_matches(
                interface_name, sender_adapter, receiver_adapter,
                top_per_source))
        return results

    # ── Source-specific fetchers ──────────────────────────────────────

    def _tenant_matches(self, interface_name, sender_adapter, receiver_adapter,
                        tenant_artifacts, top) -> list[MatchResult]:
        if not self.cpi_fetcher or not tenant_artifacts:
            return []
        try:
            suggestions = self.cpi_fetcher.suggest_matches(
                interface_name, sender_adapter, receiver_adapter,
                tenant_artifacts)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Tenant match failed: %s", exc)
            return []
        results = []
        for score, art in suggestions[:top]:
            results.append(MatchResult(
                source=MatchSource.TENANT,
                id=art.id, name=art.name,
                score=score,
                package_id=getattr(art, "package_id", ""),
                description=getattr(art, "description", ""),
                raw=art,
            ))
        return results

    def _hub_matches(self, interface_name, sender_adapter, receiver_adapter,
                     target_id, top) -> list[MatchResult]:
        if not self.hub_client:
            return []
        try:
            scored = self.hub_client.search_for_interface(
                interface_name, sender_adapter, receiver_adapter, target_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("Hub search failed: %s", exc)
            return []
        results = []
        for score, pkg in scored[:top]:
            results.append(MatchResult(
                source=MatchSource.HUB,
                id=pkg.id, name=pkg.name,
                score=score,
                description=pkg.short_text,
                url=pkg.url,
                artifact_count=pkg.artifact_count,
                raw=pkg,
            ))
        return results

    def _github_matches(self, interface_name, sender_adapter, receiver_adapter,
                        top) -> list[MatchResult]:
        if not self.samples_browser:
            return []
        try:
            index = self.samples_browser.get_package_index()
        except Exception as exc:  # pragma: no cover
            logger.warning("GitHub samples browse failed: %s", exc)
            return []
        # Score each sample package against the interface
        kw = _keywords(interface_name, sender_adapter, receiver_adapter)
        scored = []
        for pkg in index:
            text = " ".join([pkg.name, pkg.description,
                             " ".join(pkg.tags),
                             " ".join(pkg.detected_adapters)]).lower()
            score = sum(2 for k in kw if k and k in text)
            if score > 0:
                scored.append((score, pkg))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, pkg in scored[:top]:
            results.append(MatchResult(
                source=MatchSource.GITHUB,
                id=pkg.id, name=pkg.name,
                score=score,
                description=pkg.description,
                url=pkg.download_url,
                raw=pkg,
            ))
        return results

    # ── Dedup ─────────────────────────────────────────────────────────

    @staticmethod
    def _dedup(results: list[MatchResult]) -> list[MatchResult]:
        """Hub-preferred deduplication.

        When two results have the same canonical key (normalised name or
        package id), keep the Hub one if present, otherwise keep the first
        encountered. Order is preserved otherwise so the source priority of
        the caller is respected.
        """
        # Group by canonical key
        groups: dict[str, list[MatchResult]] = {}
        order: list[str] = []
        for r in results:
            key = _canonical_key(r)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(r)

        kept = []
        for key in order:
            bucket = groups[key]
            # Hub-preferred selection within the bucket
            hub = next((r for r in bucket if r.source == MatchSource.HUB), None)
            kept.append(hub if hub else bucket[0])
        return kept


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _keywords(interface_name: str, sender_adapter: str, receiver_adapter: str) -> set[str]:
    kw = {w.lower() for w in re.split(r"[_\-\s/]", interface_name or "")
          if len(w) > 3}
    if sender_adapter:
        kw.add(sender_adapter.lower())
    if receiver_adapter:
        kw.add(receiver_adapter.lower())
    return kw


_NORMALISE_RE = re.compile(r"[^a-z0-9]+")


def _canonical_key(result: MatchResult) -> str:
    """Build a comparable key for dedup. Same package across sources should
    hash to the same string."""
    # Prefer package_id when present (most reliable identifier); fall back to
    # a normalised name. Tenant artifacts carry package_id; Hub results have
    # id == package id; GitHub packages have neither and rely on name.
    base = result.package_id or result.id or result.name
    return _NORMALISE_RE.sub("", base.lower())

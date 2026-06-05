"""
destinations/hub_fetcher.py

Fetches SAP integration content metadata from:
  PRIMARY:  SAP GitHub (apibusinesshub-integration-recipes) — no auth required
  FALLBACK: Built-in static catalog — works fully offline

SAP's api.sap.com Hub enforces an IP allowlist that blocks server-side
HTTP clients. GitHub mirrors the same content and is fully accessible.

Cache layout (~/.cpi_migrator/cache/):
  pkg_<package_id>.json       — artifact list
  pkg_<package_id>.meta.json  — fetched_at, ttl, expires_at
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR   = Path.home() / ".cpi_migrator" / "cache"
DEFAULT_TTL         = 86_400   # 24 h
STALE_WARN_SECS     = 3_600    # warn if stale cache >1 h old

# ---------------------------------------------------------------------------
# GitHub source — SAP samples repo (open, no auth)
# ---------------------------------------------------------------------------

GITHUB_RAW   = "https://raw.githubusercontent.com/SAP-samples/apibusinesshub-integration-recipes/master/Recipes"
GITHUB_API   = "https://api.github.com/repos/SAP-samples/apibusinesshub-integration-recipes/contents/Recipes"

# ---------------------------------------------------------------------------
# Static catalog — built-in artifact list per package
# Used when GitHub is unavailable or rate-limited.
# Keep this updated as SAP releases new standard content.
# ---------------------------------------------------------------------------

STATIC_CATALOG: dict[str, list[dict]] = {
    "SAPS4HANACloud": [
        {"Id": "S4HC_PO_Replication",       "Title": "Purchase Order Replication to S4HANA Cloud",       "Type": "IntegrationFlow", "ShortText": "Replicates purchase orders from on-premise ERP to S/4HANA Cloud"},
        {"Id": "S4HC_Sales_Order",           "Title": "Sales Order Integration S4HANA Cloud",             "Type": "IntegrationFlow", "ShortText": "Sales order creation and replication for S/4HANA Cloud"},
        {"Id": "S4HC_Invoice_Processing",    "Title": "Invoice Processing S4HANA Cloud",                  "Type": "IntegrationFlow", "ShortText": "Inbound invoice processing via IDoc to S/4HANA Cloud"},
        {"Id": "S4HC_Employee_Replication",  "Title": "Employee Master Data Replication",                 "Type": "IntegrationFlow", "ShortText": "Replicate employee master data to S/4HANA Cloud"},
        {"Id": "S4HC_Material_Master",       "Title": "Material Master Replication S4HANA Cloud",         "Type": "IntegrationFlow", "ShortText": "Material master data sync from ECC to S/4HANA Cloud"},
        {"Id": "S4HC_GoodsReceipt",          "Title": "Goods Receipt Notification S4HANA Cloud",          "Type": "IntegrationFlow", "ShortText": "Goods receipt IDoc processing for S/4HANA Cloud"},
        {"Id": "S4HC_CostCenter",            "Title": "Cost Center Replication S4HANA Cloud",             "Type": "IntegrationFlow", "ShortText": "Cost center master data replication"},
        {"Id": "S4HC_PaymentAdvice",         "Title": "Payment Advice Processing S4HANA Cloud",           "Type": "IntegrationFlow", "ShortText": "Inbound payment advice via SOAP to S/4HANA Cloud"},
    ],
    "SAPIntegrationSuiteS4HANACloud": [
        {"Id": "IS_S4HC_B2B_PO",            "Title": "B2B Purchase Order S4HANA Cloud",                  "Type": "IntegrationFlow", "ShortText": "B2B PO integration using Integration Suite for S/4HANA Cloud"},
        {"Id": "IS_S4HC_API_Management",     "Title": "API Management Integration S4HANA Cloud",          "Type": "IntegrationFlow", "ShortText": "API-based integration pattern for S/4HANA Cloud"},
    ],
    "SAPS4HANAOnPremise": [
        {"Id": "S4OP_IDoc_Inbound",          "Title": "IDoc Inbound Processing S4HANA On-Premise",        "Type": "IntegrationFlow", "ShortText": "Generic IDoc inbound for S/4HANA On-Premise"},
        {"Id": "S4OP_RFC_Wrapper",           "Title": "RFC to OData Wrapper S4HANA On-Premise",           "Type": "IntegrationFlow", "ShortText": "Wraps RFC/BAPI calls as OData service for S/4HANA OP"},
        {"Id": "S4OP_SOAP_Inbound",          "Title": "SOAP Service Integration S4HANA On-Premise",       "Type": "IntegrationFlow", "ShortText": "SOAP-based inbound integration for S/4HANA On-Premise"},
        {"Id": "S4OP_File_Transfer",         "Title": "File to S4HANA On-Premise Transfer",               "Type": "IntegrationFlow", "ShortText": "File-based data ingestion for S/4HANA On-Premise via SFTP"},
        {"Id": "S4OP_PO_Replication",        "Title": "Purchase Order Replication On-Premise",            "Type": "IntegrationFlow", "ShortText": "PO replication between ECC and S/4HANA On-Premise"},
        {"Id": "S4OP_Material_Master",       "Title": "Material Master S4HANA On-Premise",                "Type": "IntegrationFlow", "ShortText": "Material master replication for S/4HANA On-Premise"},
    ],
    "SAPAriba": [
        {"Id": "Ariba_PO_Outbound",          "Title": "Purchase Order Outbound to Ariba Network",         "Type": "IntegrationFlow", "ShortText": "Sends purchase orders from SAP ERP to Ariba Network via cXML"},
        {"Id": "Ariba_Invoice_Inbound",      "Title": "Invoice Inbound from Ariba Network",               "Type": "IntegrationFlow", "ShortText": "Receives supplier invoices from Ariba Network"},
        {"Id": "Ariba_GR_Confirmation",      "Title": "Goods Receipt Confirmation to Ariba",              "Type": "IntegrationFlow", "ShortText": "Sends goods receipt confirmations to Ariba Network"},
        {"Id": "Ariba_Catalog_Integration",  "Title": "Catalog Integration with Ariba",                   "Type": "IntegrationFlow", "ShortText": "Catalog punchout and content integration with Ariba"},
        {"Id": "Ariba_Sourcing",             "Title": "Sourcing Event Integration Ariba",                  "Type": "IntegrationFlow", "ShortText": "Sourcing event data sync between SAP and Ariba Sourcing"},
    ],
    "SAPAribaNetworkIntegration": [
        {"Id": "AN_OrderConfirmation",       "Title": "Order Confirmation from Ariba Network",            "Type": "IntegrationFlow", "ShortText": "Order confirmation processing from Ariba Network suppliers"},
        {"Id": "AN_ShipNotice",              "Title": "Ship Notice from Ariba Network",                   "Type": "IntegrationFlow", "ShortText": "Advance ship notice (ASN) inbound from Ariba Network"},
    ],
    "SAPSuccessFactors": [
        {"Id": "SF_Employee_Central",        "Title": "Employee Central Replication to SAP",              "Type": "IntegrationFlow", "ShortText": "Replicates employee master data from SuccessFactors EC to SAP"},
        {"Id": "SF_Payroll_Integration",     "Title": "Payroll Integration SuccessFactors",               "Type": "IntegrationFlow", "ShortText": "Payroll data integration between SuccessFactors and SAP Payroll"},
        {"Id": "SF_Position_Sync",           "Title": "Position Management Sync SuccessFactors",          "Type": "IntegrationFlow", "ShortText": "Bidirectional position data sync for SuccessFactors"},
        {"Id": "SF_Learning_Integration",    "Title": "Learning Management Integration SF",               "Type": "IntegrationFlow", "ShortText": "Learning completion data sync from SuccessFactors LMS"},
        {"Id": "SF_Recruitment_Integration", "Title": "Recruitment to Onboarding SuccessFactors",         "Type": "IntegrationFlow", "ShortText": "Candidate data transfer from SF Recruiting to Onboarding"},
    ],
    "SAPSuccessFactorsEmployeeCentral": [
        {"Id": "SFEC_Cost_Center_Sync",      "Title": "Cost Center Sync SuccessFactors EC",               "Type": "IntegrationFlow", "ShortText": "Cost center replication from SAP to SuccessFactors EC"},
        {"Id": "SFEC_Org_Sync",             "Title": "Org Structure Sync SuccessFactors EC",              "Type": "IntegrationFlow", "ShortText": "Organisational structure sync for SuccessFactors Employee Central"},
    ],
    "SAPBTPIntegration": [
        {"Id": "BTP_Workflow_Trigger",       "Title": "BTP Workflow Service Trigger",                     "Type": "IntegrationFlow", "ShortText": "Triggers SAP BTP Workflow Service from external events"},
        {"Id": "BTP_HANA_Cloud_OData",       "Title": "HANA Cloud OData Integration",                    "Type": "IntegrationFlow", "ShortText": "Exposes HANA Cloud data via OData through CPI"},
        {"Id": "BTP_Event_Mesh",             "Title": "Advanced Event Mesh Integration BTP",              "Type": "IntegrationFlow", "ShortText": "Publishes and consumes events via BTP Advanced Event Mesh"},
        {"Id": "BTP_CAP_Extension",          "Title": "CAP Extension Integration BTP",                   "Type": "IntegrationFlow", "ShortText": "Integrates SAP CAP applications on BTP with backend systems"},
    ],
    "SAPAdvancedEventMesh": [
        {"Id": "AEM_IDoc_Bridge",            "Title": "IDoc to Event Mesh Bridge",                       "Type": "IntegrationFlow", "ShortText": "Converts IDoc messages to events on Advanced Event Mesh"},
        {"Id": "AEM_AMQP_Consumer",          "Title": "AMQP Consumer Advanced Event Mesh",               "Type": "IntegrationFlow", "ShortText": "Consumes AMQP messages from Advanced Event Mesh queues"},
        {"Id": "AEM_Topic_Publisher",        "Title": "Topic Publisher Advanced Event Mesh",              "Type": "IntegrationFlow", "ShortText": "Publishes integration events to AEM topics"},
    ],
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

class CacheEntry:
    def __init__(self, cache_dir: Path, key: str):
        self.data_path = cache_dir / f"{key}.json"
        self.meta_path = cache_dir / f"{key}.meta.json"

    def exists(self) -> bool:
        return self.data_path.exists() and self.meta_path.exists()

    def read(self) -> Optional[dict]:
        try:
            return json.loads(self.data_path.read_text("utf-8"))
        except Exception:
            return None

    def age_seconds(self) -> float:
        if not self.meta_path.exists():
            return float("inf")
        meta = json.loads(self.meta_path.read_text("utf-8"))
        return time.time() - meta.get("fetched_at", 0)

    def is_fresh(self, ttl: int) -> bool:
        return self.age_seconds() < ttl

    def write(self, data: dict, ttl: int, source_url: str = ""):
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text(json.dumps(data, indent=2), "utf-8")
        meta = {
            "fetched_at": time.time(),
            "ttl": ttl,
            "source_url": source_url,
            "expires_at": time.time() + ttl,
        }
        self.meta_path.write_text(json.dumps(meta, indent=2), "utf-8")

    def ttl_remaining(self) -> int:
        if not self.meta_path.exists():
            return 0
        meta = json.loads(self.meta_path.read_text("utf-8"))
        return max(0, int(meta.get("expires_at", 0) - time.time()))


# ---------------------------------------------------------------------------
# Hub Fetcher
# ---------------------------------------------------------------------------

class HubFetcher:
    """
    Fetches SAP integration content with local JSON cache + TTL.

    Source priority:
      1. Local cache (if fresh)
      2. SAP GitHub mirror (open, no auth)
      3. Built-in static catalog (always available offline)
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        default_ttl: int = DEFAULT_TTL,
        hub_api_key: Optional[str] = None,   # kept for config compatibility, not used
    ):
        self.cache_dir   = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self.session     = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "CPI-Migration-Scaffolder/1.0",
        })

    # ── Public API ───────────────────────────────────────────────────

    def get_package_artifacts(
        self,
        package_id: str,
        ttl: Optional[int] = None,
        force_refresh: bool = False,
    ) -> dict:
        effective_ttl = ttl or self.default_ttl
        cache = CacheEntry(self.cache_dir, f"pkg_{package_id}")

        if not force_refresh and cache.is_fresh(effective_ttl):
            logger.debug("Cache HIT %s (TTL remaining: %ds)", package_id, cache.ttl_remaining())
            return cache.read() or {}

        logger.info("Cache MISS/STALE for %s — fetching …", package_id)
        try:
            data = self._fetch_from_github(package_id)
            if data.get("value"):
                cache.write(data, effective_ttl, source_url="github")
                logger.info("✓ Fetched %d artifacts for %s from GitHub",
                            len(data["value"]), package_id)
                return data
        except Exception as exc:
            logger.debug("GitHub fetch failed for %s: %s", package_id, exc)

        # Fall back to static catalog
        data = self._fetch_from_static(package_id)
        cache.write(data, effective_ttl, source_url="static")
        count = len(data.get("value", []))
        if count:
            logger.info("✓ Loaded %d artifacts for %s from built-in catalog", count, package_id)
        else:
            logger.debug("No catalog entry for %s", package_id)
        return data

    def get_all_for_target(self, target_id: str, ttl: Optional[int] = None) -> dict:
        from destinations.registry import get_target
        target = get_target(target_id)
        result = {}
        for source in target.hub_sources:
            data = self.get_package_artifacts(
                source.package_id, ttl=ttl or target.cache_ttl_seconds
            )
            result[source.package_id] = {
                "label":     source.label,
                "artifacts": data.get("value", []),
                "count":     len(data.get("value", [])),
            }
        return result

    def cache_status(self) -> list[dict]:
        status = []
        for meta_path in self.cache_dir.glob("*.meta.json"):
            try:
                meta      = json.loads(meta_path.read_text("utf-8"))
                key       = meta_path.stem.replace(".meta", "")
                data_path = meta_path.parent / f"{key}.json"
                status.append({
                    "key":           key,
                    "fetched_at":    meta.get("fetched_at", 0),
                    "ttl":           meta.get("ttl", 0),
                    "ttl_remaining": max(0, int(meta.get("expires_at", 0) - time.time())),
                    "size_bytes":    data_path.stat().st_size if data_path.exists() else 0,
                    "source_url":    meta.get("source_url", ""),
                    "fresh":         time.time() < meta.get("expires_at", 0),
                })
            except Exception:
                continue
        return sorted(status, key=lambda x: x["fetched_at"], reverse=True)

    def invalidate(self, package_id: str):
        for suffix in (".json", ".meta.json"):
            p = self.cache_dir / f"pkg_{package_id}{suffix}"
            if p.exists():
                p.unlink()

    def invalidate_all(self):
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
        logger.info("Cache cleared: %s", self.cache_dir)

    # ── Async bulk refresh ───────────────────────────────────────────

    async def refresh_all_async(
        self,
        package_ids: list[str],
        ttl: Optional[int] = None,
        concurrency: int = 4,
    ) -> dict[str, bool]:
        try:
            import aiohttp
        except ImportError:
            return self._refresh_sequential(package_ids, ttl)

        effective_ttl = ttl or self.default_ttl
        semaphore     = asyncio.Semaphore(concurrency)
        results: dict[str, bool] = {}

        async def fetch_one(session, pkg_id: str):
            async with semaphore:
                # Try GitHub first
                url = f"{GITHUB_API}/{pkg_id}"
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        if resp.status == 200:
                            raw   = await resp.json(content_type=None)
                            items = self._parse_github_contents(raw, pkg_id)
                            data  = {"value": items}
                            CacheEntry(self.cache_dir, f"pkg_{pkg_id}").write(
                                data, effective_ttl, url
                            )
                            logger.info("✓ GitHub refreshed %s (%d artifacts)", pkg_id, len(items))
                            results[pkg_id] = True
                            return
                except Exception:
                    pass
                # Fall back to static
                data = self._fetch_from_static(pkg_id)
                CacheEntry(self.cache_dir, f"pkg_{pkg_id}").write(
                    data, effective_ttl, "static"
                )
                results[pkg_id] = True

        async with aiohttp.ClientSession(
            headers={"Accept": "application/json",
                     "User-Agent": "CPI-Migration-Scaffolder/1.0"}
        ) as session:
            await asyncio.gather(*[fetch_one(session, pid) for pid in package_ids])

        return results

    def _refresh_sequential(self, package_ids: list[str], ttl: Optional[int]) -> dict[str, bool]:
        results = {}
        for pid in package_ids:
            try:
                self.get_package_artifacts(pid, ttl=ttl, force_refresh=True)
                results[pid] = True
            except Exception:
                results[pid] = False
        return results

    # ── Fetch strategies ─────────────────────────────────────────────

    def _fetch_from_github(self, package_id: str) -> dict:
        url  = f"{GITHUB_API}/{package_id}"
        resp = self.session.get(url, timeout=20)
        if resp.status_code == 404:
            return {"value": []}
        if resp.status_code == 403:
            raise ConnectionError("GitHub rate limit hit")
        resp.raise_for_status()
        items = self._parse_github_contents(resp.json(), package_id)
        return {"value": items}

    def _fetch_from_static(self, package_id: str) -> dict:
        items = STATIC_CATALOG.get(package_id, [])
        return {"value": items}

    @staticmethod
    def _parse_github_contents(contents: list, package_id: str) -> list[dict]:
        """Convert GitHub directory listing into artifact dicts."""
        if not isinstance(contents, list):
            return []
        artifacts = []
        for item in contents:
            name = item.get("name", "")
            if item.get("type") == "dir" and not name.startswith("."):
                artifacts.append({
                    "Id":        name,
                    "Title":     name.replace("_", " ").replace("-", " "),
                    "Type":      "IntegrationFlow",
                    "ShortText": f"{name} — from SAP GitHub {package_id}",
                })
        return artifacts


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_default_fetcher: Optional[HubFetcher] = None

def get_fetcher(hub_api_key: Optional[str] = None) -> HubFetcher:
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = HubFetcher(hub_api_key=hub_api_key)
    return _default_fetcher

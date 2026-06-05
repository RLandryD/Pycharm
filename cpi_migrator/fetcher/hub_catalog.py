"""
fetcher/hub_catalog.py

Authenticates with SAP Business Accelerator Hub using API key → bearer token
exchange, then queries the full integration content catalog.

Provides access to:
  - 3,400+ integration packages
  - iFlow artifact lists per package
  - Direct download URLs for standard content
  - Business event catalog
  - API specifications

Auth flow:
  POST https://api.sap.com/oauth2/token
  Header: APIKey: <your-key>
  → returns bearer token

Then all catalog calls use:
  Authorization: Bearer <token>
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

HUB_TOKEN_URL    = "https://api.sap.com/oauth2/token"
HUB_CATALOG_URL  = "https://api.sap.com/odata/1.0/catalog.svc"
HUB_CONTENT_URL  = "https://api.sap.com/odata/1.0/catalog.svc/ContentEntities.ContentPackages"
HUB_PACKAGE_URL  = "https://api.sap.com/package/{pid}"
HUB_ARTIFACT_URL = "https://api.sap.com/odata/1.0/catalog.svc/ContentEntities.ContentPackages('{pid}')/ContentEntities.IntegrationFlows"

DEFAULT_CACHE = Path.home() / ".cpi_migrator" / "hub_catalog"

# Process-level negative cache: set True after the catalog API returns a hard
# failure (400/401/403) so we don't retry the doomed call on every rerun.
_CATALOG_API_DEAD = False


@dataclass
class HubPackage:
    id: str
    name: str
    short_text: str = ""
    version: str = ""
    vendor: str = "SAP"
    categories: list[str] = field(default_factory=list)
    artifact_count: int = 0
    url: str = ""


@dataclass
class HubArtifact:
    id: str
    name: str
    package_id: str
    artifact_type: str = "IntegrationFlow"
    short_text: str = ""
    version: str = ""
    sender_adapter: str = ""
    receiver_adapter: str = ""


class HubCatalogClient:
    """
    Full SAP Business Accelerator Hub catalog client.
    Uses API key → bearer token exchange for server-side access.
    """

    def __init__(
        self,
        api_key: str,
        cache_dir: Optional[Path] = None,
        cache_ttl_hours: int = 24,
    ):
        self.api_key       = api_key
        self.cache_dir     = Path(cache_dir) if cache_dir else DEFAULT_CACHE
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl     = cache_ttl_hours * 3600
        self._token: str   = ""
        self._token_expiry = 0.0
        self.session       = requests.Session()
        self.session.headers.update({
            "Accept":     "application/json",
            "User-Agent": "CPI-Migration-Scaffolder/1.0",
        })

    # ── Auth ──────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Exchange API key for bearer token. Caches until expiry."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        # Try token endpoint
        try:
            resp = requests.post(
                HUB_TOKEN_URL,
                headers={"APIKey": self.api_key, "Accept": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._token        = data.get("access_token", "")
                expires_in         = int(data.get("expires_in", 3600))
                self._token_expiry = time.time() + expires_in
                logger.info("Hub bearer token obtained, expires in %ds", expires_in)
                return self._token
        except Exception as exc:
            logger.debug("Token endpoint failed: %s", exc)

        # Fallback: use API key directly as header
        logger.info("Using API key directly (token exchange unavailable)")
        self._token        = self.api_key
        self._token_expiry = time.time() + 3600
        return self._token

    def _auth_headers(self) -> dict:
        token = self._get_token()
        if token == self.api_key:
            return {"APIKey": self.api_key}
        return {"Authorization": f"Bearer {token}"}

    def _get(self, url: str, params: dict = None) -> Optional[dict]:
        """Make authenticated GET request with cache fallback.

        Negative cache: once the catalog API has returned a hard failure (e.g.
        the persistent 400 on trial tenants without Hub entitlement), every
        later call in this process short-circuits to the local cache instead of
        re-hitting the network. This stops the same 400 from being logged and
        retried on every Streamlit rerun (it was firing ~6× per render)."""
        global _CATALOG_API_DEAD
        if _CATALOG_API_DEAD:
            return None
        try:
            headers = {**self.session.headers, **self._auth_headers()}
            resp    = requests.get(url, headers=headers,
                                   params=params, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Hub API %s returned %d", url[:60], resp.status_code)
            if resp.status_code in (400, 401, 403):
                # entitlement/auth failure won't fix itself within the session
                _CATALOG_API_DEAD = True
                logger.info("Hub catalog API disabled for this session after "
                            "HTTP %d — using local cache only.", resp.status_code)
            return None
        except Exception as exc:
            logger.warning("Hub API request failed: %s", exc)
            return None

    # ── Catalog ───────────────────────────────────────────────────────

    def search_packages(
        self,
        query: str = "",
        category: str = "",
        top: int = 50,
    ) -> list[HubPackage]:
        """Search Hub packages by keyword or category."""
        cache_key = f"pkg_search_{query}_{category}_{top}".replace(" ", "_")
        cached    = self._read_cache(cache_key)
        if cached:
            return [self._dict_to_package(p) for p in cached]

        params = {
            "$format": "json",
            "$top":    top,
            "$skip":   0,
        }
        if query:
            params["$filter"] = f"substringof('{query}',Title) or substringof('{query}',ShortText)"

        data = self._get(HUB_CONTENT_URL, params)
        if not data:
            return self._fallback_packages(query)

        results = data.get("d", {}).get("results", data.get("value", []))
        packages = []
        for p in results:
            pkg = HubPackage(
                id=p.get("PackageId", p.get("id", "")),
                name=p.get("Title", p.get("title", "")),
                short_text=p.get("ShortText", p.get("description", ""))[:200],
                version=p.get("Version", ""),
                vendor=p.get("Vendor", "SAP"),
                categories=[p.get("Category", "")] if p.get("Category") else [],
                url=f"https://api.sap.com/package/{p.get('PackageId', '')}",
            )
            if pkg.id:
                packages.append(pkg)

        if packages:
            self._write_cache(cache_key, [self._package_to_dict(p) for p in packages])

        return packages or self._fallback_packages(query)

    def get_package_artifacts(self, package_id: str) -> list[HubArtifact]:
        """Get all iFlow artifacts in a package."""
        cache_key = f"artifacts_{package_id}"
        cached    = self._read_cache(cache_key)
        if cached:
            return [self._dict_to_artifact(a) for a in cached]

        url  = HUB_ARTIFACT_URL.format(pid=package_id)
        data = self._get(url, {"$format": "json"})
        if not data:
            return []

        results   = data.get("d", {}).get("results", data.get("value", []))
        artifacts = []
        for a in results:
            art = HubArtifact(
                id=a.get("Id", a.get("id", "")),
                name=a.get("Title", a.get("title", a.get("Name", ""))),
                package_id=package_id,
                artifact_type=a.get("Type", "IntegrationFlow"),
                short_text=a.get("ShortText", a.get("description", ""))[:150],
                version=a.get("Version", ""),
            )
            if art.id:
                artifacts.append(art)

        if artifacts:
            self._write_cache(cache_key, [self._artifact_to_dict(a) for a in artifacts])

        return artifacts

    def search_for_interface(
        self,
        interface_name: str,
        sender_adapter: str,
        receiver_adapter: str,
        target_id: str = "",
    ) -> list[tuple[int, HubPackage]]:
        """
        Find Hub packages matching an interface by keyword + adapter scoring.
        Returns [(score, HubPackage)] sorted by relevance.
        """
        import re
        keywords = set(
            w.lower() for w in re.split(r"[_\-\s/]", interface_name)
            if len(w) > 3
        )
        keywords.update({sender_adapter.lower(), receiver_adapter.lower()})
        if target_id:
            keywords.add(target_id.replace("_", " ").lower())

        # Build search query from top keywords
        query = " ".join(list(keywords)[:3])
        packages = self.search_packages(query=query, top=30)

        scored = []
        for pkg in packages:
            text  = (pkg.name + " " + pkg.short_text + " " +
                     " ".join(pkg.categories)).lower()
            score = sum(2 for kw in keywords if kw in text)
            if score > 0:
                scored.append((score, pkg))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:5]

    # ── Fallback static catalog ───────────────────────────────────────

    def _fallback_packages(self, query: str = "") -> list[HubPackage]:
        """Return known packages from static catalog when API is unavailable."""
        from destinations.hub_fetcher import STATIC_CATALOG
        packages = []
        query_lower = query.lower()
        for pkg_id, artifacts in STATIC_CATALOG.items():
            if not query_lower or query_lower in pkg_id.lower():
                packages.append(HubPackage(
                    id=pkg_id,
                    name=pkg_id.replace("SAP", "SAP ").replace("HANA", "HANA "),
                    short_text=f"{len(artifacts)} standard iFlows",
                    vendor="SAP",
                    artifact_count=len(artifacts),
                    url=f"https://api.sap.com/package/{pkg_id}",
                ))
        return packages

    # ── Cache helpers ─────────────────────────────────────────────────

    def _read_cache(self, key: str) -> Optional[list]:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.cache_ttl:
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            return data.get("items")
        except Exception:
            return None

    def _write_cache(self, key: str, items: list):
        path = self.cache_dir / f"{key}.json"
        path.write_text(json.dumps({"items": items, "cached_at": time.time()},
                                    indent=2), "utf-8")

    @staticmethod
    def _package_to_dict(p: HubPackage) -> dict:
        return {"id": p.id, "name": p.name, "short_text": p.short_text,
                "version": p.version, "vendor": p.vendor,
                "categories": p.categories, "artifact_count": p.artifact_count,
                "url": p.url}

    @staticmethod
    def _dict_to_package(d: dict) -> HubPackage:
        return HubPackage(id=d.get("id",""), name=d.get("name",""),
                          short_text=d.get("short_text",""),
                          version=d.get("version",""), vendor=d.get("vendor","SAP"),
                          categories=d.get("categories",[]),
                          artifact_count=d.get("artifact_count",0),
                          url=d.get("url",""))

    @staticmethod
    def _artifact_to_dict(a: HubArtifact) -> dict:
        return {"id": a.id, "name": a.name, "package_id": a.package_id,
                "artifact_type": a.artifact_type, "short_text": a.short_text,
                "version": a.version}

    @staticmethod
    def _dict_to_artifact(d: dict) -> HubArtifact:
        return HubArtifact(id=d.get("id",""), name=d.get("name",""),
                           package_id=d.get("package_id",""),
                           artifact_type=d.get("artifact_type","IntegrationFlow"),
                           short_text=d.get("short_text",""),
                           version=d.get("version",""))

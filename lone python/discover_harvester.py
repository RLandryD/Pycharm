"""fetcher/discover_harvester.py

Harvest SAP standard / Discover integration packages into local files for
REFERENCE AND LEARNING ONLY.

Why this exists
---------------
The SAP Business Accelerator Hub (api.sap.com) is largely view-only. A tenant
entitled to SAP standard content can obtain that content two ways, both
confirmed against the live Integration Suite tenant:

  STRATEGY B — CATALOG-DIRECT (light, preferred):
    The design catalog serves artifact content directly by id + type, with NO
    copy into the workspace and therefore NO tenant-space usage and NO cleanup:
        GET /odata/1.0/catalog.svc/ContentEntities.<Type>s('<id>')/$value
    Confirmed working for MediaLinks; iFlows/Files use the same shape. Some
    types (e.g. certain IntegrationAdapters) may be denied — skipped gracefully.

  STRATEGY A — COPY → DOWNLOAD → DELETE (fallback):
    For content the catalog won't serve directly, copy it into the tenant
    workspace, then export, then delete the copy to free space:
        POST /api/1.0/workspace?mode=copy   body {"id":"<id>","source":"CATALOG"}
        ... export ...
        DELETE the copied package
    SAFETY: only packages THIS RUN copied in are ever deleted, tracked by id.
    Pre-existing packages are never touched.

Both confirmed call shapes (captured from the tenant's network traffic):
    Copy:            POST  {design}/api/1.0/workspace?mode=copy
                     body {"id": <regId>, "source": "CATALOG"}, X-CSRF-Token
    Validate copy:   GET   {design}/api/1.0/integration-packages/<id>?validatepackagecopy=true
    Catalog $value:  GET   {design}/odata/1.0/catalog.svc/ContentEntities.<Type>s('<id>')/$value
    Copy response:   {listOfStatus:[{responseInfo:{Type,Title,id,...}}, ...]}
                     Type in {IntegrationFlow, IntegrationAdapter, File, Url,
                              MediaLink, ContentPackage}

Hosts
-----
The Discover/catalog calls live on the DESIGN host
(`*.integrationsuite-trial.*`), which differs from the runtime OData API host
(`*.it-cpitrial05.*`) used for upload/deploy. Pass `design_base_url` for the
former; `runtime_base_url` is optional and only used by the workspace-export
fallback.

IP / clean-room boundary
------------------------
Downloads SAP's published standard content to your LOCAL cache for your own
working reference (e.g. understanding a pattern while fixing a client problem),
which is legitimate given tenant entitlement. It must NOT be used to fold SAP's
shipped artifacts into a redistributed library as if they were your own.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("fetcher.discover_harvester")


# Map the Type values seen in a copy's listOfStatus to the catalog entity set
# used for /odata/1.0/catalog.svc/ContentEntities.<EntitySet>('<id>')/$value
_TYPE_TO_ENTITYSET = {
    "IntegrationFlow":    "IntegrationFlows",
    "IntegrationAdapter": "IntegrationAdapters",
    "File":               "Files",
    "MediaLink":          "MediaLinks",
    "ValueMapping":       "ValueMappings",
    "MessageMapping":     "MessageMappings",
    "ScriptCollection":   "ScriptCollections",
    # "Url" has no downloadable $value (it's an external link) — skipped.
}

# Extension chosen per type for the saved file.
_TYPE_TO_EXT = {
    "IntegrationFlow": ".zip", "IntegrationAdapter": ".zip",
    "ValueMapping": ".zip", "MessageMapping": ".zip",
    "ScriptCollection": ".zip", "File": ".bin", "MediaLink": ".bin",
}


@dataclass
class AssetResult:
    asset_id: str
    asset_type: str
    title: str
    downloaded: bool = False
    bytes_len: int = 0
    path: str = ""
    reason: str = ""        # why skipped/failed


@dataclass
class HarvestResult:
    package_id: str
    strategy: str = ""              # "catalog" | "copy"
    copied: bool = False
    deleted: bool = False
    assets: list = field(default_factory=list)   # list[AssetResult]
    errors: list = field(default_factory=list)

    @property
    def n_downloaded(self) -> int:
        return sum(1 for a in self.assets if a.downloaded)


class DiscoverHarvester:
    """Two-strategy harvester over Discover/standard packages.

    Parameters
    ----------
    design_base_url : the *.integrationsuite-trial.* host (copy + catalog).
    session         : an authenticated requests.Session (same auth as the app).
    runtime_base_url: optional *.it-cpitrial05.* host (workspace-export fallback).
    download_dir    : where harvested files are written.
    """

    def __init__(self, design_base_url: str, session: requests.Session,
                 runtime_base_url: str = "", download_dir: Optional[Path] = None):
        self.design = design_base_url.rstrip("/")
        self.runtime = runtime_base_url.rstrip("/")
        self.session = session
        self.download_dir = Path(download_dir) if download_dir else (
            Path.home() / ".cpi_migrator" / "discover_downloads")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._csrf: Optional[str] = None

    # ── CSRF on the design host ──────────────────────────────────────────
    def _ensure_csrf(self) -> Optional[str]:
        if self._csrf:
            return self._csrf
        # The design host issues CSRF from its base; a HEAD/GET with Fetch works.
        for path in ("/api/1.0/workspace", "/odata/1.0/catalog.svc/"):
            try:
                resp = self.session.get(f"{self.design}{path}",
                                        headers={"X-CSRF-Token": "Fetch"}, timeout=15)
                tok = resp.headers.get("x-csrf-token") or resp.headers.get("X-CSRF-Token")
                if tok and tok.lower() != "required":
                    self._csrf = tok
                    return self._csrf
            except Exception:
                continue
        return self._csrf

    def _write_headers(self) -> dict:
        hdrs = {"Content-Type": "application/json"}
        tok = self._ensure_csrf()
        if tok:
            hdrs["X-CSRF-Token"] = tok
        return hdrs

    # ── List all Discover packages (confirmed endpoint) ─────────────────
    def list_discover_packages(self, top: int = 1000) -> list[dict]:
        """Return Discover/catalog packages with reg ids and inline assets.

        Confirmed endpoint (captured from the live tenant):
            GET {design}/odata/1.0/catalog.svc/ContentEntities.ContentPackages
                ?$format=json&$top=N   ->  {d:{results:[...]}}

        Each row carries reg_id, TechnicalName, DisplayName, Vendor, Type, plus
        the asset arrays (Artifacts/Files/Urls/MediaLinks) already inline — so
        callers can enumerate downloadable assets without a second request.

        Returns [{id, technical_name, name, vendor, type, assets:[{id,type,title}]}].
        """
        from fetcher import wire_log
        url = (f"{self.design}/odata/1.0/catalog.svc/"
               f"ContentEntities.ContentPackages")
        try:
            wire_log.log_request("list discover packages", "GET", url)
            resp = self.session.get(url, params={"$format": "json", "$top": top},
                                    headers={"Accept": "application/json"},
                                    timeout=60)
            wire_log.log_response("list discover packages", resp.status_code,
                                  dict(resp.headers), resp.text[:200])
            if resp.status_code != 200:
                # fall back to the alternate collection if the primary 4xx's
                return self._list_discover_packages_fallback(top)
            rows = resp.json().get("d", {}).get("results", [])
            out = []
            for p in rows:
                rid = p.get("reg_id") or p.get("TechnicalName") or ""
                if not rid:
                    continue
                out.append({
                    "id": rid,
                    "technical_name": p.get("TechnicalName", ""),
                    "name": p.get("DisplayName") or p.get("TechnicalName") or rid,
                    "vendor": p.get("Vendor", ""),
                    "type": p.get("Type", ""),
                    "assets": self._inline_assets(p),
                })
            logger.info("Listed %d Discover packages", len(out))
            return out
        except Exception as exc:
            logger.warning("list_discover_packages failed: %s", exc)
            return self._list_discover_packages_fallback(top)

    @staticmethod
    def _inline_assets(pkg_row: dict) -> list[dict]:
        """Pull downloadable assets straight from a ContentPackages row.

        Artifacts (iFlows etc.), Files, MediaLinks carry $value content; Urls
        are external links (not downloadable) and are skipped. Each OData nav
        property may be an inline list or a deferred {__deferred} stub — only
        inline lists are usable here."""
        assets = []
        for key, default_type in (("Artifacts", "IntegrationFlow"),
                                   ("Files", "File"),
                                   ("MediaLinks", "MediaLink")):
            val = pkg_row.get(key)
            results = None
            if isinstance(val, dict):
                results = val.get("results")
            elif isinstance(val, list):
                results = val
            if not results:
                continue
            for a in results:
                aid = a.get("reg_id") or a.get("Id") or a.get("id") or ""
                atype = a.get("Type") or default_type
                title = a.get("DisplayName") or a.get("Title") or a.get("Name") or aid
                if aid:
                    assets.append({"id": aid, "type": atype, "title": title})
        return assets

    def _list_discover_packages_fallback(self, top: int) -> list[dict]:
        try:
            resp = self.session.get(
                f"{self.design}/api/1.0/integration-packages",
                headers={"Accept": "application/json"}, timeout=60)
            if resp.status_code != 200:
                return []
            data = resp.json()
            rows = (data if isinstance(data, list)
                    else data.get("integrationPackages") or data.get("value") or [])
            out = []
            for p in rows:
                rid = p.get("reg_id") or p.get("id") or p.get("technicalName") or ""
                if rid:
                    out.append({"id": rid, "technical_name": p.get("technicalName", ""),
                                "name": p.get("displayName") or p.get("name") or rid,
                                "vendor": p.get("vendor", ""), "type": p.get("type", ""),
                                "assets": []})
            return out
        except Exception:
            return []

    # ── Enumerate a package's assets (without copying) ───────────────────
    def list_package_assets(self, package_reg_id: str) -> list[dict]:
        """Read a Discover package's asset list from the catalog.

        Returns [{id, type, title}]. Uses the integration-packages detail
        endpoint; falls back to empty list on failure (caller may still try
        a copy, whose response also lists assets)."""
        url = (f"{self.design}/api/1.0/integration-packages/"
               f"{package_reg_id}?validatepackagecopy=true")
        try:
            resp = self.session.get(url, headers={"Accept": "application/json"},
                                    timeout=30)
            if resp.status_code != 200:
                return []
            data = resp.json()
            # The detail payload nests artifacts under various keys depending on
            # tenant version; try the common ones.
            assets = []
            candidates = (data.get("artifacts") or data.get("Artifacts") or
                          data.get("contentEntities") or [])
            for a in candidates:
                aid = a.get("id") or a.get("Id") or a.get("reg_id")
                atype = a.get("type") or a.get("Type") or ""
                title = a.get("title") or a.get("Title") or aid
                if aid and atype:
                    assets.append({"id": aid, "type": atype, "title": title})
            return assets
        except Exception as exc:
            logger.warning("list_package_assets failed for %s: %s",
                           package_reg_id, exc)
            return []

    # ── STRATEGY B: catalog-direct download (no copy) ────────────────────
    def download_catalog_asset(self, asset_id: str, asset_type: str) -> Optional[bytes]:
        """GET /odata/1.0/catalog.svc/ContentEntities.<Type>s('<id>')/$value.

        Returns content bytes, or None if not downloadable / denied."""
        entityset = _TYPE_TO_ENTITYSET.get(asset_type)
        if not entityset:
            return None
        url = (f"{self.design}/odata/1.0/catalog.svc/"
               f"ContentEntities.{entityset}('{asset_id}')/$value")
        try:
            from fetcher import wire_log
            wire_log.log_request(f"catalog $value [{asset_type}]", "GET", url)
            resp = self.session.get(url, timeout=120)
            wire_log.log_response(f"catalog $value [{asset_type}]",
                                  resp.status_code, dict(resp.headers), "")
            if resp.status_code == 200 and resp.content:
                return resp.content
            return None
        except Exception as exc:
            logger.warning("catalog download failed for %s: %s", asset_id, exc)
            return None

    # ── STRATEGY A: copy → (workspace export) → delete ───────────────────
    def copy_package(self, package_reg_id: str) -> Optional[dict]:
        """POST /api/1.0/workspace?mode=copy  {"id":..,"source":"CATALOG"}.

        Returns the parsed listOfStatus payload (so the caller can enumerate
        what landed), or None on failure."""
        from fetcher import wire_log
        url = f"{self.design}/api/1.0/workspace?mode=copy"
        body = json.dumps({"id": package_reg_id, "source": "CATALOG"})
        hdrs = self._write_headers()
        try:
            wire_log.log_request("copy discover package", "POST", url, hdrs, body)
            resp = self.session.post(url, data=body, headers=hdrs, timeout=90)
            wire_log.log_response("copy discover package", resp.status_code,
                                  dict(resp.headers), resp.text[:600])
            if resp.status_code in (200, 201):
                try:
                    return resp.json()
                except Exception:
                    return {"listOfStatus": []}
            logger.warning("Copy returned %d for %s", resp.status_code, package_reg_id)
            return None
        except Exception as exc:
            logger.warning("Copy failed for %s: %s", package_reg_id, exc)
            return None

    @staticmethod
    def assets_from_copy_response(payload: dict) -> list[dict]:
        """Extract [{id,type,title}] from a copy's listOfStatus."""
        out = []
        for entry in (payload or {}).get("listOfStatus", []):
            info = entry.get("responseInfo", {})
            aid = info.get("id")
            atype = info.get("Type", "")
            title = info.get("Title", aid)
            if aid and atype:
                out.append({"id": aid, "type": atype, "title": title})
        return out

    @staticmethod
    def package_id_from_copy_response(payload: dict) -> str:
        """Find the ContentPackage id/TechnicalName in a copy response, so we
        know which package to delete in cleanup."""
        for entry in (payload or {}).get("listOfStatus", []):
            info = entry.get("responseInfo", {})
            if info.get("Type") == "ContentPackage":
                return info.get("TechnicalName") or info.get("id") or ""
        return ""

    def delete_package(self, package_id: str) -> bool:
        """DELETE a workspace package (runtime OData host if available, else
        design host). Used only for packages this run copied in."""
        from fetcher import wire_log
        base = self.runtime or self.design
        url = f"{base}/api/v1/IntegrationPackages('{package_id}')"
        hdrs = self._write_headers()
        try:
            wire_log.log_request("delete package", "DELETE", url, hdrs, "")
            resp = self.session.delete(url, headers=hdrs, timeout=30)
            wire_log.log_response("delete package", resp.status_code,
                                  dict(resp.headers), resp.text[:200])
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Delete failed for %s: %s", package_id, exc)
            return False

    # ── per-package harvest (tries B, falls back to A) ──────────────────
    def _save_asset(self, pkg_dir: Path, asset: dict, data: bytes) -> AssetResult:
        ext = _TYPE_TO_EXT.get(asset["type"], ".bin")
        out = pkg_dir / f"{_safe(asset['title'] or asset['id'])}{ext}"
        out.write_bytes(data)
        return AssetResult(asset_id=asset["id"], asset_type=asset["type"],
                           title=asset.get("title", ""), downloaded=True,
                           bytes_len=len(data), path=str(out))

    def harvest_one(self, package_reg_id: str, prefer: str = "catalog",
                    allow_copy_fallback: bool = True,
                    cleanup: bool = True,
                    known_assets: Optional[list] = None) -> HarvestResult:
        res = HarvestResult(package_id=package_reg_id)
        pkg_dir = self.download_dir / _safe(package_reg_id)
        pkg_dir.mkdir(parents=True, exist_ok=True)

        # --- Strategy B: catalog-direct ---
        if prefer == "catalog":
            # Prefer assets already supplied (from the package list payload);
            # otherwise fetch them.
            assets = known_assets if known_assets else self.list_package_assets(package_reg_id)
            if assets:
                res.strategy = "catalog"
                any_dl = False
                for a in assets:
                    data = self.download_catalog_asset(a["id"], a["type"])
                    if data:
                        res.assets.append(self._save_asset(pkg_dir, a, data))
                        any_dl = True
                    else:
                        res.assets.append(AssetResult(
                            asset_id=a["id"], asset_type=a["type"],
                            title=a.get("title", ""), reason="not downloadable via catalog"))
                if any_dl:
                    return res
                # nothing came down directly — fall through to copy if allowed
                if not allow_copy_fallback:
                    return res

        # --- Strategy A: copy → download → delete ---
        if allow_copy_fallback:
            payload = self.copy_package(package_reg_id)
            if not payload:
                res.errors.append("copy failed (and catalog-direct yielded nothing)")
                return res
            res.strategy = "copy"
            res.copied = True
            copied_pkg_id = self.package_id_from_copy_response(payload)
            assets = self.assets_from_copy_response(payload)
            time.sleep(1.0)
            for a in assets:
                if a["type"] in ("ContentPackage", "Url"):
                    continue
                data = self.download_catalog_asset(a["id"], a["type"])
                if data:
                    res.assets.append(self._save_asset(pkg_dir, a, data))
                else:
                    res.assets.append(AssetResult(
                        asset_id=a["id"], asset_type=a["type"],
                        title=a.get("title", ""), reason="not downloadable"))
            # cleanup: delete ONLY the package we just copied
            if cleanup and copied_pkg_id:
                if self.delete_package(copied_pkg_id):
                    res.deleted = True
                else:
                    res.errors.append("cleanup delete failed")
        return res

    # ── batch orchestration ──────────────────────────────────────────────
    def harvest(self, package_reg_ids: list[str], batch_size: int = 5,
                prefer: str = "catalog", allow_copy_fallback: bool = True,
                cleanup: bool = True, progress_cb=None) -> list[HarvestResult]:
        results = []
        total = len(package_reg_ids)
        done = 0
        for start in range(0, total, batch_size):
            for pid in package_reg_ids[start:start + batch_size]:
                r = self.harvest_one(pid, prefer=prefer,
                                     allow_copy_fallback=allow_copy_fallback,
                                     cleanup=cleanup)
                results.append(r)
                done += 1
                if progress_cb:
                    progress_cb(done, total, r)
        return results


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^\w.\-]", "_", str(s))[:80]

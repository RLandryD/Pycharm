"""
fetcher/sap_samples_browser.py

Browses github.com/SAP-samples and github.com/SAP for integration packages and
repos that contain iFlow / integration content. Downloads and classifies them
automatically as test data and migration templates.

Each repo carries its own `org` field; the default is `SAP-samples` when
unspecified for backward compatibility. The two primary recipe repos live
under the SAP organisation, not SAP-samples:

  - SAP/apibusinesshub-integration-recipes   (CPI recipes — official)
  - SAP/apibusinesshub-api-recipes           (API Management recipes — official)

Other known repos scanned (SAP-samples):
  - cloud-integration-flow               (community flows)
  - btp-integration-suite-*              (BTP integration content)
  - s4hana-*                             (S/4HANA specific)
  - abap-*                               (ABAP/RFC related)

Output: list[SAPSamplePackage] — each with metadata + downloadable content
"""
from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GITHUB_API      = "https://api.github.com"
GITHUB_RAW      = "https://raw.githubusercontent.com"
DEFAULT_ORG     = "SAP-samples"   # fallback when a repo entry has no `org`
SAP_ORG         = DEFAULT_ORG     # back-compat alias for any external import
DEFAULT_CACHE   = Path.home() / ".cpi_migrator" / "sap_samples"

# Process memo for the parsed package index, keyed by (path, mtime). Avoids
# re-reading/re-parsing the cache file on every matcher call within a rerun.
_INDEX_MEMO: dict = {}

# Repos known to contain integration content — ordered by relevance
INTEGRATION_REPOS = [
    {
        "org":         "SAP",
        "repo":        "apibusinesshub-integration-recipes",
        "description": "Official SAP integration recipes — adapter examples, patterns, utilities",
        "tags":        ["recipes", "all-adapters", "official", "cpi"],
        "priority":    1,
    },
    {
        "org":         "SAP",
        "repo":        "apibusinesshub-api-recipes",
        "description": "Official SAP API Management recipes — proxy templates, policies, security samples",
        "tags":        ["recipes", "api-management", "official", "policies"],
        "priority":    1,
    },
    {
        "repo":        "cloud-integration-flow",
        "description": "Community integration flows contributed to SAP Hub",
        "tags":        ["community", "iflow", "samples"],
        "priority":    2,
    },
    {
        "repo":        "btp-integration-suite-advanced-event-mesh",
        "description": "Advanced Event Mesh integration patterns",
        "tags":        ["aem", "event-mesh", "async"],
        "priority":    2,
    },
    {
        "repo":        "btp-integration-suite-migration-tool",
        "description": "SAP's official migration tooling and samples",
        "tags":        ["migration", "pi-po", "official"],
        "priority":    1,
    },
    {
        "repo":        "s4hana-btp-extension-series",
        "description": "S/4HANA BTP extension patterns with integration",
        "tags":        ["s4hana", "btp", "extension"],
        "priority":    2,
    },
    {
        "repo":        "cloud-integration-connectivity",
        "description": "Connectivity patterns — SCC, RFC, JDBC samples",
        "tags":        ["connectivity", "scc", "rfc", "jdbc"],
        "priority":    2,
    },
    {
        "repo":        "successfactors-integration-samples",
        "description": "SuccessFactors integration patterns",
        "tags":        ["successfactors", "hcm", "sf"],
        "priority":    2,
    },
    {
        "repo":        "ariba-extensibility",
        "description": "SAP Ariba extensibility and integration samples",
        "tags":        ["ariba", "procurement", "b2b"],
        "priority":    2,
    },
]

# Adapter keywords for auto-classification
ADAPTER_KEYWORDS = {
    "IDoc":    ["idoc", "edidc", "orders05", "desadv", "invoic", "matmas", "debmas"],
    "RFC":     ["rfc", "bapi", "function_module", "remote_function"],
    "SOAP":    ["soap", "wsdl", "webservice", "ws-"],
    "OData":   ["odata", "v2", "v4", "$metadata", "entityset"],
    "HTTPS":   ["https", "rest", "http", "webhook", "api"],
    "File":    ["file", "sftp", "ftp", "directory", "polling"],
    "JDBC":    ["jdbc", "database", "sql", "query", "stored_proc"],
    "AS2":     ["as2", "ediint", "b2b", "edi"],
    "JMS":     ["jms", "queue", "amqp", "messaging"],
    "SuccessFactors": ["successfactors", "sfsf", "employee-central", "ec-"],
}

# Target system keywords
TARGET_KEYWORDS = {
    "s4hana_cloud":   ["s4hana", "s/4hana", "s4", "s4cloud", "public-cloud"],
    "s4hana_op":      ["s4op", "on-premise", "onpremise", "ecc", "erp"],
    "ariba":          ["ariba", "ariba-network", "procurement"],
    "successfactors": ["successfactors", "sfsf", "hcm", "employee"],
    "btp":            ["btp", "business-technology", "workflow", "hana-cloud"],
    "aws_s3":         ["aws", "s3", "amazon"],
    "azure_servicebus": ["azure", "servicebus", "microsoft"],
    "gcp_pubsub":     ["gcp", "google-cloud", "pubsub"],
}


@dataclass
class SAPSamplePackage:
    """One discovered integration package from SAP-samples."""
    id: str
    name: str
    repo: str
    path: str                            # path within repo
    description: str = ""
    tags: list[str] = field(default_factory=list)
    detected_adapters: list[str] = field(default_factory=list)
    detected_targets: list[str] = field(default_factory=list)
    download_url: str = ""               # zip download URL
    readme_url: str = ""
    local_path: Optional[Path] = None
    file_count: int = 0
    has_iflow: bool = False
    has_mapping: bool = False
    has_wsdl: bool = False
    has_zip: bool = False                # downloadable .zip artifact present
    complexity_hint: str = "MEDIUM"      # LOW / MEDIUM / HIGH
    # Populated after extract_artifacts() runs against this package
    extracted_mappings: list[str] = field(default_factory=list)   # .mmap paths
    extracted_xslt:     list[str] = field(default_factory=list)   # .xsl/.xslt paths
    extracted_iflows:   list[str] = field(default_factory=list)   # .iflw paths
    extracted_groovy:   list[str] = field(default_factory=list)   # .groovy/.gsh paths


class SAPSamplesBrowser:
    """
    Browses SAP-samples GitHub repos for integration content.
    Uses raw GitHub API for directory listings and raw content for files.
    Caches results locally to avoid repeated API calls.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        github_token: str = "",
        cache_ttl_hours: int = 24,
        recipes_topic_limit: int = 200,
    ):
        self.cache_dir   = Path(cache_dir) if cache_dir else DEFAULT_CACHE
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl   = cache_ttl_hours * 3600
        self.recipes_topic_limit = recipes_topic_limit
        self.session     = requests.Session()
        self.session.headers.update({
            "Accept":     "application/vnd.github.v3+json",
            "User-Agent": "CPI-Migration-Scaffolder/1.0",
        })
        if github_token:
            self.session.headers["Authorization"] = f"Bearer {github_token}"

    # ── Index ─────────────────────────────────────────────────────────

    def get_package_index(self, force_refresh: bool = False) -> list[SAPSamplePackage]:
        """
        Returns the full index of available SAP sample packages.
        Uses cache if fresh, fetches from GitHub otherwise.
        """
        index_path = self.cache_dir / "package_index.json"

        if not force_refresh and index_path.exists():
            age = time.time() - index_path.stat().st_mtime
            if age < self.cache_ttl:
                # Process memo keyed by (path, mtime): the matcher calls this
                # once per interface, so without a memo the 163-package JSON was
                # re-read and re-parsed ~6× on every Streamlit rerun. Reuse the
                # parsed list until the cache file actually changes.
                mkey = (str(index_path), int(index_path.stat().st_mtime))
                memo = _INDEX_MEMO.get(mkey)
                if memo is None:
                    memo = json.loads(index_path.read_text("utf-8"))
                    _INDEX_MEMO.clear()          # only keep the current file
                    _INDEX_MEMO[mkey] = memo
                    logger.info("Loaded %d packages from cache", len(memo))
                return [self._dict_to_package(p) for p in memo]

        logger.info("Scanning SAP-samples repos for integration content…")
        packages = []

        for repo_info in INTEGRATION_REPOS:
            repo = repo_info["repo"]
            try:
                repo_packages = self._scan_repo(repo, repo_info)
                packages.extend(repo_packages)
                logger.info("Found %d packages in %s", len(repo_packages), repo)
            except Exception as exc:
                logger.warning("Could not scan %s: %s", repo, exc)

        # Cache index
        index_path.write_text(
            json.dumps([self._package_to_dict(p) for p in packages], indent=2),
            "utf-8",
        )
        logger.info("Indexed %d total SAP sample packages", len(packages))
        return packages

    def _scan_repo(self, repo: str, repo_info: dict) -> list[SAPSamplePackage]:
        """Scan one repo for integration packages."""
        packages = []
        org = repo_info.get("org", DEFAULT_ORG)

        # Try to get top-level directory listing
        url  = f"{GITHUB_API}/repos/{org}/{repo}/contents"
        resp = self.session.get(url, timeout=15)

        if resp.status_code == 404:
            logger.debug("Repo %s not found", repo)
            return []
        if resp.status_code == 403:
            logger.warning("GitHub rate limit — using cached data for %s", repo)
            return self._load_cached_repo(repo)
        resp.raise_for_status()

        contents = resp.json()
        if not isinstance(contents, list):
            return []

        # Check for Recipes/ subdirectory (apibusinesshub pattern)
        recipes_dir = next(
            (item for item in contents
             if item["type"] == "dir" and item["name"] in ("Recipes", "recipes", "Flows", "flows")),
            None,
        )

        if recipes_dir:
            packages = self._scan_recipes_dir(repo, recipes_dir, repo_info)
        else:
            # Scan top level for .zip or iflow dirs
            pkg = self._build_package_from_contents(
                repo=repo,
                path="",
                contents=contents,
                repo_info=repo_info,
            )
            if pkg:
                packages.append(pkg)

            # Scan subdirectories up to 1 level
            for item in contents[:20]:
                if item["type"] == "dir" and not item["name"].startswith("."):
                    try:
                        sub_resp = self.session.get(item["url"], timeout=10)
                        if sub_resp.status_code == 200:
                            sub_contents = sub_resp.json()
                            sub_pkg = self._build_package_from_contents(
                                repo=repo,
                                path=item["name"],
                                contents=sub_contents,
                                repo_info=repo_info,
                                name=item["name"],
                            )
                            if sub_pkg:
                                packages.append(sub_pkg)
                    except Exception:
                        continue

        return packages

    def _scan_recipes_dir(
        self, repo: str, recipes_dir: dict, repo_info: dict
    ) -> list[SAPSamplePackage]:
        """Scan a Recipes/ directory structure.

        For each leaf folder, peek at the file listing to classify whether it
        actually contains a downloadable artifact (.zip / .iflw / .mmap / xslt
        / groovy). README-only folders are skipped — they show up in the raw
        GitHub tree but have no usable content for migration testing.
        """
        packages = []
        # The cap exists to avoid blowing through the unauthenticated rate
        # limit on cold scans. With a token (5000 req/hr) it can be very large.
        # `recipes_topic_limit` is overridable; the default 200 effectively
        # disables the cap for any real recipes repo today (~170 topics).
        topic_limit = self.recipes_topic_limit
        try:
            resp = self.session.get(recipes_dir["url"], timeout=15)
            if resp.status_code != 200:
                return []
            topics = [i for i in resp.json() if i["type"] == "dir"]
        except Exception:
            return []

        artifact_exts = (".zip", ".iflw", ".mmap", ".xsl", ".xslt", ".groovy", ".gsh")

        for topic in topics[:topic_limit]:
            try:
                t_resp = self.session.get(topic["url"], timeout=10)
                if t_resp.status_code == 403:
                    logger.warning("GitHub rate limit hit at %s — partial index returned. "
                                   "Configure a GitHub token to raise limit from 60 to 5000 req/hr.",
                                   topic["name"])
                    break
                if t_resp.status_code != 200:
                    continue

                for item in t_resp.json():
                    if item["type"] != "dir":
                        continue
                    # Peek into the leaf folder to see what files it holds
                    has_zip = has_iflow = has_mapping = has_wsdl = False
                    try:
                        leaf_resp = self.session.get(item["url"], timeout=10)
                        if leaf_resp.status_code == 403:
                            logger.warning("Rate limit at leaf %s — token recommended", item["name"])
                            break
                        if leaf_resp.status_code == 200:
                            leaf_files = [c["name"].lower() for c in leaf_resp.json()
                                          if c["type"] == "file"]
                            has_zip     = any(n.endswith(".zip") for n in leaf_files)
                            has_iflow   = any(n.endswith(".iflw") for n in leaf_files)
                            has_mapping = any(n.endswith((".mmap", ".xslt", ".xsl"))
                                              for n in leaf_files)
                            has_wsdl    = any(n.endswith((".wsdl", ".xsd")) for n in leaf_files)
                    except Exception:
                        # If we can't peek, assume nothing — folder will be filtered out
                        pass

                    # Skip README-only folders. A folder is "useful" if it has
                    # any downloadable migration artifact. Folders that only
                    # carry markdown + images are documentation; useful for
                    # browsing but not for shadow-testing, so they don't
                    # belong in the main package index.
                    if not (has_zip or has_iflow or has_mapping):
                        continue

                    pkg = SAPSamplePackage(
                        id=f"{repo}/{topic['name']}/{item['name']}",
                        name=item["name"].replace("-", " ").replace("_", " "),
                        repo=repo,
                        path=f"{recipes_dir['name']}/{topic['name']}/{item['name']}",
                        description=f"{topic['name']} — {item['name']}",
                        tags=repo_info.get("tags", []) + [topic["name"].lower()],
                        download_url=f"https://github.com/{repo_info.get('org', DEFAULT_ORG)}/{repo}/archive/refs/heads/master.zip",
                        has_zip=has_zip,
                        has_iflow=has_iflow,
                        has_mapping=has_mapping,
                        has_wsdl=has_wsdl,
                    )
                    self._classify_package(pkg)
                    packages.append(pkg)

            except Exception:
                continue

        return packages

    def _build_package_from_contents(
        self,
        repo: str,
        path: str,
        contents: list,
        repo_info: dict,
        name: str = "",
    ) -> Optional[SAPSamplePackage]:
        """Build a SAPSamplePackage from a directory contents listing."""
        file_names = [i["name"].lower() for i in contents if i["type"] == "file"]
        dir_names  = [i["name"].lower() for i in contents if i["type"] == "dir"]

        has_iflow   = any(f.endswith(".iflw") for f in file_names)
        has_mapping = any(f.endswith((".mmap", ".xslt", ".xsl")) for f in file_names)
        has_wsdl    = any(f.endswith((".wsdl", ".xsd")) for f in file_names)
        has_zip     = any(f.endswith(".zip") for f in file_names)
        has_params  = "parameters.prop" in file_names

        # Only include if it looks like integration content
        if not (has_iflow or has_zip or has_params or
                "src" in dir_names or "integrationflow" in str(dir_names)):
            return None

        pkg_name = name or repo
        # Find readme for description
        readme_url = ""
        for item in contents:
            if item["name"].lower() == "readme.md":
                readme_url = item.get("download_url", "")
                break

        # Find zip download
        zip_url = ""
        for item in contents:
            if item["name"].lower().endswith(".zip"):
                zip_url = item.get("download_url", "")
                break
        if not zip_url:
            zip_url = f"https://github.com/{repo_info.get('org', DEFAULT_ORG)}/{repo}/archive/refs/heads/master.zip"

        pkg = SAPSamplePackage(
            id=f"{repo}/{path or 'root'}",
            name=pkg_name.replace("-", " ").replace("_", " "),
            repo=repo,
            path=path,
            description=repo_info.get("description", ""),
            tags=repo_info.get("tags", []),
            download_url=zip_url,
            readme_url=readme_url,
            file_count=len(contents),
            has_iflow=has_iflow,
            has_mapping=has_mapping,
            has_wsdl=has_wsdl,
            has_zip=has_zip,
        )
        self._classify_package(pkg)
        return pkg

    # ── Classification ────────────────────────────────────────────────

    def _classify_package(self, pkg: SAPSamplePackage):
        """Auto-detect adapters and target systems from name/description/tags."""
        text = (pkg.name + " " + pkg.description + " " + " ".join(pkg.tags)).lower()

        for adapter, keywords in ADAPTER_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                if adapter not in pkg.detected_adapters:
                    pkg.detected_adapters.append(adapter)

        for target, keywords in TARGET_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                if target not in pkg.detected_targets:
                    pkg.detected_targets.append(target)

        # Complexity hint
        complex_signals = sum([
            "bpm" in text,
            "multi" in text,
            "orchestrat" in text,
            "rfc" in text or "jdbc" in text,
            "as2" in text or "as4" in text,
        ])
        if complex_signals >= 2:
            pkg.complexity_hint = "HIGH"
        elif complex_signals == 1:
            pkg.complexity_hint = "MEDIUM"
        else:
            pkg.complexity_hint = "LOW"

    # ── Download ─────────────────────────────────────────────────────

    def download_package(
        self,
        pkg: SAPSamplePackage,
        extract_to: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        Download and unpack a SAP sample package.
        Returns local path where content was extracted.
        """
        dest = extract_to or (self.cache_dir / "downloads" / pkg.repo /
                              pkg.path.replace("/", "_"))

        if dest.exists() and any(dest.rglob("*.iflw")):
            logger.debug("Already downloaded: %s", dest)
            pkg.local_path = dest
            return dest

        dest.mkdir(parents=True, exist_ok=True)

        try:
            logger.info("Downloading %s from %s…", pkg.name, pkg.download_url)
            resp = requests.get(pkg.download_url, timeout=60, stream=True)
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                # If it's a full repo zip, extract only the relevant path
                if pkg.path:
                    prefix = f"{pkg.repo}-master/{pkg.path}/"
                    members = [m for m in zf.namelist() if m.startswith(prefix)]
                else:
                    members = zf.namelist()

                if members:
                    for member in members:
                        zf.extract(member, dest)
                else:
                    zf.extractall(dest)

            pkg.local_path = dest
            logger.info("Extracted to %s", dest)
            return dest

        except Exception as exc:
            logger.warning("Download failed for %s: %s", pkg.name, exc)
            return None

    def extract_artifacts(
        self,
        pkg: SAPSamplePackage,
        dest: Optional[Path] = None,
    ) -> dict[str, list[Path]]:
        """Download the package zip and surface paths to migration artifacts.

        Walks the extracted tree and populates pkg.extracted_mappings /
        extracted_xslt / extracted_iflows / extracted_groovy with the absolute
        paths of files found, plus returns the same data as a dict.

        Used by the shadow-testing pipeline: the .mmap / .xsl / .iflw / .groovy
        files surfaced here are the inputs to the mapping equivalence engine.

        Returns an empty dict if the download failed.
        """
        local = self.download_package(pkg, extract_to=dest)
        if local is None:
            return {}

        out: dict[str, list[Path]] = {
            "mappings": [],
            "xslt":     [],
            "iflows":   [],
            "groovy":   [],
            "wsdl":     [],
            "xsd":      [],
        }
        for p in local.rglob("*"):
            if not p.is_file():
                continue
            suffix = p.suffix.lower()
            if suffix == ".mmap":
                out["mappings"].append(p)
            elif suffix in (".xsl", ".xslt"):
                out["xslt"].append(p)
            elif suffix == ".iflw":
                out["iflows"].append(p)
            elif suffix in (".groovy", ".gsh"):
                out["groovy"].append(p)
            elif suffix == ".wsdl":
                out["wsdl"].append(p)
            elif suffix == ".xsd":
                out["xsd"].append(p)

        pkg.extracted_mappings = [str(p) for p in out["mappings"]]
        pkg.extracted_xslt     = [str(p) for p in out["xslt"]]
        pkg.extracted_iflows   = [str(p) for p in out["iflows"]]
        pkg.extracted_groovy   = [str(p) for p in out["groovy"]]

        logger.info("Extracted from %s: %d mappings, %d xslt, %d iflows, %d groovy",
                    pkg.name, len(out["mappings"]), len(out["xslt"]),
                    len(out["iflows"]), len(out["groovy"]))
        return out

    # ── Search ────────────────────────────────────────────────────────

    def search(
        self,
        query: str = "",
        adapter: str = "",
        target_id: str = "",
        complexity: str = "",
        top_n: int = 20,
    ) -> list[SAPSamplePackage]:
        """Search the package index by query, adapter, target, or complexity."""
        packages = self.get_package_index()
        results  = []

        query_lower = query.lower()

        for pkg in packages:
            score = 0
            text  = (pkg.name + " " + pkg.description + " " +
                     " ".join(pkg.tags)).lower()

            if query_lower and query_lower in text:
                score += 3
            if adapter and adapter in pkg.detected_adapters:
                score += 5
            if target_id and target_id in pkg.detected_targets:
                score += 4
            if complexity and pkg.complexity_hint == complexity:
                score += 2
            if not query_lower and not adapter and not target_id:
                score = 1  # return everything if no filter

            if score > 0:
                results.append((score, pkg))

        results.sort(key=lambda x: x[0], reverse=True)
        return [pkg for _, pkg in results[:top_n]]

    # ── Cache helpers ─────────────────────────────────────────────────

    def _load_cached_repo(self, repo: str) -> list[SAPSamplePackage]:
        cache = self.cache_dir / "package_index.json"
        if cache.exists():
            all_packages = json.loads(cache.read_text())
            return [
                self._dict_to_package(p)
                for p in all_packages
                if p.get("repo") == repo
            ]
        return []

    @staticmethod
    def _package_to_dict(pkg: SAPSamplePackage) -> dict:
        return {
            "id":               pkg.id,
            "name":             pkg.name,
            "repo":             pkg.repo,
            "path":             pkg.path,
            "description":      pkg.description,
            "tags":             pkg.tags,
            "detected_adapters": pkg.detected_adapters,
            "detected_targets": pkg.detected_targets,
            "download_url":     pkg.download_url,
            "readme_url":       pkg.readme_url,
            "has_iflow":        pkg.has_iflow,
            "has_mapping":      pkg.has_mapping,
            "has_wsdl":         pkg.has_wsdl,
            "has_zip":          pkg.has_zip,
            "file_count":       pkg.file_count,
            "complexity_hint":  pkg.complexity_hint,
        }

    @staticmethod
    def _dict_to_package(d: dict) -> SAPSamplePackage:
        return SAPSamplePackage(
            id=d.get("id", ""),
            name=d.get("name", ""),
            repo=d.get("repo", ""),
            path=d.get("path", ""),
            description=d.get("description", ""),
            tags=d.get("tags", []),
            detected_adapters=d.get("detected_adapters", []),
            detected_targets=d.get("detected_targets", []),
            download_url=d.get("download_url", ""),
            readme_url=d.get("readme_url", ""),
            has_iflow=d.get("has_iflow", False),
            has_mapping=d.get("has_mapping", False),
            has_wsdl=d.get("has_wsdl", False),
            has_zip=d.get("has_zip", False),
            file_count=d.get("file_count", 0),
            complexity_hint=d.get("complexity_hint", "MEDIUM"),
        )

    def cache_status(self) -> dict:
        index = self.cache_dir / "package_index.json"
        downloads = list((self.cache_dir / "downloads").rglob("*.iflw")) \
            if (self.cache_dir / "downloads").exists() else []
        return {
            "index_exists":    index.exists(),
            "index_age_hours": round((time.time() - index.stat().st_mtime) / 3600, 1)
                               if index.exists() else None,
            "cached_packages": len(json.loads(index.read_text())) if index.exists() else 0,
            "downloaded_iflows": len(downloads),
            "cache_dir":       str(self.cache_dir),
        }

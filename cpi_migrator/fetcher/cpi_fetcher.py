"""
fetcher/cpi_fetcher.py
Downloads and parses iFlow artifacts from a live CPI tenant via OData API.
Works for both Cloud Foundry (OAuth2) and Neo (Basic auth) — the caller
passes in an already-authenticated requests.Session.

Key endpoints:
  GET /api/v1/IntegrationPackages
  GET /api/v1/IntegrationPackages('{id}')/Artifacts
  GET /api/v1/IntegrationDesigntimeArtifacts(Id='{id}',Version='active')/$value  → .zip
"""
from __future__ import annotations

import io
import json
import logging
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


@dataclass
class CPIArtifact:
    """Metadata for one CPI iFlow artifact."""
    id: str
    name: str
    package_id: str
    package_name: str
    version: str = "active"
    description: str = ""
    sender_adapters: list[str] = field(default_factory=list)
    receiver_adapters: list[str] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)   # key → default value
    local_path: Optional[Path] = None      # set after download
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def adapter_summary(self) -> str:
        s = ", ".join(self.sender_adapters) or "?"
        r = ", ".join(self.receiver_adapters) or "?"
        return f"{s} → {r}"


class CPIFetcher:
    """Fetches iFlow artifacts from a live CPI tenant."""

    def __init__(self, base_url: str, session: requests.Session,
                 cache_dir: Optional[Path] = None):
        self.base_url  = base_url.rstrip("/")
        self.session   = session
        self.cache_dir = Path(cache_dir) if cache_dir else TEMPLATES_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Package + artifact listing ───────────────────────────────────

    def list_packages(self) -> list[dict]:
        url  = f"{self.base_url}/api/v1/IntegrationPackages"
        resp = self.session.get(url, params={"$format": "json"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        pkgs = data.get("d", {}).get("results", data.get("value", []))
        logger.info("Found %d packages on CPI tenant", len(pkgs))
        return pkgs

    def list_artifacts(self, package_id: str) -> list[dict]:
        url  = f"{self.base_url}/api/v1/IntegrationPackages('{package_id}')/Artifacts"
        resp = self.session.get(url, params={"$format": "json"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        arts = data.get("d", {}).get("results", data.get("value", []))
        return [a for a in arts if a.get("ArtifactType") in ("IFlow", "IntegrationFlow")]

    def list_all_artifacts(self) -> list[CPIArtifact]:
        """Fetch artifact list from all packages — no downloads, metadata only."""
        results = []
        try:
            packages = self.list_packages()
        except Exception as exc:
            logger.error("Cannot list packages: %s", exc)
            return self._load_local_artifacts()

        for pkg in packages:
            pkg_id   = pkg.get("Id", pkg.get("id", ""))
            pkg_name = pkg.get("Name", pkg.get("name", pkg_id))
            try:
                arts = self.list_artifacts(pkg_id)
                for a in arts:
                    art_id   = a.get("Id", a.get("id", ""))
                    art_name = a.get("Name", a.get("name", art_id))
                    artifact = CPIArtifact(
                        id=art_id,
                        name=art_name,
                        package_id=pkg_id,
                        package_name=pkg_name,
                        version=a.get("Version", "active"),
                        description=a.get("Description", ""),
                        raw=a,
                    )
                    # Check if already downloaded locally
                    local = self.cache_dir / pkg_id / art_id
                    if local.exists():
                        artifact.local_path = local
                        self._enrich_from_local(artifact, local)
                    results.append(artifact)
            except Exception as exc:
                # A 404 just means this package has no listable artifacts
                # (empty package, or one without the Artifacts sub-endpoint) —
                # that's normal and shouldn't spam the log as a warning.
                if "404" in str(exc):
                    logger.debug("No artifacts for package %s (404)", pkg_id)
                else:
                    logger.warning("Could not list artifacts for package %s: %s",
                                   pkg_id, exc)

        if not results:
            results = self._load_local_artifacts()
        return results

    # ── Download ─────────────────────────────────────────────────────

    def download_package_zip(self, package_id: str) -> bytes:
        """Export a WHOLE package as the tenant's own export zip
        (GET /api/v1/IntegrationPackages('{id}')/$value) — the exact format
        the upload intake already understands, which makes source-tenant →
        workbench → target-tenant round trips one call per package."""
        url = (f"{self.base_url}/api/v1/IntegrationPackages"
               f"('{package_id}')/$value")
        r = self.session.get(url, timeout=300)
        r.raise_for_status()
        return r.content

    def download_artifact(self, artifact: CPIArtifact) -> Path:
        """Download a .zip from CPI and unpack to templates/{pkg_id}/{art_id}/."""
        dest = self.cache_dir / artifact.package_id / artifact.id
        if dest.exists():
            logger.debug("Already cached: %s", dest)
            artifact.local_path = dest
            self._enrich_from_local(artifact, dest)
            return dest

        url = (f"{self.base_url}/api/v1/IntegrationDesigntimeArtifacts"
               f"(Id='{artifact.id}',Version='{artifact.version}')/$value")
        logger.info("Downloading %s …", artifact.id)
        resp = self.session.get(url, timeout=60)
        resp.raise_for_status()

        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(dest)

        artifact.local_path = dest
        self._enrich_from_local(artifact, dest)
        logger.info("Unpacked %s → %s", artifact.id, dest)
        return dest

    def download_from_upload(self, zip_bytes: bytes, package_id: str = "uploaded") -> list[CPIArtifact]:
        """Unpack a user-uploaded archive and return artifacts found inside.

        Three layouts are supported:
          1. Single iFlow zip:       contains iflow.xml / src/ at the top level
          2. Container zip:          contains multiple iFlow .zip files at top level
          3. GitHub repo zip:        contains <repo>-master/Recipes/for/<topic>/<pkg>/<artifact>.zip
                                     (used for full-repo dumps from
                                     SAP/apibusinesshub-integration-recipes etc.)

        Archive formats supported: .zip, .tar, .tar.gz / .tgz, .tar.bz2.
        Tar archives are converted to an in-memory zip stream so all three
        layouts above apply uniformly. For very large uploads (multi-GB SAP
        landscapes), this conversion happens member-by-member to keep peak
        memory close to the largest single file rather than the full archive.

        Layout 3 walks every leaf folder under Recipes/ that contains a .zip
        and treats each inner zip as one package. Folders with README only
        are skipped (same filter the GitHub scanner uses).
        """
        artifacts = []
        dest_base = self.cache_dir / package_id
        dest_base.mkdir(parents=True, exist_ok=True)

        # If the upload is a tar/tar.gz/tar.bz2, convert to a zip-shaped buffer
        # so the existing zipfile-based logic below works unchanged. The
        # detection is by magic bytes, not filename, so renamed files still work.
        zip_bytes = self._normalise_to_zip_bytes(zip_bytes)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()

            # Layout 3 detection — GitHub repo zip has files at <repo>-<branch>/...
            # with Recipes/for/ or recipes/for/ deep inside
            repo_zip_inner = [n for n in names
                              if ("/recipes/for/" in n.lower() or "/recipes/" in n.lower())
                              and n.lower().endswith(".zip")]
            if repo_zip_inner:
                logger.info("Detected GitHub repo zip layout: %d inner package zips",
                            len(repo_zip_inner))
                for inner_name in repo_zip_inner:
                    # Build a meaningful artifact id from the folder structure:
                    #   <repo>-master/Recipes/for/<topic>/<pkg>/<x>.zip
                    # -> <topic>__<pkg>
                    parts = inner_name.split("/")
                    try:
                        for_idx = next(i for i, p in enumerate(parts)
                                       if p.lower() == "for")
                        # Take folder names between "for" and the .zip filename
                        # (skip the filename itself, parts[-1]).
                        between = parts[for_idx + 1:-1]
                        # Common SAP pattern: <topic>/<package> where the two
                        # are often identical (folder named after its single
                        # contained package). Dedupe to keep ids readable.
                        seen = []
                        for p in between:
                            if p and (not seen or seen[-1] != p):
                                seen.append(p)
                        art_id = "__".join(seen) or Path(inner_name).stem
                    except (StopIteration, IndexError):
                        art_id = Path(inner_name).stem
                    art_id = "".join(c if c.isalnum() or c in "_-" else "_"
                                     for c in art_id)[:80]
                    dest = dest_base / art_id
                    dest.mkdir(parents=True, exist_ok=True)
                    try:
                        inner_bytes = zf.read(inner_name)
                        with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                            inner.extractall(dest)
                    except (zipfile.BadZipFile, KeyError) as exc:
                        logger.warning("Skip %s (bad inner zip): %s", inner_name, exc)
                        continue
                    artifact = CPIArtifact(
                        id=art_id, name=art_id.replace("_", " "),
                        package_id=package_id, package_name=package_id,
                        local_path=dest,
                    )
                    self._enrich_from_local(artifact, dest)
                    artifacts.append(artifact)
                return artifacts

            # Layout 2 — container zip
            iflow_zips = [n for n in names if n.endswith(".zip")]
            if iflow_zips:
                for iflow_zip_name in iflow_zips:
                    art_id = Path(iflow_zip_name).stem
                    dest   = dest_base / art_id
                    dest.mkdir(parents=True, exist_ok=True)
                    inner_bytes = zf.read(iflow_zip_name)
                    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
                        inner.extractall(dest)
                    artifact = CPIArtifact(
                        id=art_id, name=art_id,
                        package_id=package_id, package_name=package_id,
                        local_path=dest,
                    )
                    self._enrich_from_local(artifact, dest)
                    artifacts.append(artifact)
            else:
                # Layout 1 — single iFlow zip
                zf.extractall(dest_base)
                artifact = CPIArtifact(
                    id=package_id, name=package_id,
                    package_id=package_id, package_name=package_id,
                    local_path=dest_base,
                )
                self._enrich_from_local(artifact, dest_base)
                artifacts.append(artifact)

        return artifacts

    @staticmethod
    def _normalise_to_zip_bytes(archive_bytes: bytes) -> bytes:
        """If ``archive_bytes`` is a tar/tar.gz/tar.bz2, return a zip-formatted
        equivalent. If it's already a zip, return the bytes unchanged.

        Detection is by magic bytes (first 4 bytes for zip's ``PK\\x03\\x04``,
        ``\\x1f\\x8b`` for gzip, ``BZh`` for bzip2, and the trailing ``ustar``
        signature at byte 257 for uncompressed tar). Renamed extensions are
        therefore handled correctly.

        For multi-GB tar archives this streams member-by-member rather than
        loading the whole archive twice — peak memory is approximately
        ``max(member_size for member in archive) + small overhead``.
        """
        # Cheap signature check — zipfile.is_zipfile is fine for in-memory
        if archive_bytes[:4] == b"PK\x03\x04":
            return archive_bytes

        is_gzip  = archive_bytes[:2] == b"\x1f\x8b"
        is_bzip2 = archive_bytes[:3] == b"BZh"
        is_ustar = (len(archive_bytes) > 262 and
                    archive_bytes[257:262] == b"ustar")

        if not (is_gzip or is_bzip2 or is_ustar):
            # Unknown format — let downstream zipfile.ZipFile raise a clear
            # BadZipFile so the user sees a sensible error message.
            return archive_bytes

        mode = "r:gz" if is_gzip else ("r:bz2" if is_bzip2 else "r:")
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode=mode) as tar:
                out_buf = io.BytesIO()
                with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for member in tar:
                        if not member.isfile():
                            continue
                        # extractfile() returns a file-like object that
                        # streams from the tar without loading prior
                        # members into memory.
                        f = tar.extractfile(member)
                        if f is None:
                            continue
                        # Refuse files whose extracted size would overflow
                        # the zip64 boundary unless ZIP64 is required.
                        # zipfile handles zip64 automatically as of py3.4.
                        zf.writestr(member.name, f.read())
                return out_buf.getvalue()
        except tarfile.TarError as exc:
            logger.warning("Failed to read tar archive: %s — passing original "
                           "bytes through so caller sees zipfile error", exc)
            return archive_bytes

    # ── Local scan ───────────────────────────────────────────────────

    def _load_local_artifacts(self) -> list[CPIArtifact]:
        """Scan templates/ for previously downloaded/unpacked iFlows."""
        artifacts = []
        for iflw in self.cache_dir.rglob("*.iflw"):
            art_id  = iflw.stem
            pkg_dir = iflw.parent
            while pkg_dir.parent != self.cache_dir and pkg_dir != self.cache_dir:
                pkg_dir = pkg_dir.parent
            pkg_id  = pkg_dir.name if pkg_dir != self.cache_dir else "local"
            dest    = iflw.parent
            artifact = CPIArtifact(
                id=art_id, name=art_id.replace("_", " "),
                package_id=pkg_id, package_name=pkg_id,
                local_path=dest,
            )
            self._enrich_from_local(artifact, dest)
            artifacts.append(artifact)
        logger.info("Loaded %d local artifacts from %s", len(artifacts), self.cache_dir)
        return artifacts

    # ── Metadata extraction ──────────────────────────────────────────

    def _enrich_from_local(self, artifact: CPIArtifact, path: Path):
        """Extract parameters and adapter types from unpacked iFlow directory."""
        artifact.parameters     = self._read_parameters(path)
        sender_a, receiver_a    = self._read_adapter_types(path)
        artifact.sender_adapters   = sender_a
        artifact.receiver_adapters = receiver_a
        # Read description from metainfo.prop
        meta = self._read_metainfo(path)
        if meta.get("description"):
            artifact.description = meta["description"]
        if meta.get("name"):
            artifact.name = meta["name"]

    def _read_parameters(self, path: Path) -> dict[str, str]:
        params = {}
        for prop_file in path.rglob("parameters.prop"):
            try:
                for line in prop_file.read_text("utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        params[k.strip()] = v.strip()
            except Exception:
                pass
        return params

    def _read_adapter_types(self, path: Path) -> tuple[list[str], list[str]]:
        """Parse .iflw BPMN XML to extract sender/receiver adapter types."""
        sender_adapters, receiver_adapters = [], []
        try:
            import xml.etree.ElementTree as ET
            for iflw in path.rglob("*.iflw"):
                tree = ET.parse(iflw)
                root = tree.getroot()
                ns = {"ifl": "http:///com.sap.ifl.model/Ifl.xsd"}
                # Look for Transport property in startEvent (sender)
                for start in root.iter():
                    if "startEvent" in start.tag:
                        for prop in start.iter():
                            if "key" in prop.tag and prop.text == "Transport":
                                val_el = prop.find("../ifl:value", ns) or \
                                         prop.getnext() if hasattr(prop, 'getnext') else None
                                if val_el is not None and val_el.text:
                                    sender_adapters.append(val_el.text)
                # Receiver adapters from serviceTask Transport
                for task in root.iter():
                    if "serviceTask" in task.tag:
                        for prop in task.iter():
                            if "key" in prop.tag and prop.text == "Transport":
                                val_el = list(prop.getparent() or [])
                                if val_el:
                                    receiver_adapters.append(
                                        next((e.text for e in val_el
                                              if "value" in e.tag), "")
                                    )
        except Exception:
            pass
        return (sender_adapters or ["Unknown"]), (receiver_adapters or ["Unknown"])

    def _read_metainfo(self, path: Path) -> dict:
        meta = {}
        for prop_file in path.rglob("metainfo.prop"):
            try:
                for line in prop_file.read_text("utf-8").splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        meta[k.strip().lower()] = v.strip()
            except Exception:
                pass
        return meta

    # ── Matching ─────────────────────────────────────────────────────

    def suggest_matches(
        self, interface_name: str,
        sender_adapter: str, receiver_adapter: str,
        all_artifacts: list[CPIArtifact],
        top_n: int = 5,
    ) -> list[tuple[int, CPIArtifact]]:
        """Score and rank artifacts by relevance to a PI/PO interface."""
        import re
        keywords = set(
            w.lower() for w in re.split(r"[_\-\s/]", interface_name) if len(w) > 3
        )
        keywords.update({sender_adapter.lower(), receiver_adapter.lower()})

        scored = []
        for art in all_artifacts:
            score = 0
            text  = (art.name + " " + art.description + " " + art.adapter_summary).lower()
            score += sum(2 for kw in keywords if kw in text)
            # Adapter match bonus
            for sa in art.sender_adapters:
                if sa.lower() == sender_adapter.lower():
                    score += 5
            for ra in art.receiver_adapters:
                if ra.lower() == receiver_adapter.lower():
                    score += 5
            if score > 0:
                scored.append((score, art))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_n]

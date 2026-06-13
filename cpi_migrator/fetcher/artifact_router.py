"""
artifact_router.py — route package artifacts to their correct CPI endpoint.

Step 3 of the upload refactor. Builds on the proven, idempotent upload
primitive in CPIUploader (_post_artifact / upload_raw_bundle), which already
knows how to hit any of the 7 designtime endpoints given an artifact_type.

This module's job is the *routing decision*: given a package's artifacts
(each an artifact bundle zip), determine each one's type and dispatch it to
the matching endpoint via the proven primitive.

Key facts established by reverse-engineering the live tenant + $metadata:
  • There is NO package-import endpoint. Upload is always per-artifact.
  • Each designtime artifact type has its own entity set (endpoint). All are
    m:HasStream media entities using the same JSON+base64 upload form.
  • Scripts / mappings / wsdl / xsd that are *referenced inside* an iFlow ride
    INSIDE the iFlow bundle (src/main/resources/...). They are NOT uploaded
    separately. Only artifacts that are standalone package members get their
    own upload call.

Artifact type is detected from the bundle's internal structure:
  • a .iflw under scenarioflows  → IFlow
  • a .mmap (and no .iflw)        → MessageMapping
  • a value-mapping file          → ValueMapping
  • a script-collection layout    → ScriptCollection
  • .mt / message-type            → MessageType
  • .xsd-as-datatype              → DataType
  • .wsdl-as-interface            → ServiceInterface
When a resources.cnt resourceType is supplied by the caller, that wins over
structural detection (it's authoritative).
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Map a detected/declared resourceType to the CPIUploader artifact_type key.
# (They're the same strings, but this indirection documents the contract and
# lets us normalize odd casings/aliases from resources.cnt.)
RESOURCE_TYPE_ALIASES = {
    "iflow": "IFlow",
    "integrationflow": "IFlow",
    "messagemapping": "MessageMapping",
    "valuemapping": "ValueMapping",
    "scriptcollection": "ScriptCollection",
    "messagetype": "MessageType",
    "datatype": "DataType",
    "serviceinterface": "ServiceInterface",
}

# Types that are NOT designtime artifacts and must be skipped (package docs,
# links, the package wrapper itself).
NON_ARTIFACT_TYPES = {"file", "url", "contentpackage"}


def normalize_type(raw: str) -> Optional[str]:
    """Normalize a resourceType string to a CPIUploader artifact_type key.
    Returns None for non-artifact types (File/Url/ContentPackage)."""
    if not raw:
        return None
    key = raw.strip().lower().replace(" ", "").replace("_", "")
    if key in NON_ARTIFACT_TYPES:
        return None
    return RESOURCE_TYPE_ALIASES.get(key)


def detect_type_from_bundle(zip_bytes: bytes) -> Optional[str]:
    """Inspect an artifact bundle zip and infer its designtime type from the
    files it contains. Returns a CPIUploader artifact_type key, or None if it
    can't be determined (caller may then fall back to IFlow or skip)."""
    try:
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
    except Exception as exc:
        logger.warning("Cannot read bundle zip for type detection: %s", exc)
        return None

    lower = [n.lower() for n in names]

    def has(suffix_or_part: str) -> bool:
        return any(suffix_or_part in n for n in lower)

    # iFlow is the strongest signal — a .iflw under scenarioflows.
    if has(".iflw") or has("scenarioflows/"):
        return "IFlow"
    # Standalone message mapping bundle.
    if has(".mmap") or has("/mapping/") and has(".mmap"):
        return "MessageMapping"
    # Value mapping.
    if has("valuemapping") or has(".vmap"):
        return "ValueMapping"
    # Script collection (groovy/js scripts with a script-collection manifest).
    if has("/script/") and not has(".iflw"):
        return "ScriptCollection"
    # Message / data types & service interfaces (less common standalone).
    if has(".mt") or has("messagetype"):
        return "MessageType"
    if has(".xsd") and not has(".iflw"):
        return "DataType"
    if has(".wsdl") and not has(".iflw"):
        return "ServiceInterface"
    return None


def read_artifact_id_name(zip_bytes: bytes) -> tuple:
    """Read the real artifact Id and Name from a bundle's MANIFEST.MF.
    Returns (id, name). CPI uses Bundle-SymbolicName as the artifact Id and
    Bundle-Name as the display name. Falls back to ('', '') if unreadable."""
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
        mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except Exception:
        return "", ""
    # MANIFEST.MF can wrap long lines; join continuation lines (leading space).
    joined, cur = [], ""
    for raw in mf.splitlines():
        if raw.startswith(" "):
            cur += raw[1:]
        else:
            if cur:
                joined.append(cur)
            cur = raw
    if cur:
        joined.append(cur)
    sym = name = ""
    for line in joined:
        if line.startswith("Bundle-SymbolicName:"):
            sym = line.split(":", 1)[1].strip().split(";")[0].strip()
        elif line.startswith("Bundle-Name:"):
            name = line.split(":", 1)[1].strip()
    return sym, (name or sym)


def _iflw_text_from_bundle(zip_bytes: bytes) -> str:
    """Return the .iflw XML text from an iFlow inner-bundle zip (empty if none).
    Lets the upload path carry the real source structure to the regenerator."""
    try:
        zb = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception:
        return ""
    for n in zb.namelist():
        if n.endswith(".iflw"):
            try:
                return zb.read(n).decode("utf-8", "replace")
            except Exception:
                return ""
    return ""


def extract_iflows_recursive(archive_bytes: bytes, _depth: int = 0,
                             container_name: str = "") -> list:
    """Recursively find every iFlow inside an arbitrary archive.

    Handles three nesting levels uniformly so Tab 1's interface count reflects
    real iFlows (interfaces/ICOs), not package containers:

      1. A single artifact bundle  → 1 iFlow if it is one
      2. A package EXPORT wrapper  → each *_content bundle that is an iFlow
      3. A container zip of many package zips (e.g. a 55-package landscape
         dump) → recurse into each inner .zip

    Returns a list of {"id","name","zip_bytes","resource_type","package"} for
    iFlows ONLY (message mappings, value mappings, script collections etc.
    inside a package are intentionally NOT counted as interfaces).
    Deduplicated by id.

    `container_name` (the enclosing package zip's filename stem) is recorded
    as each flow's "package" — the SOURCE package identity. Downstream this
    drives (a) the targeted resource-corpus top-up (matching the package zip
    on disk by name), (b) resolver package scoping, and (c) tenant package
    naming, so generated packages mirror the originals instead of synthesized
    Sender/Receiver names — and flows can't land in the wrong package.

    Depth-guarded to avoid pathological recursion on malformed archives.
    """
    if _depth > 4:
        return []
    try:
        z = zipfile.ZipFile(io.BytesIO(archive_bytes))
        names = z.namelist()
    except Exception:
        return []

    found = []

    # Case 1 & 2: this archive is itself a bundle or a package wrapper.
    # extract_package_artifacts returns one entry for a single bundle, or one
    # per _content for a wrapper.
    descriptors = extract_package_artifacts(archive_bytes)
    for d in descriptors:
        zb = d.get("zip_bytes")
        if not zb:
            continue
        rtype = (d.get("resource_type") or "").lower()
        # Prefer resources.cnt resourceType when present; else structural detect.
        is_iflow = (
            "integrationflow" in rtype or "iflow" in rtype
            or detect_type_from_bundle(zb) == "IFlow"
        )
        if is_iflow:
            found.append({
                "id": d.get("id"), "name": d.get("name"),
                "zip_bytes": zb, "resource_type": d.get("resource_type", ""),
                "iflw_xml": _iflw_text_from_bundle(zb),
                "package": container_name,
            })

    # Case 3: container of inner .zip files (package dumps). Recurse into any
    # top-level .zip that we didn't already account for as a _content bundle.
    if not descriptors or all(not d.get("zip_bytes") for d in descriptors):
        inner_zips = [n for n in names if n.lower().endswith(".zip")]
        for n in inner_zips:
            try:
                inner_bytes = z.read(n)
            except Exception:
                continue
            found.extend(extract_iflows_recursive(
                inner_bytes, _depth + 1,
                container_name=n.rsplit("/", 1)[-1][:-4] or container_name))
    else:
        # Even when this level had bundles, there may also be nested package
        # zips alongside them — recurse into those too (mixed archives).
        inner_zips = [n for n in names
                      if n.lower().endswith(".zip") and not n.endswith("_content")]
        for n in inner_zips:
            try:
                inner_bytes = z.read(n)
            except Exception:
                continue
            found.extend(extract_iflows_recursive(
                inner_bytes, _depth + 1,
                container_name=n.rsplit("/", 1)[-1][:-4] or container_name))

    # Deduplicate by id (same iFlow can appear once); keep first occurrence.
    seen, deduped = set(), []
    for f in found:
        fid = f.get("id") or id(f)
        if fid in seen:
            continue
        seen.add(fid)
        deduped.append(f)
    return deduped


def count_iflows_recursive(archive_bytes: bytes) -> int:
    """Convenience: number of distinct iFlows (interfaces) in an archive."""
    return len(extract_iflows_recursive(archive_bytes))


def extract_package_artifacts(package_zip_bytes: bytes) -> list:
    """Given a CPI package EXPORT wrapper zip (containing resources.cnt +
    *_content artifact bundles) OR a single artifact bundle, return a list of
    artifact descriptors ready for ArtifactRouter.plan():
        [{"id","name","zip_bytes","resource_type"?,"source_hint"}]

    A package wrapper has no META-INF/MANIFEST.MF at its root but contains
    one or more '<hash>_content' entries (each itself a bundle zip). A single
    bundle HAS META-INF/MANIFEST.MF at root and is returned as one artifact.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(package_zip_bytes))
        names = z.namelist()
    except Exception as exc:
        logger.warning("Cannot read package zip: %s", exc)
        return []

    # Single bundle? (manifest at root)
    if "META-INF/MANIFEST.MF" in names:
        aid, aname = read_artifact_id_name(package_zip_bytes)
        return [{"id": aid or "artifact", "name": aname or aid or "artifact",
                 "zip_bytes": package_zip_bytes, "source_hint": "single bundle"}]

    # Package wrapper: parse resources.cnt for resourceType per _content (if
    # present), then return each _content as its own bundle.
    type_by_content = _parse_resources_cnt(z, names)

    artifacts = []
    for n in names:
        if not n.endswith("_content"):
            continue
        try:
            content_bytes = z.read(n)
        except Exception:
            continue
        # Confirm it's actually a bundle (has a manifest).
        try:
            inner = zipfile.ZipFile(io.BytesIO(content_bytes))
            if "META-INF/MANIFEST.MF" not in inner.namelist():
                continue
        except Exception:
            continue
        aid, aname = read_artifact_id_name(content_bytes)
        artifacts.append({
            "id": aid or n.replace("_content", ""),
            "name": aname or aid or n.replace("_content", ""),
            "zip_bytes": content_bytes,
            "resource_type": type_by_content.get(n, ""),
            "source_hint": n,
        })
    return artifacts


def _parse_resources_cnt(z, names) -> dict:
    """Best-effort: map each '<hash>_content' filename to its resourceType by
    reading resources.cnt. Returns {} if not parseable (router will then
    detect type structurally). The mapping is by the resourceId/hash that
    appears in both resources.cnt and the _content filename."""
    cnt_name = next((n for n in names if n.endswith("resources.cnt")), None)
    if not cnt_name:
        return {}
    try:
        import base64 as _b64
        raw = z.read(cnt_name)
        try:
            txt = _b64.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            txt = raw.decode("utf-8", "replace")
    except Exception:
        return {}
    import re
    # resources.cnt lists entries with an id/hash and a resourceType. We map
    # the hash → type, then match to '<hash>_content' filenames.
    out = {}
    # Find id + resourceType pairs near each other.
    for m in re.finditer(r'"id"\s*:\s*"([0-9a-f]{16,})".*?"resourceType"\s*:\s*"([^"]+)"',
                         txt, re.DOTALL):
        h, rtype = m.group(1), m.group(2)
        for n in names:
            if n.endswith("_content") and h in n:
                out[n] = rtype
    return out


@dataclass
class RoutedArtifact:
    """One artifact to upload, with its resolved routing decision."""
    artifact_id:   str
    artifact_name: str
    zip_bytes:     bytes
    artifact_type: str                 # resolved CPIUploader key, e.g. "IFlow"
    endpoint:      str                 # resolved entity-set name
    source_hint:   str = ""            # filename/dir it came from (for logs)


@dataclass
class RoutePlan:
    """The full upload plan for a package — what will be created/updated where.
    This is what a dry-run preview renders before anything is sent."""
    package_id:    str
    package_name:  str
    artifacts:     list = field(default_factory=list)   # list[RoutedArtifact]
    skipped:       list = field(default_factory=list)    # list[(name, reason)]

    def summary(self) -> str:
        by_type: dict[str, int] = {}
        for a in self.artifacts:
            by_type[a.artifact_type] = by_type.get(a.artifact_type, 0) + 1
        parts = ", ".join(f"{n}× {t}" for t, n in sorted(by_type.items()))
        s = f"Package '{self.package_id}': {len(self.artifacts)} artifact(s)"
        if parts:
            s += f" ({parts})"
        if self.skipped:
            s += f"; {len(self.skipped)} skipped"
        return s


class ArtifactRouter:
    """Decides where each artifact goes and dispatches uploads via CPIUploader.

    Usage:
        router = ArtifactRouter(uploader)
        plan   = router.plan(package_id, package_name, artifacts)
        # (show plan to user for confirmation — dry run)
        results = router.execute(plan, overwrite=True)
    """

    def __init__(self, uploader):
        self.uploader = uploader

    # ── Planning (dry-run friendly: no network writes) ────────────────────
    def plan(self, package_id: str, package_name: str,
             artifacts: list) -> RoutePlan:
        """Build a RoutePlan from a list of artifact descriptors.

        Each artifact descriptor is a dict:
            {"id", "name", "zip_bytes", optional "resource_type"}
        resource_type (from resources.cnt) is authoritative when present;
        otherwise the type is detected from the bundle's structure.
        """
        plan = RoutePlan(package_id=package_id, package_name=package_name)
        for art in artifacts:
            name = art.get("name") or art.get("id") or "artifact"
            aid  = art.get("id") or name
            zb   = art.get("zip_bytes")
            declared = normalize_type(art.get("resource_type", "")) \
                if art.get("resource_type") else None

            if art.get("resource_type") and declared is None:
                # Explicitly a non-artifact (File/Url/ContentPackage) — skip.
                plan.skipped.append((name, f"non-artifact type "
                                            f"'{art.get('resource_type')}'"))
                continue

            atype = declared or (detect_type_from_bundle(zb) if zb else None)
            if not atype:
                plan.skipped.append((name, "could not determine artifact type"))
                continue

            endpoint = self.uploader.endpoint_for(atype)
            plan.artifacts.append(RoutedArtifact(
                artifact_id=aid, artifact_name=name, zip_bytes=zb,
                artifact_type=atype, endpoint=endpoint,
                source_hint=art.get("source_hint", "")))
        return plan

    # ── Execution (actually uploads) ──────────────────────────────────────
    def execute(self, plan: RoutePlan, overwrite: bool = True,
                ensure_package: bool = True, owner_email: str = "") -> list:
        """Execute a RoutePlan: ensure the package exists, then upload each
        routed artifact via the proven idempotent primitive. Returns a list of
        UploadResult. Bundled scripts/mappings ride inside their iFlow — they
        are not in the plan as separate artifacts, so nothing extra to do."""
        results = []
        if ensure_package:
            self.uploader.ensure_package(
                self.uploader.sanitize_package_id(plan.package_id),
                plan.package_name, owner_email=owner_email)

        for a in plan.artifacts:
            res = self.uploader.upload_raw_bundle(
                a.zip_bytes, plan.package_id, a.artifact_id, a.artifact_name,
                overwrite=overwrite, artifact_type=a.artifact_type)
            results.append(res)
            logger.info("Routed %s [%s] → %s : %s",
                        a.artifact_id, a.artifact_type, a.endpoint, res.status)
        return results

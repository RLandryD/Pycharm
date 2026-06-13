"""Bundle passthrough for non-wiring resources.

Real bundles carry cargo that no BPMN step references and no WIRING_EXTS
walk collects, but that the runtime needs verbatim:

  - src/main/resources/lib/*.jar      archived Java mappings invoked from
                                      Groovy (the PI Java-port pattern —
                                      seen live: delaware XML→FlatFile
                                      converter ships 2 jars per flow)
  - *.crt / *.cer / *.pem             certificates shipped in-bundle (seen
                                      live: Mexico CFDI publicsign.crt at
                                      bundle ROOT)
  - META-INF/deployment/*             deployment descriptors (seen live:
                                      queueDefinitions.json for JMS flows)

Without passthrough, a regenerated flow silently loses this cargo and only
fails at runtime. Collection is keyed by SHA-256 of the .iflw XML found in
the same inner bundle, so cargo re-attaches to exactly the flow it shipped
with, no name matching involved.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile

logger = logging.getLogger("scaffolder.passthrough")

# arcname patterns (matched against full path inside the bundle zip)
PASSTHROUGH_PATTERNS = (
    re.compile(r"^src/main/resources/lib/[^/]+\.jar$", re.I),
    re.compile(r"(^|/)[^/]+\.(crt|cer|pem)$", re.I),
    re.compile(r"^META-INF/deployment/[^/]+$"),
)

_MAX_MEMBER = 5 * 1024 * 1024      # 5 MB per cargo file
_MAX_TOTAL = 40 * 1024 * 1024      # 40 MB per collection run
_MAX_DEPTH = 4


def is_passthrough(arcname: str) -> bool:
    return any(p.search(arcname) for p in PASSTHROUGH_PATTERNS)


def iflw_key(iflw_xml: str) -> str:
    """Stable key for a flow: SHA-256 of its .iflw XML."""
    return hashlib.sha256(iflw_xml.encode("utf-8", "replace")).hexdigest()


def _collect_from_bundle(bz: zipfile.ZipFile) -> tuple:
    """(iflw_xml or None, {arcname: bytes}) for ONE bundle zip."""
    names = bz.namelist()
    iflws = [n for n in names if n.endswith(".iflw")]
    cargo = {}
    for n in names:
        if not is_passthrough(n):
            continue
        try:
            info = bz.getinfo(n)
            if info.file_size > _MAX_MEMBER:
                logger.warning("passthrough member too big, skipped: %s "
                               "(%d bytes)", n, info.file_size)
                continue
            cargo[n] = bz.read(n)
        except Exception as exc:
            logger.warning("passthrough read failed for %s: %s", n, exc)
    if not iflws:
        return None, cargo
    return bz.read(iflws[0]).decode("utf-8", "replace"), cargo


def collect_passthrough_from_zip(raw: bytes) -> dict:
    """Walk a zip (package export, zip-of-zips, or single bundle) and return
    {iflw_key: {arcname: bytes}} for every inner bundle that carries cargo.
    Graceful: returns {} on any container-level error."""
    out: dict = {}
    budget = [0]

    def add(xml: str, cargo: dict):
        if not xml or not cargo:
            return
        size = sum(len(b) for b in cargo.values())
        if budget[0] + size > _MAX_TOTAL:
            logger.warning("passthrough budget exhausted; cargo dropped")
            return
        budget[0] += size
        out.setdefault(iflw_key(xml), {}).update(cargo)

    def walk(zf: zipfile.ZipFile, depth: int):
        if depth > _MAX_DEPTH:
            return
        xml, cargo = _collect_from_bundle(zf)
        add(xml, cargo)
        for n in zf.namelist():
            if n.endswith("/"):
                continue
            head = zf.read(n)[:2] if zf.getinfo(n).file_size >= 2 else b""
            if head == b"PK" and (n.lower().endswith(".zip")
                                  or n.endswith("_content")):
                try:
                    walk(zipfile.ZipFile(io.BytesIO(zf.read(n))), depth + 1)
                except Exception:
                    pass

    try:
        walk(zipfile.ZipFile(io.BytesIO(raw)), 0)
    except Exception as exc:
        logger.warning("passthrough collection failed: %s", exc)
        return {}
    return out


def inject_cargo(result, src_xml: str, passthrough: dict) -> int:
    """Attach collected cargo to a regenerated MinimalIFlowResult. Returns
    the number of files injected. Never overwrites generated files."""
    if not passthrough or not src_xml:
        return 0
    cargo = passthrough.get(iflw_key(src_xml))
    if not cargo:
        return 0
    files = getattr(result, "files", None)
    if files is None:
        return 0
    n = 0
    for arcname, blob in cargo.items():
        if arcname in files:
            continue
        files[arcname] = blob
        n += 1
    if n:
        logger.info("passthrough: %d cargo file(s) re-attached (%s)",
                    n, ", ".join(sorted(cargo)[:4]))
    return n

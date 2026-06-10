"""resource_resolver.py — resolve an iFlow step's resource reference to the
real file content from a path-keyed corpus built from the ORIGINAL package
exports (not the by-extension extraction, which renames/flattens).

Why original-path corpus: an iFlow references a resource by a path-bearing URI
(e.g. `dir://mapping/xslt/src/main/resources/mapping/EnvioDTE_Schema_Conversion.xsl`,
or `schemaResourceUri = src/main/resources/xsd/EnvioDTE_v10.xsd`, or a script
`clientID.groovy`). walk_corpus keys files by their full zip-internal path, so
the reference resolves by path/basename directly — no de-prefixing or collision
guessing. Same-named files across packages are disambiguated by PACKAGE SCOPE.

The resolver is deliberately decoupled from the generator: it takes a `files`
dict {path: content} and a reference string, and returns the best match. The
generator passes step-config values (mappinguri / schemaResourceUri / script
name) and the iFlow's package, and ships the returned content verbatim.
"""
from __future__ import annotations

import html
import os
import re
import re
from dataclasses import dataclass, field

# Subfolder hints per resource kind — used to prefer the right file when a
# basename appears under several folders in one package.
_KIND_DIR = {
    "mapping": ("/mapping/",),
    "xslt": ("/mapping/", "/xslt/"),
    "mmap": ("/mapping/",),
    "script": ("/script/", "/groovy/"),
    "groovy": ("/script/", "/groovy/"),
    "js": ("/script/",),
    "xsd": ("/xsd/", "/wsdl/", "/edmx/"),
    "schema": ("/xsd/", "/wsdl/", "/edmx/"),
    "wsdl": ("/wsdl/",),
}


@dataclass
class ResolveResult:
    path: str | None = None          # corpus key that matched
    content: str | None = None       # real file content (None if unresolved)
    ambiguous: bool = False          # >1 plausible match (after scoping)
    candidates: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.content is not None


def _basename(ref: str) -> str:
    """Pull the bare filename from any reference form (dir:// URI, bundle path,
    or plain name), unescaping XML entities (&amp; -> &)."""
    ref = html.unescape((ref or "").strip())
    # strip a dir:// scheme + leading category segments, keep the tail path
    ref = re.sub(r'^dir://[^/]*/', '', ref)
    return ref.rsplit("/", 1)[-1]


def _package_of(path: str) -> str:
    """Best-effort package id from a corpus key. walk_corpus keys look like
    '<batch>.zip!<Package>.zip!<hash>_content!src/main/resources/...'. The real
    package is the '.zip' segment immediately BEFORE the '*_content' bundle —
    not the outer batch zip. Falls back to the last .zip, then the first path
    segment."""
    parts = path.split("!")
    for i, seg in enumerate(parts):
        if seg.endswith("_content") or "_content" in seg:
            for j in range(i - 1, -1, -1):       # nearest preceding .zip
                if parts[j].lower().endswith(".zip"):
                    return parts[j].lower()
            break
    zips = [p for p in parts if p.lower().endswith(".zip")]
    if zips:
        return zips[-1].lower()
    return path.split("/", 1)[0].lower() if "/" in path else ""


def build_index(files: dict) -> dict:
    """basename(lower) -> [corpus paths]. Built once per corpus."""
    idx: dict = {}
    for p in files:
        b = os.path.basename(p).lower()
        if b:
            idx.setdefault(b, []).append(p)
    return idx


def resolve(reference: str, files: dict, index: dict | None = None,
            package: str | None = None, kind: str | None = None) -> ResolveResult:
    """Resolve `reference` to a real file in `files`.

    package: prefer matches inside this package (the iFlow's own package), so
             same-named files elsewhere don't win. Falls back to global if the
             package has no match.
    kind:    one of _KIND_DIR keys; prefers files under the expected subfolder.
    """
    if index is None:
        index = build_index(files)
    base = _basename(reference)
    if not base or "." not in base:
        return ResolveResult(candidates=[])
    cands = list(index.get(base.lower(), []))
    if not cands:
        return ResolveResult(candidates=[])

    # 1) scope to the iFlow's package when asked
    scoped = cands
    if package:
        pkg = package.lower()
        norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
        npkg = norm(package)
        in_pkg = [p for p in cands if pkg in _package_of(p) or pkg in p.lower()
                  or (npkg and npkg in norm(p))]
        if in_pkg:
            scoped = in_pkg

    # 2) prefer the expected subfolder for this kind
    if kind and kind in _KIND_DIR and len(scoped) > 1:
        hinted = [p for p in scoped
                  if any(h in p.lower() for h in _KIND_DIR[kind])]
        if hinted:
            scoped = hinted

    chosen = scoped[0]
    return ResolveResult(path=chosen, content=files.get(chosen),
                         ambiguous=len(scoped) > 1, candidates=scoped)

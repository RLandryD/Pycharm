"""library_builder/corpus_pipeline.py

The clean corpus orchestrator — replaces the retired extractor.py/run_extractor.
Thin layer on the existing pieces:

    corpus (dir | zip | nested zips | *_content bundles)
        -> walk + read every file
        -> classify by extension  (capability_catalog.type_for_ext)
        -> group into per-type corpora
        -> build per-type catalogs (capability_catalog.build_all)
        -> normalize into one view  (solver.normalize)
        -> ready for: solve(requirement) / search(term) / report()

Every engine (the 4 today; js / props / opmap / iflw / PI tomorrow) plugs in by
being registered in capability_catalog — it then flows through this pipeline
unchanged and normalizes to the SAME shape, so capabilities from different types
(and eventually PI vs CPI) are directly comparable. That uniform normalized
shape is the PI->CPI migration mechanism: same universal ops, different bindings.

No SAP, no tenant — pure corpus processing, fully sandbox-testable.
"""
from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field as _field

from . import capability_catalog as _cc
from . import solver as _solver


# ───────────────────────────── corpus walking ─────────────────────────────
def _read_zip(zf: zipfile.ZipFile, out: dict, depth: int = 0, max_depth: int = 8):
    """Recursively read files from a zip, descending into nested zips and
    *_content bundles (the SAP package structure)."""
    if depth > max_depth:
        return
    for n in zf.namelist():
        if n.endswith("/"):
            continue
        try:
            raw = zf.read(n)
        except Exception:
            continue
        if raw[:2] == b"PK" and (n.endswith(".zip") or n.endswith("_content")):
            try:
                _read_zip(zipfile.ZipFile(io.BytesIO(raw)), out, depth + 1)
                continue
            except Exception:
                pass
        # key by the full zip-internal PATH, not the basename — same-named files
        # in different folders (e.g. many script.groovy) must not collapse.
        if n and n not in out:
            try:
                out[n] = raw.decode("utf-8", "replace")
            except Exception:
                pass


def walk_corpus_bytes(packages) -> dict:
    """Read files from in-memory uploads: a list of zip-bytes, or a list of
    dicts with a 'bytes' key (the workbench's `uploaded_packages` shape), or a
    {name: bytes} mapping. Descends nested zips and *_content bundles. Returns
    {filename: text}. Lets callers build a corpus without writing to disk."""
    out: dict = {}
    items = []
    if isinstance(packages, dict):
        items = list(packages.values())
    else:
        for p in (packages or []):
            items.append(p.get("bytes") if isinstance(p, dict) else p)
    for raw in items:
        if not raw:
            continue
        try:
            _read_zip(zipfile.ZipFile(io.BytesIO(raw)), out)
        except Exception:
            continue
    return out


def walk_corpus(path: str) -> dict:
    """Read every file from a path (a directory, a .zip, or a single file) into
    {name: text}. Descends nested zips and *_content bundles.

    Keyed by a PATH-QUALIFIED name (relative path under the walked dir), NOT just
    the basename — because real harvests have many same-named files (e.g.
    hundreds of `script.groovy` across packages). Keying by basename collapsed
    them to one (first-seen wins), silently shrinking the catalog. Path-qualified
    keys keep them all; true content-duplicates are still handled downstream
    (e.g. the schema catalog dedups by structure).
    """
    out: dict = {}
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if f.endswith(".zip"):
                        with zipfile.ZipFile(fp) as zf:
                            _read_zip(zf, out)
                    else:
                        with open(fp, "r", encoding="utf-8",
                                  errors="replace") as fh:
                            # path-qualified key (relative to the walked dir)
                            rel = os.path.relpath(fp, path)
                            if rel not in out:
                                out[rel] = fh.read()
                except Exception:
                    continue
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            _read_zip(zf, out)
    elif os.path.isfile(path):
        out[os.path.basename(path)] = open(
            path, "r", encoding="utf-8", errors="replace").read()
    return out


def group_by_type(files: dict) -> dict:
    """Group {filename: text} into {ctype: {filename: text}} using the facade's
    extension→type routing. Unknown extensions are dropped (reported separately
    via classify_report)."""
    grouped: dict = {}
    for name, text in files.items():
        ext = name.rsplit(".", 1)[-1] if "." in name else ""
        ctype = _cc.type_for_ext(ext)
        if ctype:
            grouped.setdefault(ctype, {})[name] = text
    return grouped


def classify_report(files: dict) -> dict:
    """How the corpus breaks down by recognized type vs unknown."""
    known, unknown = {}, {}
    for name in files:
        ext = name.rsplit(".", 1)[-1] if "." in name else "(none)"
        ctype = _cc.type_for_ext(ext)
        (known if ctype else unknown).setdefault(ctype or ext, 0)
        (known if ctype else unknown)[ctype or ext] += 1
    return {"known": known, "unknown": unknown, "total": len(files)}


# ───────────────────────────── the pipeline ───────────────────────────────
@dataclass
class Corpus:
    """A processed corpus: catalogs + the normalized capability view + an
    inverted index for fast search. Built once, queried many times."""
    files: dict = _field(default_factory=dict)
    grouped: dict = _field(default_factory=dict)
    catalogs: dict = _field(default_factory=dict)        # {ctype: catalog}
    normalized: list = _field(default_factory=list)      # NormalizedCapability[]
    _idf: dict = _field(default_factory=dict)

    def report(self) -> dict:
        by_type = {}
        for c in self.normalized:
            by_type[c.ctype] = by_type.get(c.ctype, 0) + 1
        return {
            "files": len(self.files),
            "types": list(self.grouped.keys()),
            "capabilities": len(self.normalized),
            "by_type": by_type,
            "classify": classify_report(self.files),
        }

    def solve(self, requirement: str):
        """Run the full reasoning pipeline against this corpus's capabilities."""
        return _solver.solve(requirement, self.normalized)

    def search(self, term: str, top_n: int = 10) -> list:
        """Direct FETCH: rank capabilities by a free-text term."""
        from library_builder.solver import Need, fetch, _kw
        need = Need(text=term, keywords=_kw(term))
        if not self._idf:
            self._idf = _solver._idf(self.normalized)
        ranked = fetch(need, self.normalized, self._idf)
        return [(m.capability.cap_id, m.score) for m in ranked[:top_n]]


def build_corpus(path: str = None, files: dict = None, packages=None,
                 **catalog_kw) -> Corpus:
    """Build a Corpus from a path, a pre-read {name: text} dict, OR in-memory
    upload `packages` (list of zip-bytes / dicts with 'bytes'). `catalog_kw` is
    passed to per-type catalog builders (e.g. verify=True for xslt). The single
    entry point that replaces extractor.py/run_extractor."""
    if files is None:
        if packages is not None:
            files = walk_corpus_bytes(packages)
        elif path is not None:
            files = walk_corpus(path)
        else:
            raise ValueError("build_corpus needs a path, files dict, or packages")
    grouped = group_by_type(files)
    catalogs = {}
    for ctype, sub in grouped.items():
        try:
            catalogs[ctype] = _cc.catalog_for(ctype, sub, **(
                catalog_kw if ctype == "xslt" else {}))
        except TypeError:
            catalogs[ctype] = _cc.catalog_for(ctype, sub)
    normalized = []
    for ctype, cat in catalogs.items():
        normalized.extend(_solver.normalize(cat, ctype))
    return Corpus(files=files, grouped=grouped, catalogs=catalogs,
                  normalized=normalized)

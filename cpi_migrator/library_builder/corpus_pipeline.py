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

import hashlib
import io
import logging
import os
import pickle
import re
import zipfile
from dataclasses import dataclass, field as _field
from pathlib import Path

from . import capability_catalog as _cc
from . import solver as _solver

log = logging.getLogger(__name__)

# Safety caps so a too-broad capability_corpus_dir (e.g. pointed at a whole
# Resources tree with thousands of schemas) degrades to a warning + partial
# corpus instead of a multi-minute hang. Loose files above the size cap (e.g. a
# 12.9 MB EDMX) are skipped — the capability corpus is built from package
# internals, not large standalone schemas.
_MAX_FILES = 20000
_MAX_TOTAL_BYTES = 200 * 1024 * 1024
_MAX_FILE_BYTES = 5 * 1024 * 1024


class _Budget:
    """Tracks files/bytes ingested during a corpus walk and trips a cap so a
    misconfigured directory can't run away."""
    def __init__(self, max_files=_MAX_FILES, max_bytes=_MAX_TOTAL_BYTES):
        self.max_files, self.max_bytes = max_files, max_bytes
        self.files = self.bytes = 0
        self.capped = False

    def ok(self) -> bool:
        return not self.capped

    def take(self, nbytes: int) -> bool:
        if self.capped:
            return False
        self.files += 1
        self.bytes += nbytes
        if self.files > self.max_files or self.bytes > self.max_bytes:
            self.capped = True
            log.warning(
                "corpus walk hit the safety cap (%d files / %d MB) — ingesting no "
                "more. Point capability_corpus_dir at a narrower folder (CPI "
                "package exports only), not a whole tree.",
                self.max_files, self.max_bytes // (1024 * 1024))
            return False
        return True


# ───────────────────────────── corpus walking ─────────────────────────────
def _read_zip(zf: zipfile.ZipFile, out: dict, depth: int = 0, max_depth: int = 8,
              budget: "_Budget | None" = None, prefix: str = "",
              exts: "set | None" = None,
              max_file_bytes: int = _MAX_FILE_BYTES):
    """Recursively read files from a zip, descending into nested zips and
    *_content bundles (the SAP package structure). Keys are CONTAINER-QUALIFIED
    (outer.zip/inner.zip/hash_content::src/...): without the prefix, every
    package's identical internal paths (src/main/resources/parameters.prop,
    same-named scripts) collide and all but the first are silently dropped —
    which shipped the WRONG package's parameter values and lost resources."""
    if depth > max_depth:
        return
    for n in zf.namelist():
        if n.endswith("/"):
            continue
        if budget is not None and not budget.ok():
            return
        try:
            raw = zf.read(n)
        except Exception:
            continue
        if raw[:2] == b"PK" and (n.endswith(".zip") or n.endswith("_content")):
            try:
                sep = "::" if n.endswith("_content") else "/"
                _read_zip(zipfile.ZipFile(io.BytesIO(raw)), out, depth + 1,
                          max_depth, budget, prefix=f"{prefix}{n}{sep}",
                          exts=exts, max_file_bytes=max_file_bytes)
                continue
            except Exception:
                pass
        if len(raw) > max_file_bytes:         # skip oversized leaf blobs
            continue
        if exts is not None and \
                ("." + n.rsplit(".", 1)[-1].lower() if "." in n else "") \
                not in exts:
            continue
        key = f"{prefix}{n}"
        if key and key not in out:
            if budget is not None and not budget.take(len(raw)):
                return
            try:
                out[key] = raw.decode("utf-8", "replace")
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


#: leaf extensions the bundle-wiring resource corpus actually consumes —
#: walking everything blew the 200 MB budget before reaching the user's
#: packages, so nothing resolved and parameters shipped empty.
WIRING_EXTS = {".groovy", ".gsh", ".js", ".xsl", ".xslt", ".mmap", ".opmap",
               ".xsd", ".wsdl", ".edmx", ".prop", ".propdef", ".iflw"}

#: per-package targeted ingestion may legitimately carry big schemas (the SF
#: adapter EDMX files are ~6.8 MB each) — the bulk-walk 5 MB leaf cap exists to
#: protect a 20k-file sweep, not a single known package.
_TARGETED_MAX_FILE_BYTES = 16 * 1024 * 1024


def _norm_name(s: str) -> str:
    """Alphanumeric-only lowercase form used for package-name matching."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _name_variants(s: str) -> set:
    """Normalized variants of a package/iflow name. 'Version 2' vs '_V2' is a
    real corpus discrepancy, so a version-collapsed variant is included."""
    n = _norm_name(s)
    out = {n} if n else set()
    if "version" in n:
        out.add(n.replace("version", "v"))
    return out


def _names_match(a_variants: set, b: str) -> bool:
    """True when any normalized variant of A is a substring of B's normalized
    form or vice versa. A minimum-length guard keeps short tokens (e.g. 'v2')
    from matching everything."""
    nb_variants = _name_variants(b)
    for na in a_variants:
        for nb in nb_variants:
            if len(na) >= 6 and na in nb:
                return True
            if len(nb) >= 6 and nb in na:
                return True
    return False


def walk_corpus_for_names(path: str, names, exts: "set | None" = None) -> dict:
    """TARGETED corpus ingestion: scan zip *names* under `path` (cheap — only
    central directories are read for non-matches) and fully ingest just the
    containers whose name matches one of `names` (package or iFlow names,
    alphanumeric-normalized, 'Version N'~'VN' tolerated). Matches are also
    looked for one level INSIDE top-level zips (batch zips like part2.zip hold
    the real package zips). Returns container-qualified keys IDENTICAL to
    walk_corpus, so resolver package-scoping behaves the same.

    This is the antidote to the bulk-walk safety cap: when a too-broad
    Packages tree caps out before reaching the flow's own package (seen live:
    'corpus walk hit the safety cap' then 0 resolved, stub parameters), the
    scaffolder tops the cached corpus up with exactly the packages it is
    migrating. A single package is small, so the per-file leaf cap is raised
    enough to carry adapter EDMX schemas."""
    out: dict = {}
    variants = set()
    for nm in (names or []):
        variants |= _name_variants(nm)
    if not variants or not path or not os.path.isdir(path):
        return out
    budget = _Budget(max_files=8000, max_bytes=120 * 1024 * 1024)
    for root, _dirs, files in os.walk(path):
        if not budget.ok():
            break
        for f in sorted(files):
            if not f.endswith(".zip") or not budget.ok():
                continue
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, path)
            try:
                if _names_match(variants, f[:-4]):
                    with zipfile.ZipFile(fp) as zf:
                        _read_zip(zf, out, budget=budget, prefix=rel + "/",
                                  exts=exts,
                                  max_file_bytes=_TARGETED_MAX_FILE_BYTES)
                    continue
                # batch zip: scan entry names for an inner matching container
                with zipfile.ZipFile(fp) as zf:
                    for n in zf.namelist():
                        if n.endswith("/") or not budget.ok():
                            continue
                        stem = n.rsplit("/", 1)[-1]
                        is_zip = stem.endswith(".zip")
                        if not (is_zip and
                                _names_match(variants, stem[:-4])):
                            continue
                        raw = zf.read(n)
                        if raw[:2] != b"PK":
                            continue
                        _read_zip(zipfile.ZipFile(io.BytesIO(raw)), out,
                                  depth=1, budget=budget,
                                  prefix=f"{rel}/{n}/", exts=exts,
                                  max_file_bytes=_TARGETED_MAX_FILE_BYTES)
            except Exception:
                continue
    return out


def walk_zip_bytes(raw: bytes, container: str,
                   exts: "set | None" = None) -> dict:
    """Read an UPLOADED zip's files into {container-qualified name: text} —
    the same key scheme as walk_corpus. Makes any uploaded source package or
    iFlow bundle self-sufficient for resource wiring, instead of depending on
    a same-named export sitting in the pinned Packages folder (seen live: a
    bundle uploaded for migration shipped with ZERO of its own scripts because
    resolution only consulted the on-disk corpus). Graceful: returns {} on any
    error."""
    out: dict = {}
    try:
        import io as _io
        budget = _Budget()
        with zipfile.ZipFile(_io.BytesIO(raw)) as zf:
            _read_zip(zf, out, budget=budget,
                      prefix=(container or "upload") + "/", exts=exts)
    except Exception:
        return {}
    return out


def walk_corpus(path: str, exts: "set | None" = None) -> dict:
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
    budget = _Budget()
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            if not budget.ok():
                break
            for f in files:
                if not budget.ok():
                    break
                fp = os.path.join(root, f)
                try:
                    if f.endswith(".zip"):
                        rel = os.path.relpath(fp, path)
                        with zipfile.ZipFile(fp) as zf:
                            _read_zip(zf, out, budget=budget,
                                      prefix=rel + "/", exts=exts)
                    else:
                        try:
                            sz = os.path.getsize(fp)
                        except OSError:
                            sz = 0
                        if sz > _MAX_FILE_BYTES:     # skip large standalone files
                            continue                 # (e.g. a 12.9 MB EDMX)
                        rel = os.path.relpath(fp, path)
                        if exts is not None and \
                                ("." + f.rsplit(".", 1)[-1].lower()
                                 if "." in f else "") not in exts:
                            continue
                        if rel not in out:
                            if not budget.take(sz):
                                break
                            with open(fp, "r", encoding="utf-8",
                                      errors="replace") as fh:
                                out[rel] = fh.read()
                except Exception:
                    continue
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            _read_zip(zf, out, budget=budget,
                      prefix=os.path.basename(path) + "/", exts=exts)
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


def _dir_signature(path: str) -> str:
    """Cheap content signature of a directory: file count + total size + newest
    mtime. Stat-only (no reads), so it's fast even on large trees and changes
    whenever any file is added/removed/modified."""
    n = total = 0
    newest = 0.0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                stt = os.stat(os.path.join(root, f))
            except OSError:
                continue
            n += 1
            total += stt.st_size
            newest = max(newest, stt.st_mtime)
    # _CORPUS_FORMAT bumps invalidate stale disk caches when the walk's keying
    # changes (v2: container-prefixed keys — old caches collapsed same-named
    # files across packages)
    key = f"v2|{os.path.abspath(path)}|{n}|{total}|{int(newest)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _cache_file(sig: str) -> Path:
    d = Path.home() / ".cpi_migrator" / "corpus_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"corpus_{sig}.pkl"


def build_corpus(path: str = None, files: dict = None, packages=None,
                 use_disk_cache: bool = True, **catalog_kw) -> Corpus:
    """Build a Corpus from a path, a pre-read {name: text} dict, OR in-memory
    upload `packages`. For a directory `path`, the built corpus is persisted to a
    disk cache keyed by the directory's signature, so it is built once and
    reloaded instantly on later runs (and across app restarts) instead of
    re-walking. `catalog_kw` is passed to per-type catalog builders."""
    # disk cache only for the heavy, repeatable case: a directory path with no
    # special catalog kwargs (those change the built output).
    cache_fp = None
    if (use_disk_cache and path is not None and files is None
            and packages is None and not catalog_kw and os.path.isdir(path)):
        try:
            cache_fp = _cache_file(_dir_signature(path))
            if cache_fp.exists():
                with open(cache_fp, "rb") as fh:
                    corpus = pickle.load(fh)
                log.info("corpus: loaded from disk cache (%s)", cache_fp.name)
                return corpus
        except Exception:
            cache_fp = None      # any cache problem → just build normally

    corpus = _build_corpus(path=path, files=files, packages=packages, **catalog_kw)

    if cache_fp is not None:
        try:
            with open(cache_fp, "wb") as fh:
                pickle.dump(corpus, fh)
            log.info("corpus: built + cached to disk (%s)", cache_fp.name)
        except Exception:
            pass                 # non-picklable / write error → skip caching
    return corpus


def _build_corpus(path: str = None, files: dict = None, packages=None,
                  **catalog_kw) -> Corpus:
    """The actual build (walk → group → catalogs → normalize)."""
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
